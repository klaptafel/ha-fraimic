"""Tests for Fraimic's async_setup_entry / async_unload_entry."""
from __future__ import annotations

import aiohttp
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fraimic.const import CONF_HOST, DOMAIN
from custom_components.fraimic.runtime_data import device_key

HOST = "http://1.2.3.4"
INFO = {"device": {"device_key": "abc123"}, "firmware_version": "1.0.0"}
BATTERY = {"percent": 77}
INFO_PAGE_HTML = (
    "<div class='info-row'><span class='info-label'>Device Type</span>"
    "<span class='info-value'>13.3\" E-Ink</span></div>"
)


def _make_entry() -> MockConfigEntry:
    return MockConfigEntry(domain=DOMAIN, unique_id="abc123", data={CONF_HOST: HOST})


async def test_setup_entry_success_registers_device_and_runtime_data(
    hass: HomeAssistant, aioclient_mock
) -> None:
    aioclient_mock.get(f"{HOST}/api/info", json=INFO)
    aioclient_mock.get(f"{HOST}/api/battery", json=BATTERY)
    entry = _make_entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    runtime = entry.runtime_data
    assert runtime.base_url == HOST
    # info_page is always present -- get_info_page never raises, it just
    # returns {} when (as here) /info isn't mocked/reachable.
    assert runtime.coordinator.data == {**INFO, "info_page": {}}
    assert runtime.battery_coordinator.data == BATTERY

    device_reg = dr.async_get(hass)
    device = device_reg.async_get_device(identifiers={(DOMAIN, device_key(entry))})
    assert device is not None
    assert device.manufacturer == "Fraimic"
    assert device.sw_version == "1.0.0"
    assert device.model == "E-Ink Canvas (Spectra 6)"
    assert device.configuration_url == HOST


async def test_setup_entry_fails_when_frame_unreachable(
    hass: HomeAssistant, aioclient_mock
) -> None:
    aioclient_mock.get(f"{HOST}/api/info", exc=aiohttp.ClientError)
    aioclient_mock.get(f"{HOST}/api/battery", json=BATTERY)
    entry = _make_entry()
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_entry_succeeds_when_albums_fetch_fails(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """Albums is optional and cloud-dependent (see coordinator.py) -- a
    failure there must not take the media player/buttons/every other
    entity down with it, unlike a genuine /api/info failure."""
    aioclient_mock.get(f"{HOST}/api/info", json=INFO)
    aioclient_mock.get(f"{HOST}/api/battery", json=BATTERY)
    aioclient_mock.get(f"{HOST}/api/albums", exc=aiohttp.ClientError)
    entry = _make_entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.albums_coordinator.last_update_success is False


async def test_unload_entry_clears_runtime_data(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(f"{HOST}/api/info", json=INFO)
    aioclient_mock.get(f"{HOST}/api/battery", json=BATTERY)
    entry = _make_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    assert not hasattr(entry, "runtime_data")


async def test_last_image_survives_unload_and_reload(hass: HomeAssistant, aioclient_mock) -> None:
    """Simulates a Home Assistant restart: the media player's last-sent
    preview must not go blank just because the integration reloaded."""
    aioclient_mock.get(f"{HOST}/api/info", json=INFO)
    aioclient_mock.get(f"{HOST}/api/battery", json=BATTERY)
    entry = _make_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    updated_at = await entry.runtime_data.image_store.async_set(b"last-sent-preview-png")

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{HOST}/api/info", json=INFO)
    aioclient_mock.get(f"{HOST}/api/battery", json=BATTERY)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.runtime_data.image_store.content == b"last-sent-preview-png"
    assert entry.runtime_data.image_store.updated_at == updated_at


async def test_removing_entry_deletes_persisted_image(hass: HomeAssistant, aioclient_mock) -> None:
    from custom_components.fraimic.image_store import FraimicImageStore

    aioclient_mock.get(f"{HOST}/api/info", json=INFO)
    aioclient_mock.get(f"{HOST}/api/battery", json=BATTERY)
    entry = _make_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    await entry.runtime_data.image_store.async_set(b"last-sent-preview-png")

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    leftover = FraimicImageStore(hass, entry.entry_id)
    await leftover.async_load()
    assert leftover.content is None


async def test_device_model_reflects_detected_panel_size_at_setup(
    hass: HomeAssistant, aioclient_mock
) -> None:
    aioclient_mock.get(f"{HOST}/api/info", json=INFO)
    aioclient_mock.get(f"{HOST}/api/battery", json=BATTERY)
    aioclient_mock.get(f"{HOST}/info", text=INFO_PAGE_HTML)
    entry = _make_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    device_reg = dr.async_get(hass)
    device = device_reg.async_get_device(identifiers={(DOMAIN, device_key(entry))})
    assert device.model == 'E-Ink Canvas 13.3" (Spectra 6)'


async def test_device_model_syncs_once_detection_succeeds_on_later_refresh(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """/info isn't mocked the first time around -- model detection fails
    silently (see get_info_page) and the generic fallback model is used --
    then succeeds on a later poll, same self-healing shape as firmware
    version syncing."""
    aioclient_mock.get(f"{HOST}/api/info", json=INFO)
    aioclient_mock.get(f"{HOST}/api/battery", json=BATTERY)
    entry = _make_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    device_reg = dr.async_get(hass)
    device = device_reg.async_get_device(identifiers={(DOMAIN, device_key(entry))})
    assert device.model == "E-Ink Canvas (Spectra 6)"

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{HOST}/api/info", json=INFO)
    aioclient_mock.get(f"{HOST}/info", text=INFO_PAGE_HTML)
    await entry.runtime_data.coordinator.async_refresh()

    device = device_reg.async_get_device(identifiers={(DOMAIN, device_key(entry))})
    assert device.model == 'E-Ink Canvas 13.3" (Spectra 6)'


async def test_firmware_version_syncs_to_device_on_later_refresh(
    hass: HomeAssistant, aioclient_mock
) -> None:
    aioclient_mock.get(f"{HOST}/api/info", json=INFO)
    aioclient_mock.get(f"{HOST}/api/battery", json=BATTERY)
    entry = _make_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{HOST}/api/info", json={**INFO, "firmware_version": "2.0.0"})
    await entry.runtime_data.coordinator.async_refresh()

    device_reg = dr.async_get(hass)
    device = device_reg.async_get_device(identifiers={(DOMAIN, device_key(entry))})
    assert device.sw_version == "2.0.0"
