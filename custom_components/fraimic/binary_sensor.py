"""Binary sensors for Fraimic E-Ink Canvas.

Boolean fields from the API belong here, not as generic text sensors --
this gives Home Assistant proper on/off semantics, device-class icons,
and correct history/logbook behavior instead of a sensor whose state is
literally the string "True" or "False".
"""
from __future__ import annotations

from typing import Any, cast

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import FraimicBatteryCoordinator, FraimicCoordinator
from .entity import FraimicEntity
from .runtime_data import FraimicConfigEntry

# All state comes from the coordinators' shared poll, not per-entity I/O,
# so there's nothing for entities of this platform to serialize against.
PARALLEL_UPDATES = 0

BATTERY_BINARY_SENSORS: tuple[BinarySensorEntityDescription, ...] = (
    BinarySensorEntityDescription(
        key="charging",
        translation_key="charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BinarySensorEntityDescription(
        key="cable_connected",
        translation_key="cable_connected",
        device_class=BinarySensorDeviceClass.PLUG,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

# settings.* isn't documented in the official API guide, but it's already
# part of every /api/info poll this integration fetches -- no new network
# call or dependency to surface these. None of BinarySensorDeviceClass fits
# any of the four (no "keep awake"/"voice recording" class exists), so
# device_class is deliberately left unset, same as wifi_ip's sensor.
INFO_BINARY_SENSOR_DESCRIPTIONS: tuple[tuple[BinarySensorEntityDescription, tuple[str, ...]], ...] = (
    (
        BinarySensorEntityDescription(
            key="voice_recording", translation_key="voice_recording", entity_category=EntityCategory.DIAGNOSTIC
        ),
        ("settings", "voice_recording"),
    ),
    (
        BinarySensorEntityDescription(
            key="keep_awake", translation_key="keep_awake", entity_category=EntityCategory.DIAGNOSTIC
        ),
        ("settings", "keep_awake"),
    ),
    (
        BinarySensorEntityDescription(
            key="auto_update", translation_key="auto_update", entity_category=EntityCategory.DIAGNOSTIC
        ),
        ("settings", "auto_update"),
    ),
    (
        BinarySensorEntityDescription(
            key="charging_led", translation_key="charging_led", entity_category=EntityCategory.DIAGNOSTIC
        ),
        ("settings", "charging_led"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: FraimicConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    runtime = entry.runtime_data
    entities: list[BinarySensorEntity] = [
        FraimicBatteryBinarySensor(runtime.battery_coordinator, entry, description)
        for description in BATTERY_BINARY_SENSORS
    ]
    entities += [
        FraimicInfoBinarySensor(runtime.coordinator, entry, description, path)
        for description, path in INFO_BINARY_SENSOR_DESCRIPTIONS
    ]
    entities.append(FraimicReachableBinarySensor(runtime.coordinator, entry))
    entities.append(FraimicRenderProblemBinarySensor(runtime.coordinator, entry))
    async_add_entities(entities)


class FraimicBatteryBinarySensor(FraimicEntity, BinarySensorEntity):
    """A boolean value from /api/battery."""

    def __init__(
        self,
        coordinator: FraimicBatteryCoordinator,
        entry: ConfigEntry,
        description: BinarySensorEntityDescription,
    ) -> None:
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return (self.coordinator.data or {}).get(self.entity_description.key)


class FraimicInfoBinarySensor(FraimicEntity, BinarySensorEntity):
    """A (possibly nested) boolean value from /api/info."""

    def __init__(
        self,
        coordinator: FraimicCoordinator,
        entry: ConfigEntry,
        description: BinarySensorEntityDescription,
        path: tuple[str, ...],
    ) -> None:
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description
        self._path = path

    @property
    def is_on(self) -> bool | None:
        value: Any = self.coordinator.data or {}
        for key in self._path:
            if not isinstance(value, dict):
                return None
            value = value.get(key)
        # value's shape is only known at runtime (raw JSON from /api/info) --
        # the path this class is constructed with is what actually guarantees
        # it's a bool, not something mypy can see from here.
        return cast("bool | None", value)


class FraimicReachableBinarySensor(FraimicEntity, BinarySensorEntity):
    """Whether the most recent poll actually reached the frame.

    Unlike the other entities, this one is deliberately always available
    and flips on/off with every poll cycle -- that's the whole point: off
    simply means "asleep or unreachable right now", which is completely
    normal for this device, not an error state to hide.
    """

    _attr_translation_key = "reachable"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: FraimicCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "reachable")

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.last_update_success


class FraimicRenderProblemBinarySensor(FraimicEntity, BinarySensorEntity):
    """On when the frame reports failed display renders.

    display.render_attempts / display.render_failures aren't documented
    in the official API guide but are returned by /api/info in practice.
    Unlike a raw failure counter, this collapses them into a single
    actionable on/off signal suitable for automations/notifications.
    """

    _attr_translation_key = "render_problem"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: FraimicCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "render_problem")

    @property
    def is_on(self) -> bool | None:
        display = (self.coordinator.data or {}).get("display") or {}
        failures = display.get("render_failures")
        if failures is None:
            return None
        return bool(failures > 0)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        display = (self.coordinator.data or {}).get("display") or {}
        return {
            "render_attempts": display.get("render_attempts"),
            "render_failures": display.get("render_failures"),
        }
