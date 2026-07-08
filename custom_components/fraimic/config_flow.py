"""Config flow for Fraimic E-Ink Canvas."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from aiohttp import ClientError

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
)
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

from . import api, discovery
from .api import normalize_host
from .const import (
    CONF_DEFAULT_DITHER,
    CONF_DEFAULT_FIT,
    CONF_DEVICE_ORIENTATION,
    CONF_HOST,
    DEFAULT_DEVICE_ORIENTATION,
    DEFAULT_DITHER,
    DEFAULT_FIT,
    DEVICE_ORIENTATIONS,
    DITHER_MODES,
    DOMAIN,
    FIT_MODES,
)
from .frame_types import device_model_name

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema({vol.Optional(CONF_HOST, default=""): str})


async def _validate_host(hass: HomeAssistant, host: str) -> dict[str, Any]:
    """Try /api/info against the given host, return the JSON payload."""
    session = async_get_clientsession(hass)
    return await api.get_info(session, host)


async def _try_validate(
    hass: HomeAssistant, host: str
) -> tuple[dict[str, Any] | None, dict[str, str]]:
    """Validate `host`, returning (info, errors) -- info is None on failure."""
    try:
        return await _validate_host(hass, host), {}
    except (ClientError, TimeoutError, HomeAssistantError):
        return None, {"base": "cannot_connect"}


def _device_key(info: dict[str, Any], fallback_host: str) -> str:
    """Prefer the frame's own hardware device_key as the unique id, so
    entity/device identity survives removing and re-adding the integration
    or the frame's IP address changing. Falls back to the host if a
    frame's firmware doesn't report a device_key.
    """
    key = (info.get("device") or {}).get("device_key")
    return key or fallback_host


def _model_of(info: dict[str, Any]) -> str:
    """The same model string shown on the device page, but derived from a
    discovery probe's result (see discovery.probe_frame) instead of a
    running coordinator -- so the model can be shown before the entry
    even exists yet."""
    return device_model_name((info.get("info_page") or {}).get("panel_size"))


class FraimicConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Fraimic E-Ink Canvas."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered_host: str = ""
        self._discovered_devices: list[dict[str, Any]] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            raw_host = user_input[CONF_HOST].strip()
            if not raw_host:
                self._discovered_devices = await discovery.scan_subnet(self.hass)
                if not self._discovered_devices:
                    errors["base"] = "no_devices_found"
                else:
                    return await self.async_step_pick_device()
            else:
                host = normalize_host(raw_host)
                info, errors = await _try_validate(self.hass, host)
                if info is not None:
                    await self.async_set_unique_id(_device_key(info, host))
                    # If this exact frame is already configured (e.g. someone
                    # re-runs "Add integration" after its IP changed), update
                    # the existing entry instead of erroring out.
                    self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                    title = f"Fraimic ({host.replace('http://', '').replace('https://', '')})"
                    return self.async_create_entry(title=title, data={CONF_HOST: host})

        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors)

    async def async_step_pick_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick one of the devices found by scan_subnet."""
        if user_input is not None:
            picked_ip = user_input[CONF_HOST]
            device = next(d for d in self._discovered_devices if d["ip"] == picked_ip)
            host = normalize_host(picked_ip)
            await self.async_set_unique_id(_device_key(device["info"], host))
            self._abort_if_unique_id_configured(updates={CONF_HOST: host})
            title = f"Fraimic ({picked_ip})"
            return self.async_create_entry(title=title, data={CONF_HOST: host})

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(
                                value=d["ip"], label=f"{d['ip']} — {_model_of(d['info'])}"
                            )
                            for d in self._discovered_devices
                        ]
                    )
                )
            }
        )
        return self.async_show_form(step_id="pick_device", data_schema=schema)

    async def async_step_dhcp(self, discovery_info: DhcpServiceInfo) -> ConfigFlowResult:
        """Handle a Fraimic frame observed via DHCP.

        Verified with a real /api/info probe before ever showing anything
        to the user -- our manifest's MAC-OUI matcher (Espressif) is too
        generic to trust alone. Also opportunistically self-heals an
        already-configured entry's stored host (including normalizing a
        previously fraimic.local-configured entry to a tracked IP)."""
        session = async_get_clientsession(self.hass)
        host = normalize_host(discovery_info.ip)
        info = await discovery.probe_frame(session, host)
        if info is None:
            return self.async_abort(reason="not_fraimic_device")

        await self.async_set_unique_id(_device_key(info, host))
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})

        self._discovered_host = host
        # Shows the detected model (instead of just "Fraimic") on the
        # discovery card under Settings > Devices & Services, before the
        # user ever opens the confirm form -- see strings.json's
        # config.flow_title, which reads this same placeholder.
        self.context["title_placeholders"] = {
            "ip": discovery_info.ip,
            "model": _model_of(info),
        }
        return self.async_show_form(
            step_id="dhcp_confirm",
            description_placeholders={"ip": discovery_info.ip, "model": _model_of(info)},
        )

    async def async_step_dhcp_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        title = f"Fraimic ({self._discovered_host.replace('http://', '')})"
        return self.async_create_entry(title=title, data={CONF_HOST: self._discovered_host})

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user change the frame's address without removing the integration."""
        errors: dict[str, str] = {}
        reconfigure_entry = self._get_reconfigure_entry()

        if user_input is not None:
            host = normalize_host(user_input[CONF_HOST])
            info, errors = await _try_validate(self.hass, host)
            if info is not None:
                await self.async_set_unique_id(_device_key(info, host))
                # Guard against accidentally pointing this entry at a
                # different physical frame.
                self._abort_if_unique_id_mismatch(reason="wrong_device")
                return self.async_update_reload_and_abort(
                    reconfigure_entry, data={CONF_HOST: host}
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {vol.Required(CONF_HOST, default=reconfigure_entry.data[CONF_HOST]): str}
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> FraimicOptionsFlow:
        return FraimicOptionsFlow()


class FraimicOptionsFlow(config_entries.OptionsFlow):
    """Defaults used when tapping an image in the media browser.

    The media browser just fires play_media with no way to pass extra
    parameters, unlike the fraimic.send_image service (which always lets
    you override fit/dither per call regardless of these defaults).
    `device_orientation` is the one exception -- it's a fact about how
    the frame is physically mounted, not something that varies per image,
    so it's Options-only (not a send_image field).
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_DEVICE_ORIENTATION,
                    default=current.get(CONF_DEVICE_ORIENTATION, DEFAULT_DEVICE_ORIENTATION),
                ): SelectSelector(SelectSelectorConfig(options=list(DEVICE_ORIENTATIONS))),
                vol.Optional(
                    CONF_DEFAULT_FIT, default=current.get(CONF_DEFAULT_FIT, DEFAULT_FIT)
                ): SelectSelector(SelectSelectorConfig(options=list(FIT_MODES))),
                vol.Optional(
                    CONF_DEFAULT_DITHER, default=current.get(CONF_DEFAULT_DITHER, DEFAULT_DITHER)
                ): SelectSelector(SelectSelectorConfig(options=list(DITHER_MODES))),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
