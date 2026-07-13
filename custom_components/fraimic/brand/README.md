# Brand assets

Since Home Assistant 2026.3, this local folder is the current standard —
see the [Brands Proxy API
announcement](https://developers.home-assistant.io/blog/2026/02/24/brands-proxy-api/).
HA core serves these files directly (via
`/api/brands/integration/{domain}/{image}`, with local images taking
priority over the CDN) for both its own UI and HACS — no submission to the
[home-assistant/brands](https://github.com/home-assistant/brands) repo is
needed (its `custom_integrations/` folder is now explicitly called
"legacy").

## Expected files

| File | Format | Required |
|---|---|---|
| `icon.png` | 256×256, transparent background | Yes |
| `icon@2x.png` | 512×512 | Recommended |
| `logo.png` | square or wide, transparent background | Optional |
| `logo@2x.png` | 2x of logo.png | Optional, only together with logo.png |
| `dark_icon.png` / `dark_logo.png` | same dimensions as their light variant | Optional, for dark mode |

`icon.png`/`icon@2x.png` are present — the required/recommended minimum is
met. `logo.png`/`logo@2x.png` are missing; optional, but a nice addition if
real Fraimic logo artwork is available (not something to fabricate).

See also `../quality_scale.yaml` (the `brands` rule).
