"""Sensors for Fraimic E-Ink Canvas."""
from __future__ import annotations

from datetime import datetime
from typing import Any, cast

import voluptuous as vol
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    EntityCategory,
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfElectricPotential,
)
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from . import api
from .const import (
    ATTR_ACTIVE,
    ATTR_ALBUM_ID,
    ATTR_ALBUM_NAME,
    ATTR_DAYS,
    ATTR_DESCRIPTION,
    ATTR_INTERVAL_UNIT,
    ATTR_INTERVAL_VALUE,
    ATTR_PLAYBACK_MODE,
    ATTR_SCHEDULE_TYPE,
    DOMAIN,
    PLAYBACK_MODES,
    SCHEDULE_DAYS,
    SCHEDULE_INTERVAL_UNITS,
    SCHEDULE_TYPES,
    SERVICE_UPDATE_ALBUM,
)
from .coordinator import FraimicAlbumsCoordinator, FraimicCoordinator
from .entity import FraimicEntity
from .runtime_data import FraimicConfigEntry, FraimicRuntimeData, send_status_signal

# All state comes from the coordinators' shared poll, not per-entity I/O,
# so there's nothing for entities of this platform to serialize against.
PARALLEL_UPDATES = 0

# A plain domain service (not an entity service) -- deliberately, so a
# call is never silently skipped by HA's own entity_service_call, which
# drops the request with no error/log for any entity that's currently
# unavailable (homeassistant/helpers/service.py). That's exactly the
# failure mode this write action must not have: the albums sensor is
# tolerant-availability (frame reachable over LAN but no internet, for
# example), and silently doing nothing to a write the user explicitly
# triggered would be far worse than for a read-only display.
UPDATE_ALBUM_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_ALBUM_ID): cv.string,
        vol.Optional(ATTR_ALBUM_NAME): cv.string,
        vol.Optional(ATTR_DESCRIPTION): cv.string,
        vol.Optional(ATTR_ACTIVE): cv.boolean,
        vol.Optional(ATTR_PLAYBACK_MODE): vol.In(PLAYBACK_MODES),
        vol.Optional(ATTR_SCHEDULE_TYPE): vol.In(SCHEDULE_TYPES),
        vol.Optional(ATTR_INTERVAL_VALUE): vol.Coerce(int),
        vol.Optional(ATTR_INTERVAL_UNIT): vol.In(SCHEDULE_INTERVAL_UNITS),
        vol.Optional(ATTR_DAYS): vol.All(cv.ensure_list, [vol.In(SCHEDULE_DAYS)]),
    }
)

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
        FraimicBatterySensor(runtime, entry, description)
        for description in BATTERY_SENSOR_DESCRIPTIONS
    ]
    entities += [
        FraimicInfoSensor(runtime.coordinator, entry, description, path)
        for description, path in INFO_SENSOR_DESCRIPTIONS
    ]
    entities.append(FraimicLastSeenSensor(runtime.coordinator, entry))
    entities.append(FraimicStatusSensor(runtime, entry))
    entities.append(FraimicAlbumsSensor(runtime.albums_coordinator, entry))
    async_add_entities(entities)

    if not hass.services.has_service(DOMAIN, SERVICE_UPDATE_ALBUM):

        async def _async_handle_update_album(call: ServiceCall) -> None:
            await _async_update_album(hass, call)

        hass.services.async_register(
            DOMAIN, SERVICE_UPDATE_ALBUM, _async_handle_update_album, schema=UPDATE_ALBUM_SCHEMA
        )


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


def _flatten_schedule(schedule: dict[str, Any]) -> str:
    """Render an album's schedule as short, readable text (e.g. "every 24
    hours" / "monday, wednesday") instead of exposing the raw nested dict
    as an entity attribute."""
    schedule_type = schedule.get("type")
    if schedule_type == "interval":
        return f"every {schedule.get('interval_value')} {schedule.get('interval_unit')}"
    if schedule_type == "specific_days":
        days = schedule.get("days") or []
        return ", ".join(days)
    return "unknown schedule"


def _build_schedule(
    schedule_type: str,
    interval_value: int | None,
    interval_unit: str | None,
    days: list[str] | None,
) -> dict[str, Any]:
    """Reassemble the update_album service's flat schedule_type/
    interval_value/interval_unit/days fields into the nested shape the
    cloud API expects -- symmetric with _flatten_schedule above.

    Sent as-is, whichever fields the caller didn't pass stay None -- the
    cloud API treats `schedule` as a full replacement, not a merge
    (confirmed via curl: PUTting {"type": "specific_days", "days": [...]}
    nulls out interval_value/interval_unit server-side), so this always
    produces the complete shape for whichever type is being set rather
    than a partial one that could be misread as "keep the old values".
    """
    return {
        "type": schedule_type,
        "interval_value": interval_value,
        "interval_unit": interval_unit,
        "days": days,
    }


async def _async_update_album(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handler for fraimic.update_album -- resolves entity_id to the
    owning config entry itself (rather than via HA's entity-service
    helpers) so an unavailable or unknown entity raises a clear error
    instead of silently doing nothing -- see UPDATE_ALBUM_SCHEMA's comment
    for why that matters here specifically."""
    entity_id = call.data[ATTR_ENTITY_ID]
    entity_entry = er.async_get(hass).async_get(entity_id)
    config_entry = (
        hass.config_entries.async_get_entry(entity_entry.config_entry_id)
        if entity_entry and entity_entry.config_entry_id
        else None
    )
    # Checked beyond "is this registered at all" -- the services.yaml
    # selector already scopes the picker to this integration's sensors,
    # but doesn't stop someone choosing e.g. the Battery sensor by mistake.
    # unique_id's "_albums" suffix (see entity_unique_id/FraimicAlbumsSensor)
    # confirms it's specifically the entity this service is meant for.
    if (
        entity_entry is None
        or config_entry is None
        or config_entry.domain != DOMAIN
        or not (entity_entry.unique_id or "").endswith("_albums")
    ):
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="entity_not_found",
            translation_placeholders={"entity_id": entity_id},
        )
    runtime: FraimicRuntimeData = config_entry.runtime_data

    fields: dict[str, Any] = {
        key: call.data[attr]
        for attr, key in (
            (ATTR_ALBUM_NAME, "name"),
            (ATTR_DESCRIPTION, "description"),
            (ATTR_ACTIVE, "active"),
            (ATTR_PLAYBACK_MODE, "playback_mode"),
        )
        if attr in call.data
    }
    if ATTR_SCHEDULE_TYPE in call.data:
        fields["schedule"] = _build_schedule(
            call.data[ATTR_SCHEDULE_TYPE],
            call.data.get(ATTR_INTERVAL_VALUE),
            call.data.get(ATTR_INTERVAL_UNIT),
            call.data.get(ATTR_DAYS),
        )

    session = async_get_clientsession(hass)
    await api.update_album(session, runtime.base_url, call.data[ATTR_ALBUM_ID], **fields)
    # Reflect the change immediately instead of waiting up to 30 minutes
    # for the next scheduled poll. Note: async_request_refresh() never
    # raises on failure (a failed fetch is only visible via
    # last_update_success/last_exception) -- if FraimicAlbumsCoordinator's
    # gate on the main coordinator's device_reachable happens to trip in
    # this exact narrow window, this refresh silently no-ops and the
    # sensor keeps showing pre-write data until the next poll. Not worth
    # engineering around given how narrow that window is.
    await runtime.albums_coordinator.async_request_refresh()


class FraimicBatterySensor(FraimicEntity, SensorEntity):
    """A value from /api/battery.

    "percent"'s extra_state_attributes additionally surfaces battery cycle
    count/health/current/temperature -- scraped from the undocumented
    /info admin page (see api.get_info_page; /api/battery's JSON doesn't
    have any of these). That scrape rides along on the main coordinator's
    slower 5-minute poll, not this entity's own faster battery_coordinator
    -- hence needing the whole FraimicRuntimeData, not just one coordinator.
    """

    def __init__(
        self, runtime: FraimicRuntimeData, entry: ConfigEntry, description: SensorEntityDescription
    ) -> None:
        super().__init__(runtime.battery_coordinator, entry, description.key)
        self.entity_description = description
        self._runtime = runtime

    @property
    def native_value(self) -> StateType:
        return (self.coordinator.data or {}).get(self.entity_description.key)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.key != "percent":
            return None
        info_page = (self._runtime.coordinator.data or {}).get("info_page") or {}
        attrs = {
            attr: info_page[source_key]
            for attr, source_key in (
                ("cycles", "battery_cycles"),
                ("health_percent", "battery_health_percent"),
                ("current_ma", "battery_current_ma"),
                ("temperature_c", "battery_temperature_c"),
            )
            if source_key in info_page
        }
        return attrs or None


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


class FraimicAlbumsSensor(FraimicEntity, SensorEntity):
    """Read-only listing of albums from the undocumented, cloud-proxied
    /api/albums endpoint -- see coordinator.FraimicAlbumsCoordinator and
    api.get_albums for why this only updates while the frame has real
    internet access, unlike every other entity in this integration.

    Deliberately excludes each image's presigned S3 URL from attributes --
    those expire in about an hour and are functionally bearer credentials
    embedded in a URL, neither of which belongs in HA's state history.
    """

    _attr_translation_key = "albums"

    def __init__(self, coordinator: FraimicAlbumsCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "albums")

    @property
    def native_value(self) -> int:
        return len((self.coordinator.data or {}).get("albums", []))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        albums = (self.coordinator.data or {}).get("albums", [])
        return {
            "albums": [
                {
                    "id": album.get("id"),
                    "name": album.get("name"),
                    "active": album.get("active"),
                    "playback_mode": album.get("playback_mode"),
                    "image_count": album.get("image_count"),
                    "schedule": _flatten_schedule(album.get("schedule") or {}),
                }
                for album in albums
            ]
        }
