"""The Fraimic E-Ink Canvas integration."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.const import Platform
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import service as service_helper

from .const import CONF_HOST, DOMAIN, SEND_IMAGE_SCHEMA, SERVICE_SEND_IMAGE
from .coordinator import FraimicAlbumsCoordinator, FraimicBatteryCoordinator, FraimicCoordinator
from .entity import device_identity_base
from .frame_types import device_model_from_info
from .image_store import FraimicImageStore
from .runtime_data import FraimicConfigEntry, FraimicRuntimeData

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.MEDIA_PLAYER,
]


async def async_setup_entry(hass: HomeAssistant, entry: FraimicConfigEntry) -> bool:
    host = entry.data[CONF_HOST]

    coordinator = FraimicCoordinator(hass, host)
    battery_coordinator = FraimicBatteryCoordinator(hass, host)
    image_store = FraimicImageStore(hass, entry.entry_id)
    await asyncio.gather(
        coordinator.async_config_entry_first_refresh(),
        battery_coordinator.async_config_entry_first_refresh(),
        image_store.async_load(),
    )

    # Constructed (and refreshed) only after the main coordinator's own
    # first refresh completes -- its device_reachable gate would otherwise
    # read a still-in-flight main coordinator's default False, silently
    # skipping the real fetch on every fresh restart regardless of actual
    # reachability. Plain async_refresh(), not async_config_entry_first_
    # refresh() -- this is optional and cloud-dependent, a hiccup here must
    # not raise ConfigEntryNotReady and take the whole entry down with it.
    albums_coordinator = FraimicAlbumsCoordinator(hass, host, coordinator, entry.entry_id)
    await albums_coordinator.async_refresh()

    entry.runtime_data = FraimicRuntimeData(
        coordinator=coordinator,
        battery_coordinator=battery_coordinator,
        albums_coordinator=albums_coordinator,
        image_store=image_store,
    )

    # Register the device explicitly (rather than relying on whichever
    # entity happens to be set up first) so model/firmware show up on the
    # device page immediately, and stay in sync as firmware changes (or, on
    # a later poll, once the model's best-effort detection first succeeds).
    device_reg = dr.async_get(hass)
    device_entry = device_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        **device_identity_base(entry, coordinator.base_url),
        model=device_model_from_info(coordinator.data or {}),
        sw_version=(coordinator.data or {}).get("firmware_version"),
    )

    @callback
    def _sync_device_info() -> None:
        data = coordinator.data or {}
        updates: dict[str, Any] = {}
        fw = data.get("firmware_version")
        if fw and device_entry.sw_version != fw:
            updates["sw_version"] = fw
        model = device_model_from_info(data)
        if device_entry.model != model:
            updates["model"] = model
        if updates:
            device_reg.async_update_device(device_entry.id, **updates)

    entry.async_on_unload(coordinator.async_add_listener(_sync_device_info))

    # Registered once here (not per media_player platform setup) via the
    # current recommended helper -- guarded since async_setup_entry can run
    # once per config entry (e.g. a second frame added later) and
    # hass.services.async_register would otherwise just re-register the
    # same service redundantly each time.
    if not hass.services.has_service(DOMAIN, SERVICE_SEND_IMAGE):
        service_helper.async_register_platform_entity_service(
            hass,
            DOMAIN,
            SERVICE_SEND_IMAGE,
            entity_domain=Platform.MEDIA_PLAYER,
            func="async_send_local_file",
            schema=SEND_IMAGE_SCHEMA,
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: FraimicConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: FraimicConfigEntry) -> None:
    """Delete the persisted last-sent-image preview when the entry itself
    (not just a reload/unload) is removed, so nothing orphaned is left
    behind in .storage."""
    await FraimicImageStore(hass, entry.entry_id).async_remove()
