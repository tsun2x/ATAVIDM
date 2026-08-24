"""Rule-based violation engine (manuscript Ch3, Layer 4).

Canonical 12-violation roster. Detection confidence is never copied wholesale
into violation confidence. Track/rule state expires with the tracker policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time as dtime
from typing import Any, Callable

from core.detection_config import (
    CANONICAL_VIOLATIONS,
    DEFAULT_ENABLED_VIOLATIONS,
    DEFAULT_RULE_PARAMETERS,
    DEFAULT_TRUCK_BAN_CLASSES,
    RIDER_ASSOCIATION_PADDING,
    VEHICLE_CLASSES,
    VIOLATION_CARGO_PASSENGERS,
    VIOLATION_COUNTERFLOW,
    VIOLATION_DISREGARDING_SIGN,
    VIOLATION_ILLEGAL_PARKING,
    VIOLATION_ILLEGAL_TERMINAL,
    VIOLATION_MOTORCYCLE_OVERLOADING,
    VIOLATION_NO_HELMET,
    VIOLATION_NO_SIDE_MIRROR,
    VIOLATION_OBSTRUCTION,
    VIOLATION_PAVEMENT_MARKINGS,
    VIOLATION_PERSISTENCE_SEC,
    VIOLATION_SUBSTANDARD_HELMET,
    VIOLATION_TRUCK_BAN,
    YOLO_CLASS_BUS,
    YOLO_CLASS_HELMET,
    YOLO_CLASS_HELMET_ACCEPTABLE,
    YOLO_CLASS_HELMET_NUT_SHELL,
    YOLO_CLASS_JEEPNEY,
    YOLO_CLASS_MOTORCYCLE,
    YOLO_CLASS_PERSON,
    YOLO_CLASS_AUTORICKSHAW,
    YOLO_CLASS_PICKUP_TRUCK,
    YOLO_CLASS_RIDER,
    YOLO_CLASS_SIDE_MIRROR,
    YOLO_CLASS_TRICYCLE,
    YOLO_CLASS_TRUCK,
    YOLO_CLASS_VAN,
    is_cargo_passenger_applicable,
    is_truck_ban_applicable,
    vehicle_category,
)
from core.geometry_profile import GeometryProfile, build_geometry_profile
from core.model_capability import assess_rule_capability, classes_satisfy_rule
from core.rule_confidence import legacy_confidence_mapping, score_from_persistence, score_violation
from core.rule_types import (
    MembershipState,
    ProcessingDiagnostics,
    RuleCapabilityStatus,
    TriState,
    ViolationScore,
)
from core.tracker import TRACK_EXPIRY_SEC, angle_difference
from core.zone_membership import (
    POLICY_LANE,
    POLICY_STATIONARY,
    MembershipHysteresis,
    evaluate_zone_membership,
    is_inside_for_rule,
)

# Spec terminal-applicable visual types. Van needs additional public/for-hire
# context; without it the terminal context stays UNKNOWN (fail-closed).
PUV_CLASSES = (
    YOLO_CLASS_JEEPNEY,
    YOLO_CLASS_VAN,
    YOLO_CLASS_TRICYCLE,
    YOLO_CLASS_AUTORICKSHAW,
    YOLO_CLASS_BUS,
)
TERMINAL_STRICT_CLASSES = (
    YOLO_CLASS_JEEPNEY,
    YOLO_CLASS_TRICYCLE,
    YOLO_CLASS_AUTORICKSHAW,
)
DEFAULT_RESTRICTED_LANE_CLASSES = (YOLO_CLASS_MOTORCYCLE, "bicycle")
REARM_CLEAR_SEC = 2.0


@dataclass
class ViolationEvent:
    """A rule-engine violation candidate.

    Compatibility: ``confidence`` mirrors ``violation_confidence`` (never the
    raw detector score). Prefer the explicit dual fields for new code.
    """

    violation_type: str
    track_id: int
    confidence: float
    frame_number: int
    timestamp_sec: float
    reason_log: str
    vehicle_class: str | None = None
    detection_confidence: float = 0.0
    violation_confidence: float = 0.0
    evidence_sufficiency: float = 0.0
    contributing_factors: dict[str, float] = field(default_factory=dict)
    unavailable_factors: tuple[str, ...] = ()
    # candidate = automatic candidate; review = uncertain/partial; suppressed = not emitted as auto
    outcome: str = "candidate"
    capability_notes: str | None = None
    evidence_timing: dict[str, Any] | None = None


@dataclass
class _PersistenceTracker:
    """Tracks continuous truth of a condition; gaps reset the window."""

    started_at: float | None = None
    last_true_at: float | None = None
    last_seen: float = 0.0
    max_gap_sec: float = 1.0

    def update(
        self,
        condition: bool,
        timestamp_sec: float,
        threshold_sec: float = VIOLATION_PERSISTENCE_SEC,
    ) -> bool:
        self.last_seen = timestamp_sec
        if condition:
            if self.started_at is None:
                self.started_at = timestamp_sec
            elif (
                self.last_true_at is not None
                and (timestamp_sec - self.last_true_at) > self.max_gap_sec
            ):
                # Missing observations interrupt consecutive persistence.
                self.started_at = timestamp_sec
            self.last_true_at = timestamp_sec
            return (timestamp_sec - self.started_at) >= threshold_sec
        self.started_at = None
        self.last_true_at = None
        return False

    def elapsed(self, timestamp_sec: float) -> float:
        if self.started_at is None:
            return 0.0
        return timestamp_sec - self.started_at

    def reset(self) -> None:
        self.started_at = None
        self.last_true_at = None


@dataclass
class _FiredEpisode:
    fired_at: float
    condition_false_since: float | None = None


@dataclass
class RuleEngineState:
    """Mutable per-run rule state with track-aligned expiry."""

    persistence: dict[tuple[str, int], _PersistenceTracker] = field(default_factory=dict)
    fired: dict[tuple[str, int], _FiredEpisode] = field(default_factory=dict)
    track_last_seen: dict[int, float] = field(default_factory=dict)
    contextual: dict[tuple[str, int], dict[str, Any]] = field(default_factory=dict)
    associations: dict[tuple[str, int], Any] = field(default_factory=dict)
    membership_hysteresis: MembershipHysteresis = field(default_factory=MembershipHysteresis)
    diagnostics: list[str] = field(default_factory=list)
    capability: dict[str, RuleCapabilityStatus] = field(default_factory=dict)
    track_expiry_sec: float = TRACK_EXPIRY_SEC
    rearm_clear_sec: float = REARM_CLEAR_SEC
    clock: Callable[[], float] | None = None  # optional injectable clock for tests
    # (violation_type, track_id, cleared_at_sec) produced when a fired episode re-arms
    recently_cleared: list[tuple[str, int, float]] = field(default_factory=list)

    def now(self, fallback: float) -> float:
        if self.clock is not None:
            return float(self.clock())
        return float(fallback)

    def observe_tracks(self, tracked: list[dict[str, Any]], timestamp_sec: float) -> None:
        for det in tracked:
            tid = int(det["track_id"])
            self.track_last_seen[tid] = float(det.get("timestamp_sec", timestamp_sec))

    def tracker_for(self, rule_key: str, track_id: int) -> _PersistenceTracker:
        return self.persistence.setdefault((rule_key, track_id), _PersistenceTracker())

    def already_fired(self, violation_type: str, track_id: int) -> bool:
        return (violation_type, track_id) in self.fired

    def mark_fired(self, violation_type: str, track_id: int, timestamp_sec: float) -> None:
        self.fired[(violation_type, track_id)] = _FiredEpisode(fired_at=timestamp_sec)

    def note_condition(
        self,
        violation_type: str,
        track_id: int,
        condition_true: bool,
        timestamp_sec: float,
    ) -> None:
        """Update re-arm bookkeeping after a fire: clear period re-arms."""
        key = (violation_type, track_id)
        ep = self.fired.get(key)
        if ep is None:
            return
        if condition_true:
            ep.condition_false_since = None
            return
        if ep.condition_false_since is None:
            ep.condition_false_since = timestamp_sec
        elif (timestamp_sec - ep.condition_false_since) >= self.rearm_clear_sec:
            del self.fired[key]
            self.recently_cleared.append((violation_type, track_id, timestamp_sec))

    def consume_cleared(self) -> list[tuple[str, int, float]]:
        """Return and clear (violation_type, track_id, cleared_at) re-arm events."""
        out = list(self.recently_cleared)
        self.recently_cleared.clear()
        return out

    def prune_expired(self, now: float) -> list[int]:
        """Drop state for tracks past the tracker expiry policy."""
        expired = [
            tid
            for tid, last in self.track_last_seen.items()
            if now - last > self.track_expiry_sec
        ]
        for tid in expired:
            self._purge_track(tid, cleared_at=now)
        # Also purge keys whose track is unknown / never observed recently.
        for key in list(self.persistence.keys()):
            tid = key[1]
            last = self.track_last_seen.get(tid)
            if last is None or now - last > self.track_expiry_sec:
                self.persistence.pop(key, None)
        for key in list(self.fired.keys()):
            tid = key[1]
            last = self.track_last_seen.get(tid)
            if last is None or now - last > self.track_expiry_sec:
                vtype, _ = key
                self.fired.pop(key, None)
                self.recently_cleared.append((vtype, tid, float(now)))
        for key in list(self.contextual.keys()):
            tid = key[1]
            last = self.track_last_seen.get(tid)
            if last is None or now - last > self.track_expiry_sec:
                self.contextual.pop(key, None)
        for key in list(self.associations.keys()):
            tid = key[1]
            last = self.track_last_seen.get(tid)
            if last is None or now - last > self.track_expiry_sec:
                self.associations.pop(key, None)
        active = {
            (rk, tid)
            for (rk, tid) in self.membership_hysteresis.history
            if tid in self.track_last_seen
            and now - self.track_last_seen[tid] <= self.track_expiry_sec
        }
        self.membership_hysteresis.prune(active)
        return expired

    def _purge_track(self, track_id: int, *, cleared_at: float | None = None) -> None:
        for key in [k for k in self.fired if k[1] == track_id]:
            vtype, tid = key
            self.fired.pop(key, None)
            if cleared_at is not None:
                self.recently_cleared.append((vtype, tid, float(cleared_at)))
        self.track_last_seen.pop(track_id, None)
        for store in (self.persistence, self.contextual, self.associations):
            for key in [k for k in store if k[1] == track_id]:
                store.pop(key, None)


# ---------------------------------------------------------------------------
# Geometry / association helpers
# ---------------------------------------------------------------------------

def _bbox_center(det: dict[str, Any]) -> tuple[float, float]:
    x = float(det["bbox_x"]) + float(det["bbox_w"]) / 2
    y = float(det["bbox_y"]) + float(det["bbox_h"]) / 2
    return x, y


def _in_zone(
    det: dict[str, Any],
    polygon: list[list[float]],
    state: RuleEngineState,
    rule_key: str,
    *,
    policy=POLICY_STATIONARY,
) -> bool:
    """Footprint/overlap membership (replaces bottom-center-only)."""
    if not polygon or len(polygon) < 3:
        return False
    track_id = int(det["track_id"])
    result = evaluate_zone_membership(
        det,
        polygon,
        policy=policy,
        hysteresis=state.membership_hysteresis,
        hysteresis_key=(rule_key, track_id),
    )
    require_stable = bool(getattr(policy, "require_stable", False))
    return is_inside_for_rule(result, require_stable=require_stable)


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


def _iou(a: dict[str, Any], b: dict[str, Any]) -> float:
    ax1, ay1 = float(a["bbox_x"]), float(a["bbox_y"])
    ax2, ay2 = ax1 + float(a["bbox_w"]), ay1 + float(a["bbox_h"])
    bx1, by1 = float(b["bbox_x"]), float(b["bbox_y"])
    bx2, by2 = bx1 + float(b["bbox_w"]), by1 + float(b["bbox_h"])
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


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


def _helmet_labels(tracked: list[dict[str, Any]]) -> tuple[list[dict], list[dict], list[dict]]:
    acceptable = [
        d
        for d in tracked
        if d.get("class_label") in (YOLO_CLASS_HELMET_ACCEPTABLE, YOLO_CLASS_HELMET)
    ]
    nut = [d for d in tracked if d.get("class_label") == YOLO_CLASS_HELMET_NUT_SHELL]
    legacy = [d for d in tracked if d.get("class_label") == YOLO_CLASS_HELMET]
    return acceptable, nut, legacy


def _rider_helmet_state(
    rider: dict[str, Any],
    acceptable: list[dict[str, Any]],
    nut_shell: list[dict[str, Any]],
) -> str:
    """Return NO_HELMET | NUT_SHELL | ACCEPTABLE_SHAPE | UNKNOWN."""
    if any(_boxes_overlap(rider, h) for h in nut_shell):
        return "NUT_SHELL"
    if any(_boxes_overlap(rider, h) for h in acceptable):
        return "ACCEPTABLE_SHAPE"
    # Small / edge / low-confidence rider → UNKNOWN rather than NO_HELMET.
    conf = float(rider.get("confidence", 0))
    area = float(rider.get("bbox_w", 0)) * float(rider.get("bbox_h", 0))
    if conf < 0.45 or area < 400:
        return "UNKNOWN"
    return "NO_HELMET"


def _is_stationary(
    det: dict[str, Any],
    stationary_px: float,
    geometry: GeometryProfile | None = None,
) -> bool:
    speed = det.get("speed_px_per_sec")
    if speed is None:
        return False
    threshold = geometry.stationary_px_per_sec() if geometry is not None else stationary_px
    return float(speed) <= threshold


def _parse_clock(value: str) -> dtime:
    hour, minute = value.split(":")
    return dtime(int(hour), int(minute))


def _within_time_window(now: dtime, start: str, end: str) -> bool:
    start_t, end_t = _parse_clock(start), _parse_clock(end)
    if start_t <= end_t:
        return start_t <= now <= end_t
    return now >= start_t or now <= end_t


def _emit(
    state: RuleEngineState,
    events: list[ViolationEvent],
    violation_type: str,
    det: dict[str, Any],
    frame_number: int,
    reason: str,
    score: ViolationScore,
    *,
    outcome: str = "candidate",
    capability_notes: str | None = None,
) -> None:
    track_id = int(det["track_id"])
    ts = float(det.get("timestamp_sec", 0))
    if state.already_fired(violation_type, track_id):
        return
    state.mark_fired(violation_type, track_id, ts)
    det_conf = float(
        score.detection_confidence
        if score.detection_confidence is not None
        else det.get("confidence", 0)
    )
    viol_conf = float(score.violation_confidence)
    events.append(
        ViolationEvent(
            violation_type=violation_type,
            track_id=track_id,
            confidence=legacy_confidence_mapping(score),
            detection_confidence=det_conf,
            violation_confidence=viol_conf,
            evidence_sufficiency=float(score.evidence_sufficiency),
            contributing_factors=dict(score.contributing_factors),
            unavailable_factors=tuple(score.unavailable_factors),
            frame_number=frame_number,
            timestamp_sec=ts,
            reason_log=reason,
            vehicle_class=vehicle_category(str(det.get("class_label", ""))),
            outcome=outcome,
            capability_notes=capability_notes,
        )
    )


def _rule_allowed(state: RuleEngineState, name: str) -> bool:
    cap = state.capability.get(name)
    if cap is None:
        return True
    return cap.automatic_evaluation


def _is_usable_vehicle_detection(det: dict[str, Any]) -> bool:
    """Canonical vehicle detections only; UNCERTAIN legacy brands fail closed."""
    label = det.get("class_label")
    if label not in VEHICLE_CLASSES:
        return False
    if det.get("class_review_state") in ("UNCERTAIN", "UNKNOWN"):
        return False
    if det.get("canonical_class") is None and det.get("raw_class") in (
        "piaggio",
        "uv_express_van",
    ):
        return False
    return True


# ---------------------------------------------------------------------------
# Detection-only rules
# ---------------------------------------------------------------------------

def check_no_helmet(
    tracked: list[dict[str, Any]],
    state: RuleEngineState,
    frame_number: int,
    params: dict[str, Any],
    *,
    model_classes: tuple[str, ...] | None = None,
) -> list[ViolationEvent]:
    """No Helmet — requires rider association + helmet taxonomy classes.

    Absence of a helmet detection alone is not proof when visibility or model
    capability is insufficient (UNKNOWN / fail-closed).
    """
    if not _rule_allowed(state, VIOLATION_NO_HELMET):
        return []
    if model_classes is not None and not classes_satisfy_rule(VIOLATION_NO_HELMET, model_classes):
        state.diagnostics.append(
            "No Helmet automatic evaluation disabled: required helmet classes missing."
        )
        return []

    motorcycles = [d for d in tracked if d.get("class_label") == YOLO_CLASS_MOTORCYCLE]
    persons = [
        d
        for d in tracked
        if d.get("class_label") in (YOLO_CLASS_PERSON, YOLO_CLASS_RIDER)
    ]
    acceptable, nut, _ = _helmet_labels(tracked)

    events: list[ViolationEvent] = []
    for mc in motorcycles:
        track_id = int(mc["track_id"])
        riders = _associate_riders(mc, persons)
        ts = float(mc.get("timestamp_sec", 0))
        if not riders:
            state.persistence.pop(("no_helmet", track_id), None)
            state.note_condition(VIOLATION_NO_HELMET, track_id, False, ts)
            continue

        states = [_rider_helmet_state(r, acceptable, nut) for r in riders]
        if any(s == "UNKNOWN" for s in states) and not any(s == "NO_HELMET" for s in states):
            # Visibility insufficient — do not treat as NO_HELMET.
            state.persistence.pop(("no_helmet", track_id), None)
            continue

        unhelmeted = [
            (r, s) for r, s in zip(riders, states) if s == "NO_HELMET"
        ]
        condition = len(unhelmeted) > 0
        tracker = state.tracker_for("no_helmet", track_id)
        if tracker.update(condition, ts):
            rider_ids = ", ".join(str(int(r["track_id"])) for r, _ in unhelmeted)
            score = score_from_persistence(
                detection_confidence=float(mc.get("confidence", 0)),
                elapsed_sec=tracker.elapsed(ts),
                required_sec=VIOLATION_PERSISTENCE_SEC,
                association_quality=min(1.0, len(riders) / 2.0),
                geometry_stability=0.8,
            )
            _emit(
                state,
                events,
                VIOLATION_NO_HELMET,
                mc,
                frame_number,
                f"Motorcycle track #{track_id}: rider(s) #{rider_ids} classified "
                f"NO_HELMET for >={VIOLATION_PERSISTENCE_SEC}s.",
                score,
            )
        state.note_condition(VIOLATION_NO_HELMET, track_id, condition, ts)
    return events


def check_substandard_helmet(
    tracked: list[dict[str, Any]],
    state: RuleEngineState,
    frame_number: int,
    *,
    model_classes: tuple[str, ...] | None = None,
) -> list[ViolationEvent]:
    """Substandard / Nut-Shell Helmet — distinct from No Helmet."""
    if not _rule_allowed(state, VIOLATION_SUBSTANDARD_HELMET):
        state.diagnostics.append(
            "Substandard / Nut-Shell Helmet: automatic evaluation disabled "
            "(missing prerequisites)."
        )
        return []
    if model_classes is not None and not classes_satisfy_rule(
        VIOLATION_SUBSTANDARD_HELMET, model_classes
    ):
        return []

    motorcycles = [d for d in tracked if d.get("class_label") == YOLO_CLASS_MOTORCYCLE]
    persons = [
        d
        for d in tracked
        if d.get("class_label") in (YOLO_CLASS_PERSON, YOLO_CLASS_RIDER)
    ]
    acceptable, nut, _ = _helmet_labels(tracked)
    events: list[ViolationEvent] = []
    for mc in motorcycles:
        track_id = int(mc["track_id"])
        riders = _associate_riders(mc, persons)
        ts = float(mc.get("timestamp_sec", 0))
        if not riders:
            continue
        nut_riders = [
            r for r in riders if _rider_helmet_state(r, acceptable, nut) == "NUT_SHELL"
        ]
        condition = len(nut_riders) > 0
        tracker = state.tracker_for("substandard_helmet", track_id)
        if tracker.update(condition, ts):
            score = score_from_persistence(
                detection_confidence=float(mc.get("confidence", 0)),
                elapsed_sec=tracker.elapsed(ts),
                required_sec=VIOLATION_PERSISTENCE_SEC,
                association_quality=0.85,
            )
            _emit(
                state,
                events,
                VIOLATION_SUBSTANDARD_HELMET,
                mc,
                frame_number,
                f"Motorcycle track #{track_id}: nut-shell/substandard helmet form "
                f"persisted >={VIOLATION_PERSISTENCE_SEC}s.",
                score,
            )
        state.note_condition(VIOLATION_SUBSTANDARD_HELMET, track_id, condition, ts)
    return events


def check_no_side_mirror(
    tracked: list[dict[str, Any]],
    state: RuleEngineState,
    frame_number: int,
    *,
    model_classes: tuple[str, ...] | None = None,
) -> list[ViolationEvent]:
    """No Side Mirror — PRESENT / ABSENT / UNKNOWN.

    Non-detection alone is never proof of absence. Vehicle bbox size and
    detector confidence do not establish mirror visibility, orientation,
    cropping, or occlusion. Without approved affirmative-absence evidence the
    observation remains UNKNOWN and no candidate is emitted (PARTIAL / NEEDS DECISION).
    """
    if not _rule_allowed(state, VIOLATION_NO_SIDE_MIRROR):
        return []
    if model_classes is not None and not classes_satisfy_rule(
        VIOLATION_NO_SIDE_MIRROR, model_classes
    ):
        state.diagnostics.append(
            "No Side Mirror: side_mirror class unavailable; automatic evaluation disabled."
        )
        return []

    mirrors = [d for d in tracked if d.get("class_label") == YOLO_CLASS_SIDE_MIRROR]
    vehicles = [
        d
        for d in tracked
        if d.get("class_label") in VEHICLE_CLASSES
        and d.get("class_label") != "bicycle"
    ]
    # Record PRESENT observations only. Absence is never inferred from
    # non-detection; no persistence timer is advanced for missing mirrors.
    for veh in vehicles:
        track_id = int(veh["track_id"])
        ts = float(veh.get("timestamp_sec", 0))
        vx, vy = float(veh["bbox_x"]), float(veh["bbox_y"])
        vw, vh = float(veh["bbox_w"]), float(veh["bbox_h"])
        present = False
        for m in mirrors:
            if _iou(veh, m) > 0 or _boxes_overlap(veh, m):
                mx = float(m["bbox_x"]) + float(m["bbox_w"]) / 2
                my = float(m["bbox_y"]) + float(m["bbox_h"]) / 2
                if vx <= mx <= vx + vw and vy <= my <= vy + vh * 0.6:
                    present = True
                    break
        ctx = state.contextual.setdefault(
            ("side_mirror", track_id),
            {"state": "UNKNOWN", "present_frames": 0},
        )
        if present:
            ctx["state"] = "PRESENT"
            ctx["present_frames"] = int(ctx.get("present_frames", 0)) + 1
        else:
            # Stay UNKNOWN — do not treat non-detection as ABSENT and do not
            # start/advance an absence persistence tracker.
            if ctx.get("state") != "PRESENT":
                ctx["state"] = "UNKNOWN"
            state.persistence.pop(("no_side_mirror", track_id), None)
        state.note_condition(VIOLATION_NO_SIDE_MIRROR, track_id, False, ts)
    return []


def check_motorcycle_overloading(
    tracked: list[dict[str, Any]],
    state: RuleEngineState,
    frame_number: int,
) -> list[ViolationEvent]:
    """More than two occupants actually riding the motorcycle."""
    if not _rule_allowed(state, VIOLATION_MOTORCYCLE_OVERLOADING):
        return []
    motorcycles = [d for d in tracked if d.get("class_label") == YOLO_CLASS_MOTORCYCLE]
    persons = [
        d
        for d in tracked
        if d.get("class_label") in (YOLO_CLASS_PERSON, YOLO_CLASS_RIDER)
    ]
    events: list[ViolationEvent] = []
    for mc in motorcycles:
        track_id = int(mc["track_id"])
        riders = _associate_riders(mc, persons)
        tracker = state.tracker_for("overloading", track_id)
        ts = float(mc.get("timestamp_sec", 0))
        condition = len(riders) > 2
        if tracker.update(condition, ts):
            score = score_from_persistence(
                detection_confidence=float(mc.get("confidence", 0)),
                elapsed_sec=tracker.elapsed(ts),
                required_sec=VIOLATION_PERSISTENCE_SEC,
                association_quality=min(1.0, len(riders) / 3.0),
            )
            _emit(
                state,
                events,
                VIOLATION_MOTORCYCLE_OVERLOADING,
                mc,
                frame_number,
                f"Motorcycle track #{track_id}: {len(riders)} associated riders "
                f"(max 2) for >={VIOLATION_PERSISTENCE_SEC}s.",
                score,
            )
        state.note_condition(VIOLATION_MOTORCYCLE_OVERLOADING, track_id, condition, ts)
    return events


def check_cargo_passenger(
    tracked: list[dict[str, Any]],
    state: RuleEngineState,
    frame_number: int,
    *,
    model_classes: tuple[str, ...] | None = None,
) -> list[ViolationEvent]:
    """Person associated with truck/pickup cargo ROI via shared motion + persistence."""
    if not _rule_allowed(state, VIOLATION_CARGO_PASSENGERS):
        return []
    if model_classes is not None and not classes_satisfy_rule(
        VIOLATION_CARGO_PASSENGERS, model_classes
    ):
        return []

    vehicles = [
        d for d in tracked if is_cargo_passenger_applicable(str(d.get("class_label", "")))
    ]
    persons = [d for d in tracked if d.get("class_label") == YOLO_CLASS_PERSON]
    events: list[ViolationEvent] = []
    for veh in vehicles:
        track_id = int(veh["track_id"])
        ts = float(veh.get("timestamp_sec", 0))
        # Cargo ROI ≈ rear 45% of vehicle bbox.
        vx, vy = float(veh["bbox_x"]), float(veh["bbox_y"])
        vw, vh = float(veh["bbox_w"]), float(veh["bbox_h"])
        cargo = {
            "bbox_x": vx + vw * 0.55,
            "bbox_y": vy + vh * 0.25,
            "bbox_w": vw * 0.45,
            "bbox_h": vh * 0.75,
        }
        v_speed = float(veh.get("speed_px_per_sec") or 0.0)
        associated = 0
        for person in persons:
            if not _boxes_overlap(cargo, person):
                continue
            p_speed = float(person.get("speed_px_per_sec") or 0.0)
            # Shared motion: speeds within 40% or both near-stationary.
            shared = abs(v_speed - p_speed) <= max(8.0, 0.4 * max(v_speed, p_speed, 1.0))
            if shared:
                associated += 1
        condition = associated > 0
        tracker = state.tracker_for("cargo_passenger", track_id)
        # One-frame overlap is insufficient — require persistence.
        if tracker.update(condition, ts, threshold_sec=max(2.0, VIOLATION_PERSISTENCE_SEC)):
            score = score_from_persistence(
                detection_confidence=float(veh.get("confidence", 0)),
                elapsed_sec=tracker.elapsed(ts),
                required_sec=max(2.0, VIOLATION_PERSISTENCE_SEC),
                association_quality=min(1.0, 0.5 + 0.25 * associated),
                contextual_availability=0.6,
            )
            label = str(veh.get("class_label", "vehicle"))
            _emit(
                state,
                events,
                VIOLATION_CARGO_PASSENGERS,
                veh,
                frame_number,
                f"{label} track #{track_id}: person associated with cargo ROI "
                f"via overlap + shared motion for >={max(2.0, VIOLATION_PERSISTENCE_SEC)}s.",
                score,
                outcome="review",
            )
        state.note_condition(VIOLATION_CARGO_PASSENGERS, track_id, condition, ts)
    return events


# ---------------------------------------------------------------------------
# Zone / context rules
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
    geometry: GeometryProfile | None = None,
    require_extra: Callable[[dict[str, Any]], TriState] | None = None,
    outcome: str = "candidate",
) -> list[ViolationEvent]:
    events: list[ViolationEvent] = []
    stationary_px = float(params["stationary_px"])
    for det in vehicles:
        if class_filter and det.get("class_label") not in class_filter:
            continue
        track_id = int(det["track_id"])
        ts = float(det.get("timestamp_sec", 0))
        inside = _in_zone(det, polygon, state, rule_key, policy=POLICY_STATIONARY)
        stationary = _is_stationary(det, stationary_px, geometry)
        extra = TriState.TRUE
        if require_extra is not None:
            extra = require_extra(det)
            if extra is TriState.UNKNOWN:
                state.note_condition(violation_type, track_id, False, ts)
                continue
            if extra is TriState.FALSE:
                state.persistence.pop((rule_key, track_id), None)
                state.note_condition(violation_type, track_id, False, ts)
                continue
        condition = inside and stationary and extra.is_true()
        tracker = state.tracker_for(rule_key, track_id)
        if tracker.update(condition, ts, dwell_sec):
            mem = evaluate_zone_membership(
                det, polygon, policy=POLICY_STATIONARY,
                hysteresis=state.membership_hysteresis,
                hysteresis_key=(rule_key, track_id),
            )
            score = score_from_persistence(
                detection_confidence=float(det.get("confidence", 0)),
                elapsed_sec=tracker.elapsed(ts),
                required_sec=dwell_sec,
                geometry_stability=min(1.0, mem.overlap_ratio + 0.3),
                contextual_availability=0.7 if inside else 0.2,
            )
            _emit(
                state,
                events,
                violation_type,
                det,
                frame_number,
                reason_template.format(track_id=track_id, dwell=dwell_sec),
                score,
                outcome=outcome,
            )
        state.note_condition(violation_type, track_id, condition, ts)
    return events


def check_illegal_parking(
    vehicles: list[dict[str, Any]],
    polygon: list[list[float]],
    state: RuleEngineState,
    frame_number: int,
    params: dict[str, Any],
    geometry: GeometryProfile | None = None,
) -> list[ViolationEvent]:
    """Illegal Parking — distinct from Illegal Terminal.

    Stationary dwell alone is not proof of parking; without richer parking
    context this remains a PARTIAL zone-dwell proxy (review outcome).
    """
    if not _rule_allowed(state, VIOLATION_ILLEGAL_PARKING):
        return []
    # Stopping briefly must not prove Illegal Parking — use parking_dwell only.
    return _check_zone_dwell(
        vehicles,
        polygon,
        state,
        frame_number,
        params,
        rule_key="illegal_parking_park",
        violation_type=VIOLATION_ILLEGAL_PARKING,
        dwell_sec=float(params.get("parking_dwell_sec", 30.0)),
        reason_template=(
            "Vehicle track #{track_id} stationary in No Parking Zone for "
            ">={dwell}s (Illegal Parking proxy — parking-like context incomplete; review)."
        ),
        geometry=geometry,
        outcome="review",
    )


def check_illegal_terminal(
    vehicles: list[dict[str, Any]],
    polygon: list[list[float]],
    state: RuleEngineState,
    frame_number: int,
    params: dict[str, Any],
    geometry: GeometryProfile | None = None,
) -> list[ViolationEvent]:
    """Illegal Terminal — PUV/terminal context required; stop alone insufficient."""
    if not _rule_allowed(state, VIOLATION_ILLEGAL_TERMINAL):
        return []

    def _terminal_context(det: dict[str, Any]) -> TriState:
        label = str(det.get("class_label", ""))
        if label in TERMINAL_STRICT_CLASSES:
            # Passenger-activity evidence is not yet available → UNKNOWN unless
            # caller injects contextual flag on the detection.
            if det.get("terminal_passenger_activity") is True:
                return TriState.TRUE
            if det.get("terminal_passenger_activity") is False:
                return TriState.FALSE
            return TriState.UNKNOWN
        if label == YOLO_CLASS_VAN:
            # Van needs apparent public/for-hire context.
            if det.get("apparent_for_hire") is True and det.get("terminal_passenger_activity") is True:
                return TriState.TRUE
            return TriState.UNKNOWN
        if label == YOLO_CLASS_BUS:
            # Bus retained only with explicit passenger-activity evidence.
            if det.get("terminal_passenger_activity") is True:
                return TriState.TRUE
            return TriState.UNKNOWN
        return TriState.FALSE

    return _check_zone_dwell(
        vehicles,
        polygon,
        state,
        frame_number,
        params,
        rule_key="illegal_terminal",
        violation_type=VIOLATION_ILLEGAL_TERMINAL,
        dwell_sec=float(params.get("loading_dwell_sec", 8.0)),
        reason_template=(
            "PUV track #{track_id} terminal-like dwell in No Loading zone "
            ">={dwell}s with passenger-activity evidence."
        ),
        class_filter=PUV_CLASSES,
        geometry=geometry,
        require_extra=_terminal_context,
        outcome="review",
    )


def check_obstruction(
    vehicles: list[dict[str, Any]],
    polygon: list[list[float]],
    state: RuleEngineState,
    frame_number: int,
    params: dict[str, Any],
    geometry: GeometryProfile | None = None,
) -> list[ViolationEvent]:
    if not _rule_allowed(state, VIOLATION_OBSTRUCTION):
        return []
    return _check_zone_dwell(
        vehicles,
        polygon,
        state,
        frame_number,
        params,
        rule_key="obstruction",
        violation_type=VIOLATION_OBSTRUCTION,
        dwell_sec=float(params.get("obstruction_dwell_sec", 10.0)),
        reason_template=(
            "Vehicle track #{track_id} stationary in Active Lane for "
            ">={dwell}s (Obstruction)."
        ),
        geometry=geometry,
    )


def check_blocking_pedestrian(
    vehicles: list[dict[str, Any]],
    polygon: list[list[float]],
    state: RuleEngineState,
    frame_number: int,
    params: dict[str, Any],
    geometry: GeometryProfile | None = None,
) -> list[ViolationEvent]:
    if not _rule_allowed(state, VIOLATION_OBSTRUCTION):
        return []
    return _check_zone_dwell(
        vehicles,
        polygon,
        state,
        frame_number,
        params,
        rule_key="blocking_pedestrian",
        violation_type=VIOLATION_OBSTRUCTION,
        dwell_sec=float(params.get("crossing_block_sec", 3.0)),
        reason_template=(
            "Vehicle track #{track_id} stationary on Pedestrian Crossing for "
            ">={dwell}s (Obstruction)."
        ),
        geometry=geometry,
    )


def check_counterflow(
    vehicles: list[dict[str, Any]],
    polygon: list[list[float]],
    state: RuleEngineState,
    frame_number: int,
    params: dict[str, Any],
    geometry: GeometryProfile | None = None,
) -> list[ViolationEvent]:
    if not _rule_allowed(state, VIOLATION_COUNTERFLOW):
        return []
    lane_flow = float(params.get("lane_flow_degrees", 90.0))
    tolerance = float(params.get("flow_tolerance_degrees", 60.0))
    opposite = (lane_flow + 180.0) % 360.0
    # Design direction: initial 4s persistence (not invented legal threshold —
    # uses configurable param with documented default).
    persist = float(params.get("counterflow_persist_sec", 4.0))

    events: list[ViolationEvent] = []
    for det in vehicles:
        track_id = int(det["track_id"])
        ts = float(det.get("timestamp_sec", 0))
        heading = det.get("direction_degrees")
        inside = _in_zone(det, polygon, state, "counterflow", policy=POLICY_LANE)
        condition = (
            heading is not None
            and inside
            and angle_difference(float(heading), opposite) <= tolerance
        )
        tracker = state.tracker_for("counterflow", track_id)
        if tracker.update(condition, ts, persist):
            score = score_from_persistence(
                detection_confidence=float(det.get("confidence", 0)),
                elapsed_sec=tracker.elapsed(ts),
                required_sec=persist,
                geometry_stability=0.85,
            )
            _emit(
                state,
                events,
                VIOLATION_COUNTERFLOW,
                det,
                frame_number,
                f"Vehicle track #{track_id} travelling {float(heading):.0f}deg "
                f"against lane flow ({lane_flow:.0f}deg) in Active Lane "
                f"for >={persist}s.",
                score,
            )
        state.note_condition(VIOLATION_COUNTERFLOW, track_id, condition, ts)
    return events


def check_truck_ban(
    vehicles: list[dict[str, Any]],
    polygon: list[list[float]],
    state: RuleEngineState,
    frame_number: int,
    params: dict[str, Any],
    recording_time: dtime | None = None,
    recording_time_known: bool = False,
    *,
    now_time: dtime | None = None,
) -> list[ViolationEvent]:
    """Truck-Ban uses actual recording datetime. Upload time is never used.

    ``now_time`` is retained as a compatibility alias for ``recording_time``;
    when provided it implies the recording clock is known.
    """
    if now_time is not None and recording_time is None:
        recording_time = now_time
        recording_time_known = True
    if not _rule_allowed(state, VIOLATION_TRUCK_BAN):
        return []
    if not recording_time_known or recording_time is None:
        state.diagnostics.append(
            "Truck-Ban Violation: recording datetime unavailable → time context UNKNOWN; "
            "automatic evaluation suppressed."
        )
        return []

    if not _within_time_window(
        recording_time,
        str(params.get("truck_ban_start", "06:00")),
        str(params.get("truck_ban_end", "09:00")),
    ):
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
        inside = _in_zone(det, polygon, state, "truck_ban", policy=POLICY_LANE)
        tracker = state.tracker_for("truck_ban", track_id)
        if tracker.update(inside, ts):
            score = score_from_persistence(
                detection_confidence=float(det.get("confidence", 0)),
                elapsed_sec=tracker.elapsed(ts),
                required_sec=VIOLATION_PERSISTENCE_SEC,
                contextual_availability=1.0,
            )
            _emit(
                state,
                events,
                VIOLATION_TRUCK_BAN,
                det,
                frame_number,
                f"{label} track #{track_id} inside Truck Ban Zone during ban window "
                f"({params['truck_ban_start']}-{params['truck_ban_end']}) "
                f"using recording datetime.",
                score,
            )
        state.note_condition(VIOLATION_TRUCK_BAN, track_id, inside, ts)
    return events


def check_pavement_markings(
    vehicles: list[dict[str, Any]],
    state: RuleEngineState,
    frame_number: int,
    params: dict[str, Any],
    *,
    restricted_lane: list[list[float]] | None = None,
    marking_geometry: list[dict[str, Any]] | None = None,
) -> list[ViolationEvent]:
    """Pavement markings via operator-saved geometry; restricted-lane as partial proxy."""
    if not _rule_allowed(state, VIOLATION_PAVEMENT_MARKINGS):
        return []
    events: list[ViolationEvent] = []

    # Preferred path: configured marking segments with permitted/prohibited side.
    markings = marking_geometry or params.get("marking_geometry") or []
    if markings:
        for det in vehicles:
            track_id = int(det["track_id"])
            ts = float(det.get("timestamp_sec", 0))
            heading = det.get("direction_degrees")
            crossed = False
            for marking in markings:
                geom = marking.get("polygon") or marking.get("polyline") or []
                if len(geom) < 2:
                    continue
                # Trajectory segment: use last motion via centroid if present.
                cx = float(det.get("centroid_x", det["bbox_x"] + det["bbox_w"] / 2))
                cy = float(det.get("centroid_y", det["bbox_y"] + det["bbox_h"] / 2))
                mem = evaluate_zone_membership(
                    det,
                    geom if len(geom) >= 3 else [
                        geom[0],
                        geom[-1],
                        [geom[-1][0] + 1, geom[-1][1] + 1],
                    ],
                    policy=POLICY_LANE,
                    hysteresis=state.membership_hysteresis,
                    hysteresis_key=("marking", track_id),
                )
                prohibited = marking.get("prohibited_side")
                side = marking.get("vehicle_side")
                if mem.state is MembershipState.INSIDE or mem.overlap_ratio >= 0.2:
                    if prohibited and side and side == prohibited:
                        crossed = True
                    elif marking.get("type") in ("double_solid", "marking_double_solid"):
                        crossed = True
                    elif prohibited is None and marking.get("type"):
                        # Marking present but side unknown → do not invent.
                        crossed = False
            tracker = state.tracker_for("pavement_markings", track_id)
            if tracker.update(crossed and heading is not None, ts):
                score = score_from_persistence(
                    detection_confidence=float(det.get("confidence", 0)),
                    elapsed_sec=tracker.elapsed(ts),
                    required_sec=VIOLATION_PERSISTENCE_SEC,
                    geometry_stability=0.7,
                    contextual_availability=0.8,
                )
                _emit(
                    state,
                    events,
                    VIOLATION_PAVEMENT_MARKINGS,
                    det,
                    frame_number,
                    f"Vehicle track #{track_id} trajectory conflicts with configured "
                    f"pavement marking geometry.",
                    score,
                    outcome="review",
                )
            state.note_condition(VIOLATION_PAVEMENT_MARKINGS, track_id, crossed, ts)
        return events

    # Partial proxy: restricted lane zone (legacy behavior), review outcome.
    if restricted_lane and len(restricted_lane) >= 3:
        restricted = tuple(
            params.get("restricted_lane_classes", DEFAULT_RESTRICTED_LANE_CLASSES)
        )
        for det in vehicles:
            if det.get("class_label") not in restricted:
                continue
            track_id = int(det["track_id"])
            ts = float(det.get("timestamp_sec", 0))
            inside = _in_zone(det, restricted_lane, state, "restricted_lane", policy=POLICY_LANE)
            tracker = state.tracker_for("restricted_lane", track_id)
            if tracker.update(inside, ts):
                score = score_from_persistence(
                    detection_confidence=float(det.get("confidence", 0)),
                    elapsed_sec=tracker.elapsed(ts),
                    required_sec=VIOLATION_PERSISTENCE_SEC,
                    contextual_availability=0.5,
                )
                _emit(
                    state,
                    events,
                    VIOLATION_PAVEMENT_MARKINGS,
                    det,
                    frame_number,
                    f"{det.get('class_label', 'vehicle')} track #{track_id} inside "
                    f"Restricted Lane (pavement-markings proxy; marking geometry "
                    f"not configured).",
                    score,
                    outcome="review",
                )
            state.note_condition(VIOLATION_PAVEMENT_MARKINGS, track_id, inside, ts)
    else:
        state.diagnostics.append(
            "Failure to Follow Road/Pavement Markings: no marking geometry or "
            "restricted_lane zone configured; automatic confirmation disabled."
        )
    return events


def check_disregarding_traffic_sign(
    tracked: list[dict[str, Any]],
    state: RuleEngineState,
    frame_number: int,
    params: dict[str, Any],
) -> list[ViolationEvent]:
    """Disregarding Traffic Sign — supported signs only; no speed-limit.

    Fail-closed unless configured supported sign annotations and conflicting
    track behavior are available.
    """
    if not _rule_allowed(state, VIOLATION_DISREGARDING_SIGN):
        return []
    signs = params.get("supported_signs") or []
    if not signs:
        state.diagnostics.append(
            "Disregarding Traffic Sign: no supported sign annotations configured; "
            "automatic evaluation disabled. STOP and speed-limit are excluded."
        )
        return []

    events: list[ViolationEvent] = []
    vehicles = [d for d in tracked if _is_usable_vehicle_detection(d)]
    allowed_types = {
        "no_entry",
        "no_overtaking",
        "no_left_turn",
        "no_u_turn",
        "no_parking",
        "no_stopping",
        "sign_no_entry",
        "sign_no_overtaking",
        "sign_no_left_turn",
        "sign_no_u_turn",
        "sign_no_parking",
        "sign_no_stopping",
    }
    for sign in signs:
        stype = str(sign.get("type", "")).lower()
        if stype in ("stop", "speed_limit", "sign_stop", "sign_speed_limit"):
            continue
        if stype not in allowed_types:
            continue
        zone = sign.get("applicability_polygon") or sign.get("polygon") or []
        behavior = sign.get("prohibited_behavior", "enter")
        for det in vehicles:
            track_id = int(det["track_id"])
            ts = float(det.get("timestamp_sec", 0))
            if len(zone) < 3:
                continue
            inside = _in_zone(det, zone, state, f"sign_{stype}", policy=POLICY_LANE)
            heading = det.get("direction_degrees")
            conflict = False
            if behavior == "enter" and inside:
                conflict = True
            elif behavior == "left_turn" and inside and heading is not None:
                # Without a frozen turn detector, require explicit flag.
                conflict = bool(det.get("performed_left_turn"))
            elif behavior == "u_turn" and inside:
                conflict = bool(det.get("performed_u_turn"))
            elif behavior == "overtake" and inside:
                conflict = bool(det.get("performed_overtake"))
            tracker = state.tracker_for(f"sign_{stype}", track_id)
            if tracker.update(conflict, ts):
                score = score_from_persistence(
                    detection_confidence=float(det.get("confidence", 0)),
                    elapsed_sec=tracker.elapsed(ts),
                    required_sec=VIOLATION_PERSISTENCE_SEC,
                    contextual_availability=0.7,
                )
                _emit(
                    state,
                    events,
                    VIOLATION_DISREGARDING_SIGN,
                    det,
                    frame_number,
                    f"Vehicle track #{track_id} conflicting behavior vs supported "
                    f"sign '{stype}'.",
                    score,
                    outcome="review",
                )
            state.note_condition(VIOLATION_DISREGARDING_SIGN, track_id, conflict, ts)
    return events


# Legacy alias kept for older imports/tests.
def check_restricted_lane(
    vehicles: list[dict[str, Any]],
    polygon: list[list[float]],
    state: RuleEngineState,
    frame_number: int,
    params: dict[str, Any],
) -> list[ViolationEvent]:
    return check_pavement_markings(
        vehicles,
        state,
        frame_number,
        params,
        restricted_lane=polygon,
    )


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
    *,
    geometry: GeometryProfile | None = None,
    model_classes: tuple[str, ...] | None = None,
    recording_time_known: bool | None = None,
    frame_size: tuple[int, int] | None = None,
    now_sec: float | None = None,
) -> list[ViolationEvent]:
    """Run every enabled rule on the current frame's tracked detections.

    ``now_time`` is the scene/recording clock when known. When recording
    datetime is unavailable, time-dependent rules receive UNKNOWN and must
    not use upload/processing wall-clock time.
    """
    merged = dict(DEFAULT_RULE_PARAMETERS)
    if params:
        merged.update(params)
    zones = zones or {}

    enabled_set = (
        set(enabled_violations)
        if enabled_violations is not None
        else set(DEFAULT_ENABLED_VIOLATIONS)
    )

    # Resolve geometry profile (normalized by default).
    if geometry is None and frame_size is not None:
        geometry = build_geometry_profile(
            frame_size[0],
            frame_size[1],
            rule_params=merged,
            zone_polygons={k: v for k, v in zones.items() if v},
        )
    if geometry is not None:
        # Keep tracker-facing stationary_px consistent with resolved profile.
        merged = dict(merged)
        merged["stationary_px"] = geometry.stationary_px_per_sec()
        merged["min_direction_px"] = geometry.min_direction_px()

    # Capability gate snapshot (once per state unless refreshed by caller).
    if model_classes is not None and not state.capability:
        context_flags = {
            "zone:no_parking": bool(zones.get("no_parking")),
            "zone:active_lane": bool(zones.get("active_lane")),
            "zone:active_lane_or_crossing": bool(
                zones.get("active_lane") or zones.get("pedestrian_crossing")
            ),
            "zone:truck_ban_zone": bool(zones.get("truck_ban_zone")),
            "zone:loading_unloading_or_terminal": bool(zones.get("loading_unloading")),
            "recording_datetime": bool(
                recording_time_known
                if recording_time_known is not None
                else now_time is not None
            ),
            "lane_flow_degrees": "lane_flow_degrees" in merged,
            "marking_geometry": bool(merged.get("marking_geometry") or zones.get("restricted_lane")),
            "supported_sign_annotations": bool(merged.get("supported_signs")),
            "puv_context": True,  # evaluated per-detection as TriState
            "mirror_roi_visibility": True,
            "cargo_roi_association": True,
        }
        for name in CANONICAL_VIOLATIONS:
            if name in enabled_set:
                state.capability[name] = assess_rule_capability(
                    name, model_classes, context_flags=context_flags
                )

    # Track lifecycle — use the frame clock even when this frame has no tracks.
    ts_now = 0.0
    if now_sec is not None:
        ts_now = float(now_sec)
    elif tracked:
        ts_now = max(float(d.get("timestamp_sec", 0)) for d in tracked)
    state.observe_tracks(tracked, ts_now)
    state.prune_expired(ts_now)

    def _usable_vehicle(det: dict[str, Any]) -> bool:
        return _is_usable_vehicle_detection(det)

    vehicles = [d for d in tracked if _usable_vehicle(d)]
    events: list[ViolationEvent] = []

    time_known = (
        recording_time_known
        if recording_time_known is not None
        else now_time is not None
    )

    if VIOLATION_NO_HELMET in enabled_set:
        events.extend(
            check_no_helmet(
                tracked, state, frame_number, merged, model_classes=model_classes
            )
        )
    if VIOLATION_SUBSTANDARD_HELMET in enabled_set:
        events.extend(
            check_substandard_helmet(
                tracked, state, frame_number, model_classes=model_classes
            )
        )
    if VIOLATION_NO_SIDE_MIRROR in enabled_set:
        events.extend(
            check_no_side_mirror(
                tracked, state, frame_number, model_classes=model_classes
            )
        )
    if VIOLATION_MOTORCYCLE_OVERLOADING in enabled_set:
        events.extend(check_motorcycle_overloading(tracked, state, frame_number))
    if VIOLATION_CARGO_PASSENGERS in enabled_set:
        events.extend(
            check_cargo_passenger(
                tracked, state, frame_number, model_classes=model_classes
            )
        )
    if VIOLATION_DISREGARDING_SIGN in enabled_set:
        events.extend(
            check_disregarding_traffic_sign(tracked, state, frame_number, merged)
        )

    if zones.get("no_parking") and VIOLATION_ILLEGAL_PARKING in enabled_set:
        events.extend(
            check_illegal_parking(
                vehicles, zones["no_parking"], state, frame_number, merged, geometry
            )
        )
    if zones.get("active_lane"):
        if VIOLATION_OBSTRUCTION in enabled_set:
            events.extend(
                check_obstruction(
                    vehicles, zones["active_lane"], state, frame_number, merged, geometry
                )
            )
        if VIOLATION_COUNTERFLOW in enabled_set:
            events.extend(
                check_counterflow(
                    vehicles, zones["active_lane"], state, frame_number, merged, geometry
                )
            )
    if zones.get("pedestrian_crossing") and VIOLATION_OBSTRUCTION in enabled_set:
        events.extend(
            check_blocking_pedestrian(
                vehicles,
                zones["pedestrian_crossing"],
                state,
                frame_number,
                merged,
                geometry,
            )
        )
    if zones.get("truck_ban_zone") and VIOLATION_TRUCK_BAN in enabled_set:
        events.extend(
            check_truck_ban(
                vehicles,
                zones["truck_ban_zone"],
                state,
                frame_number,
                merged,
                recording_time=now_time if time_known else None,
                recording_time_known=time_known,
            )
        )
    if zones.get("loading_unloading") and VIOLATION_ILLEGAL_TERMINAL in enabled_set:
        events.extend(
            check_illegal_terminal(
                vehicles,
                zones["loading_unloading"],
                state,
                frame_number,
                merged,
                geometry,
            )
        )
    if VIOLATION_PAVEMENT_MARKINGS in enabled_set:
        events.extend(
            check_pavement_markings(
                vehicles,
                state,
                frame_number,
                merged,
                restricted_lane=zones.get("restricted_lane"),
            )
        )

    return events


def build_processing_diagnostics(
    *,
    model_classes: tuple[str, ...],
    enabled_violations: tuple[str, ...],
    geometry: GeometryProfile | None,
    state: RuleEngineState | None = None,
) -> ProcessingDiagnostics:
    caps = [
        assess_rule_capability(name, model_classes)
        for name in enabled_violations
        if name in CANONICAL_VIOLATIONS
    ]
    notes = list(state.diagnostics) if state else []
    mode = geometry.mode if geometry else None
    from core.rule_types import GeometryMode

    return ProcessingDiagnostics(
        model_classes=model_classes,
        geometry_mode=mode or GeometryMode.LEGACY_FALLBACK,
        geometry_snapshot=geometry.snapshot() if geometry else {},
        rule_capabilities=caps,
        notes=notes,
    )
