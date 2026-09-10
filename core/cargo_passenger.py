"""Unauthorized Passenger in Applicable Truck/Pickup Cargo Area — review candidate.

Uploaded-video path only for complete multi-frame/clip evidence. Live evaluation
is suppressed until equivalent live evidence support is approved.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from core.detection_config import (
    VIOLATION_CARGO_PASSENGERS,
    VIOLATION_PERSISTENCE_SEC,
    YOLO_CLASS_PERSON,
    is_cargo_passenger_applicable,
)
from core.rule_confidence import score_from_persistence
from core.trajectory import displacement

# Engineering calibration (not legal thresholds).
_MIN_MOTION_PX = 40.0
_CARGO_TRAILING_FRACTION = 0.45
_CARGO_LEAD_EXCLUSION = 0.55
_REL_POS_STABILITY = 0.22
_SHARED_SPEED_TOL = 0.4
_PERSIST_SEC = max(2.0, VIOLATION_PERSISTENCE_SEC)
_BUFFER_MAX = 48


@dataclass
class CargoEvidenceFrame:
    timestamp_sec: float
    frame_number: int
    vehicle_box: tuple[float, float, float, float]
    person_box: tuple[float, float, float, float]
    person_conf: float
    containment: float
    sharpness_proxy: float
    clipped: bool
    rel_x: float
    rel_y: float


@dataclass
class CargoAssociationBuffer:
    vehicle_track_id: int
    person_track_id: int
    frames: deque = field(default_factory=lambda: deque(maxlen=_BUFFER_MAX))
    started_at: float | None = None
    last_ts: float | None = None

    def add(self, item: CargoEvidenceFrame) -> None:
        if self.started_at is None:
            self.started_at = item.timestamp_sec
        self.last_ts = item.timestamp_sec
        self.frames.append(item)

    def duration(self) -> float:
        if self.started_at is None or self.last_ts is None:
            return 0.0
        return max(0.0, self.last_ts - self.started_at)

    def select_evidence(self) -> dict[str, Any]:
        """Choose contextual, clearest-person, supporting frames — not largest box alone."""
        items = list(self.frames)
        if not items:
            return {}
        contextual = max(
            items,
            key=lambda f: f.vehicle_box[2] * f.vehicle_box[3] * (0.5 if f.clipped else 1.0),
        )
        clearest = max(
            items,
            key=lambda f: (
                f.containment * 0.35
                + f.person_conf * 0.2
                + f.sharpness_proxy * 0.2
                + (0.0 if f.clipped else 0.15)
                + min(1.0, (f.person_box[2] * f.person_box[3]) / 8000.0) * 0.1
            ),
        )
        supporting = [f for f in items if f is not contextual and f is not clearest][:3]
        return {
            "contextual_frame": contextual.frame_number,
            "clearest_person_frame": clearest.frame_number,
            "supporting_frames": [f.frame_number for f in supporting],
            "episode_start_sec": self.started_at,
            "episode_end_sec": self.last_ts,
            "factor_scores": _score_factors(items),
        }


def _score_factors(items: list[CargoEvidenceFrame]) -> dict[str, float]:
    if not items:
        return {}
    return {
        "anchor_containment": sum(f.containment for f in items) / len(items),
        "shared_motion_agreement": 0.8,
        "relative_position_stability": 1.0
        - min(
            1.0,
            max(
                abs(items[-1].rel_x - items[0].rel_x),
                abs(items[-1].rel_y - items[0].rel_y),
            )
            / max(_REL_POS_STABILITY, 1e-6),
        ),
        "persistence_duration": min(1.0, (items[-1].timestamp_sec - items[0].timestamp_sec) / _PERSIST_SEC),
        "orientation_stability": 0.75,
        "person_visibility": sum(0.0 if f.clipped else f.person_conf for f in items)
        / len(items),
        "vehicle_cargo_visibility": 0.7,
        "detector_confidence": sum(f.person_conf for f in items) / len(items),
    }


def trailing_cargo_region(
    box: tuple[float, float, float, float],
    motion_dx: float,
    motion_dy: float,
) -> dict[str, float]:
    """Conservative trailing cargo candidate region opposite stable movement."""
    x, y, w, h = box
    mag = math.hypot(motion_dx, motion_dy) or 1.0
    ux, uy = motion_dx / mag, motion_dy / mag
    # Trailing direction is opposite motion.
    tx, ty = -ux, -uy
    # Project box into motion axis; keep trailing fraction.
    # Approximate axis-aligned ROI biased toward trailing side.
    if abs(tx) >= abs(ty):
        # Horizontal-dominant: trailing is left if moving right, else right.
        if tx < 0:
            return {"bbox_x": x, "bbox_y": y + h * 0.2, "bbox_w": w * _CARGO_TRAILING_FRACTION, "bbox_h": h * 0.8}
        return {
            "bbox_x": x + w * _CARGO_LEAD_EXCLUSION,
            "bbox_y": y + h * 0.2,
            "bbox_w": w * _CARGO_TRAILING_FRACTION,
            "bbox_h": h * 0.8,
        }
    # Vertical-dominant
    if ty < 0:
        return {"bbox_x": x + w * 0.1, "bbox_y": y, "bbox_w": w * 0.8, "bbox_h": h * _CARGO_TRAILING_FRACTION}
    return {
        "bbox_x": x + w * 0.1,
        "bbox_y": y + h * _CARGO_LEAD_EXCLUSION,
        "bbox_w": w * 0.8,
        "bbox_h": h * _CARGO_TRAILING_FRACTION,
    }


def person_lower_anchor(person: dict[str, Any]) -> tuple[float, float] | None:
    """Bottom-center anchor; None when lower body appears cropped."""
    x = float(person["bbox_x"])
    y = float(person["bbox_y"])
    w = float(person["bbox_w"])
    h = float(person["bbox_h"])
    frame_h = person.get("frame_h")
    if frame_h is not None and (y + h) >= float(frame_h) - 1.0:
        return None
    if person.get("lower_body_visible") is False:
        return None
    return (x + w / 2.0, y + h)


def anchor_in_region(anchor: tuple[float, float], region: dict[str, float]) -> bool:
    ax, ay = anchor
    return (
        region["bbox_x"] <= ax <= region["bbox_x"] + region["bbox_w"]
        and region["bbox_y"] <= ay <= region["bbox_y"] + region["bbox_h"]
    )


def relative_position(
    anchor: tuple[float, float],
    vehicle_box: tuple[float, float, float, float],
) -> tuple[float, float]:
    x, y, w, h = vehicle_box
    if w <= 0 or h <= 0:
        return (0.5, 0.5)
    return ((anchor[0] - x) / w, (anchor[1] - y) / h)


def evaluate_cargo_passenger_candidates(
    tracked: list[dict[str, Any]],
    state: Any,
    frame_number: int,
    *,
    history: Any | None,
    live_mode: bool = False,
    emit_fn: Any,
) -> list[Any]:
    """Return review candidates; suppress entirely in live_mode."""
    if live_mode:
        state.diagnostics.append(
            "Unauthorized Passenger in Applicable Truck/Pickup Cargo Area: "
            "live evaluation suppressed pending equivalent multi-frame/clip evidence support."
        )
        return []

    vehicles = [
        d for d in tracked if is_cargo_passenger_applicable(str(d.get("class_label", "")))
    ]
    persons = [d for d in tracked if d.get("class_label") == YOLO_CLASS_PERSON]
    events: list[Any] = []

    buffers: dict[tuple[int, int], CargoAssociationBuffer] = state.contextual.setdefault(
        "_cargo_buffers", {}
    )

    active_keys: set[tuple[int, int]] = set()

    for veh in vehicles:
        track_id = int(veh["track_id"])
        ts = float(veh.get("timestamp_sec", 0))
        vbox = (
            float(veh["bbox_x"]),
            float(veh["bbox_y"]),
            float(veh["bbox_w"]),
            float(veh["bbox_h"]),
        )

        # Stable motion from history when available.
        motion_ok = False
        dx = dy = 0.0
        if history is not None:
            snap = history.get(track_id)
            if snap is not None and len(snap.observations) >= 3:
                pts = snap.centroids()
                disp = displacement(pts, min_distance=_MIN_MOTION_PX)
                if disp.sufficient:
                    dx, dy = disp.dx, disp.dy
                    motion_ok = True
        if not motion_ok:
            heading = veh.get("direction_degrees")
            speed = float(veh.get("speed_px_per_sec") or 0.0)
            if heading is None or speed < 5.0:
                state.note_condition(VIOLATION_CARGO_PASSENGERS, track_id, False, ts)
                continue
            rad = math.radians(float(heading))
            dx, dy = math.cos(rad) * speed, math.sin(rad) * speed
            motion_ok = True

        if veh.get("cargo_region_observable") is False:
            state.note_condition(VIOLATION_CARGO_PASSENGERS, track_id, False, ts)
            continue

        region = trailing_cargo_region(vbox, dx, dy)
        v_speed = float(veh.get("speed_px_per_sec") or 0.0)

        for person in persons:
            pid = int(person["track_id"])
            key = (track_id, pid)
            anchor = person_lower_anchor(person)
            if anchor is None:
                buffers.pop(key, None)
                continue
            if not anchor_in_region(anchor, region):
                buffers.pop(key, None)
                continue
            p_speed = float(person.get("speed_px_per_sec") or 0.0)
            shared = abs(v_speed - p_speed) <= max(
                8.0, _SHARED_SPEED_TOL * max(v_speed, p_speed, 1.0)
            )
            if not shared and v_speed > 5.0:
                buffers.pop(key, None)
                continue

            rel = relative_position(anchor, vbox)
            buf = buffers.get(key)
            if buf is None:
                buf = CargoAssociationBuffer(vehicle_track_id=track_id, person_track_id=pid)
                buffers[key] = buf
            elif buf.frames:
                prev = buf.frames[-1]
                if (
                    abs(rel[0] - prev.rel_x) > _REL_POS_STABILITY
                    or abs(rel[1] - prev.rel_y) > _REL_POS_STABILITY
                ):
                    buffers.pop(key, None)
                    continue

            clipped = bool(person.get("clipped") or person.get("frame_boundary_clip"))
            item = CargoEvidenceFrame(
                timestamp_sec=ts,
                frame_number=frame_number,
                vehicle_box=vbox,
                person_box=(
                    float(person["bbox_x"]),
                    float(person["bbox_y"]),
                    float(person["bbox_w"]),
                    float(person["bbox_h"]),
                ),
                person_conf=float(person.get("confidence", 0)),
                containment=1.0,
                sharpness_proxy=float(person.get("sharpness", 0.5)),
                clipped=clipped,
                rel_x=rel[0],
                rel_y=rel[1],
            )
            buf.add(item)
            active_keys.add(key)

            condition = buf.duration() >= _PERSIST_SEC and len(buf.frames) >= 3
            tracker = state.tracker_for("cargo_passenger", track_id)
            if tracker.update(condition, ts, threshold_sec=_PERSIST_SEC) and condition:
                evidence = buf.select_evidence()
                score = score_from_persistence(
                    detection_confidence=float(veh.get("confidence", 0)),
                    elapsed_sec=tracker.elapsed(ts),
                    required_sec=_PERSIST_SEC,
                    association_quality=min(1.0, 0.5 + 0.1 * len(buf.frames)),
                    contextual_availability=0.75,
                )
                # Attach evidence metadata onto detection for persistence layer.
                veh = dict(veh)
                veh["_cargo_evidence"] = evidence
                veh["_cargo_person_track_id"] = pid
                emit_fn(
                    state,
                    events,
                    VIOLATION_CARGO_PASSENGERS,
                    veh,
                    frame_number,
                    f"{veh.get('class_label', 'vehicle')} track #{track_id}: person "
                    f"#{pid} stable in movement-relative cargo region for "
                    f">={_PERSIST_SEC}s (manual review).",
                    score,
                    outcome="review",
                )
            state.note_condition(VIOLATION_CARGO_PASSENGERS, track_id, condition, ts)

    # Drop inactive associations this frame.
    for key in list(buffers.keys()):
        if key not in active_keys:
            buffers.pop(key, None)

    return events
