"""Sensori binari: problemi e connessione."""
from __future__ import annotations

from collections.abc import Callable

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity

from .entity import ScoveEntity
from .hub import ScoveHub


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    hub: ScoveHub = entry.runtime_data
    B = ScoveBinary
    async_add_entities([
        B(hub, "connected", "Connesso", lambda h: h.connected, BinarySensorDeviceClass.CONNECTIVITY),
        B(hub, "bin_full", "Contenitore pieno", lambda h: h.bin_full, BinarySensorDeviceClass.PROBLEM),
        B(hub, "problem", "Problema", lambda h: bool(h.error_code) or h.phase == "stuck",
          BinarySensorDeviceClass.PROBLEM),
        B(hub, "maintenance", "Manutenzione necessaria",
          lambda h: any(p["level"] == "due" for p in h.parts.values()), BinarySensorDeviceClass.PROBLEM),
        B(hub, "cleaning", "Pulizia in corso", lambda h: h.phase == "run", BinarySensorDeviceClass.RUNNING),
    ])


class ScoveBinary(ScoveEntity, BinarySensorEntity):
    def __init__(self, hub: ScoveHub, key: str, name: str, value: Callable[[ScoveHub], bool],
                 device_class: BinarySensorDeviceClass) -> None:
        super().__init__(hub, key, name)
        self._value = value
        self._attr_device_class = device_class

    @property
    def is_on(self) -> bool:
        return bool(self._value(self.hub))
