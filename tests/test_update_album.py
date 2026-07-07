"""Tests for the fraimic.update_album service (sensor.py)."""
from __future__ import annotations

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fraimic.const import CONF_HOST, DOMAIN

HOST = "http://1.2.3.4"
INFO = {"device": {"device_key": "abc123"}}
BATTERY = {"percent": 50}


def _albums_entity_id(hass: HomeAssistant) -> str:
    entity_reg = er.async_get(hass)
    entity_id = entity_reg.async_get_entity_id("sensor", DOMAIN, "abc123_albums")
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


async def test_update_album_reconstructs_interval_schedule(
    hass: HomeAssistant, aioclient_mock
) -> None:
    await _setup(hass, aioclient_mock)
    aioclient_mock.put(f"{HOST}/api/albums/album-1", json={"id": "album-1"})

    await hass.services.async_call(
        DOMAIN,
        "update_album",
        {
            "entity_id": _albums_entity_id(hass),
            "album_id": "album-1",
            "schedule_type": "interval",
            "interval_value": 6,
            "interval_unit": "hours",
        },
        blocking=True,
    )

    put_calls = [c for c in aioclient_mock.mock_calls if c[1].path == "/api/albums/album-1"]
    assert len(put_calls) == 1
    assert put_calls[0][2] == {
        "schedule": {
            "type": "interval",
            "interval_value": 6,
            "interval_unit": "hours",
            "days": None,
        }
    }


async def test_update_album_reconstructs_specific_days_schedule(
    hass: HomeAssistant, aioclient_mock
) -> None:
    await _setup(hass, aioclient_mock)
    aioclient_mock.put(f"{HOST}/api/albums/album-1", json={"id": "album-1"})

    await hass.services.async_call(
        DOMAIN,
        "update_album",
        {
            "entity_id": _albums_entity_id(hass),
            "album_id": "album-1",
            "schedule_type": "specific_days",
            "days": ["monday", "friday"],
        },
        blocking=True,
    )

    put_calls = [c for c in aioclient_mock.mock_calls if c[1].path == "/api/albums/album-1"]
    assert put_calls[0][2] == {
        "schedule": {
            "type": "specific_days",
            "interval_value": None,
            "interval_unit": None,
            "days": ["monday", "friday"],
        }
    }


async def test_update_album_active_only_sends_no_schedule_key(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """No schedule_type passed at all -- the PUT body must not contain a
    "schedule" key, not even a mostly-empty one."""
    await _setup(hass, aioclient_mock)
    aioclient_mock.put(f"{HOST}/api/albums/album-1", json={"id": "album-1"})

    await hass.services.async_call(
        DOMAIN,
        "update_album",
        {"entity_id": _albums_entity_id(hass), "album_id": "album-1", "active": True},
        blocking=True,
    )

    put_calls = [c for c in aioclient_mock.mock_calls if c[1].path == "/api/albums/album-1"]
    assert put_calls[0][2] == {"active": True}


async def test_update_album_rejects_invalid_playback_mode_before_any_http_call(
    hass: HomeAssistant, aioclient_mock
) -> None:
    await _setup(hass, aioclient_mock)

    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN,
            "update_album",
            {
                "entity_id": _albums_entity_id(hass),
                "album_id": "album-1",
                "playback_mode": "bogus",
            },
            blocking=True,
        )

    assert not any(c[1].path == "/api/albums/album-1" for c in aioclient_mock.mock_calls)


async def test_update_album_refreshes_sensor_immediately(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """No manual async_update_listeners()/reload -- the service call itself
    must trigger the coordinator refresh that updates the sensor."""
    await _setup(hass, aioclient_mock)
    aioclient_mock.get(f"{HOST}/api/albums", json={"albums": []})
    await hass.async_block_till_done()

    aioclient_mock.put(f"{HOST}/api/albums/album-1", json={"id": "album-1", "active": True})
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{HOST}/api/info", json=INFO)
    aioclient_mock.get(f"{HOST}/api/battery", json=BATTERY)
    aioclient_mock.put(f"{HOST}/api/albums/album-1", json={"id": "album-1", "active": True})
    aioclient_mock.get(
        f"{HOST}/api/albums",
        json={"albums": [{"id": "album-1", "name": "Test", "active": True, "playback_mode": "sequential", "image_count": 0, "schedule": {"type": "interval", "interval_value": 1, "interval_unit": "hours"}}]},
    )

    await hass.services.async_call(
        DOMAIN,
        "update_album",
        {"entity_id": _albums_entity_id(hass), "album_id": "album-1", "active": True},
        blocking=True,
    )
    await hass.async_block_till_done()

    state = hass.states.get(_albums_entity_id(hass))
    assert state.state == "1"
    assert state.attributes["albums"][0]["id"] == "album-1"


async def test_update_album_unknown_entity_id_raises_and_makes_no_http_call(
    hass: HomeAssistant, aioclient_mock
) -> None:
    await _setup(hass, aioclient_mock)

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            "update_album",
            {"entity_id": "sensor.does_not_exist", "album_id": "album-1", "active": True},
            blocking=True,
        )
    assert exc_info.value.translation_key == "entity_not_found"
    assert not any(c[1].path == "/api/albums/album-1" for c in aioclient_mock.mock_calls)


async def test_update_album_entity_from_other_domain_raises(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """A real, registered entity that just isn't a Fraimic one -- must be
    rejected the same way as a nonexistent entity_id, not treated as valid."""
    await _setup(hass, aioclient_mock)

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            "update_album",
            {
                "entity_id": _entity_id_for_other_domain(hass),
                "album_id": "album-1",
                "active": True,
            },
            blocking=True,
        )
    assert exc_info.value.translation_key == "entity_not_found"


def _entity_id_for_other_domain(hass: HomeAssistant) -> str:
    """The button platform's entities belong to the same config entry but
    aren't the albums sensor -- close enough to prove the domain-service
    handler checks more than "is this entity_id registered at all"."""
    entity_reg = er.async_get(hass)
    entity_id = entity_reg.async_get_entity_id("button", DOMAIN, "abc123_restart")
    assert entity_id is not None
    return entity_id
