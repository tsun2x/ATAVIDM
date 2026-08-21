"""Tests for the violation engine.

Tests all canonical violation types defined in detection_config.py.
"""

from __future__ import annotations

import pytest

from core.detection_config import (
    IMPLEMENTED_VIOLATIONS,
    VIOLATION_OBSTRUCTION,
    VIOLATION_COUNTERFLOW,
    VIOLATION_ILLEGAL_PARKING,
    VIOLATION_ILLEGAL_TERMINAL,
    VIOLATION_NO_HELMET,
    VIOLATION_PAVEMENT_MARKINGS,
    VIOLATION_MOTORCYCLE_OVERLOADING,
    VIOLATION_CARGO_PASSENGERS,
    VIOLATION_TRUCK_BAN,
    VIOLATION_SUBSTANDARD_HELMET,
    VIOLATION_DISREGARDING_SIGN,
    VIOLATION_NO_SIDE_MIRROR,
    CANONICAL_VIOLATIONS,
    PARTIAL_VIOLATIONS,
    PLANNED_VIOLATIONS,
)


@pytest.fixture
def rule_params():
    """Default rule parameters for testing."""
    return {
        "confidence_threshold": 0.5,
        "stationary_px": 8.0,
        "stopping_dwell_sec": 0.5,
        "parking_dwell_sec": 0.5,
        "obstruction_dwell_sec": 0.5,
        "loading_dwell_sec": 8.0,
        "crossing_block_sec": 1.0,
        "truck_ban_start": "06:00",
        "truck_ban_end": "09:00",
        "lane_flow_degrees": 90.0,
        "flow_tolerance_degrees": 60.0,
        "min_direction_px": 40.0,
    }


@pytest.fixture
def no_parking_zone():
    """A simple no-parking zone polygon."""
    return [[100, 100], [200, 100], [200, 150], [100, 150], [100, 100]]


@pytest.fixture
def active_lane_zone():
    """A simple active lane zone polygon."""
    return [[50, 50], [250, 50], [250, 120], [50, 120], [50, 50]]


@pytest.fixture
def sample_vehicle():
    """A sample vehicle detection in the active lane zone."""
    return {
        "class_label": "car",
        "track_id": 1,
        "bbox_x": 120,
        "bbox_y": 50,
        "bbox_w": 40,
        "bbox_h": 20,
        "confidence": 0.95,
        "timestamp_sec": 5.0,
        "speed_px_per_sec": 0.1,
        "direction_degrees": 90,
    }


@pytest.fixture
def sample_motorcycle():
    """A sample motorcycle detection with person rider."""
    return {
        "class_label": "motorcycle",
        "track_id": 2,
        "bbox_x": 150,
        "bbox_y": 100,
        "bbox_w": 50,
        "bbox_h": 25,
        "confidence": 0.90,
        "timestamp_sec": 3.0,
        "speed_px_per_sec": 0.0,
        "direction_degrees": 0,
    }


@pytest.fixture
def sample_person():
    """A sample person detection as rider."""
    return {
        "class_label": "person",
        "track_id": 3,
        "bbox_x": 175,
        "bbox_y": 95,
        "bbox_w": 20,
        "bbox_h": 40,
        "confidence": 0.85,
        "timestamp_sec": 3.0,
        "speed_px_per_sec": 0.0,
        "direction_degrees": 0,
    }


@pytest.fixture
def sample_truck():
    """A sample truck detection."""
    return {
        "class_label": "truck",
        "track_id": 4,
        "bbox_x": 100,
        "bbox_y": 100,
        "bbox_w": 80,
        "bbox_h": 30,
        "confidence": 0.92,
        "timestamp_sec": 7.5,
        "speed_px_per_sec": 1.0,
        "direction_degrees": 0,
    }


class TestViolationRegistry:
    """Tests for violation registry configuration."""

    def test_canonical_violations_count(self):
        assert len(CANONICAL_VIOLATIONS) == 12

    def test_canonical_violations_contains_all_twelve(self):
        expected = (
            VIOLATION_ILLEGAL_PARKING,
            VIOLATION_OBSTRUCTION,
            VIOLATION_COUNTERFLOW,
            VIOLATION_TRUCK_BAN,
            VIOLATION_NO_HELMET,
            VIOLATION_NO_SIDE_MIRROR,
            VIOLATION_MOTORCYCLE_OVERLOADING,
            VIOLATION_DISREGARDING_SIGN,
            VIOLATION_PAVEMENT_MARKINGS,
            VIOLATION_ILLEGAL_TERMINAL,
            VIOLATION_CARGO_PASSENGERS,
            VIOLATION_SUBSTANDARD_HELMET,
        )
        assert CANONICAL_VIOLATIONS == expected
        assert len(set(CANONICAL_VIOLATIONS)) == 12

    def test_implemented_violations_is_subset(self):
        for v in IMPLEMENTED_VIOLATIONS:
            assert v in CANONICAL_VIOLATIONS, f"{v} in IMPLEMENTED but not in CANONICAL"

    def test_implemented_count(self):
        assert len(IMPLEMENTED_VIOLATIONS) == 5

    def test_planned_violations_not_in_implemented(self):
        for stub in PLANNED_VIOLATIONS:
            assert stub not in IMPLEMENTED_VIOLATIONS, f"{stub} should be planned, not implemented"

    def test_partial_violations_not_in_implemented(self):
        for partial in PARTIAL_VIOLATIONS:
            assert partial not in IMPLEMENTED_VIOLATIONS, f"{partial} should be partial, not fully implemented"


class TestObstruction:
    """Tests for Obstruction violation."""

    def test_obstruction_fires_when_stationary(self, rule_params, active_lane_zone, sample_vehicle):
        """Obstruction should fire when a vehicle is stationary in active lane."""
        from core.violation_engine import RuleEngineState, check_obstruction
        
        state = RuleEngineState()
        
        # Create a fresh vehicle copy
        vehicle = dict(sample_vehicle)
        
        # Accumulate across frames: the engine emits once, then later frames
        # return [] because the track is already marked fired.
        events = []
        for frame in range(0, 10):
            vehicle["timestamp_sec"] = 5.0 + frame * 0.6
            events.extend(check_obstruction([vehicle], active_lane_zone, state, frame, rule_params))

        assert any(e.violation_type == VIOLATION_OBSTRUCTION for e in events), \
            f"Expected Obstruction event, got {events}"


class TestCounterflow:
    """Tests for Counterflow violation."""

    def test_counterflow_fires_when_opposite_heading(self, rule_params, active_lane_zone, sample_vehicle):
        """Counterflow should fire when vehicle heading is opposite to lane flow."""
        from core.violation_engine import RuleEngineState, check_counterflow
        
        state = RuleEngineState()
        
        # Create a fresh vehicle copy
        vehicle = dict(sample_vehicle)
        vehicle["direction_degrees"] = 270  # Opposite of 90
        
        events = []
        for frame in range(0, 10):
            vehicle["timestamp_sec"] = 5.0 + frame * 0.3
            events.extend(check_counterflow([vehicle], active_lane_zone, state, frame, rule_params))

        assert any(e.violation_type == VIOLATION_COUNTERFLOW for e in events), \
            f"Expected Counterflow event, got {events}"


class TestIllegalParking:
    def test_illegal_parking_fires_for_dwell(self, rule_params, no_parking_zone, sample_vehicle):
        from core.violation_engine import RuleEngineState, check_illegal_parking

        state = RuleEngineState()
        vehicle = {
            "class_label": "car",
            "track_id": 1,
            "bbox_x": 120,
            "bbox_y": 110,
            "bbox_w": 40,
            "bbox_h": 20,
            "confidence": 0.95,
            "speed_px_per_sec": 0.0,
            "direction_degrees": 90,
        }
        events = []
        for frame in range(0, 10):
            vehicle["timestamp_sec"] = 5.0 + frame * 0.6
            events.extend(check_illegal_parking([vehicle], no_parking_zone, state, frame, rule_params))

        assert any(e.violation_type == VIOLATION_ILLEGAL_PARKING for e in events)


class TestIllegalTerminal:
    def test_illegal_terminal_requires_puv(self, rule_params, no_parking_zone):
        from core.violation_engine import RuleEngineState, check_illegal_terminal

        state = RuleEngineState()
        car = {
            "class_label": "car",
            "track_id": 1,
            "bbox_x": 120,
            "bbox_y": 110,
            "bbox_w": 40,
            "bbox_h": 20,
            "confidence": 0.95,
            "speed_px_per_sec": 0.0,
            "direction_degrees": 90,
        }
        events = []
        for frame in range(0, 10):
            car["timestamp_sec"] = 5.0 + frame * 0.6
            events.extend(check_illegal_terminal([car], no_parking_zone, state, frame, rule_params))
        assert len(events) == 0

    def test_illegal_terminal_fires_for_puv_dwell(self, rule_params, no_parking_zone):
        from core.violation_engine import RuleEngineState, check_illegal_terminal

        params = dict(rule_params)
        params["loading_dwell_sec"] = 0.5
        state = RuleEngineState()
        jeepney = {
            "class_label": "jeepney",
            "track_id": 2,
            "bbox_x": 120,
            "bbox_y": 110,
            "bbox_w": 40,
            "bbox_h": 20,
            "confidence": 0.95,
            "speed_px_per_sec": 0.0,
            "direction_degrees": 90,
        }
        events = []
        for frame in range(0, 10):
            jeepney["timestamp_sec"] = 5.0 + frame * 0.6
            events.extend(check_illegal_terminal([jeepney], no_parking_zone, state, frame, params))
        assert any(e.violation_type == VIOLATION_ILLEGAL_TERMINAL for e in events)


class TestTruckBanApplicability:
    def test_truck_ban_fires_for_truck(self, rule_params):
        from datetime import time as dtime
        from core.violation_engine import RuleEngineState, check_truck_ban

        state = RuleEngineState()
        zone = [[50, 50], [250, 50], [250, 200], [50, 200], [50, 50]]
        truck = {
            "class_label": "truck",
            "track_id": 9,
            "bbox_x": 100,
            "bbox_y": 100,
            "bbox_w": 80,
            "bbox_h": 30,
            "confidence": 0.92,
            "speed_px_per_sec": 1.0,
            "direction_degrees": 0,
        }
        events = []
        for frame in range(0, 10):
            truck["timestamp_sec"] = frame * 0.3
            events.extend(
                check_truck_ban(
                    [truck], zone, state, frame, rule_params, now_time=dtime(7, 0)
                )
            )
        assert any(e.violation_type == VIOLATION_TRUCK_BAN for e in events)

    def test_truck_ban_does_not_auto_include_pickup(self, rule_params):
        from datetime import time as dtime
        from core.violation_engine import RuleEngineState, check_truck_ban

        state = RuleEngineState()
        zone = [[50, 50], [250, 50], [250, 200], [50, 200], [50, 50]]
        pickup = {
            "class_label": "pickup_truck",
            "track_id": 10,
            "bbox_x": 100,
            "bbox_y": 100,
            "bbox_w": 60,
            "bbox_h": 25,
            "confidence": 0.9,
            "speed_px_per_sec": 1.0,
            "direction_degrees": 0,
        }
        events = []
        for frame in range(0, 10):
            pickup["timestamp_sec"] = frame * 0.3
            events.extend(
                check_truck_ban(
                    [pickup], zone, state, frame, rule_params, now_time=dtime(7, 0)
                )
            )
        assert events == []


class TestNoHelmet:
    """Tests for No Helmet violation."""

    def test_no_helmet_fires_without_helmet(self, rule_params):
        """No Helmet should fire when rider detected without helmet."""
        from core.violation_engine import RuleEngineState, check_no_helmet
        
        state = RuleEngineState()
        
        mc = {
            "class_label": "motorcycle",
            "track_id": 1,
            "bbox_x": 100, "bbox_y": 100, "bbox_w": 50, "bbox_h": 25,
            "confidence": 0.95, "speed_px_per_sec": 0.0, "direction_degrees": 0,
        }
        
        # Rider positioned to be associated with motorcycle
        person = {
            "class_label": "person",
            "track_id": 2,
            "bbox_x": 110, "bbox_y": 90, "bbox_w": 20, "bbox_h": 40,
            "confidence": 0.9, "speed_px_per_sec": 0.0,
        }
        
        # No helmets in detections!
        events = []
        for frame in range(0, 10):
            ts = frame * 0.3
            mc["timestamp_sec"] = ts
            person["timestamp_sec"] = ts
            evt = check_no_helmet([mc, person], state, frame, rule_params)
            events.extend(evt)
        
        assert any(e.violation_type == VIOLATION_NO_HELMET for e in events), \
            f"Expected No Helmet event, got {events}"


class TestMotorcycleOverloading:
    """Tests for Motorcycle Overloading violation."""

    def test_motorcycle_overloading_fires_for_three_riders(self, rule_params):
        """Motorcycle Overloading (NOT cargo passengers) should fire for 3+ riders."""
        from core.violation_engine import RuleEngineState, check_motorcycle_overloading
        
        state = RuleEngineState()
        
        # Create motorcycle with 3+ riders
        mc = {
            "class_label": "motorcycle",
            "track_id": 1,
            "bbox_x": 100, "bbox_y": 100, "bbox_w": 50, "bbox_h": 25,
            "confidence": 0.95, "speed_px_per_sec": 0.0, "direction_degrees": 0,
        }
        
        persons = [
            {"class_label": "person", "track_id": 2, "bbox_x": 110, "bbox_y": 90, "bbox_w": 20, "bbox_h": 40, "confidence": 0.9, "speed_px_per_sec": 0.0},
            {"class_label": "person", "track_id": 3, "bbox_x": 130, "bbox_y": 95, "bbox_w": 20, "bbox_h": 40, "confidence": 0.9, "speed_px_per_sec": 0.0},
            {"class_label": "person", "track_id": 4, "bbox_x": 118, "bbox_y": 102, "bbox_w": 18, "bbox_h": 22, "confidence": 0.9, "speed_px_per_sec": 0.0},
        ]
        
        events = []
        for frame in range(0, 10):
            ts = frame * 0.3
            mc["timestamp_sec"] = ts
            for p in persons:
                p["timestamp_sec"] = ts
            evt = check_motorcycle_overloading([mc] + persons, state, frame)
            events.extend(evt)
        
        # Should NOT produce Cargo Passengers
        cargo_events = [e for e in events if e.violation_type == VIOLATION_CARGO_PASSENGERS]
        assert len(cargo_events) == 0, "Motorcycle Overloading should NOT produce Cargo Passengers"
        
        # Should produce Motorcycle Overloading
        overload_events = [e for e in events if e.violation_type == VIOLATION_MOTORCYCLE_OVERLOADING]
        assert len(overload_events) > 0, f"Expected Motorcycle Overloading event, got {events}"


class TestViolationDetection:
    """Integration tests for violation detection pipeline."""

    def test_evaluate_detection_rules_full_pipeline(self, tracked_detections, rule_params):
        """Test full pipeline evaluates rules correctly."""
        from core.violation_engine import RuleEngineState, evaluate_detection_rules
        
        state = RuleEngineState()
        zones = {}
        
        events = evaluate_detection_rules(
            tracked_detections, state, 1, zones=zones, params=rule_params
        )
        
        assert isinstance(events, list)

    def test_evaluated_violations_match_enabled_set(self, tracked_detections, rule_params):
        """Only enabled violations should be evaluated."""
        from core.violation_engine import RuleEngineState, evaluate_detection_rules
        
        state = RuleEngineState()
        zones = {}
        
        enabled = (VIOLATION_OBSTRUCTION,)
        
        events = evaluate_detection_rules(
            tracked_detections, state, 1, zones=zones, 
            params=rule_params, enabled_violations=enabled
        )
        
        for event in events:
            assert event.violation_type == VIOLATION_OBSTRUCTION

    def test_enabled_violations_filter_suppresses_disabled(self, tracked_detections, rule_params):
        from core.violation_engine import RuleEngineState, evaluate_detection_rules

        state = RuleEngineState()
        zones = {"active_lane": [[50, 50], [250, 50], [250, 120], [50, 120], [50, 50]]}

        events = evaluate_detection_rules(
            tracked_detections, state, 1, zones=zones,
            params=rule_params, enabled_violations=(),
        )
        assert len(events) == 0

    def test_partial_parking_runs_when_enabled(self, tracked_detections, rule_params, no_parking_zone):
        from core.violation_engine import RuleEngineState, evaluate_detection_rules

        state = RuleEngineState()
        zones = {"no_parking": no_parking_zone}
        enabled = (VIOLATION_ILLEGAL_PARKING,)

        events = evaluate_detection_rules(
            tracked_detections, state, 1, zones=zones,
            params=rule_params, enabled_violations=enabled,
        )
        for event in events:
            assert event.violation_type == VIOLATION_ILLEGAL_PARKING


@pytest.fixture
def tracked_detections():
    """Sample tracked detections for integration tests."""
    return [
        {
            "class_label": "car",
            "track_id": 1,
            "bbox_x": 100,
            "bbox_y": 70,
            "bbox_w": 50,
            "bbox_h": 20,
            "confidence": 0.9,
            "timestamp_sec": 5.0,
            "speed_px_per_sec": 0.0,
            "direction_degrees": 90,
        }
    ]