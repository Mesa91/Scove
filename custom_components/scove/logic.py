"""Logica pura di Scove (senza Home Assistant), così si può provare da sola."""
from __future__ import annotations

import math
from typing import Any

from .const import ERRORS, PARTS

CELL_MM = 150


def runtime_hours(reported: dict[str, Any]) -> float:
    """Ore di lavoro totali del robot.

    La serie i/s/j le riporta in `runtimeStats`, la serie 900 in `bbrun`.
    """
    for key in ("runtimeStats", "bbrun"):
        block = reported.get(key)
        if isinstance(block, dict) and block.get("hr") is not None:
            return float(block.get("hr") or 0) + float(block.get("min") or 0) / 60.0
    return 0.0


def battery_cycles(reported: dict[str, Any]) -> int | None:
    """Cicli di ricarica: `bbchg3` sulla serie 900, `batInfo.cCount` sulla serie i/s/j."""
    chg = reported.get("bbchg3") or {}
    if "nLithChrg" in chg:
        return int((chg.get("nLithChrg") or 0) + (chg.get("nNimhChrg") or 0))
    count = (reported.get("batInfo") or {}).get("cCount")
    return int(count) if count is not None else None


def parts_status(now_hours: float, baselines: dict[str, float]) -> dict[str, dict[str, Any]]:
    """Ore rimaste e livello (ok / soon / due) per ogni componente."""
    out: dict[str, dict[str, Any]] = {}
    for key, part in PARTS.items():
        used = max(0.0, now_hours - baselines.get(key, now_hours))
        remaining = part["hours"] - used
        ratio = used / part["hours"]
        level = "due" if remaining <= 0 else "soon" if ratio >= 0.8 else "ok"
        out[key] = {
            "name": part["name"], "interval": part["hours"], "todo": part["todo"],
            "routine": bool(part.get("routine")),
            "remaining": round(remaining, 1), "used_pct": round(min(ratio, 1.5) * 100),
            "level": level,
        }
    return out


def battery_retention(reported: dict[str, Any], peak: float | None) -> float | None:
    """Capacità stimata della batteria rispetto al valore più alto mai visto da Scove."""
    est = (reported.get("bbchg3") or {}).get("estCap")
    if not est or not peak:
        return None
    return round(min(100.0, est / peak * 100.0), 1)


def health_score(
    reported: dict[str, Any], parts: dict[str, dict], connected: bool, peak: float | None = None
) -> int:
    score = 100.0
    for p in parts.values():
        if p.get("routine"):
            score -= 2 if p["level"] == "due" else 0
        else:
            score -= 8 if p["level"] == "due" else 3 if p["level"] == "soon" else 0
    ret = battery_retention(reported, peak)
    if ret is not None and ret < 80:
        score -= min(25.0, (80 - ret) * 0.8)
    if (reported.get("cleanMissionStatus") or {}).get("error"):
        score -= 15
    rssi = (reported.get("signal") or {}).get("rssi")
    if isinstance(rssi, (int, float)) and rssi < -75:
        score -= 5
    if not connected:
        score -= 10
    return int(max(0, min(100, round(score))))


def error_text(code: int) -> str:
    return ERRORS.get(int(code), f"Errore {code}")


def transform(x: float, y: float, cal: dict[str, Any]) -> tuple[float, float]:
    """Porta una posizione del robot (mm, origine sulla base) nel sistema della mappa."""
    if cal.get("mirror"):
        y = -y
    a = math.radians(float(cal.get("rotation") or 0))
    xr = x * math.cos(a) - y * math.sin(a)
    yr = x * math.sin(a) + y * math.cos(a)
    return xr + float(cal.get("dx") or 0), yr + float(cal.get("dy") or 0)


def cell_of(x: float, y: float) -> str:
    return f"{math.floor(x / CELL_MM)},{math.floor(y / CELL_MM)}"


def point_in_polygon(x: float, y: float, polygon: list[list[float]]) -> bool:
    inside = False
    n = len(polygon)
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def schedule_due(schedule: dict[str, Any], weekday: int, hhmm: str) -> bool:
    """weekday: 0 = lunedì."""
    return bool(
        schedule.get("enabled", True)
        and weekday in (schedule.get("days") or [])
        and schedule.get("time") == hhmm
    )


def next_run(schedules: list[dict[str, Any]], now: Any) -> Any | None:
    """Prossima partenza programmata (datetime) entro 8 giorni."""
    import datetime as dt

    best = None
    for s in schedules:
        if not s.get("enabled", True) or not s.get("time"):
            continue
        try:
            hh, mm = (int(v) for v in s["time"].split(":"))
        except ValueError:
            continue
        for add in range(8):
            day = now + dt.timedelta(days=add)
            if day.weekday() not in (s.get("days") or []):
                continue
            cand = day.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if cand > now and (best is None or cand < best):
                best = cand
    return best


def room_at(x: float, y: float, rooms: list[dict[str, Any]]) -> dict[str, Any] | None:
    """La stanza della mappa che contiene il punto."""
    for room in rooms:
        polygon = room.get("polygon") or []
        if len(polygon) >= 3 and point_in_polygon(x, y, polygon):
            return room
    return None


def room_days(rooms: list[dict[str, Any]], last: dict[str, str], since: dict[str, str], now: Any) -> list[dict[str, Any]]:
    """Per ogni stanza: giorni dall'ultima pulizia completata (o da quando la teniamo d'occhio)."""
    import datetime as dt

    out = []
    for room in rooms:
        stamp = last.get(room["id"]) or since.get(room["id"])
        days = None
        if stamp:
            try:
                days = max(0, (now - dt.datetime.fromisoformat(stamp)).days)
            except (ValueError, TypeError):
                days = None
        out.append({
            "id": room["id"], "label": room.get("label") or room.get("name"),
            "days": days, "never": room["id"] not in last,
        })
    return out


def stale_rooms(days: list[dict[str, Any]], limit: int) -> list[str]:
    if limit <= 0:
        return []
    return [d["label"] for d in days if d["days"] is not None and d["days"] >= limit]
