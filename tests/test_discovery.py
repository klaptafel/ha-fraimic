"""Tests for discovery.py's probe_frame/scan_subnet."""
from __future__ import annotations

import re

import aiohttp
import pytest
from homeassistant.core import HomeAssistant

from custom_components.fraimic import discovery

INFO_A = {"device": {"device_key": "device-a"}}

_INFO_PAGE_HTML = (
    "<div class='info-row'><span class='info-label'>Device Type</span>"
    "<span class='info-value'>13.3\" E-Ink</span></div>"
)


async def test_probe_frame_success_includes_panel_size_from_info_page(
    hass: HomeAssistant, aioclient_mock
) -> None:
    aioclient_mock.get("http://1.2.3.4/api/info", json=INFO_A)
    aioclient_mock.get("http://1.2.3.4/info", text=_INFO_PAGE_HTML)
    session = discovery.async_get_clientsession(hass)

    result = await discovery.probe_frame(session, "http://1.2.3.4")

    assert result == {**INFO_A, "info_page": {"panel_size": "13.3"}}


async def test_probe_frame_still_matches_when_info_page_scrape_fails(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """get_info_page is best-effort -- a frame whose /info page can't be
    scraped (old firmware, network hiccup, etc.) still counts as a match,
    it just won't have a detected panel size yet."""
    aioclient_mock.get("http://1.2.3.4/api/info", json=INFO_A)
    aioclient_mock.get("http://1.2.3.4/info", exc=aiohttp.ClientError)
    session = discovery.async_get_clientsession(hass)

    result = await discovery.probe_frame(session, "http://1.2.3.4")

    assert result == {**INFO_A, "info_page": {}}


async def test_probe_frame_client_error_returns_none(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get("http://1.2.3.4/api/info", exc=aiohttp.ClientError)
    session = discovery.async_get_clientsession(hass)

    assert await discovery.probe_frame(session, "http://1.2.3.4") is None


async def test_probe_frame_timeout_returns_none(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get("http://1.2.3.4/api/info", exc=TimeoutError)
    session = discovery.async_get_clientsession(hass)

    assert await discovery.probe_frame(session, "http://1.2.3.4") is None


async def test_probe_frame_matches_without_device_key(hass: HomeAssistant, aioclient_mock) -> None:
    """A frame whose firmware doesn't report a device_key still counts as
    a match -- only a connection-level failure means "not a Fraimic
    device" (see probe_frame's docstring)."""
    aioclient_mock.get("http://1.2.3.4/api/info", json={"device": {}})
    aioclient_mock.get("http://1.2.3.4/info", exc=aiohttp.ClientError)
    session = discovery.async_get_clientsession(hass)

    result = await discovery.probe_frame(session, "http://1.2.3.4")

    assert result == {"device": {}, "info_page": {}}


async def test_scan_subnet_finds_only_responding_hosts(
    hass: HomeAssistant, aioclient_mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_source_ip(hass: HomeAssistant, target_ip: str) -> str:
        return "192.168.1.50"

    monkeypatch.setattr(discovery, "async_get_source_ip", fake_source_ip)

    aioclient_mock.get("http://192.168.1.10/api/info", json=INFO_A)
    aioclient_mock.get("http://192.168.1.10/info", text=_INFO_PAGE_HTML)
    aioclient_mock.get("http://192.168.1.20/api/info", json={"device": {"device_key": "device-b"}})
    # Registered last: matches everything else in the /24 that wasn't
    # already claimed above (including the /info follow-up request for
    # 192.168.1.20, which has no specific mock) -- aioclient_mock matches
    # in insertion order, so this catch-all must stay after the specific
    # registrations or it will shadow them.
    aioclient_mock.get(re.compile(r".*"), exc=aiohttp.ClientError)

    found = await discovery.scan_subnet(hass)

    assert found == [
        {"ip": "192.168.1.10", "info": {**INFO_A, "info_page": {"panel_size": "13.3"}}},
        {
            "ip": "192.168.1.20",
            "info": {"device": {"device_key": "device-b"}, "info_page": {}},
        },
    ]


async def test_scan_subnet_returns_empty_list_when_nothing_responds(
    hass: HomeAssistant, aioclient_mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_source_ip(hass: HomeAssistant, target_ip: str) -> str:
        return "192.168.1.50"

    monkeypatch.setattr(discovery, "async_get_source_ip", fake_source_ip)
    aioclient_mock.get(re.compile(r".*"), exc=aiohttp.ClientError)

    assert await discovery.scan_subnet(hass) == []
