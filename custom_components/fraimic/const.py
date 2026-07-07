"""Constants for the Fraimic E-Ink Canvas integration."""
from datetime import timedelta

DOMAIN = "fraimic"

CONF_HOST = "host"

DEFAULT_SCAN_INTERVAL = timedelta(minutes=5)
DEFAULT_TIMEOUT = 10

# API endpoints (relative to http://<host>)
EP_INFO = "/api/info"
EP_BATTERY = "/api/battery"
EP_RESTART = "/api/restart"
EP_SLEEP = "/api/sleep"
EP_REFRESH = "/api/refresh"
EP_IMAGE = "/api/image"
# Not in the official API guide -- see api.get_albums for why.
EP_ALBUMS = "/api/albums"

DEFAULT_BATTERY_SCAN_INTERVAL = timedelta(seconds=60)
# Cloud-proxied and not time-sensitive -- much slower than the main poll,
# and gated on device_reachable besides (see FraimicAlbumsCoordinator).
DEFAULT_ALBUMS_SCAN_INTERVAL = timedelta(minutes=30)

# Spectra 6 panel geometry (EL133UF1 controller)
PANEL_WIDTH = 1200
PANEL_HEIGHT = 1600
PANEL_BIN_SIZE = 960_000  # bytes

# Service for pushing a local file straight to the frame, bypassing the
# media browser (handy from automations/scripts).
SERVICE_SEND_IMAGE = "send_image"
ATTR_PATH = "path"
ATTR_FIT = "fit"
ATTR_DITHER = "dither"
ATTR_DRY_RUN = "dry_run"

DEFAULT_FIT = "fill"
DEFAULT_DITHER = "atkinson"
DEFAULT_DRY_RUN = False

# "none": nearest-color only, hard edges, banding on gradients.
# "floyd_steinberg": common error-diffusion dither, fast.
# "atkinson": the algorithm fraimic_bin_converter itself uses -- closest
# match to the original tool's output, and this integration's default.
DITHER_MODES = (
    "none",
    "floyd_steinberg",
    "atkinson",
    "ordered",
    "burkes",
    "stucki",
    "sierra",
    "sierra_lite",
    "jarvis_judice_ninke",
)

# "fit" (CSS object-fit: contain): show the whole image, pad with black.
# "fill" (CSS object-fit: cover): fill the frame, cropping overflow.
FIT_MODES = ("fit", "fill")

# How the frame is physically mounted. The panel's native buffer is
# ALWAYS 1200(w)x1600(h) -- that never changes, it's a hardware fact of
# the EL133UF1 controller. This setting is about compensating for
# physical mounting: "landscape" means compose the image against a
# visually-1600x1200 canvas, then rotate the whole composed result 90
# degrees into the panel's native buffer shape, so it displays upright
# once the physically-rotated panel shows it.
#
# UNCONFIRMED: Fraimic's marketing mentions an on-board accelerometer
# that "knows" device orientation -- if the firmware already compensates
# for physical rotation itself, setting this to "landscape" here would
# double-rotate content. Test empirically; if content already displays
# upright on a landscape-mounted frame with this left at "portrait",
# leave it alone.
CONF_DEVICE_ORIENTATION = "device_orientation"
DEFAULT_DEVICE_ORIENTATION = "portrait"
DEVICE_ORIENTATIONS = ("portrait", "landscape")

# Options flow keys: lets the person set defaults used when tapping an
# image in the media browser (which has no way to pass per-call
# parameters, unlike the fraimic.send_image service).
CONF_DEFAULT_FIT = "default_fit"
CONF_DEFAULT_DITHER = "default_dither"

# How long we tolerate silence from the frame before treating entities as
# genuinely unavailable instead of just "asleep right now". The frame only
# wakes on a tap or its own refresh schedule, so it can legitimately be
# unreachable for long stretches -- this is deliberately generous. Tune
# this if your usage pattern differs (e.g. lower it if your frame wakes
# more often, or raise it if it goes days between scheduled refreshes).
UNAVAILABLE_AFTER = timedelta(hours=72)

# Service for updating an existing album (found via sensor.albums' own `id`
# attribute) -- not full CRUD, just the fields worth flipping from HA.
# Confirmed via curl against the real device: album creation/deletion and
# device-assignment stay on app.fraimic.com.
SERVICE_UPDATE_ALBUM = "update_album"
ATTR_ALBUM_ID = "album_id"
ATTR_ALBUM_NAME = "name"
ATTR_DESCRIPTION = "description"
ATTR_ACTIVE = "active"
ATTR_PLAYBACK_MODE = "playback_mode"
ATTR_SCHEDULE_TYPE = "schedule_type"
ATTR_INTERVAL_VALUE = "interval_value"
ATTR_INTERVAL_UNIT = "interval_unit"
ATTR_DAYS = "days"

PLAYBACK_MODES = ("sequential", "random")
SCHEDULE_TYPES = ("interval", "specific_days")
SCHEDULE_INTERVAL_UNITS = ("minutes", "hours", "days")
SCHEDULE_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
