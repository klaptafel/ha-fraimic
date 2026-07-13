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

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from aiohttp import ClientError, ClientSession

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import api
from .const import (
    DEFAULT_ALBUMS_SCAN_INTERVAL,
    DEFAULT_BATTERY_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    UNAVAILABLE_AFTER,
)

_LOGGER = logging.getLogger(__name__)


class FraimicBaseCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(
        self, hass: HomeAssistant, host: str, name: str, interval: timedelta
    ) -> None:
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

    def _log_reachability_change(self, was_reachable: bool) -> None:
        """Log only the moment device_reachable actually flips, not every
        individual missed poll -- keeps this quiet during the sleep gaps
        it's specifically designed to tolerate, per the module docstring.
        """
        is_reachable = self.device_reachable
        if was_reachable and not is_reachable:
            _LOGGER.warning(
                "%s: no contact with the frame at %s for over %s -- marking unreachable",
                self.name, self.base_url, UNAVAILABLE_AFTER,
            )
        elif not was_reachable and is_reachable:
            _LOGGER.info("%s: frame at %s is reachable again", self.name, self.base_url)

    async def _fetch(self, session: ClientSession) -> dict[str, Any]:
        raise NotImplementedError

    async def _async_update_data(self) -> dict[str, Any]:
        was_reachable = self.device_reachable
        try:
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
        finally:
            self._log_reachability_change(was_reachable)


class FraimicCoordinator(FraimicBaseCoordinator):
    """Polls /api/info, plus the /info admin page (see api.get_info_page)
    for the couple of fields the JSON API doesn't expose at all -- merged
    in under the "info_page" key so readers can tell at a glance it's from
    that separate, best-effort source, not /api/info's own JSON."""

    def __init__(self, hass: HomeAssistant, host: str) -> None:
        super().__init__(hass, host, f"{DOMAIN}_info", DEFAULT_SCAN_INTERVAL)

    async def _fetch(self, session: ClientSession) -> dict[str, Any]:
        data, info_page = await asyncio.gather(
            api.get_info(session, self.base_url),
            api.get_info_page(session, self.base_url),
        )
        data["info_page"] = info_page
        return data


class FraimicBatteryCoordinator(FraimicBaseCoordinator):
    """Polls the lightweight /api/battery endpoint more frequently."""

    def __init__(self, hass: HomeAssistant, host: str) -> None:
        super().__init__(hass, host, f"{DOMAIN}_battery", DEFAULT_BATTERY_SCAN_INTERVAL)

    async def _fetch(self, session: ClientSession) -> dict[str, Any]:
        return await api.get_battery(session, self.base_url)


class FraimicAlbumsCoordinator(FraimicBaseCoordinator):
    """Polls the cloud-proxied /api/albums endpoint -- see api.get_albums.

    Gated on the main coordinator's device_reachable so this doesn't
    attempt (and time out on) a call already known to fail while the frame
    itself is asleep/unreachable -- that gate is purely an efficiency/
    politeness measure toward an unofficial, cloud-proxied endpoint, not a
    correctness requirement (a real attempt would fail the same way).
    """

    def __init__(
        self, hass: HomeAssistant, host: str, main_coordinator: FraimicCoordinator, entry_id: str
    ) -> None:
        super().__init__(hass, host, f"{DOMAIN}_albums", DEFAULT_ALBUMS_SCAN_INTERVAL)
        self._main_coordinator = main_coordinator
        self._issue_id = f"albums_sync_failing_{entry_id}"

    async def _fetch(self, session: ClientSession) -> dict[str, Any]:
        if not self._main_coordinator.device_reachable:
            # Same translation path (-> UpdateFailed) as every other
            # _fetch failure, via _async_update_data's except clause --
            # not raising UpdateFailed directly here, for consistency.
            raise HomeAssistantError("Frame not reachable -- skipping albums fetch")
        return await api.get_albums(session, self.base_url)

    def _log_reachability_change(self, was_reachable: bool) -> None:
        super()._log_reachability_change(was_reachable)
        # A repair issue here needs a signal that can't just be explained by
        # the frame being asleep (which is normal, expected, and frequent --
        # see the module docstring). Gating on the *main* coordinator's own
        # device_reachable, not this one's, is what makes that distinction:
        # this only fires while the frame itself is demonstrably awake and
        # answering, yet album sync specifically keeps failing -- pointing
        # at the cloud-proxied endpoint itself, not the frame's sleep cycle.
        if not self.device_reachable and self._main_coordinator.device_reachable:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self._issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="albums_sync_failing",
                translation_placeholders={"host": self.base_url},
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
