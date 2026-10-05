"""Fail-closed helmet, Illegal Terminal capability, and pickup-track evidence.

These are synthetic unit tests. They do not measure real-world detector
accuracy. Class flicker, cargo occupancy, and missed motorcycle helmets still
need reviewed footage. The held-out WMSU set stays unused for tuning.
"""

from __future__ import annotations

from core.cargo_passenger import cargo_applicability_label
from core.detection_config import (
    VIOLATION_CARGO_PASSENGERS,
    VIOLATION_ILLEGAL_PARKING,
    VIOLATION_ILLEGAL_TERMINAL,
    VIOLATION_NO_HELMET,
)
from core.model_capability import (
    NO_HELMET_BLOCKED_PREREQUISITE,
    POSITIVE_UNCOVERED_HEAD_OBSERVATION_APPROVED,
    assess_rule_capability,
)
from core.scene_annotation import load_scene_annotation
from core.tracker import MOTION_WINDOW_SEC, TrackState
from core.violation_engine import (
    RuleEngineState,
    _rider_helmet_state,
    attributed_motorcycle_riders,
    build_processing_diagnostics,
    check_cargo_passenger,
    check_no_helmet,
    evaluate_detection_rules,
    scene_capability_flags,
)

HELMET_CLASSES = ("motorcycle", "rider", "helmet_acceptable", "helmet_nut_shell")
LOADING_ZONE = [[0, 0], [400, 0], [400, 300], [0, 300]]
PARKING_ZONE = [[0, 0], [80, 0], [80, 80], [0, 80]]


def _rider(**overrides):
    row = {
        "class_label": "rider",
        "track_id": 2,
        "bbox_x": 110.0,
        "bbox_y": 90.0,
        "bbox_w": 40.0,
        "bbox_h": 50.0,
        "confidence": 0.95,
        "timestamp_sec": 0.0,
    }
    row.update(overrides)
    return row


def _motorcycle(**overrides):
    row = {
        "class_label": "motorcycle",
        "track_id": 1,
        "bbox_x": 100.0,
        "bbox_y": 100.0,
        "bbox_w": 80.0,
        "bbox_h": 50.0,
        "confidence": 0.95,
        "timestamp_sec": 0.0,
    }
    row.update(overrides)
    return row


def _vehicle(track_id, label, t, x, *, y=100.0, w=120.0, h=60.0):
    return {
        "track_id": track_id,
        "class_label": label,
        "raw_class": label,
        "bbox_x": float(x),
        "bbox_y": y,
        "bbox_w": w,
        "bbox_h": h,
        "confidence": 0.92,
        "timestamp_sec": t,
    }


def _cargo_person(t, x):
    return _vehicle(2, "person", t, x + 5.0, y=120.0, w=25.0, h=35.0)


def _activity_scene():
    return load_scene_annotation(
        {
            "schema_version": 2,
            "activity_regions": [
                {
                    "id": "act-1",
                    "type": "passenger_activity",
                    "points": [[300, 10], [380, 10], [380, 80], [300, 80]],
                }
            ],
        }
    ).to_rule_context()


def _run_helmet(tracked_frames):
    state = RuleEngineState()
    events = []
    for index, row in enumerate(tracked_frames):
        events.extend(
            check_no_helmet(
                row,
                state,
                index,
                {},
                model_classes=HELMET_CLASSES,
            )
        )
        events.extend(
            evaluate_detection_rules(
                row,
                state,
                frame_number=index,
                enabled_violations=(VIOLATION_NO_HELMET,),
                model_classes=HELMET_CLASSES,
            )
        )
    return state, events


class TestMissingHelmetIsUnknown:
    def test_positive_uncovered_head_source_is_not_approved(self):
        assert POSITIVE_UNCOVERED_HEAD_OBSERVATION_APPROVED is False
        cap = assess_rule_capability(VIOLATION_NO_HELMET, HELMET_CLASSES)
        assert cap.automatic_evaluation is False
        assert NO_HELMET_BLOCKED_PREREQUISITE in cap.missing_prerequisites
        assert "owner/adviser" in cap.notes

    def test_large_clear_rider_without_helmet_box_is_unknown(self):
        rider = _rider()
        assert _rider_helmet_state(rider, [], []) == "UNKNOWN"
        state, events = _run_helmet([[_motorcycle(), rider]])
        assert events == []
        assert any("blocked" in note.lower() for note in state.diagnostics)
        assert all(getattr(event, "outcome", "") != "confirmed" for event in events)

    def test_tiny_low_confidence_and_uncertain_head_stay_unknown(self):
        tiny = _rider(bbox_w=10, bbox_h=10, confidence=0.99)
        dim = _rider(confidence=0.2, bbox_w=40, bbox_h=50)
        hidden = _rider(head_visible=False)
        assert _rider_helmet_state(tiny, [], []) == "UNKNOWN"
        assert _rider_helmet_state(dim, [], []) == "UNKNOWN"
        assert _rider_helmet_state(hidden, [], []) == "UNKNOWN"
        _, events = _run_helmet(
            [[_motorcycle(), tiny], [_motorcycle(), dim], [_motorcycle(), hidden]]
        )
        assert events == []

    def test_clipped_occluded_and_edge_riders_stay_unknown(self):
        cases = [
            _rider(clipped=True),
            _rider(occluded=True),
            _rider(blurred=True),
            _rider(frame_w=200, frame_h=200, bbox_x=0, bbox_y=90),
        ]
        for rider in cases:
            assert _rider_helmet_state(rider, [], []) == "UNKNOWN"
        _, events = _run_helmet([[_motorcycle(), rider] for rider in cases])
        assert events == []

    def test_person_is_not_a_rider(self):
        person = _rider(class_label="person", track_id=3)
        attributed = attributed_motorcycle_riders([_motorcycle()], [person])
        assert attributed == {}
        _, events = _run_helmet([[_motorcycle(), person]])
        assert events == []

    def test_competing_motorcycles_are_not_attributable(self):
        left = _motorcycle(track_id=1, bbox_x=100)
        right = _motorcycle(track_id=4, bbox_x=130)
        rider = _rider(bbox_x=140, bbox_y=100, bbox_w=40, bbox_h=40)
        assert attributed_motorcycle_riders([left, right], [rider]) == {}
        _, events = _run_helmet([[left, right, rider]])
        assert events == []

    def test_detail_no_helmet_box_creates_no_violation(self):
        detail = {
            "class_label": "no_helmet",
            "track_id": 9,
            "bbox_x": 112.0,
            "bbox_y": 88.0,
            "bbox_w": 18.0,
            "bbox_h": 16.0,
            "confidence": 0.99,
            "timestamp_sec": 0.0,
        }
        state, events = _run_helmet([[_motorcycle(), _rider(), detail]])
        assert events == []
        assert all(getattr(event, "outcome", "") != "confirmed" for event in events)
        assert state.capability[VIOLATION_NO_HELMET].automatic_evaluation is False


class TestIllegalTerminalCapability:
    def test_activity_region_alone_is_not_runnable(self):
        scene = _activity_scene()
        assert scene.activity_regions
        flags = scene_capability_flags(dict(scene.legacy_zones), scene, {})
        assert flags["zone:loading_unloading"] is False
        cap = assess_rule_capability(
            VIOLATION_ILLEGAL_TERMINAL, ("jeepney", "car"), context_flags=flags
        )
        assert cap.automatic_evaluation is False
        assert "context:zone:loading_unloading" in cap.missing_prerequisites

        parking = assess_rule_capability(
            VIOLATION_ILLEGAL_PARKING, ("car",), context_flags=flags
        )
        assert parking.automatic_evaluation is False

        state = RuleEngineState()
        jeepney = _vehicle(2, "jeepney", 0.0, 120, w=40, h=20)
        jeepney["speed_px_per_sec"] = 0.0
        jeepney["terminal_passenger_activity"] = True
        events = []
        for frame, t in enumerate((0.0, 0.6, 1.2, 1.8)):
            row = dict(jeepney, timestamp_sec=t)
            events.extend(
                evaluate_detection_rules(
                    [row],
                    state,
                    frame_number=frame,
                    params={"loading_dwell_sec": 0.5, "stationary_px": 8.0},
                    enabled_violations=(
                        VIOLATION_ILLEGAL_TERMINAL,
                        VIOLATION_ILLEGAL_PARKING,
                    ),
                    model_classes=("jeepney", "car"),
                    scene=scene,
                )
            )
        assert events == []
        assert state.capability[VIOLATION_ILLEGAL_TERMINAL].automatic_evaluation is False
        assert state.capability[VIOLATION_ILLEGAL_PARKING].automatic_evaluation is False

    def test_loading_zone_matches_the_evaluator(self):
        scene = _activity_scene()
        flags = scene_capability_flags(
            {"loading_unloading": LOADING_ZONE, "no_parking": PARKING_ZONE},
            scene,
            {},
        )
        assert flags["zone:loading_unloading"] is True
        assert flags["zone:no_parking"] is True
        terminal = assess_rule_capability(
            VIOLATION_ILLEGAL_TERMINAL, ("jeepney",), context_flags=flags
        )
        parking = assess_rule_capability(
            VIOLATION_ILLEGAL_PARKING, ("car",), context_flags=flags
        )
        assert terminal.automatic_evaluation is True
        assert parking.automatic_evaluation is True

        reported = build_processing_diagnostics(
            model_classes=("jeepney", "car"),
            enabled_violations=(VIOLATION_ILLEGAL_TERMINAL, VIOLATION_ILLEGAL_PARKING),
            geometry=None,
            context_flags=flags,
        )
        by_name = {item.rule_name: item for item in reported.rule_capabilities}
        assert by_name[VIOLATION_ILLEGAL_TERMINAL].automatic_evaluation is True
        assert by_name[VIOLATION_ILLEGAL_PARKING].automatic_evaluation is True

        state = RuleEngineState()
        jeepney = _vehicle(2, "jeepney", 0.0, 120, w=40, h=20)
        jeepney.update(
            {
                "speed_px_per_sec": 0.0,
                "direction_degrees": 0.0,
                "terminal_passenger_activity": True,
            }
        )
        events = []
        for frame, t in enumerate((0.0, 0.6, 1.2)):
            events.extend(
                evaluate_detection_rules(
                    [dict(jeepney, timestamp_sec=t)],
                    state,
                    frame_number=frame,
                    zones={"loading_unloading": LOADING_ZONE},
                    params={"loading_dwell_sec": 0.5, "stationary_px": 8.0},
                    enabled_violations=(VIOLATION_ILLEGAL_TERMINAL,),
                    model_classes=("jeepney",),
                )
            )
        assert any(
            event.violation_type == VIOLATION_ILLEGAL_TERMINAL and event.outcome == "review"
            for event in events
        )
        assert all(event.outcome != "confirmed" for event in events)

    def test_stop_or_headingless_approach_is_not_boarding(self):
        state = RuleEngineState()
        jeepney = _vehicle(2, "jeepney", 0.0, 120, w=80, h=40)
        jeepney["speed_px_per_sec"] = 0.0
        person = _vehicle(3, "person", 0.0, 140, y=120, w=20, h=30)
        person["direction_degrees"] = None
        events = []
        for frame, t in enumerate((0.0, 1.0, 2.0, 3.0)):
            events.extend(
                evaluate_detection_rules(
                    [dict(jeepney, timestamp_sec=t), dict(person, timestamp_sec=t)],
                    state,
                    frame_number=frame,
                    zones={"loading_unloading": LOADING_ZONE},
                    params={"loading_dwell_sec": 0.5, "stationary_px": 8.0},
                    enabled_violations=(VIOLATION_ILLEGAL_TERMINAL,),
                    model_classes=("jeepney", "person"),
                )
            )
        assert events == []

    def test_activity_region_diagnostics_do_not_claim_the_evaluator_runs(self):
        scene = _activity_scene()
        flags = scene_capability_flags(dict(scene.legacy_zones), scene, {})
        reported = build_processing_diagnostics(
            model_classes=("jeepney",),
            enabled_violations=(VIOLATION_ILLEGAL_TERMINAL,),
            geometry=None,
            context_flags=flags,
        )
        cap = reported.rule_capabilities[0]
        assert cap.automatic_evaluation is False
        assert "zone:loading_unloading" in cap.notes


class TestPickupTrackEvidence:
    def _drive(self, labels, *, jump_at=None):
        track = TrackState()
        state = RuleEngineState()
        events = []
        rows = []
        for index, (t, label) in enumerate(labels):
            x = 4000.0 if jump_at is not None and t >= jump_at else 100.0 + index * 30.0
            veh = _vehicle(1, label, t, x)
            person = _cargo_person(t, x)
            annotated = track.update([veh, person], now=t)
            rows.append(annotated[0])
            events.extend(
                check_cargo_passenger(
                    annotated,
                    state,
                    index,
                    model_classes=("person", "pickup_truck", "truck", "car"),
                    params={"_track_history": track.history_view(t)},
                )
            )
        return track, state, events, rows

    def test_brief_car_prediction_keeps_pickup_history_and_cargo_evidence(self):
        labels = []
        t = 0.0
        while t <= 8.0 + 1e-9:
            labels.append((round(t, 2), "car" if abs(t - 3.0) < 1e-9 else "pickup_truck"))
            t += 0.5
        track, _state, events, rows = self._drive(labels)
        flicker = next(row for row in rows if abs(row["timestamp_sec"] - 3.0) < 1e-9)
        assert flicker["class_label"] == "car"
        assert flicker["raw_class"] == "car"
        assert flicker["resolved_track_class"] == "pickup_truck"
        snap = track.history_view(now=8.0).get(1)
        assert snap is not None
        assert len(snap.observations) > 4
        assert snap.class_label == "car" or snap.resolved_track_class == "pickup_truck"
        assert snap.resolved_track_class == "pickup_truck"
        assert any(
            event.violation_type == VIOLATION_CARGO_PASSENGERS and event.outcome == "review"
            for event in events
        )
        assert all(event.outcome != "confirmed" for event in events)

    def test_one_false_pickup_does_not_make_a_car_cargo_applicable(self):
        labels = [(round(i * 0.5, 2), "car") for i in range(0, 9)]
        labels.append((4.5, "pickup_truck"))
        track, _state, events, rows = self._drive(labels)
        flipped = rows[-1]
        assert flipped["class_label"] == "pickup_truck"
        assert flipped["resolved_track_class"] == "car"
        assert cargo_applicability_label(flipped) == "car"
        snap = track.history_view(now=4.5).get(1)
        assert snap is not None
        assert len(snap.observations) > 1
        assert events == []

    def test_sustained_pickup_with_cargo_person_is_review_only(self):
        labels = [(round(i * 0.5, 2), "pickup_truck") for i in range(0, 17)]
        _track, _state, events, rows = self._drive(labels)
        assert rows[0]["resolved_track_class"] is None
        assert rows[0]["class_label"] == "pickup_truck"
        established = next(row for row in rows if row["timestamp_sec"] >= MOTION_WINDOW_SEC)
        assert established["resolved_track_class"] == "pickup_truck"
        reviews = [
            event
            for event in events
            if event.violation_type == VIOLATION_CARGO_PASSENGERS
        ]
        assert reviews
        assert all(event.outcome == "review" for event in reviews)

    def test_sustained_car_label_resets_a_pickup_identity(self):
        labels = [(round(i * 0.5, 2), "pickup_truck") for i in range(0, 5)]
        labels += [(round(2.0 + i * 0.5, 2), "car") for i in range(1, 6)]
        track, _state, events, rows = self._drive(labels)
        assert rows[4]["resolved_track_class"] == "pickup_truck"
        snap = track.history_view(now=labels[-1][0]).get(1)
        assert snap is not None
        assert snap.class_label == "car"
        assert len(snap.observations) == 1
        assert snap.identity_epoch > 1
        assert events == [] or all(event.outcome == "review" for event in events)

    def test_discontinuous_motion_drops_old_evidence(self):
        labels = [(round(i * 0.5, 2), "pickup_truck") for i in range(0, 8)]
        track, state, events, _rows = self._drive(labels, jump_at=3.0)
        snap = track.history_view(now=3.5).get(1)
        assert snap is not None
        assert snap.identity_epoch > 1
        assert snap.observations
        assert all(obs.x > 1000 for obs in snap.observations)
        buffers = state.contextual.get("_cargo_buffers", {})
        assert buffers == {} or all(
            item.timestamp_sec >= 3.0 for buf in buffers.values() for item in buf.frames
        )
        assert events == []

    def test_reappearing_id_after_track_expiry_starts_new_identity(self):
        track = TrackState()
        first = track.update([_vehicle(1, "pickup_truck", 0.0, 100.0)], now=0.0)[0]
        returned = track.update([_vehicle(1, "pickup_truck", 6.0, 100.0)], now=6.0)[0]

        assert first["track_identity_epoch"] == 1
        assert returned["track_identity_epoch"] == 2
        snapshot = track.history_view(now=6.0).get(1)
        assert snapshot is not None
        assert len(snapshot.observations) == 1

    def test_reused_vehicle_id_can_start_a_separate_cargo_review_episode(self):
        """A second pickup must not inherit the first pickup's fired state."""
        track = TrackState()
        state = RuleEngineState()
        reviews = []
        epochs = set()
        clear_events = []
        for index in range(34):
            t = index * 0.5
            x = 100.0 + index * 30.0 + (5000.0 if index >= 15 else 0.0)
            tracked = track.update(
                [_vehicle(1, "pickup_truck", t, x), _cargo_person(t, x)],
                now=t,
            )
            epochs.add(tracked[0]["track_identity_epoch"])
            reviews.extend(evaluate_detection_rules(
                tracked,
                state,
                index,
                enabled_violations=(VIOLATION_CARGO_PASSENGERS,),
                model_classes=("person", "pickup_truck", "truck", "car"),
                now_sec=t,
                history=track.history_view(t),
            ))
            clear_events.extend(state.consume_cleared())

        assert epochs == {1, 2}
        assert len(reviews) == 2
        assert all(event.violation_type == VIOLATION_CARGO_PASSENGERS for event in reviews)
        assert all(event.outcome == "review" for event in reviews)
        assert reviews[0].timestamp_sec < 7.5 < reviews[1].timestamp_sec
        assert len(clear_events) == 1
        assert clear_events[0][:2] == (VIOLATION_CARGO_PASSENGERS, 1)
        assert clear_events[0][2] <= 7.0

    def test_brief_vehicle_label_change_preserves_raw_and_resolved_identity(self):
        track = TrackState()
        car_rows = []
        for index, t in enumerate((0.0, 0.5, 1.0, 1.5, 2.0)):
            annotated = track.update([_vehicle(7, "car", t, 50 + index * 20, w=80, h=40)], now=t)
            car_rows.append(annotated[0])
        assert car_rows[0]["class_label"] == "car"
        assert car_rows[0]["resolved_track_class"] is None
        assert car_rows[-1]["resolved_track_class"] == "car"
        assert len(track.history_view(now=2.0).get(7).observations) == 5

        truck = track.update([_vehicle(8, "truck", 0.0, 10, w=80, h=40)], now=0.0)[0]
        assert truck["class_label"] == "truck"
        assert truck["resolved_track_class"] == "truck"
        car_flicker = track.update(
            [_vehicle(8, "car", 0.4, 30, w=80, h=40)], now=0.4
        )[0]
        same_identity = track.history_view(now=0.4).get(8)
        assert car_flicker["class_label"] == "car"  # Preserve frame prediction.
        assert car_flicker["resolved_track_class"] == "truck"
        assert same_identity is not None
        assert same_identity.identity_epoch == truck["track_identity_epoch"]
        assert len(same_identity.observations) == 2

        person = track.update([_vehicle(9, "truck", 0.0, 10)], now=0.0)[0]
        changed_object = track.update([_vehicle(9, "person", 0.4, 30)], now=0.4)[0]
        assert changed_object["track_identity_epoch"] > person["track_identity_epoch"]
        assert len(track.history_view(now=0.4).get(9).observations) == 1

        assert track.count_by_class().get("car", 0) >= 1
