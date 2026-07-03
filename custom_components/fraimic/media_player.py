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

import async_timeout
import voluptuous as vol

from homeassistant.components import media_source
from homeassistant.components.media_player import (
    BrowseMedia,
    MediaClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.network import get_url

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
from .runtime_data import FraimicConfigEntry, FraimicRuntimeData

_LOGGER = logging.getLogger(__name__)

# Deliberately 0 (no HA-managed queueing): _busy_lock is what enforces
# "one conversion+upload at a time", and it does so by *rejecting* a
# second call immediately with a visible "already_busy" error, not by
# making the caller wait. PARALLEL_UPDATES=1 would instead have HA queue
# the second call behind a semaphore -- it would silently run for real
# once the first finishes, exactly the silent-backlog behavior the
# busy-lock check exists to prevent (see the comment in _convert_and_send).
PARALLEL_UPDATES = 0

SEND_IMAGE_SCHEMA = {
    vol.Required(ATTR_PATH): cv.string,
    vol.Optional(ATTR_FIT, default=DEFAULT_FIT): vol.In(FIT_MODES),
    vol.Optional(ATTR_DITHER, default=DEFAULT_DITHER): vol.In(DITHER_MODES),
    vol.Optional(ATTR_DRY_RUN, default=DEFAULT_DRY_RUN): cv.boolean,
}

_MEDIA_SOURCE_PREFIX = "media-source://media_source/"


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

    def __init__(self, runtime: FraimicRuntimeData, entry: ConfigEntry) -> None:
        super().__init__(runtime.coordinator, entry, "display")
        self._runtime = runtime
        self._entry = entry
        self._busy_lock = asyncio.Lock()
        self._attr_state = MediaPlayerState.IDLE
        self._sending: str | None = None

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
        # While a conversion+upload is in progress, reflects that instead
        # of the last-sent timestamp -- the only free (no extra
        # subsystems, no dependency on the user having a notification
        # panel open) confirmation that a tap actually landed, since a
        # real toast isn't something a backend-only integration can
        # trigger outside of service-call failures.
        if self._sending is not None:
            return f"Sending {self._sending}…"
        updated_at = self._runtime.image_store.updated_at
        if updated_at is None:
            return None
        return f"Sent {updated_at.strftime('%Y-%m-%d %H:%M')}"

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

    async def async_play_media(self, media_type: str, media_id: str, **kwargs) -> None:
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
        await self._convert_and_send(raw_bytes, fit, dither, source=media_id)

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
        await self._convert_and_send(raw_bytes, fit, dither, dry_run=dry_run, source=path)

    async def _convert_and_send(
        self, raw_bytes: bytes, fit: str, dither: str, dry_run: bool = False, source: str = ""
    ) -> None:
        # device_orientation isn't caller-supplied -- it's a fact about
        # how the frame is physically mounted, not something that varies
        # per image, so it always comes straight from Options rather than
        # being threaded through both async_play_media and
        # async_send_local_file.
        device_orientation = self._entry.options.get(
            CONF_DEVICE_ORIENTATION, DEFAULT_DEVICE_ORIENTATION
        )

        if self._busy_lock.locked():
            # Surfaces as a visible error toast in the UI -- important,
            # since otherwise repeated taps while a conversion+upload is
            # already running (which takes several seconds, longer with
            # atkinson dithering) silently pile up into a backlog with no
            # feedback that anything happened.
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="already_busy"
            )
        async with self._busy_lock:
            self._attr_state = MediaPlayerState.BUFFERING
            self._sending = _display_name(source)
            self.async_write_ha_state()
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
                    session = async_get_clientsession(self.hass)
                    await api.upload_image(session, self._runtime.base_url, bin_data)
                await self._runtime.image_store.async_set(preview_png)
            finally:
                self._sending = None
                self._attr_state = MediaPlayerState.IDLE
                self.async_write_ha_state()

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
