"""Tests for the diagnostics platform."""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fraimic.const import CONF_HOST, DOMAIN
from custom_components.fraimic.diagnostics import async_get_config_entry_diagnostics

HOST = "http://1.2.3.4"
INFO = {
    "device": {"device_key": "abc123"},
    "wifi": {"ssid": "MyHomeWifi", "bssid": "AA:BB:CC:DD:EE:FF", "mac": "11:22:33:44:55:66", "ip": "1.2.3.4"},
}
BATTERY = {"percent": 55}


async def test_diagnostics_redacts_network_identifiers(
    hass: HomeAssistant, aioclient_mock
) -> None:
    aioclient_mock.get(f"{HOST}/api/info", json=INFO)
    aioclient_mock.get(f"{HOST}/api/battery", json=BATTERY)
    entry = MockConfigEntry(domain=DOMAIN, unique_id="abc123", data={CONF_HOST: HOST})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["entry_data"][CONF_HOST] == "**REDACTED**"
    assert diagnostics["info"]["wifi"]["ssid"] == "**REDACTED**"
    assert diagnostics["info"]["wifi"]["bssid"] == "**REDACTED**"
    assert diagnostics["info"]["wifi"]["mac"] == "**REDACTED**"
    assert diagnostics["info"]["wifi"]["ip"] == "**REDACTED**"
    assert diagnostics["info"]["device"]["device_key"] == "abc123"
    assert diagnostics["battery"] == BATTERY
    assert diagnostics["device_reachable"] is True
    assert diagnostics["last_update_success"] is True
    assert diagnostics["last_success"] is not None
