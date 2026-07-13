[![Made for Home Assistant](https://img.shields.io/badge/Made%20for-Home%20Assistant-blue?style=for-the-badge&logo=homeassistant)](https://www.home-assistant.io/)
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge)](https://github.com/hacs/integration)

# Fraimic E-Ink Canvas: Home Assistant integration

> [!NOTE]
> This integration is vibe coded. Not made or maintained by Fraimic. Fraimic can change their API without notice.

## Features

- **Media player**: `Display` -- browse your photos and send one to the frame
- **Buttons**: `Refresh Display` · `Restart` · `Sleep`
- **Sensors**: `Battery` · `Battery Voltage` · `WiFi Signal` · `IP Address` · `Next Scheduled Refresh` · `Last Seen` · `Send Status` · `Albums`
- **Binary sensors**: `Charging` · `Charging Cable Connected` · `Reachable` · `Render Problem` · `Voice Recording` · `Keep Awake` · `Auto Update` · `Charging LED`
- **Services**: `fraimic.send_image` · `fraimic.update_album`

## Installation

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=klaptafel&repository=ha-fraimic&category=integration)

Add the integration and Home Assistant will find your frame on the network by itself. You can also
type in its address yourself if you prefer.

### Supported devices

Any Fraimic E-Ink Canvas frame reachable on your local network. Panel size (13.3" or 31.5") is
detected automatically and shown as the device model; there's nothing to select manually.

## Configuration

- **Device orientation**: `portrait` `landscape`
- **Default fit**: `fit` `fill`
- **Default dithering**: `none` `floyd_steinberg` `atkinson` `ordered` `burkes` `stucki` `sierra` `sierra_lite` `jarvis_judice_ninke`

These apply when you tap a photo in the media browser. The `send_image` service can always override fit/dithering per call.

## Removal

Go to **Settings > Devices & Services**, find **Fraimic**, and remove it from there. This also
deletes the locally cached last-sent-image preview; it does not affect anything on
[app.fraimic.com](https://app.fraimic.com) or on the frame itself.

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

## How updates work

The frame is battery-powered and sleeps most of the time, so this integration polls rather than
holds a live connection: full status (`/api/info`) every 5 minutes, battery every 60 seconds. A
single missed poll while the frame is asleep is normal and expected: entities keep showing their
last known value instead of flipping to unavailable, and only go unavailable after a much longer
stretch (72h) with no successful contact at all. Albums are polled separately and only while the
frame itself is reachable, since that endpoint is cloud-proxied and would otherwise time out for
no reason while the frame is asleep.

## Example automation

Send today's calendar view to the frame every morning:

```yaml
automation:
  - alias: "Fraimic - morning refresh"
    trigger:
      - platform: time
        at: "07:00:00"
    action:
      - service: fraimic.send_image
        target:
          entity_id: media_player.fraimic_e_ink_canvas_display
        data:
          media_content_id: media-source://media_source/local/calendar-today.png
          media_content_type: image/png
```

## Troubleshooting

- **Discovery finds nothing**: make sure the frame and your Home Assistant instance are on the
  same subnet; the subnet scan only probes your local /24. You can always type the frame's IP
  address in manually instead.
- **Frame shows as unreachable right after adding it**: this is often just the frame being asleep.
  Wait for its next scheduled wake, or wake it with a tap; the integration doesn't need to be
  the one to wake it up.
- **Album edits from Home Assistant don't seem to apply**: check that the frame is connected to
  the internet, not just your home network: album data lives on Fraimic's own servers, not on
  the frame.

## Known limitations

- This is a reverse-engineered integration, not built or maintained by Fraimic; their API can
  change without notice and break things here.
- Panel-size detection (used for the device model) is a best-effort scrape of the frame's own
  `/info` admin page, not something the official API exposes: it can briefly show a generic
  model name right after a fresh restart, before that scrape has succeeded once.
- Right after a Home Assistant restart, if the frame happens to be asleep, all of this
  integration's entities can be briefly unavailable; setup waits for one successful poll before
  creating them. Once entities exist, sending an image already tolerates the frame being asleep
  (it retries for up to 10 minutes, waiting for the frame's own wake), so this only affects the
  narrow window immediately after a restart, not normal day-to-day sleep.
- Creating, deleting, or assigning an album to a frame can only be done on
  [app.fraimic.com](https://app.fraimic.com); this integration can only edit existing albums'
  metadata.
