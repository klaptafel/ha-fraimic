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
from typing import Any, Awaitable, Callable

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
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.network import get_url
from homeassistant.util import dt as dt_util

from . import api
from .const import (
    CONF_DEFAULT_DITHER,
    CONF_DEFAULT_FIT,
    CONF_DEVICE_ORIENTATION,
    DEFAULT_DEVICE_ORIENTATION,
    DEFAULT_DITHER,
    DEFAULT_DRY_RUN,
    DEFAULT_FIT,
    DEFAULT_TIMEOUT,
    DOMAIN,
)
from .entity import FraimicEntity
from .frame_types import frame_type_for_size, panel_size_from_info
from .image_converter import convert_image
from .runtime_data import (
    FraimicConfigEntry,
    FraimicRuntimeData,
    resend_requested_signal,
    send_status_signal,
)

_LOGGER = logging.getLogger(__name__)

# Deliberately 0 (no HA-managed queueing): _busy_lock is what enforces
# "one conversion+upload at a time", and it does so by *rejecting* a
# second call immediately with a visible "already_busy" error, not by
# making the caller wait. PARALLEL_UPDATES=1 would instead have HA queue
# the second call behind a semaphore -- it would silently run for real
# once the first finishes, exactly the silent-backlog behavior the
# busy-lock check exists to prevent (see the comment in _convert_and_send).
PARALLEL_UPDATES = 0

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

    # The fraimic.send_image *service* itself is registered once, from
    # __init__.py's async_setup_entry, via service.async_register_platform_entity_service
    # -- not here. That's the current recommended pattern for platform
    # entity services (this used to be EntityPlatform.async_register_entity_service,
    # called per-platform-setup like this function; see
    # https://developers.home-assistant.io/blog/2025/09/25/entity-services-api-changes).


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
        self._resend_requested_signal = resend_requested_signal(entry)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # See resend_scheduler.py's own module docstring for why this
        # exists: fired shortly after the frame's own scheduled refresh,
        # asking this entity to re-upload the last image verbatim. Handled
        # here (not inside the scheduler itself) so the busy_lock/send-
        # status bookkeeping this already needs stays in one place.
        # _resend_last_image is a coroutine function, so the dispatcher
        # itself schedules it as a task -- no separate wrapper needed here.
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self._resend_requested_signal, self._resend_last_image
            )
        )

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
        # async_create_BACKGROUND_task, not the plain entry.async_create_task
        # this used to call (found live, 2026-08-17): confirmed against
        # config_entries.py's own real source -- a plain async_create_task is
        # only ever *waited on* (10s) during unload, never actually
        # cancelled; only a background task gets task.cancel() called on it
        # first. _convert_and_send's own wake-wait retry loop can run for up
        # to WAKE_WAIT_TIMEOUT (10 minutes), so any unload/reload landing
        # inside that window used to always log "Task ... did not complete
        # in time", every single time, since nothing ever told it to stop.
        self._entry.async_create_background_task(
            self.hass,
            self._convert_and_send(raw_bytes, fit, dither, dry_run=dry_run, source=source),
            name="fraimic_convert_and_send",
        )

    async def _convert_and_send(
        self, raw_bytes: bytes, fit: str, dither: str, dry_run: bool = False, source: str = ""
    ) -> None:
        """Runs as a background task -- _busy_lock is already held by the
        caller (_queue_send) and is released here (via _run_tracked_send),
        regardless of outcome."""

        async def _do_convert_and_send() -> None:
            # device_orientation isn't caller-supplied -- it's a fact about
            # how the frame is physically mounted, not something that varies
            # per image, so it always comes straight from Options rather than
            # being threaded through both async_play_media and
            # async_send_local_file.
            device_orientation = self._entry.options.get(
                CONF_DEVICE_ORIENTATION, DEFAULT_DEVICE_ORIENTATION
            )
            # Detected via the frame's own /info admin page (api.get_info_page,
            # merged into the main coordinator's data) -- falls back to the
            # 13.3" default if that best-effort scrape hasn't succeeded yet.
            panel_size = panel_size_from_info(self._runtime.coordinator.data or {})
            frame_type = frame_type_for_size(panel_size)

            bin_data, preview_png = await self.hass.async_add_executor_job(
                lambda: convert_image(
                    raw_bytes,
                    fit=fit,
                    device_orientation=device_orientation,
                    dither=dither,
                    width=frame_type.width,
                    height=frame_type.height,
                )
            )
            # Recorded only for a real upload, never a dry run -- a dry run
            # never actually reaches the frame, so marking it "confirmed
            # synced" as of the current next_refresh would be wrong: see
            # resend_guard.py's own detection logic, which trusts this
            # value to mean the frame genuinely has this exact image.
            next_refresh = None
            if dry_run:
                _LOGGER.debug("dry_run=True: skipping upload to %s", self._runtime.base_url)
            else:
                await self._upload_waiting_for_frame(bin_data)
                next_refresh = ((self._runtime.coordinator.data or {}).get("display") or {}).get(
                    "next_refresh"
                )
            await self._runtime.image_store.async_set(preview_png, bin_data, next_refresh)

        await self._run_tracked_send(_display_name(source), _do_convert_and_send)

    async def _resend_last_image(self, next_refresh: str) -> None:
        """Re-uploads the exact bytes last sent, verbatim -- no
        reconversion (see image_store.py's own bin_content comment).
        Triggered by resend_guard.py's own dispatcher signal once it sees
        the frame's own next_refresh has moved on since this exact image
        was last confirmed sent: the frame wakes and redraws on its own
        schedule, independent of what Home Assistant last pushed, so a
        frame with no active cloud album goes black at that point even
        though the last real send is still perfectly valid -- see
        resend_guard.py's own module docstring for the full story.
        `next_refresh` is the value resend_guard.py detected the change
        against; recorded via image_store.async_set once this resend
        actually succeeds, so a later poll seeing that same value again
        doesn't trigger another one for nothing.

        Silently skipped (not surfaced as an error anywhere -- this is a
        background action, not something a person is waiting on) whenever
        nothing has ever been sent yet, or a real send/another resend is
        already in flight; resend_guard.py will simply notice again on a
        later poll if this one gets skipped.

        Already runs as its own background task -- the dispatcher itself
        schedules this (a coroutine function) via hass.async_create_task
        when resend_guard.py's signal fires (see async_added_to_hass) --
        so this awaits _run_tracked_send directly, unlike _queue_send's
        own synchronous-acquire-then-dispatch two-step, which exists
        specifically to let a *caller* (a service call/media browser tap)
        return immediately instead of blocking on the send.
        """
        image_store = self._runtime.image_store
        bin_data = image_store.bin_content
        if bin_data is None or self._busy_lock.locked():
            return
        await self._busy_lock.acquire()

        async def _do_resend() -> None:
            await self._upload_waiting_for_frame(bin_data)
            await image_store.async_set(image_store.content, bin_data, next_refresh)

        await self._run_tracked_send("scheduled resend", _do_resend, background=True)

    async def _run_tracked_send(
        self, display_name: str, action: Callable[[], Awaitable[None]], *, background: bool = False
    ) -> None:
        """Runs `action` (the actual conversion+upload, or just a verbatim
        re-upload) with the send-status bookkeeping, busy_lock release, and
        error handling shared by every send path -- factored out
        (2026-08-07) so a scheduled resend doesn't have to duplicate this
        block's five different exception cases just to reuse one of them
        (ClientError/TimeoutError, meaning the frame never woke up).

        `background=True` (used by the scheduled resend) still logs a
        failure, but does NOT set status.send_failed -- direct user
        feedback, 2026-08-07: a missed automatic resend (the frame's own
        wake window after next_refresh can be short, and the configured
        delay might not land inside it) isn't something to act on, since
        resend_scheduler.py will simply try again at the frame's own next
        scheduled refresh regardless. Leaving "Frame never woke up, gave
        up: scheduled resend" lingering as the visible Send Status until
        something else overwrites it would misrepresent a normal, self-
        correcting background retry as a real problem -- that message is
        reserved for a real, user-initiated send failing, where it's the
        only feedback channel this integration has (see
        FraimicSendStatus's own docstring)."""
        # status is read in the except/finally blocks below, so it's
        # assigned before the try (not inside it) -- otherwise a failure in
        # `action`'s own setup code, before this line runs, would leave
        # `status` unbound and turn the finally block's cleanup itself into
        # an UnboundLocalError instead of releasing _busy_lock.
        status = self._runtime.send_status
        try:
            self._attr_state = MediaPlayerState.BUFFERING
            status.sending = display_name
            status.send_failed = None
            self._notify_send_status_changed()
            await action()
        except (ClientError, TimeoutError):
            _LOGGER.warning(
                "Fraimic frame at %s never woke up within %s -- gave up sending %s",
                self._runtime.base_url,
                WAKE_WAIT_TIMEOUT,
                status.sending,
            )
            if not background:
                status.send_failed = status.sending
        except HomeAssistantError as err:
            _LOGGER.warning("Fraimic failed to send %s: %s", status.sending, err)
            if not background:
                status.send_failed = status.sending
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Fraimic: unexpected error sending %s", status.sending)
            if not background:
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
        async with asyncio.timeout(DEFAULT_TIMEOUT):
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
