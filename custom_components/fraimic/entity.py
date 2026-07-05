"""Shared unique_id/device_info helpers and entity base class.

Consolidates what every Fraimic entity needs (unique_id, device_info, and
the tolerant `available` built on coordinator.device_reachable) so each
platform module only has to describe what's different about its entities.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .runtime_data import device_key


def entity_unique_id(entry: ConfigEntry, key: str) -> str:
    return f"{device_key(entry)}_{key}"


def device_info(entry: ConfigEntry, base_url: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, device_key(entry))},
        name="Fraimic E-Ink Canvas",
        manufacturer="Fraimic",
        configuration_url=base_url,
    )


class FraimicEntity(CoordinatorEntity):
    """Common base for entities backed by one of the Fraimic coordinators.

    Set `_fraimic_always_available = True` on a subclass to opt out of the
    default 72h-tolerant model entirely, for entities whose whole job is to
    surface *that* the frame is unreachable (FraimicLastSeenSensor,
    FraimicMediaPlayer, FraimicStatusSensor) -- if one of those went
    unavailable via the default model, the exact message/value it exists to
    show would be replaced by HA's own generic "unavailable" instead.
    """

    _attr_has_entity_name = True
    _fraimic_always_available = False

    def __init__(self, coordinator, entry: ConfigEntry, key: str) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = entity_unique_id(entry, key)
        self._attr_device_info = device_info(entry, coordinator.base_url)

    @property
    def available(self) -> bool:
        if self._fraimic_always_available:
            return True
        # Tolerate expected deep-sleep gaps -- see coordinator.device_reachable.
        return self.coordinator.device_reachable
