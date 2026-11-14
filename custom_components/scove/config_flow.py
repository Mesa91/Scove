"""Configurazione guidata di Scove."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .cloud import CloudAuthError, CloudError, IRobotCloud
from .const import CONF_BLID, CONF_COUNTRY, CONF_ROBOT_PASSWORD, DOMAIN


class ScoveConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._input: dict[str, Any] = {}
        self._robots: dict[str, dict[str, Any]] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            country = (self.hass.config.country or "IT").upper()
            cloud = IRobotCloud(
                async_get_clientsession(self.hass), user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD], country,
            )
            try:
                self._robots = await cloud.login()
            except CloudAuthError:
                errors["base"] = "invalid_auth"
            except CloudError:
                errors["base"] = "cannot_connect"
            else:
                self._input = {**user_input, CONF_COUNTRY: country}
                if not self._robots:
                    errors["base"] = "no_robots"
                elif len(self._robots) == 1:
                    return await self._create(next(iter(self._robots)))
                else:
                    return await self.async_step_robot()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(CONF_HOST): str,
            }),
            errors=errors,
        )

    async def async_step_robot(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return await self._create(user_input[CONF_BLID])
        options = {blid: (r.get("name") or blid) for blid, r in self._robots.items()}
        return self.async_show_form(
            step_id="robot", data_schema=vol.Schema({vol.Required(CONF_BLID): vol.In(options)})
        )

    async def _create(self, blid: str) -> ConfigFlowResult:
        robot = self._robots[blid]
        await self.async_set_unique_id(blid)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=robot.get("name") or "Roomba",
            data={**self._input, CONF_BLID: blid, CONF_ROBOT_PASSWORD: robot.get("password", "")},
        )
