"""Conservative, run-local line-crossing vehicle passage counter."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable


class VehicleCrossingCounter:
    def __init__(self, lines: Iterable[Any], *, hysteresis_px: float = 3.0):
        self._lines = [line for line in lines if len(getattr(line, "points", ())) >= 2]
        self._hysteresis = max(0.0, float(hysteresis_px))
        self._last_side: dict[tuple[str, int], int] = {}
        self._counted: set[tuple[str, int]] = set()
        self.available = bool(self._lines)
        self.total = 0 if self.available else None
        self.by_class: dict[str, int] = {}
        self.by_line: dict[str, int] = defaultdict(int)

    @staticmethod
    def _signed_distance(line: Any, point: tuple[float, float]) -> float:
        (x1, y1), (x2, y2) = line.points[:2]
        dx, dy = x2 - x1, y2 - y1
        length = (dx * dx + dy * dy) ** 0.5
        if length <= 1e-9:
            return 0.0
        return (dx * (point[1] - y1) - dy * (point[0] - x1)) / length

    @staticmethod
    def _within_segment(line: Any, point: tuple[float, float]) -> bool:
        (x1, y1), (x2, y2) = line.points[:2]
        dx, dy = x2 - x1, y2 - y1
        denom = dx * dx + dy * dy
        if denom <= 1e-9:
            return False
        projection = ((point[0] - x1) * dx + (point[1] - y1) * dy) / denom
        return 0.0 <= projection <= 1.0

    def update(self, detections: Iterable[dict[str, Any]]) -> None:
        if not self.available:
            return
        for det in detections:
            if det.get("track_id") is None:
                continue
            tid = int(det["track_id"])
            point = (
                float(det.get("bbox_x", 0.0)) + float(det.get("bbox_w", 0.0)) / 2.0,
                float(det.get("bbox_y", 0.0)) + float(det.get("bbox_h", 0.0)),
            )
            for line in self._lines:
                if not self._within_segment(line, point):
                    continue
                distance = self._signed_distance(line, point)
                side = 1 if distance > self._hysteresis else -1 if distance < -self._hysteresis else 0
                if side == 0:
                    continue
                key = (str(line.id), tid)
                previous = self._last_side.get(key)
                self._last_side[key] = side
                if previous is None or previous == side or key in self._counted:
                    continue
                direction = str(getattr(line, "metadata", {}).get("count_direction", "both"))
                transition = "negative_to_positive" if previous < side else "positive_to_negative"
                if direction not in ("both", transition):
                    continue
                self._counted.add(key)
                self.total = int(self.total or 0) + 1
                label = str(det.get("class_label") or "unknown")
                self.by_class[label] = self.by_class.get(label, 0) + 1
                self.by_line[str(line.id)] += 1
