"""Convert an ordinary image into the Spectra 6 `.bin` frame-buffer format.

This is a Home Assistant-friendly re-implementation of the packing logic
from https://github.com/Fraimic/fraimic_bin_converter, so it can run
in-process (via hass.async_add_executor_job) instead of shelling out to a
separate Python script.

Output format (EL133UF1 / Spectra 6):
- 1200 x 1600 pixels, portrait.
- 6 colors, 4-bit device code per pixel:
    black=0x0, white=0x1, yellow=0x2, red=0x3, blue=0x5, green=0x6
  (0x4 is intentionally unused by the panel.)
- 4-bit indexed, two pixels per byte (high nibble = even column, low
  nibble = odd column).
- Each row is split into a left half (columns 0-599) and a right half
  (columns 600-1199). ALL left-half bytes for the whole image come first,
  then ALL right-half bytes. Total size is always exactly 960,000 bytes.

Dithering is done by the required `epaper-dithering` package (Rust core,
https://github.com/OpenDisplay/epaper-dithering): OKLab color matching,
serpentine scanning, and all 9 error-diffusion algorithms. The frame's
own conversion tool (fraimic_bin_converter) treats dithering quality as
essential, not optional polish, so this doesn't carry a pure-Python
fallback -- epaper-dithering ships wheels for every platform Home
Assistant actually runs on (x86_64, aarch64/armv7l, and musllinux for
HA OS's Alpine-based containers), so there's nothing realistic to
degrade gracefully into.

Always uses epaper_dithering's *theoretical* ColorScheme.BWGBRY palette
(pure RGB primaries), not its "measured" SPECTRA_7_3_6COLOR* palettes --
those are calibrated for the 7.3" Spectra panel, not the 13.3"/31.5" one
this device uses. An earlier A/B test comparing them concluded
floyd_steinberg + BWGBRY looked best, so this no longer exposes a palette
choice.
"""
from __future__ import annotations

import io
import logging

import epaper_dithering as _epaper_dithering
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from .const import PANEL_HEIGHT, PANEL_WIDTH

_LOGGER = logging.getLogger(__name__)

# Palette order must match the code table below 1:1.
_PALETTE_RGB = [
    (0, 0, 0),        # 0x0 black
    (255, 255, 255),  # 0x1 white
    (255, 255, 0),    # 0x2 yellow
    (255, 0, 0),      # 0x3 red
    (0, 0, 255),      # 0x5 blue
    (0, 255, 0),      # 0x6 green
]
_CODE_FOR_PALETTE_INDEX = [0x0, 0x1, 0x2, 0x3, 0x5, 0x6]
_RGB_TO_INDEX = {rgb: i for i, rgb in enumerate(_PALETTE_RGB)}

_BRIGHTNESS = 1.1
_CONTRAST = 1.2
_SATURATION = 1.2


def _fit_image(img: Image.Image, fit: str, device_orientation: str) -> Image.Image:
    """Fit `img` onto a canvas matching the panel's native buffer shape.

    The panel's native buffer is ALWAYS PANEL_WIDTH x PANEL_HEIGHT
    (1200x1600) -- a hardware fact that never changes. `device_orientation`
    is about compensating for how the frame is physically mounted:
    - "portrait": compose directly against the native 1200x1600 canvas.
    - "landscape": compose against a visually-1600x1200 canvas instead
      (matching what a viewer actually sees), then rotate the whole
      composed result 90 degrees into the native buffer shape at the end.

    `fit`: "fit" (show the whole image, pad with black -- CSS
    object-fit: contain) or "fill" (fill the frame, cropping overflow --
    CSS object-fit: cover) -- applied against whichever canvas shape
    `device_orientation` selects.
    """
    landscape_target = device_orientation == "landscape"
    visual_w, visual_h = (PANEL_HEIGHT, PANEL_WIDTH) if landscape_target else (PANEL_WIDTH, PANEL_HEIGHT)

    steps: list[str] = [f"input={img.size}", f"visual_canvas_target={visual_w}x{visual_h}"]

    src_ratio = img.width / img.height
    target_ratio = visual_w / visual_h

    if fit == "fill":
        if src_ratio > target_ratio:
            new_height = visual_h
            new_width = round(new_height * src_ratio)
        else:
            new_width = visual_w
            new_height = round(new_width / src_ratio)
        img = img.resize((new_width, new_height), Image.LANCZOS)
        left = (new_width - visual_w) // 2
        top = (new_height - visual_h) // 2
        result = img.crop((left, top, left + visual_w, top + visual_h))
        steps.append(f"fill: resized={img.size} cropped={result.size}")
    else:
        # "fit": scale to fit entirely, pad with black.
        if src_ratio > target_ratio:
            new_width = visual_w
            new_height = round(new_width / src_ratio)
        else:
            new_height = visual_h
            new_width = round(new_height * src_ratio)
        resized = img.resize((new_width, new_height), Image.LANCZOS)
        canvas = Image.new("RGB", (visual_w, visual_h), (0, 0, 0))
        canvas.paste(resized, ((visual_w - new_width) // 2, (visual_h - new_height) // 2))
        result = canvas
        steps.append(f"fit: resized={resized.size} padded_canvas={result.size}")

    if landscape_target:
        # Rotation direction is a guess (clockwise) -- unconfirmed which
        # way matches a real landscape mounting. If it comes out upside
        # down or sideways, this is the line to flip (change -90 to 90).
        result = result.rotate(-90, expand=True)
        steps.append(f"landscape_final_rotate(-90)={result.size}")

    _LOGGER.debug(
        "_fit_image pipeline (fit=%s, device_orientation=%s): %s",
        fit, device_orientation, " -> ".join(steps),
    )

    return result


def _build_palette_image() -> Image.Image:
    pal_img = Image.new("P", (1, 1))
    flat = []
    for rgb in _PALETTE_RGB:
        flat.extend(rgb)
    flat.extend([0, 0, 0] * (256 - len(_PALETTE_RGB)))
    pal_img.putpalette(flat)
    return pal_img


def _nearest_palette_index(r: float, g: float, b: float) -> int:
    best_idx = 0
    best_dist = None
    for pi, (pr, pg, pb) in enumerate(_PALETTE_RGB):
        dist = (pr - r) ** 2 + (pg - g) ** 2 + (pb - b) ** 2
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_idx = pi
    return best_idx


def _library_dither_indices(img: Image.Image, dither: str) -> bytearray:
    """Preferred path: the epaper_dithering Rust extension."""
    lib_mode = getattr(_epaper_dithering.DitherMode, dither.upper())
    result = _epaper_dithering.dither_image(
        img, _epaper_dithering.ColorScheme.BWGBRY, mode=lib_mode, serpentine=True
    )
    result = result.convert("RGB")

    width, height = result.size
    indices = bytearray(width * height)
    for i, px in enumerate(result.getdata()):
        idx = _RGB_TO_INDEX.get(px)
        if idx is None:
            # Shouldn't normally happen (dithered output should already be
            # snapped to palette colors), but don't crash if it does.
            idx = _nearest_palette_index(*px)
        indices[i] = idx
    return indices


def _pack_bin(indices: bytes | bytearray) -> bytes:
    """indices: flat, row-major buffer of palette indices (0-5), length
    PANEL_WIDTH * PANEL_HEIGHT."""
    bytes_per_half_row = PANEL_WIDTH // 4  # 300
    left = bytearray(PANEL_HEIGHT * bytes_per_half_row)
    right = bytearray(PANEL_HEIGHT * bytes_per_half_row)

    li = 0
    ri = 0
    for y in range(PANEL_HEIGHT):
        row_offset = y * PANEL_WIDTH
        for x in range(0, PANEL_WIDTH // 2, 2):
            hi = _CODE_FOR_PALETTE_INDEX[indices[row_offset + x]]
            lo = _CODE_FOR_PALETTE_INDEX[indices[row_offset + x + 1]]
            left[li] = (hi << 4) | lo
            li += 1
        for x in range(PANEL_WIDTH // 2, PANEL_WIDTH, 2):
            hi = _CODE_FOR_PALETTE_INDEX[indices[row_offset + x]]
            lo = _CODE_FOR_PALETTE_INDEX[indices[row_offset + x + 1]]
            right[ri] = (hi << 4) | lo
            ri += 1

    assert li == PANEL_HEIGHT * bytes_per_half_row
    assert ri == PANEL_HEIGHT * bytes_per_half_row
    return bytes(left) + bytes(right)


def _indices_to_preview_png(indices: bytes | bytearray) -> bytes:
    pal_img = _build_palette_image()
    out = Image.frombytes("P", (PANEL_WIDTH, PANEL_HEIGHT), bytes(indices))
    out.putpalette(pal_img.getpalette())
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()


def convert_image(
    raw_bytes: bytes,
    *,
    fit: str = "fit",
    device_orientation: str = "portrait",
    dither: str = "floyd_steinberg",
) -> tuple[bytes, bytes]:
    """Convert raw image bytes (jpg/png/etc.) into a Spectra 6 .bin blob.

    All arguments after `raw_bytes` are keyword-only, deliberately --
    this function has enough similarly-typed str parameters that
    positional calls are an easy way to silently swap two of them.

    `fit`: "fit" (show the whole image, pad with black -- CSS
    object-fit: contain) or "fill" (fill the frame, cropping overflow --
    CSS object-fit: cover).
    `device_orientation`: "portrait" (default) or "landscape" -- how the
    frame is physically mounted. See the comment on
    CONF_DEVICE_ORIENTATION in const.py for important caveats.
    `dither`: "none", "floyd_steinberg", "atkinson", "ordered", "burkes",
    "stucki", "sierra", "sierra_lite", or "jarvis_judice_ninke" -- all 9
    algorithms epaper_dithering supports.
    Always uses the theoretical BWGBRY palette (see module docstring).

    Returns (bin_data, preview_png). preview_png is the quantized/dithered
    1200x1600 image re-encoded as PNG -- i.e. exactly what will show up on
    the panel -- used as the media player's entity picture.

    Runs synchronously/CPU-bound -- call via hass.async_add_executor_job.
    """
    img = Image.open(io.BytesIO(raw_bytes))
    pre_exif_size = img.size
    img = ImageOps.exif_transpose(img)
    if img.size != pre_exif_size:
        _LOGGER.debug(
            "EXIF orientation tag corrected image size %s -> %s",
            pre_exif_size,
            img.size,
        )
    img = img.convert("RGB")

    # Enhance/filter the source image BEFORE fitting it onto the panel
    # canvas, not after. Doing this after "fit" mode adds black padding
    # would let those solid-black bars skew the image's mean brightness,
    # which Pillow's Contrast/Brightness enhancers use as their pivot --
    # a heavily letterboxed (e.g. square) photo would get visibly
    # under/over-adjusted as a result. It also avoids the sharpen/smooth
    # filters blurring across the hard photo-to-padding edge.
    img = ImageEnhance.Brightness(img).enhance(_BRIGHTNESS)
    img = ImageEnhance.Contrast(img).enhance(_CONTRAST)
    img = ImageEnhance.Color(img).enhance(_SATURATION)
    img = img.filter(ImageFilter.EDGE_ENHANCE)
    img = img.filter(ImageFilter.SMOOTH)
    img = img.filter(ImageFilter.SHARPEN)

    img = _fit_image(img, fit, device_orientation)

    indices = _library_dither_indices(img, dither)

    bin_data = _pack_bin(indices)
    preview_png = _indices_to_preview_png(indices)

    return bin_data, preview_png
