"""Keeps track of the last image pushed to the frame by this integration.

The Fraimic REST API has no endpoint to read back the current framebuffer,
so this is the best available proxy for "what's on the screen": the exact
quantized/dithered image Home Assistant last sent, not the original source
photo.
"""
from __future__ import annotations

from datetime import datetime

from homeassistant.util import dt as dt_util


class FraimicImageStore:
    """Holds the most recent preview PNG in memory (per config entry)."""

    def __init__(self) -> None:
        self.content: bytes | None = None
        self.updated_at: datetime | None = None

    def set(self, content: bytes) -> datetime:
        self.content = content
        self.updated_at = dt_util.utcnow()
        return self.updated_at
