"""Registry of panel physical sizes this integration knows how to drive.

Each FrameType carries the panel's native buffer dimensions and the byte
layout its frame buffer is packed in (see bin_layouts.py): the 13.3" uses
the "split_half" layout, the 31.5" uses the "blocks_315" controller-block
layout.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .bin_layouts import BLOCKS_315, SPLIT_HALF, bin_size


@dataclass(frozen=True)
class FrameType:
    id: str
    width: int
    height: int
    byte_layout: str

    @property
    def bin_size(self) -> int:
        """Exact size in bytes of this panel's frame buffer -- anything
        else is rejected by the frame with 400 invalid_image_size."""
        return bin_size(self.byte_layout, self.width, self.height)


# Native buffer dimensions: 13.3" against Fraimic's own reference converter
# (EL133UF1, portrait 1200x1600); 31.5" is portrait-native 1440x2560, with
# a byte layout confirmed on a real frame (see bin_layouts.py). The panel
# size itself comes from the frame's own /info admin page -- see
# api.get_info_page.
FRAME_TYPES: dict[str, FrameType] = {
    "13.3": FrameType(id="13.3", width=1200, height=1600, byte_layout=SPLIT_HALF),
    "31.5": FrameType(id="31.5", width=1440, height=2560, byte_layout=BLOCKS_315),
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


def panel_size_from_info(info: dict[str, Any]) -> str | None:
    """Dig the panel-size string out of a raw info dict.

    Panel size isn't in /api/info's JSON at all -- see api.get_info_page
    for where it actually comes from (best-effort HTML scrape). Shared by
    a running coordinator's data and a discovery probe's result (see
    discovery.probe_frame), which use the same {"info_page": {...}} shape
    but are read at different times (after vs. before the config entry
    exists).
    """
    return (info.get("info_page") or {}).get("panel_size")


def device_model_from_info(info: dict[str, Any]) -> str:
    """device_model_name(), reading panel_size out of a raw info dict."""
    return device_model_name(panel_size_from_info(info))
