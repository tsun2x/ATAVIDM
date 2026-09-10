"""Versioned scene-annotation contract (v2) with legacy polygon projection.

Legacy polygon-only documents remain readable unchanged. Structured v2
documents preserve unknown fields on safe round-trips. Reading never
silently rewrites stored annotations.
"""

from __future__ import annotations

import copy
import json
import math
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping

SCHEMA_VERSION_V2 = 2

LEGACY_ZONE_KEYS: tuple[str, ...] = (
    "no_parking",
    "active_lane",
    "pedestrian_crossing",
    "truck_ban_zone",
    "loading_unloading",
    "restricted_lane",
)

ZONE_OBJECT_TYPES = frozenset(LEGACY_ZONE_KEYS)
LANE_OBJECT_TYPE = "active_lane"
FLOW_ARROW_TYPE = "lane_flow"
THRESHOLD_LINE_TYPES = frozenset({"threshold", "no_entry_threshold"})
MARKING_TYPES = frozenset(
    {
        "double_solid",
        "single_solid",
        "solid_broken",
        "restricted_lane_boundary",
        "generic_marking",
    }
)
SIGN_TYPES = frozenset(
    {
        "no_entry",
        "no_left_turn",
        "no_right_turn",
        "no_u_turn",
        "no_overtaking",
        "generic_sign",
    }
)
ACTIVITY_REGION_TYPES = frozenset({"passenger_activity", "boarding_alighting"})
PROHIBITED_FROM_VALUES = frozenset({"left", "right", "both"})

V2_ARRAY_KEYS: tuple[str, ...] = (
    "zones",
    "lanes",
    "flow_arrows",
    "threshold_lines",
    "markings",
    "signs",
    "activity_regions",
)


class SceneAnnotationError(ValueError):
    """Invalid scene-annotation document."""


def _finite(value: Any, *, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise SceneAnnotationError(f"{label} must be a finite number.") from exc
    if not math.isfinite(number):
        raise SceneAnnotationError(f"{label} must be a finite number.")
    return number


def _point(raw: Any, *, label: str) -> list[float]:
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        raise SceneAnnotationError(f"{label} must be [x, y].")
    return [_finite(raw[0], label=f"{label}.x"), _finite(raw[1], label=f"{label}.y")]


def _polyline(raw: Any, *, label: str, min_points: int = 2) -> list[list[float]]:
    if not isinstance(raw, list) or len(raw) < min_points:
        raise SceneAnnotationError(
            f"{label} must be a list of at least {min_points} points."
        )
    return [_point(pt, label=f"{label}[{idx}]") for idx, pt in enumerate(raw)]


def _polygon(raw: Any, *, label: str) -> list[list[float]]:
    return _polyline(raw, label=label, min_points=3)


def _stable_id(raw: Any | None = None) -> str:
    if raw is None or raw == "":
        return str(uuid.uuid4())
    text = str(raw).strip()
    if not text:
        raise SceneAnnotationError("Object id must be a non-empty string.")
    return text


def _optional_string_list(raw: Any, *, label: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(x, str) and x for x in raw):
        raise SceneAnnotationError(f"{label} must be a list of non-empty strings.")
    return list(raw)


@dataclass(frozen=True)
class ScenePoint:
    x: float
    y: float

    def as_list(self) -> list[float]:
        return [self.x, self.y]


@dataclass(frozen=True)
class IdentifiedSceneObject:
    id: str
    type: str
    points: tuple[tuple[float, float], ...] = ()
    lane_ids: tuple[str, ...] = ()
    prohibited_from: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def points_list(self) -> list[list[float]]:
        return [[x, y] for x, y in self.points]


@dataclass
class SceneAnnotation:
    """In-memory scene document (legacy or v2)."""

    schema_version: int | None
    zones: list[IdentifiedSceneObject] = field(default_factory=list)
    lanes: list[IdentifiedSceneObject] = field(default_factory=list)
    flow_arrows: list[IdentifiedSceneObject] = field(default_factory=list)
    threshold_lines: list[IdentifiedSceneObject] = field(default_factory=list)
    markings: list[IdentifiedSceneObject] = field(default_factory=list)
    signs: list[IdentifiedSceneObject] = field(default_factory=list)
    activity_regions: list[IdentifiedSceneObject] = field(default_factory=list)
    # Legacy polygon map when document is polygon-only (schema_version is None).
    legacy_polygons: dict[str, list[list[float]]] = field(default_factory=dict)
    unknown_fields: dict[str, Any] = field(default_factory=dict)
    source_kind: str = "legacy"  # "legacy" | "v2"

    def is_v2(self) -> bool:
        return self.source_kind == "v2"

    def all_objects(self) -> list[IdentifiedSceneObject]:
        return (
            list(self.zones)
            + list(self.lanes)
            + list(self.flow_arrows)
            + list(self.threshold_lines)
            + list(self.markings)
            + list(self.signs)
            + list(self.activity_regions)
        )

    def legacy_zones(self) -> dict[str, list[list[float]]]:
        """Explicit legacy projection with exactly the six canonical keys."""
        out: dict[str, list[list[float]]] = {key: [] for key in LEGACY_ZONE_KEYS}
        if self.source_kind == "legacy":
            for key in LEGACY_ZONE_KEYS:
                pts = self.legacy_polygons.get(key) or []
                out[key] = [list(pt) for pt in pts]
            return out

        # Prefer dedicated zone objects; fall back to first lane polygon for
        # active_lane only when no zone of that type exists (projection aid —
        # rules that need lane identity must use RuleSceneContext.lanes).
        for obj in self.zones:
            if obj.type in out and not out[obj.type]:
                out[obj.type] = obj.points_list()
        if not out["active_lane"] and self.lanes:
            # Do NOT collapse multiple lanes into one ambiguous polygon for
            # identity-aware rules. Projection exposes the first lane only as
            # a compatibility silhouette; callers needing identity use lanes.
            out["active_lane"] = self.lanes[0].points_list()
        if not out["restricted_lane"]:
            for marking in self.markings:
                if marking.type == "restricted_lane_boundary" and len(marking.points) >= 3:
                    out["restricted_lane"] = marking.points_list()
                    break
        return out

    def to_rule_context(self) -> "RuleSceneContext":
        return project_rule_scene_context(self)

    def to_storage_dict(self) -> dict[str, Any]:
        if self.source_kind == "legacy":
            return {key: list(self.legacy_polygons.get(key) or []) for key in LEGACY_ZONE_KEYS}

        doc: dict[str, Any] = {"schema_version": SCHEMA_VERSION_V2}
        for key in V2_ARRAY_KEYS:
            objects: list[IdentifiedSceneObject] = getattr(self, key)
            doc[key] = [_object_to_dict(obj) for obj in objects]
        for key, value in self.unknown_fields.items():
            if key in doc or key == "schema_version":
                continue
            doc[key] = copy.deepcopy(value)
        return doc


@dataclass(frozen=True)
class LaneFlow:
    lane_id: str
    degrees: float
    vector: tuple[float, float]
    arrow_id: str | None = None


@dataclass(frozen=True)
class RuleSceneContext:
    """Engine-facing projection shared by uploaded-video and live paths."""

    legacy_zones: Mapping[str, list[list[float]]]
    lanes: tuple[IdentifiedSceneObject, ...]
    lane_flows: tuple[LaneFlow, ...]
    signs: tuple[IdentifiedSceneObject, ...]
    markings: tuple[IdentifiedSceneObject, ...]
    threshold_lines: tuple[IdentifiedSceneObject, ...]
    activity_regions: tuple[IdentifiedSceneObject, ...]
    schema_version: int | None = None

    def zone(self, key: str) -> list[list[float]]:
        return list(self.legacy_zones.get(key) or [])


def _object_to_dict(obj: IdentifiedSceneObject) -> dict[str, Any]:
    data = {
        "id": obj.id,
        "type": obj.type,
        "points": obj.points_list(),
    }
    if obj.lane_ids:
        data["lane_ids"] = list(obj.lane_ids)
    if obj.prohibited_from is not None:
        data["prohibited_from"] = obj.prohibited_from
    for key, value in obj.metadata.items():
        if key in data:
            continue
        data[key] = copy.deepcopy(value)
    return data


def _parse_object(
    raw: Any,
    *,
    allowed_types: frozenset[str] | set[str],
    min_points: int,
    require_prohibited_from: bool = False,
) -> IdentifiedSceneObject:
    if not isinstance(raw, dict):
        raise SceneAnnotationError("Scene objects must be JSON objects.")
    obj_type = raw.get("type")
    if obj_type not in allowed_types:
        raise SceneAnnotationError(f"Unsupported object type: {obj_type!r}.")
    points = _polyline(raw.get("points"), label=f"{obj_type}.points", min_points=min_points)
    lane_ids = tuple(_optional_string_list(raw.get("lane_ids"), label="lane_ids"))
    prohibited_from = raw.get("prohibited_from")
    if prohibited_from is not None:
        if prohibited_from not in PROHIBITED_FROM_VALUES:
            raise SceneAnnotationError(
                "prohibited_from must be 'left', 'right', or 'both'."
            )
    elif require_prohibited_from:
        raise SceneAnnotationError(f"{obj_type} requires prohibited_from.")

    known = {"id", "type", "points", "lane_ids", "prohibited_from"}
    metadata = {k: copy.deepcopy(v) for k, v in raw.items() if k not in known}
    return IdentifiedSceneObject(
        id=_stable_id(raw.get("id")),
        type=str(obj_type),
        points=tuple((p[0], p[1]) for p in points),
        lane_ids=lane_ids,
        prohibited_from=prohibited_from,
        metadata=metadata,
    )


def _is_v2_document(data: dict[str, Any]) -> bool:
    if data.get("schema_version") == SCHEMA_VERSION_V2:
        return True
    # Structured documents without explicit version still treated as v2 when
    # any array key is present as a list of objects.
    return any(isinstance(data.get(key), list) and key in V2_ARRAY_KEYS for key in ("lanes", "signs", "markings", "flow_arrows", "threshold_lines", "activity_regions"))


def _is_legacy_polygon_document(data: dict[str, Any]) -> bool:
    if data.get("schema_version") == SCHEMA_VERSION_V2:
        return False
    if any(key in data for key in V2_ARRAY_KEYS if key != "zones"):
        # zones alone can appear in both forms; other arrays imply structured.
        if any(isinstance(data.get(key), list) for key in V2_ARRAY_KEYS if key != "zones"):
            return False
    # Legacy: values for zone keys are point lists (or absent).
    for key, value in data.items():
        if key in LEGACY_ZONE_KEYS:
            if value in (None, []):
                continue
            if not isinstance(value, list):
                return False
            if value and isinstance(value[0], dict):
                return False
            continue
        if key in V2_ARRAY_KEYS or key == "schema_version":
            continue
        # Unknown top-level keys are allowed on legacy round-trip only if we
        # treat the document as structured; for classic polygon docs ignore.
    return True


def empty_v2_document() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION_V2,
        **{key: [] for key in V2_ARRAY_KEYS},
    }


def empty_legacy_zones() -> dict[str, list[list[float]]]:
    return {key: [] for key in LEGACY_ZONE_KEYS}


def load_scene_annotation(raw: str | dict[str, Any] | None) -> SceneAnnotation:
    """Parse a stored annotation without rewriting storage format."""
    if raw is None or raw == "":
        return SceneAnnotation(
            schema_version=None,
            legacy_polygons=empty_legacy_zones(),
            source_kind="legacy",
        )
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SceneAnnotationError("zones_json must be valid JSON.") from exc
    if not isinstance(data, dict):
        raise SceneAnnotationError("zones_json must be a JSON object.")

    if data.get("schema_version") not in (None, SCHEMA_VERSION_V2):
        raise SceneAnnotationError(
            f"Unsupported schema_version: {data.get('schema_version')!r}."
        )

    if _is_v2_document(data) or data.get("schema_version") == SCHEMA_VERSION_V2:
        return _load_v2(data)
    if not _is_legacy_polygon_document(data):
        # Ambiguous/malformed structured document without proper objects.
        raise SceneAnnotationError("Malformed scene annotation document.")
    return _load_legacy(data)


def _load_legacy(data: dict[str, Any]) -> SceneAnnotation:
    polygons = empty_legacy_zones()
    unknown: dict[str, Any] = {}
    for key, value in data.items():
        if key in LEGACY_ZONE_KEYS:
            if value in (None, []):
                polygons[key] = []
                continue
            if not isinstance(value, list):
                raise SceneAnnotationError(f"Zone '{key}' must be a list of points.")
            polygons[key] = [_point(pt, label=f"{key}[{i}]") for i, pt in enumerate(value)]
        elif key == "no_loading":
            # Never project or persist as no_loading. Canonical key is
            # loading_unloading; unknown alias is dropped (same as historic parser).
            continue
        else:
            unknown[key] = copy.deepcopy(value)
    return SceneAnnotation(
        schema_version=None,
        legacy_polygons=polygons,
        unknown_fields=unknown,
        source_kind="legacy",
    )


def _load_v2(data: dict[str, Any]) -> SceneAnnotation:
    known_top = {"schema_version", *V2_ARRAY_KEYS}
    unknown = {
        key: copy.deepcopy(value)
        for key, value in data.items()
        if key not in known_top
    }

    def _array(key: str) -> list[Any]:
        raw = data.get(key, [])
        if raw is None:
            return []
        if not isinstance(raw, list):
            raise SceneAnnotationError(f"{key} must be a list.")
        return raw

    zones = [
        _parse_object(item, allowed_types=ZONE_OBJECT_TYPES, min_points=3)
        for item in _array("zones")
    ]
    lanes = [
        _parse_object(item, allowed_types={LANE_OBJECT_TYPE}, min_points=3)
        for item in _array("lanes")
    ]
    flow_arrows = [
        _parse_object(item, allowed_types={FLOW_ARROW_TYPE}, min_points=2)
        for item in _array("flow_arrows")
    ]
    threshold_lines = [
        _parse_object(item, allowed_types=THRESHOLD_LINE_TYPES, min_points=2)
        for item in _array("threshold_lines")
    ]
    markings_raw = _array("markings")
    markings: list[IdentifiedSceneObject] = []
    for item in markings_raw:
        require_pf = isinstance(item, dict) and item.get("type") == "double_solid"
        markings.append(
            _parse_object(
                item,
                allowed_types=MARKING_TYPES,
                min_points=2,
                require_prohibited_from=require_pf,
            )
        )
    signs = [
        _parse_object(item, allowed_types=SIGN_TYPES, min_points=2)
        for item in _array("signs")
    ]
    activity_regions = [
        _parse_object(item, allowed_types=ACTIVITY_REGION_TYPES, min_points=3)
        for item in _array("activity_regions")
    ]

    scene = SceneAnnotation(
        schema_version=SCHEMA_VERSION_V2,
        zones=zones,
        lanes=lanes,
        flow_arrows=flow_arrows,
        threshold_lines=threshold_lines,
        markings=markings,
        signs=signs,
        activity_regions=activity_regions,
        unknown_fields=unknown,
        source_kind="v2",
    )
    _validate_unique_ids(scene)
    _validate_references(scene)
    return scene


def _validate_unique_ids(scene: SceneAnnotation) -> None:
    seen: set[str] = set()
    for obj in scene.all_objects():
        if obj.id in seen:
            raise SceneAnnotationError(f"Duplicate scene object id: {obj.id!r}.")
        seen.add(obj.id)


def _validate_references(scene: SceneAnnotation) -> None:
    lane_ids = {lane.id for lane in scene.lanes}
    for obj in scene.all_objects():
        for lane_id in obj.lane_ids:
            if lane_id not in lane_ids:
                raise SceneAnnotationError(
                    f"Object {obj.id!r} references unknown lane_id {lane_id!r}."
                )
    for arrow in scene.flow_arrows:
        if not arrow.lane_ids:
            raise SceneAnnotationError(
                f"Flow arrow {arrow.id!r} must reference a lane_id."
            )


def dumps_scene_annotation(scene: SceneAnnotation) -> str:
    return json.dumps(scene.to_storage_dict())


def serialize_scene_raw(raw: str | dict[str, Any] | None) -> str:
    """Validate and serialize without converting legacy↔v2."""
    return dumps_scene_annotation(load_scene_annotation(raw))


def project_rule_scene_context(scene: SceneAnnotation) -> RuleSceneContext:
    legacy = scene.legacy_zones()
    flows: list[LaneFlow] = []
    if scene.is_v2():
        arrows_by_lane: dict[str, IdentifiedSceneObject] = {}
        for arrow in scene.flow_arrows:
            for lane_id in arrow.lane_ids:
                arrows_by_lane.setdefault(lane_id, arrow)
        for lane in scene.lanes:
            arrow = arrows_by_lane.get(lane.id)
            if arrow is None or len(arrow.points) < 2:
                continue
            (x0, y0), (x1, y1) = arrow.points[0], arrow.points[-1]
            dx, dy = x1 - x0, y1 - y0
            if dx == 0 and dy == 0:
                continue
            degrees = math.degrees(math.atan2(dy, dx)) % 360.0
            length = math.hypot(dx, dy) or 1.0
            flows.append(
                LaneFlow(
                    lane_id=lane.id,
                    degrees=degrees,
                    vector=(dx / length, dy / length),
                    arrow_id=arrow.id,
                )
            )
    return RuleSceneContext(
        legacy_zones=legacy,
        lanes=tuple(scene.lanes),
        lane_flows=tuple(flows),
        signs=tuple(scene.signs),
        markings=tuple(scene.markings),
        threshold_lines=tuple(scene.threshold_lines),
        activity_regions=tuple(scene.activity_regions),
        schema_version=scene.schema_version,
    )


def merge_rule_scene_inputs(
    *,
    scene: RuleSceneContext | None = None,
    zones: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> RuleSceneContext:
    """Resolve scene context; reject conflicting structured vs legacy inputs."""
    params = params or {}
    param_signs = params.get("supported_signs")
    param_markings = params.get("marking_geometry")

    if scene is not None:
        if zones:
            # Compatibility zones must match the scene projection when both given.
            projected = dict(scene.legacy_zones)
            for key, value in zones.items():
                if not value:
                    continue
                if key not in projected:
                    raise SceneAnnotationError(
                        f"Conflicting legacy zone key {key!r} with structured scene."
                    )
                if projected.get(key) and list(projected[key]) != list(value):
                    raise SceneAnnotationError(
                        "Conflicting structured scene and legacy zone inputs."
                    )
        if param_signs and scene.signs:
            raise SceneAnnotationError(
                "Conflicting supported_signs parameter and scene.signs."
            )
        if param_markings and scene.markings:
            raise SceneAnnotationError(
                "Conflicting marking_geometry parameter and scene.markings."
            )
        return scene

    legacy = {key: list((zones or {}).get(key) or []) for key in LEGACY_ZONE_KEYS}
    # Params-only structured inputs are not the normal path; expose empty
    # structured collections and leave params for fail-closed compatibility.
    return RuleSceneContext(
        legacy_zones=legacy,
        lanes=(),
        lane_flows=(),
        signs=(),
        markings=(),
        threshold_lines=(),
        activity_regions=(),
        schema_version=None,
    )
