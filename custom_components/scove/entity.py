"""Base comune delle entità."""
from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, SIGNAL_UPDATE
from .hub import ScoveHub


class ScoveEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hub: ScoveHub, key: str, name: str | None) -> None:
        self.hub = hub
        self._attr_unique_id = f"{hub.blid}_{key}"
        self._attr_name = name
        rep = hub.reported
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, hub.blid)}, name=rep.get("name") or hub.entry.title,
            manufacturer="iRobot", model=rep.get("sku"), sw_version=rep.get("softwareVer"),
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_UPDATE.format(self.hub.blid), self._refresh)
        )

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()
