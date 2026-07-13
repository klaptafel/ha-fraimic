"""Tests for the media_player platform: send_image service, play_media, locking."""
from __future__ import annotations

import io

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_component import DATA_INSTANCES
from PIL import Image
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fraimic.const import CONF_HOST, DOMAIN
from custom_components.fraimic.frame_types import DEFAULT_FRAME_TYPE

from .conftest import write_test_image

PANEL_BIN_SIZE = DEFAULT_FRAME_TYPE.bin_size

HOST = "http://1.2.3.4"
INFO = {"device": {"device_key": "abc123"}}
BATTERY = {"percent": 50}


def _entity_id(hass: HomeAssistant) -> str:
    entity_reg = er.async_get(hass)
    entity_id = entity_reg.async_get_entity_id("media_player", DOMAIN, "abc123_display")
    assert entity_id is not None
    return entity_id


def _get_entity(hass: HomeAssistant):
    entity_id = _entity_id(hass)
    return hass.data[DATA_INSTANCES]["media_player"].get_entity(entity_id)


async def _setup(hass: HomeAssistant, aioclient_mock) -> MockConfigEntry:
    aioclient_mock.get(f"{HOST}/api/info", json=INFO)
    aioclient_mock.get(f"{HOST}/api/battery", json=BATTERY)
    entry = MockConfigEntry(domain=DOMAIN, unique_id="abc123", data={CONF_HOST: HOST})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_send_image_uploads_and_updates_picture(
    hass: HomeAssistant, aioclient_mock, tmp_path
) -> None:
    await _setup(hass, aioclient_mock)
    aioclient_mock.post(f"{HOST}/api/image", json={})
    hass.config.allowlist_external_dirs.add(str(tmp_path))
    path = write_test_image(tmp_path)

    await hass.services.async_call(
        DOMAIN,
        "send_image",
        {"entity_id": _entity_id(hass), "path": path, "fit": "fill", "dither": "none"},
        blocking=True,
    )
    # The service call only queues the send (see _queue_send) -- the actual
    # conversion+upload runs in a background task tracked on the config
    # entry, so wait for it before asserting on the outcome.
    await hass.async_block_till_done()

    upload_calls = [c for c in aioclient_mock.mock_calls if c[1].path == "/api/image"]
    assert len(upload_calls) == 1
    assert len(upload_calls[0][2]) == PANEL_BIN_SIZE

    entity = _get_entity(hass)
    assert entity._runtime.image_store.content is not None
    assert Image.open(io.BytesIO(entity._runtime.image_store.content)).format == "PNG"

    state = hass.states.get(_entity_id(hass))
    assert state.state == "idle"


async def test_media_title_reflects_sending_then_sent(
    hass: HomeAssistant, aioclient_mock, tmp_path
) -> None:
    """media_title is the only free (no extra subsystems, always-visible-if-
    you-check-the-entity) confirmation that a tap landed and is in progress,
    since a real toast isn't something a backend-only integration can
    trigger outside of service-call failures."""
    await _setup(hass, aioclient_mock)
    aioclient_mock.post(f"{HOST}/api/image", json={})
    hass.config.allowlist_external_dirs.add(str(tmp_path))
    path = write_test_image(tmp_path)
    entity = _get_entity(hass)

    assert entity.media_title is None

    await hass.services.async_call(
        DOMAIN, "send_image", {"entity_id": _entity_id(hass), "path": path}, blocking=True
    )
    # The service call only queues the send -- HA's eager task execution
    # means the background task has already run up to its first real
    # suspension point (the executor job) by the time this returns, so the
    # "sending" state is already visible without needing to synchronize on
    # a slowed-down convert_image.
    assert entity.media_title == "Sending photo.jpg…"

    await hass.async_block_till_done()
    assert entity.media_title.startswith("Sent ")


async def test_send_image_dry_run_skips_upload(
    hass: HomeAssistant, aioclient_mock, tmp_path
) -> None:
    await _setup(hass, aioclient_mock)
    hass.config.allowlist_external_dirs.add(str(tmp_path))
    path = write_test_image(tmp_path)

    await hass.services.async_call(
        DOMAIN,
        "send_image",
        {"entity_id": _entity_id(hass), "path": path, "dry_run": True},
        blocking=True,
    )
    await hass.async_block_till_done()

    upload_calls = [c for c in aioclient_mock.mock_calls if c[1].path == "/api/image"]
    assert len(upload_calls) == 0
    entity = _get_entity(hass)
    assert entity._runtime.image_store.content is not None


async def test_send_image_file_not_found(hass: HomeAssistant, aioclient_mock, tmp_path) -> None:
    await _setup(hass, aioclient_mock)
    missing = str(tmp_path / "nope.jpg")
    hass.config.allowlist_external_dirs.add(str(tmp_path))

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            DOMAIN, "send_image", {"entity_id": _entity_id(hass), "path": missing}, blocking=True
        )
    assert exc_info.value.translation_key == "file_not_found"


async def test_send_image_path_not_allowed(hass: HomeAssistant, aioclient_mock, tmp_path) -> None:
    await _setup(hass, aioclient_mock)
    path = write_test_image(tmp_path)
    # Deliberately NOT added to hass.config.allowlist_external_dirs.

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            DOMAIN, "send_image", {"entity_id": _entity_id(hass), "path": path}, blocking=True
        )
    assert exc_info.value.translation_key == "path_not_allowed"


async def test_send_image_waits_out_transient_failures_then_succeeds(
    hass: HomeAssistant, aioclient_mock, tmp_path, monkeypatch
) -> None:
    """_upload_waiting_for_frame's outer retry loop must keep going past
    api.upload_image's own short internal retry budget, since the frame
    only wakes on its own schedule -- not on a retried request."""
    import aiohttp
    from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

    async def instant_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr("asyncio.sleep", instant_sleep)

    attempts = {"count": 0}

    async def flaky_then_ok(method, url, data):
        attempts["count"] += 1
        if attempts["count"] < 5:
            return AiohttpClientMockResponse(method, url, exc=aiohttp.ClientError("asleep"))
        return AiohttpClientMockResponse(method, url, json={})

    await _setup(hass, aioclient_mock)
    aioclient_mock.post(f"{HOST}/api/image", side_effect=flaky_then_ok)
    hass.config.allowlist_external_dirs.add(str(tmp_path))
    path = write_test_image(tmp_path)

    await hass.services.async_call(
        DOMAIN, "send_image", {"entity_id": _entity_id(hass), "path": path}, blocking=True
    )
    await hass.async_block_till_done()

    assert attempts["count"] == 5
    entity = _get_entity(hass)
    assert entity.media_title.startswith("Sent ")
    assert entity._runtime.image_store.content is not None


async def test_upload_waiting_for_frame_reflects_waiting_in_title_then_clears(
    hass: HomeAssistant, aioclient_mock, monkeypatch
) -> None:
    """While _upload_waiting_for_frame is genuinely waiting on the outer
    retry loop (not just api.upload_image's own quick internal retries),
    media_title must say so -- and the flag must clear again once the
    frame actually wakes and the upload goes through."""
    import aiohttp
    from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

    await _setup(hass, aioclient_mock)
    entity = _get_entity(hass)
    entity._runtime.send_status.sending = "photo.jpg"

    last_observed_title: str | None = None

    async def observing_sleep(delay: float) -> None:
        nonlocal last_observed_title
        last_observed_title = entity.media_title

    monkeypatch.setattr("asyncio.sleep", observing_sleep)

    attempts = {"count": 0}

    async def flaky_then_ok(method, url, data):
        attempts["count"] += 1
        # Exhausts api.upload_image's own 3-attempt budget once before
        # succeeding, forcing at least one real outer-loop retry.
        if attempts["count"] < 4:
            return AiohttpClientMockResponse(method, url, exc=aiohttp.ClientError("asleep"))
        return AiohttpClientMockResponse(method, url, json={})

    aioclient_mock.post(f"{HOST}/api/image", side_effect=flaky_then_ok)

    assert entity._runtime.send_status.waiting_for_wake is False
    await entity._upload_waiting_for_frame(b"bindata")

    assert attempts["count"] == 4
    assert entity._runtime.send_status.waiting_for_wake is False  # cleared again after success
    # The outer loop's own sleep (the last one observed, right before the
    # attempt that finally succeeds) must see the waiting message -- the
    # earlier ones are api.upload_image's own inner retries, too soon for
    # the outer loop to have set the flag yet.
    assert last_observed_title == "Waiting to send photo.jpg -- tap the frame to wake it up"


async def test_send_image_gives_up_after_wake_wait_timeout(
    hass: HomeAssistant, aioclient_mock, tmp_path, monkeypatch
) -> None:
    import aiohttp
    from datetime import timedelta

    from custom_components.fraimic import media_player as media_player_module

    async def instant_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr("asyncio.sleep", instant_sleep)
    monkeypatch.setattr(media_player_module, "WAKE_WAIT_TIMEOUT", timedelta(seconds=0))

    await _setup(hass, aioclient_mock)
    aioclient_mock.post(f"{HOST}/api/image", exc=aiohttp.ClientError("asleep"))
    hass.config.allowlist_external_dirs.add(str(tmp_path))
    path = write_test_image(tmp_path)

    await hass.services.async_call(
        DOMAIN, "send_image", {"entity_id": _entity_id(hass), "path": path}, blocking=True
    )
    await hass.async_block_till_done()

    entity = _get_entity(hass)
    assert entity.media_title == "Frame never woke up, gave up: photo.jpg"
    state = hass.states.get(_entity_id(hass))
    assert state.state == "idle"

    # The lock must be released so a later attempt isn't blocked forever.
    assert not entity._busy_lock.locked()


async def test_send_image_frame_error_is_not_retried(
    hass: HomeAssistant, aioclient_mock, tmp_path
) -> None:
    """A real frame-reported error (not a connection issue) must fail
    immediately -- retrying for up to 10 minutes can't fix a genuinely
    invalid request."""
    await _setup(hass, aioclient_mock)
    aioclient_mock.post(f"{HOST}/api/image", json={"error": "buffer_not_ready"})
    hass.config.allowlist_external_dirs.add(str(tmp_path))
    path = write_test_image(tmp_path)

    await hass.services.async_call(
        DOMAIN, "send_image", {"entity_id": _entity_id(hass), "path": path}, blocking=True
    )
    await hass.async_block_till_done()

    entity = _get_entity(hass)
    assert entity.media_title == "Frame never woke up, gave up: photo.jpg"
    upload_calls = [c for c in aioclient_mock.mock_calls if c[1].path == "/api/image"]
    assert len(upload_calls) == 1  # no retries at all


async def test_send_image_unexpected_error_is_caught(
    hass: HomeAssistant, aioclient_mock, tmp_path, monkeypatch
) -> None:
    from custom_components.fraimic import media_player as media_player_module

    def broken_convert_image(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(media_player_module, "convert_image", broken_convert_image)

    await _setup(hass, aioclient_mock)
    hass.config.allowlist_external_dirs.add(str(tmp_path))
    path = write_test_image(tmp_path)

    await hass.services.async_call(
        DOMAIN, "send_image", {"entity_id": _entity_id(hass), "path": path}, blocking=True
    )
    await hass.async_block_till_done()

    entity = _get_entity(hass)
    assert entity.media_title == "Frame never woke up, gave up: photo.jpg"
    assert not entity._busy_lock.locked()


async def test_convert_and_send_resolves_detected_panel_size(
    hass: HomeAssistant, aioclient_mock, tmp_path, monkeypatch
) -> None:
    """/info reports a 31.5" panel -- _convert_and_send must resolve that
    via frame_types.frame_type_for_size and pass its real dimensions to
    convert_image, not silently keep using the 13.3" default."""
    from custom_components.fraimic import media_player as media_player_module

    aioclient_mock.get(
        f"{HOST}/info",
        text=(
            "<div class='info-row'><span class='info-label'>Device Type</span>"
            "<span class='info-value'>31.5\" E-Ink</span></div>"
        ),
    )

    captured: dict = {}

    def fake_convert_image(*args, **kwargs):
        captured.update(kwargs)
        return b"\x00" * 100, b"fake-png"

    monkeypatch.setattr(media_player_module, "convert_image", fake_convert_image)

    await _setup(hass, aioclient_mock)
    aioclient_mock.post(f"{HOST}/api/image", json={})
    hass.config.allowlist_external_dirs.add(str(tmp_path))
    path = write_test_image(tmp_path)

    await hass.services.async_call(
        DOMAIN, "send_image", {"entity_id": _entity_id(hass), "path": path}, blocking=True
    )
    await hass.async_block_till_done()

    assert captured["width"] == 2560
    assert captured["height"] == 1440


async def test_send_image_already_busy(hass: HomeAssistant, aioclient_mock, tmp_path) -> None:
    await _setup(hass, aioclient_mock)
    hass.config.allowlist_external_dirs.add(str(tmp_path))
    path = write_test_image(tmp_path)
    entity = _get_entity(hass)

    await entity._busy_lock.acquire()
    try:
        with pytest.raises(HomeAssistantError) as exc_info:
            await hass.services.async_call(
                DOMAIN, "send_image", {"entity_id": _entity_id(hass), "path": path}, blocking=True
            )
        assert exc_info.value.translation_key == "already_busy"
    finally:
        entity._busy_lock.release()


async def test_concurrent_taps_reject_second_immediately_not_queue(
    hass: HomeAssistant, aioclient_mock, tmp_path, monkeypatch
) -> None:
    """Regression test for PARALLEL_UPDATES: a second tap while the frame
    is busy must be rejected right away (already_busy), never silently
    queued behind HA's parallel-updates semaphore to run for real once the
    first tap finishes -- that would defeat the whole point of _busy_lock."""
    import asyncio
    import threading

    from custom_components.fraimic import media_player as media_player_module

    await _setup(hass, aioclient_mock)
    aioclient_mock.post(f"{HOST}/api/image", json={})
    hass.config.allowlist_external_dirs.add(str(tmp_path))
    path = write_test_image(tmp_path)

    started = threading.Event()
    release = threading.Event()
    real_convert_image = media_player_module.convert_image

    def slow_convert_image(*args, **kwargs):
        started.set()
        release.wait(timeout=5)
        return real_convert_image(*args, **kwargs)

    monkeypatch.setattr(media_player_module, "convert_image", slow_convert_image)

    first_call = asyncio.create_task(
        hass.services.async_call(
            DOMAIN, "send_image", {"entity_id": _entity_id(hass), "path": path}, blocking=True
        )
    )
    await hass.async_add_executor_job(started.wait, 5)

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            DOMAIN, "send_image", {"entity_id": _entity_id(hass), "path": path}, blocking=True
        )
    assert exc_info.value.translation_key == "already_busy"

    release.set()
    await first_call


async def test_play_media_local_file_uses_options_defaults(
    hass: HomeAssistant, aioclient_mock, tmp_path
) -> None:
    entry = await _setup(hass, aioclient_mock)
    hass.config_entries.async_update_entry(
        entry, options={"default_fit": "fill", "default_dither": "none"}
    )
    aioclient_mock.post(f"{HOST}/api/image", json={})
    hass.config.allowlist_external_dirs.add(str(tmp_path))
    path = write_test_image(tmp_path)

    entity = _get_entity(hass)
    await entity.async_play_media("image/jpeg", path)
    await hass.async_block_till_done()

    upload_calls = [c for c in aioclient_mock.mock_calls if c[1].path == "/api/image"]
    assert len(upload_calls) == 1


async def test_media_image_and_title_before_and_after_send(
    hass: HomeAssistant, aioclient_mock, tmp_path
) -> None:
    await _setup(hass, aioclient_mock)
    entity = _get_entity(hass)

    assert await entity.async_get_media_image() == (None, None)
    assert entity.media_title is None
    assert entity.media_image_hash is None

    aioclient_mock.post(f"{HOST}/api/image", json={})
    hass.config.allowlist_external_dirs.add(str(tmp_path))
    path = write_test_image(tmp_path)
    await hass.services.async_call(
        DOMAIN, "send_image", {"entity_id": _entity_id(hass), "path": path}, blocking=True
    )
    await hass.async_block_till_done()

    content, content_type = await entity.async_get_media_image()
    assert content is not None
    assert content_type == "image/png"
    assert entity.media_title.startswith("Sent ")
    assert entity.media_image_hash is not None


async def test_play_media_via_http_url(hass: HomeAssistant, aioclient_mock, tmp_path) -> None:
    await _setup(hass, aioclient_mock)
    aioclient_mock.post(f"{HOST}/api/image", json={})
    img_buf = io.BytesIO()
    Image.new("RGB", (200, 200), (1, 2, 3)).save(img_buf, format="JPEG")
    aioclient_mock.get("http://example.com/photo.jpg", content=img_buf.getvalue())

    entity = _get_entity(hass)
    await entity.async_play_media("image/jpeg", "http://example.com/photo.jpg")
    await hass.async_block_till_done()

    upload_calls = [c for c in aioclient_mock.mock_calls if c[1].path == "/api/image"]
    assert len(upload_calls) == 1


async def test_fetch_url_resolves_relative_path(hass: HomeAssistant, aioclient_mock, monkeypatch) -> None:
    from custom_components.fraimic import media_player as media_player_module

    monkeypatch.setattr(
        media_player_module, "get_url", lambda hass, **kwargs: "http://homeassistant.local:8123"
    )
    aioclient_mock.get("http://homeassistant.local:8123/local/photo.jpg", content=b"raw-bytes")

    await _setup(hass, aioclient_mock)
    entity = _get_entity(hass)
    raw = await entity._fetch_url("/local/photo.jpg")
    assert raw == b"raw-bytes"


async def test_read_media_source_local_media_dir_file(
    hass: HomeAssistant, aioclient_mock, tmp_path
) -> None:
    await _setup(hass, aioclient_mock)
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"local-media-dir-bytes")
    hass.config.media_dirs["local"] = str(tmp_path)

    entity = _get_entity(hass)
    raw = await entity._read_media_source("media-source://media_source/local/photo.jpg")
    assert raw == b"local-media-dir-bytes"


async def test_read_local_media_dir_file_rejects_non_media_source_id(
    hass: HomeAssistant, aioclient_mock
) -> None:
    await _setup(hass, aioclient_mock)
    entity = _get_entity(hass)
    with pytest.raises(ValueError):
        await entity._read_local_media_dir_file("/config/www/photo.jpg")


async def test_read_media_source_unknown_media_dir_raises_lookup_error(
    hass: HomeAssistant, aioclient_mock
) -> None:
    await _setup(hass, aioclient_mock)
    entity = _get_entity(hass)
    with pytest.raises(LookupError):
        await entity._read_local_media_dir_file("media-source://media_source/nope/photo.jpg")


async def test_read_media_source_falls_back_to_resolve_and_fetch(
    hass: HomeAssistant, aioclient_mock, monkeypatch
) -> None:
    from custom_components.fraimic import media_player as media_player_module

    await _setup(hass, aioclient_mock)
    entity = _get_entity(hass)

    async def fake_resolve(hass, media_content_id, entity_id):
        class _PlayItem:
            url = "http://camera.local/snapshot.jpg"

        return _PlayItem()

    monkeypatch.setattr(media_player_module.media_source, "async_resolve_media", fake_resolve)
    aioclient_mock.get("http://camera.local/snapshot.jpg", content=b"camera-bytes")

    raw = await entity._read_media_source("media-source://media_source/camera/snapshot.jpg")
    assert raw == b"camera-bytes"


async def test_play_media_via_media_source(hass: HomeAssistant, aioclient_mock, tmp_path) -> None:
    await _setup(hass, aioclient_mock)
    aioclient_mock.post(f"{HOST}/api/image", json={})
    photo = tmp_path / "photo.jpg"
    Image.new("RGB", (200, 200), (4, 5, 6)).save(photo, format="JPEG")
    hass.config.media_dirs["local"] = str(tmp_path)

    entity = _get_entity(hass)
    await entity.async_play_media("image/jpeg", "media-source://media_source/local/photo.jpg")
    await hass.async_block_till_done()

    upload_calls = [c for c in aioclient_mock.mock_calls if c[1].path == "/api/image"]
    assert len(upload_calls) == 1


async def test_async_browse_media_delegates_to_media_source(
    hass: HomeAssistant, aioclient_mock, monkeypatch
) -> None:
    from custom_components.fraimic import media_player as media_player_module

    await _setup(hass, aioclient_mock)
    entity = _get_entity(hass)

    captured = {}

    async def fake_browse_media(hass, media_content_id, *, content_filter):
        captured["media_content_id"] = media_content_id
        captured["content_filter"] = content_filter
        return "browse-result"

    monkeypatch.setattr(media_player_module.media_source, "async_browse_media", fake_browse_media)

    result = await entity.async_browse_media(media_content_id="media-source://media_source/local")
    assert result == "browse-result"
    assert captured["media_content_id"] == "media-source://media_source/local"


async def test_async_search_media_delegates_to_media_source(
    hass: HomeAssistant, aioclient_mock, monkeypatch
) -> None:
    from homeassistant.components.media_player.browse_media import SearchMediaQuery
    from homeassistant.components.media_player.const import MediaClass

    from custom_components.fraimic import media_player as media_player_module

    await _setup(hass, aioclient_mock)
    entity = _get_entity(hass)

    captured = {}

    async def fake_search_media(hass, media_content_id, query):
        captured["media_content_id"] = media_content_id
        captured["query"] = query
        return "search-result"

    monkeypatch.setattr(media_player_module.media_source, "async_search_media", fake_search_media)

    result = await entity.async_search_media(
        SearchMediaQuery(search_query="vacation", media_content_id="media-source://media_source/local")
    )

    assert result == "search-result"
    assert captured["media_content_id"] == "media-source://media_source/local"
    assert captured["query"].search_query == "vacation"
    # Enforced server-side regardless of what the caller passed in --
    # mirrors async_browse_media's own directories+images content_filter.
    assert set(captured["query"].media_filter_classes) == {MediaClass.DIRECTORY, MediaClass.IMAGE}


async def test_async_search_media_raises_clear_error_when_helper_unavailable(
    hass: HomeAssistant, aioclient_mock, monkeypatch
) -> None:
    # media_source.async_search_media (core PR #175485) is newer than the
    # rest of the search feature and isn't in any stable HA release yet --
    # simulates running against a version that predates it.
    from homeassistant.components.media_player.browse_media import SearchMediaQuery

    from custom_components.fraimic import media_player as media_player_module

    await _setup(hass, aioclient_mock)
    entity = _get_entity(hass)

    monkeypatch.delattr(media_player_module.media_source, "async_search_media", raising=False)

    with pytest.raises(HomeAssistantError):
        await entity.async_search_media(
            SearchMediaQuery(search_query="vacation", media_content_id="media-source://media_source/local")
        )


async def test_media_player_stays_available_when_frame_unreachable(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """Unlike most other Fraimic entities, the media player must stay
    available even when the frame hasn't been heard from in ages --
    otherwise picking an image would be blocked at the UI level for as
    long as the frame is asleep, defeating _upload_waiting_for_frame.

    But since HA's own "unavailable" greying-out never kicks in for this
    entity, media_title must say so explicitly -- otherwise a genuinely
    dead frame looks identical to a healthy one that just hasn't been
    sent anything in a while."""
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    from custom_components.fraimic.const import UNAVAILABLE_AFTER

    entry = await _setup(hass, aioclient_mock)
    last_success = dt_util.utcnow() - (UNAVAILABLE_AFTER + timedelta(minutes=1))
    entry.runtime_data.coordinator._last_success = last_success
    entry.runtime_data.coordinator.async_update_listeners()
    await hass.async_block_till_done()

    state = hass.states.get(_entity_id(hass))
    assert state.state != "unavailable"

    entity = _get_entity(hass)
    assert entity.media_title == "Frame unreachable -- tap it to wake it up"


async def test_media_title_sending_takes_priority_over_unreachable(
    hass: HomeAssistant, aioclient_mock, tmp_path
) -> None:
    """A send already in flight (e.g. via _upload_waiting_for_frame while
    the frame is asleep) must still show progress, not the generic
    unreachable message -- they can be true at the same time."""
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    from custom_components.fraimic.const import UNAVAILABLE_AFTER

    entry = await _setup(hass, aioclient_mock)
    entry.runtime_data.coordinator._last_success = dt_util.utcnow() - (
        UNAVAILABLE_AFTER + timedelta(minutes=1)
    )

    entity = _get_entity(hass)
    entity._runtime.send_status.sending = "photo.jpg"
    assert entity.media_title == "Sending photo.jpg…"

    entity._runtime.send_status.sending = None
    entity._runtime.send_status.send_failed = "photo.jpg"
    assert entity.media_title == "Frame never woke up, gave up: photo.jpg"
