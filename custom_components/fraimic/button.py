"""Buttons for Fraimic E-Ink Canvas: restart, sleep, refresh."""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import api
from .const import DOMAIN
from .runtime_data import FraimicRuntimeData, device_key

_LOGGER = logging.getLogger(__name__)

BUTTONS: tuple[tuple[ButtonEntityDescription, Callable[..., Awaitable[None]]], ...] = (
    (ButtonEntityDescription(key="restart", name="Restart", icon="mdi:restart"), api.restart),
    (ButtonEntityDescription(key="sleep", name="Sleep", icon="mdi:sleep"), api.sleep),
    (ButtonEntityDescription(key="refresh", name="Refresh Display", icon="mdi:monitor"), api.refresh),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    runtime: FraimicRuntimeData = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        FraimicButton(runtime, entry, description, action) for description, action in BUTTONS
    )


class FraimicButton(ButtonEntity):
    """Fires a POST action against the frame, surfacing the frame's own error messages."""

    _attr_has_entity_name = True

    def __init__(
        self,
        runtime: FraimicRuntimeData,
        entry: ConfigEntry,
        description: ButtonEntityDescription,
        action: Callable[..., Awaitable[None]],
    ) -> None:
        self._runtime = runtime
        self._action = action
        self.entity_description = description
        self._attr_unique_id = f"{device_key(entry)}_{description.key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, device_key(entry))})

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # The frame is unreachable while asleep -- reflect that here instead
        # of only finding out when a tap fails.
        self.async_on_remove(
            self._runtime.coordinator.async_add_listener(self.async_write_ha_state)
        )

    @property
    def available(self) -> bool:
        return self._runtime.coordinator.device_reachable

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
