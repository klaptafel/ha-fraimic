"""Runtime data bundle stored on the config entry (entry.runtime_data)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from homeassistant.config_entries import ConfigEntry

from .const import DOMAIN
from .coordinator import FraimicAlbumsCoordinator, FraimicBatteryCoordinator, FraimicCoordinator
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


def send_status_signal(entry: ConfigEntry) -> str:
    """Dispatcher signal name for "send status changed" -- entry-scoped so
    multiple config entries (multiple frames) don't cross-talk. See
    FraimicSendStatus.text and its readers (media_player's media_title,
    sensor.py's FraimicStatusSensor)."""
    return f"{DOMAIN}_{entry.entry_id}_send_status_updated"


@dataclass
class FraimicSendStatus:
    """In-flight state of the media player's convert+upload pipeline,
    shared (via FraimicRuntimeData) so more than one entity can reflect
    it -- see .text()."""

    sending: str | None = None
    send_failed: str | None = None
    waiting_for_wake: bool = False

    def text(self, device_reachable: bool, last_sent_at: datetime | None) -> str | None:
        """Human-readable summary of what the media player is doing --
        the only feedback channel available for a background-only
        integration outside of service-call failures (no real "toast" API
        for a Python-only integration; see media_player.py's docstrings
        for the fuller reasoning). Read by both the media player's
        media_title and sensor.py's FraimicStatusSensor, so it lives here
        once instead of being duplicated per-entity.

        The "frame unreachable" branch, below, is why the reader that
        shows this (FraimicStatusSensor) must itself be always-available:
        if that entity went unavailable at the same 72h mark, this exact
        message would never be visible, replaced by HA's own generic
        "unavailable" instead.
        """
        if self.sending is not None:
            if self.waiting_for_wake:
                # device_reachable (coordinator.py) is a coarse 72h signal
                # and usually still True during a normal few-minute sleep
                # gap -- this flag is the fine-grained "the last upload
                # attempt just failed to connect" signal instead, set by
                # media_player.py's _upload_waiting_for_frame the moment
                # it's actually waiting, not just converting/uploading.
                return f"Waiting to send {self.sending} -- tap the frame to wake it up"
            return f"Sending {self.sending}…"
        if self.send_failed is not None:
            return f"Frame never woke up, gave up: {self.send_failed}"
        if not device_reachable:
            return "Frame unreachable -- tap it to wake it up"
        if last_sent_at is None:
            return None
        return f"Sent {last_sent_at.strftime('%Y-%m-%d %H:%M')}"


@dataclass
class FraimicRuntimeData:
    coordinator: FraimicCoordinator
    battery_coordinator: FraimicBatteryCoordinator
    albums_coordinator: FraimicAlbumsCoordinator
    image_store: FraimicImageStore
    send_status: FraimicSendStatus = field(default_factory=FraimicSendStatus)

    @property
    def base_url(self) -> str:
        return self.coordinator.base_url

    @property
    def status_text(self) -> str | None:
        """Convenience wrapper around FraimicSendStatus.text -- see there
        for what this actually means. Kept here too since every reader
        already holds a FraimicRuntimeData and this saves threading
        coordinator/image_store through as separate arguments at each of
        the (currently two) call sites."""
        return self.send_status.text(self.coordinator.device_reachable, self.image_store.updated_at)


FraimicConfigEntry = ConfigEntry[FraimicRuntimeData]
