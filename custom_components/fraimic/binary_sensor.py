"""Binary sensors for Fraimic E-Ink Canvas.

Boolean fields from the API belong here, not as generic text sensors --
this gives Home Assistant proper on/off semantics, device-class icons,
and correct history/logbook behavior instead of a sensor whose state is
literally the string "True" or "False".
"""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import FraimicBatteryCoordinator, FraimicCoordinator
from .runtime_data import FraimicRuntimeData, device_key

BATTERY_BINARY_SENSORS: tuple[BinarySensorEntityDescription, ...] = (
    BinarySensorEntityDescription(
        key="charging",
        name="Charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BinarySensorEntityDescription(
        key="cable_connected",
        name="Charging Cable Connected",
        device_class=BinarySensorDeviceClass.PLUG,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    runtime: FraimicRuntimeData = hass.data[DOMAIN][entry.entry_id]
    entities: list[BinarySensorEntity] = [
        FraimicBatteryBinarySensor(runtime.battery_coordinator, entry, description)
        for description in BATTERY_BINARY_SENSORS
    ]
    entities.append(FraimicReachableBinarySensor(runtime.coordinator, entry))
    entities.append(FraimicRenderProblemBinarySensor(runtime.coordinator, entry))
    async_add_entities(entities)


def _device_info(entry: ConfigEntry, coordinator) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, device_key(entry))},
        name="Fraimic E-Ink Canvas",
        manufacturer="Fraimic",
        configuration_url=coordinator.base_url,
    )


class FraimicBatteryBinarySensor(CoordinatorEntity[FraimicBatteryCoordinator], BinarySensorEntity):
    """A boolean value from /api/battery."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FraimicBatteryCoordinator,
        entry: ConfigEntry,
        description: BinarySensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{device_key(entry)}_{description.key}"
        self._attr_device_info = _device_info(entry, coordinator)

    @property
    def available(self) -> bool:
        # Tolerate expected deep-sleep gaps -- see coordinator.device_reachable.
        return self.coordinator.device_reachable

    @property
    def is_on(self) -> bool | None:
        return (self.coordinator.data or {}).get(self.entity_description.key)


class FraimicReachableBinarySensor(CoordinatorEntity[FraimicCoordinator], BinarySensorEntity):
    """Whether the most recent poll actually reached the frame.

    Unlike the other entities, this one is deliberately always available
    and flips on/off with every poll cycle -- that's the whole point: off
    simply means "asleep or unreachable right now", which is completely
    normal for this device, not an error state to hide.
    """

    _attr_has_entity_name = True
    _attr_name = "Reachable"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: FraimicCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{device_key(entry)}_reachable"
        self._attr_device_info = _device_info(entry, coordinator)

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.last_update_success


class FraimicRenderProblemBinarySensor(CoordinatorEntity[FraimicCoordinator], BinarySensorEntity):
    """On when the frame reports failed display renders.

    display.render_attempts / display.render_failures aren't documented
    in the official API guide but are returned by /api/info in practice.
    Unlike a raw failure counter, this collapses them into a single
    actionable on/off signal suitable for automations/notifications.
    """

    _attr_has_entity_name = True
    _attr_name = "Render Problem"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: FraimicCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{device_key(entry)}_render_problem"
        self._attr_device_info = _device_info(entry, coordinator)

    @property
    def available(self) -> bool:
        # Tolerate expected deep-sleep gaps -- see coordinator.device_reachable.
        return self.coordinator.device_reachable

    @property
    def is_on(self) -> bool | None:
        display = (self.coordinator.data or {}).get("display") or {}
        failures = display.get("render_failures")
        if failures is None:
            return None
        return failures > 0

    @property
    def extra_state_attributes(self) -> dict:
        display = (self.coordinator.data or {}).get("display") or {}
        return {
            "render_attempts": display.get("render_attempts"),
            "render_failures": display.get("render_failures"),
        }
