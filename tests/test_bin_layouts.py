"""Tests for bin_layouts.py: byte layouts of the Spectra 6 frame buffer."""
from __future__ import annotations

import numpy as np
import pytest

from custom_components.fraimic import bin_layouts
from custom_components.fraimic.bin_layouts import BLOCKS_315, SPLIT_HALF

W, H = 1440, 2560
BLOCK_BYTES = 288_000  # 720 rows x 800 pixels / 2


def _blank(value: int = 0) -> np.ndarray:
    return np.full((H, W), value, dtype=np.uint8)


def _pixel_location(row: int, col: int) -> tuple[int, bool]:
    """Independent re-derivation of where source pixel (row, col) lands in
    a blocks_315 buffer: (byte offset, is_high_nibble). Written from the
    layout description, not from the packer's code."""
    flipped_row = H - 1 - row
    half, half_row = divmod(flipped_row, 1280)
    block_row, strip_px = divmod(col, 2)
    band_col = half_row * 2 + strip_px
    block_in_half, col_in_block = divmod(band_col, 800)
    block = half * 4 + block_in_half
    offset = block * BLOCK_BYTES + block_row * 400 + col_in_block // 2
    return offset, col_in_block % 2 == 0


def test_blocks_315_size() -> None:
    assert bin_layouts.bin_size(BLOCKS_315, W, H) == 2_304_000
    assert len(bin_layouts.pack(_blank(), BLOCKS_315, W, H)) == 2_304_000


def test_split_half_size() -> None:
    assert bin_layouts.bin_size(SPLIT_HALF, 1200, 1600) == 960_000


def test_blocks_315_golden_offsets() -> None:
    """Known pixels land at known byte offsets (worked out by hand from
    the layout: image flipped vertically, bottom half of the *flipped*
    image in blocks 5-8, 2-pixel strips, 160 real columns in blocks 4/8)."""
    pixels = _blank(0)  # all black (code 0x0)
    pixels[2559, 0] = 5  # bottom-left source pixel -> very first nibble
    pixels[0, 0] = 2  # top-left (yellow)
    pixels[0, 1] = 4  # top-left, second pixel of the strip (blue)
    pixels[0, 1439] = 3  # top-right (red)

    packed = bin_layouts.pack(pixels, BLOCKS_315, W, H)

    # index 5 = green = nibble 0x6; index 2 = yellow = 0x2; 4 = blue = 0x5; 3 = red = 0x3
    assert packed[0] == 0x60
    # (row 0, col 0): flipped row 2559 -> second half, strip col 2558 ->
    # block 8 (index 7), column 158 of 800 -> byte 79, high nibble.
    assert packed[7 * BLOCK_BYTES + 79] == 0x25  # yellow high, blue low
    # (row 0, col 1439): block row 719, same strip column 2559 -> byte 79 low nibble.
    assert packed[7 * BLOCK_BYTES + 719 * 400 + 79] == 0x03  # black high, red low


def test_blocks_315_padding_is_white() -> None:
    packed = bin_layouts.pack(_blank(0), BLOCKS_315, W, H)
    for block in (3, 7):  # last block of each half: only 160 real columns
        base = block * BLOCK_BYTES
        assert packed[base + 79] == 0x00  # last real byte (columns 158-159)
        assert packed[base + 80] == 0x11  # first padding byte: white
        assert packed[base + 399] == 0x11  # end of the first block row
    assert packed[2 * BLOCK_BYTES + 399] == 0x00  # full blocks have no padding


@pytest.mark.parametrize("seed", [0, 1])
def test_blocks_315_matches_independent_location_formula(seed: int) -> None:
    rng = np.random.default_rng(seed)
    pixels = rng.integers(0, 6, size=(H, W), dtype=np.uint8)
    packed = np.frombuffer(bin_layouts.pack(pixels, BLOCKS_315, W, H), dtype=np.uint8)
    codes = np.array([0x0, 0x1, 0x2, 0x3, 0x5, 0x6], dtype=np.uint8)
    for _ in range(2000):
        row, col = int(rng.integers(0, H)), int(rng.integers(0, W))
        offset, high = _pixel_location(row, col)
        nibble = packed[offset] >> 4 if high else packed[offset] & 0x0F
        assert nibble == codes[pixels[row, col]], (row, col)


@pytest.mark.parametrize(
    ("layout", "width", "height"), [(BLOCKS_315, W, H), (SPLIT_HALF, 1200, 1600)]
)
def test_pack_unpack_round_trip(layout: str, width: int, height: int) -> None:
    rng = np.random.default_rng(42)
    pixels = rng.integers(0, 6, size=(height, width), dtype=np.uint8)

    packed = bin_layouts.pack(pixels, layout, width, height)
    assert len(packed) == bin_layouts.bin_size(layout, width, height)
    np.testing.assert_array_equal(bin_layouts.unpack(packed, layout, width, height), pixels)


def test_pack_accepts_flat_bytes() -> None:
    pixels = bytearray(W * H)
    pixels[0] = 1
    packed = bin_layouts.pack(pixels, BLOCKS_315, W, H)
    unpacked = bin_layouts.unpack(packed, BLOCKS_315, W, H)
    assert unpacked[0, 0] == 1


def test_unpack_rejects_wrong_size() -> None:
    with pytest.raises(ValueError, match="expected 2304000 bytes"):
        bin_layouts.unpack(b"\x00" * 1_843_200, BLOCKS_315, W, H)


def test_unknown_layout_raises() -> None:
    with pytest.raises(ValueError, match="unknown byte layout"):
        bin_layouts.bin_size("nope", W, H)
