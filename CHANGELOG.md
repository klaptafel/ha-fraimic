# Changelog

All notable changes to this project are documented here. Format loosely follows [Keep a Changelog](https://keepachangelog.com/). Versions before 1.0.0 are not retroactively documented. See git history / GitHub releases for those.

## [Unreleased]

### Fixed
- `media_player.py`'s `_convert_and_send` could permanently leak `_busy_lock` if an exception occurred in its setup code (device-orientation/panel-size lookup) before the original `try` block began -- every later send would then fail with "already busy" until the integration was reloaded. Fixed by widening the `try`/`finally` to cover that setup code too.

### Changed
- `api.py`'s `get_info_page` now scrapes the `/info` admin page's label/value rows in a single pass (`_info_page_values`) instead of a separate `re.search` per field; verified against the existing `test_api.py` fixtures (including the badge-wrapped "Registration" row, still correctly ignored). No behavior change.
- Device-identity fields (`identifiers`/`name`/`manufacturer`/`configuration_url`) shared by `__init__.py`'s `device_reg.async_get_or_create()` and `entity.py`'s `device_info()` consolidated into one `device_identity_base()` helper, so the two can no longer drift apart. No behavior change.
- `image_converter.py`'s per-pixel palette-index mapping and 4-bit bit-packing (previously pure-Python loops over every pixel) now use numpy, new dependency. ~15-35x faster for that part of the conversion (measured: 13.3" panel ~0.3s -> ~0.02s, 31.5" panel ~0.6s -> ~0.03s), verified byte-for-byte identical output against the old implementation across every dither mode, both panel sizes, and the defensive "unexpected color" fallback path. The dithering step itself (the `epaper-dithering` Rust extension) is unchanged.
- `requirements.txt`'s `epaper-dithering` floor bumped to `>=5.0.9` to match `manifest.json` (had drifted out of sync from a previous Dependabot bump that was only applied to one of the two files).
- The duplicated dict-walking loop in `binary_sensor.py`'s `FraimicInfoBinarySensor` and `sensor.py`'s `FraimicInfoSensor` consolidated into `entity.py`'s shared `dig_path()` helper. No behavior change.
- The duplicated "model name from a raw info dict" logic in `config_flow.py` (`_model_of`) and `media_player.py` consolidated into `frame_types.py`'s shared `device_model_from_info()`/`panel_size_from_info()` helpers. No behavior change.
- Remaining em-dashes in `quality_scale.yaml`'s comments replaced with plain punctuation, matching the rest of this HACS collection's writing style.

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
