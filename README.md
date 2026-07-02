# Fraimic E-Ink Canvas — Home Assistant integration
> [!NOTE]
> This integration is vibe coded. Not made or maintained by Fraimic. Use at your own risk. Fraimic can change their API without notice.

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=klaptafel&repository=ha-fraimic&category=integration)

Based on:
- Fraimic's REST API guide
- [fraimic_bin_converter](https://github.com/Fraimic/fraimic_bin_converter) (Spectra 6 `.bin` format)
- [epaper-dithering](https://github.com/OpenDisplay/epaper-dithering) (dithering)


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
