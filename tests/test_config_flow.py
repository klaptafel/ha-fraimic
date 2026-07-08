"""Tests for the Fraimic config flow (config-flow-test-coverage)."""
from __future__ import annotations

import re

import aiohttp
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fraimic import discovery
from custom_components.fraimic.const import (
    CONF_DEFAULT_DITHER,
    CONF_DEFAULT_FIT,
    CONF_DEVICE_ORIENTATION,
    CONF_HOST,
    DOMAIN,
)

INFO_A = {"device": {"device_key": "device-a"}}
INFO_B = {"device": {"device_key": "device-b"}}

_INFO_PAGE_HTML = (
    "<div class='info-row'><span class='info-label'>Device Type</span>"
    "<span class='info-value'>13.3\" E-Ink</span></div>"
)


def _dhcp_info(ip: str) -> DhcpServiceInfo:
    return DhcpServiceInfo(ip=ip, hostname="fraimic", macaddress="3cdc75123456")


async def test_user_step_success(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get("http://1.2.3.4/api/info", json=INFO_A)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "http://1.2.3.4"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Fraimic (1.2.3.4)"
    assert result["data"] == {CONF_HOST: "http://1.2.3.4"}


async def test_user_step_strips_trailing_slash(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get("http://1.2.3.4/api/info", json=INFO_A)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "http://1.2.3.4/"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_HOST: "http://1.2.3.4"}


async def test_user_step_defaults_to_http_scheme(hass: HomeAssistant, aioclient_mock) -> None:
    """A bare hostname (e.g. fraimic.local, as the form's own placeholder
    suggests) has no scheme -- aiohttp can't request that at all, so this
    must default to http:// rather than surfacing a confusing
    cannot_connect for exactly the input the UI recommends."""
    aioclient_mock.get("http://fraimic.local/api/info", json=INFO_A)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "fraimic.local"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_HOST: "http://fraimic.local"}


async def test_user_step_cannot_connect(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get("http://1.2.3.4/api/info", exc=aiohttp.ClientError)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "http://1.2.3.4"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_step_already_configured_updates_host(
    hass: HomeAssistant, aioclient_mock
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="device-a", data={CONF_HOST: "http://9.9.9.9"})
    entry.add_to_hass(hass)

    aioclient_mock.get("http://1.2.3.4/api/info", json=INFO_A)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "http://1.2.3.4"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "http://1.2.3.4"


async def test_reconfigure_step_success(hass: HomeAssistant, aioclient_mock) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="device-a", data={CONF_HOST: "http://9.9.9.9"})
    entry.add_to_hass(hass)

    aioclient_mock.get("http://1.2.3.4/api/info", json=INFO_A)

    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "http://1.2.3.4"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == "http://1.2.3.4"


async def test_reconfigure_step_wrong_device(hass: HomeAssistant, aioclient_mock) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="device-a", data={CONF_HOST: "http://9.9.9.9"})
    entry.add_to_hass(hass)

    # This address answers, but as a *different* physical frame.
    aioclient_mock.get("http://1.2.3.4/api/info", json=INFO_B)

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "http://1.2.3.4"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_device"
    # The original entry must be untouched.
    assert entry.data[CONF_HOST] == "http://9.9.9.9"


async def test_reconfigure_step_cannot_connect(hass: HomeAssistant, aioclient_mock) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="device-a", data={CONF_HOST: "http://9.9.9.9"})
    entry.add_to_hass(hass)

    aioclient_mock.get("http://1.2.3.4/api/info", exc=aiohttp.ClientError)

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "http://1.2.3.4"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_step_blank_host_finds_device_and_creates_entry(
    hass: HomeAssistant, aioclient_mock, monkeypatch
) -> None:
    async def fake_source_ip(hass, target_ip):
        return "192.168.1.50"

    monkeypatch.setattr(discovery, "async_get_source_ip", fake_source_ip)
    aioclient_mock.get("http://192.168.1.10/api/info", json=INFO_A)
    aioclient_mock.get(re.compile(r".*"), exc=aiohttp.ClientError)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOST: ""})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "pick_device"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.168.1.10"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Fraimic (192.168.1.10)"
    assert result["data"] == {CONF_HOST: "http://192.168.1.10"}


async def test_user_step_blank_host_pick_device_shows_detected_model(
    hass: HomeAssistant, aioclient_mock, monkeypatch
) -> None:
    async def fake_source_ip(hass, target_ip):
        return "192.168.1.50"

    monkeypatch.setattr(discovery, "async_get_source_ip", fake_source_ip)
    aioclient_mock.get("http://192.168.1.10/api/info", json=INFO_A)
    aioclient_mock.get("http://192.168.1.10/info", text=_INFO_PAGE_HTML)
    aioclient_mock.get(re.compile(r".*"), exc=aiohttp.ClientError)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOST: ""})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "pick_device"

    options = result["data_schema"].schema[CONF_HOST].config["options"]
    assert options == [
        {"value": "192.168.1.10", "label": '192.168.1.10 — E-Ink Canvas 13.3" (Spectra 6)'}
    ]


async def test_user_step_blank_host_no_devices_found(
    hass: HomeAssistant, aioclient_mock, monkeypatch
) -> None:
    async def fake_source_ip(hass, target_ip):
        return "192.168.1.50"

    monkeypatch.setattr(discovery, "async_get_source_ip", fake_source_ip)
    aioclient_mock.get(re.compile(r".*"), exc=aiohttp.ClientError)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOST: ""})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "no_devices_found"}


async def test_dhcp_discovery_new_device_confirm_creates_entry(
    hass: HomeAssistant, aioclient_mock
) -> None:
    aioclient_mock.get("http://1.2.3.4/api/info", json=INFO_A)
    aioclient_mock.get("http://1.2.3.4/info", text=_INFO_PAGE_HTML)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_DHCP},
        data=_dhcp_info("1.2.3.4"),
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "dhcp_confirm"
    # The detected model must be shown at discovery time, not just the IP.
    assert result["description_placeholders"] == {
        "ip": "1.2.3.4",
        "model": 'E-Ink Canvas 13.3" (Spectra 6)',
    }
    # This is what actually drives the "Discovered" card's title on the
    # Settings > Devices & Services page (via strings.json's
    # config.flow_title), shown before the user ever opens the form.
    [progress] = hass.config_entries.flow.async_progress()
    assert progress["context"]["title_placeholders"] == {
        "ip": "1.2.3.4",
        "model": 'E-Ink Canvas 13.3" (Spectra 6)',
    }

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Fraimic (1.2.3.4)"
    assert result["data"] == {CONF_HOST: "http://1.2.3.4"}


async def test_dhcp_discovery_non_fraimic_device_aborts(
    hass: HomeAssistant, aioclient_mock
) -> None:
    aioclient_mock.get("http://1.2.3.4/api/info", exc=aiohttp.ClientError)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_DHCP},
        data=_dhcp_info("1.2.3.4"),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_fraimic_device"


async def test_dhcp_discovery_updates_already_configured_entry_host(
    hass: HomeAssistant, aioclient_mock
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="device-a", data={CONF_HOST: "http://9.9.9.9"})
    entry.add_to_hass(hass)

    aioclient_mock.get("http://1.2.3.4/api/info", json=INFO_A)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_DHCP},
        data=_dhcp_info("1.2.3.4"),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "http://1.2.3.4"


async def test_dhcp_discovery_self_heals_fraimic_local_entry_to_ip(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """The specific case the user called out: an entry configured via the
    fraimic.local hostname must be normalized to a tracked IP on a DHCP
    match, not preserved as a hostname."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="device-a", data={CONF_HOST: "http://fraimic.local"}
    )
    entry.add_to_hass(hass)

    aioclient_mock.get("http://1.2.3.4/api/info", json=INFO_A)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_DHCP},
        data=_dhcp_info("1.2.3.4"),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "http://1.2.3.4"


async def test_options_flow(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="device-a", data={CONF_HOST: "http://9.9.9.9"})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_DEVICE_ORIENTATION: "landscape",
            CONF_DEFAULT_FIT: "fill",
            CONF_DEFAULT_DITHER: "atkinson",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_DEVICE_ORIENTATION: "landscape",
        CONF_DEFAULT_FIT: "fill",
        CONF_DEFAULT_DITHER: "atkinson",
    }
