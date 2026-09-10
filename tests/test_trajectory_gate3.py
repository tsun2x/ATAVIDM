"""Gate 3 — TrackHistoryView and shared trajectory primitives."""

from __future__ import annotations

import pytest

from core.tracker import TRACK_EXPIRY_SEC, TrackHistoryView, TrackState
from core.trajectory import (
    ZoneMembershipHysteresis,
    crossed_oriented_line,
    displacement,
    endpoint_jitter,
    heading_degrees,
    is_parallel_motion,
    motion_projects_into_direction,
    outside_to_inside_transition,
    segments_intersect,
    signed_side,
)


class TestTrackHistoryView:
    def test_immutable_projection_and_expiry(self):
        state = TrackState(stationary_px=8.0)
        state.update(
            [
                {
                    "track_id": 1,
                    "class_label": "car",
                    "bbox_x": 10,
                    "bbox_y": 10,
                    "bbox_w": 20,
                    "bbox_h": 20,
                    "timestamp_sec": 1.0,
                    "confidence": 0.9,
                }
            ],
            now=1.0,
        )
        view = state.history_view(now=1.0)
        assert isinstance(view, TrackHistoryView)
        snap = view.get(1)
        assert snap is not None
        assert len(snap.observations) == 1
        # Mutating the view mapping is not supported via frozen snapshots.
        with pytest.raises(Exception):
            snap.observations[0].x = 99  # type: ignore[misc]

        # After expiry prune, history is cleared.
        state.update([], now=1.0 + TRACK_EXPIRY_SEC + 0.1)
        assert state.history_view(now=1.0 + TRACK_EXPIRY_SEC + 0.1).get(1) is None

    def test_class_change_clears_prior_points(self):
        state = TrackState()
        state.update(
            [
                {
                    "track_id": 7,
                    "class_label": "car",
                    "bbox_x": 0,
                    "bbox_y": 0,
                    "bbox_w": 10,
                    "bbox_h": 10,
                    "timestamp_sec": 0.0,
                    "confidence": 0.9,
                }
            ],
            now=0.0,
        )
        state.update(
            [
                {
                    "track_id": 7,
                    "class_label": "truck",
                    "bbox_x": 50,
                    "bbox_y": 50,
                    "bbox_w": 10,
                    "bbox_h": 10,
                    "timestamp_sec": 0.5,
                    "confidence": 0.9,
                }
            ],
            now=0.5,
        )
        snap = state.history_view(now=0.5).get(7)
        assert snap is not None
        assert snap.class_label == "truck"
        assert len(snap.observations) == 1


class TestTrajectoryPrimitives:
    def test_insufficient_motion_rejected(self):
        result = displacement([(0, 0), (1, 0)], min_distance=40)
        assert result.sufficient is False
        assert heading_degrees([(0, 0), (1, 0)], min_distance=40) is None

    def test_stable_displacement_and_heading(self):
        result = displacement([(0, 0), (50, 0)], min_distance=40)
        assert result.sufficient is True
        assert result.degrees == pytest.approx(0.0)

    def test_finite_segment_intersection(self):
        assert segments_intersect(((0, 0), (10, 10)), ((0, 10), (10, 0)))
        assert not segments_intersect(((0, 0), (10, 0)), ((0, 5), (10, 5)))

    def test_signed_side_and_prohibited_from(self):
        a, b = (0.0, 0.0), (10.0, 0.0)
        assert signed_side((5.0, -5.0), a, b) < 0  # right of A→B (image y-down: actually left in math?)
        # Image coords: +y down. A→B along +x; point below line has +y → positive cross = left of direction in image?
        # (bx-ax)*(py-ay)-(by-ay)*(px-ax) = 10*(+5)-0 = +50 → left of A→B.
        assert signed_side((5.0, 5.0), a, b) > 0
        assert crossed_oriented_line(
            (5.0, 5.0), (5.0, -5.0), a, b, prohibited_from="left"
        )
        assert not crossed_oriented_line(
            (5.0, -5.0), (5.0, 5.0), a, b, prohibited_from="left"
        )
        assert crossed_oriented_line(
            (5.0, -5.0), (5.0, 5.0), a, b, prohibited_from="right"
        )
        assert crossed_oriented_line(
            (5.0, 5.0), (5.0, -5.0), a, b, prohibited_from="both"
        )

    def test_prohibited_direction_projection(self):
        assert motion_projects_into_direction((10, 0), (1, 0), min_projection=5)
        assert not motion_projects_into_direction((-10, 0), (1, 0), min_projection=5)
        assert not motion_projects_into_direction((0, 10), (1, 0), min_projection=5)

    def test_parallel_and_jitter_rejection(self):
        assert is_parallel_motion((10, 0), (0, 0), (10, 0))
        assert not is_parallel_motion((0, 10), (0, 0), (10, 0))
        assert endpoint_jitter([(0, 0), (1, 0)], max_jitter=5)
        assert not endpoint_jitter([(0, 0), (20, 0)], max_jitter=5)

    def test_outside_to_inside_hysteresis(self):
        assert outside_to_inside_transition(False, True)
        assert not outside_to_inside_transition(True, True)
        hyst = ZoneMembershipHysteresis(enter_frames=2, exit_frames=2)
        assert hyst.update(True) == "outside"
        assert hyst.update(True) == "entered"
        assert hyst.update(True) == "inside"
        assert hyst.update(False) == "inside"
        assert hyst.update(False) == "exited"
