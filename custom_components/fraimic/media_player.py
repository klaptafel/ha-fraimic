"""Media player entity for Fraimic E-Ink Canvas.

Lets you pick an image through Home Assistant's media browser (Local
Media, camera snapshots, any media source that serves images) and send
it straight to the frame -- converting to the Spectra 6 .bin format
in-process. Shows the last-sent image as the entity's picture (the same
mechanism music players use for album art), instead of a separate image
entity.

Also exposes an entity service `fraimic.send_image` for pushing a file
from disk directly (e.g. from an automation), bypassing the browser.
"""
from __future__ import annotations

import asyncio
import logging
import os
import urllib.parse
from datetime import timedelta
from typing import Any

import async_timeout
import voluptuous as vol
from aiohttp import ClientError

from homeassistant.components import media_source
from homeassistant.components.media_player import MediaPlayerEntity
from homeassistant.components.media_player.browse_media import BrowseMedia
from homeassistant.components.media_player.const import (
    MediaClass,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.network import get_url
from homeassistant.util import dt as dt_util

from . import api
from .const import (
    ATTR_DITHER,
    ATTR_DRY_RUN,
    ATTR_FIT,
    ATTR_PATH,
    CONF_DEFAULT_DITHER,
    CONF_DEFAULT_FIT,
    CONF_DEVICE_ORIENTATION,
    DEFAULT_DEVICE_ORIENTATION,
    DEFAULT_DITHER,
    DEFAULT_DRY_RUN,
    DEFAULT_FIT,
    DEFAULT_TIMEOUT,
    DITHER_MODES,
    DOMAIN,
    FIT_MODES,
    SERVICE_SEND_IMAGE,
)
from .entity import FraimicEntity
from .image_converter import convert_image
from .runtime_data import FraimicConfigEntry, FraimicRuntimeData, send_status_signal

_LOGGER = logging.getLogger(__name__)

# Deliberately 0 (no HA-managed queueing): _busy_lock is what enforces
# "one conversion+upload at a time", and it does so by *rejecting* a
# second call immediately with a visible "already_busy" error, not by
# making the caller wait. PARALLEL_UPDATES=1 would instead have HA queue
# the second call behind a semaphore -- it would silently run for real
# once the first finishes, exactly the silent-backlog behavior the
# busy-lock check exists to prevent (see the comment in _convert_and_send).
PARALLEL_UPDATES = 0

# Deliberately a plain dict, not vol.Schema(...) -- async_register_entity_service
# inspects the schema's actual shape at runtime and rejects anything that
# isn't a raw field dict as "a non entity service schema" (it merges in the
# standard entity-service fields itself). The dict-vs-Any typing mismatch
# this causes is a stub limitation, not a real type error.
SEND_IMAGE_SCHEMA: dict[str | vol.Marker, Any] = {
    vol.Required(ATTR_PATH): cv.string,
    vol.Optional(ATTR_FIT, default=DEFAULT_FIT): vol.In(FIT_MODES),
    vol.Optional(ATTR_DITHER, default=DEFAULT_DITHER): vol.In(DITHER_MODES),
    vol.Optional(ATTR_DRY_RUN, default=DEFAULT_DRY_RUN): cv.boolean,
}

_MEDIA_SOURCE_PREFIX = "media-source://media_source/"

# The frame only wakes on its own schedule or a physical tap -- never on
# an incoming request -- so a failed upload while it's asleep can't be
# fixed by retrying quickly (see api.upload_image's own short retry for
# that). Instead, if it's asleep *right now*, keep retrying at this
# interval until it happens to wake on its own, up to this total budget,
# before giving up.
WAKE_WAIT_TIMEOUT = timedelta(minutes=10)
WAKE_WAIT_INTERVAL = 30


def _display_name(source: str) -> str:
    """Best-effort human-readable name for a path/URL/media-source id, for
    the "sending..." notification -- doesn't need to be exact."""
    return urllib.parse.unquote(source.rstrip("/").rsplit("/", 1)[-1]) or source


async def async_setup_entry(
    hass: HomeAssistant, entry: FraimicConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    runtime = entry.runtime_data
    async_add_entities([FraimicMediaPlayer(runtime, entry)])

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_SEND_IMAGE, SEND_IMAGE_SCHEMA, "async_send_local_file"
    )


class FraimicMediaPlayer(FraimicEntity, MediaPlayerEntity):
    """Represents the frame as a media player you can push images to."""

    _attr_supported_features = (
        MediaPlayerEntityFeature.BROWSE_MEDIA | MediaPlayerEntityFeature.PLAY_MEDIA
    )
    _attr_media_content_type = MediaType.IMAGE
    _attr_translation_key = "display"
    # An unavailable media player can't be browsed or played to from the
    # UI -- that would block picking an image for the entire time the
    # frame happens to be asleep, defeating _upload_waiting_for_frame's
    # whole point of letting you queue a send and have it wait out the
    # sleep itself. See FraimicEntity for the general reasoning.
    _fraimic_always_available = True

    def __init__(self, runtime: FraimicRuntimeData, entry: ConfigEntry) -> None:
        super().__init__(runtime.coordinator, entry, "display")
        self._runtime = runtime
        self._busy_lock = asyncio.Lock()
        self._attr_state = MediaPlayerState.IDLE
        self._send_status_signal = send_status_signal(entry)

    # -- "album art" for the last image sent, shown as entity_picture --

    @property
    def media_image_hash(self) -> str | None:
        updated_at = self._runtime.image_store.updated_at
        return str(updated_at.timestamp()) if updated_at else None

    async def async_get_media_image(self) -> tuple[bytes | None, str | None]:
        content = self._runtime.image_store.content
        if content is None:
            return None, None
        return content, "image/png"

    @property
    def media_title(self) -> str | None:
        # Delegates to FraimicRuntimeData.status_text -- shared with
        # sensor.py's FraimicStatusSensor so the logic lives in exactly
        # one place, not duplicated per-entity.
        return self._runtime.status_text

    # -- browsing and playback --

    async def async_browse_media(
        self, media_content_type: str | None = None, media_content_id: str | None = None
    ) -> BrowseMedia:
        return await media_source.async_browse_media(
            self.hass,
            media_content_id,
            content_filter=lambda item: (
                item.media_class == MediaClass.DIRECTORY
                or (item.media_content_type or "").startswith("image/")
            ),
        )

    async def async_play_media(self, media_type: str, media_id: str, **kwargs: Any) -> None:
        """Called when the user taps an image in the media browser.

        Uses the fit/dither set in the integration's Options (Configure),
        since the media browser itself has no way to pass extra
        parameters per tap -- use the fraimic.send_image service instead
        if you need per-call control.
        """
        if media_source.is_media_source_id(media_id):
            raw_bytes = await self._read_media_source(media_id)
        elif media_id.startswith(("http://", "https://")):
            raw_bytes = await self._fetch_url(media_id)
        else:
            raw_bytes = await self.hass.async_add_executor_job(self._read_local_file, media_id)

        fit = self._entry.options.get(CONF_DEFAULT_FIT, DEFAULT_FIT)
        dither = self._entry.options.get(CONF_DEFAULT_DITHER, DEFAULT_DITHER)
        await self._queue_send(raw_bytes, fit, dither, source=media_id)

    async def async_send_local_file(
        self,
        path: str,
        fit: str = DEFAULT_FIT,
        dither: str = DEFAULT_DITHER,
        dry_run: bool = DEFAULT_DRY_RUN,
    ) -> None:
        """Entity service: push a file already on disk (e.g. /config/www/...).

        dry_run=True converts and updates the entity_picture like normal,
        but skips the actual upload to the frame -- handy for previewing
        fit/dither/device_orientation results without waiting through the
        frame's 20-30s refresh cycle each time.
        """
        if not self.hass.config.is_allowed_path(path):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="path_not_allowed",
                translation_placeholders={"path": path},
            )
        raw_bytes = await self.hass.async_add_executor_job(self._read_local_file, path)
        await self._queue_send(raw_bytes, fit, dither, dry_run=dry_run, source=path)

    async def _queue_send(
        self, raw_bytes: bytes, fit: str, dither: str, dry_run: bool = False, source: str = ""
    ) -> None:
        """Reject immediately if already busy, otherwise hand off to a
        background task and return right away.

        The frame is often asleep when a photo is picked -- waiting for it
        to wake (see _upload_waiting_for_frame) can take minutes, and
        blocking the triggering service call/media browser tap for that
        long would be a worse experience than a queued background send.
        """
        if self._busy_lock.locked():
            # Surfaces as a visible error toast in the UI -- important,
            # since otherwise repeated taps while a conversion+upload is
            # already running (which takes several seconds, longer with
            # atkinson dithering, or minutes if it's waiting for the frame
            # to wake) silently pile up into a backlog with no feedback
            # that anything happened.
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="already_busy"
            )
        await self._busy_lock.acquire()
        # Tracked on the config entry (not a bare hass.async_create_task) so
        # HA waits for/cancels it on unload instead of leaving it dangling.
        self._entry.async_create_task(
            self.hass,
            self._convert_and_send(raw_bytes, fit, dither, dry_run=dry_run, source=source),
            name="fraimic_convert_and_send",
        )

    async def _convert_and_send(
        self, raw_bytes: bytes, fit: str, dither: str, dry_run: bool = False, source: str = ""
    ) -> None:
        """Runs as a background task -- _busy_lock is already held by the
        caller (_queue_send) and is released here, regardless of outcome."""
        # device_orientation isn't caller-supplied -- it's a fact about
        # how the frame is physically mounted, not something that varies
        # per image, so it always comes straight from Options rather than
        # being threaded through both async_play_media and
        # async_send_local_file.
        device_orientation = self._entry.options.get(
            CONF_DEVICE_ORIENTATION, DEFAULT_DEVICE_ORIENTATION
        )

        status = self._runtime.send_status
        self._attr_state = MediaPlayerState.BUFFERING
        status.sending = _display_name(source)
        status.send_failed = None
        self._notify_send_status_changed()
        try:
            bin_data, preview_png = await self.hass.async_add_executor_job(
                lambda: convert_image(
                    raw_bytes,
                    fit=fit,
                    device_orientation=device_orientation,
                    dither=dither,
                )
            )
            if dry_run:
                _LOGGER.debug("dry_run=True: skipping upload to %s", self._runtime.base_url)
            else:
                await self._upload_waiting_for_frame(bin_data)
            await self._runtime.image_store.async_set(preview_png)
        except (ClientError, TimeoutError):
            _LOGGER.warning(
                "Fraimic frame at %s never woke up within %s -- gave up sending %s",
                self._runtime.base_url,
                WAKE_WAIT_TIMEOUT,
                status.sending,
            )
            status.send_failed = status.sending
        except HomeAssistantError as err:
            _LOGGER.warning("Fraimic failed to send %s: %s", status.sending, err)
            status.send_failed = status.sending
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Fraimic: unexpected error sending %s", status.sending)
            status.send_failed = status.sending
        finally:
            status.sending = None
            status.waiting_for_wake = False
            self._attr_state = MediaPlayerState.IDLE
            self._busy_lock.release()
            self._notify_send_status_changed()

    def _notify_send_status_changed(self) -> None:
        """Write this entity's own state, and poke any other entity (e.g.
        sensor.py's FraimicStatusSensor) reflecting the same
        FraimicRuntimeData.status_text, since a plain
        self.async_write_ha_state() only refreshes this entity."""
        self.async_write_ha_state()
        async_dispatcher_send(self.hass, self._send_status_signal)

    async def _upload_waiting_for_frame(self, bin_data: bytes) -> None:
        """Upload, retrying on connection failures until the frame wakes up
        on its own (a tap or its own refresh schedule -- never an incoming
        request) or WAKE_WAIT_TIMEOUT elapses, whichever comes first."""
        status = self._runtime.send_status
        session = async_get_clientsession(self.hass)
        deadline = dt_util.utcnow() + WAKE_WAIT_TIMEOUT
        while True:
            try:
                await api.upload_image(session, self._runtime.base_url, bin_data)
                status.waiting_for_wake = False
                return
            except (ClientError, TimeoutError):
                if dt_util.utcnow() >= deadline:
                    raise
                status.waiting_for_wake = True
                self._notify_send_status_changed()
                await asyncio.sleep(WAKE_WAIT_INTERVAL)

    # -- fetching media bytes --

    async def _read_media_source(self, media_content_id: str) -> bytes:
        """Read a media_source:// item.

        For local files (Local Media / configured media_dirs), reads
        directly from disk -- sidestepping HA's HTTP layer entirely, which
        avoids 401/403 errors from fetching signed/authenticated media
        URLs (a known rough edge for backend-to-backend fetches; see
        community reports on /media/local/... requiring an access token).
        For anything else (other media source providers, e.g. cameras),
        falls back to resolving + fetching the URL on a best-effort basis.
        """
        try:
            return await self._read_local_media_dir_file(media_content_id)
        except (LookupError, ValueError, OSError) as err:
            _LOGGER.debug(
                "Not a plain local media_dirs file (%s), falling back to URL fetch", err
            )
        play_item = await media_source.async_resolve_media(self.hass, media_content_id, self.entity_id)
        return await self._fetch_url(play_item.url)

    async def _read_local_media_dir_file(self, media_content_id: str) -> bytes:
        if not media_content_id.startswith(_MEDIA_SOURCE_PREFIX):
            raise ValueError("not a media_source:// id")

        remainder = media_content_id[len(_MEDIA_SOURCE_PREFIX):]
        media_dir_id, _, relative_path = remainder.partition("/")
        media_dir_id = media_dir_id or "local"
        relative_path = urllib.parse.unquote(relative_path)

        media_dirs = self.hass.config.media_dirs
        base_path = media_dirs.get(media_dir_id)
        if not base_path:
            raise LookupError(f"Unknown media_dir '{media_dir_id}'")

        full_path = os.path.join(base_path, relative_path)
        return await self.hass.async_add_executor_job(self._read_local_file, full_path)

    async def _fetch_url(self, url: str) -> bytes:
        if url.startswith("/"):
            base = get_url(self.hass, allow_internal=True, allow_external=False)
            url = f"{base}{url}"
        session = async_get_clientsession(self.hass)
        async with async_timeout.timeout(DEFAULT_TIMEOUT):
            async with session.get(url) as resp:
                resp.raise_for_status()
                return await resp.read()

    @staticmethod
    def _read_local_file(path: str) -> bytes:
        if not os.path.isfile(path):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="file_not_found",
                translation_placeholders={"path": path},
            )
        with open(path, "rb") as file:
            return file.read()
