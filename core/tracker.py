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
from typing import Any

# Seconds without an update before a track is discarded.
TRACK_EXPIRY_SEC = 5.0
# Sliding window (seconds) used for speed/direction estimation.
MOTION_WINDOW_SEC = 2.0


@dataclass
class TrackHistory:
    track_id: int
    class_label: str
    points: deque = field(default_factory=lambda: deque(maxlen=300))  # (ts, cx, cy)
    stationary_since: float | None = None
    last_seen: float = 0.0

    def add(self, timestamp_sec: float, cx: float, cy: float) -> None:
        self.points.append((timestamp_sec, cx, cy))
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


class TrackState:
    """Registry of TrackHistory objects keyed by ByteTrack ID."""

    def __init__(self, stationary_px: float = 8.0) -> None:
        self.stationary_px = stationary_px
        self.tracks: dict[int, TrackHistory] = {}

    def update(self, detections: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Fold tracked detections into per-track history and annotate each
        detection with motion attributes used by the rule engine:
        centroid_x/centroid_y, speed_px_per_sec, direction_degrees, dwell_sec.
        """
        now = 0.0
        annotated: list[dict[str, Any]] = []
        for det in detections:
            tid = int(det["track_id"])
            ts = float(det.get("timestamp_sec", 0.0))
            now = max(now, ts)
            cx = float(det["bbox_x"]) + float(det["bbox_w"]) / 2
            cy = float(det["bbox_y"]) + float(det["bbox_h"]) / 2

            history = self.tracks.get(tid)
            if history is None:
                history = TrackHistory(track_id=tid, class_label=str(det.get("class_label", "")))
                self.tracks[tid] = history
            history.add(ts, cx, cy)
            dwell = history.update_stationary(ts, self.stationary_px)

            row = dict(det)
            row["centroid_x"] = cx
            row["centroid_y"] = cy
            row["speed_px_per_sec"] = history.speed_px_per_sec()
            row["direction_degrees"] = history.direction_degrees()
            row["dwell_sec"] = dwell
            annotated.append(row)

        self._prune(now)
        return annotated

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
