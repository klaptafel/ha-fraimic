# Fraimic E-Ink Canvas — Home Assistant integration

> [!NOTE]
> This integration is vibe coded. Not made or maintained by Fraimic. Fraimic can change their API without notice.

## Installation
[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=klaptafel&repository=ha-fraimic&category=integration)

## What you get

- **Media player**: `Display` *(To browse your media library and send a photo to the frame)*
- **Buttons**: `Refresh Display` `Restart` `Sleep`
- **Sensors**: `Battery` `Next Scheduled Refresh` `Charging` `Charging Cable Connected` `IP Address` `Last Seen` `Reachable` `Render Problem` `Wifi Signal` `Battery Voltage`
- **Service**: `fraimic.send_image`

## Settings (Configure)

- **Device orientation**: `portrait` `landscape`
- **Default fit**: `fit` `fill`
- **Default dithering**: `none` `floyd_steinberg` `atkinson` `ordered` `burkes` `stucki` `sierra` `sierra_lite` `jarvis_judice_ninke`

These apply to taps in the media browser. The `send_image` service can always override fit/dither per call.
