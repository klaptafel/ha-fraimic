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

import async_timeout
from aiohttp import ClientSession

from homeassistant.exceptions import HomeAssistantError

from .const import DEFAULT_TIMEOUT, DOMAIN, EP_BATTERY, EP_IMAGE, EP_INFO, EP_REFRESH, EP_RESTART, EP_SLEEP

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


async def _request_json(
    session: ClientSession, method: str, url: str, request_timeout: int = DEFAULT_TIMEOUT, **kwargs
) -> dict:
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


async def get_info(session: ClientSession, host: str) -> dict:
    return await _request_json(session, "GET", f"{host}{EP_INFO}")


async def get_battery(session: ClientSession, host: str) -> dict:
    return await _request_json(session, "GET", f"{host}{EP_BATTERY}")


async def restart(session: ClientSession, host: str) -> None:
    await _request_json(session, "POST", f"{host}{EP_RESTART}")


async def sleep(session: ClientSession, host: str) -> None:
    await _request_json(session, "POST", f"{host}{EP_SLEEP}")


async def refresh(session: ClientSession, host: str) -> None:
    await _request_json(session, "POST", f"{host}{EP_REFRESH}")


async def upload_image(session: ClientSession, host: str, bin_data: bytes) -> dict:
    return await _request_json(
        session,
        "POST",
        f"{host}{EP_IMAGE}",
        request_timeout=60,
        data=bin_data,
        headers={"Content-Type": "application/octet-stream"},
    )
