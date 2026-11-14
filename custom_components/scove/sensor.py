"""Sensori di stato, salute e manutenzione."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime

from . import logic
from .const import PARTS, PHASE_LABELS
from .entity import ScoveEntity
from .hub import ScoveHub


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    hub: ScoveHub = entry.runtime_data
    S = ScoveSensor
    entities: list[SensorEntity] = [
        S(hub, "battery", "Batteria", lambda h: h.reported.get("batPct"), PERCENTAGE, SensorDeviceClass.BATTERY),
        S(hub, "phase", "Stato", lambda h: PHASE_LABELS.get(h.phase, h.phase), icon="mdi:robot-vacuum"),
        S(hub, "error", "Errore", lambda h: logic.error_text(h.error_code) if h.error_code else "Nessuno",
          icon="mdi:alert-circle-outline", attrs=lambda h: {"codice": h.error_code}),
        S(hub, "health", "Salute", lambda h: h.health, PERCENTAGE, icon="mdi:heart-pulse",
          attrs=lambda h: {"componenti": h.parts}),
        S(hub, "battery_retention", "Capacità batteria", lambda h: h.battery_retention, PERCENTAGE,
          icon="mdi:battery-heart-variant", diag=True),
        S(hub, "battery_cycles", "Cicli batteria",
          lambda h: (h.reported.get("bbchg3") or {}).get("nLithChrg"), icon="mdi:battery-sync", diag=True),
        S(hub, "wifi", "Segnale Wi-Fi", lambda h: (h.reported.get("signal") or {}).get("rssi"), "dBm",
          SensorDeviceClass.SIGNAL_STRENGTH, diag=True),
        S(hub, "missions", "Missioni totali", lambda h: (h.reported.get("bbmssn") or {}).get("nMssn"),
          icon="mdi:counter", diag=True),
        S(hub, "next_run", "Prossima pulizia", lambda h: h.next_run, device_class=SensorDeviceClass.TIMESTAMP),
        S(hub, "last_alert", "Ultimo avviso",
          lambda h: ((h.data.get("alerts") or [{}])[0].get("message") or "Nessuno")[:250], icon="mdi:bell-outline"),
        S(hub, "last_mission", "Ultima pulizia",
          lambda h: ((h.data.get("history") or [{}])[0].get("result")), icon="mdi:broom",
          attrs=lambda h: (h.data.get("history") or [{}])[0]),
    ]
    for key, part in PARTS.items():
        entities.append(S(
            hub, f"part_{key}", part["name"], lambda h, k=key: h.parts[k]["remaining"],
            UnitOfTime.HOURS, icon=part["icon"], diag=True, attrs=lambda h, k=key: h.parts[k],
        ))
    async_add_entities(entities)


class ScoveSensor(ScoveEntity, SensorEntity):
    def __init__(
        self, hub: ScoveHub, key: str, name: str, value: Callable[[ScoveHub], Any],
        unit: str | None = None, device_class: SensorDeviceClass | None = None,
        icon: str | None = None, diag: bool = False,
        attrs: Callable[[ScoveHub], dict] | None = None,
    ) -> None:
        super().__init__(hub, key, name)
        self._value, self._attrs = value, attrs
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_icon = icon
        if unit and device_class is not SensorDeviceClass.TIMESTAMP:
            self._attr_state_class = SensorStateClass.MEASUREMENT
        if diag:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> Any:
        return self._value(self.hub)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        return self._attrs(self.hub) if self._attrs else None
