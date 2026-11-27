"""Tests for image_converter.py's Spectra 6 .bin packing and fit/dither logic."""
from __future__ import annotations

import io

import pytest
from PIL import Image

from custom_components.fraimic import bin_layouts, image_converter
from custom_components.fraimic.const import DITHER_MODES
from custom_components.fraimic.frame_types import FRAME_TYPES

PANEL_WIDTH = FRAME_TYPES["13.3"].width
PANEL_LAYOUT = FRAME_TYPES["13.3"].byte_layout
PANEL_HEIGHT = FRAME_TYPES["13.3"].height
PANEL_BIN_SIZE = FRAME_TYPES["13.3"].bin_size


def _solid_jpeg_bytes(width: int, height: int, color: tuple[int, int, int]) -> bytes:
    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def test_pack_bin_nibble_order_and_halves() -> None:
    indices = bytearray(PANEL_WIDTH * PANEL_HEIGHT)  # all black (index 0)
    indices[0] = 1  # white
    indices[1] = 3  # red
    indices[600] = 5  # green (start of the right half of row 0)

    packed = image_converter._pack_bin(indices, PANEL_LAYOUT, PANEL_WIDTH, PANEL_HEIGHT)

    assert len(packed) == PANEL_BIN_SIZE
    bytes_per_half_row = PANEL_WIDTH // 4
    left, right = packed[: len(packed) // 2], packed[len(packed) // 2 :]
    assert len(left) == len(right) == PANEL_HEIGHT * bytes_per_half_row

    # white(0x1) in the high nibble, red(0x3) in the low nibble.
    assert left[0] == 0x13
    # green(0x6) is the first pixel of the right half -> high nibble.
    assert right[0] == 0x60
    # Everything else is untouched black (0x0 / 0x0).
    assert left[1] == 0x00
    assert right[1] == 0x00


def test_convert_image_31_5_inch_produces_correctly_sized_output() -> None:
    frame_type = FRAME_TYPES["31.5"]
    raw = _solid_jpeg_bytes(400, 300, (200, 30, 30))

    for orientation in ("portrait", "landscape"):
        bin_data, preview_png = image_converter.convert_image(
            raw, fit="fill", device_orientation=orientation, dither="none",
            width=frame_type.width, height=frame_type.height, byte_layout=frame_type.byte_layout,
        )

        assert len(bin_data) == frame_type.bin_size == 2_304_000
        preview = Image.open(io.BytesIO(preview_png))
        assert preview.size == (frame_type.width, frame_type.height)


def test_portrait_native_31_5_inch_panel_orientation() -> None:
    """The 31.5" panel is portrait-native (1440x2560), like the 13.3":
    device_orientation="portrait" must NOT rotate (a stripe at the top of
    a portrait source stays at the top of the buffer), and "landscape"
    must rotate onto the portrait buffer."""
    frame_type = FRAME_TYPES["31.5"]
    width, height = frame_type.width, frame_type.height

    img = Image.new("RGB", (width, height), (0, 0, 0))
    img.paste((255, 0, 0), (0, 0, width, 40))  # red stripe across the top
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    raw = buf.getvalue()

    def convert(orientation: str) -> bytes:
        return image_converter.convert_image(
            raw, fit="fill", device_orientation=orientation, dither="none",
            width=width, height=height, byte_layout=frame_type.byte_layout,
        )[0]

    portrait_bin = convert("portrait")
    landscape_bin = convert("landscape")

    pixels = bin_layouts.unpack(portrait_bin, frame_type.byte_layout, width, height)
    assert (pixels[:20] == 3).all()  # red at the top
    assert (pixels[-20:] == 0).all()  # black at the bottom
    assert portrait_bin != landscape_bin


@pytest.mark.parametrize("fit", ["fit", "fill"])
@pytest.mark.parametrize("device_orientation", ["portrait", "landscape"])
@pytest.mark.parametrize("dither", DITHER_MODES)
def test_convert_image_produces_correctly_sized_output(fit, device_orientation, dither) -> None:
    raw = _solid_jpeg_bytes(400, 300, (200, 30, 30))

    bin_data, preview_png = image_converter.convert_image(
        raw, fit=fit, device_orientation=device_orientation, dither=dither,
        width=PANEL_WIDTH, height=PANEL_HEIGHT, byte_layout=PANEL_LAYOUT,
    )

    assert len(bin_data) == PANEL_BIN_SIZE
    preview = Image.open(io.BytesIO(preview_png))
    assert preview.size == (PANEL_WIDTH, PANEL_HEIGHT)
    assert preview.format == "PNG"


def test_fit_pads_with_black_fill_does_not() -> None:
    # A very wide image: "fit" must letterbox (black bars), "fill" crops
    # to cover, so it should have far less pure-black content.
    raw = _solid_jpeg_bytes(2000, 100, (255, 255, 255))

    fit_bin, _ = image_converter.convert_image(
        raw, fit="fit", dither="none", width=PANEL_WIDTH, height=PANEL_HEIGHT, byte_layout=PANEL_LAYOUT
    )
    fill_bin, _ = image_converter.convert_image(
        raw, fit="fill", dither="none", width=PANEL_WIDTH, height=PANEL_HEIGHT, byte_layout=PANEL_LAYOUT
    )

    black_code = image_converter._CODE_FOR_PALETTE_INDEX[0]
    # Packed nibble 0x00 means both pixels in that byte are black.
    fit_black_bytes = sum(1 for b in fit_bin if b == (black_code << 4 | black_code))
    fill_black_bytes = sum(1 for b in fill_bin if b == (black_code << 4 | black_code))
    assert fit_black_bytes > fill_black_bytes


def test_landscape_orientation_rotates_relative_to_portrait() -> None:
    # A single, unmistakable colored stripe near one edge lets us confirm
    # the whole canvas was rotated, not just cropped differently.
    img = Image.new("RGB", (PANEL_WIDTH, PANEL_HEIGHT), (0, 0, 0))
    for x in range(PANEL_WIDTH):
        for y in range(20):
            img.putpixel((x, y), (255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    raw = buf.getvalue()

    portrait_bin, _ = image_converter.convert_image(
        raw, fit="fill", device_orientation="portrait", dither="none",
        width=PANEL_WIDTH, height=PANEL_HEIGHT, byte_layout=PANEL_LAYOUT,
    )
    landscape_bin, _ = image_converter.convert_image(
        raw, fit="fill", device_orientation="landscape", dither="none",
        width=PANEL_WIDTH, height=PANEL_HEIGHT, byte_layout=PANEL_LAYOUT,
    )

    assert portrait_bin != landscape_bin


def test_nearest_palette_index_snaps_to_closest_color() -> None:
    # Near-black should snap to black (index 0), near-white to white (index 1).
    assert image_converter._nearest_palette_index(5, 5, 5) == 0
    assert image_converter._nearest_palette_index(250, 250, 250) == 1


def test_library_dither_snaps_unexpected_colors_via_nearest_palette(monkeypatch) -> None:
    """epaper_dithering should only ever emit palette-exact colors, but the
    safety net (_nearest_palette_index) must not crash if it doesn't."""

    def fake_dither_image(img, scheme, mode, serpentine):
        return Image.new("RGB", img.size, (128, 128, 128))

    monkeypatch.setattr(image_converter._epaper_dithering, "dither_image", fake_dither_image)

    img = Image.new("RGB", (4, 4), (0, 0, 0))
    indices = image_converter._library_dither_indices(img, "floyd_steinberg")
    assert len(indices) == 16
    # (128,128,128) is equidistant-ish but nearest_palette_index picks a
    # single deterministic winner -- just confirm it's a valid index.
    assert all(0 <= i <= 5 for i in indices)


def test_convert_image_logs_exif_correction(monkeypatch, caplog) -> None:
    img = Image.new("RGB", (300, 400), (10, 20, 30))
    exif = img.getexif()
    exif[0x0112] = 6  # "Rotate 90 CW" orientation tag
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)

    with caplog.at_level("DEBUG", logger="custom_components.fraimic.image_converter"):
        bin_data, _ = image_converter.convert_image(
            buf.getvalue(), fit="fit", dither="none", width=PANEL_WIDTH, height=PANEL_HEIGHT, byte_layout=PANEL_LAYOUT
        )
    assert len(bin_data) == PANEL_BIN_SIZE
    assert "EXIF orientation" in caplog.text


def test_convert_image_propagates_library_dither_errors(monkeypatch) -> None:
    """No pure-Python fallback exists anymore -- epaper-dithering is a hard
    dependency (see module docstring), so a failure here must surface
    loudly rather than silently degrade to a different result."""

    def _boom(img, dither):
        raise RuntimeError("epaper_dithering blew up")

    monkeypatch.setattr(image_converter, "_library_dither_indices", _boom)
    raw = _solid_jpeg_bytes(400, 300, (5, 5, 200))

    with pytest.raises(RuntimeError, match="epaper_dithering blew up"):
        image_converter.convert_image(
            raw, fit="fit", dither="floyd_steinberg", width=PANEL_WIDTH, height=PANEL_HEIGHT, byte_layout=PANEL_LAYOUT
        )
