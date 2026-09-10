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
    riders: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Associate motorcycle with rider detections only (never person)."""
    associated: list[dict[str, Any]] = []
    for rider in riders:
        if rider.get("class_label") != YOLO_CLASS_RIDER:
            continue
        center = _bbox_center(rider)
        if _point_in_padded_bbox(center, motorcycle, RIDER_ASSOCIATION_PADDING):
            associated.append(rider)
    return associated


def _rider_detections(tracked: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [d for d in tracked if d.get("class_label") == YOLO_CLASS_RIDER]


def _rider_capability_ok(
    state: RuleEngineState,
    rule_name: str,
    model_classes: tuple[str, ...] | None,
) -> bool:
    if model_classes is None:
        return True
    available = {str(c).lower() for c in model_classes}
    if YOLO_CLASS_RIDER.lower() not in available:
        state.diagnostics.append(
            f"{rule_name}: automatic evaluation disabled — rider class unavailable; "
            "person detections cannot substitute."
        )
        return False
    return True


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
            "No Helmet automatic evaluation disabled: required helmet/rider classes missing."
        )
        return []
    if not _rider_capability_ok(state, VIOLATION_NO_HELMET, model_classes):
        return []

    motorcycles = [d for d in tracked if d.get("class_label") == YOLO_CLASS_MOTORCYCLE]
    riders_pool = _rider_detections(tracked)
    acceptable, nut, _ = _helmet_labels(tracked)

    events: list[ViolationEvent] = []
    for mc in motorcycles:
        track_id = int(mc["track_id"])
        riders = _associate_riders(mc, riders_pool)
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
        state.diagnostics.append(
            "Substandard / Nut-Shell Helmet: automatic evaluation disabled "
            "(missing rider/nut-shell prerequisites)."
        )
        return []
    if not _rider_capability_ok(state, VIOLATION_SUBSTANDARD_HELMET, model_classes):
        return []

    motorcycles = [d for d in tracked if d.get("class_label") == YOLO_CLASS_MOTORCYCLE]
    riders_pool = _rider_detections(tracked)
    acceptable, nut, _ = _helmet_labels(tracked)
    events: list[ViolationEvent] = []
    for mc in motorcycles:
        track_id = int(mc["track_id"])
        riders = _associate_riders(mc, riders_pool)
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
    """No Side Mirror — permanently manual-review only.

    One detected mirror proves presence of at least one mirror, not two.
    Missing detection never proves absence alone. Candidates require multi-frame
    evidence with both mounting areas clearly observable.
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
    events: list[ViolationEvent] = []
    persist = float(VIOLATION_PERSISTENCE_SEC) + 1.0

    for veh in vehicles:
        track_id = int(veh["track_id"])
        ts = float(veh.get("timestamp_sec", 0))
        # Real observation producer: tri-state mounting visibility.
        visibility = veh.get("mirror_roi_visibility")
        if visibility is None:
            # Absent producer → UNKNOWN (fail closed). Never hardcode True.
            visibility = "unknown"
        if visibility is True:
            visibility = "both_visible"
        if visibility is False:
            visibility = "unsuitable"

        associated = []
        vx, vy = float(veh["bbox_x"]), float(veh["bbox_y"])
        vw, vh = float(veh["bbox_w"]), float(veh["bbox_h"])
        for m in mirrors:
            if _iou(veh, m) > 0 or _boxes_overlap(veh, m):
                mx = float(m["bbox_x"]) + float(m["bbox_w"]) / 2
                my = float(m["bbox_y"]) + float(m["bbox_h"]) / 2
                if vx <= mx <= vx + vw and vy <= my <= vy + vh * 0.6:
                    side = "left" if mx < vx + vw / 2 else "right"
                    associated.append(side)

        unique_sides = set(associated)
        ctx = state.contextual.setdefault(
            ("side_mirror", track_id),
            {
                "state": "UNKNOWN",
                "present_frames": 0,
                "suspect_frames": 0,
                "mirror_count_samples": [],
            },
        )

        if len(unique_sides) >= 2:
            ctx["state"] = "PRESENT_BOTH"
            ctx["present_frames"] = int(ctx.get("present_frames", 0)) + 1
            ctx["suspect_frames"] = 0
            state.persistence.pop(("no_side_mirror", track_id), None)
            state.note_condition(VIOLATION_NO_SIDE_MIRROR, track_id, False, ts)
            continue

        if len(unique_sides) == 1:
            # One associated mirror proves at least one is present. Missing
            # mirror_roi_visibility must not erase that evidence or claim both.
            if ctx.get("state") != "PRESENT_BOTH":
                ctx["state"] = "PRESENT"
            ctx["present_frames"] = int(ctx.get("present_frames", 0)) + 1

        if visibility not in ("both_visible", "clear"):
            # Cropped/blurred/distant/occluded/unknown: no candidate.
            # Do not prove absence, and do not overwrite PRESENT / PRESENT_BOTH.
            if ctx.get("state") not in ("PRESENT", "PRESENT_BOTH"):
                ctx["state"] = "UNKNOWN"
            ctx["suspect_frames"] = 0
            state.persistence.pop(("no_side_mirror", track_id), None)
            state.note_condition(VIOLATION_NO_SIDE_MIRROR, track_id, False, ts)
            continue

        if len(unique_sides) == 1:
            # Both mounting areas observable and only one mirror → review candidate.
            # Keep PRESENT as the evidence state (never PRESENT_BOTH).
            ctx["suspect_kind"] = "SUSPECT_ONE"
            ctx["suspect_frames"] = int(ctx.get("suspect_frames", 0)) + 1
        elif len(unique_sides) == 0:
            ctx["state"] = "SUSPECT_ZERO"
            ctx["suspect_kind"] = "SUSPECT_ZERO"
            ctx["suspect_frames"] = int(ctx.get("suspect_frames", 0)) + 1
        else:
            ctx["suspect_frames"] = 0

        condition = ctx["suspect_frames"] > 0 and ctx.get("suspect_kind") in (
            "SUSPECT_ONE",
            "SUSPECT_ZERO",
        )
        tracker = state.tracker_for("no_side_mirror", track_id)
        if tracker.update(condition, ts, threshold_sec=persist) and condition:
            score = score_from_persistence(
                detection_confidence=float(veh.get("confidence", 0)),
                elapsed_sec=tracker.elapsed(ts),
                required_sec=persist,
                contextual_availability=0.55,
            )
            _emit(
                state,
                events,
                VIOLATION_NO_SIDE_MIRROR,
                veh,
                frame_number,
                f"Vehicle track #{track_id}: mirror mounting areas observable with "
                f"{len(unique_sides)} usable mirror observation(s) across multiple "
                f"frames — pending manual review (never auto-confirmed).",
                score,
                outcome="review",
            )
        state.note_condition(VIOLATION_NO_SIDE_MIRROR, track_id, condition, ts)
    return events


def check_motorcycle_overloading(
    tracked: list[dict[str, Any]],
    state: RuleEngineState,
    frame_number: int,
    *,
    model_classes: tuple[str, ...] | None = None,
) -> list[ViolationEvent]:
    """More than two riders actually associated with the motorcycle."""
    if not _rule_allowed(state, VIOLATION_MOTORCYCLE_OVERLOADING):
        return []
    if model_classes is not None and not classes_satisfy_rule(
        VIOLATION_MOTORCYCLE_OVERLOADING, model_classes
    ):
        state.diagnostics.append(
            "Motorcycle Overloading: automatic evaluation disabled "
            "(missing motorcycle/rider classes)."
        )
        return []
    if not _rider_capability_ok(state, VIOLATION_MOTORCYCLE_OVERLOADING, model_classes):
        return []
    motorcycles = [d for d in tracked if d.get("class_label") == YOLO_CLASS_MOTORCYCLE]
    riders_pool = _rider_detections(tracked)
    events: list[ViolationEvent] = []
    for mc in motorcycles:
        track_id = int(mc["track_id"])
        riders = _associate_riders(mc, riders_pool)
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
    params: dict[str, Any] | None = None,
) -> list[ViolationEvent]:
    """Person in truck/pickup cargo region via multi-frame geometric evidence."""
    if not _rule_allowed(state, VIOLATION_CARGO_PASSENGERS):
        return []
    if model_classes is not None and not classes_satisfy_rule(
        VIOLATION_CARGO_PASSENGERS, model_classes
    ):
        return []
    from core.cargo_passenger import evaluate_cargo_passenger_candidates

    params = params or {}
    live_mode = bool(params.get("_live_mode", False))
    history = params.get("_track_history")
    return evaluate_cargo_passenger_candidates(
        tracked,
        state,
        frame_number,
        history=history,
        live_mode=live_mode,
        emit_fn=_emit,
    )


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
    *,
    all_tracked: list[dict[str, Any]] | None = None,
) -> list[ViolationEvent]:
    """Illegal Terminal — PUV + activity region + multi-frame boarding/alighting."""
    if not _rule_allowed(state, VIOLATION_ILLEGAL_TERMINAL):
        return []

    import math as _math

    from core.scene_annotation import RuleSceneContext
    from core.tracker import point_in_polygon

    rule_scene = params.get("_rule_scene")
    activity_regions = []
    if isinstance(rule_scene, RuleSceneContext):
        activity_regions = list(rule_scene.activity_regions)

    frame_persons = [
        d
        for d in (all_tracked or [])
        if d.get("class_label") == YOLO_CLASS_PERSON
    ]

    def _terminal_activity(det: dict[str, Any]) -> TriState:
        label = str(det.get("class_label", ""))

        # Van remains UNKNOWN until a separately approved non-model for-hire source.
        if label == YOLO_CLASS_VAN:
            return TriState.UNKNOWN

        if label not in PUV_CLASSES:
            return TriState.FALSE

        # Compatibility: explicit flags still honored when set.
        if det.get("terminal_passenger_activity") is True:
            return TriState.TRUE
        if det.get("terminal_passenger_activity") is False:
            return TriState.FALSE

        if not activity_regions:
            return TriState.UNKNOWN

        cx = float(det.get("centroid_x", det["bbox_x"] + det["bbox_w"] / 2))
        cy = float(det.get("centroid_y", det["bbox_y"] + det["bbox_h"] / 2))
        in_activity = False
        for region in activity_regions:
            pts = region.points_list()
            if len(pts) >= 3 and point_in_polygon(cx, cy, pts):
                in_activity = True
                break
        if not in_activity:
            return TriState.UNKNOWN

        # Person/vehicle overlap alone is insufficient — need approach geometry.
        evidence = 0
        for person in frame_persons:
            if not _boxes_overlap(det, person):
                # Near-boundary approach still counts when heading toward vehicle.
                px = float(person["bbox_x"] + person["bbox_w"] / 2)
                py = float(person["bbox_y"] + person["bbox_h"] / 2)
                dist = _math.hypot(cx - px, cy - py)
                if dist > max(float(det["bbox_w"]), float(det["bbox_h"])) * 1.2:
                    continue
            p_heading = person.get("direction_degrees")
            if p_heading is None:
                continue
            px = float(person["bbox_x"] + person["bbox_w"] / 2)
            py = float(person["bbox_y"] + person["bbox_h"] / 2)
            toward = _math.degrees(_math.atan2(cy - py, cx - px)) % 360.0
            if angle_difference(float(p_heading), toward) <= 45.0:
                evidence += 1
        if evidence > 0:
            return TriState.TRUE
        return TriState.UNKNOWN

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
            ">={dwell}s with passenger-activity evidence (manual review)."
        ),
        class_filter=PUV_CLASSES,
        geometry=geometry,
        require_extra=_terminal_activity,
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
    """Counterflow using per-lane flow when v2 lanes exist; legacy fallback otherwise."""
    if not _rule_allowed(state, VIOLATION_COUNTERFLOW):
        return []
    from core.scene_annotation import RuleSceneContext
    from core.tracker import point_in_polygon
    from core.trajectory import angle_difference_degrees

    rule_scene = params.get("_rule_scene")
    tolerance = float(params.get("flow_tolerance_degrees", 60.0))
    persist = float(params.get("counterflow_persist_sec", 4.0))
    min_dir = float(params.get("min_direction_px", 40.0))
    if geometry is not None:
        min_dir = geometry.min_direction_px()

    use_v2_lanes = (
        isinstance(rule_scene, RuleSceneContext) and len(rule_scene.lanes) > 0
    )
    legacy_flow = float(params.get("lane_flow_degrees", 90.0))
    # Legacy lane_flow_degrees only when one legacy active_lane and no v2 flows.
    allow_legacy_flow = (not use_v2_lanes) or (
        len(getattr(rule_scene, "lanes", ())) == 0
        and len(getattr(rule_scene, "lane_flows", ())) == 0
        and bool(polygon)
    )

    events: list[ViolationEvent] = []
    for det in vehicles:
        track_id = int(det["track_id"])
        ts = float(det.get("timestamp_sec", 0))
        heading = det.get("direction_degrees")
        cx = float(det.get("centroid_x", det["bbox_x"] + det["bbox_w"] / 2))
        cy = float(det.get("centroid_y", det["bbox_y"] + det["bbox_h"] / 2))

        lane_flow: float | None = None
        inside = False
        ambiguous = False

        if use_v2_lanes:
            containing = []
            for lane in rule_scene.lanes:
                pts = lane.points_list()
                if len(pts) >= 3 and point_in_polygon(cx, cy, pts):
                    containing.append(lane)
            if not containing:
                inside = False
            elif len(containing) > 1:
                flows = []
                flow_by_lane = {f.lane_id: f.degrees for f in rule_scene.lane_flows}
                for lane in containing:
                    if lane.id not in flow_by_lane:
                        ambiguous = True
                        break
                    flows.append(flow_by_lane[lane.id])
                if not ambiguous and flows:
                    # Equivalent directions may resolve; conflicting → UNKNOWN.
                    ref = flows[0]
                    if any(angle_difference_degrees(ref, f) > 30.0 for f in flows[1:]):
                        ambiguous = True
                    else:
                        inside = True
                        lane_flow = ref
                else:
                    ambiguous = True
            else:
                lane = containing[0]
                inside = True
                flow_by_lane = {f.lane_id: f.degrees for f in rule_scene.lane_flows}
                if lane.id not in flow_by_lane:
                    # Missing arrow → UNKNOWN/suppress
                    state.note_condition(VIOLATION_COUNTERFLOW, track_id, False, ts)
                    state.persistence.pop(("counterflow", track_id), None)
                    continue
                lane_flow = flow_by_lane[lane.id]
        else:
            inside = _in_zone(det, polygon, state, "counterflow", policy=POLICY_LANE)
            if allow_legacy_flow:
                lane_flow = legacy_flow

        if ambiguous or heading is None or lane_flow is None:
            state.note_condition(VIOLATION_COUNTERFLOW, track_id, False, ts)
            state.persistence.pop(("counterflow", track_id), None)
            continue

        opposite = (float(lane_flow) + 180.0) % 360.0
        condition = (
            inside
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
                f"against lane flow ({float(lane_flow):.0f}deg) "
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

    # Preferred path: configured marking segments with prohibited_from.
    markings = marking_geometry or params.get("marking_geometry") or []
    history = params.get("_track_history")
    if markings:
        from core.trajectory import crossed_oriented_line

        for det in vehicles:
            track_id = int(det["track_id"])
            ts = float(det.get("timestamp_sec", 0))
            heading = det.get("direction_degrees")
            crossed = False
            for marking in markings:
                mtype = str(marking.get("type") or "")
                # Review-only types until separately approved.
                if mtype in (
                    "single_solid",
                    "solid_broken",
                    "generic_marking",
                ):
                    continue
                geom = (
                    marking.get("polygon")
                    or marking.get("polyline")
                    or marking.get("points")
                    or []
                )
                if len(geom) < 2:
                    continue
                prohibited = marking.get("prohibited_from") or marking.get(
                    "prohibited_side"
                )
                if mtype in ("double_solid", "marking_double_solid"):
                    prohibited = prohibited or "both"
                if prohibited not in ("left", "right", "both"):
                    continue
                prev = curr = None
                if history is not None:
                    snap = history.get(track_id)
                    if snap is not None and len(snap.observations) >= 2:
                        o0, o1 = snap.observations[-2], snap.observations[-1]
                        prev = (o0.x, o0.y)
                        curr = (o1.x, o1.y)
                if prev is None or curr is None or heading is None:
                    continue
                a = (float(geom[0][0]), float(geom[0][1]))
                b = (float(geom[-1][0]), float(geom[-1][1]))
                if crossed_oriented_line(
                    prev, curr, a, b, prohibited_from=str(prohibited)
                ):
                    crossed = True
                    break
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
                    f"Vehicle track #{track_id} crossed pavement marking from a "
                    f"prohibited side (manual review).",
                    score,
                    outcome="review",
                )
            state.note_condition(VIOLATION_PAVEMENT_MARKINGS, track_id, crossed, ts)
        return events

    # Restricted-lane: outside-to-inside transition with hysteresis.
    if restricted_lane and len(restricted_lane) >= 3:
        from core.trajectory import ZoneMembershipHysteresis, point_in_polygon as tip

        restricted = tuple(
            params.get("restricted_lane_classes", DEFAULT_RESTRICTED_LANE_CLASSES)
        )
        for det in vehicles:
            if det.get("class_label") not in restricted:
                continue
            track_id = int(det["track_id"])
            ts = float(det.get("timestamp_sec", 0))
            cx = float(det.get("centroid_x", det["bbox_x"] + det["bbox_w"] / 2))
            cy = float(det.get("centroid_y", det["bbox_y"] + det["bbox_h"] / 2))
            currently = tip((cx, cy), restricted_lane)
            hyst = state.contextual.setdefault(
                ("restricted_hyst", track_id),
                ZoneMembershipHysteresis(enter_frames=2, exit_frames=2),
            )
            transition = hyst.update(currently)
            entered = transition == "entered"
            tracker = state.tracker_for("restricted_lane", track_id)
            if tracker.update(entered or currently, ts) and entered:
                score = score_from_persistence(
                    detection_confidence=float(det.get("confidence", 0)),
                    elapsed_sec=tracker.elapsed(ts),
                    required_sec=VIOLATION_PERSISTENCE_SEC,
                    contextual_availability=0.6,
                )
                _emit(
                    state,
                    events,
                    VIOLATION_PAVEMENT_MARKINGS,
                    det,
                    frame_number,
                    f"{det.get('class_label', 'vehicle')} track #{track_id} "
                    f"entered Restricted Lane (outside-to-inside; manual review).",
                    score,
                    outcome="review",
                )
            state.note_condition(VIOLATION_PAVEMENT_MARKINGS, track_id, entered, ts)
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
    """Disregarding Traffic Sign — no-entry threshold crossing only in this release.

    Other maneuver signs remain review-only/unsupported. Do not rely on unset
    performed_* fields as the normal path.
    """
    if not _rule_allowed(state, VIOLATION_DISREGARDING_SIGN):
        return []

    from core.scene_annotation import RuleSceneContext
    from core.trajectory import (
        crossed_oriented_line,
        endpoint_jitter,
        is_parallel_motion,
        motion_projects_into_direction,
    )

    rule_scene = params.get("_rule_scene")
    history = params.get("_track_history")
    signs = []
    thresholds = []
    if isinstance(rule_scene, RuleSceneContext) and rule_scene.signs:
        signs = list(rule_scene.signs)
        thresholds = list(rule_scene.threshold_lines)
    else:
        signs = params.get("supported_signs") or []

    if not signs:
        state.diagnostics.append(
            "Disregarding Traffic Sign: no supported sign annotations configured; "
            "automatic evaluation disabled."
        )
        return []

    events: list[ViolationEvent] = []
    vehicles = [d for d in tracked if _is_usable_vehicle_detection(d)]
    min_dir = float(params.get("min_direction_px", 40.0))

    for sign in signs:
        if hasattr(sign, "type"):
            stype = str(sign.type).lower()
            sign_lane_ids = list(sign.lane_ids)
            sign_id = sign.id
        else:
            stype = str(sign.get("type", "")).lower()
            sign_lane_ids = list(sign.get("lane_ids") or [])
            sign_id = str(sign.get("id") or stype)

        if stype in ("stop", "speed_limit", "sign_stop", "sign_speed_limit"):
            continue
        # Only no-entry is executable; others stay unsupported.
        if stype not in ("no_entry", "sign_no_entry"):
            state.diagnostics.append(
                f"Disregarding Traffic Sign: '{stype}' remains review-only/unsupported "
                "in this release (no-entry only)."
            )
            continue

        # Resolve threshold: matching threshold_line or points on the sign.
        line_a = line_b = None
        prohibited_vec = None
        for th in thresholds:
            if sign_lane_ids and th.lane_ids and not set(sign_lane_ids) & set(th.lane_ids):
                continue
            if len(th.points) >= 2:
                line_a = th.points[0]
                line_b = th.points[-1]
                # Prohibited direction: A→B normal into the restricted side, or
                # metadata vector when provided.
                meta = th.metadata if hasattr(th, "metadata") else {}
                if meta.get("prohibited_vector"):
                    pv = meta["prohibited_vector"]
                    prohibited_vec = (float(pv[0]), float(pv[1]))
                else:
                    # Default: crossing from A-left toward A-right is not assumed;
                    # use segment direction as prohibited motion projection axis.
                    prohibited_vec = (line_b[0] - line_a[0], line_b[1] - line_a[1])
                    # Prefer perpendicular into "entry" if annotated.
                    if meta.get("prohibited_from") == "left":
                        prohibited_vec = (
                            -(line_b[1] - line_a[1]),
                            (line_b[0] - line_a[0]),
                        )
                    elif meta.get("prohibited_from") == "right":
                        prohibited_vec = (
                            (line_b[1] - line_a[1]),
                            -(line_b[0] - line_a[0]),
                        )
                break
        if line_a is None and hasattr(sign, "points") and len(sign.points) >= 2:
            line_a, line_b = sign.points[0], sign.points[-1]
            prohibited_vec = (line_b[0] - line_a[0], line_b[1] - line_a[1])
        if line_a is None or line_b is None or prohibited_vec is None:
            continue

        for det in vehicles:
            track_id = int(det["track_id"])
            ts = float(det.get("timestamp_sec", 0))
            prev = curr = None
            if history is not None:
                snap = history.get(track_id)
                if snap is not None and len(snap.observations) >= 2:
                    # Bottom-center approx from centroids for crossing.
                    o0, o1 = snap.observations[-2], snap.observations[-1]
                    prev = (o0.x, o0.y + float(det.get("bbox_h", 0)) / 2)
                    curr = (o1.x, o1.y + float(det.get("bbox_h", 0)) / 2)
            if prev is None or curr is None:
                state.note_condition(VIOLATION_DISREGARDING_SIGN, track_id, False, ts)
                continue
            motion = (curr[0] - prev[0], curr[1] - prev[1])
            if endpoint_jitter([prev, curr], max_jitter=min_dir * 0.25):
                continue
            if is_parallel_motion(motion, line_a, line_b):
                continue
            if not motion_projects_into_direction(
                motion, prohibited_vec, min_projection=min_dir * 0.5
            ):
                continue
            conflict = crossed_oriented_line(
                prev, curr, line_a, line_b, prohibited_from="both"
            )
            tracker = state.tracker_for(f"sign_no_entry_{sign_id}", track_id)
            if tracker.update(conflict, ts):
                score = score_from_persistence(
                    detection_confidence=float(det.get("confidence", 0)),
                    elapsed_sec=tracker.elapsed(ts),
                    required_sec=VIOLATION_PERSISTENCE_SEC,
                    contextual_availability=0.75,
                )
                _emit(
                    state,
                    events,
                    VIOLATION_DISREGARDING_SIGN,
                    det,
                    frame_number,
                    f"Vehicle track #{track_id} crossed no-entry threshold "
                    f"({sign_id}) in prohibited direction (manual review).",
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
    scene: Any | None = None,
    history: Any | None = None,
) -> list[ViolationEvent]:
    """Run every enabled rule on the current frame's tracked detections.

    ``now_time`` is the scene/recording clock when known. When recording
    datetime is unavailable, time-dependent rules receive UNKNOWN and must
    not use upload/processing wall-clock time.

    ``scene`` is the preferred ``RuleSceneContext``. Raw ``zones`` remains a
    compatibility-only input. ``history`` is an optional immutable
    ``TrackHistoryView`` for trajectory-aware rules.
    """
    from core.scene_annotation import SceneAnnotationError, merge_rule_scene_inputs

    merged = dict(DEFAULT_RULE_PARAMETERS)
    if params:
        merged.update(params)
    zones = zones or {}
    try:
        rule_scene = merge_rule_scene_inputs(scene=scene, zones=zones, params=merged)
    except SceneAnnotationError as exc:
        raise ValueError(str(exc)) from exc
    # Compatibility zones mirror the projected legacy map for existing rules.
    zones = dict(rule_scene.legacy_zones)
    # Stash for rules that opt into structured scene / history (Gates 5–8).
    merged["_rule_scene"] = rule_scene
    merged["_track_history"] = history

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
        merged["_rule_scene"] = rule_scene
        merged["_track_history"] = history

    # Capability gate snapshot (once per state unless refreshed by caller).
    if model_classes is not None and not state.capability:
        context_flags = {
            "zone:no_parking": bool(zones.get("no_parking")),
            "zone:active_lane": bool(zones.get("active_lane") or rule_scene.lanes),
            "zone:active_lane_or_crossing": bool(
                zones.get("active_lane")
                or rule_scene.lanes
                or zones.get("pedestrian_crossing")
            ),
            "zone:truck_ban_zone": bool(zones.get("truck_ban_zone")),
            "zone:loading_unloading_or_terminal": bool(
                zones.get("loading_unloading") or rule_scene.activity_regions
            ),
            "recording_datetime": bool(
                recording_time_known
                if recording_time_known is not None
                else now_time is not None
            ),
            "lane_flow_degrees": "lane_flow_degrees" in merged
            or bool(rule_scene.lane_flows),
            "marking_geometry": bool(
                merged.get("marking_geometry")
                or zones.get("restricted_lane")
                or rule_scene.markings
            ),
            "supported_sign_annotations": bool(
                merged.get("supported_signs") or rule_scene.signs
            ),
            "puv_context": True,  # evaluated per-detection as TriState
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
        events.extend(
            check_motorcycle_overloading(
                tracked, state, frame_number, model_classes=model_classes
            )
        )
    if VIOLATION_CARGO_PASSENGERS in enabled_set:
        events.extend(
            check_cargo_passenger(
                tracked,
                state,
                frame_number,
                model_classes=model_classes,
                params=merged,
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
    # Counterflow: v2 lanes or legacy active_lane polygon.
    if VIOLATION_COUNTERFLOW in enabled_set and (
        zones.get("active_lane") or getattr(rule_scene, "lanes", ())
    ):
        events.extend(
            check_counterflow(
                vehicles,
                zones.get("active_lane") or [],
                state,
                frame_number,
                merged,
                geometry,
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
                all_tracked=tracked,
            )
        )
    if VIOLATION_PAVEMENT_MARKINGS in enabled_set:
        scene_markings = None
        if getattr(rule_scene, "markings", None):
            scene_markings = [
                {
                    "id": m.id,
                    "type": m.type,
                    "polyline": m.points_list(),
                    "prohibited_from": m.prohibited_from,
                    "lane_ids": list(m.lane_ids),
                }
                for m in rule_scene.markings
            ]
        events.extend(
            check_pavement_markings(
                vehicles,
                state,
                frame_number,
                merged,
                restricted_lane=zones.get("restricted_lane"),
                marking_geometry=scene_markings,
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
