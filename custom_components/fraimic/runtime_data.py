"""Runtime data bundle stored on the config entry (entry.runtime_data)."""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry

from .coordinator import FraimicBatteryCoordinator, FraimicCoordinator
from .image_store import FraimicImageStore


def device_key(entry: ConfigEntry) -> str:
    """Stable identifier for unique_ids and device registry entries.

    Prefers the frame's own hardware device_key (stored as the config
    entry's unique_id during setup) so entities/history survive removing
    and re-adding the integration or changing the frame's IP address.
    Falls back to entry_id only for the rare case a unique_id was never
    set (shouldn't happen post-migration, but keeps things from crashing).
    """
    return entry.unique_id or entry.entry_id


@dataclass
class FraimicRuntimeData:
    coordinator: FraimicCoordinator
    battery_coordinator: FraimicBatteryCoordinator
    image_store: FraimicImageStore

    @property
    def base_url(self) -> str:
        return self.coordinator.base_url


FraimicConfigEntry = ConfigEntry[FraimicRuntimeData]
