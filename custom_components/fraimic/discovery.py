"""Best-effort discovery of Fraimic frames on the local network.

Two entry points, used by config_flow.py:
  - probe_frame: confirm a single candidate host is actually a Fraimic
    frame (used both for DHCP-triggered candidates and subnet scanning).
  - scan_subnet: probe every host in the local /24 concurrently, for the
    "Add Integration" flow when no host was typed.
"""
from __future__ import annotations

import asyncio
from ipaddress import ip_network
from typing import Any

from aiohttp import ClientError, ClientSession

from homeassistant.components.network import async_get_source_ip
from homeassistant.components.network.const import MDNS_TARGET_IP
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import api
from .const import DISCOVERY_PROBE_TIMEOUT


async def probe_frame(session: ClientSession, host: str) -> dict[str, Any] | None:
    """GET /api/info at `host` with a short timeout -- returns the parsed
    payload if it responds like a Fraimic frame, None on any failure
    (connection error, timeout, or a documented API-level error).
    Presence of device.device_key is NOT required for a match -- a frame
    without one still counts (matches _device_key's own fallback logic
    in config_flow.py), only a connection-level failure means "not a
    Fraimic device".

    Also opportunistically scrapes /info for the panel size (nested under
    "info_page", matching the shape coordinator.py's own merge produces)
    so callers can show the detected model at discovery time, before any
    coordinator exists -- get_info_page is best-effort and never raises,
    so this never turns a real match into a failure.
    """
    try:
        info = await api.get_info(session, host, request_timeout=DISCOVERY_PROBE_TIMEOUT)
    except (ClientError, TimeoutError, HomeAssistantError):
        return None
    info["info_page"] = await api.get_info_page(
        session, host, request_timeout=DISCOVERY_PROBE_TIMEOUT
    )
    return info


async def scan_subnet(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Probe every host address in the local /24 concurrently, returning
    {"ip": ..., "info": ...} for each that responded, sorted by IP for a
    stable picker order."""
    local_ip = await async_get_source_ip(hass, MDNS_TARGET_IP)
    network = ip_network(f"{local_ip}/24", strict=False)
    session = async_get_clientsession(hass)
    hosts = [str(host) for host in network.hosts()]
    results = await asyncio.gather(
        *(probe_frame(session, api.normalize_host(host)) for host in hosts),
        return_exceptions=True,
    )
    found = [
        {"ip": host, "info": info}
        for host, info in zip(hosts, results)
        if isinstance(info, dict)
    ]
    return sorted(found, key=lambda d: str(d["ip"]))
