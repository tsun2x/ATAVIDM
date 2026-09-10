"""Gates 5–8 — Counterflow, rider-only, contextual, and cargo behavioral tests."""

from __future__ import annotations

import pytest

from core.detection_config import (
    VIOLATION_CARGO_PASSENGERS,
    VIOLATION_COUNTERFLOW,
    VIOLATION_MOTORCYCLE_OVERLOADING,
    VIOLATION_NO_HELMET,
    VIOLATION_NO_SIDE_MIRROR,
    VIOLATION_SUBSTANDARD_HELMET,
)
from core.scene_annotation import load_scene_annotation
from core.tracker import TrackState
from core.violation_engine import (
    RuleEngineState,
    check_cargo_passenger,
    check_counterflow,
    check_motorcycle_overloading,
    check_no_helmet,
    check_no_side_mirror,
    check_substandard_helmet,
    evaluate_detection_rules,
)


def _det(**kwargs):
    base = {
        "confidence": 0.9,
        "bbox_x": 100.0,
        "bbox_y": 100.0,
        "bbox_w": 40.0,
        "bbox_h": 40.0,
        "timestamp_sec": 1.0,
        "centroid_x": 120.0,
        "centroid_y": 120.0,
        "speed_px_per_sec": 20.0,
        "direction_degrees": 0.0,
        "dwell_sec": 0.0,
    }
    base.update(kwargs)
    return base


class TestPerLaneCounterflowGate5:
    def _scene(self):
        return load_scene_annotation(
            {
                "schema_version": 2,
                "zones": [],
                "lanes": [
                    {
                        "id": "lane-right",
                        "type": "active_lane",
                        "points": [[0, 0], [200, 0], [200, 100], [0, 100]],
                    },
                    {
                        "id": "lane-left",
                        "type": "active_lane",
                        "points": [[0, 120], [200, 120], [200, 220], [0, 220]],
                    },
                ],
                "flow_arrows": [
                    {
                        "id": "a-r",
                        "type": "lane_flow",
                        "points": [[20, 50], [180, 50]],
                        "lane_ids": ["lane-right"],
                    },
                    {
                        "id": "a-l",
                        "type": "lane_flow",
                        "points": [[180, 170], [20, 170]],
                        "lane_ids": ["lane-left"],
                    },
                ],
                "threshold_lines": [],
                "markings": [],
                "signs": [],
                "activity_regions": [],
            }
        ).to_rule_context()

    def test_opposing_lane_triggers(self):
        scene = self._scene()
        state = RuleEngineState()
        params = {
            "_rule_scene": scene,
            "flow_tolerance_degrees": 60,
            "counterflow_persist_sec": 0.5,
        }
        events = []
        for t in (0.0, 0.6, 1.2):
            veh = _det(
                track_id=1,
                class_label="car",
                bbox_x=80,
                bbox_y=30,
                centroid_x=100,
                centroid_y=50,
                direction_degrees=180.0,  # against rightward flow
                timestamp_sec=t,
            )
            events.extend(
                check_counterflow([veh], [], state, 1, params)
            )
        assert any(e.violation_type == VIOLATION_COUNTERFLOW for e in events)

    def test_same_direction_does_not_trigger(self):
        scene = self._scene()
        state = RuleEngineState()
        params = {
            "_rule_scene": scene,
            "flow_tolerance_degrees": 60,
            "counterflow_persist_sec": 0.5,
        }
        events = []
        for t in (0.0, 0.6, 1.2):
            veh = _det(
                track_id=2,
                class_label="car",
                bbox_x=80,
                bbox_y=30,
                centroid_x=100,
                centroid_y=50,
                direction_degrees=0.0,
                timestamp_sec=t,
            )
            events.extend(check_counterflow([veh], [], state, 1, params))
        assert events == []

    def test_missing_arrow_unknown(self):
        scene = load_scene_annotation(
            {
                "schema_version": 2,
                "zones": [],
                "lanes": [
                    {
                        "id": "lane-x",
                        "type": "active_lane",
                        "points": [[0, 0], [100, 0], [100, 100], [0, 100]],
                    }
                ],
                "flow_arrows": [],
                "threshold_lines": [],
                "markings": [],
                "signs": [],
                "activity_regions": [],
            }
        ).to_rule_context()
        state = RuleEngineState()
        veh = _det(
            track_id=3,
            class_label="car",
            centroid_x=50,
            centroid_y=50,
            direction_degrees=180.0,
        )
        assert (
            check_counterflow(
                [veh], [], state, 1, {"_rule_scene": scene, "counterflow_persist_sec": 0.1}
            )
            == []
        )

    def test_legacy_fallback_single_active_lane(self):
        state = RuleEngineState()
        poly = [[0, 0], [200, 0], [200, 100], [0, 100]]
        params = {
            "lane_flow_degrees": 0.0,
            "flow_tolerance_degrees": 60,
            "counterflow_persist_sec": 0.5,
        }
        events = []
        for t in (0.0, 0.6, 1.2):
            veh = _det(
                track_id=4,
                class_label="car",
                bbox_x=50,
                bbox_y=20,
                bbox_w=40,
                bbox_h=40,
                direction_degrees=180.0,
                timestamp_sec=t,
            )
            events.extend(check_counterflow([veh], poly, state, 1, params))
        assert any(e.violation_type == VIOLATION_COUNTERFLOW for e in events)


class TestRiderOnlyGate6:
    def test_person_cannot_trigger_no_helmet(self):
        state = RuleEngineState()
        classes = ("motorcycle", "rider", "helmet_acceptable", "helmet_nut_shell")
        mc = _det(track_id=1, class_label="motorcycle", bbox_x=100, bbox_y=100, bbox_w=60, bbox_h=60)
        person = _det(
            track_id=2,
            class_label="person",
            bbox_x=110,
            bbox_y=90,
            bbox_w=30,
            bbox_h=50,
            confidence=0.95,
        )
        events = []
        for t in (0.0, 1.0, 2.0):
            mc["timestamp_sec"] = t
            person["timestamp_sec"] = t
            events.extend(
                check_no_helmet([mc, person], state, 1, {}, model_classes=classes)
            )
        assert events == []

    def test_rider_triggers_no_helmet_without_helmet(self):
        state = RuleEngineState()
        classes = ("motorcycle", "rider", "helmet_acceptable", "helmet_nut_shell")
        mc = _det(track_id=1, class_label="motorcycle", bbox_x=100, bbox_y=100, bbox_w=60, bbox_h=60)
        rider = _det(
            track_id=2,
            class_label="rider",
            bbox_x=110,
            bbox_y=90,
            bbox_w=40,
            bbox_h=50,
            confidence=0.95,
        )
        events = []
        for t in (0.0, 1.0, 2.0):
            mc["timestamp_sec"] = t
            rider["timestamp_sec"] = t
            events.extend(
                check_no_helmet([mc, rider], state, 1, {}, model_classes=classes)
            )
        assert any(e.violation_type == VIOLATION_NO_HELMET for e in events)

    def test_person_cannot_trigger_overloading_or_substandard(self):
        state = RuleEngineState()
        classes = ("motorcycle", "rider", "helmet_acceptable", "helmet_nut_shell")
        mc = _det(track_id=1, class_label="motorcycle", bbox_x=100, bbox_y=100, bbox_w=80, bbox_h=60)
        persons = [
            _det(track_id=i, class_label="person", bbox_x=105 + i * 5, bbox_y=95, bbox_w=25, bbox_h=40)
            for i in (2, 3, 4)
        ]
        assert (
            check_motorcycle_overloading(
                [mc, *persons], state, 1, model_classes=classes
            )
            == []
        )
        assert (
            check_substandard_helmet(
                [mc, *persons], state, 1, model_classes=classes
            )
            == []
        )

    def test_missing_rider_capability_fail_closed(self):
        state = RuleEngineState()
        classes = ("motorcycle", "person", "helmet")
        mc = _det(track_id=1, class_label="motorcycle")
        person = _det(track_id=2, class_label="person", bbox_x=110, bbox_y=100)
        assert check_no_helmet([mc, person], state, 1, {}, model_classes=classes) == []
        assert any("rider" in d.lower() for d in state.diagnostics)


class TestMirrorGate7:
    def test_two_mirrors_no_candidate(self):
        state = RuleEngineState()
        classes = ("side_mirror", "car")
        veh = _det(
            track_id=1,
            class_label="car",
            bbox_x=100,
            bbox_y=100,
            bbox_w=100,
            bbox_h=60,
            mirror_roi_visibility="both_visible",
        )
        m1 = _det(track_id=2, class_label="side_mirror", bbox_x=105, bbox_y=105, bbox_w=10, bbox_h=10)
        m2 = _det(track_id=3, class_label="side_mirror", bbox_x=180, bbox_y=105, bbox_w=10, bbox_h=10)
        events = []
        for t in (0.0, 1.0, 2.0, 3.0):
            veh["timestamp_sec"] = t
            events.extend(
                check_no_side_mirror([veh, m1, m2], state, 1, model_classes=classes)
            )
        assert events == []

    def test_zero_mirrors_with_visibility_emits_review(self):
        state = RuleEngineState()
        classes = ("side_mirror", "car")
        veh = _det(
            track_id=1,
            class_label="car",
            bbox_x=100,
            bbox_y=100,
            bbox_w=100,
            bbox_h=60,
            mirror_roi_visibility="both_visible",
        )
        events = []
        for t in (0.0, 1.0, 2.0, 3.0, 4.0):
            veh["timestamp_sec"] = t
            events.extend(check_no_side_mirror([veh], state, 1, model_classes=classes))
        assert any(
            e.violation_type == VIOLATION_NO_SIDE_MIRROR and e.outcome == "review"
            for e in events
        )


class TestCargoGate8:
    def test_live_suppressed(self):
        state = RuleEngineState()
        classes = ("person", "truck", "pickup_truck")
        events = check_cargo_passenger(
            [],
            state,
            1,
            model_classes=classes,
            params={"_live_mode": True},
        )
        assert events == []
        assert any("live" in d.lower() for d in state.diagnostics)

    def test_one_frame_overlap_insufficient(self):
        state = RuleEngineState()
        classes = ("person", "truck")
        track = TrackState()
        truck = _det(
            track_id=1,
            class_label="truck",
            bbox_x=100,
            bbox_y=100,
            bbox_w=120,
            bbox_h=60,
            direction_degrees=0.0,
            speed_px_per_sec=30.0,
            timestamp_sec=0.0,
        )
        person = _det(
            track_id=2,
            class_label="person",
            bbox_x=180,
            bbox_y=120,
            bbox_w=20,
            bbox_h=30,
            speed_px_per_sec=30.0,
            timestamp_sec=0.0,
        )
        track.update([truck, person], now=0.0)
        events = check_cargo_passenger(
            [truck, person],
            state,
            1,
            model_classes=classes,
            params={"_track_history": track.history_view(0.0)},
        )
        assert events == []

    def test_stable_shared_motion_emits_review(self):
        state = RuleEngineState()
        classes = ("person", "truck")
        track = TrackState()
        events = []
        for i, t in enumerate([0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0]):
            x = 100 + i * 30
            truck = _det(
                track_id=1,
                class_label="truck",
                bbox_x=x,
                bbox_y=100,
                bbox_w=120,
                bbox_h=60,
                direction_degrees=0.0,
                speed_px_per_sec=40.0,
                timestamp_sec=t,
                centroid_x=x + 60,
                centroid_y=130,
            )
            # Person in trailing (left) cargo region while moving right.
            person = _det(
                track_id=2,
                class_label="person",
                bbox_x=x + 5,
                bbox_y=120,
                bbox_w=25,
                bbox_h=35,
                speed_px_per_sec=40.0,
                timestamp_sec=t,
                centroid_x=x + 17,
                centroid_y=137,
            )
            tracked = track.update([truck, person], now=t)
            events.extend(
                check_cargo_passenger(
                    tracked,
                    state,
                    i,
                    model_classes=classes,
                    params={"_track_history": track.history_view(t)},
                )
            )
        assert any(
            e.violation_type == VIOLATION_CARGO_PASSENGERS and e.outcome == "review"
            for e in events
        )

    def test_unknown_direction_fail_closed(self):
        state = RuleEngineState()
        classes = ("person", "truck")
        truck = _det(
            track_id=1,
            class_label="truck",
            direction_degrees=None,
            speed_px_per_sec=0.0,
        )
        person = _det(track_id=2, class_label="person", bbox_x=160, bbox_y=120)
        assert (
            check_cargo_passenger(
                [truck, person], state, 1, model_classes=classes, params={}
            )
            == []
        )
