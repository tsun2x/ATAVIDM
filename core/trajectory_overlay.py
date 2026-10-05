"""Bounded, review-only vehicle trace rendering using Roboflow Supervision."""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

import numpy as np


class TrajectoryOverlay:
    """Draw recent bottom-center paths for ByteTrack observations.

    The overlay is visual evidence only. It does not alter detections, tracker
    state, lane membership, or violation decisions.
    """

    def __init__(self, *, trace_length: int = 30, thickness: int = 2) -> None:
        import supervision as sv

        self._sv = sv
        self._annotator = sv.TraceAnnotator(
            position=sv.Position.BOTTOM_CENTER,
            trace_length=max(int(trace_length), 1),
            thickness=max(int(thickness), 1),
            color_lookup=sv.ColorLookup.TRACK,
        )
        self._visual_ids: dict[tuple[int, int], int] = {}
        self._next_visual_id = 1

    def annotate(
        self,
        frame: Any,
        detections: Sequence[Mapping[str, Any]],
    ) -> Any:
        """Draw traces for valid tracked boxes onto ``frame`` and return it."""
        boxes: list[list[float]] = []
        track_ids: list[int] = []
        for detection in detections:
            try:
                track_id = int(detection.get("track_id"))
                identity_epoch = int(detection.get("track_identity_epoch") or 0)
                x = float(detection.get("bbox_x"))
                y = float(detection.get("bbox_y"))
                width = float(detection.get("bbox_w"))
                height = float(detection.get("bbox_h"))
            except (TypeError, ValueError, OverflowError):
                continue
            if (
                track_id < 0
                or not all(math.isfinite(value) for value in (x, y, width, height))
                or width <= 0
                or height <= 0
            ):
                continue
            identity = (track_id, identity_epoch)
            visual_id = self._visual_ids.get(identity)
            if visual_id is None:
                visual_id = self._next_visual_id
                self._next_visual_id += 1
                self._visual_ids[identity] = visual_id
            boxes.append([x, y, x + width, y + height])
            track_ids.append(visual_id)

        if not track_ids:
            return frame

        tracked = self._sv.Detections(
            xyxy=np.asarray(boxes, dtype=np.float32),
            tracker_id=np.asarray(track_ids, dtype=np.int64),
        )
        return self._annotator.annotate(scene=frame, detections=tracked)
