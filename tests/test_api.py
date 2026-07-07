"""Tests for the thin Fraimic REST API client."""
from __future__ import annotations

import aiohttp
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

from custom_components.fraimic import api

HOST = "http://1.2.3.4"


async def test_get_info_success(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(f"{HOST}/api/info", json={"device": {"device_key": "abc"}})
    result = await api.get_info(async_get_clientsession(hass), HOST)
    assert result == {"device": {"device_key": "abc"}}


async def test_get_battery_success(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(f"{HOST}/api/battery", json={"percent": 88})
    result = await api.get_battery(async_get_clientsession(hass), HOST)
    assert result == {"percent": 88}


async def test_get_albums_success(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(f"{HOST}/api/albums", json={"albums": []})
    result = await api.get_albums(async_get_clientsession(hass), HOST)
    assert result == {"albums": []}


async def test_update_album_sends_only_passed_fields(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.put(f"{HOST}/api/albums/abc-123", json={"id": "abc-123", "active": True})
    result = await api.update_album(async_get_clientsession(hass), HOST, "abc-123", active=True)

    assert result == {"id": "abc-123", "active": True}
    assert len(aioclient_mock.mock_calls) == 1
    method, url, sent_json, _ = aioclient_mock.mock_calls[0]
    assert method == "PUT"
    assert str(url) == f"{HOST}/api/albums/abc-123"
    assert sent_json == {"active": True}


async def test_update_album_422_list_detail_raises_cloud_validation_error(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """Confirmed via curl against the real device: a 422 body's "detail" is
    a list of Pydantic-style error dicts, not a plain string."""
    aioclient_mock.put(
        f"{HOST}/api/albums/abc-123",
        status=422,
        json={
            "detail": [
                {
                    "type": "greater_than",
                    "loc": ["body", "interval_value"],
                    "msg": "Input should be greater than 0",
                }
            ]
        },
    )
    with pytest.raises(HomeAssistantError) as exc_info:
        await api.update_album(async_get_clientsession(hass), HOST, "abc-123", interval_value=0)
    assert exc_info.value.translation_key == "cloud_validation_error"
    assert exc_info.value.translation_placeholders == {
        "detail": "interval_value: Input should be greater than 0"
    }


async def test_update_album_400_string_detail_raises_cloud_validation_error(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """Confirmed via curl: a 400 body's "detail" is a plain string for
    business-rule failures, unlike the 422 list shape above."""
    aioclient_mock.put(
        f"{HOST}/api/albums/abc-123",
        status=400,
        json={"detail": "interval_value must be greater than 0"},
    )
    with pytest.raises(HomeAssistantError) as exc_info:
        await api.update_album(async_get_clientsession(hass), HOST, "abc-123", interval_value=0)
    assert exc_info.value.translation_key == "cloud_validation_error"
    assert exc_info.value.translation_placeholders == {
        "detail": "interval_value must be greater than 0"
    }


async def test_update_album_error_without_recognizable_detail_shape_still_raises(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """Defensive: a raw gateway/proxy error (e.g. a 502 during a real
    outage) won't have FastAPI's shape at all -- must not crash formatting it."""
    aioclient_mock.put(f"{HOST}/api/albums/abc-123", status=502, json={})
    with pytest.raises(HomeAssistantError) as exc_info:
        await api.update_album(async_get_clientsession(hass), HOST, "abc-123", active=True)
    assert exc_info.value.translation_key == "cloud_validation_error"
    assert exc_info.value.translation_placeholders == {"detail": "HTTP 502"}


async def test_known_error_code_raises_translation_key(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.post(f"{HOST}/api/sleep", json={"error": "charging_cable_connected"})
    with pytest.raises(HomeAssistantError) as exc_info:
        await api.sleep(async_get_clientsession(hass), HOST)
    assert exc_info.value.translation_key == "charging_cable_connected"
    assert exc_info.value.translation_domain == "fraimic"


async def test_unknown_error_code_raises_unknown_error(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.post(f"{HOST}/api/restart", json={"error": "something_new"})
    with pytest.raises(HomeAssistantError) as exc_info:
        await api.restart(async_get_clientsession(hass), HOST)
    assert exc_info.value.translation_key == "unknown_error"
    assert exc_info.value.translation_placeholders == {"error_code": "something_new"}


async def test_http_error_without_error_field(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.post(f"{HOST}/api/refresh", status=503, json={})
    with pytest.raises(HomeAssistantError) as exc_info:
        await api.refresh(async_get_clientsession(hass), HOST)
    assert exc_info.value.translation_key == "http_error"
    assert exc_info.value.translation_placeholders == {"status": "503"}


async def test_non_json_body_is_treated_as_empty_payload(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """A non-JSON (or non-dict) body shouldn't crash -- no error/status<400 just succeeds."""
    aioclient_mock.post(f"{HOST}/api/restart", text="not json at all")
    await api.restart(async_get_clientsession(hass), HOST)  # must not raise


async def test_non_dict_json_body_is_treated_as_empty_payload(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """A JSON body that parses but isn't an object (e.g. a bare list)."""
    aioclient_mock.post(f"{HOST}/api/restart", json=[1, 2, 3])
    await api.restart(async_get_clientsession(hass), HOST)  # must not raise


async def test_upload_image_sends_octet_stream(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.post(f"{HOST}/api/image", json={})
    await api.upload_image(async_get_clientsession(hass), HOST, b"\x00" * 960_000)

    assert len(aioclient_mock.mock_calls) == 1
    _, _, sent_data, headers = aioclient_mock.mock_calls[0]
    assert sent_data == b"\x00" * 960_000
    assert headers["Content-Type"] == "application/octet-stream"


async def test_upload_image_retries_on_connection_error_then_succeeds(
    hass: HomeAssistant, aioclient_mock, monkeypatch
) -> None:
    sleeps: list[int] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(api.asyncio, "sleep", fake_sleep)

    attempts = {"count": 0}

    async def flaky_then_ok(method, url, data):
        attempts["count"] += 1
        if attempts["count"] < 3:
            return AiohttpClientMockResponse(method, url, exc=aiohttp.ClientError("blip"))
        return AiohttpClientMockResponse(method, url, json={})

    aioclient_mock.post(f"{HOST}/api/image", side_effect=flaky_then_ok)

    await api.upload_image(async_get_clientsession(hass), HOST, b"data")

    assert attempts["count"] == 3
    assert sleeps == [2, 5]


async def test_upload_image_gives_up_after_max_attempts(
    hass: HomeAssistant, aioclient_mock, monkeypatch
) -> None:
    async def fake_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr(api.asyncio, "sleep", fake_sleep)

    attempts = {"count": 0}

    async def always_flaky(method, url, data):
        attempts["count"] += 1
        return AiohttpClientMockResponse(method, url, exc=aiohttp.ClientError("still blip"))

    aioclient_mock.post(f"{HOST}/api/image", side_effect=always_flaky)

    with pytest.raises(aiohttp.ClientError, match="still blip"):
        await api.upload_image(async_get_clientsession(hass), HOST, b"data")

    assert attempts["count"] == 3
