"""Config flow for the TeddyCloud integration."""
from __future__ import annotations

import logging

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers import selector

from .api import TeddyCloudApiClient, TeddyCloudApiError, build_base_url
from .const import (
    CONF_GITHUB_BRANCH,
    CONF_GITHUB_CHECK_INTERVAL,
    CONF_GITHUB_PATH,
    CONF_GITHUB_REPO,
    CONF_GITHUB_TOKEN,
    CONF_HOST,
    CONF_PORT,
    CONF_SIDECAR_URL,
    CONF_SSL,
    CONF_VERIFY_SSL,
    DEFAULT_GITHUB_BRANCH,
    DEFAULT_GITHUB_CHECK_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_SSL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def _schema(defaults: dict) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
            vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): int,
            vol.Required(CONF_SSL, default=defaults.get(CONF_SSL, DEFAULT_SSL)): bool,
            vol.Required(
                CONF_VERIFY_SSL,
                default=defaults.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
            ): bool,
            vol.Optional(
                CONF_SIDECAR_URL,
                default=defaults.get(CONF_SIDECAR_URL, ""),
            ): str,
            vol.Optional(
                CONF_GITHUB_REPO,
                default=defaults.get(CONF_GITHUB_REPO, ""),
            ): str,
            vol.Optional(
                CONF_GITHUB_BRANCH,
                default=defaults.get(CONF_GITHUB_BRANCH, DEFAULT_GITHUB_BRANCH),
            ): str,
            vol.Optional(
                CONF_GITHUB_PATH,
                default=defaults.get(CONF_GITHUB_PATH, ""),
            ): str,
            vol.Optional(
                CONF_GITHUB_TOKEN,
                default=defaults.get(CONF_GITHUB_TOKEN, ""),
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            vol.Optional(
                CONF_GITHUB_CHECK_INTERVAL,
                default=defaults.get(CONF_GITHUB_CHECK_INTERVAL, DEFAULT_GITHUB_CHECK_INTERVAL),
            ): vol.All(int, vol.Range(min=1)),
        }
    )


async def _test_connection(hass: HomeAssistant, user_input: dict) -> str | None:
    """Try to reach /api/getBoxes. Returns an error key, or None on success."""
    url = build_base_url(user_input[CONF_HOST], user_input[CONF_PORT], user_input[CONF_SSL])
    client = TeddyCloudApiClient(hass, url, verify_ssl=user_input[CONF_VERIFY_SSL])
    try:
        await client.get_boxes()
    except TeddyCloudApiError as err:
        err_str = str(err)
        _LOGGER.warning("teddycloud: cannot connect to %s — %s", url, err_str)
        if "SSL" in err_str or "certificate" in err_str.lower():
            return "ssl_error"
        if "timeout" in err_str.lower() or isinstance(err.__cause__, TimeoutError):
            return "timeout"
        return "cannot_connect"
    return None


class TeddyCloudConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for TeddyCloud."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}")
            self._abort_if_unique_id_configured()

            error_key = await _test_connection(self.hass, user_input)
            if error_key is None:
                return self.async_create_entry(
                    title=f"TeddyCloud ({user_input[CONF_HOST]})", data=user_input
                )
            errors["base"] = error_key

        return self.async_show_form(
            step_id="user",
            data_schema=_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        reconfigure_entry = self._get_reconfigure_entry()
        defaults = {**reconfigure_entry.data, **(user_input or {})}

        if user_input is not None:
            new_unique_id = f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}"
            await self.async_set_unique_id(new_unique_id)
            self._abort_if_unique_id_configured()

            error_key = await _test_connection(self.hass, user_input)
            if error_key is None:
                return self.async_update_reload_and_abort(
                    reconfigure_entry,
                    unique_id=new_unique_id,
                    title=f"TeddyCloud ({user_input[CONF_HOST]})",
                    data=user_input,
                )
            errors["base"] = error_key

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_schema(defaults),
            errors=errors,
        )
