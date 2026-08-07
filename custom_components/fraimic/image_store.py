"""Keeps track of the last image pushed to the frame by this integration.

The Fraimic REST API has no endpoint to read back the current framebuffer,
so this is the best available proxy for "what's on the screen": the exact
quantized/dithered image Home Assistant last sent, not the original source
photo. Persisted to disk (not just kept in memory) so the media player's
entity_picture survives a Home Assistant restart instead of going blank
until the next image is sent.
"""
from __future__ import annotations

import base64
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

STORAGE_VERSION = 1


class FraimicImageStore:
    """Holds the most recent preview PNG and its packed .bin frame-buffer
    bytes (per config entry)."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.content: bytes | None = None
        # The exact bytes last POSTed to /api/image -- kept alongside the
        # preview PNG (not derived from it) so resend_guard.py can re-upload
        # verbatim without reconverting: image_converter.convert_image's
        # enhancement/dither pipeline is only correct against a raw source
        # photo, running it a second time against an already-quantized/
        # dithered preview would double-apply brightness/contrast/
        # saturation and sharpen filters onto flat, already-final colors.
        self.bin_content: bytes | None = None
        # The frame's own display.next_refresh value as of the last time
        # Home Assistant successfully sent this exact image -- a raw string
        # copy of whatever the frame's own /api/info reported (never parsed
        # into a datetime; only ever compared for equality against a later
        # poll's value), not a timestamp of our own. resend_guard.py's
        # whole detection mechanism is built on this: if a later poll's
        # next_refresh no longer matches, the frame has gone through at
        # least one scheduled refresh since this image was last confirmed
        # sent, which is reason enough to resend it once. Updated by every
        # successful send, not just an automatic resend -- see
        # media_player.py's own callers of async_set.
        self.synced_as_of_next_refresh: str | None = None
        self.updated_at: datetime | None = None
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"fraimic_image_{entry_id}")

    async def async_load(self) -> None:
        """Restore the last-sent preview (and, if present, bin data /
        sync marker) from disk, if any."""
        data = await self._store.async_load()
        if data is None:
            return
        self.content = base64.b64decode(data["content"])
        # bin_content/synced_as_of_next_refresh are new (2026-08-07) --
        # absent from a store written by an older version of this
        # integration. Fall back to None rather than erroring: an
        # automatic resend simply has nothing to resend, or nothing to
        # compare against, until the next real send repopulates them.
        bin_b64 = data.get("bin_content")
        self.bin_content = base64.b64decode(bin_b64) if bin_b64 else None
        self.synced_as_of_next_refresh = data.get("synced_as_of_next_refresh")
        self.updated_at = dt_util.parse_datetime(data["updated_at"])

    async def async_set(
        self, content: bytes, bin_content: bytes, synced_as_of_next_refresh: str | None = None
    ) -> datetime:
        self.content = content
        self.bin_content = bin_content
        self.synced_as_of_next_refresh = synced_as_of_next_refresh
        self.updated_at = dt_util.utcnow()
        await self._store.async_save(
            {
                "content": base64.b64encode(content).decode("ascii"),
                "bin_content": base64.b64encode(bin_content).decode("ascii"),
                "synced_as_of_next_refresh": synced_as_of_next_refresh,
                "updated_at": self.updated_at.isoformat(),
            }
        )
        return self.updated_at

    async def async_remove(self) -> None:
        """Delete the stored preview -- called when the config entry itself
        (not just a reload/unload) is removed, so nothing orphaned lingers
        in .storage."""
        await self._store.async_remove()
