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
    """Holds the most recent preview PNG (per config entry)."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.content: bytes | None = None
        self.updated_at: datetime | None = None
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"fraimic_image_{entry_id}")

    async def async_load(self) -> None:
        """Restore the last-sent preview from disk, if any."""
        data = await self._store.async_load()
        if data is None:
            return
        self.content = base64.b64decode(data["content"])
        self.updated_at = dt_util.parse_datetime(data["updated_at"])

    async def async_set(self, content: bytes) -> datetime:
        self.content = content
        self.updated_at = dt_util.utcnow()
        await self._store.async_save(
            {
                "content": base64.b64encode(content).decode("ascii"),
                "updated_at": self.updated_at.isoformat(),
            }
        )
        return self.updated_at

    async def async_remove(self) -> None:
        """Delete the stored preview -- called when the config entry itself
        (not just a reload/unload) is removed, so nothing orphaned lingers
        in .storage."""
        await self._store.async_remove()
