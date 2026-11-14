"""Disegna la mappa con la posizione del robot, per allegarla agli avvisi."""
from __future__ import annotations

import io
from typing import Any


def render_png(robot_map: dict[str, Any] | None, trail: list[list[float]],
               position: dict[str, float] | None, width: int = 900) -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    rooms = (robot_map or {}).get("rooms") or []
    keepouts = (robot_map or {}).get("keepouts") or []
    pts: list[list[float]] = [[0, 0]]
    for room in rooms:
        pts.extend(room.get("polygon") or [])
    for keepout in keepouts:
        pts.extend(keepout)
    pts.extend(trail or [])
    if position:
        pts.append([position["x"], position["y"]])
    pad = 350
    x0, x1 = min(p[0] for p in pts) - pad, max(p[0] for p in pts) + pad
    y0, y1 = min(p[1] for p in pts) - pad, max(p[1] for p in pts) + pad
    scale = width / max(1.0, x1 - x0)
    height = max(1, min(2000, round((y1 - y0) * scale)))

    def to_px(x: float, y: float) -> tuple[float, float]:
        return ((x - x0) * scale, (y1 - y) * scale)

    img = Image.new("RGB", (width, height), (6, 18, 42))
    draw = ImageDraw.Draw(img)
    for room in rooms:
        poly = [to_px(*p) for p in room.get("polygon") or []]
        if len(poly) >= 3:
            draw.polygon(poly, fill=(23, 43, 77), outline=(120, 150, 190))
    for keepout in keepouts:
        poly = [to_px(*p) for p in keepout]
        if len(poly) >= 3:
            draw.polygon(poly, fill=(110, 28, 36), outline=(239, 68, 68))
    if trail and len(trail) > 1:
        draw.line([to_px(*p) for p in trail], fill=(53, 208, 165), width=3)
    try:
        font = ImageFont.load_default(size=max(14, width // 42))
    except TypeError:
        font = ImageFont.load_default()
    for room in rooms:
        poly = room.get("polygon") or []
        if not poly:
            continue
        cx = sum(p[0] for p in poly) / len(poly)
        cy = sum(p[1] for p in poly) / len(poly)
        label = str(room.get("label") or room.get("name") or "")
        px, py = to_px(cx, cy)
        try:
            draw.text((px, py), label, fill=(227, 238, 255), font=font, anchor="mm")
        except (ValueError, UnicodeEncodeError):
            draw.text((px, py), label.encode("ascii", "ignore").decode(), fill=(227, 238, 255))
    dock = to_px(0, 0)
    draw.rectangle([dock[0] - 7, dock[1] - 7, dock[0] + 7, dock[1] + 7], fill=(250, 204, 21))
    if position:
        px, py = to_px(position["x"], position["y"])
        draw.ellipse([px - 16, py - 16, px + 16, py + 16], fill=(239, 68, 68), outline=(255, 255, 255), width=3)
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()
