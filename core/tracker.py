"""Per-track motion history on top of ByteTrack IDs.

ByteTrack (run inside ``core.detector``) provides stable track IDs; this module
maintains the per-track state the rule engine needs (manuscript Ch3, Layer 4):
- trajectory analysis: centroid history per track
- direction analysis: travel heading in degrees
- dwell-time analysis: how long a track has been stationary
- object counting: tracks currently present, by class
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping

from core.detection_config import VEHICLE_CLASSES


# Seconds without an update before a track is discarded.
TRACK_EXPIRY_SEC = 5.0
# Sliding window (seconds) used for speed/direction estimation.
MOTION_WINDOW_SEC = 2.0
# Longest current rule consumer needs nine seconds (four-second smoothing plus
# a five-second parking comparison). Retain a small time margin independent
# of processed frame rate; histories are also cleared on normal track expiry.
TRACK_HISTORY_RETENTION_SEC = 10.0
# Brief changes between recognized vehicle labels do not by themselves create
# a new physical track: the detector can alternate labels while ByteTrack
# keeps the same spatially continuous identity. Non-vehicle class changes still
# start a new identity immediately.
_VEHICLE_CLASS_FLICKER_GROUP = frozenset(VEHICLE_CLASSES)
# Preserve the existing delayed class resolution for the two labels whose
# distinction controls cargo-area applicability. Other initial vehicle labels
# keep their existing immediate resolved class.
_DEFERRED_CLASS_RESOLUTION = frozenset({"car", "pickup_truck"})
# A contradictory vehicle label does not replace the resolved class or erase
# motion history until it has lasted MOTION_WINDOW_SEC.
# Units: seconds of track time. This reuses MOTION_WINDOW_SEC; it is not a
# separate user setting and was not tuned on held-out WMSU footage.
# Sensitivity: a shorter window lets a brief false pickup become cargo-applicable;
# a longer window delays cargo eligibility for a stable pickup.
# Centroid jump, in pixels, above which a reused ByteTrack ID is a new vehicle:
# observed_speed * dt + IDENTITY_JUMP_BOX_LENGTHS * max(bbox side).
# IDENTITY_JUMP_BOX_LENGTHS is a dimensionless multiple of the longer box side.
# Sensitivity: lower values split fast vehicles on sparse frames; higher values
# can keep a reused ID. Not tuned on held-out WMSU footage.
IDENTITY_JUMP_BOX_LENGTHS = 4.0


@dataclass(frozen=True)
class CentroidObservation:
    timestamp_sec: float
    x: float
    y: float


@dataclass(frozen=True)
class TrackHistorySnapshot:
    track_id: int
    class_label: str
    observations: tuple[CentroidObservation, ...]
    last_seen: float
    stationary_since: float | None
    resolved_track_class: str | None = None
    identity_epoch: int = 0

    def centroids(self) -> tuple[tuple[float, float], ...]:
        return tuple((o.x, o.y) for o in self.observations)


@dataclass(frozen=True)
class TrackHistoryView:
    """Bounded immutable projection of tracker-owned history for rules."""

    tracks: Mapping[int, TrackHistorySnapshot]
    now_sec: float
    expiry_sec: float = TRACK_EXPIRY_SEC

    def get(self, track_id: int) -> TrackHistorySnapshot | None:
        return self.tracks.get(int(track_id))

    def __contains__(self, track_id: object) -> bool:
        try:
            return int(track_id) in self.tracks  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return False

    def __iter__(self) -> Iterator[TrackHistorySnapshot]:
        return iter(self.tracks.values())

    def ids(self) -> tuple[int, ...]:
        return tuple(self.tracks.keys())


@dataclass
class TrackHistory:
    track_id: int
    class_label: str
    points: deque = field(default_factory=deque)  # (ts, cx, cy), bounded by time in add()
    stationary_since: float | None = None
    last_seen: float = 0.0
    # Latest per-frame label stays on class_label. resolved_track_class is the
    # separate, hysteresis-stabilized identity used by cargo applicability.
    resolved_track_class: str | None = None
    identity_epoch: int = 0
    support_label: str = ""
    support_since: float | None = None

    def add(self, timestamp_sec: float, cx: float, cy: float) -> None:
        point = (timestamp_sec, cx, cy)
        if self.points and timestamp_sec == self.points[-1][0]:
            # A repeated media timestamp must not grow history without bound.
            self.points[-1] = point
        else:
            self.points.append(point)
        cutoff = timestamp_sec - TRACK_HISTORY_RETENTION_SEC
        while self.points and self.points[0][0] < cutoff:
            self.points.popleft()
        self.last_seen = timestamp_sec

    def _window(self, window_sec: float = MOTION_WINDOW_SEC) -> list[tuple[float, float, float]]:
        if not self.points:
            return []
        cutoff = self.points[-1][0] - window_sec
        return [p for p in self.points if p[0] >= cutoff]

    def speed_px_per_sec(self) -> float | None:
        window = self._window()
        if len(window) < 2:
            return None
        t0, x0, y0 = window[0]
        t1, x1, y1 = window[-1]
        dt = t1 - t0
        if dt <= 0:
            return None
        return math.hypot(x1 - x0, y1 - y0) / dt

    def displacement_px(self) -> float:
        window = self._window()
        if len(window) < 2:
            return 0.0
        _, x0, y0 = window[0]
        _, x1, y1 = window[-1]
        return math.hypot(x1 - x0, y1 - y0)

    def direction_degrees(self, min_displacement_px: float = 40.0) -> float | None:
        """Travel heading (0deg = +x/right, 90deg = +y/down), or None if ~static."""
        window = self._window()
        if len(window) < 2:
            return None
        _, x0, y0 = window[0]
        _, x1, y1 = window[-1]
        dx, dy = x1 - x0, y1 - y0
        if math.hypot(dx, dy) < min_displacement_px:
            return None
        return math.degrees(math.atan2(dy, dx)) % 360.0

    def update_stationary(self, timestamp_sec: float, stationary_px: float) -> float:
        """Update stationary state; return continuous dwell duration in seconds."""
        speed = self.speed_px_per_sec()
        if speed is None:
            # Not enough history yet; treat as newly observed.
            return 0.0
        if speed <= stationary_px:
            if self.stationary_since is None:
                self.stationary_since = timestamp_sec
            return timestamp_sec - self.stationary_since
        self.stationary_since = None
        return 0.0

    def snapshot(self) -> TrackHistorySnapshot:
        observations = tuple(
            CentroidObservation(timestamp_sec=ts, x=cx, y=cy)
            for ts, cx, cy in self.points
        )
        return TrackHistorySnapshot(
            track_id=self.track_id,
            class_label=self.class_label,
            observations=observations,
            last_seen=self.last_seen,
            stationary_since=self.stationary_since,
            resolved_track_class=self.resolved_track_class,
            identity_epoch=self.identity_epoch,
        )


def _flicker_pair(left: str, right: str) -> bool:
    return (
        left != right
        and left in _VEHICLE_CLASS_FLICKER_GROUP
        and right in _VEHICLE_CLASS_FLICKER_GROUP
    )


def _motion_discontinuous(
    history: TrackHistory,
    ts: float,
    cx: float,
    cy: float,
    det: Mapping[str, Any],
) -> bool:
    """True when this observation cannot be the same physical vehicle."""
    if not history.points:
        return False
    last_ts, last_x, last_y = history.points[-1]
    dt = ts - float(last_ts)
    if dt < 0:
        return True
    dist = math.hypot(cx - float(last_x), cy - float(last_y))
    speed = history.speed_px_per_sec() or 0.0
    longer = max(float(det.get("bbox_w") or 0.0), float(det.get("bbox_h") or 0.0), 1.0)
    limit = speed * max(dt, 0.0) + IDENTITY_JUMP_BOX_LENGTHS * longer
    return dist > limit


class TrackState:
    """Registry of TrackHistory objects keyed by ByteTrack ID."""

    def __init__(self, stationary_px: float = 8.0) -> None:
        self.stationary_px = stationary_px
        self.tracks: dict[int, TrackHistory] = {}
        # Monotonic per ByteTrack ID so an expired ID that is reused does not
        # look like the previous vehicle's identity epoch.
        self._identity_epochs: dict[int, int] = {}

    def _open_identity(self, tid: int, label: str, ts: float) -> TrackHistory:
        epoch = self._identity_epochs.get(tid, 0) + 1
        self._identity_epochs[tid] = epoch
        history = TrackHistory(
            track_id=tid,
            class_label=label,
            resolved_track_class=(
                None if label in _DEFERRED_CLASS_RESOLUTION else (label or None)
            ),
            identity_epoch=epoch,
            support_label=label,
            support_since=ts,
        )
        self.tracks[tid] = history
        return history

    def _note_label(self, history: TrackHistory, label: str, ts: float) -> None:
        """Record the raw label and establish a resolved class when support is long enough."""
        if label != history.support_label:
            history.support_label = label
            history.support_since = ts
        history.class_label = label or history.class_label
        since = ts if history.support_since is None else history.support_since
        elapsed = ts - since
        if history.resolved_track_class is not None:
            return
        if label not in _DEFERRED_CLASS_RESOLUTION or elapsed >= MOTION_WINDOW_SEC:
            history.resolved_track_class = label or None

    def _challenger_established(self, history: TrackHistory, label: str, ts: float) -> bool:
        """True when a contradictory label has lasted MOTION_WINDOW_SEC.

        The caller then starts a new identity and drops the previous motion
        history. A shorter contradiction is only a flicker.
        """
        if history.resolved_track_class is None or label == history.resolved_track_class:
            return False
        if history.support_label != label or history.support_since is None:
            return False
        return (ts - history.support_since) >= MOTION_WINDOW_SEC

    def update(
        self,
        detections: list[dict[str, Any]],
        now: float | None = None,
    ) -> list[dict[str, Any]]:
        """
        Fold tracked detections into per-track history and annotate each
        detection with motion attributes used by the rule engine:
        centroid_x/centroid_y, speed_px_per_sec, direction_degrees, dwell_sec.

        ``now`` is the current frame timestamp and is used for expiry even
        when ``detections`` is empty (disappeared tracks).
        """
        clock = 0.0 if now is None else float(now)
        detection_times = [float(det.get("timestamp_sec", 0.0)) for det in detections]
        if detection_times:
            clock = max(clock, max(detection_times))
        # Expire stale identities before associating the incoming frame. If
        # pruning happens only after processing, a reused ByteTrack ID can
        # inherit old motion/class evidence on the first frame after a gap.
        self._prune(clock)
        annotated: list[dict[str, Any]] = []
        for det in detections:
            tid = int(det["track_id"])
            ts = float(det.get("timestamp_sec", 0.0))
            clock = max(clock, ts)
            cx = float(det["bbox_x"]) + float(det["bbox_w"]) / 2
            cy = float(det["bbox_y"]) + float(det["bbox_h"]) / 2

            history = self.tracks.get(tid)
            label = str(det.get("class_label", ""))
            if history is None or _motion_discontinuous(history, ts, cx, cy, det):
                history = self._open_identity(tid, label, ts)
            elif (
                label
                and history.class_label
                and label != history.class_label
                and not _flicker_pair(label, history.class_label)
            ):
                # A non-vehicle class change is an identity change. Brief
                # vehicle-label flicker is handled by class-support hysteresis.
                history = self._open_identity(tid, label, ts)
            elif label and self._challenger_established(history, label, ts):
                history = self._open_identity(tid, label, ts)
            elif label:
                self._note_label(history, label, ts)
            history.add(ts, cx, cy)
            dwell = history.update_stationary(ts, self.stationary_px)

            row = dict(det)
            row["centroid_x"] = cx
            row["centroid_y"] = cy
            row["speed_px_per_sec"] = history.speed_px_per_sec()
            row["direction_degrees"] = history.direction_degrees()
            row["dwell_sec"] = dwell
            # Per-frame class_label / raw_class stay as the detector wrote them.
            row["resolved_track_class"] = history.resolved_track_class
            row["track_identity_epoch"] = history.identity_epoch
            annotated.append(row)

        self._prune(clock)
        return annotated

    def history_view(self, now: float | None = None) -> TrackHistoryView:
        """Immutable snapshot for rule evaluation (cannot mutate tracker state)."""
        clock = float(now) if now is not None else 0.0
        if clock <= 0 and self.tracks:
            clock = max(h.last_seen for h in self.tracks.values())
        snapshots = {
            tid: history.snapshot()
            for tid, history in self.tracks.items()
            if clock - history.last_seen <= TRACK_EXPIRY_SEC
        }
        return TrackHistoryView(
            tracks=snapshots, now_sec=clock, expiry_sec=TRACK_EXPIRY_SEC
        )

    def _prune(self, now: float) -> None:
        stale = [tid for tid, h in self.tracks.items() if now - h.last_seen > TRACK_EXPIRY_SEC]
        for tid in stale:
            del self.tracks[tid]

    def count_by_class(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for history in self.tracks.values():
            counts[history.class_label] = counts.get(history.class_label, 0) + 1
        return counts


def point_in_polygon(x: float, y: float, polygon: list[list[float]]) -> bool:
    """Ray-casting point-in-polygon test (polygon points in frame pixels)."""
    if len(polygon) < 3:
        return False
    inside = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i][0], polygon[i][1]
        xj, yj = polygon[j][0], polygon[j][1]
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def angle_difference(a: float, b: float) -> float:
    """Smallest absolute difference between two headings, in degrees [0, 180]."""
    diff = abs(a - b) % 360.0
    return min(diff, 360.0 - diff)
