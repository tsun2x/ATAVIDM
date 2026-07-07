"""Rule-based violation logic (9 classes) — Phase 3.

Zone polygons come from video annotations at runtime.
Helmet and motorcycle-overloading rules use YOLO detections + ByteTrack IDs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.detection_config import (
    MOTORCYCLE_OVERLOADING,
    NO_HELMET_VIOLATION,
    RIDER_ASSOCIATION_PADDING,
    VIOLATION_PERSISTENCE_SEC,
    YOLO_CLASS_HELMET,
    YOLO_CLASS_MOTORCYCLE,
    YOLO_CLASS_PERSON,
)

VIOLATION_ZONE_RULES = (
    "Illegal Parking",
    "Counterflowing",
    "Obstruction",
    "Illegal Loading/Unloading",
    "Blocking Pedestrian Crossing",
    "Truck Ban",
    "Reckless Driving",
)


@dataclass
class ViolationEvent:
    violation_type: str
    track_id: int
    confidence: float
    frame_number: int
    timestamp_sec: float
    reason_log: str


@dataclass
class _PersistenceTracker:
    """Tracks how long a condition has been continuously true for a track."""

    started_at: float | None = None

    def update(self, condition: bool, timestamp_sec: float) -> bool:
        if condition:
            if self.started_at is None:
                self.started_at = timestamp_sec
            return (timestamp_sec - self.started_at) >= VIOLATION_PERSISTENCE_SEC
        self.started_at = None
        return False


@dataclass
class RuleEngineState:
    no_helmet: dict[int, _PersistenceTracker] = field(default_factory=dict)
    motorcycle_overloading: dict[int, _PersistenceTracker] = field(default_factory=dict)


def _bbox_center(det: dict[str, Any]) -> tuple[float, float]:
    x = float(det["bbox_x"]) + float(det["bbox_w"]) / 2
    y = float(det["bbox_y"]) + float(det["bbox_h"]) / 2
    return x, y


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


def _latest_by_track(detections: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    by_track: dict[int, dict[str, Any]] = {}
    for det in detections:
        tid = det.get("track_id")
        if tid is None:
            continue
        by_track[int(tid)] = det
    return by_track


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


def check_no_helmet(
    tracked: list[dict[str, Any]],
    state: RuleEngineState,
    frame_number: int,
) -> list[ViolationEvent]:
    """
    IF motorcycle + rider detected AND no helmet on rider for >= persistence window
    THEN No Helmet Violation.
    """
    if not any(d.get("class_label") == YOLO_CLASS_HELMET for d in tracked):
        # Model does not output helmet class yet — do not fabricate violations.
        return []

    motorcycles = [d for d in tracked if d.get("class_label") == YOLO_CLASS_MOTORCYCLE]
    persons = [d for d in tracked if d.get("class_label") == YOLO_CLASS_PERSON]
    helmets = [d for d in tracked if d.get("class_label") == YOLO_CLASS_HELMET]

    events: list[ViolationEvent] = []
    for mc in motorcycles:
        track_id = int(mc["track_id"])
        riders = _associate_riders(mc, persons)
        if not riders:
            state.no_helmet.pop(track_id, None)
            continue

        unhelmeted = [r for r in riders if not _rider_has_helmet(r, helmets)]
        condition = len(unhelmeted) > 0
        tracker = state.no_helmet.setdefault(track_id, _PersistenceTracker())
        ts = float(mc.get("timestamp_sec", 0))

        if tracker.update(condition, ts):
            rider_ids = ", ".join(str(int(r["track_id"])) for r in unhelmeted)
            events.append(
                ViolationEvent(
                    violation_type=NO_HELMET_VIOLATION,
                    track_id=track_id,
                    confidence=float(min(r["confidence"] for r in unhelmeted)),
                    frame_number=frame_number,
                    timestamp_sec=ts,
                    reason_log=(
                        f"Motorcycle track #{track_id}: rider(s) #{rider_ids} "
                        f"without helmet for >={VIOLATION_PERSISTENCE_SEC}s."
                    ),
                )
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
        condition = len(riders) > 2
        tracker = state.motorcycle_overloading.setdefault(track_id, _PersistenceTracker())
        ts = float(mc.get("timestamp_sec", 0))

        if tracker.update(condition, ts):
            events.append(
                ViolationEvent(
                    violation_type=MOTORCYCLE_OVERLOADING,
                    track_id=track_id,
                    confidence=float(mc.get("confidence", 0)),
                    frame_number=frame_number,
                    timestamp_sec=ts,
                    reason_log=(
                        f"Motorcycle track #{track_id}: {len(riders)} riders detected "
                        f"(max 2) for >={VIOLATION_PERSISTENCE_SEC}s."
                    ),
                )
            )
    return events


def evaluate_detection_rules(
    tracked: list[dict[str, Any]],
    state: RuleEngineState,
    frame_number: int,
    zones: dict[str, Any] | None = None,
) -> list[ViolationEvent]:
    """
    Run all detection-based rules on the current frame.

    Zone-based rules (parking, counterflow, etc.) will be added in Phase 3.
    """
    _ = zones
    events: list[ViolationEvent] = []
    events.extend(check_no_helmet(tracked, state, frame_number))
    events.extend(check_motorcycle_overloading(tracked, state, frame_number))
    return events
