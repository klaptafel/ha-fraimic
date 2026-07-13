# Changelog

All notable changes to this project are documented here. Format loosely follows [Keep a Changelog](https://keepachangelog.com/). Versions before 1.0.0 are not retroactively documented. See git history / GitHub releases for those.

## [Unreleased]

You can now search by file name right from the frame's media browser, instead of only browsing folder by folder. Behind the scenes, the integration now logs when the frame's reachability actually changes rather than on every missed check while it's asleep, and raises a self-healing notification if album syncing keeps failing while the frame is clearly awake. The README also gained a proper explanation of how updates work, plus troubleshooting and known-limitations sections.

### Changed
- `send_image` service registration modernized from the deprecated `EntityPlatform.async_register_entity_service()` to `homeassistant.helpers.service.async_register_platform_entity_service()`, registered once in `__init__.py` instead of per-platform in `media_player.py`.
- Minimum supported Home Assistant version raised from 2024.6.0 to 2025.5.0 (`hacs.json`), the release that added the `SEARCH_MEDIA` media player feature used below. No installs are known to depend on the old floor yet, so this was a clean bump rather than a defensive compatibility shim. Note that the underlying `media_source.async_search_media` helper is newer still and isn't in any stable release at the time of writing; until it ships, searching raises a clear error instead of a raw `AttributeError`.

### Added
- README: badges, a Features section (listing entities/services), Supported devices, Removal, "How updates work" (polling model + deep-sleep-tolerant availability explained), an example automation, Troubleshooting, and Known limitations sections, brought in line with this HACS collection's other projects' README structure.
- Logging when a coordinator's `device_reachable` actually flips (not on every missed poll: only the real transition, keeping the sleep-tolerant model quiet during expected deep-sleep gaps).
- A self-healing repair issue when album sync keeps failing while the main frame is demonstrably reachable, deliberately gated on the *main* coordinator's `device_reachable`, not the albums coordinator's own, so ordinary sleep gaps can never trigger it.
- `GITHUB_TOKEN` env on the HACS validation CI step, `dependabot.yml` for GitHub Actions updates.
- New coordinator tests covering the reachability-transition logging and the repair-issue create/clear behavior.
- `brand/README.md` documenting the current (2026.3+) local brand-assets standard.
- The `Display` media player's browse dialog can now search by file name (`MediaPlayerEntityFeature.SEARCH_MEDIA` + `async_search_media`), restricted to folders and images the same way browsing already is.

### Quality Scale
- Self-assessed against Home Assistant's Integration Quality Scale: **46 done / 5 exempt / 0 todo** (up from 43/5/3). `brands` reclassified from todo to done: since HA 2026.3 (Brands Proxy API) a local `brand/` folder is the current standard, not a `home-assistant/brands` submission: the existing `icon.png`/`icon@2x.png` already meet the required/recommended minimum (the optional `logo.png`/`logo@2x.png` are still missing, but need real Fraimic logo artwork to add, not something to fabricate).
