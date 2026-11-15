"""Il cuore di Scove: connessione al robot, missioni, programma, salute, avvisi."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import json
import logging
from pathlib import Path
import time
import uuid
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar
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
MAX_BACKUPS = 10
MAX_RETRIES = 4
RETRY_MINUTES = 30
PRENOTICE_MINUTES = 10
DEFAULT_SETTINGS = {
    "notify_service": "", "min_battery": 30, "notify_done": True, "notify_start": True,
    # Funzioni facoltative: spente finché non le accendi dal pannello.
    "action_buttons": False, "retry": False, "stuck_details": False,
    "stale_days": 0, "map_backup": False,
}


def _write_backup(folder: str, name: str, payload: dict[str, Any]) -> None:
    path = Path(folder)
    path.mkdir(parents=True, exist_ok=True)
    (path / name).write_text(json.dumps(payload), encoding="utf-8")
    prefix = name.split("_")[0]
    old = sorted(path.glob(f"{prefix}_*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    for extra in old[MAX_BACKUPS:]:
        extra.unlink(missing_ok=True)


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
        self._over_since: float | None = None
        self._run_since: float | None = None
        self._recharging = False

    # ── avvio / arresto ────────────────────────────────────────────────

    async def async_start(self) -> None:
        from roombapy import RoombaClient, TransportOptions, generate_tls_context

        stored = await self._store.async_load() or {}
        self.data = {
            "schedules": [], "zones": [], "room_areas": {}, "baselines": {},
            "calibration": {"dx": 0, "dy": 0, "rotation": 0, "mirror": False},
            "settings": dict(DEFAULT_SETTINGS), "map": None, "trail": [],
            "coverage": {}, "history": [], "alerts": [], "mission": None,
            "peak_cap": None, "deferred": [], "skips": {},
            "room_last": {}, "room_since": {}, "backup_version": None,
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
        last = self.data.get("mission")
        history = self.data.get("history") or []
        if (
            last and last.get("ended") and "coverage" not in last and history
            and history[0].get("started") == last.get("started") and not last.get("error")
        ):
            # Giri chiusi prima della 1.0.11: registra ora le stanze dal percorso salvato.
            missed = self._register_rooms(last)
            if missed is not None:
                last["result"] = "parziale" if missed else "completata"
                history[0] = dict(last)
        self._prev_phase = self.phase
        self._recharging = self.recharging
        self._prev_error = self.error_code
        self._prev_bin_full = self.bin_full
        self._prev_levels = {k: v["level"] for k, v in self.parts.items()}

        self._unsubs.append(async_track_time_change(self.hass, self._tick, second=0))
        self._unsubs.append(
            self.hass.bus.async_listen("mobile_app_notification_action", self._on_action)
        )
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
    def recharging(self) -> bool:
        """In base a ricaricarsi a metà pulizia: il ciclo è ancora aperto e poi riprende."""
        return self.phase == "charge" and (self.mission_status.get("cycle") or "none") != "none"

    @property
    def phase_label(self) -> str:
        if self.recharging:
            return "In ricarica, poi riprende"
        return PHASE_LABELS.get(self.phase, self.phase)

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

    def room_label(self, room: dict[str, Any]) -> str:
        """Nome mostrato per una stanza: l'area di Home Assistant associata, altrimenti quello del robot."""
        area_id = (self.data.get("room_areas") or {}).get(room["id"]) if room.get("type") == "rid" else None
        area = ar.async_get(self.hass).async_get_area(area_id) if area_id else None
        return area.name if area else room["name"]

    def labelled_map(self) -> dict[str, Any] | None:
        robot_map = self.data.get("map")
        if not robot_map:
            return None
        return {
            **robot_map,
            "rooms": [{**r, "label": self.room_label(r)} for r in robot_map.get("rooms") or []],
            "zones": [{**z, "label": z["name"]} for z in robot_map.get("zones") or []],
        }

    def room_days(self) -> list[dict[str, Any]]:
        rooms = (self.labelled_map() or {}).get("rooms") or []
        return logic.room_days(
            rooms, self.data.get("room_last") or {}, self.data.get("room_since") or {}, dt_util.now()
        )

    def snapshot(self) -> dict[str, Any]:
        """Tutto quello che serve al pannello."""
        rep = self.reported
        nxt = self.next_run
        return {
            "name": rep.get("name") or self.entry.title,
            "sku": rep.get("sku"), "firmware": rep.get("softwareVer"),
            "connected": self.connected, "battery": rep.get("batPct"),
            "phase": self.phase, "phase_label": self.phase_label,
            "recharging": self.recharging, "recharge_minutes": self.mission_status.get("rechrgM"),
            "cycle": self.mission_status.get("cycle"),
            "mission_minutes": self.mission_status.get("mssnM"),
            "run_minutes": round(((self.data.get("mission") or {}).get("run_seconds") or 0) / 60),
            "mission_sqft": self.mission_status.get("sqft"),
            "initiator": self.mission_status.get("initiator"),
            "last_command": (self.reported.get("lastCommand") or {}).get("command"),
            "last_command_initiator": (self.reported.get("lastCommand") or {}).get("initiator"),
            "error": self.error_code,
            "error_text": logic.error_text(self.error_code) if self.error_code else None,
            "bin_full": self.bin_full, "bin_present": bool((rep.get("bin") or {}).get("present", True)),
            "rssi": (rep.get("signal") or {}).get("rssi"),
            "health": self.health, "battery_retention": self.battery_retention,
            "battery_cycles": logic.battery_cycles(rep),
            "parts": self.parts, "runtime_hours": round(logic.runtime_hours(rep), 1),
            "missions": rep.get("bbmssn") or {},
            "map": self.labelled_map(), "cloud_error": self.cloud_error,
            "position": self.position, "trail": self.data.get("trail") or [],
            "coverage": self.data.get("coverage") or {}, "cell_mm": logic.CELL_MM,
            "mission": self.data.get("mission"), "history": self.data.get("history") or [],
            "schedules": self.data.get("schedules") or [], "zones": self.data.get("zones") or [],
            "room_areas": self.data.get("room_areas") or {},
            "room_days": self.room_days(),
            "deferred": self.data.get("deferred") or [],
            "backup_version": self.data.get("backup_version"),
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
        self._count_run_time()
        # La missione è finita solo quando il robot è fermo e non ha più un ciclo in corso:
        # una sosta in base per ricaricarsi a metà pulizia non la chiude.
        if phase == "charge":
            self._recharging = self.recharging
        self._check_mission_over()
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

    def _count_run_time(self) -> None:
        """Cronometro di Scove: avanza solo mentre il robot pulisce (non in ricarica o in pausa)."""
        mission = self.data.get("mission")
        now = time.monotonic()
        if self._run_since is not None and mission and not mission.get("ended"):
            mission["run_seconds"] = round(mission.get("run_seconds", 0) + now - self._run_since, 1)
            self._dirty = True
        self._run_since = now if self.phase == "run" else None

    def _check_mission_over(self) -> None:
        """Chiude la missione solo se il robot resta fermo e senza ciclo per un po':
        mentre rientra per ricaricarsi passa per un attimo da uno stato che sembra la fine."""
        mission = self.data.get("mission")
        over = (
            bool(mission) and not mission.get("ended") and self.phase in ("charge", "stop")
            and (self.mission_status.get("cycle") or "none") == "none"
        )
        if not over:
            self._over_since = None
        elif self._over_since is None:
            self._over_since = time.monotonic()
        elif time.monotonic() - self._over_since >= 45:
            self._over_since = None
            self._recharging = False
            self._mission_ended()

    def _reopen_mission(self, mission: dict[str, Any]) -> None:
        """Il robot riprende dopo la ricarica una pulizia che avevamo dato per chiusa."""
        for cell in {logic.cell_of(p[0], p[1]) for p in self.data.get("trail") or []}:
            left = self.data["coverage"].get(cell, 0) - 1
            if left > 0:
                self.data["coverage"][cell] = left
            else:
                self.data["coverage"].pop(cell, None)
        history = self.data.get("history") or []
        if history and history[0].get("started") == mission.get("started"):
            self.data["history"] = history[1:]
        if "run_seconds" not in mission:
            started = dt_util.parse_datetime(mission.get("started") or "")
            ended = dt_util.parse_datetime(mission.get("ended") or "")
            if started and ended:
                mission["run_seconds"] = round((ended - started).total_seconds(), 1)
        for key in ("ended", "result", "error", "minutes_estimated", "minutes", "total_minutes", "area_m2"):
            mission.pop(key, None)
        mission["ended"] = None
        mission["recharges"] = mission.get("recharges", 0) + 1

    def _phase_changed(self, old: str, new: str) -> None:
        mission = self.data.get("mission")
        if new == "run" and mission and self._recharging:
            self._recharging = False
            if mission.get("ended"):
                self._reopen_mission(mission)
            else:
                mission["recharges"] = mission.get("recharges", 0) + 1
            if (self.data.get("settings") or {}).get("notify_start", True):
                self.alert("resume", "Ricarica finita: riprendo la pulizia.", "info")
        elif new == "run" and (mission is None or mission.get("ended")):
            self.data["mission"] = {
                "started": dt_util.now().isoformat(), "ended": None,
                "rooms": (mission or {}).get("pending_rooms") or [],
                "room_ids": (mission or {}).get("pending_room_ids"),
                "initiator": self.mission_status.get("initiator"), "stuck": 0,
                "schedule": self._pending_schedule,
            }
            self._pending_schedule = None
            self.data["trail"] = []
            self._pos_blocked = False
            started = self.data["mission"]
            what = f" per il programma «{started['schedule']}»" if started["schedule"] else ""
            where = f" ({', '.join(started['rooms'])})" if started["rooms"] else ""
            settings = self.data.get("settings") or {}
            if settings.get("notify_start", True):
                self.alert(
                    "start", f"Pulizia avviata{what}{where}.", "info",
                    actions=[("dock", "Torna alla base")] if settings.get("action_buttons") else None,
                )
        if new == "stuck":
            if self.data.get("mission"):
                self.data["mission"]["stuck"] = self.data["mission"].get("stuck", 0) + 1
            message, image = "Il robot è bloccato e ha bisogno di aiuto.", None
            if (self.data.get("settings") or {}).get("stuck_details"):
                rooms = (self.labelled_map() or {}).get("rooms") or []
                room = logic.room_at(self.position["x"], self.position["y"], rooms) if self.position else None
                if room:
                    message = f"Il robot è bloccato in {room['label']} e ha bisogno di aiuto."
                image = f"/api/{DOMAIN}/{self.blid}/map.png?t={int(time.time())}"
            self.alert("stuck", message, "problema", image=image)
        if new in MOVING_PHASES:
            self._start_position()
        else:
            self._stop_position()

    def _register_rooms(self, mission: dict[str, Any]) -> list[str] | None:
        """Segna come pulite le stanze davvero percorse. Restituisce i nomi di quelle previste
        ma rimaste indietro, oppure None se non si può giudicare (niente mappa o solo zone)."""
        rooms = (self.labelled_map() or {}).get("rooms") or []
        trail = self.data.get("trail") or []
        if not rooms or not trail:
            return None
        coverage = logic.room_coverage(trail, rooms)
        wanted = mission.get("room_ids") if mission.get("rooms") else [r["id"] for r in rooms]
        mission["coverage"] = {r["label"]: round(coverage.get(r["id"], 0) * 100) for r in rooms}
        for room in rooms:
            if coverage.get(room["id"], 0) >= logic.ROOM_DONE:
                self.data["room_last"][room["id"]] = mission["ended"]
        if not wanted:
            return None
        mission["missed"] = [
            r["label"] for r in rooms if r["id"] in wanted and coverage.get(r["id"], 0) < logic.ROOM_DONE
        ]
        return mission["missed"]

    def _mission_ended(self) -> None:
        mission = self.data.get("mission")
        if not mission or mission.get("ended"):
            return
        status = self.mission_status
        mission["ended"] = dt_util.now().isoformat()
        self._count_run_time()
        started = dt_util.parse_datetime(mission.get("started") or "")
        total = round((dt_util.now() - started).total_seconds() / 60) if started else None
        run_seconds = mission.get("run_seconds")
        # Minuti di pulizia effettiva, senza soste; se manca il cronometro, il tempo trascorso.
        mission["minutes"] = round(run_seconds / 60) if run_seconds else (status.get("mssnM") or total or 0)
        mission["total_minutes"] = total
        reported_area = round((status.get("sqft") or 0) * 0.0929, 1)
        mission["area_m2"] = max(reported_area, mission.get("area_m2") or 0) or logic.swept_area_m2(
            self.data.get("trail") or []
        )
        mission["error"] = self.error_code
        missed = self._register_rooms(mission)
        if self.error_code:
            mission["result"] = "errore"
        elif missed is None:
            mission["result"] = "completata" if self.phase == "charge" else "interrotta"
        else:
            mission["result"] = "parziale" if missed else "completata"
        for cell in {logic.cell_of(p[0], p[1]) for p in self.data.get("trail") or []}:
            self.data["coverage"][cell] = self.data["coverage"].get(cell, 0) + 1
        self.data["history"] = ([dict(mission)] + (self.data.get("history") or []))[:MAX_HISTORY]
        if (self.data.get("settings") or {}).get("notify_done", True):
            what = f" del programma «{mission['schedule']}»" if mission.get("schedule") else ""
            self.alert(
                "done",
                f"Pulizia{what} {mission['result']}"
                + (f": circa {mission['area_m2']:g} m² in {mission['minutes']} min di lavoro." if mission["area_m2"]
                   else f" dopo {mission['minutes']} min di lavoro." if mission["minutes"] else ".")
                + (f" Poco o per nulla pulite: {', '.join(mission['missed'])}." if mission.get("missed") else ""),
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
                now_iso = dt_util.now().isoformat()
                for room in parsed.get("rooms") or []:
                    self.data["room_since"].setdefault(room["id"], now_iso)
                if (
                    (self.data.get("settings") or {}).get("map_backup")
                    and parsed.get("version") and parsed["version"] != self.data.get("backup_version")
                ):
                    await self.hass.async_add_executor_job(
                        _write_backup, self.hass.config.path("scove_backups"),
                        f"{self.blid[:8]}_{parsed['version']}.json",
                        {"saved": now_iso, "pmaps": pmaps, "umf": umfs},
                    )
                    self.data["backup_version"] = parsed["version"]
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
            if f"{c['type']}:{c['id']}".casefold() in wanted
            or (c["type"] == "rid" and c["id"].casefold() in wanted)
            or c["name"].casefold() in wanted
            or self.room_label(c).casefold() in wanted
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
        mission["pending_rooms"] = [self.room_label(r) for r in regions]
        mission["pending_room_ids"] = [r["id"] for r in regions if r["type"] == "rid"]
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
        backup_on = False
        if key == "settings":
            value = {**DEFAULT_SETTINGS, **value}
            backup_on = bool(value.get("map_backup")) and not (self.data.get("settings") or {}).get("map_backup")
        self.data[key] = value
        if backup_on:
            self.entry.async_create_background_task(self.hass, self.async_refresh_cloud(), "scove_backup")
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
        settings = self.data.get("settings") or {}
        schedules = self.data.get("schedules") or []
        today = local.date().isoformat()
        skips = self.data.setdefault("skips", {})
        deferred = self.data.setdefault("deferred", [])
        for sid in [k for k, day in skips.items() if day != today]:
            del skips[sid]
        for schedule in schedules:
            if not logic.schedule_due(schedule, local.weekday(), hhmm):
                continue
            sid, name = schedule.get("id"), schedule.get("name") or "Programma"
            if skips.pop(sid, None):
                self.alert("schedule", f"«{name}» saltato per oggi, come richiesto.", "programma")
            elif any(d["id"] == sid and d.get("postponed") for d in deferred):
                continue  # rimandato dall'avviso: parte più tardi
            else:
                await self._run_schedule(schedule)
        if settings.get("action_buttons"):
            ahead = local + timedelta(minutes=PRENOTICE_MINUTES)
            for schedule in schedules:
                if logic.schedule_due(schedule, ahead.weekday(), ahead.strftime("%H:%M")):
                    sid, name = schedule.get("id"), schedule.get("name") or "Programma"
                    self.alert(
                        "prenotice", f"Tra {PRENOTICE_MINUTES} minuti parte «{name}».", "info",
                        actions=[(f"postpone:{sid}", "Rimanda di un'ora"), (f"skip:{sid}", "Salta oggi")],
                    )
        for item in [d for d in deferred if d["at"] <= local.timestamp()]:
            deferred.remove(item)
            schedule = next((s for s in schedules if s.get("id") == item["id"]), None)
            if schedule and schedule.get("enabled", True):
                await self._run_schedule(schedule, item.get("attempt", 0))
            self._dirty = True
        stale_days = int(settings.get("stale_days") or 0)
        if stale_days > 0 and hhmm == "09:00":
            stale = logic.stale_rooms(self.room_days(), stale_days)
            if stale:
                self.alert("stale", f"Non pulite da almeno {stale_days} giorni: {', '.join(stale)}.", "manutenzione")
        self._count_run_time()
        self._check_mission_over()
        if (
            self._offline_since is not None and not self._offline_alerted
            and time.monotonic() - self._offline_since > 1800
        ):
            self._offline_alerted = True
            self.alert("offline", "Il robot non risponde da 30 minuti.", "problema")
        if self._dirty:
            self._dirty = False
            await self._store.async_save(self.data)

    async def _run_schedule(self, schedule: dict[str, Any], attempt: int = 0) -> None:
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
            if (self.data.get("settings") or {}).get("retry") and attempt < MAX_RETRIES:
                deferred = self.data.setdefault("deferred", [])
                deferred[:] = [d for d in deferred if d["id"] != schedule.get("id")]
                deferred.append({
                    "id": schedule.get("id"), "attempt": attempt + 1,
                    "at": dt_util.now().timestamp() + RETRY_MINUTES * 60 - 5,
                })
                self.alert(
                    "schedule",
                    f"«{name}» rimandato: {reason}. Riprovo tra {RETRY_MINUTES} minuti "
                    f"(tentativo {attempt + 1} di {MAX_RETRIES}).", "programma",
                )
            else:
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

    # ── pulsanti negli avvisi ──────────────────────────────────────────

    @callback
    def _on_action(self, event: Any) -> None:
        """Un pulsante premuto in una notifica sul telefono."""
        action = str(event.data.get("action") or "")
        prefix = f"SCOVE:{self.blid}:"
        if not action.startswith(prefix):
            return
        verb, _, arg = action[len(prefix):].partition(":")
        self.hass.async_create_task(self._handle_action(verb, arg))

    async def _handle_action(self, verb: str, arg: str) -> None:
        if verb == "dock":
            try:
                await self.async_command("dock")
            except HomeAssistantError as err:
                self.alert("action", f"Rientro alla base non riuscito: {err}", "programma")
            return
        schedule = next((s for s in self.data.get("schedules") or [] if s.get("id") == arg), None)
        if schedule is None or verb not in ("postpone", "skip"):
            return
        name = schedule.get("name") or "Programma"
        now = dt_util.now()
        try:
            hh, mm = (int(v) for v in str(schedule.get("time")).split(":"))
        except ValueError:
            return
        planned = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if planned < now - timedelta(seconds=30):
            self.alert("action", f"«{name}» è già partito: usa «Torna alla base» per fermarlo.", "info")
            return
        if verb == "skip":
            self.data.setdefault("skips", {})[arg] = now.date().isoformat()
            self.alert("action", f"«{name}» oggi non parte.", "info")
        else:
            later = planned + timedelta(hours=1)
            deferred = self.data.setdefault("deferred", [])
            deferred[:] = [d for d in deferred if d["id"] != arg]
            deferred.append({"id": arg, "attempt": 0, "postponed": True, "at": later.timestamp() - 5})
            self.alert("action", f"«{name}» rimandato alle {later.strftime('%H:%M')}.", "info")
        self._save_soon()

    # ── avvisi ─────────────────────────────────────────────────────────

    def alert(
        self, key: str, message: str, kind: str,
        actions: list[tuple[str, str]] | None = None, image: str | None = None,
    ) -> None:
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
                payload: dict[str, Any] = {"title": name, "message": message}
                extra: dict[str, Any] = {}
                if actions:
                    extra["actions"] = [
                        {"action": f"SCOVE:{self.blid}:{verb}", "title": title} for verb, title in actions
                    ]
                    extra["tag"] = f"{DOMAIN}_{key}"
                if image:
                    extra["image"] = image
                if extra:
                    payload["data"] = extra
                self.hass.async_create_task(self.hass.services.async_call("notify", service, payload))
        self._save_soon()

    # ── utilità ────────────────────────────────────────────────────────

    def _save_soon(self) -> None:
        """Scrive su disco entro un paio di secondi (e comunque allo spegnimento di HA)."""
        self._store.async_delay_save(lambda: self.data, 2)

    def _notify_entities(self) -> None:
        async_dispatcher_send(self.hass, SIGNAL_UPDATE.format(self.blid))
