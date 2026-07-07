"""Thin async HTTP client for the Fraimic REST API.

Centralizes error handling for the documented failure modes (section
"The Endpoints" / "Upload Image" and "Enter Deep Sleep" of the official
Fraimic REST API Guide v0.2.16):
  - POST /api/image: 400 invalid_image_size, 400 file_too_large,
    501 unsupported_content_type, 503 buffer_not_ready
  - POST /api/sleep: {"error": "charging_cable_connected"} when a
    charging cable is plugged in (deep sleep is intentionally blocked).

Errors are raised as HomeAssistantError with a translation_key rather
than a hardcoded message, so they show up in whichever language the
person's Home Assistant is set to (see strings.json / translations/).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

import async_timeout
from aiohttp import ClientError, ClientSession

from homeassistant.exceptions import HomeAssistantError

from .const import (
    DEFAULT_TIMEOUT,
    DOMAIN,
    EP_ALBUMS,
    EP_BATTERY,
    EP_IMAGE,
    EP_INFO,
    EP_REFRESH,
    EP_RESTART,
    EP_SLEEP,
)

_LOGGER = logging.getLogger(__name__)

# Error codes documented by the frame's own API -- each must have a
# matching key under "exceptions" in strings.json / translations/*.json.
_KNOWN_ERROR_KEYS = frozenset(
    {
        "invalid_image_size",
        "file_too_large",
        "unsupported_content_type",
        "buffer_not_ready",
        "charging_cable_connected",
    }
)

# Retry budget for uploads specifically -- covers a genuine transient
# hiccup (a dropped packet, a momentary Wi-Fi blip) while the frame is
# awake and listening. It does NOT help if the frame is actually asleep:
# deep sleep means its web server isn't running at all, so no amount of
# retrying reaches it -- only a real "connection refused/timed out while
# otherwise reachable" case benefits here.
_UPLOAD_MAX_ATTEMPTS = 3
_UPLOAD_RETRY_DELAYS = (2, 5)  # seconds to wait before attempt 2 and 3

ErrorParser = Callable[[dict[str, Any], int], "HomeAssistantError | None"]


def _frame_error(payload: dict[str, Any], status: int) -> HomeAssistantError | None:
    """The frame's own native error shape: {"error": "code"}."""
    error_code = payload.get("error")
    if error_code in _KNOWN_ERROR_KEYS:
        return HomeAssistantError(translation_domain=DOMAIN, translation_key=error_code)
    if error_code:
        return HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="unknown_error",
            translation_placeholders={"error_code": str(error_code)},
        )
    if status >= 400:
        return HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="http_error",
            translation_placeholders={"status": str(status)},
        )
    return None


def _cloud_error(payload: dict[str, Any], status: int) -> HomeAssistantError | None:
    """FastAPI's {"detail": ...} shape from /api/albums writes -- detail is
    a list of Pydantic-style error dicts for a 422, or a plain string for a
    400 business-rule failure (both confirmed via curl against the real
    device). Different shapes under the same key, unlike _frame_error."""
    if status < 400:
        return None
    detail = payload.get("detail")
    if isinstance(detail, str):
        message = detail
    elif isinstance(detail, list):
        parts = []
        for item in detail:
            loc = item.get("loc") or []
            if loc and loc[0] == "body":
                loc = loc[1:]
            parts.append(f"{'.'.join(str(p) for p in loc)}: {item.get('msg')}")
        message = "; ".join(parts) if parts else f"HTTP {status}"
    else:
        # Defensive: a raw gateway/proxy error that isn't FastAPI's shape at
        # all (e.g. a 502/504 during a real outage) -- don't crash formatting it.
        message = f"HTTP {status}"
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="cloud_validation_error",
        translation_placeholders={"detail": message},
    )


async def _request_json(
    session: ClientSession,
    method: str,
    url: str,
    request_timeout: int = DEFAULT_TIMEOUT,
    error_parser: ErrorParser = _frame_error,
    **kwargs: Any,
) -> dict[str, Any]:
    async with async_timeout.timeout(request_timeout):
        async with session.request(method, url, **kwargs) as resp:
            try:
                payload = await resp.json(content_type=None)
            except Exception:  # noqa: BLE001
                payload = {}
            if not isinstance(payload, dict):
                payload = {}

            err = error_parser(payload, resp.status)
            if err is not None:
                raise err
            return payload


async def get_info(session: ClientSession, host: str) -> dict[str, Any]:
    return await _request_json(session, "GET", f"{host}{EP_INFO}")


async def get_battery(session: ClientSession, host: str) -> dict[str, Any]:
    return await _request_json(session, "GET", f"{host}{EP_BATTERY}")


async def get_albums(session: ClientSession, host: str) -> dict[str, Any]:
    """GET /api/albums -- NOT in the official API guide, found via the
    frame's own /logs debug output. That output confirmed the firmware
    proxies this straight to Fraimic's cloud backend
    (https://origin.fraimic.com), authenticated with device_key -- unlike
    every other endpoint here, this only works if the frame itself has
    real internet access, not just LAN reachability."""
    return await _request_json(session, "GET", f"{host}{EP_ALBUMS}")


async def update_album(session: ClientSession, host: str, album_id: str, **fields: Any) -> dict[str, Any]:
    """PUT /api/albums/{id} -- partial update; only pass fields you want
    changed, everything else is preserved server-side. `schedule`, if
    present, must be the FULL replacement shape for whichever type is
    being set -- the cloud does NOT merge it (confirmed via curl: sending
    {"type": "specific_days", "days": [...]} nulls out interval_value/
    interval_unit server-side)."""
    return await _request_json(
        session,
        "PUT",
        f"{host}{EP_ALBUMS}/{album_id}",
        json=fields,
        error_parser=_cloud_error,
    )


async def restart(session: ClientSession, host: str) -> None:
    await _request_json(session, "POST", f"{host}{EP_RESTART}")


async def sleep(session: ClientSession, host: str) -> None:
    await _request_json(session, "POST", f"{host}{EP_SLEEP}")


async def refresh(session: ClientSession, host: str) -> None:
    await _request_json(session, "POST", f"{host}{EP_REFRESH}")


async def upload_image(session: ClientSession, host: str, bin_data: bytes) -> dict[str, Any]:
    """POST the converted image to the frame.

    Retries on connection-level failures (ClientError/TimeoutError) --
    NOT on the frame's own reported error codes (already_busy,
    invalid_image_size, buffer_not_ready, etc.), which are meaningful
    responses that a retry can't fix.
    """
    last_error: ClientError | TimeoutError | None = None
    for attempt in range(_UPLOAD_MAX_ATTEMPTS):
        if attempt > 0:
            await asyncio.sleep(_UPLOAD_RETRY_DELAYS[attempt - 1])
        try:
            return await _request_json(
                session,
                "POST",
                f"{host}{EP_IMAGE}",
                request_timeout=60,
                data=bin_data,
                headers={"Content-Type": "application/octet-stream"},
            )
        except (ClientError, TimeoutError) as err:
            last_error = err
            _LOGGER.debug(
                "Upload attempt %s/%s failed (%s): %s",
                attempt + 1,
                _UPLOAD_MAX_ATTEMPTS,
                "retrying" if attempt + 1 < _UPLOAD_MAX_ATTEMPTS else "giving up",
                err,
            )
    # Unreachable unless _UPLOAD_MAX_ATTEMPTS is changed to 0 -- asserted
    # (rather than left implicit) so that change fails loudly instead of
    # raising a confusing "exceptions must derive from BaseException".
    assert last_error is not None
    raise last_error
