"""The Fraimic E-Ink Canvas integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.const import Platform
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import CONF_HOST, DOMAIN
from .coordinator import FraimicBatteryCoordinator, FraimicCoordinator
from .image_store import FraimicImageStore
from .runtime_data import FraimicRuntimeData, device_key

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.MEDIA_PLAYER,
]

# All PANEL_* constants (and this integration in general) assume the
# Spectra 6 color format, confirmed against fraimic_bin_converter. Panel
# size (13.3" vs 31.5") isn't reported anywhere in the local API, so it's
# deliberately left out of the model string below rather than guessed.
DEVICE_MODEL = "E-Ink Canvas (Spectra 6)"

# Entities retired during development (replaced or dropped as redundant).
# Matched by unique_id *suffix* rather than a reconstructed exact
# unique_id, so this reliably catches leftovers regardless of naming.
_RETIRED_SUFFIXES: tuple[str, ...] = (
    "_firmware_version",  # sensor: superseded by device_info.sw_version
    "_firmware",  # update: removed entirely, no local install capability
    "_now_showing",  # image: superseded by media_player entity_picture
    "_last_boot",  # sensor: redundant with sensor.last_seen
    "_last_refresh",  # sensor: redundant with sensor.last_seen
    "_registered",  # binary_sensor: low-value, rarely changes
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    host = entry.data[CONF_HOST]

    coordinator = FraimicCoordinator(hass, host)
    battery_coordinator = FraimicBatteryCoordinator(hass, host)
    await coordinator.async_config_entry_first_refresh()
    await battery_coordinator.async_config_entry_first_refresh()

    runtime_data = FraimicRuntimeData(
        coordinator=coordinator,
        battery_coordinator=battery_coordinator,
        image_store=FraimicImageStore(),
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime_data

    # Register the device explicitly (rather than relying on whichever
    # entity happens to be set up first) so model/firmware show up on the
    # device page immediately, and stay in sync as firmware changes.
    device_reg = dr.async_get(hass)
    device_entry = device_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, device_key(entry))},
        name="Fraimic E-Ink Canvas",
        manufacturer="Fraimic",
        model=DEVICE_MODEL,
        sw_version=(coordinator.data or {}).get("firmware_version"),
        configuration_url=coordinator.base_url,
    )

    @callback
    def _sync_firmware_version() -> None:
        fw = (coordinator.data or {}).get("firmware_version")
        if fw and device_entry.sw_version != fw:
            device_reg.async_update_device(device_entry.id, sw_version=fw)

    entry.async_on_unload(coordinator.async_add_listener(_sync_firmware_version))

    # Clean up entities from retired platforms/fields so they don't linger
    # as permanently "unavailable" and undeletable in the registry.
    entity_reg = er.async_get(hass)
    for reg_entry in list(er.async_entries_for_config_entry(entity_reg, entry.entry_id)):
        if reg_entry.unique_id.endswith(_RETIRED_SUFFIXES):
            entity_reg.async_remove(reg_entry.entity_id)
            _LOGGER.debug("Removed retired Fraimic entity %s", reg_entry.entity_id)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
