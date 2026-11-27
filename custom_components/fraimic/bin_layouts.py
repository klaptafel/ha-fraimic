"""Byte layouts for the Spectra 6 frame buffer (`.bin`) of each panel family.

Every layout packs an array of palette indices (0..5, see
image_converter._PALETTE_RGB) into 4-bit device codes
(black=0x0, white=0x1, yellow=0x2, red=0x3, blue=0x5, green=0x6; 0x4 is
unused by the panel), two pixels per byte, first pixel in the high nibble.
They differ in how pixels are ordered into bytes:

``split_half`` (13.3", EL133UF1)
    Each row is split into a left and a right half. ALL left-half bytes
    for the whole image come first, then ALL right-half bytes, each half
    row-major. Total size is width * height // 2 bytes.

``blocks_315`` (31.5", native 1440 x 2560 portrait)
    The image is flipped vertically, then its top and bottom halves
    (1280 rows each) go to four controller blocks apiece. A block is
    720 rows x 800 pixels = 288,000 bytes; 8 blocks = 2,304,000 bytes.
    Inside a half, a block row is a 2-pixel-wide strip of the image walked
    bottom-to-top, so block ``ic`` holds strip columns
    [800 * ic, 800 * ic + 800). The last block of each half holds only 160
    real columns; the rest is padded with white (nibble 0x1).

The ``blocks_315`` layout follows the reverse-engineering notes in
https://github.com/kristofferR/ha-fraimic-eink (MIT, see its
docs/image-conversion.md and custom_components/fraimic/image_convert.py),
and was confirmed on a real 31.5" frame with a test pattern (corner
colors, orientation text and arrow, six ink bars, 1-pixel stripes, no
seams).

``unpack_*`` are the exact inverses of ``pack_*`` for the pixels a panel
actually shows (padding is discarded). They exist so a raw `.bin` can be
turned back into a preview image, and for round-trip tests.
"""
from __future__ import annotations

from typing import Any

import numpy as np

SPLIT_HALF = "split_half"
BLOCKS_315 = "blocks_315"
BYTE_LAYOUTS = (SPLIT_HALF, BLOCKS_315)

# Palette index (see image_converter._PALETTE_RGB order) -> device nibble.
_CODE_FOR_INDEX = np.array([0x0, 0x1, 0x2, 0x3, 0x5, 0x6], dtype=np.uint8)
_WHITE_CODE = 0x1
# Device nibble -> palette index; the unused nibbles decode as white.
_INDEX_FOR_CODE = np.full(16, 1, dtype=np.uint8)
for _idx, _code in enumerate(_CODE_FOR_INDEX):
    _INDEX_FOR_CODE[_code] = _idx

# blocks_315 geometry.
_B_WIDTH, _B_HEIGHT = 1440, 2560
_B_BLOCK_ROWS = 720
_B_BLOCK_PIXELS = 800
_B_HALF_ROWS = 1280
_B_BLOCKS_PER_HALF = 4
_B_LAST_BLOCK_REAL_COLUMNS = 2 * _B_HALF_ROWS - (_B_BLOCKS_PER_HALF - 1) * _B_BLOCK_PIXELS  # 160
_B_BLOCK_BYTES = _B_BLOCK_ROWS * _B_BLOCK_PIXELS // 2  # 288,000

_Array = np.ndarray[Any, np.dtype[np.uint8]]


def bin_size(layout: str, width: int, height: int) -> int:
    """Exact size in bytes of a frame buffer in `layout` for a
    `width` x `height` panel."""
    if layout == SPLIT_HALF:
        assert width % 4 == 0, f"width must be a multiple of 4, got {width}"
        return 2 * height * (width // 4)
    if layout == BLOCKS_315:
        assert (width, height) == (_B_WIDTH, _B_HEIGHT), (width, height)
        return 2 * _B_BLOCKS_PER_HALF * _B_BLOCK_BYTES
    raise ValueError(f"unknown byte layout: {layout!r}")


def pack(indices: bytes | bytearray | _Array, layout: str, width: int, height: int) -> bytes:
    """Pack a row-major buffer of palette indices (0..5, shape
    height x width, or flat) into a frame buffer in `layout`."""
    idx = np.frombuffer(bytes(indices), dtype=np.uint8) if isinstance(indices, (bytes, bytearray)) else indices
    idx = np.asarray(idx, dtype=np.uint8).reshape(height, width)
    codes = _CODE_FOR_INDEX[idx]
    if layout == SPLIT_HALF:
        out = _pack_split_half(codes, width)
    elif layout == BLOCKS_315:
        out = _pack_blocks_315(codes, width, height)
    else:
        raise ValueError(f"unknown byte layout: {layout!r}")
    assert len(out) == bin_size(layout, width, height)
    return out


def unpack(raw: bytes, layout: str, width: int, height: int) -> _Array:
    """Inverse of `pack`: a frame buffer -> (height, width) array of
    palette indices. Raises ValueError if `raw` is the wrong size."""
    expected = bin_size(layout, width, height)
    if len(raw) != expected:
        raise ValueError(f"expected {expected} bytes for layout {layout!r}, got {len(raw)}")
    data = np.frombuffer(raw, dtype=np.uint8)
    if layout == SPLIT_HALF:
        codes = _unpack_split_half(data, width, height)
    else:
        codes = _unpack_blocks_315(data)
    return _INDEX_FOR_CODE[codes]


def _pack_nibbles(codes: _Array) -> _Array:
    return ((codes[..., 0::2] << 4) | codes[..., 1::2]).astype(np.uint8)


def _unpack_nibbles(packed: _Array) -> _Array:
    out = np.empty(packed.shape[:-1] + (packed.shape[-1] * 2,), dtype=np.uint8)
    out[..., 0::2] = packed >> 4
    out[..., 1::2] = packed & 0x0F
    return out


def _pack_split_half(codes: _Array, width: int) -> bytes:
    left, right = codes[:, : width // 2], codes[:, width // 2 :]
    return _pack_nibbles(left).tobytes() + _pack_nibbles(right).tobytes()


def _unpack_split_half(data: _Array, width: int, height: int) -> _Array:
    half_bytes = height * (width // 4)
    left = _unpack_nibbles(data[:half_bytes].reshape(height, width // 4))
    right = _unpack_nibbles(data[half_bytes:].reshape(height, width // 4))
    return np.concatenate([left, right], axis=1)


def _pack_blocks_315(codes: _Array, width: int, height: int) -> bytes:
    assert (width, height) == (_B_WIDTH, _B_HEIGHT)
    flipped = np.flipud(codes)
    blocks: list[_Array] = []
    for half in range(2):
        rows = flipped[half * _B_HALF_ROWS : (half + 1) * _B_HALF_ROWS]
        # (1280, 1440) -> (720 strip rows, 2560 = 1280 image rows x 2 px)
        band = rows.reshape(_B_HALF_ROWS, _B_BLOCK_ROWS, 2).transpose(1, 0, 2).reshape(_B_BLOCK_ROWS, 2 * _B_HALF_ROWS)
        for ic in range(_B_BLOCKS_PER_HALF):
            real = _B_LAST_BLOCK_REAL_COLUMNS if ic == _B_BLOCKS_PER_HALF - 1 else _B_BLOCK_PIXELS
            block = np.full((_B_BLOCK_ROWS, _B_BLOCK_PIXELS), _WHITE_CODE, dtype=np.uint8)
            block[:, :real] = band[:, ic * _B_BLOCK_PIXELS : ic * _B_BLOCK_PIXELS + real]
            blocks.append(_pack_nibbles(block).reshape(-1))
    return np.concatenate(blocks).tobytes()


def _unpack_blocks_315(data: _Array) -> _Array:
    blocks = data.reshape(2 * _B_BLOCKS_PER_HALF, _B_BLOCK_ROWS, _B_BLOCK_PIXELS // 2)
    flipped = np.empty((2 * _B_HALF_ROWS, _B_WIDTH), dtype=np.uint8)
    for half in range(2):
        band = np.empty((_B_BLOCK_ROWS, 2 * _B_HALF_ROWS), dtype=np.uint8)
        for ic in range(_B_BLOCKS_PER_HALF):
            real = _B_LAST_BLOCK_REAL_COLUMNS if ic == _B_BLOCKS_PER_HALF - 1 else _B_BLOCK_PIXELS
            block = _unpack_nibbles(blocks[half * _B_BLOCKS_PER_HALF + ic])
            band[:, ic * _B_BLOCK_PIXELS : ic * _B_BLOCK_PIXELS + real] = block[:, :real]
        flipped[half * _B_HALF_ROWS : (half + 1) * _B_HALF_ROWS] = (
            band.reshape(_B_BLOCK_ROWS, _B_HALF_ROWS, 2).transpose(1, 0, 2).reshape(_B_HALF_ROWS, _B_WIDTH)
        )
    return np.flipud(flipped)
