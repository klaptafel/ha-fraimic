"""Convert an ordinary image into the Spectra 6 `.bin` frame-buffer format.

This is a Home Assistant-friendly re-implementation of the packing logic
from https://github.com/Fraimic/fraimic_bin_converter, so it can run
in-process (via hass.async_add_executor_job) instead of shelling out to a
separate Python script.

Output format (Spectra 6, both known panel sizes -- see frame_types.py):
- 1200 x 1600 pixels portrait (13.3", EL133UF1) or 2560 x 1440 pixels
  landscape (31.5") -- the pixel dimensions vary by physical panel, but
  the byte layout below is identical for both.
- 6 colors, 4-bit device code per pixel:
    black=0x0, white=0x1, yellow=0x2, red=0x3, blue=0x5, green=0x6
  (0x4 is intentionally unused by the panel.)
- 4-bit indexed, two pixels per byte (high nibble = even column, low
  nibble = odd column).
- Each row is split into a left half (columns 0 .. width//2 - 1) and a
  right half (columns width//2 .. width - 1). ALL left-half bytes for the
  whole image come first, then ALL right-half bytes. Total size is
  always exactly width * height // 2 bytes.

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
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

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

# Every _PALETTE_RGB entry has each of its R/G/B channels at exactly 0 or
# 255, so it can be identified by a 3-bit "which channels are lit" code
# (R<<2 | G<<1 | B) instead of a full RGB comparison. Two of the 8 possible
# codes (011=cyan, 101=magenta) aren't used by any real palette entry.
# _INDEX_FOR_CHANNEL_CODE maps a code straight to its palette index for the
# 6 codes that are real palette entries; _VALID_CHANNEL_CODE flags which
# codes those are, so the (should-never-happen) fallback path below still
# runs for cyan/magenta codes instead of silently mapping them to index 0.
def _build_channel_code_luts() -> tuple[np.ndarray, np.ndarray]:
    index_for_code = np.zeros(8, dtype=np.uint8)
    valid_code = np.zeros(8, dtype=bool)
    for idx, (r, g, b) in enumerate(_PALETTE_RGB):
        code = ((1 if r == 255 else 0) << 2) | ((1 if g == 255 else 0) << 1) | (1 if b == 255 else 0)
        index_for_code[code] = idx
        valid_code[code] = True
    return index_for_code, valid_code


_INDEX_FOR_CHANNEL_CODE, _VALID_CHANNEL_CODE = _build_channel_code_luts()
_CODE_LUT = np.array(_CODE_FOR_PALETTE_INDEX, dtype=np.uint8)

_BRIGHTNESS = 1.1
_CONTRAST = 1.2
_SATURATION = 1.2


def _fit_image(img: Image.Image, fit: str, device_orientation: str, width: int, height: int) -> Image.Image:
    """Fit `img` onto a canvas matching the panel's native buffer shape.

    `width`/`height` are the panel's native buffer dimensions -- a
    hardware fact that never changes for a given physical panel, but
    differs between panel sizes (e.g. portrait-native 1200x1600 for the
    13.3", landscape-native 2560x1440 for the 31.5" -- see frame_types.py).
    `device_orientation` is a *visual* preference, independent of which
    shape happens to be native: "portrait" always means compose against a
    canvas taller than it is wide, "landscape" always means wider than
    tall, regardless of the native buffer's own shape. Composing then
    rotates onto the native buffer only when the two shapes differ --
    e.g. "portrait" on a landscape-native panel does rotate; "landscape"
    on a portrait-native panel (the only case this integration has ever
    shipped before) also rotates, exactly as before.

    `fit`: "fit" (show the whole image, pad with black -- CSS
    object-fit: contain) or "fill" (fill the frame, cropping overflow --
    CSS object-fit: cover) -- applied against whichever canvas shape
    `device_orientation` selects.
    """
    is_native_landscape = width > height
    want_landscape_canvas = device_orientation == "landscape"
    visual_w, visual_h = (
        (max(width, height), min(width, height))
        if want_landscape_canvas
        else (min(width, height), max(width, height))
    )
    rotate_needed = want_landscape_canvas != is_native_landscape

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
        img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
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
        resized = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (visual_w, visual_h), (0, 0, 0))
        canvas.paste(resized, ((visual_w - new_width) // 2, (visual_h - new_height) // 2))
        result = canvas
        steps.append(f"fit: resized={resized.size} padded_canvas={result.size}")

    if rotate_needed:
        # Rotation direction is a guess (clockwise) -- unconfirmed on
        # either known panel size (no hardware here to test the 31.5"
        # against, and the 13.3" case is separately flagged as untested
        # too). If content comes out upside down or sideways, this is the
        # line to flip (change -90 to 90).
        result = result.rotate(-90, expand=True)
        steps.append(f"rotate(-90)={result.size}")

    _LOGGER.debug(
        "_fit_image pipeline (fit=%s, device_orientation=%s): %s",
        fit, device_orientation, " -> ".join(steps),
    )

    return result


def _build_palette_image() -> Image.Image:
    pal_img = Image.new("P", (1, 1))
    flat: list[int] = []
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

    # Vectorized: every pixel's R/G/B channel is compared against 255 once
    # (not per-pixel in a Python loop), then reduced to its 3-bit channel
    # code and mapped through the 8-entry LUT above.
    arr = np.asarray(result, dtype=np.uint8)
    r_hi, g_hi, b_hi = arr[..., 0] == 255, arr[..., 1] == 255, arr[..., 2] == 255
    channel_is_binary = ((arr[..., 0] == 0) | r_hi) & ((arr[..., 1] == 0) | g_hi) & ((arr[..., 2] == 0) | b_hi)
    code = (r_hi.astype(np.uint8) << 2) | (g_hi.astype(np.uint8) << 1) | b_hi.astype(np.uint8)
    valid = channel_is_binary & _VALID_CHANNEL_CODE[code]
    indices = _INDEX_FOR_CHANNEL_CODE[code]

    if not valid.all():
        # Shouldn't normally happen (dithered output should already be
        # snapped to palette colors), but don't crash if it does.
        for y, x in zip(*np.where(~valid)):
            indices[y, x] = _nearest_palette_index(*arr[y, x].tolist())

    return bytearray(indices.reshape(-1).tobytes())


def _pack_bin(indices: bytes | bytearray, width: int, height: int) -> bytes:
    """indices: flat, row-major buffer of palette indices (0-5), length
    width * height."""
    # Vectorized: see _library_dither_indices for why. Same left/right-half,
    # high/low-nibble layout as the original per-pixel loop.
    idx = np.frombuffer(bytes(indices), dtype=np.uint8).reshape(height, width)
    codes = _CODE_LUT[idx]
    left_half, right_half = codes[:, 0 : width // 2], codes[:, width // 2 : width]

    def _pack_half(half: np.ndarray) -> bytes:
        return ((half[:, 0::2] << 4) | half[:, 1::2]).astype(np.uint8).tobytes()

    return _pack_half(left_half) + _pack_half(right_half)


def _indices_to_preview_png(indices: bytes | bytearray, width: int, height: int) -> bytes:
    pal_img = _build_palette_image()
    palette = pal_img.getpalette()
    assert palette is not None  # set by _build_palette_image's own putpalette() call
    out = Image.frombytes("P", (width, height), bytes(indices))
    out.putpalette(palette)
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()


def convert_image(
    raw_bytes: bytes,
    *,
    fit: str = "fit",
    device_orientation: str = "portrait",
    dither: str = "floyd_steinberg",
    width: int,
    height: int,
) -> tuple[bytes, bytes]:
    """Convert raw image bytes (jpg/png/etc.) into a Spectra 6 .bin blob.

    All arguments after `raw_bytes` are keyword-only, deliberately --
    this function has enough similarly-typed str parameters that
    positional calls are an easy way to silently swap two of them.
    `width`/`height` have no default for the same reason, just more so:
    silently defaulting to one panel's dimensions is exactly the kind of
    mistake that produces a garbled image on a *different* physical
    panel with no error at all (see frame_types.py for where these
    actually come from -- the frame's detected physical size).

    `fit`: "fit" (show the whole image, pad with black -- CSS
    object-fit: contain) or "fill" (fill the frame, cropping overflow --
    CSS object-fit: cover).
    `device_orientation`: "portrait" (default) or "landscape" -- how the
    frame is physically mounted. See the comment on
    CONF_DEVICE_ORIENTATION in const.py for important caveats.
    `dither`: "none", "floyd_steinberg", "atkinson", "ordered", "burkes",
    "stucki", "sierra", "sierra_lite", or "jarvis_judice_ninke" -- all 9
    algorithms epaper_dithering supports.
    `width`/`height`: the target panel's native buffer dimensions (see
    frame_types.FrameType).
    Always uses the theoretical BWGBRY palette (see module docstring).

    Returns (bin_data, preview_png). preview_png is the quantized/dithered
    image re-encoded as PNG at the panel's native dimensions -- i.e.
    exactly what will show up on the panel -- used as the media player's
    entity picture.

    Runs synchronously/CPU-bound -- call via hass.async_add_executor_job.
    """
    img: Image.Image = Image.open(io.BytesIO(raw_bytes))
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

    img = _fit_image(img, fit, device_orientation, width, height)

    indices = _library_dither_indices(img, dither)

    bin_data = _pack_bin(indices, width, height)
    preview_png = _indices_to_preview_png(indices, width, height)

    return bin_data, preview_png
