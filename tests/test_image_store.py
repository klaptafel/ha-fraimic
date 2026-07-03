"""Tests for FraimicImageStore's disk persistence."""
from __future__ import annotations

from homeassistant.core import HomeAssistant

from custom_components.fraimic.image_store import FraimicImageStore


async def test_set_then_load_in_a_fresh_instance_restores_content(hass: HomeAssistant) -> None:
    store = FraimicImageStore(hass, "entry-1")
    updated_at = await store.async_set(b"fake-png-bytes")

    restored = FraimicImageStore(hass, "entry-1")
    assert restored.content is None  # not loaded yet
    await restored.async_load()

    assert restored.content == b"fake-png-bytes"
    assert restored.updated_at == updated_at


async def test_load_with_nothing_stored_stays_empty(hass: HomeAssistant) -> None:
    store = FraimicImageStore(hass, "never-used-entry")
    await store.async_load()
    assert store.content is None
    assert store.updated_at is None


async def test_remove_clears_persisted_content(hass: HomeAssistant) -> None:
    store = FraimicImageStore(hass, "entry-2")
    await store.async_set(b"fake-png-bytes")
    await store.async_remove()

    restored = FraimicImageStore(hass, "entry-2")
    await restored.async_load()
    assert restored.content is None
