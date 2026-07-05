"""Sensors for Fraimic E-Ink Canvas."""
from __future__ import annotations

from datetime import datetime
from typing import Any, cast

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
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .coordinator import FraimicBatteryCoordinator, FraimicCoordinator
from .entity import FraimicEntity
from .runtime_data import FraimicConfigEntry, FraimicRuntimeData, send_status_signal

# All state comes from the coordinators' shared poll, not per-entity I/O,
# so there's nothing for entities of this platform to serialize against.
PARALLEL_UPDATES = 0

# Sensors backed by the fast-polling /api/battery endpoint (60s).
# Boolean fields (charging, cable_connected) live in binary_sensor.py instead.
BATTERY_SENSOR_DESCRIPTIONS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="percent",
        translation_key="percent",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
    ),
    SensorEntityDescription(
        key="voltage_mv",
        translation_key="voltage_mv",
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
            translation_key="wifi_rssi",
            device_class=SensorDeviceClass.SIGNAL_STRENGTH,
            state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
            entity_category=EntityCategory.DIAGNOSTIC,
            # HA's own entity docs use "RSSI" as the textbook example of a
            # diagnostic entity that should ship disabled by default.
            entity_registry_enabled_default=False,
        ),
        ("wifi", "rssi"),
    ),
    (
        SensorEntityDescription(
            key="wifi_ip", translation_key="wifi_ip", entity_category=EntityCategory.DIAGNOSTIC
        ),
        ("wifi", "ip"),
    ),
    (
        SensorEntityDescription(
            key="next_refresh",
            translation_key="next_refresh",
            device_class=SensorDeviceClass.TIMESTAMP,
        ),
        ("display", "next_refresh"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: FraimicConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    runtime = entry.runtime_data

    entities: list[SensorEntity] = [
        FraimicBatterySensor(runtime.battery_coordinator, entry, description)
        for description in BATTERY_SENSOR_DESCRIPTIONS
    ]
    entities += [
        FraimicInfoSensor(runtime.coordinator, entry, description, path)
        for description, path in INFO_SENSOR_DESCRIPTIONS
    ]
    entities.append(FraimicLastSeenSensor(runtime.coordinator, entry))
    entities.append(FraimicStatusSensor(runtime, entry))
    async_add_entities(entities)


def _parse_timestamp(value: Any) -> datetime | None:
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


class FraimicBatterySensor(FraimicEntity, SensorEntity):
    """A value from /api/battery."""

    def __init__(
        self, coordinator: FraimicBatteryCoordinator, entry: ConfigEntry, description: SensorEntityDescription
    ) -> None:
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType:
        return (self.coordinator.data or {}).get(self.entity_description.key)


class FraimicInfoSensor(FraimicEntity, SensorEntity):
    """A (possibly nested) value from /api/info."""

    def __init__(
        self,
        coordinator: FraimicCoordinator,
        entry: ConfigEntry,
        description: SensorEntityDescription,
        path: tuple[str, ...],
    ) -> None:
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description
        self._path = path

    @property
    def native_value(self) -> StateType | datetime:
        value: Any = self.coordinator.data or {}
        for key in self._path:
            if not isinstance(value, dict):
                return None
            value = value.get(key)

        if self.entity_description.device_class == SensorDeviceClass.TIMESTAMP:
            return _parse_timestamp(value)
        # value's shape is only known at runtime (raw JSON from the
        # frame's /api/info) -- the SensorEntityDescription paths this
        # class is constructed with are what actually guarantee it's a
        # plain scalar, not something mypy can see from here.
        return cast("StateType", value)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
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


class FraimicLastSeenSensor(FraimicEntity, SensorEntity):
    """When the frame was last successfully reached.

    Deliberately always available (even while the frame is asleep) so it
    can actually answer "how long has it been?" instead of going blank
    exactly when that answer matters most.
    """

    _attr_translation_key = "last_seen"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _fraimic_always_available = True

    def __init__(self, coordinator: FraimicCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "last_seen")

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.last_success


class FraimicStatusSensor(FraimicEntity, SensorEntity):
    """Plain-text mirror of the media player's status (FraimicRuntimeData.
    status_text) -- same information, just a first-class entity instead of
    tucked away in the media player's more-info dialog, so it's easy to
    put on a dashboard or reference in an automation/template.
    """

    _attr_translation_key = "send_status"
    _fraimic_always_available = True

    def __init__(self, runtime: FraimicRuntimeData, entry: ConfigEntry) -> None:
        super().__init__(runtime.coordinator, entry, "send_status")
        self._runtime = runtime
        self._send_status_signal = send_status_signal(entry)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self._send_status_signal, self.async_write_ha_state)
        )

    @property
    def native_value(self) -> str | None:
        return self._runtime.status_text
