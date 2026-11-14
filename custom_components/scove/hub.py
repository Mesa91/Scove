"""Il cuore di Scove: connessione al robot, missioni, programma, salute, avvisi."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
import time
import uuid
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_change, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import logic
from .cloud import CloudError, IRobotCloud, parse_map
from .const import (
    CONF_BLID, CONF_COUNTRY, CONF_ROBOT_PASSWORD, DOMAIN, MISSION_PHASES,
    MOVING_PHASES, PARTS, PHASE_LABELS, SIGNAL_UPDATE, STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

MAX_TRAIL = 6000
MAX_HISTORY = 30
MAX_ALERTS = 60
DEFAULT_SETTINGS = {"notify_service": "", "min_battery": 30, "notify_done": True, "notify_start": True}


class ScoveHub:
    """Un robot."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.blid: str = entry.data[CONF_BLID]
        self.client: Any = None
        self.cloud = IRobotCloud(
            async_get_clientsession(hass), entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD], entry.data.get(CONF_COUNTRY, "IT"),
        )
        self._store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{self.blid}")
        self.data: dict[str, Any] = {}
        self.position: dict[str, float] | None = None
        self.cloud_error: str | None = None
        self._unsubs: list[Any] = []
        self._pos_task: asyncio.Task | None = None
        self._pos_blocked = False
        self._prev_phase = ""
        self._prev_error = 0
        self._prev_bin_full = False
        self._prev_levels: dict[str, str] = {}
        self._offline_since: float | None = None
        self._offline_alerted = False
        self._dirty = False
        self._pending_schedule: str | None = None

    # ── avvio / arresto ────────────────────────────────────────────────

    async def async_start(self) -> None:
        from roombapy import RoombaClient, TransportOptions, generate_tls_context

        stored = await self._store.async_load() or {}
        self.data = {
            "schedules": [], "zones": [], "room_areas": {}, "baselines": {},
            "calibration": {"dx": 0, "dy": 0, "rotation": 0, "mirror": False},
            "settings": dict(DEFAULT_SETTINGS), "map": None, "trail": [],
            "coverage": {}, "history": [], "alerts": [], "mission": None,
            "peak_cap": None,
            **stored,
        }
        self.data["settings"] = {**DEFAULT_SETTINGS, **(self.data.get("settings") or {})}

        tls = await self.hass.async_add_executor_job(generate_tls_context)
        self.client = RoombaClient(
            self.entry.data[CONF_HOST], self.blid, self.entry.data[CONF_ROBOT_PASSWORD],
            transport=TransportOptions(tls_context=tls),
        )
        self.client.register_on_message_callback(self._on_message)
        self.client.register_on_connection_state_callback(self._on_connection)
        async with asyncio.timeout(20):
            await self.client.connect()
            while not self.reported.get("name") and not self.reported.get("batPct"):
                await asyncio.sleep(0.5)

        if self.data.get("meter") != 2:
            # La 1.0.0 leggeva le ore da un campo che sulla serie i vale sempre 0:
            # i riferimenti salvati allora non sono confrontabili e ripartono da qui.
            self.data["baselines"] = {}
            self.data["meter"] = 2
        for old_key in [k for k in self.data["baselines"] if k not in PARTS]:
            del self.data["baselines"][old_key]
        self._ensure_baselines()
        self._prev_phase = self.phase
        self._prev_error = self.error_code
        self._prev_bin_full = self.bin_full
        self._prev_levels = {k: v["level"] for k, v in self.parts.items()}

        self._unsubs.append(async_track_time_change(self.hass, self._tick, second=0))
        self._unsubs.append(
            async_track_time_interval(self.hass, self._refresh_cloud_job, timedelta(hours=6))
        )
        self.entry.async_create_background_task(self.hass, self.async_refresh_cloud(), "scove_cloud")
        if self.phase in MOVING_PHASES:
            self._start_position()

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._stop_position()
        if self.client is not None:
            await self.client.disconnect()
        await self._store.async_save(self.data)

    # ── stato ──────────────────────────────────────────────────────────

    @property
    def reported(self) -> dict[str, Any]:
        if self.client is None:
            return {}
        return (self.client.master_state.get("state") or {}).get("reported") or {}

    @property
    def connected(self) -> bool:
        return bool(self.client and self.client.connected)

    @property
    def mission_status(self) -> dict[str, Any]:
        return self.reported.get("cleanMissionStatus") or {}

    @property
    def phase(self) -> str:
        return self.mission_status.get("phase") or ""

    @property
    def error_code(self) -> int:
        return int(self.mission_status.get("error") or 0)

    @property
    def bin_full(self) -> bool:
        return bool((self.reported.get("bin") or {}).get("full"))

    @property
    def parts(self) -> dict[str, dict[str, Any]]:
        return logic.parts_status(logic.runtime_hours(self.reported), self.data.get("baselines") or {})

    @property
    def battery_retention(self) -> float | None:
        return logic.battery_retention(self.reported, self.data.get("peak_cap"))

    @property
    def health(self) -> int:
        return logic.health_score(self.reported, self.parts, self.connected, self.data.get("peak_cap"))

    @property
    def next_run(self) -> Any | None:
        return logic.next_run(self.data.get("schedules") or [], dt_util.now())

    def snapshot(self) -> dict[str, Any]:
        """Tutto quello che serve al pannello."""
        rep = self.reported
        nxt = self.next_run
        return {
            "name": rep.get("name") or self.entry.title,
            "sku": rep.get("sku"), "firmware": rep.get("softwareVer"),
            "connected": self.connected, "battery": rep.get("batPct"),
            "phase": self.phase, "phase_label": PHASE_LABELS.get(self.phase, self.phase),
            "cycle": self.mission_status.get("cycle"),
            "error": self.error_code,
            "error_text": logic.error_text(self.error_code) if self.error_code else None,
            "bin_full": self.bin_full, "bin_present": bool((rep.get("bin") or {}).get("present", True)),
            "rssi": (rep.get("signal") or {}).get("rssi"),
            "health": self.health, "battery_retention": self.battery_retention,
            "battery_cycles": logic.battery_cycles(rep),
            "parts": self.parts, "runtime_hours": round(logic.runtime_hours(rep), 1),
            "missions": rep.get("bbmssn") or {},
            "map": self.data.get("map"), "cloud_error": self.cloud_error,
            "position": self.position, "trail": self.data.get("trail") or [],
            "coverage": self.data.get("coverage") or {}, "cell_mm": logic.CELL_MM,
            "mission": self.data.get("mission"), "history": self.data.get("history") or [],
            "schedules": self.data.get("schedules") or [], "zones": self.data.get("zones") or [],
            "room_areas": self.data.get("room_areas") or {},
            "calibration": self.data.get("calibration") or {},
            "settings": self.data.get("settings") or {},
            "alerts": self.data.get("alerts") or [],
            "next_run": nxt.isoformat() if nxt else None,
        }

    # ── messaggi dal robot ─────────────────────────────────────────────

    @callback
    def _on_connection(self, *_: Any) -> None:
        if self.connected:
            self._offline_since = None
            self._offline_alerted = False
        elif self._offline_since is None:
            self._offline_since = time.monotonic()
        self._notify_entities()

    def _ensure_baselines(self) -> None:
        """Fissa il punto di partenza dei contatori appena il robot comunica le ore."""
        baselines = self.data["baselines"]
        if len(baselines) >= len(PARTS):
            return
        hours = logic.runtime_hours(self.reported)
        if hours <= 0:
            return
        for key in PARTS:
            baselines.setdefault(key, hours)
        self._dirty = True

    @callback
    def _on_message(self, _message: Any) -> None:
        self._ensure_baselines()
        phase, error, full = self.phase, self.error_code, self.bin_full
        est = (self.reported.get("bbchg3") or {}).get("estCap")
        if est and est > (self.data.get("peak_cap") or 0):
            self.data["peak_cap"] = est
            self._dirty = True

        if phase != self._prev_phase:
            self._phase_changed(self._prev_phase, phase)
            self._prev_phase = phase
        if error != self._prev_error:
            if error:
                self.alert("error", f"{logic.error_text(error)} (codice {error})", "problema")
            self._prev_error = error
        if full and not self._prev_bin_full:
            self.alert("bin", "Contenitore pieno: va svuotato.", "problema")
        self._prev_bin_full = full

        levels = {k: v["level"] for k, v in self.parts.items()}
        for key, level in levels.items():
            if level == self._prev_levels.get(key):
                continue
            todo = PARTS[key]["todo"]
            if level == "due":
                self.alert(f"part_{key}", f"{todo}.", "manutenzione")
            elif level == "soon" and not PARTS[key].get("routine"):
                hours = self.parts[key]["remaining"]
                self.alert(f"part_{key}", f"{todo}: mancano circa {hours:g} ore di lavoro.", "manutenzione")
        self._prev_levels = levels
        self._notify_entities()

    def _phase_changed(self, old: str, new: str) -> None:
        mission = self.data.get("mission")
        if new == "run" and (mission is None or mission.get("ended")):
            self.data["mission"] = {
                "started": dt_util.now().isoformat(), "ended": None,
                "rooms": (mission or {}).get("pending_rooms") or [],
                "initiator": self.mission_status.get("initiator"), "stuck": 0,
                "schedule": self._pending_schedule,
            }
            self._pending_schedule = None
            self.data["trail"] = []
            self._pos_blocked = False
            started = self.data["mission"]
            what = f" per il programma «{started['schedule']}»" if started["schedule"] else ""
            where = f" ({', '.join(started['rooms'])})" if started["rooms"] else ""
            if (self.data.get("settings") or {}).get("notify_start", True):
                self.alert("start", f"Pulizia avviata{what}{where}.", "info")
        if new == "stuck":
            if self.data.get("mission"):
                self.data["mission"]["stuck"] = self.data["mission"].get("stuck", 0) + 1
            self.alert("stuck", "Il robot è bloccato e ha bisogno di aiuto.", "problema")
        if new in MOVING_PHASES:
            self._start_position()
        else:
            self._stop_position()
        if new in ("charge", "stop") and old in MISSION_PHASES:
            self._mission_ended()

    def _mission_ended(self) -> None:
        mission = self.data.get("mission")
        if not mission or mission.get("ended"):
            return
        status = self.mission_status
        mission["ended"] = dt_util.now().isoformat()
        mission["minutes"] = status.get("mssnM")
        mission["area_m2"] = round((status.get("sqft") or 0) * 0.0929, 1)
        mission["error"] = self.error_code
        mission["result"] = "errore" if self.error_code else "completata" if self.phase == "charge" else "interrotta"
        for cell in {logic.cell_of(p[0], p[1]) for p in self.data.get("trail") or []}:
            self.data["coverage"][cell] = self.data["coverage"].get(cell, 0) + 1
        self.data["history"] = ([dict(mission)] + (self.data.get("history") or []))[:MAX_HISTORY]
        if (self.data.get("settings") or {}).get("notify_done", True):
            what = f" del programma «{mission['schedule']}»" if mission.get("schedule") else ""
            self.alert(
                "done",
                f"Pulizia{what} {mission['result']}: {mission['area_m2']} m² in {mission['minutes'] or '?'} min.",
                "info",
            )
        self._save_soon()

    # ── posizione e passaggi ───────────────────────────────────────────

    def _start_position(self) -> None:
        if self._pos_blocked or (self._pos_task and not self._pos_task.done()):
            return
        self._pos_task = self.entry.async_create_background_task(
            self.hass, self._watch_position(), "scove_position"
        )

    def _stop_position(self) -> None:
        if self._pos_task and not self._pos_task.done():
            self._pos_task.cancel()
        self._pos_task = None

    async def _watch_position(self) -> None:
        from roombapy import RrtpUnsupportedError

        try:
            async for pos in self.client.watch_position(interval=1.0):
                x, y = logic.transform(pos.x * 1000.0, pos.y * 1000.0, self.data.get("calibration") or {})
                self.position = {"x": round(x), "y": round(y), "theta": round(pos.theta, 2)}
                trail = self.data["trail"]
                if self.phase == "run" and (
                    not trail or abs(trail[-1][0] - x) + abs(trail[-1][1] - y) >= 80
                ):
                    trail.append([round(x), round(y)])
                    if len(trail) > MAX_TRAIL:
                        del trail[: len(trail) - MAX_TRAIL]
                    self._dirty = True
        except RrtpUnsupportedError:
            self._pos_blocked = True
            _LOGGER.info("Il robot non risponde alle richieste di posizione in questa missione")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Lettura della posizione interrotta")

    # ── cloud ──────────────────────────────────────────────────────────

    async def _refresh_cloud_job(self, _now: Any = None) -> None:
        await self.async_refresh_cloud()

    async def async_refresh_cloud(self) -> None:
        try:
            pmaps = await self.cloud.get_pmaps(self.blid)
            umfs: dict[str, dict] = {}
            first = parse_map(pmaps, {})
            if first and first.get("version"):
                try:
                    umfs[first["pmap_id"]] = await self.cloud.get_umf(
                        self.blid, first["pmap_id"], first["version"]
                    )
                except CloudError as err:
                    _LOGGER.warning("Geometria della mappa non disponibile: %s", err)
            parsed = parse_map(pmaps, umfs)
            if parsed:
                self.data["map"] = parsed
                self._save_soon()
            self.cloud_error = None
        except CloudError as err:
            self.cloud_error = str(err)
            _LOGGER.warning("Cloud iRobot: %s", err)
        self._notify_entities()

    # ── comandi ────────────────────────────────────────────────────────

    async def async_command(self, command: str) -> None:
        """start, stop, pause, resume, dock, find, evac."""
        if not self.connected:
            raise HomeAssistantError("Il robot non è raggiungibile")
        if command == "start" and self.phase == "pause":
            command = "resume"
        if command == "dock" and self.phase == "run":
            await self.client.send_command("stop")
            await asyncio.sleep(1)
        await self.client.send_command(command)

    def resolve_rooms(self, rooms: list[str] | None, areas: list[str] | None) -> list[dict[str, Any]]:
        """Trasforma nomi/id di stanze e aree HA nelle regioni del robot."""
        robot_map = self.data.get("map") or {}
        candidates = (robot_map.get("rooms") or []) + (robot_map.get("zones") or [])
        room_areas = self.data.get("room_areas") or {}
        wanted = {str(r).casefold() for r in rooms or []}
        area_set = set(areas or [])
        found = [
            c for c in candidates
            if c["id"].casefold() in wanted or c["name"].casefold() in wanted
            or (area_set and room_areas.get(c["id"]) in area_set)
        ]
        return found

    async def async_clean_rooms(
        self, rooms: list[str] | None = None, areas: list[str] | None = None, ordered: bool = True
    ) -> None:
        robot_map = self.data.get("map") or {}
        regions = self.resolve_rooms(rooms, areas)
        if not regions:
            raise HomeAssistantError("Nessuna stanza corrisponde alla richiesta")
        if not robot_map.get("pmap_id") or not robot_map.get("version"):
            raise HomeAssistantError("Mappa del robot non ancora scaricata dal cloud iRobot")
        if not self.connected:
            raise HomeAssistantError("Il robot non è raggiungibile")
        mission = self.data.get("mission") or {"ended": "x"}
        mission["pending_rooms"] = [r["name"] for r in regions]
        self.data["mission"] = mission
        await self.client.send_command("start", {
            "ordered": 1 if ordered else 0,
            "pmap_id": robot_map["pmap_id"],
            "user_pmapv_id": robot_map["version"],
            "regions": [{"region_id": r["id"], "type": r["type"]} for r in regions],
        })

    def reset_part(self, key: str) -> None:
        if key not in PARTS:
            raise HomeAssistantError(f"Componente sconosciuto: {key}")
        self.data["baselines"][key] = logic.runtime_hours(self.reported)
        self._save_soon()
        self._notify_entities()

    def save_section(self, key: str, value: Any) -> None:
        if key not in ("schedules", "zones", "room_areas", "calibration", "settings"):
            raise HomeAssistantError(f"Sezione sconosciuta: {key}")
        if key == "schedules":
            for item in value:
                item.setdefault("id", uuid.uuid4().hex[:8])
        if key == "settings":
            value = {**DEFAULT_SETTINGS, **value}
        self.data[key] = value
        self._save_soon()
        self._notify_entities()

    def clear_coverage(self) -> None:
        self.data["coverage"] = {}
        self.data["trail"] = []
        self._save_soon()

    # ── programma ──────────────────────────────────────────────────────

    async def _tick(self, now: Any) -> None:
        local = dt_util.as_local(now)
        hhmm = local.strftime("%H:%M")
        for schedule in self.data.get("schedules") or []:
            if logic.schedule_due(schedule, local.weekday(), hhmm):
                await self._run_schedule(schedule)
        if (
            self._offline_since is not None and not self._offline_alerted
            and time.monotonic() - self._offline_since > 1800
        ):
            self._offline_alerted = True
            self.alert("offline", "Il robot non risponde da 30 minuti.", "problema")
        if self._dirty:
            self._dirty = False
            await self._store.async_save(self.data)

    async def _run_schedule(self, schedule: dict[str, Any]) -> None:
        name = schedule.get("name") or "Programma"
        battery = self.reported.get("batPct") or 0
        min_battery = (self.data.get("settings") or {}).get("min_battery", 30)
        reason = None
        if not self.connected:
            reason = "il robot non è raggiungibile"
        elif self.phase in MISSION_PHASES:
            reason = "il robot sta già lavorando"
        elif self.bin_full:
            reason = "il contenitore è pieno"
        elif battery < min_battery:
            reason = f"la batteria è al {battery}%"
        if reason:
            self.alert("schedule", f"«{name}» saltato: {reason}.", "programma")
            return
        self._pending_schedule = name
        try:
            if schedule.get("rooms"):
                await self.async_clean_rooms(rooms=schedule["rooms"])
            else:
                await self.async_command("start")
        except HomeAssistantError as err:
            self._pending_schedule = None
            self.alert("schedule", f"«{name}» non partito: {err}", "programma")

    # ── avvisi ─────────────────────────────────────────────────────────

    def alert(self, key: str, message: str, kind: str) -> None:
        name = self.reported.get("name") or self.entry.title
        entry = {"time": dt_util.now().isoformat(), "key": key, "kind": kind, "message": message}
        self.data["alerts"] = ([entry] + (self.data.get("alerts") or []))[:MAX_ALERTS]
        self.hass.bus.async_fire(f"{DOMAIN}_alert", {"robot": name, **entry})
        if kind != "info":
            persistent_notification.async_create(
                self.hass, message, title=f"{name}", notification_id=f"{DOMAIN}_{self.blid}_{key}"
            )
        target = ((self.data.get("settings") or {}).get("notify_service") or "").strip()
        if target:
            service = target.removeprefix("notify.")
            if self.hass.services.has_service("notify", service):
                self.hass.async_create_task(
                    self.hass.services.async_call("notify", service, {"title": name, "message": message})
                )
        self._save_soon()

    # ── utilità ────────────────────────────────────────────────────────

    def _save_soon(self) -> None:
        self._dirty = True

    def _notify_entities(self) -> None:
        async_dispatcher_send(self.hass, SIGNAL_UPDATE.format(self.blid))
