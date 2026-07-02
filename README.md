# Fraimic E-Ink Canvas — Home Assistant integration

> **Unofficial, community integration.** Not made or maintained by
> Fraimic. Use at your own risk — Fraimic can change their API without
> notice.

Based on:
- Fraimic's REST API guide
- [fraimic_bin_converter](https://github.com/Fraimic/fraimic_bin_converter) (Spectra 6 `.bin` format)
- [epaper-dithering](https://github.com/OpenDisplay/epaper-dithering) (dithering)

## Installation

1. Copy `custom_components/fraimic` to `/config/custom_components/`.
2. Restart Home Assistant.
3. **Settings → Devices & Services → Add Integration** → search
   "Fraimic" → enter your frame's address (e.g. `http://fraimic.local`).

Dependencies (Pillow, numpy, epaper-dithering) are installed automatically by HA.

## What you get

- **Sensors**: battery, WiFi signal, `last_seen`, next scheduled refresh
- **Binary sensors**: charging status, cable connected, reachability (`reachable`)
- **Buttons**: Restart, Sleep, Refresh Display
- **Media player "Display"**: browse your media library and tap a photo
  to send it to the frame — with a live preview via the entity's own
  picture
- **Service `fraimic.send_image`**: send a path on disk, with fit/dither/
  dry_run options — for automations

## Settings (Configure)

- **Device orientation**: portrait/landscape, how the frame is physically mounted
- **Default fit**: `fit` (whole image, black bars) or `fill` (fills the
  frame, cropping overflow)
- **Default dither**: none / floyd_steinberg / atkinson / ordered /
  burkes / stucki / sierra / sierra_lite / jarvis_judice_ninke

These apply to taps in the media browser. The `send_image` service can
always override fit/dither per call.

## Good to know

- The frame is battery-powered and often unreachable (deep sleep) — this
  is normal, not a fault. Entities keep showing their last known value
  until 72 hours of silence (configurable via `UNAVAILABLE_AFTER` in
  `const.py`).
- Frame error responses (e.g. "charging cable connected blocks sleep")
  are translated into clear messages (English/Dutch, per your HA language).
- `epaper-dithering` is optional but recommended (Rust, faster/better
  than the built-in Python fallback) — already listed in `manifest.json`.

## Repo maintenance

`requirements.txt` + `.github/dependabot.yml` let Dependabot open PRs for
new dependency versions. **Note**: Home Assistant doesn't read
`requirements.txt` — you need to manually apply a Dependabot PR's version
bump to `custom_components/fraimic/manifest.json` too.
