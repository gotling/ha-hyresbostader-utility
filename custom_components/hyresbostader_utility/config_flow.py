"""Config flow for Hyresbostäder Utility."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import HyresbostaderAPI, HyresbostaderAuthError, HyresbostaderConnectionError
from .const import CONF_USER_ID, DOMAIN

_LOGGER = logging.getLogger(__name__)

PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): TextSelector(),
        vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
    }
)
STEP_REAUTH_DATA_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR})


class HyresbostaderConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Hyresbostäder Utility."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for portal username and password."""
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_USERNAME])
            self._abort_if_unique_id_configured()
            user_id = await self._async_get_user_id(
                user_input[CONF_USERNAME], user_input[CONF_PASSWORD], errors
            )
            if user_id is not None:
                return self.async_create_entry(
                    title=user_input[CONF_USERNAME],
                    data={**user_input, CONF_USER_ID: user_id},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA, user_input
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication after the portal rejected the credentials."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new password."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        username = entry.data[CONF_USERNAME]
        if user_input is not None:
            user_id = await self._async_get_user_id(
                username, user_input[CONF_PASSWORD], errors
            )
            if user_id is not None:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_USER_ID: user_id,
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_DATA_SCHEMA,
            description_placeholders={"username": username},
            errors=errors,
        )

    async def _async_get_user_id(
        self, username: str, password: str, errors: dict[str, str]
    ) -> str | None:
        """Log in and return the IMD userId, or fill in errors and return None."""
        session = async_create_clientsession(self.hass, auto_cleanup=False)
        api = HyresbostaderAPI(session, username, password)
        try:
            await api.async_login()
            return await api.async_get_user_id()
        except HyresbostaderAuthError:
            errors["base"] = "invalid_auth"
        except HyresbostaderConnectionError:
            errors["base"] = "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error during login")
            errors["base"] = "unknown"
        finally:
            session.detach()
        return None
