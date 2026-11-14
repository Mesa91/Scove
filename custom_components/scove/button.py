"""Pulsanti: conferme di manutenzione e azioni rapide."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory

from .const import PARTS
from .entity import ScoveEntity
from .hub import ScoveHub


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    hub: ScoveHub = entry.runtime_data
    entities: list[ButtonEntity] = [
        ScoveResetButton(hub, key, f"Conferma: {part['name'].lower()}") for key, part in PARTS.items()
    ]
    entities.append(ScoveCommandButton(hub, "evac", "Svuota contenitore", "mdi:delete-empty"))
    entities.append(ScoveCommandButton(hub, "find", "Trova robot", "mdi:map-marker-question"))
    async_add_entities(entities)


class ScoveResetButton(ScoveEntity, ButtonEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:check-circle-outline"

    def __init__(self, hub: ScoveHub, part: str, name: str) -> None:
        super().__init__(hub, f"reset_{part}", name)
        self._part = part

    async def async_press(self) -> None:
        self.hub.reset_part(self._part)


class ScoveCommandButton(ScoveEntity, ButtonEntity):
    def __init__(self, hub: ScoveHub, command: str, name: str, icon: str) -> None:
        super().__init__(hub, f"cmd_{command}", name)
        self._command = command
        self._attr_icon = icon

    async def async_press(self) -> None:
        await self.hub.async_command(self._command)
