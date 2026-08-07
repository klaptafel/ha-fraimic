"""Detects when the frame's own scheduled refresh has moved on since Home
Assistant last confirmed the display is correct, and re-sends the last
image to correct it.

See FraimicResendGuard's own docstring for the full reasoning (direct user
feedback, 2026-08-07: a frame with no active cloud album still goes black
on its own scheduled refresh, since that refresh is a completely separate,
device-internal thing from whatever this integration last pushed to it).
"""
from __future__ import annotations

import logging
from typing import Callable

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import CONF_RESEND_AFTER_REFRESH, DEFAULT_RESEND_AFTER_REFRESH
from .runtime_data import FraimicConfigEntry, FraimicRuntimeData, resend_requested_signal

_LOGGER = logging.getLogger(__name__)


class FraimicResendGuard:
    """Re-sends the last image once the frame's own next_refresh has moved
    on since Home Assistant last confirmed the display shows it.

    The frame wakes and redraws on its own internal schedule (display.
    next_refresh, exposed as the "Next Scheduled Refresh" sensor),
    completely independent of whatever Home Assistant last pushed to it.
    Confirmed live: a frame with no active cloud album still wakes and
    redraws on this schedule -- with nothing valid to draw from, it goes
    black, even though the last image sent via Home Assistant is still
    perfectly valid and was never actually replaced by anything.

    Deliberately reactive, not scheduled -- an earlier version of this
    tried to predict the exact moment and set a one-shot timer for it, but
    direct user feedback (2026-08-07) surfaced two real problems with
    that: the frame is asleep too often for timing around its own schedule
    to be reliably caught, and a fixed timer fires regardless of whether
    anything actually needs correcting, which risked resending the same
    image on every single Home Assistant restart for no reason at all.

    Instead, this piggybacks entirely on the *regular* coordinator poll
    (every 5 minutes, only actually succeeding while the frame happens to
    be reachable for any reason -- its own scheduled refresh, a tap, or
    plain luck): every time that poll succeeds, compare the frame's
    current next_refresh against image_store.synced_as_of_next_refresh, a
    marker persisted and updated by *every* successful send (automatic or
    a real one you triggered yourself -- see media_player.py's own callers
    of FraimicImageStore.async_set). A mismatch means at least one
    scheduled refresh has happened since the display was last confirmed
    correct, so the last image is re-sent once to correct it -- using the
    connection this poll just proved is live, no separate wake-retry logic
    of its own needed here (though the resend itself still goes through
    the same one every other send uses, in case the frame goes back to
    sleep in between). No mismatch, no image queued yet, or the option
    disabled: does nothing, including right after a restart, since the
    persisted marker survives it.
    """

    def __init__(self, hass: HomeAssistant, entry: FraimicConfigEntry, runtime: FraimicRuntimeData) -> None:
        self._hass = hass
        self._entry = entry
        self._runtime = runtime

    @callback
    def async_setup(self) -> Callable[[], None]:
        """Registers this as a coordinator listener; returns the matching
        unsubscribe, the same shape coordinator.async_add_listener's own
        return value has, so __init__.py can pass it straight to
        entry.async_on_unload alongside its other listeners."""
        return self._runtime.coordinator.async_add_listener(self._on_coordinator_update)

    @callback
    def _on_coordinator_update(self) -> None:
        # Read fresh from entry.options every time, not cached at __init__
        # -- same "no reload needed to pick up an Options change" pattern
        # media_player.py already uses for device_orientation/default_fit/
        # default_dither, since this integration never registers an
        # options-update listener that would force a reload.
        if not self._entry.options.get(CONF_RESEND_AFTER_REFRESH, DEFAULT_RESEND_AFTER_REFRESH):
            return
        image_store = self._runtime.image_store
        if image_store.bin_content is None:
            return  # nothing has ever been sent yet -- nothing to resend
        display = (self._runtime.coordinator.data or {}).get("display") or {}
        next_refresh = display.get("next_refresh")
        if not next_refresh or next_refresh == image_store.synced_as_of_next_refresh:
            return
        _LOGGER.debug(
            "Fraimic: next_refresh changed (%s -> %s) since the last confirmed send -- requesting a resend",
            image_store.synced_as_of_next_refresh, next_refresh,
        )
        async_dispatcher_send(self._hass, resend_requested_signal(self._entry), next_refresh)
