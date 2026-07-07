# Fraimic E-Ink Canvas — Home Assistant integration

> [!NOTE]
> This integration is vibe coded. Not made or maintained by Fraimic. Fraimic can change their API without notice.

## Installation

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=klaptafel&repository=ha-fraimic&category=integration)

## What you get

- **Media player**: `Display` *(browse your media library and send a photo to the frame)*
- **Buttons**: `Refresh Display` · `Restart` · `Sleep`
- **Sensors**: `Battery` · `Battery Voltage` · `WiFi Signal` · `IP Address` · `Next Scheduled Refresh` · `Last Seen` · `Send Status` · `Albums`
- **Binary sensors**: `Charging` · `Charging Cable Connected` · `Reachable` · `Render Problem` · `Voice Recording` · `Keep Awake` · `Auto Update` · `Charging LED`
- **Services**: `fraimic.send_image` · `fraimic.update_album`

## Settings (Configure)

- **Device orientation**: `portrait` `landscape`
- **Default fit**: `fit` `fill`
- **Default dithering**: `none` `floyd_steinberg` `atkinson` `ordered` `burkes` `stucki` `sierra` `sierra_lite` `jarvis_judice_ninke`

These apply to taps in the media browser. The `send_image` service can always override fit/dither per call.

## Local vs. cloud

Everything in this integration talks directly to the frame over your LAN -- no internet connection
needed for the frame or Home Assistant -- **except the `Albums` sensor**. That one calls an
undocumented endpoint that the frame's own firmware proxies straight through to Fraimic's cloud
servers, so it only updates while the frame itself has real internet access, not just local network
connectivity.

The `fraimic.update_album` service can change an existing album's name, description, Slideshow Mode
(on/off), Playback Mode, or Image Rotation Schedule -- find the album's `id` via the `Albums`
sensor's `albums` attribute, then:

```yaml
service: fraimic.update_album
data:
  entity_id: sensor.fraimic_e_ink_canvas_albums
  album_id: 41d321c6-1e14-4b65-ada1-769d74d03b78
  active: false
```

Only the fields you set are changed -- everything else on the album is left alone. Creating,
deleting, or assigning an album to a frame still has to be done through Fraimic's own website
([app.fraimic.com](https://app.fraimic.com)); this integration doesn't touch that. If your Fraimic
account has more than one frame, note that the `Albums` sensor currently lists **every album in the
account**, not just the ones assigned to this specific frame -- there's no local way to determine
that mapping yet.

### Expect a delay after changing something on app.fraimic.com

Nothing here is instant. A change made on app.fraimic.com has to travel through two hops before it
shows up in Home Assistant:

1. The frame itself has to notice the change on its own periodic sync with Fraimic's cloud (usually
   within well under a minute).
2. Home Assistant then has to poll the frame again to pick that up locally -- every 5 minutes for
   most sensors (`Keep Awake`, `Voice Recording`, etc., all part of the same `/api/info` poll), or
   every 30 minutes for `Albums` specifically (deliberately slower and gated on the frame actually
   being reachable, since it's a cloud-proxied call).

So a setting you just flipped on app.fraimic.com can take a few minutes to show up here, and an
album edit can take up to half an hour. If you don't want to wait, call the built-in
`homeassistant.update_entity` action on the entity to force an immediate refresh.
