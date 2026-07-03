"""Integration tests for the sensor and binary_sensor platforms."""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fraimic.const import CONF_HOST, DOMAIN

HOST = "http://1.2.3.4"
INFO = {
    "device": {"device_key": "abc123"},
    "wifi": {"rssi": -55, "ip": "1.2.3.4"},
    "display": {"next_refresh": "2026-07-04T07:00:00", "render_attempts": 10, "render_failures": 2},
}
BATTERY = {"percent": 66, "voltage_mv": 4100, "charging": True, "cable_connected": True}


def _entity_id(hass: HomeAssistant, platform: str, unique_id: str) -> str:
    entity_reg = er.async_get(hass)
    entity_id = entity_reg.async_get_entity_id(platform, DOMAIN, f"abc123_{unique_id}")
    assert entity_id is not None, f"no entity registered for abc123_{unique_id}"
    return entity_id


async def _setup(hass: HomeAssistant, aioclient_mock) -> MockConfigEntry:
    aioclient_mock.get(f"{HOST}/api/info", json=INFO)
    aioclient_mock.get(f"{HOST}/api/battery", json=BATTERY)
    entry = MockConfigEntry(domain=DOMAIN, unique_id="abc123", data={CONF_HOST: HOST})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_battery_sensors(hass: HomeAssistant, aioclient_mock) -> None:
    await _setup(hass, aioclient_mock)

    percent_state = hass.states.get(_entity_id(hass, "sensor", "percent"))
    assert percent_state.state == "66"

    # voltage_mv is disabled by default -- no state, but it must exist in
    # the entity registry so a user can opt in.
    entity_reg = er.async_get(hass)
    voltage_entry = entity_reg.async_get(_entity_id(hass, "sensor", "voltage_mv"))
    assert voltage_entry.disabled_by is not None


async def test_info_sensors(hass: HomeAssistant, aioclient_mock) -> None:
    await _setup(hass, aioclient_mock)

    # wifi_rssi is disabled by default (HA's own docs use RSSI as the
    # textbook example of a diagnostic entity that should ship disabled),
    # so there's no live state -- just confirm it's registered and opt-in.
    entity_reg = er.async_get(hass)
    rssi_entry = entity_reg.async_get(_entity_id(hass, "sensor", "wifi_rssi"))
    assert rssi_entry.disabled_by is not None

    ip_state = hass.states.get(_entity_id(hass, "sensor", "wifi_ip"))
    assert ip_state.state == "1.2.3.4"

    refresh_state = hass.states.get(_entity_id(hass, "sensor", "next_refresh"))
    assert refresh_state.state != "unknown"


async def test_wifi_rssi_extra_state_attributes(hass: HomeAssistant, aioclient_mock) -> None:
    """wifi_rssi is disabled by default, so it's never live-polled through
    the entity platform in these tests -- construct it directly to cover
    its extra_state_attributes (wifi ssid/band/channel/bssid/mac)."""
    from custom_components.fraimic.sensor import INFO_SENSOR_DESCRIPTIONS, FraimicInfoSensor

    entry = await _setup(hass, aioclient_mock)
    description, path = next(d for d in INFO_SENSOR_DESCRIPTIONS if d[0].key == "wifi_rssi")

    sensor = FraimicInfoSensor(entry.runtime_data.coordinator, entry, description, path)
    attrs = sensor.extra_state_attributes
    assert attrs == {"ssid": None, "band": None, "channel": None, "bssid": None, "mac_address": None}


async def test_last_seen_sensor_always_available(hass: HomeAssistant, aioclient_mock) -> None:
    entry = await _setup(hass, aioclient_mock)
    last_seen_state = hass.states.get(_entity_id(hass, "sensor", "last_seen"))
    assert last_seen_state.state != "unavailable"

    entry.runtime_data.coordinator._last_success = None
    entry.runtime_data.coordinator.async_update_listeners()
    await hass.async_block_till_done()
    last_seen_state = hass.states.get(_entity_id(hass, "sensor", "last_seen"))
    assert last_seen_state.state != "unavailable"


async def test_binary_sensors(hass: HomeAssistant, aioclient_mock) -> None:
    await _setup(hass, aioclient_mock)

    charging_state = hass.states.get(_entity_id(hass, "binary_sensor", "charging"))
    assert charging_state.state == "on"

    cable_state = hass.states.get(_entity_id(hass, "binary_sensor", "cable_connected"))
    assert cable_state.state == "on"

    reachable_state = hass.states.get(_entity_id(hass, "binary_sensor", "reachable"))
    assert reachable_state.state == "on"

    render_problem_state = hass.states.get(_entity_id(hass, "binary_sensor", "render_problem"))
    assert render_problem_state.state == "on"
    assert render_problem_state.attributes["render_attempts"] == 10
    assert render_problem_state.attributes["render_failures"] == 2


async def test_entities_unavailable_when_frame_unreachable_too_long(
    hass: HomeAssistant, aioclient_mock
) -> None:
    entry = await _setup(hass, aioclient_mock)

    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    from custom_components.fraimic.const import UNAVAILABLE_AFTER

    entry.runtime_data.coordinator._last_success = dt_util.utcnow() - (
        UNAVAILABLE_AFTER + timedelta(minutes=1)
    )
    entry.runtime_data.coordinator.async_update_listeners()
    await hass.async_block_till_done()

    ip_state = hass.states.get(_entity_id(hass, "sensor", "wifi_ip"))
    assert ip_state.state == "unavailable"

    # But the "Reachable" and "Last Seen" diagnostics stay available --
    # that's the whole point of exposing them.
    reachable_state = hass.states.get(_entity_id(hass, "binary_sensor", "reachable"))
    assert reachable_state.state != "unavailable"
