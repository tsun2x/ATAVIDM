"""ByteTrack integration — Phase 3."""

from __future__ import annotations

from typing import Any


class TrackState:
    """Maintains stable track IDs across frames for rule-engine persistence."""

    def __init__(self) -> None:
        self._next_id = 1
        self.tracks: dict[int, dict[str, Any]] = {}

    def update(self, detections: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Assign or update track IDs on detections.

        TODO: Replace with ByteTrack when the detection pipeline is active.
        Currently assigns ephemeral IDs per detection for API shape compatibility.
        """
        tracked: list[dict[str, Any]] = []
        for det in detections:
            row = dict(det)
            row.setdefault("track_id", self._next_id)
            self.tracks[row["track_id"]] = row
            self._next_id += 1
            tracked.append(row)
        return tracked
