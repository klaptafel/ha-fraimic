"""Data update coordinators for Fraimic.

Two coordinators, matching the guide's polling recommendation:
- FraimicCoordinator: full /api/info snapshot, every 5 minutes.
- FraimicBatteryCoordinator: lightweight /api/battery, every 60 seconds,
  safe to poll more frequently.

The frame is a battery-powered, sleepy device -- it's only reachable
while awake (briefly, on a tap or its own refresh schedule) and is
*completely* unreachable during deep sleep. A single missed poll is
therefore normal, not an error. `device_reachable` reflects that: it
stays True (showing entities' last known values) until there's been no
successful contact for UNAVAILABLE_AFTER, which is a much stronger
signal of an actual problem than "the last poll happened to land while
it was asleep".
"""
from __future__ import annotations

import logging
from datetime import datetime

from aiohttp import ClientError

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import api
from .const import DEFAULT_BATTERY_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN, UNAVAILABLE_AFTER

_LOGGER = logging.getLogger(__name__)


class _BaseFraimicCoordinator(DataUpdateCoordinator[dict]):
    def __init__(self, hass: HomeAssistant, host: str, name: str, interval) -> None:
        super().__init__(hass, _LOGGER, name=name, update_interval=interval)
        self.host = host.rstrip("/")
        self._last_success: datetime | None = None

    @property
    def base_url(self) -> str:
        return self.host

    @property
    def last_success(self) -> datetime | None:
        return self._last_success

    @property
    def device_reachable(self) -> bool:
        """True unless we've heard nothing from the frame for a long time.

        Used as `available` for most entities so an expected deep-sleep
        gap just leaves them showing their last known value, instead of
        flipping to unavailable on the very next missed poll.
        """
        if self._last_success is None:
            return False
        return dt_util.utcnow() - self._last_success <= UNAVAILABLE_AFTER

    def _mark_success(self) -> None:
        self._last_success = dt_util.utcnow()

    async def _fetch(self, session) -> dict:
        raise NotImplementedError

    async def _async_update_data(self) -> dict:
        session = async_get_clientsession(self.hass)
        try:
            data = await self._fetch(session)
        except HomeAssistantError as err:
            raise UpdateFailed(str(err)) from err
        except (ClientError, TimeoutError) as err:
            raise UpdateFailed(
                f"Could not connect to Fraimic frame at {self.base_url}: {err}"
            ) from err
        self._mark_success()
        return data


class FraimicCoordinator(_BaseFraimicCoordinator):
    """Polls /api/info."""

    def __init__(self, hass: HomeAssistant, host: str) -> None:
        super().__init__(hass, host, f"{DOMAIN}_info", DEFAULT_SCAN_INTERVAL)

    async def _fetch(self, session) -> dict:
        return await api.get_info(session, self.base_url)


class FraimicBatteryCoordinator(_BaseFraimicCoordinator):
    """Polls the lightweight /api/battery endpoint more frequently."""

    def __init__(self, hass: HomeAssistant, host: str) -> None:
        super().__init__(hass, host, f"{DOMAIN}_battery", DEFAULT_BATTERY_SCAN_INTERVAL)

    async def _fetch(self, session) -> dict:
        return await api.get_battery(session, self.base_url)
