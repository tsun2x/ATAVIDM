"""Rule-based violation engine (manuscript Ch3, Layer 4).

Implements the rule features named in the manuscript: ROI (zone) analysis,
direction analysis, trajectory analysis, object counting, dwell-time analysis,
and time-based rules. Zone polygons come from the video/camera annotations;
tunable parameters come from system settings (DEFAULT_RULE_PARAMETERS).

Each violation fires at most once per track per violation type.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time as dtime
from typing import Any

from core.detection_config import (
    VIOLATION_OBSTRUCTION,
    VIOLATION_COUNTERFLOW,
    VIOLATION_ILLEGAL_PARKING,
    VIOLATION_ILLEGAL_TERMINAL,
    VIOLATION_NO_HELMET,
    VIOLATION_PAVEMENT_MARKINGS,
    VIOLATION_MOTORCYCLE_OVERLOADING,
    VIOLATION_TRUCK_BAN,
    DEFAULT_RULE_PARAMETERS,
    DEFAULT_ENABLED_VIOLATIONS,
    DEFAULT_TRUCK_BAN_CLASSES,
    RIDER_ASSOCIATION_PADDING,
    VIOLATION_PERSISTENCE_SEC,
    VEHICLE_CLASSES,
    YOLO_CLASS_BUS,
    YOLO_CLASS_HELMET,
    YOLO_CLASS_JEEPNEY,
    YOLO_CLASS_MOTORCYCLE,
    YOLO_CLASS_PERSON,
    YOLO_CLASS_PIAGGIO,
    YOLO_CLASS_TRICYCLE,
    YOLO_CLASS_UV_EXPRESS_VAN,
    is_truck_ban_applicable,
    vehicle_category,
)
from core.tracker import angle_difference, point_in_polygon

# Public Utility Vehicles subject to loading/unloading / illegal-terminal proxy.
# Spec terminal types: Jeepney, UV Express / Van, Tricycle, Piaggio.
# Bus remains included to preserve existing loading-zone behavior.
PUV_CLASSES = (
    YOLO_CLASS_JEEPNEY,
    YOLO_CLASS_UV_EXPRESS_VAN,
    YOLO_CLASS_TRICYCLE,
    YOLO_CLASS_PIAGGIO,
    YOLO_CLASS_BUS,
)
DEFAULT_RESTRICTED_LANE_CLASSES = (YOLO_CLASS_MOTORCYCLE, "bicycle")


@dataclass
class ViolationEvent:
    violation_type: str
    track_id: int
    confidence: float
    frame_number: int
    timestamp_sec: float
    reason_log: str
    vehicle_class: str | None = None


@dataclass
class _PersistenceTracker:
    """Tracks how long a condition has been continuously true for a track."""

    started_at: float | None = None
    def update(
        self,
        condition: bool,
        timestamp_sec: float,
        threshold_sec: float = VIOLATION_PERSISTENCE_SEC,
    ) -> bool:
        if condition:
            if self.started_at is None:
                self.started_at = timestamp_sec
            return (timestamp_sec - self.started_at) >= threshold_sec
        self.started_at = None
        return False

    def elapsed(self, timestamp_sec: float) -> float:
        if self.started_at is None:
            return 0.0
        return timestamp_sec - self.started_at


@dataclass
class RuleEngineState:
    # (rule_key, track_id) -> persistence tracker
    persistence: dict[tuple[str, int], _PersistenceTracker] = field(default_factory=dict)
    # (violation_type, track_id) pairs that already fired (one event per track)
    fired: set[tuple[str, int]] = field(default_factory=set)

    def tracker_for(self, rule_key: str, track_id: int) -> _PersistenceTracker:
        return self.persistence.setdefault((rule_key, track_id), _PersistenceTracker())

    def already_fired(self, violation_type: str, track_id: int) -> bool:
        return (violation_type, track_id) in self.fired

    def mark_fired(self, violation_type: str, track_id: int) -> None:
        self.fired.add((violation_type, track_id))


# ---------------------------------------------------------------------------
# Geometry / association helpers
# ---------------------------------------------------------------------------

def _bbox_center(det: dict[str, Any]) -> tuple[float, float]:
    x = float(det["bbox_x"]) + float(det["bbox_w"]) / 2
    y = float(det["bbox_y"]) + float(det["bbox_h"]) / 2
    return x, y


def _bottom_center(det: dict[str, Any]) -> tuple[float, float]:
    """Ground contact point — best proxy for a vehicle's road position."""
    x = float(det["bbox_x"]) + float(det["bbox_w"]) / 2
    y = float(det["bbox_y"]) + float(det["bbox_h"])
    return x, y


def _in_zone(det: dict[str, Any], polygon: list[list[float]]) -> bool:
    x, y = _bottom_center(det)
    return point_in_polygon(x, y, polygon)


def _point_in_padded_bbox(point: tuple[float, float], anchor: dict[str, Any], padding: float) -> bool:
    px, py = point
    x = float(anchor["bbox_x"])
    y = float(anchor["bbox_y"])
    w = float(anchor["bbox_w"]) * (1 + padding)
    h = float(anchor["bbox_h"]) * (1 + padding)
    ox = x - (w - float(anchor["bbox_w"])) / 2
    oy = y - (h - float(anchor["bbox_h"])) / 2
    return ox <= px <= ox + w and oy <= py <= oy + h


def _boxes_overlap(a: dict[str, Any], b: dict[str, Any]) -> bool:
    ax1, ay1 = float(a["bbox_x"]), float(a["bbox_y"])
    ax2, ay2 = ax1 + float(a["bbox_w"]), ay1 + float(a["bbox_h"])
    bx1, by1 = float(b["bbox_x"]), float(b["bbox_y"])
    bx2, by2 = bx1 + float(b["bbox_w"]), by1 + float(b["bbox_h"])
    return ax1 < bx2 and ax2 > bx1 and ay1 < by2 and ay2 > by1


def _associate_riders(
    motorcycle: dict[str, Any],
    persons: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    riders: list[dict[str, Any]] = []
    for person in persons:
        center = _bbox_center(person)
        if _point_in_padded_bbox(center, motorcycle, RIDER_ASSOCIATION_PADDING):
            riders.append(person)
    return riders


def _rider_has_helmet(rider: dict[str, Any], helmets: list[dict[str, Any]]) -> bool:
    return any(_boxes_overlap(rider, helmet) for helmet in helmets)


def _is_stationary(det: dict[str, Any], stationary_px: float) -> bool:
    speed = det.get("speed_px_per_sec")
    return speed is not None and float(speed) <= stationary_px


def _parse_clock(value: str) -> dtime:
    hour, minute = value.split(":")
    return dtime(int(hour), int(minute))


def _within_time_window(now: dtime, start: str, end: str) -> bool:
    start_t, end_t = _parse_clock(start), _parse_clock(end)
    if start_t <= end_t:
        return start_t <= now <= end_t
    return now >= start_t or now <= end_t  # window wraps past midnight


def _emit(
    state: RuleEngineState,
    events: list[ViolationEvent],
    violation_type: str,
    det: dict[str, Any],
    frame_number: int,
    reason: str,
) -> None:
    track_id = int(det["track_id"])
    if state.already_fired(violation_type, track_id):
        return
    state.mark_fired(violation_type, track_id)
    events.append(
        ViolationEvent(
            violation_type=violation_type,
            track_id=track_id,
            confidence=float(det.get("confidence", 0)),
            frame_number=frame_number,
            timestamp_sec=float(det.get("timestamp_sec", 0)),
            reason_log=reason,
            vehicle_class=vehicle_category(str(det.get("class_label", ""))),
        )
    )


# ---------------------------------------------------------------------------
# Detection-only rules (no zones required)
# ---------------------------------------------------------------------------

def check_no_helmet(
    tracked: list[dict[str, Any]],
    state: RuleEngineState,
    frame_number: int,
    params: dict[str, Any],
) -> list[ViolationEvent]:
    """
    IF motorcycle + rider detected AND helmets on track for >= persistence
    window THEN No Helmet Violation (RA 10054).
    Requires the custom model's helmet class; inert with COCO weights.
    """
    motorcycles = [d for d in tracked if d.get("class_label") == YOLO_CLASS_MOTORCYCLE]
    persons = [d for d in tracked if d.get("class_label") == YOLO_CLASS_PERSON]
    helmets = [d for d in tracked if d.get("class_label") == YOLO_CLASS_HELMET]

    events: list[ViolationEvent] = []
    for mc in motorcycles:
        track_id = int(mc["track_id"])
        riders = _associate_riders(mc, persons)
        if not riders:
            state.persistence.pop(("no_helmet", track_id), None)
            continue

        unhelmeted = [r for r in riders if not _rider_has_helmet(r, helmets)]
        
        tracker = state.tracker_for("no_helmet", track_id)
        ts = float(mc.get("timestamp_sec", 0))

        if tracker.update(len(unhelmeted) > 0, ts):
            rider_ids = ", ".join(str(int(r["track_id"])) for r in unhelmeted)
            _emit(
                state, events, VIOLATION_NO_HELMET, mc, frame_number,
                f"Motorcycle track #{track_id}: rider(s) #{rider_ids} without "
                f"helmet for >={VIOLATION_PERSISTENCE_SEC}s.",
            )
    return events


def check_motorcycle_overloading(
    tracked: list[dict[str, Any]],
    state: RuleEngineState,
    frame_number: int,
) -> list[ViolationEvent]:
    """
    IF more than two riders on a tracked motorcycle for >= persistence window
    THEN Motorcycle Overloading violation.
    """
    motorcycles = [d for d in tracked if d.get("class_label") == YOLO_CLASS_MOTORCYCLE]
    persons = [d for d in tracked if d.get("class_label") == YOLO_CLASS_PERSON]

    events: list[ViolationEvent] = []
    for mc in motorcycles:
        track_id = int(mc["track_id"])
        riders = _associate_riders(mc, persons)
        tracker = state.tracker_for("overloading", track_id)
        ts = float(mc.get("timestamp_sec", 0))

        if tracker.update(len(riders) > 2, ts):
            _emit(
                state, events, VIOLATION_MOTORCYCLE_OVERLOADING, mc, frame_number,
                f"Motorcycle track #{track_id}: {len(riders)} riders detected "
                f"(max 2) for >={VIOLATION_PERSISTENCE_SEC}s.",
            )
    return events


# ---------------------------------------------------------------------------
# Zone-based rules (ROI + dwell / direction / class / time)
# ---------------------------------------------------------------------------

def _check_zone_dwell(
    vehicles: list[dict[str, Any]],
    polygon: list[list[float]],
    state: RuleEngineState,
    frame_number: int,
    params: dict[str, Any],
    *,
    rule_key: str,
    violation_type: str,
    dwell_sec: float,
    reason_template: str,
    class_filter: tuple[str, ...] | None = None,
) -> list[ViolationEvent]:
    events: list[ViolationEvent] = []
    for det in vehicles:
        if class_filter and det.get("class_label") not in class_filter:
            continue
        track_id = int(det["track_id"])
        ts = float(det.get("timestamp_sec", 0))
        condition = _in_zone(det, polygon) and _is_stationary(det, float(params["stationary_px"]))
        tracker = state.tracker_for(rule_key, track_id)
        if tracker.update(condition, ts, dwell_sec):
            _emit(
                state, events, violation_type, det, frame_number,
                reason_template.format(track_id=track_id, dwell=dwell_sec),
            )
    return events


def check_illegal_parking(
    vehicles: list[dict[str, Any]],
    polygon: list[list[float]],
    state: RuleEngineState,
    frame_number: int,
    params: dict[str, Any],
) -> list[ViolationEvent]:
    """Zone-dwell parking rule in a No Parking zone (partial implementation)."""
    events: list[ViolationEvent] = []
    events.extend(
        _check_zone_dwell(
            vehicles, polygon, state, frame_number, params,
            rule_key="illegal_parking_stop",
            violation_type=VIOLATION_ILLEGAL_PARKING,
            dwell_sec=float(params.get("stopping_dwell_sec", 10.0)),
            reason_template="Vehicle track #{track_id} stopped in No Parking Zone for >={dwell}s.",
        )
    )
    events.extend(
        _check_zone_dwell(
            vehicles, polygon, state, frame_number, params,
            rule_key="illegal_parking_park",
            violation_type=VIOLATION_ILLEGAL_PARKING,
            dwell_sec=float(params.get("parking_dwell_sec", 30.0)),
            reason_template="Vehicle track #{track_id} parked in No Parking Zone for >={dwell}s.",
        )
    )
    return events


def check_illegal_terminal(
    vehicles: list[dict[str, Any]],
    polygon: list[list[float]],
    state: RuleEngineState,
    frame_number: int,
    params: dict[str, Any],
) -> list[ViolationEvent]:
    """PUV zone-dwell in No Loading/Unloading zone (partial; not full terminal logic)."""
    return _check_zone_dwell(
        vehicles, polygon, state, frame_number, params,
        rule_key="illegal_terminal",
        violation_type=VIOLATION_ILLEGAL_TERMINAL,
        dwell_sec=float(params.get("loading_dwell_sec", 8.0)),
        reason_template="PUV track #{track_id} stationary in No Loading/Unloading Zone for >={dwell}s.",
        class_filter=PUV_CLASSES,
    )


def check_obstruction(
    vehicles: list[dict[str, Any]],
    polygon: list[list[float]],
    state: RuleEngineState,
    frame_number: int,
    params: dict[str, Any],
) -> list[ViolationEvent]:
    """Stationary vehicle in the Active Lane -> Obstruction."""
    return _check_zone_dwell(
        vehicles, polygon, state, frame_number, params,
        rule_key="obstruction",
        violation_type=VIOLATION_OBSTRUCTION,
        dwell_sec=float(params.get("obstruction_dwell_sec", 10.0)),
        reason_template="Vehicle track #{track_id} stationary in Active Lane for >={dwell}s (Obstruction).",
    )


def check_blocking_pedestrian(
    vehicles: list[dict[str, Any]],
    polygon: list[list[float]],
    state: RuleEngineState,
    frame_number: int,
    params: dict[str, Any],
) -> list[ViolationEvent]:
    """
    Stationary vehicle on Pedestrian Crossing -> Obstruction.
    Blocking a pedestrian crossing is classified as Obstruction.
    """
    return _check_zone_dwell(
        vehicles, polygon, state, frame_number, params,
        rule_key="blocking_pedestrian",
        violation_type=VIOLATION_OBSTRUCTION,
        dwell_sec=float(params.get("crossing_block_sec", 3.0)),
        reason_template="Vehicle track #{track_id} stationary on Pedestrian Crossing for >={dwell}s (Obstruction).",
    )


def check_counterflow(
    vehicles: list[dict[str, Any]],
    polygon: list[list[float]],
    state: RuleEngineState,
    frame_number: int,
    params: dict[str, Any],
) -> list[ViolationEvent]:
    """
    Direction analysis in the Active Lane: a vehicle whose trajectory heading
    is within tolerance of the OPPOSITE of the configured lane flow direction
    for >= persistence window is counterflowing.
    """
    lane_flow = float(params.get("lane_flow_degrees", 90.0))
    tolerance = float(params.get("flow_tolerance_degrees", 60.0))
    opposite = (lane_flow + 180.0) % 360.0

    events: list[ViolationEvent] = []
    for det in vehicles:
        track_id = int(det["track_id"])
        ts = float(det.get("timestamp_sec", 0))
        heading = det.get("direction_degrees")
        condition = (
            heading is not None
            and _in_zone(det, polygon)
            and angle_difference(float(heading), opposite) <= tolerance
        )
        tracker = state.tracker_for("counterflow", track_id)
        if tracker.update(condition, ts):
            _emit(
                state, events, VIOLATION_COUNTERFLOW, det, frame_number,
                f"Vehicle track #{track_id} travelling {float(heading):.0f}deg "
                f"against lane flow ({lane_flow:.0f}deg) in Active Lane.",
            )
    return events


def check_truck_ban(
    vehicles: list[dict[str, Any]],
    polygon: list[list[float]],
    state: RuleEngineState,
    frame_number: int,
    params: dict[str, Any],
    now_time: dtime | None = None,
) -> list[ViolationEvent]:
    """Applicable commercial vehicle inside a Truck Ban Zone during the ban window.

    Applicability is controlled by ``params['truck_ban_classes']`` (default: truck
    only). ``pickup_truck`` is not automatically included.
    """
    now = now_time or datetime.now().time()
    if not _within_time_window(now, str(params.get("truck_ban_start", "06:00")), str(params.get("truck_ban_end", "09:00"))):
        return []

    ban_classes = params.get("truck_ban_classes", DEFAULT_TRUCK_BAN_CLASSES)
    if isinstance(ban_classes, str):
        ban_classes = (ban_classes,)

    events: list[ViolationEvent] = []
    for det in vehicles:
        label = str(det.get("class_label") or "")
        if not is_truck_ban_applicable(label, ban_classes):
            continue
        track_id = int(det["track_id"])
        ts = float(det.get("timestamp_sec", 0))
        tracker = state.tracker_for("truck_ban", track_id)
        if tracker.update(_in_zone(det, polygon), ts):
            _emit(
                state, events, VIOLATION_TRUCK_BAN, det, frame_number,
                f"{label.title()} track #{track_id} inside Truck Ban Zone during ban window "
                f"({params['truck_ban_start']}-{params['truck_ban_end']}).",
            )
    return events


def check_restricted_lane(
    vehicles: list[dict[str, Any]],
    polygon: list[list[float]],
    state: RuleEngineState,
    frame_number: int,
    params: dict[str, Any],
) -> list[ViolationEvent]:
    """
    Prohibited vehicle class travelling inside a Restricted Lane.
    Mapped to Failure to Follow Road/Pavement Markings.
    """
    restricted = tuple(params.get("restricted_lane_classes", DEFAULT_RESTRICTED_LANE_CLASSES))
    events: list[ViolationEvent] = []
    for det in vehicles:
        if det.get("class_label") not in restricted:
            continue
        track_id = int(det["track_id"])
        ts = float(det.get("timestamp_sec", 0))
        tracker = state.tracker_for("restricted_lane", track_id)
        if tracker.update(_in_zone(det, polygon), ts):
            _emit(
                state, events, VIOLATION_PAVEMENT_MARKINGS, det, frame_number,
                f"{det.get('class_label', 'vehicle').title()} track #{track_id} "
                f"inside Restricted Lane.",
            )
    return events


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def evaluate_detection_rules(
    tracked: list[dict[str, Any]],
    state: RuleEngineState,
    frame_number: int,
    zones: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    now_time: dtime | None = None,
    enabled_violations: tuple[str, ...] | None = None,
) -> list[ViolationEvent]:
    """
    Run every rule on the current frame's tracked detections.

    ``tracked`` must be the output of TrackState.update() (motion-annotated).
    ``zones`` maps zone keys to pixel polygons (see core.zone_config).
    ``params`` overrides DEFAULT_RULE_PARAMETERS (from system settings).
    ``enabled_violations`` filters which violations to evaluate.
    If None, ``DEFAULT_ENABLED_VIOLATIONS`` is used (global toggle default).
    """
    merged = dict(DEFAULT_RULE_PARAMETERS)
    if params:
        merged.update(params)
    zones = zones or {}

    enabled_set = set(enabled_violations) if enabled_violations is not None else set(DEFAULT_ENABLED_VIOLATIONS)
    
    vehicles = [d for d in tracked if d.get("class_label") in VEHICLE_CLASSES]

    events: list[ViolationEvent] = []
    
    # Run detection-only rules
    if VIOLATION_NO_HELMET in enabled_set:
        events.extend(check_no_helmet(tracked, state, frame_number, merged))
    if VIOLATION_MOTORCYCLE_OVERLOADING in enabled_set:
        events.extend(check_motorcycle_overloading(tracked, state, frame_number))

    # Run zone-based rules
    if zones.get("no_parking") and VIOLATION_ILLEGAL_PARKING in enabled_set:
        events.extend(check_illegal_parking(vehicles, zones["no_parking"], state, frame_number, merged))
    if zones.get("active_lane"):
        if VIOLATION_OBSTRUCTION in enabled_set:
            events.extend(check_obstruction(vehicles, zones["active_lane"], state, frame_number, merged))
        if VIOLATION_COUNTERFLOW in enabled_set:
            events.extend(check_counterflow(vehicles, zones["active_lane"], state, frame_number, merged))
    if zones.get("pedestrian_crossing") and VIOLATION_OBSTRUCTION in enabled_set:
        events.extend(check_blocking_pedestrian(vehicles, zones["pedestrian_crossing"], state, frame_number, merged))
    if zones.get("truck_ban_zone") and VIOLATION_TRUCK_BAN in enabled_set:
        events.extend(check_truck_ban(vehicles, zones["truck_ban_zone"], state, frame_number, merged, now_time))
    if zones.get("loading_unloading") and VIOLATION_ILLEGAL_TERMINAL in enabled_set:
        events.extend(check_illegal_terminal(vehicles, zones["loading_unloading"], state, frame_number, merged))
    if zones.get("restricted_lane") and VIOLATION_PAVEMENT_MARKINGS in enabled_set:
        events.extend(check_restricted_lane(vehicles, zones["restricted_lane"], state, frame_number, merged))

    return events