"""Sensors for Fraimic E-Ink Canvas."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EntityCategory,
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfElectricPotential,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import FraimicBatteryCoordinator, FraimicCoordinator
from .runtime_data import FraimicRuntimeData, device_key

# Sensors backed by the fast-polling /api/battery endpoint (60s).
# Boolean fields (charging, cable_connected) live in binary_sensor.py instead.
BATTERY_SENSOR_DESCRIPTIONS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="percent",
        name="Battery",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
    ),
    SensorEntityDescription(
        key="voltage_mv",
        name="Battery Voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.MILLIVOLT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
)

# Sensors backed by the slower /api/info snapshot (5 min).
# Boolean fields (registered) live in binary_sensor.py instead.
INFO_SENSOR_DESCRIPTIONS: tuple[tuple[SensorEntityDescription, tuple[str, ...]], ...] = (
    (
        SensorEntityDescription(
            key="wifi_rssi",
            name="WiFi Signal",
            device_class=SensorDeviceClass.SIGNAL_STRENGTH,
            state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        ("wifi", "rssi"),
    ),
    (
        SensorEntityDescription(
            key="wifi_ip", name="IP Address", entity_category=EntityCategory.DIAGNOSTIC
        ),
        ("wifi", "ip"),
    ),
    (
        SensorEntityDescription(
            key="next_refresh",
            name="Next Scheduled Refresh",
            device_class=SensorDeviceClass.TIMESTAMP,
        ),
        ("display", "next_refresh"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    runtime: FraimicRuntimeData = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = [
        FraimicBatterySensor(runtime.battery_coordinator, entry, description)
        for description in BATTERY_SENSOR_DESCRIPTIONS
    ]
    entities += [
        FraimicInfoSensor(runtime.coordinator, entry, description, path)
        for description, path in INFO_SENSOR_DESCRIPTIONS
    ]
    entities.append(FraimicLastSeenSensor(runtime.coordinator, entry))
    async_add_entities(entities)


def _device_info(entry: ConfigEntry, coordinator) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, device_key(entry))},
        name="Fraimic E-Ink Canvas",
        manufacturer="Fraimic",
        configuration_url=coordinator.base_url,
    )


def _parse_timestamp(value):
    """Parse an ISO datetime string (e.g. '2026-07-04T07:00:00') from the
    frame into a timezone-aware datetime, as required by
    SensorDeviceClass.TIMESTAMP.

    The frame's API doesn't specify a timezone in its timestamps. Since the
    frame syncs its clock (device.time_synced) and reports "local_time",
    we assume these are in Home Assistant's configured local timezone
    rather than UTC.
    """
    if not value or not isinstance(value, str):
        return None
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
    return dt_util.as_utc(parsed)


class FraimicBatterySensor(CoordinatorEntity[FraimicBatteryCoordinator], SensorEntity):
    """A value from /api/battery."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: FraimicBatteryCoordinator, entry: ConfigEntry, description: SensorEntityDescription
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
    def native_value(self):
        return (self.coordinator.data or {}).get(self.entity_description.key)


class FraimicInfoSensor(CoordinatorEntity[FraimicCoordinator], SensorEntity):
    """A (possibly nested) value from /api/info."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FraimicCoordinator,
        entry: ConfigEntry,
        description: SensorEntityDescription,
        path: tuple[str, ...],
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._path = path
        self._attr_unique_id = f"{device_key(entry)}_{description.key}"
        self._attr_device_info = _device_info(entry, coordinator)

    @property
    def available(self) -> bool:
        # Tolerate expected deep-sleep gaps -- see coordinator.device_reachable.
        return self.coordinator.device_reachable

    @property
    def native_value(self):
        value = self.coordinator.data or {}
        for key in self._path:
            if not isinstance(value, dict):
                return None
            value = value.get(key)

        if self.entity_description.device_class == SensorDeviceClass.TIMESTAMP:
            return _parse_timestamp(value)
        return value

    @property
    def extra_state_attributes(self) -> dict | None:
        data = self.coordinator.data or {}
        key = self.entity_description.key

        if key == "wifi_rssi":
            wifi = data.get("wifi") or {}
            return {
                "ssid": wifi.get("ssid"),
                "band": wifi.get("band"),
                "channel": wifi.get("channel"),
                "bssid": wifi.get("bssid"),
                "mac_address": wifi.get("mac"),
            }

        if key == "next_refresh":
            display = data.get("display") or {}
            return {
                "interval_days": display.get("refresh_interval_days"),
                "hour": display.get("refresh_hour"),
            }

        return None


class FraimicLastSeenSensor(CoordinatorEntity[FraimicCoordinator], SensorEntity):
    """When the frame was last successfully reached.

    Deliberately always available (even while the frame is asleep) so it
    can actually answer "how long has it been?" instead of going blank
    exactly when that answer matters most.
    """

    _attr_has_entity_name = True
    _attr_name = "Last Seen"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: FraimicCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{device_key(entry)}_last_seen"
        self._attr_device_info = _device_info(entry, coordinator)

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self):
        return self.coordinator.last_success
