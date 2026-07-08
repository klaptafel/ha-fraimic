"""Registry of panel physical sizes this integration knows how to drive.

Both registered sizes use the same split-half byte layout (see
image_converter.py's module docstring) -- if a future panel needs a
different layout, that's the trigger to add a byte_layout field here,
not before.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FrameType:
    id: str
    width: int
    height: int

    @property
    def bin_size(self) -> int:
        # 4 bits/pixel, 2 pixels/byte, split into two equal halves -- see
        # image_converter.py's module docstring for the full layout.
        assert self.width % 4 == 0, f"{self.id}: width must be a multiple of 4"
        return 2 * self.height * (self.width // 4)


# Confirmed dimensions: 13.3" against Fraimic's own reference converter
# (EL133UF1); 31.5" against a separate open-source Fraimic HA integration
# and curl against the frame's own /info admin page reporting "Device
# Type" -- see api.get_info_page.
FRAME_TYPES: dict[str, FrameType] = {
    "13.3": FrameType(id="13.3", width=1200, height=1600),
    "31.5": FrameType(id="31.5", width=2560, height=1440),
}

DEFAULT_FRAME_TYPE = FRAME_TYPES["13.3"]


def frame_type_for_size(panel_size: str | None) -> FrameType:
    """Falls back to the 13.3" default when panel_size is None (the
    best-effort /info scrape hasn't succeeded yet, e.g. right after a
    fresh restart) or unrecognized (a panel type this integration
    doesn't know about yet) -- image sending must keep working either
    way, never raise here."""
    if panel_size is None:
        return DEFAULT_FRAME_TYPE
    return FRAME_TYPES.get(panel_size, DEFAULT_FRAME_TYPE)


# Shown on the HA device page (and, at discovery time, in the picker/
# confirm forms) when the panel size hasn't been detected yet.
DEFAULT_MODEL_NAME = "E-Ink Canvas (Spectra 6)"


def device_model_name(panel_size: str | None) -> str:
    """Human-readable model string for a detected panel size (e.g. "13.3"),
    or the generic fallback if it's not known yet -- the /info scrape this
    comes from is always best-effort (see api.get_info_page)."""
    if panel_size:
        return f'E-Ink Canvas {panel_size}" (Spectra 6)'
    return DEFAULT_MODEL_NAME
