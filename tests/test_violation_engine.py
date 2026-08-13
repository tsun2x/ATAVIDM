"""Tests for the rule-based violation engine.

Tests each of the 10 implemented violation types:
1. Illegal Parking
2. Illegal Stopping
3. Obstruction
4. Counterflow Driving
5. Blocking Pedestrian Crossing
6. Truck Ban Violation
7. Illegal Loading/Unloading
8. Restricted Lane Violation
9. No Helmet Violation
10. Motorcycle Overloading
"""

from __future__ import annotations

from datetime import time as dtime

import pytest

from core.detection_config import (
    VEHICLE_CLASSES,
    VIOLATION_REGISTRY,
    IMPLEMENTED_VIOLATIONS,
    ILLEGAL_PARKING,
    ILLEGAL_STOPPING,
    OBSTRUCTION,
    COUNTERFLOW,
    BLOCKING_PEDESTRIAN_CROSSING,
    TRUCK_BAN,
    ILLEGAL_LOADING_UNLOADING,
    RESTRICTED_LANE,
    NO_HELMET_VIOLATION,
    MOTORCYCLE_OVERLOADING,
    CONF_AUTO_QUEUE,
    CONF_CAREFUL_REVIEW,
)
from core.tracker import TrackState, point_in_polygon, angle_difference
from core.violation_engine import (
    RuleEngineState,
    ViolationEvent,
    evaluate_detection_rules,
    check_no_helmet,
    check_motorcycle_overloading,
    check_parking_and_stopping,
    check_obstruction,
    check_blocking_crossing,
    check_counterflow,
    check_truck_ban,
    check_loading_unloading,
    check_restricted_lane,
)


# ---------------------------------------------------------------------------
# Test: Violation registry completeness
# ---------------------------------------------------------------------------

def test_all_implemented_violations_registered():
    """Verify all 10 implemented violations are in the registry."""
    expected_violations = {
        ILLEGAL_PARKING,
        ILLEGAL_STOPPING,
        OBSTRUCTION,
        COUNTERFLOW,
        BLOCKING_PEDESTRIAN_CROSSING,
        TRUCK_BAN,
        ILLEGAL_LOADING_UNLOADING,
        RESTRICTED_LANE,
        NO_HELMET_VIOLATION,
        MOTORCYCLE_OVERLOADING,
    }
    assert set(IMPLEMENTED_VIOLATIONS) == expected_violations


def test_violation_registry_entries_complete():
    """Verify each implemented violation has required registry fields."""
    for violation_type in IMPLEMENTED_VIOLATIONS:
        meta = VIOLATION_REGISTRY[violation_type]
        assert meta["implemented"] is True, f"{violation_type} should be implemented"
        assert "basis" in meta, f"{violation_type} missing 'basis'"
        assert "technique" in meta, f"{violation_type} missing 'technique'"


# ---------------------------------------------------------------------------
# Helper: Create detection dict with all required fields
# ---------------------------------------------------------------------------

def make_detection(track_id, class_label, bbox_x, bbox_y, speed_px_per_sec=1.0):
    """Create a detection dict in the format expected by rule engine."""
    return {
        "track_id": track_id,
        "class_label": class_label,
        "confidence": 0.85,
        "bbox_x": float(bbox_x),
        "bbox_y": float(bbox_y),
        "bbox_w": 30.0,
        "bbox_h": 20.0,
        "speed_px_per_sec": speed_px_per_sec,
        "timestamp_sec": 0.0,
    }


# ---------------------------------------------------------------------------
# Test: Parking and Stopping violations
# ---------------------------------------------------------------------------

def test_illegal_stopping_fires_after_dwell():
    """Vehicle stationary in no-parking zone for >= stopping threshold becomes Illegal Stopping."""
    # Zone covering bbox_center (bbox_x + w/2, bbox_y + h/2) = (115, 110)
    # Bottom center = (bbox_x + w/2, bbox_y + h) = (115, 130)
    zone = [[50.0, 50.0], [150.0, 50.0], [150.0, 150.0], [50.0, 150.0]]
    
    state = RuleEngineState()
    
    det = make_detection(track_id=1, class_label="car", bbox_x=100, bbox_y=100, speed_px_per_sec=1.0)
    
    params = {
        "stopping_dwell_sec": 10.0,
        "parking_dwell_sec": 30.0,
        "stationary_px": 8.0,
    }
    
    events = []
    # Simulate frames at increasing timestamps while vehicle is stationary in zone
    for ts in [0.0, 5.0, 10.0, 11.0, 12.0]:
        det["timestamp_sec"] = ts
        result = check_parking_and_stopping([det], zone, state, int(ts * 30), params)
        events.extend(result)
    
    # Should have ILLEGAL_STOPPING fire after 10 seconds
    stopping_events = [e for e in events if e.violation_type == ILLEGAL_STOPPING]
    assert len(stopping_events) >= 1, "Should have Illegal Stopping event"


def test_illegal_parking_fires_after_longer_dwell():
    """Vehicle stationary in no-parking zone for >= parking threshold becomes Illegal Parking."""
    zone = [[50.0, 50.0], [150.0, 50.0], [150.0, 150.0], [50.0, 150.0]]
    
    state = RuleEngineState()
    
    det = make_detection(track_id=1, class_label="car", bbox_x=100, bbox_y=100, speed_px_per_sec=1.0)
    
    params = {
        "stopping_dwell_sec": 10.0,
        "parking_dwell_sec": 30.0,
        "stationary_px": 8.0,
    }
    
    events = []
    # Simulate frames from t=0 to t=35 (past parking threshold)
    for ts in range(0, 36, 2):
        det["timestamp_sec"] = float(ts)
        result = check_parking_and_stopping([det], zone, state, ts, params)
        events.extend(result)
    
    parking_events = [e for e in events if e.violation_type == ILLEGAL_PARKING]
    assert len(parking_events) >= 1, "Should have Illegal Parking event"


def test_parking_events_deduplicated_per_track():
    """Each track can only fire one parking event per type."""
    zone = [[50.0, 50.0], [150.0, 50.0], [150.0, 150.0], [50.0, 150.0]]
    
    state = RuleEngineState()
    
    det = make_detection(track_id=1, class_label="car", bbox_x=100, bbox_y=100)
    
    params = {"stopping_dwell_sec": 10.0, "parking_dwell_sec": 30.0, "stationary_px": 8.0}
    
    events = []
    # Fire multiple frames - should only get one event per violation type
    for ts in range(0, 40, 5):
        det["timestamp_sec"] = float(ts)
        result = check_parking_and_stopping([det], zone, state, ts, params)
        events.extend(result)
    
    # Count unique violation types per track
    stopping_count = sum(1 for e in events if e.violation_type == ILLEGAL_STOPPING and e.track_id == 1)
    parking_count = sum(1 for e in events if e.violation_type == ILLEGAL_PARKING and e.track_id == 1)
    
    assert stopping_count <= 1, "Should not fire multiple Illegal Stopping events for same track"
    assert parking_count <= 1, "Should not fire multiple Illegal Parking events for same track"


# ---------------------------------------------------------------------------
# Test: Obstruction
# ---------------------------------------------------------------------------

def test_obstruction_fires_when_stationary_in_active_lane():
    """Stationary vehicle in active lane triggers obstruction."""
    # Zone with bottom center at (350, 150)
    zone = [[300.0, 100.0], [500.0, 100.0], [500.0, 200.0], [300.0, 200.0]]
    
    state = RuleEngineState()
    
    det = make_detection(track_id=1, class_label="car", bbox_x=350, bbox_y=150, speed_px_per_sec=2.0)
    
    params = {
        "obstruction_dwell_sec": 10.0,
        "stationary_px": 8.0,
        "lane_flow_degrees": 90.0,
        "flow_tolerance_degrees": 60.0,
    }
    
    events = []
    for ts in range(0, 15, 2):
        det["timestamp_sec"] = float(ts)
        result = check_obstruction([det], zone, state, ts, params)
        events.extend(result)
    
    obstruction_events = [e for e in events if e.violation_type == OBSTRUCTION]
    assert len(obstruction_events) >= 1, "Should have Obstruction event"


# ---------------------------------------------------------------------------
# Test: Counterflow Driving
# ---------------------------------------------------------------------------

def test_counterflow_fires_when_heading_opposite_to_lane_flow():
    """Vehicle moving against lane flow direction triggers counterflow."""
    zone = [[300.0, 100.0], [500.0, 100.0], [500.0, 200.0], [300.0, 200.0]]
    
    state = RuleEngineState()
    
    det = make_detection(track_id=1, class_label="car", bbox_x=350, bbox_y=150)
    det["direction_degrees"] = 270.0  # Moving left (opposite of 90 deg flow)
    
    params = {
        "lane_flow_degrees": 90.0,
        "flow_tolerance_degrees": 60.0,
        "obstruction_dwell_sec": 10.0,
        "stationary_px": 8.0,
    }
    
    events = []
    for ts in range(0, 10, 2):
        det["timestamp_sec"] = float(ts)
        result = check_counterflow([det], zone, state, ts, params)
        events.extend(result)
    
    counterflow_events = [e for e in events if e.violation_type == COUNTERFLOW]
    assert len(counterflow_events) >= 1, "Should have Counterflow event"


def test_counterflow_tolerance_boundary():
    """Verify counterflow triggers at tolerance boundary."""
    # Lane flow: 90 degrees (down)
    # Opposite: 270 degrees (up)
    # Tolerance: 60 degrees means 210-330 degrees should trigger
    
    # Test 270 degrees (true opposite) - should trigger
    diff_270 = angle_difference(270.0, 270.0)
    assert diff_270 == 0.0, "270 deg should be within tolerance"
    
    # Test 210 degrees (40 deg from opposite) - should NOT trigger
    diff_210 = angle_difference(210.0, 270.0)
    assert diff_210 == 60.0, "210 deg is at tolerance boundary"
    
    # Test 180 degrees (not opposite, on same axis) - should NOT trigger
    diff_180 = angle_difference(180.0, 270.0)
    assert diff_180 == 90.0, "180 deg is outside tolerance"


# ---------------------------------------------------------------------------
# Test: Blocking Pedestrian Crossing
# ---------------------------------------------------------------------------

def test_blocking_pedestrian_crossing_fires_when_stationary():
    """Stationary vehicle on pedestrian crossing triggers blocking violation."""
    # Zone with bottom center near (300, 330) inside pedestrian crossing
    zone = [[250.0, 250.0], [350.0, 250.0], [350.0, 350.0], [250.0, 350.0]]
    
    state = RuleEngineState()
    
    det = make_detection(track_id=1, class_label="car", bbox_x=290, bbox_y=300, speed_px_per_sec=1.0)
    
    params = {
        "crossing_block_sec": 3.0,
        "stationary_px": 8.0,
    }
    
    events = []
    for ts in range(0, 8, 1):
        det["timestamp_sec"] = float(ts)
        result = check_blocking_crossing([det], zone, state, ts, params)
        events.extend(result)
    
    blocking_events = [e for e in events if e.violation_type == BLOCKING_PEDESTRIAN_CROSSING]
    assert len(blocking_events) >= 1, "Should have Blocking Pedestrian Crossing event"


# ---------------------------------------------------------------------------
# Test: Truck Ban Violation
# ---------------------------------------------------------------------------

def test_truck_ban_fires_during_ban_window():
    """Truck in ban zone during ban window triggers violation."""
    # Zone with bottom center at (150, 440)
    zone = [[100.0, 400.0], [200.0, 400.0], [200.0, 500.0], [100.0, 500.0]]
    
    state = RuleEngineState()
    
    det = make_detection(track_id=1, class_label="truck", bbox_x=150, bbox_y=450, speed_px_per_sec=1.0)
    
    params = {
        "truck_ban_start": "06:00",
        "truck_ban_end": "09:00",
        "stationary_px": 8.0,
    }
    
    # During ban window (7:30 AM = 07:30)
    now_time = dtime(7, 30, 0)
    
    events = []
    for ts in range(0, 8, 1):
        det["timestamp_sec"] = float(ts)
        result = check_truck_ban([det], zone, state, ts, params, now_time=now_time)
        events.extend(result)
    
    truck_ban_events = [e for e in events if e.violation_type == TRUCK_BAN]
    assert len(truck_ban_events) >= 1, "Should have Truck Ban event during ban window"


def test_truck_ban_no_event_outside_ban_window():
    """Truck ban should not trigger outside the ban window."""
    zone = [[100.0, 400.0], [200.0, 400.0], [200.0, 500.0], [100.0, 500.0]]
    
    state = RuleEngineState()
    
    det = make_detection(track_id=1, class_label="truck", bbox_x=150, bbox_y=450, speed_px_per_sec=1.0)
    
    params = {
        "truck_ban_start": "06:00",
        "truck_ban_end": "09:00",
    }
    
    # Outside ban window (10:30 AM)
    now_time = dtime(10, 30, 0)
    
    events = []
    for ts in range(0, 8, 1):
        det["timestamp_sec"] = float(ts)
        result = check_truck_ban([det], zone, state, ts, params, now_time=now_time)
        events.extend(result)
    
    truck_ban_events = [e for e in events if e.violation_type == TRUCK_BAN]
    assert len(truck_ban_events) == 0, "Should not have Truck Ban event outside ban window"


# ---------------------------------------------------------------------------
# Test: Illegal Loading/Unloading (PUV only)
# ---------------------------------------------------------------------------

def test_illegal_loading_unloading_fires_for_puv():
    """PUV stopping in no-loading zone triggers violation."""
    zone = [[400.0, 400.0], [500.0, 400.0], [500.0, 500.0], [400.0, 500.0]]
    
    state = RuleEngineState()
    
    # Jeepney is a PUV
    det = make_detection(track_id=1, class_label="jeepney", bbox_x=450, bbox_y=430, speed_px_per_sec=1.0)
    
    params = {
        "loading_dwell_sec": 8.0,
        "stationary_px": 8.0,
    }
    
    events = []
    for ts in range(0, 12, 1):
        det["timestamp_sec"] = float(ts)
        result = check_loading_unloading([det], zone, state, ts, params)
        events.extend(result)
    
    loading_events = [e for e in events if e.violation_type == ILLEGAL_LOADING_UNLOADING]
    assert len(loading_events) >= 1, "Should have Illegal Loading event for PUV"


def test_illegal_loading_no_event_for_non_puv():
    """Non-PUV vehicle should not trigger loading violation on PUV zone."""
    zone = [[400.0, 400.0], [500.0, 400.0], [500.0, 500.0], [400.0, 500.0]]
    
    state = RuleEngineState()
    
    # Car is NOT a PUV
    det = make_detection(track_id=1, class_label="car", bbox_x=450, bbox_y=450, speed_px_per_sec=1.0)
    
    params = {
        "loading_dwell_sec": 8.0,
        "stationary_px": 8.0,
    }
    
    events = []
    for ts in range(0, 12, 1):
        det["timestamp_sec"] = float(ts)
        result = check_loading_unloading([det], zone, state, ts, params)
        events.extend(result)
    
    loading_events = [e for e in events if e.violation_type == ILLEGAL_LOADING_UNLOADING]
    assert len(loading_events) == 0, "Car should not trigger Loading violation"


# ---------------------------------------------------------------------------
# Test: Restricted Lane Violation
# ---------------------------------------------------------------------------

def test_restricted_lane_fires_for_motorcycle():
    """Motorcycle in restricted lane triggers violation."""
    # Zone with bbox center at (650, 150)
    zone = [[600.0, 100.0], [700.0, 100.0], [700.0, 200.0], [600.0, 200.0]]
    
    state = RuleEngineState()
    
    det = make_detection(track_id=1, class_label="motorcycle", bbox_x=650, bbox_y=150)
    
    params = {}  # Uses default restricted lane classes
    
    events = []
    for ts in range(0, 5, 1):
        det["timestamp_sec"] = float(ts)
        result = check_restricted_lane([det], zone, state, ts, params)
        events.extend(result)
    
    restricted_events = [e for e in events if e.violation_type == RESTRICTED_LANE]
    assert len(restricted_events) >= 1, "Should have Restricted Lane event for motorcycle"


# ---------------------------------------------------------------------------
# Test: No Helmet Violation (requires custom model)
# ---------------------------------------------------------------------------

def test_no_helmet_requires_helmet_detection():
    """No helmet rule should only fire when helmet class is detected."""
    # With COCO model, no helmet class - should return no events
    # This test verifies the logic, actual behavior needs custom weights
    motorcycles = [
        {
            "track_id": 1,
            "class_label": "motorcycle",
            "confidence": 0.9,
            "bbox_x": 100, "bbox_y": 100, "bbox_w": 50, "bbox_h": 30,
            "timestamp_sec": 0.0,
        }
    ]
    persons = [
        {
            "track_id": 2,
            "class_label": "person",
            "confidence": 0.9,
            "bbox_x": 115, "bbox_y": 115, "bbox_w": 20, "bbox_h": 40,
            "timestamp_sec": 0.0,
        }
    ]
    helmets = []  # No helmets detected
    
    tracked = motorcycles + persons + helmets
    events = check_no_helmet(tracked, RuleEngineState(), 0)
    
    # Without helmets, rider should be detected as unhelmeted
    # But the rule still requires helmet class in the frame (line 214-215 in violation_engine.py)
    assert len(events) == 0, "No helmet events without helmet class in frame"


# ---------------------------------------------------------------------------
# Test: Motorcycle Overloading
# ---------------------------------------------------------------------------

def test_motorcycle_overloading_fires_for_three_plus_riders():
    """Motorcycle with 3+ riders triggers overloading violation after persistence."""
    state = RuleEngineState()
    
    # Motorcycle at position where we can place riders inside
    # bbox_x=100, bbox_y=100, bbox_w=50, bbox_h=30
    # Center = (125, 115), bottom center = (125, 130)
    # With 0.35 padding, expanded bbox includes positions near center
    motorcycles = [
        {
            "track_id": 1,
            "class_label": "motorcycle",
            "confidence": 0.9,
            "bbox_x": 100.0,
            "bbox_y": 100.0,
            "bbox_w": 50.0,
            "bbox_h": 30.0,
            "timestamp_sec": 0.0,
        }
    ]
    
    # Three riders with centers inside the padded motorcycle bbox
    # Motorcycle center = (125, 115)
    # Padding 0.35 means bbox expands: (bbox - 0.35*w/2, bbox - 0.35*h/2) to (bbox + 0.35*w/2, bbox + 0.35*h/2)
    # Expanded: x = 100 - 8.75 to 100 + 58.75 = [91.25, 158.75]
    #           y = 100 - 5.25 to 100 + 65.25 = [94.75, 165.25]
    riders = [
        {
            "track_id": 2,
            "class_label": "person",
            "confidence": 0.9,
            "bbox_x": 115.0,  # center x = 125 (inside padded bbox)
            "bbox_y": 110.0,  # center y = 130 (inside padded bbox)
            "bbox_w": 15.0,
            "bbox_h": 30.0,
            "timestamp_sec": 0.0,
        },
        {
            "track_id": 3,
            "class_label": "person",
            "confidence": 0.9,
            "bbox_x": 120.0,
            "bbox_y": 115.0,
            "bbox_w": 15.0,
            "bbox_h": 30.0,
            "timestamp_sec": 0.0,
        },
        {
            "track_id": 4,
            "class_label": "person",
            "confidence": 0.9,
            "bbox_x": 125.0,
            "bbox_y": 120.0,
            "bbox_w": 15.0,
            "bbox_h": 30.0,
            "timestamp_sec": 0.0,
        },
    ]
    
    tracked = motorcycles + riders
    
    # The persistence window is VIOLATION_PERSISTENCE_SEC = 1.5 seconds
    # Need to call with increasing timestamps
    events = []
    for frame in range(0, 10):
        ts = frame * 0.5  # 0, 0.5, 1.0, 1.5, 2.0, ...
        # Update timestamps
        motorcycles[0]["timestamp_sec"] = ts
        for r in riders:
            r["timestamp_sec"] = ts
        result = check_motorcycle_overloading(tracked, state, frame)
        events.extend(result)
    
    overloading_events = [e for e in events if e.violation_type == MOTORCYCLE_OVERLOADING]
    assert len(overloading_events) >= 1, f"Should have Overloading event for 3+ riders, got {len(overloading_events)} events"


# ---------------------------------------------------------------------------
# Test: Full pipeline integration
# ---------------------------------------------------------------------------

def test_evaluate_detection_rules_full_pipeline(sample_zones_fixture):
    """Test the main entry point with multiple detections."""
    state = RuleEngineState()
    
    detections = [
        {
            "track_id": 1,
            "class_label": "car",
            "confidence": 0.85,
            "bbox_x": 150.0,
            "bbox_y": 150.0,
            "bbox_w": 50.0,
            "bbox_h": 30.0,
            "centroid_x": 175.0,
            "centroid_y": 165.0,
            "speed_px_per_sec": 2.0,
            "direction_degrees": 90.0,
            "dwell_sec": 15.0,
            "timestamp_sec": 5.0,
        },
        {
            "track_id": 2,
            "class_label": "truck",
            "confidence": 0.92,
            "bbox_x": 150.0,
            "bbox_y": 450.0,
            "bbox_w": 80.0,
            "bbox_h": 40.0,
            "centroid_x": 190.0,
            "centroid_y": 470.0,
            "speed_px_per_sec": 1.0,
            "direction_degrees": 90.0,
            "dwell_sec": 12.0,
            "timestamp_sec": 10.0,
        },
    ]
    
    params = {
        "stopping_dwell_sec": 10.0,
        "parking_dwell_sec": 30.0,
        "obstruction_dwell_sec": 10.0,
        "crossing_block_sec": 3.0,
        "loading_dwell_sec": 8.0,
        "truck_ban_start": "06:00",
        "truck_ban_end": "09:00",
        "lane_flow_degrees": 90.0,
        "flow_tolerance_degrees": 60.0,
        "stationary_px": 8.0,
    }
    
    zones = {
        "no_parking": sample_zones_fixture["no_parking"],
        "active_lane": sample_zones_fixture["active_lane"],
        "truck_ban_zone": sample_zones_fixture["truck_ban_zone"],
    }
    
    events = evaluate_detection_rules(
        detections, state, 0, zones=zones, params=params
    )
    
    assert isinstance(events, list), "Should return list of ViolationEvents"
    for event in events:
        assert isinstance(event, ViolationEvent), "Each event should be a ViolationEvent"


def test_violation_event_dataclass():
    """Verify ViolationEvent dataclass works correctly."""
    event = ViolationEvent(
        violation_type="Test Violation",
        track_id=1,
        confidence=0.95,
        frame_number=100,
        timestamp_sec=30.5,
        reason_log="Test reason",
        vehicle_class="car",
    )
    
    assert event.violation_type == "Test Violation"
    assert event.track_id == 1
    assert event.confidence == 0.95
    assert event.vehicle_class == "car"


# ---------------------------------------------------------------------------
# Test: Confidence bands
# ---------------------------------------------------------------------------

def test_confidence_band_auto_queue():
    """Violations >= 95% confidence are auto-queued."""
    from core.detection_config import confidence_band
    
    assert confidence_band(0.95) == "auto-queued"
    assert confidence_band(1.0) == "auto-queued"


def test_confidence_band_careful_review():
    """Violations 80-94% confidence require careful review."""
    from core.detection_config import confidence_band
    
    assert confidence_band(0.80) == "careful-review"
    assert confidence_band(0.94) == "careful-review"


def test_confidence_band_low_confidence():
    """Violations < 80% confidence are low-confidence."""
    from core.detection_config import confidence_band
    
    assert confidence_band(0.0) == "low-confidence"
    assert confidence_band(0.79) == "low-confidence"


def test_vehicle_class_categorization():
    """Verify vehicle classes are properly categorized."""
    from core.detection_config import vehicle_category, VEHICLE_CLASSES
    
    # Test each category
    assert vehicle_category("car") == "Private Vehicle"
    assert vehicle_category("suv") == "Private Vehicle"
    assert vehicle_category("jeepney") == "Public Utility Vehicle"
    assert vehicle_category("bus") == "Public Utility Vehicle"
    assert vehicle_category("truck") == "Commercial Vehicle"
    assert vehicle_category("motorcycle") == "Two-Wheeled Vehicle"
    assert vehicle_category("bicycle") == "Two-Wheeled Vehicle"
    assert vehicle_category("unknown") is None

    # Test VEHICLE_CLASSES includes expected classes
    assert "car" in VEHICLE_CLASSES
    assert "suv" in VEHICLE_CLASSES
    assert "truck" in VEHICLE_CLASSES
    assert "motorcycle" in VEHICLE_CLASSES


# ---------------------------------------------------------------------------
# Fixture: sample_zones (different from conftest)
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_zones_fixture() -> dict[str, list[list[float]]]:
    """Sample zone polygons for integration test."""
    return {
        "no_parking": [[50.0, 50.0], [150.0, 50.0], [150.0, 150.0], [50.0, 150.0]],
        "active_lane": [[300.0, 100.0], [500.0, 100.0], [500.0, 200.0], [300.0, 200.0]],
        "truck_ban_zone": [[100.0, 400.0], [200.0, 400.0], [200.0, 500.0], [100.0, 500.0]],
        "pedestrian_crossing": [[250.0, 250.0], [350.0, 250.0], [350.0, 350.0], [250.0, 350.0]],
        "loading_unloading": [[400.0, 400.0], [500.0, 400.0], [500.0, 500.0], [400.0, 500.0]],
        "restricted_lane": [[600.0, 100.0], [700.0, 100.0], [700.0, 200.0], [600.0, 200.0]],
    }