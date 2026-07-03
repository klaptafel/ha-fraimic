"""Buttons for Fraimic E-Ink Canvas: restart, sleep, refresh."""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import api
from .const import DOMAIN
from .entity import FraimicEntity
from .runtime_data import FraimicConfigEntry, FraimicRuntimeData

_LOGGER = logging.getLogger(__name__)

# The frame's embedded web server is a small single device -- serialize
# button presses (restart/sleep/refresh) rather than firing them at it
# concurrently.
PARALLEL_UPDATES = 1

BUTTONS: tuple[tuple[ButtonEntityDescription, Callable[..., Awaitable[None]]], ...] = (
    (
        ButtonEntityDescription(
            key="restart",
            translation_key="restart",
            device_class=ButtonDeviceClass.RESTART,
            entity_category=EntityCategory.CONFIG,
        ),
        api.restart,
    ),
    (
        # No matching ButtonDeviceClass (only IDENTIFY/RESTART/UPDATE exist)
        # -- sleep is a device-lifecycle action like restart, so it gets
        # the same CONFIG category even without a device class to match.
        ButtonEntityDescription(
            key="sleep", translation_key="sleep", entity_category=EntityCategory.CONFIG
        ),
        api.sleep,
    ),
    (
        # Deliberately NOT EntityCategory.CONFIG: forcing a display refresh
        # is a core, everyday action for an e-ink photo frame, not a
        # maintenance task -- it should stay a primary entity.
        ButtonEntityDescription(key="refresh", translation_key="refresh"),
        api.refresh,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: FraimicConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    runtime = entry.runtime_data
    async_add_entities(
        FraimicButton(runtime, entry, description, action) for description, action in BUTTONS
    )


class FraimicButton(FraimicEntity, ButtonEntity):
    """Fires a POST action against the frame, surfacing the frame's own error messages."""

    def __init__(
        self,
        runtime: FraimicRuntimeData,
        entry: ConfigEntry,
        description: ButtonEntityDescription,
        action: Callable[..., Awaitable[None]],
    ) -> None:
        super().__init__(runtime.coordinator, entry, description.key)
        self._runtime = runtime
        self._action = action
        self.entity_description = description

    async def async_press(self) -> None:
        session = async_get_clientsession(self._runtime.coordinator.hass)
        try:
            await self._action(session, self._runtime.base_url)
        except HomeAssistantError:
            raise
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception("Fraimic %s command failed", self.entity_description.key)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="unexpected_error",
                translation_placeholders={"error": str(err)},
            ) from err
