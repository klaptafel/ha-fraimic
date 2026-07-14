"""Shared unique_id/device_info helpers and entity base class.

Consolidates what every Fraimic entity needs (unique_id, device_info, and
the tolerant `available` built on coordinator.device_reachable) so each
platform module only has to describe what's different about its entities.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import FraimicBaseCoordinator
from .runtime_data import device_key


def entity_unique_id(entry: ConfigEntry, key: str) -> str:
    return f"{device_key(entry)}_{key}"


def dig_path(data: dict[str, Any], path: tuple[str, ...]) -> Any:
    """Walk a (possibly nested) path through a dict, tolerating a shape
    mismatch at any level -- used by the FraimicInfoSensor/FraimicInfo
    BinarySensor platforms, whose path is only known at runtime (raw JSON
    from the frame's /api/info)."""
    value: Any = data
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


@dataclass(frozen=True)
class DeviceIdentity:
    """The device-identity fields shared by every entity's DeviceInfo *and*
    __init__.py's explicit device_reg.async_get_or_create() call (which adds
    model/sw_version on top, since those are only known once the coordinator
    has data). Kept in one place so the two never drift apart. A plain
    dataclass (not a dict/TypedDict) so each caller assigns its own fields
    explicitly -- DeviceInfo is a TypedDict, which mypy --strict won't let
    a plain dict's ** expansion construct or extend safely."""
    identifiers: set[tuple[str, str]]
    name: str
    manufacturer: str
    configuration_url: str


def device_identity(entry: ConfigEntry, base_url: str) -> DeviceIdentity:
    return DeviceIdentity(
        identifiers={(DOMAIN, device_key(entry))},
        name="Fraimic E-Ink Canvas",
        manufacturer="Fraimic",
        configuration_url=base_url,
    )


def device_info(entry: ConfigEntry, base_url: str) -> DeviceInfo:
    identity = device_identity(entry, base_url)
    return DeviceInfo(
        identifiers=identity.identifiers,
        name=identity.name,
        manufacturer=identity.manufacturer,
        configuration_url=identity.configuration_url,
    )


class FraimicEntity(CoordinatorEntity[FraimicBaseCoordinator]):
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

    def __init__(self, coordinator: FraimicBaseCoordinator, entry: ConfigEntry, key: str) -> None:
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
