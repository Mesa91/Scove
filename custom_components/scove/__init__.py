"""Scove: il mio Roomba in Home Assistant."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import panel_custom, websocket_api
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv, entity_registry as er

from .const import DOMAIN, PLATFORMS, REMOVED_PARTS, VERSION
from .hub import ScoveHub

_LOGGER = logging.getLogger(__name__)
PANEL_URL = "/scove_static"
type ScoveConfigEntry = ConfigEntry[ScoveHub]


async def async_setup_entry(hass: HomeAssistant, entry: ScoveConfigEntry) -> bool:
    hub = ScoveHub(hass, entry)
    try:
        await hub.async_start()
    except Exception as err:  # noqa: BLE001
        if hub.client is not None:
            await hub.client.disconnect()
        raise ConfigEntryNotReady(f"Robot non raggiungibile: {err!r}") from err
    entry.runtime_data = hub
    registry = er.async_get(hass)
    gone = {f"{hub.blid}_{prefix}_{part}" for part in REMOVED_PARTS for prefix in ("part", "reset")}
    for ent in er.async_entries_for_config_entry(registry, entry.entry_id):
        if ent.unique_id in gone:
            registry.async_remove(ent.entity_id)
    await _async_setup_shared(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ScoveConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.async_stop()
    return ok


def _hub(hass: HomeAssistant, entry_id: str | None = None) -> ScoveHub:
    entries = [e for e in hass.config_entries.async_loaded_entries(DOMAIN)]
    for entry in entries:
        if entry_id in (None, entry.entry_id):
            return entry.runtime_data
    raise HomeAssistantError("Nessun robot Scove configurato")


async def _async_setup_shared(hass: HomeAssistant) -> None:
    """Pannello, servizi e API: una volta sola."""
    if hass.data.get(DOMAIN):
        return
    hass.data[DOMAIN] = True

    await hass.http.async_register_static_paths([
        StaticPathConfig(PANEL_URL, str(Path(__file__).parent / "frontend"), False)
    ])
    await panel_custom.async_register_panel(
        hass, webcomponent_name="scove-panel", frontend_url_path="scove",
        module_url=f"{PANEL_URL}/scove-panel.js?v={VERSION}",
        sidebar_title="Scove", sidebar_icon="mdi:robot-vacuum", require_admin=False,
    )

    add_extra_js_url(hass, f"{PANEL_URL}/scove-map-card.js?v={VERSION}")

    async def clean_rooms(call: ServiceCall) -> None:
        await _hub(hass).async_clean_rooms(
            rooms=call.data.get("rooms"), areas=call.data.get("areas"),
            ordered=call.data.get("ordered", True),
        )

    async def reset_part(call: ServiceCall) -> None:
        _hub(hass).reset_part(call.data["part"])

    async def refresh_map(call: ServiceCall) -> None:
        await _hub(hass).async_refresh_cloud()

    hass.services.async_register(DOMAIN, "clean_rooms", clean_rooms, vol.Schema({
        vol.Optional("rooms"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("areas"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("ordered", default=True): cv.boolean,
    }))
    hass.services.async_register(DOMAIN, "reset_part", reset_part, vol.Schema({vol.Required("part"): cv.string}))
    hass.services.async_register(DOMAIN, "refresh_map", refresh_map)

    websocket_api.async_register_command(hass, ws_get)
    websocket_api.async_register_command(hass, ws_command)
    websocket_api.async_register_command(hass, ws_save)


@websocket_api.websocket_command({vol.Required("type"): "scove/get"})
@callback
def ws_get(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    try:
        connection.send_result(msg["id"], _hub(hass).snapshot())
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "not_found", str(err))


@websocket_api.websocket_command({
    vol.Required("type"): "scove/command",
    vol.Required("action"): str,
    vol.Optional("rooms"): [str],
    vol.Optional("part"): str,
})
@websocket_api.async_response
async def ws_command(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    try:
        hub = _hub(hass)
        action = msg["action"]
        if action == "clean_rooms":
            await hub.async_clean_rooms(rooms=msg.get("rooms"))
        elif action == "reset_part":
            hub.reset_part(msg["part"])
        elif action == "refresh_map":
            await hub.async_refresh_cloud()
        elif action == "clear_coverage":
            hub.clear_coverage()
        elif action in ("start", "stop", "pause", "resume", "dock", "find", "evac"):
            await hub.async_command(action)
        else:
            raise HomeAssistantError(f"Comando sconosciuto: {action}")
        connection.send_result(msg["id"], hub.snapshot())
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "failed", str(err))


@websocket_api.websocket_command({
    vol.Required("type"): "scove/save",
    vol.Required("key"): str,
    vol.Required("value"): vol.Any(list, dict),
})
@callback
def ws_save(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    try:
        hub = _hub(hass)
        hub.save_section(msg["key"], msg["value"])
        connection.send_result(msg["id"], hub.snapshot())
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "failed", str(err))
