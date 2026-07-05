"""Tests for the Fraimic config flow (config-flow-test-coverage)."""
from __future__ import annotations

import aiohttp
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fraimic.const import (
    CONF_DEFAULT_DITHER,
    CONF_DEFAULT_FIT,
    CONF_DEVICE_ORIENTATION,
    CONF_HOST,
    DOMAIN,
)

INFO_A = {"device": {"device_key": "device-a"}}
INFO_B = {"device": {"device_key": "device-b"}}


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
