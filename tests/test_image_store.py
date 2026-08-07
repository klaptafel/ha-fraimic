"""Tests for FraimicImageStore's disk persistence."""
from __future__ import annotations

from homeassistant.core import HomeAssistant

from custom_components.fraimic.image_store import FraimicImageStore


async def test_set_then_load_in_a_fresh_instance_restores_content(hass: HomeAssistant) -> None:
    store = FraimicImageStore(hass, "entry-1")
    updated_at = await store.async_set(b"fake-png-bytes", b"fake-bin-bytes", "2026-08-07T01:00:00+00:00")

    restored = FraimicImageStore(hass, "entry-1")
    assert restored.content is None  # not loaded yet
    assert restored.bin_content is None
    assert restored.synced_as_of_next_refresh is None
    await restored.async_load()

    assert restored.content == b"fake-png-bytes"
    assert restored.bin_content == b"fake-bin-bytes"
    assert restored.synced_as_of_next_refresh == "2026-08-07T01:00:00+00:00"
    assert restored.updated_at == updated_at


async def test_set_without_next_refresh_defaults_to_none(hass: HomeAssistant) -> None:
    """A dry run, or a real send with no coordinator data yet, omits
    synced_as_of_next_refresh entirely -- must not raise, and must persist
    as None rather than some other falsy placeholder."""
    store = FraimicImageStore(hass, "entry-no-refresh")
    await store.async_set(b"fake-png-bytes", b"fake-bin-bytes")

    restored = FraimicImageStore(hass, "entry-no-refresh")
    await restored.async_load()
    assert restored.synced_as_of_next_refresh is None


async def test_load_with_nothing_stored_stays_empty(hass: HomeAssistant) -> None:
    store = FraimicImageStore(hass, "never-used-entry")
    await store.async_load()
    assert store.content is None
    assert store.bin_content is None
    assert store.synced_as_of_next_refresh is None
    assert store.updated_at is None


async def test_load_from_a_store_written_before_bin_content_existed(hass: HomeAssistant) -> None:
    """A pre-2026-08-07 store file has no "bin_content"/
    "synced_as_of_next_refresh" keys at all -- must load the preview fine
    and leave both None, not raise."""
    store = FraimicImageStore(hass, "entry-legacy")
    await store.async_set(b"fake-png-bytes", b"fake-bin-bytes", "2026-08-07T01:00:00+00:00")
    # Simulate an older version's write: same store, minus the new keys.
    data = await store._store.async_load()
    assert data is not None
    del data["bin_content"]
    del data["synced_as_of_next_refresh"]
    await store._store.async_save(data)

    restored = FraimicImageStore(hass, "entry-legacy")
    await restored.async_load()
    assert restored.content == b"fake-png-bytes"
    assert restored.bin_content is None
    assert restored.synced_as_of_next_refresh is None


async def test_remove_clears_persisted_content(hass: HomeAssistant) -> None:
    store = FraimicImageStore(hass, "entry-2")
    await store.async_set(b"fake-png-bytes", b"fake-bin-bytes", "2026-08-07T01:00:00+00:00")
    await store.async_remove()

    restored = FraimicImageStore(hass, "entry-2")
    await restored.async_load()
    assert restored.content is None
    assert restored.bin_content is None
    assert restored.synced_as_of_next_refresh is None
