"""Zone type registry and JSON validation for traffic annotations."""

from __future__ import annotations

import json
from typing import Any

# Manuscript ROI examples (Ch3): No Parking Zones, Loading and Unloading Areas,
# Truck Ban Areas, Pedestrian Crossings, Restricted Road Segments / Lanes.
ZONE_TYPES: dict[str, dict[str, str]] = {
    "no_parking": {"label": "No Parking Zone", "color": "#e63946"},
    "active_lane": {"label": "Active Lane", "color": "#3b82f6"},
    "pedestrian_crossing": {"label": "Pedestrian Crossing", "color": "#22c55e"},
    "truck_ban_zone": {"label": "Truck Ban Zone", "color": "#f59e0b"},
    "loading_unloading": {"label": "No Loading/Unloading Zone", "color": "#8b5cf6"},
    "restricted_lane": {"label": "Restricted Lane", "color": "#ec4899"},
}

REQUIRED_ZONE_KEYS: list[str] = list(ZONE_TYPES.keys())

VIDEO_STATUSES = ("uploaded", "annotating", "ready", "processing", "processed")


def empty_zones() -> dict[str, list[list[float]]]:
    return {key: [] for key in REQUIRED_ZONE_KEYS}


def zones_for_api() -> list[dict[str, str]]:
    return [
        {"key": key, "label": meta["label"], "color": meta["color"]}
        for key, meta in ZONE_TYPES.items()
    ]


def _normalize_point(point: Any) -> list[float]:
    if not isinstance(point, (list, tuple)) or len(point) < 2:
        raise ValueError("Each polygon point must be [x, y].")
    return [float(point[0]), float(point[1])]


def parse_zones_json(raw: str | dict[str, Any] | None) -> dict[str, list[list[float]]]:
    if raw is None or raw == "":
        return empty_zones()
    if isinstance(raw, dict):
        data = raw
    else:
        data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("zones_json must be a JSON object.")

    normalized = empty_zones()
    for key, points in data.items():
        if key not in ZONE_TYPES:
            continue
        if not isinstance(points, list):
            raise ValueError(f"Zone '{key}' must be a list of points.")
        normalized[key] = [_normalize_point(pt) for pt in points]
    return normalized


def dumps_zones(zones: dict[str, Any]) -> str:
    parsed = parse_zones_json(zones)
    return json.dumps(parsed)


def zones_complete(zones: dict[str, Any], min_points: int = 3) -> bool:
    """At least one zone must be drawn; every drawn zone needs >= min_points."""
    parsed = parse_zones_json(zones)
    drawn = [pts for pts in parsed.values() if pts]
    if not drawn:
        return False
    return all(len(pts) >= min_points for pts in drawn)
