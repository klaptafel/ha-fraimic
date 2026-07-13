# Changelog

All notable changes to this project are documented here. Format loosely follows [Keep a Changelog](https://keepachangelog.com/). Versions before 1.0.0 are not retroactively documented. See git history / GitHub releases for those.

## [1.1.0] - 2026-07-13

Raises a self-healing notification if album syncing keeps failing while the frame is clearly awake, and now only logs when the frame's reachability actually changes rather than on every missed check while it's asleep. The README also gained a proper explanation of how updates work, plus troubleshooting and known-limitations sections. Also includes a precautionary fix for a bug that could have stopped images from sending once Home Assistant itself moves to a newer Python version.

### Added
- README: badges, a Features section (listing entities/services), Supported devices, Removal, "How updates work" (polling model + deep-sleep-tolerant availability explained), an example automation, Troubleshooting, and Known limitations sections, brought in line with this HACS collection's other projects' README structure.
- Logging when a coordinator's `device_reachable` actually flips (not on every missed poll: only the real transition, keeping the sleep-tolerant model quiet during expected deep-sleep gaps).
- A self-healing repair issue when album sync keeps failing while the main frame is demonstrably reachable, deliberately gated on the *main* coordinator's `device_reachable`, not the albums coordinator's own, so ordinary sleep gaps can never trigger it.
- `GITHUB_TOKEN` env on the HACS validation CI step, `dependabot.yml` for GitHub Actions updates.
- New coordinator tests covering the reachability-transition logging and the repair-issue create/clear behavior.
- `brand/README.md` documenting the current (2026.3+) local brand-assets standard.

### Changed
- `send_image` service registration modernized from the deprecated `EntityPlatform.async_register_entity_service()` to `homeassistant.helpers.service.async_register_platform_entity_service()`, registered once in `__init__.py` instead of per-platform in `media_player.py`.
- Minimum supported Home Assistant version raised from 2024.6.0 to 2025.5.0 (`hacs.json`). No installs are known to depend on the old floor.
- CI now runs on Python 3.14, matching Home Assistant core's own current floor, instead of the already-outdated 3.13.

### Fixed
- Replaced the third-party `async_timeout` package with the stdlib `asyncio.timeout` (available since Python 3.11, which this project already requires well above). `async_timeout` was never actually in this project's own requirements; it relied entirely on another dependency pulling it in transitively, which stopped happening once the environment moved to Python 3.14 (the version Home Assistant core itself now requires), breaking every timeout-guarded request with a `ModuleNotFoundError`. This affected the already-released 1.0.0 too, on any install that upgrades its Python runtime to 3.14 to match current Home Assistant core.

### Quality Scale
- Self-assessed against Home Assistant's Integration Quality Scale: **46 done / 5 exempt / 0 todo** (up from 43/5/3). `brands` reclassified from todo to done: since HA 2026.3 (Brands Proxy API) a local `brand/` folder is the current standard, not a `home-assistant/brands` submission: the existing `icon.png`/`icon@2x.png` already meet the required/recommended minimum (the optional `logo.png`/`logo@2x.png` are still missing, but need real Fraimic logo artwork to add, not something to fabricate).
