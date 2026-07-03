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

Dithering prefers the optional `epaper-dithering` package (Rust core,
https://github.com/OpenDisplay/epaper-dithering) when it's installed:
faster and higher quality (OKLab color matching, serpentine scanning)
than the hand-rolled fallback below. It's an optional dependency (compiled
Rust extension -- not guaranteed to have a prebuilt wheel for every
platform), so everything here degrades gracefully to the pure-Python
implementation if it isn't available.

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

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from .const import PANEL_HEIGHT, PANEL_WIDTH

_LOGGER = logging.getLogger(__name__)

try:
    import epaper_dithering as _epaper_dithering

    _HAS_EPAPER_DITHERING = True
except ImportError:
    _epaper_dithering = None
    _HAS_EPAPER_DITHERING = False

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

# Atkinson error-diffusion offsets (dx, dy), each receiving 1/8 of the
# quantization error -- only used by the pure-Python fallback:
#        *  1  1
#     1  1  1
#        1
_ATKINSON_OFFSETS = ((1, 0), (2, 0), (-1, 1), (0, 1), (1, 1), (0, 2))

# Algorithms the epaper_dithering library can handle for us -- all 9 it
# supports. Our own hand-rolled fallback (used only if the library isn't
# installed, e.g. no prebuilt wheel for an unusual platform) only covers
# "none"/"floyd_steinberg" (via Pillow) and "atkinson" (hand-rolled below);
# for the other 6, that fallback path degrades to floyd_steinberg with a
# debug log rather than implementing 6 more error-diffusion algorithms by
# hand for what should be a rare edge case.
_LIBRARY_DITHER_MODES = frozenset(
    {
        "none",
        "floyd_steinberg",
        "atkinson",
        "ordered",
        "burkes",
        "stucki",
        "sierra",
        "sierra_lite",
        "jarvis_judice_ninke",
    }
)
_FALLBACK_DITHER_MODES = frozenset({"none", "floyd_steinberg", "atkinson"})


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


def _pillow_quantize_indices(img: Image.Image, dither: str) -> bytearray:
    """Fallback path: Pillow's built-in quantizer. Only supports "none"
    and "floyd_steinberg" (no Atkinson without epaper_dithering or our
    own hand-rolled version below)."""
    pal_img = _build_palette_image()
    dither_mode = Image.Dither.FLOYDSTEINBERG if dither == "floyd_steinberg" else Image.Dither.NONE
    quantized = img.quantize(colors=len(_PALETTE_RGB), palette=pal_img, dither=dither_mode)
    return bytearray(quantized.tobytes())


def _atkinson_dither_indices(img: Image.Image) -> bytearray:
    """Hand-rolled Atkinson error-diffusion fallback for when
    epaper_dithering isn't installed. Pillow has no built-in Atkinson
    ditherer (only Floyd-Steinberg via quantize()).

    Deliberately plain Python floats/lists rather than numpy: this loop
    is inherently sequential (each pixel's error depends on already-
    processed neighbors, so it can't be vectorized away), and numpy's
    per-element overhead is worse than plain floats for that access
    pattern. Takes a few seconds on a full 1200x1600 image -- fine since
    it always runs in a background executor job, never on the event loop.
    """
    width, height = img.size
    src = img.getdata()
    r_buf = [float(p[0]) for p in src]
    g_buf = [float(p[1]) for p in src]
    b_buf = [float(p[2]) for p in src]

    indices = bytearray(width * height)
    palette = _PALETTE_RGB

    for y in range(height):
        row = y * width
        for x in range(width):
            i = row + x
            r, g, b = r_buf[i], g_buf[i], b_buf[i]

            best_idx = 0
            best_dist = None
            for pi, (pr, pg, pb) in enumerate(palette):
                dist = (pr - r) ** 2 + (pg - g) ** 2 + (pb - b) ** 2
                if best_dist is None or dist < best_dist:
                    best_dist = dist
                    best_idx = pi
            indices[i] = best_idx

            pr, pg, pb = palette[best_idx]
            er = (r - pr) / 8.0
            eg = (g - pg) / 8.0
            eb = (b - pb) / 8.0

            for dx, dy in _ATKINSON_OFFSETS:
                nx, ny = x + dx, y + dy
                if 0 <= nx < width and 0 <= ny < height:
                    j = ny * width + nx
                    r_buf[j] += er
                    g_buf[j] += eg
                    b_buf[j] += eb

    return indices


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
    algorithms epaper_dithering supports. Uses that library when
    installed (faster, higher quality); otherwise falls back to Pillow's
    built-in quantizer ("none", "floyd_steinberg") or a hand-rolled
    implementation ("atkinson") -- the other 6 fall back to
    floyd_steinberg if the library is unavailable.
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

    if _HAS_EPAPER_DITHERING and dither in _LIBRARY_DITHER_MODES:
        try:
            indices = _library_dither_indices(img, dither)
        except Exception:  # noqa: BLE001
            _LOGGER.exception(
                "epaper_dithering failed for dither='%s', falling back to "
                "built-in dithering",
                dither,
            )
            indices = None
    else:
        indices = None

    if indices is None:
        # Our own hand-rolled fallback only covers 3 of the 9 algorithms
        # (see _FALLBACK_DITHER_MODES) -- this only gets hit at all if
        # epaper_dithering is unavailable/failed, which should be rare
        # since it's a manifest.json requirement. For the other 6, fall
        # back to floyd_steinberg rather than silently producing a wrong
        # or crashing result.
        effective_dither = dither if dither in _FALLBACK_DITHER_MODES else "floyd_steinberg"
        if effective_dither != dither:
            _LOGGER.debug(
                "No fallback implementation for dither='%s' without "
                "epaper_dithering; using floyd_steinberg instead.",
                dither,
            )
        if effective_dither == "atkinson":
            indices = _atkinson_dither_indices(img)
        else:
            indices = _pillow_quantize_indices(img, effective_dither)

    bin_data = _pack_bin(indices)
    preview_png = _indices_to_preview_png(indices)

    return bin_data, preview_png
