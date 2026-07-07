"""Tests for the Fraimic data update coordinators."""
from __future__ import annotations

from datetime import timedelta

import aiohttp
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.fraimic.const import UNAVAILABLE_AFTER
from custom_components.fraimic.coordinator import (
    FraimicAlbumsCoordinator,
    FraimicBatteryCoordinator,
    FraimicCoordinator,
    FraimicBaseCoordinator,
)

HOST = "http://1.2.3.4"


async def test_info_coordinator_success(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(f"{HOST}/api/info", json={"device": {"device_key": "abc"}})
    coordinator = FraimicCoordinator(hass, HOST)

    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.data == {"device": {"device_key": "abc"}}
    assert coordinator.device_reachable is True
    assert coordinator.last_success is not None


async def test_battery_coordinator_success(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(f"{HOST}/api/battery", json={"percent": 42})
    coordinator = FraimicBatteryCoordinator(hass, HOST)

    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.data == {"percent": 42}


async def test_connection_error_marks_update_failed_in_english(
    hass: HomeAssistant, aioclient_mock
) -> None:
    aioclient_mock.get(f"{HOST}/api/info", exc=aiohttp.ClientError("boom"))
    coordinator = FraimicCoordinator(hass, HOST)

    await coordinator.async_refresh()

    assert coordinator.last_update_success is False
    assert "Could not connect to Fraimic frame" in str(coordinator.last_exception)
    # Regression check: this message must never be hardcoded in another
    # language regardless of the user's Home Assistant locale.
    assert "Kan geen verbinding" not in str(coordinator.last_exception)


async def test_known_api_error_propagates_as_update_failed(
    hass: HomeAssistant, aioclient_mock
) -> None:
    aioclient_mock.get(f"{HOST}/api/info", json={"error": "buffer_not_ready"})
    coordinator = FraimicCoordinator(hass, HOST)

    await coordinator.async_refresh()

    assert coordinator.last_update_success is False


async def test_base_fetch_hook_is_abstract(hass: HomeAssistant) -> None:
    coordinator = FraimicBaseCoordinator(hass, HOST, "test", timedelta(minutes=1))
    with pytest.raises(NotImplementedError):
        await coordinator._fetch(session=None)


async def test_device_reachable_false_before_first_success(hass: HomeAssistant) -> None:
    coordinator = FraimicCoordinator(hass, HOST)
    assert coordinator.device_reachable is False
    assert coordinator.last_success is None


async def test_device_reachable_survives_within_unavailable_after_window(
    hass: HomeAssistant, aioclient_mock
) -> None:
    aioclient_mock.get(f"{HOST}/api/info", json={})
    coordinator = FraimicCoordinator(hass, HOST)
    await coordinator.async_refresh()

    # Simulate a long-but-tolerable gap in contact (e.g. deep sleep).
    coordinator._last_success = dt_util.utcnow() - (UNAVAILABLE_AFTER - timedelta(minutes=1))
    assert coordinator.device_reachable is True


async def test_device_reachable_false_after_unavailable_after_window(
    hass: HomeAssistant, aioclient_mock
) -> None:
    aioclient_mock.get(f"{HOST}/api/info", json={})
    coordinator = FraimicCoordinator(hass, HOST)
    await coordinator.async_refresh()

    coordinator._last_success = dt_util.utcnow() - (UNAVAILABLE_AFTER + timedelta(minutes=1))
    assert coordinator.device_reachable is False


async def test_albums_coordinator_success(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(f"{HOST}/api/info", json={"device": {"device_key": "abc"}})
    aioclient_mock.get(f"{HOST}/api/albums", json={"albums": []})
    main = FraimicCoordinator(hass, HOST)
    await main.async_refresh()

    albums = FraimicAlbumsCoordinator(hass, HOST, main)
    await albums.async_refresh()

    assert albums.last_update_success is True
    assert albums.data == {"albums": []}


async def test_albums_coordinator_skips_fetch_when_main_unreachable(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """No /api/albums route registered on purpose -- if the reachability
    gate failed and a real request fired, the unmocked URL itself would
    error, which is a stronger assertion than just checking a status flag."""
    main = FraimicCoordinator(hass, HOST)  # never refreshed -- device_reachable is False
    albums = FraimicAlbumsCoordinator(hass, HOST, main)

    await albums.async_refresh()

    assert albums.last_update_success is False
    assert len(aioclient_mock.mock_calls) == 0


async def test_albums_coordinator_gate_reopens_once_main_reachable(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """Mirrors the startup-ordering fix in __init__.py: the main
    coordinator's own first refresh must complete before the albums
    coordinator's gate can ever pass -- constructing/refreshing both
    before the main coordinator succeeds must not permanently wedge it."""
    aioclient_mock.get(f"{HOST}/api/info", json={"device": {"device_key": "abc"}})
    aioclient_mock.get(f"{HOST}/api/albums", json={"albums": []})
    main = FraimicCoordinator(hass, HOST)
    albums = FraimicAlbumsCoordinator(hass, HOST, main)

    await albums.async_refresh()
    assert albums.last_update_success is False

    await main.async_refresh()
    await albums.async_refresh()
    assert albums.last_update_success is True
