"""Zone type registry and JSON validation for traffic annotations.

Legacy polygon helpers remain for compatibility. Structured v2 documents
must go through ``core.scene_annotation`` so unknown/v2 fields are not
silently destroyed on write.
"""

from __future__ import annotations

import json
from typing import Any

from core.scene_annotation import (
    LEGACY_ZONE_KEYS,
    SceneAnnotationError,
    dumps_scene_annotation,
    empty_legacy_zones,
    load_scene_annotation,
    serialize_scene_raw,
)

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
    return empty_legacy_zones()


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
    """Return the explicit legacy six-key polygon projection.

    This does not rewrite storage. For round-trip-safe serialization of v2
    documents use ``dumps_zones`` / ``serialize_scene_raw``.
    """
    try:
        scene = load_scene_annotation(raw)
    except SceneAnnotationError as exc:
        raise ValueError(str(exc)) from exc
    return scene.legacy_zones()


def dumps_zones(zones: dict[str, Any] | str | None) -> str:
    """Serialize annotations without converting legacy↔v2 or dropping v2 fields."""
    try:
        return serialize_scene_raw(zones)
    except SceneAnnotationError as exc:
        raise ValueError(str(exc)) from exc


def zones_complete(zones: dict[str, Any], min_points: int = 3) -> bool:
    """At least one zone must be drawn; every drawn zone needs >= min_points."""
    parsed = parse_zones_json(zones)
    drawn = [pts for pts in parsed.values() if pts]
    if not drawn:
        return False
    return all(len(pts) >= min_points for pts in drawn)


def loads_scene(raw: str | dict[str, Any] | None):
    """Load a SceneAnnotation (legacy or v2)."""
    return load_scene_annotation(raw)


def dumps_scene(scene) -> str:
    return dumps_scene_annotation(scene)
