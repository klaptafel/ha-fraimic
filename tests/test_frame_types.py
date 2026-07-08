"""Tests for frame_types.py's panel-size registry."""
from __future__ import annotations

from custom_components.fraimic.frame_types import (
    DEFAULT_FRAME_TYPE,
    FRAME_TYPES,
    frame_type_for_size,
)


def test_default_frame_type_is_13_3_inch() -> None:
    assert DEFAULT_FRAME_TYPE is FRAME_TYPES["13.3"]
    assert (DEFAULT_FRAME_TYPE.width, DEFAULT_FRAME_TYPE.height) == (1200, 1600)


def test_frame_type_for_size_falls_back_to_default_when_none() -> None:
    """None means the best-effort /info scrape hasn't succeeded yet --
    image sending must still work, using the 13.3" default."""
    assert frame_type_for_size(None) is DEFAULT_FRAME_TYPE


def test_frame_type_for_size_falls_back_to_default_when_unrecognized() -> None:
    """A panel size this integration doesn't know about yet -- never
    raise, just fall back rather than breaking image sending."""
    assert frame_type_for_size("99.9") is DEFAULT_FRAME_TYPE


def test_frame_type_for_size_resolves_known_sizes() -> None:
    assert frame_type_for_size("13.3") is FRAME_TYPES["13.3"]
    thirty_one_five = frame_type_for_size("31.5")
    assert thirty_one_five is FRAME_TYPES["31.5"]
    assert (thirty_one_five.width, thirty_one_five.height) == (2560, 1440)


def test_bin_size_matches_expected_byte_count() -> None:
    assert FRAME_TYPES["13.3"].bin_size == 960_000
    assert FRAME_TYPES["31.5"].bin_size == 1_843_200
