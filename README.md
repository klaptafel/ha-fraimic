# Fraimic E-Ink Canvas — Home Assistant integration

> [!NOTE]
> This integration is vibe coded. Not made or maintained by Fraimic. Fraimic can change their API without notice.

## Installation

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=klaptafel&repository=ha-fraimic&category=integration)

Add the integration and Home Assistant will find your frame on the network by itself. You can also
type in its address yourself if you prefer.

## What you get

- **Media player**: `Display` -- browse your photos and send one to the frame
- **Buttons**: `Refresh Display` · `Restart` · `Sleep`
- **Sensors**: `Battery` · `Battery Voltage` · `WiFi Signal` · `IP Address` · `Next Scheduled Refresh` · `Last Seen` · `Send Status` · `Albums`
- **Binary sensors**: `Charging` · `Charging Cable Connected` · `Reachable` · `Render Problem` · `Voice Recording` · `Keep Awake` · `Auto Update` · `Charging LED`
- **Services**: `fraimic.send_image` · `fraimic.update_album`

## Settings (Configure)

- **Device orientation**: `portrait` `landscape`
- **Default fit**: `fit` `fill`
- **Default dithering**: `none` `floyd_steinberg` `atkinson` `ordered` `burkes` `stucki` `sierra` `sierra_lite` `jarvis_judice_ninke`

These apply when you tap a photo in the media browser. The `send_image` service can always override fit/dithering per call.

## Albums

Editing an album's name, description, Slideshow Mode, Playback Mode, or rotation schedule needs
your frame to be connected to the internet (not just your home network), since albums live on
Fraimic's own servers. Find the album's `id` in the `Albums` sensor's attributes, then:

```yaml
service: fraimic.update_album
data:
  entity_id: sensor.fraimic_e_ink_canvas_albums
  album_id: 41d321c6-1e14-4b65-ada1-769d74d03b78
  active: false
```

Only the fields you set are changed -- the rest of the album stays untouched. Creating, deleting,
or assigning an album to a frame still has to be done on [app.fraimic.com](https://app.fraimic.com).

Changes made on app.fraimic.com can take a few minutes to show up here, and album edits
specifically can take up to half an hour. If you don't want to wait, you can refresh it yourself
right away instead.
