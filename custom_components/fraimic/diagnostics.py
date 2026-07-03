"""Diagnostics support for Fraimic E-Ink Canvas."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_HOST
from .runtime_data import FraimicConfigEntry

# The frame's own /api/info payload includes network identifiers
# (wifi ssid/bssid/mac/ip) alongside the host address configured for
# this entry -- redact all of it, not just what came from config_flow.
TO_REDACT = {CONF_HOST, "ssid", "bssid", "mac", "ip"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: FraimicConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    runtime = entry.runtime_data
    coordinator = runtime.coordinator
    last_success = coordinator.last_success

    return {
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "entry_options": dict(entry.options),
        "info": async_redact_data(coordinator.data or {}, TO_REDACT),
        "battery": runtime.battery_coordinator.data,
        "device_reachable": coordinator.device_reachable,
        "last_update_success": coordinator.last_update_success,
        "last_success": last_success.isoformat() if last_success else None,
    }
