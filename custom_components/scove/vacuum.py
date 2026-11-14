"""Il robot come entità vacuum."""
from __future__ import annotations

from typing import Any

from homeassistant.components.vacuum import StateVacuumEntity, VacuumActivity, VacuumEntityFeature

from .entity import ScoveEntity

ACTIVITY = {
    "run": VacuumActivity.CLEANING, "charge": VacuumActivity.DOCKED, "evac": VacuumActivity.DOCKED,
    "stop": VacuumActivity.IDLE, "pause": VacuumActivity.PAUSED, "stuck": VacuumActivity.ERROR,
    "hmUsrDock": VacuumActivity.RETURNING, "hmMidMsn": VacuumActivity.RETURNING,
    "hmPostMsn": VacuumActivity.RETURNING,
}


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([ScoveVacuum(entry.runtime_data)])


class ScoveVacuum(ScoveEntity, StateVacuumEntity):
    _attr_supported_features = (
        VacuumEntityFeature.START | VacuumEntityFeature.STOP | VacuumEntityFeature.PAUSE
        | VacuumEntityFeature.RETURN_HOME | VacuumEntityFeature.LOCATE
        | VacuumEntityFeature.STATE | VacuumEntityFeature.SEND_COMMAND
    )

    def __init__(self, hub) -> None:
        super().__init__(hub, "vacuum", None)

    @property
    def available(self) -> bool:
        return self.hub.connected

    @property
    def activity(self) -> VacuumActivity:
        if self.hub.error_code:
            return VacuumActivity.ERROR
        return ACTIVITY.get(self.hub.phase, VacuumActivity.IDLE)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        robot_map = self.hub.data.get("map") or {}
        return {
            "fase": self.hub.phase,
            "stanze": [r["name"] for r in robot_map.get("rooms") or []],
            "salute": self.hub.health,
        }

    async def async_start(self) -> None:
        await self.hub.async_command("start")

    async def async_stop(self, **kwargs: Any) -> None:
        await self.hub.async_command("stop")

    async def async_pause(self) -> None:
        await self.hub.async_command("pause")

    async def async_return_to_base(self, **kwargs: Any) -> None:
        await self.hub.async_command("dock")

    async def async_locate(self, **kwargs: Any) -> None:
        await self.hub.async_command("find")

    async def async_send_command(self, command: str, params: dict | list | None = None, **kwargs: Any) -> None:
        if command == "clean_rooms" and isinstance(params, dict):
            await self.hub.async_clean_rooms(rooms=params.get("rooms"), areas=params.get("areas"))
        else:
            await self.hub.client.send_command(command, params if isinstance(params, dict) else None)
