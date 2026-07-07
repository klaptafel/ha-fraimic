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
from typing import Any

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


async def _request_json(
    session: ClientSession,
    method: str,
    url: str,
    request_timeout: int = DEFAULT_TIMEOUT,
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

            error_code = payload.get("error")
            if error_code in _KNOWN_ERROR_KEYS:
                raise HomeAssistantError(
                    translation_domain=DOMAIN, translation_key=error_code
                )
            if error_code:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="unknown_error",
                    translation_placeholders={"error_code": str(error_code)},
                )
            if resp.status >= 400:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="http_error",
                    translation_placeholders={"status": str(resp.status)},
                )
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
    real internet access, not just LAN reachability. Cloud-side write
    errors (POST/PUT, not used here) come back as FastAPI's
    {"detail": [...]} shape, not this integration's usual {"error": "code"}
    -- _request_json's status-code check handles both generically since it
    doesn't inspect body shape, but don't assume the {"error": ...} shape
    if write support is ever added here."""
    return await _request_json(session, "GET", f"{host}{EP_ALBUMS}")


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
