"""Tests for the button platform (restart/sleep/refresh)."""
from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fraimic.const import CONF_HOST, DOMAIN

HOST = "http://1.2.3.4"
INFO = {"device": {"device_key": "abc123"}}
BATTERY = {"percent": 50}


def _entity_id(hass: HomeAssistant, key: str) -> str:
    entity_reg = er.async_get(hass)
    entity_id = entity_reg.async_get_entity_id("button", DOMAIN, f"abc123_{key}")
    assert entity_id is not None
    return entity_id


async def _setup(hass: HomeAssistant, aioclient_mock) -> MockConfigEntry:
    aioclient_mock.get(f"{HOST}/api/info", json=INFO)
    aioclient_mock.get(f"{HOST}/api/battery", json=BATTERY)
    entry = MockConfigEntry(domain=DOMAIN, unique_id="abc123", data={CONF_HOST: HOST})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.mark.parametrize(
    ("key", "endpoint"),
    [("restart", "/api/restart"), ("sleep", "/api/sleep"), ("refresh", "/api/refresh")],
)
async def test_button_press_calls_correct_endpoint(
    hass: HomeAssistant, aioclient_mock, key, endpoint
) -> None:
    await _setup(hass, aioclient_mock)
    aioclient_mock.post(f"{HOST}{endpoint}", json={})

    await hass.services.async_call(
        "button", "press", {"entity_id": _entity_id(hass, key)}, blocking=True
    )

    calls = [c for c in aioclient_mock.mock_calls if c[1].path == endpoint]
    assert len(calls) == 1


async def test_button_press_surfaces_frame_error(hass: HomeAssistant, aioclient_mock) -> None:
    await _setup(hass, aioclient_mock)
    aioclient_mock.post(f"{HOST}/api/sleep", json={"error": "charging_cable_connected"})

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            "button", "press", {"entity_id": _entity_id(hass, "sleep")}, blocking=True
        )
    assert exc_info.value.translation_key == "charging_cable_connected"


async def test_button_press_wraps_unexpected_error(hass: HomeAssistant, aioclient_mock) -> None:
    from homeassistant.helpers.entity_component import DATA_INSTANCES

    await _setup(hass, aioclient_mock)
    entity = hass.data[DATA_INSTANCES]["button"].get_entity(_entity_id(hass, "restart"))

    async def _boom(session, base_url):
        raise ValueError("frame said something weird")

    entity._action = _boom

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            "button", "press", {"entity_id": _entity_id(hass, "restart")}, blocking=True
        )
    assert exc_info.value.translation_key == "unexpected_error"
    assert exc_info.value.translation_placeholders == {"error": "frame said something weird"}


async def test_button_unavailable_when_frame_unreachable(
    hass: HomeAssistant, aioclient_mock
) -> None:
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    from custom_components.fraimic.const import UNAVAILABLE_AFTER

    entry = await _setup(hass, aioclient_mock)
    entry.runtime_data.coordinator._last_success = dt_util.utcnow() - (
        UNAVAILABLE_AFTER + timedelta(minutes=1)
    )
    entry.runtime_data.coordinator.async_update_listeners()
    await hass.async_block_till_done()

    state = hass.states.get(_entity_id(hass, "restart"))
    assert state.state == "unavailable"
