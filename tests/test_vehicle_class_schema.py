"""Frozen 10-class vehicle detector roster and Phase 2 class-schema contract tests."""

from __future__ import annotations

import json
from pathlib import Path

from core.detection_config import (
    CARGO_PASSENGER_APPLICABLE_CLASSES,
    CANONICAL_VIOLATIONS,
    DEFAULT_TRUCK_BAN_CLASSES,
    FROZEN_VEHICLE_DETECTOR_CLASSES,
    LEGACY_CLASS_PIAGGIO,
    LEGACY_CLASS_UV_EXPRESS_VAN,
    REVIEW_STATE_UNCERTAIN,
    SEVEN_CLASS_BASELINE_MISSING,
    SEVEN_CLASS_BASELINE_VEHICLES,
    VEHICLE_HIERARCHY,
    YOLO_CLASS_AUTORICKSHAW,
    YOLO_CLASS_PICKUP_TRUCK,
    YOLO_CLASS_SUV_CROSSOVER,
    YOLO_CLASS_TRICYCLE,
    YOLO_CLASS_TRUCK,
    YOLO_CLASS_VAN,
    is_cargo_passenger_applicable,
    is_truck_ban_applicable,
    normalize_vehicle_class,
    resolve_vehicle_label,
    seven_class_baseline_coverage,
    vehicle_hierarchy_key,
)
from core.label_migration_audit import load_manifest, summarize_contract
from core.violation_engine import RuleEngineState, evaluate_detection_rules


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "config" / "training" / "class_schema.json"

FROZEN_VEHICLES = (
    "car",
    "van",
    "jeepney",
    "tricycle",
    "autorickshaw",
    "bus",
    "truck",
    "pickup_truck",
    "motorcycle",
    "bicycle",
)

BROAD_KEYS = (
    "passenger_vehicle",
    "public_utility_vehicle",
    "commercial_vehicle",
    "two_or_three_wheeled",
)

APPROVED_VIOLATIONS = (
    "Illegal Parking",
    "Obstruction",
    "Counterflow",
    "Truck-Ban Violation",
    "No Helmet",
    "No Side Mirror",
    "Motorcycle Overloading",
    "Disregarding Traffic Sign",
    "Failure to Follow Road/Pavement Markings",
    "Illegal Terminal",
    "Unauthorized Passenger in Applicable Truck/Pickup Cargo Area",
    "Substandard / Nut-Shell Helmet",
)


def _load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


class TestFrozenVehicleRoster:
    def test_exactly_ten_unique_vehicle_classes(self):
        assert len(FROZEN_VEHICLE_DETECTOR_CLASSES) == 10
        assert len(set(FROZEN_VEHICLE_DETECTOR_CLASSES)) == 10

    def test_exact_membership_and_order(self):
        assert FROZEN_VEHICLE_DETECTOR_CLASSES == FROZEN_VEHICLES

    def test_uv_express_van_and_piaggio_absent_from_canonical(self):
        assert LEGACY_CLASS_UV_EXPRESS_VAN not in FROZEN_VEHICLE_DETECTOR_CLASSES
        assert LEGACY_CLASS_PIAGGIO not in FROZEN_VEHICLE_DETECTOR_CLASSES
        assert "uv_express_van" not in FROZEN_VEHICLE_DETECTOR_CLASSES
        assert "piaggio" not in FROZEN_VEHICLE_DETECTOR_CLASSES

    def test_autorickshaw_present(self):
        assert YOLO_CLASS_AUTORICKSHAW in FROZEN_VEHICLE_DETECTOR_CLASSES
        assert YOLO_CLASS_AUTORICKSHAW == "autorickshaw"

    def test_pickup_truck_distinct_from_truck(self):
        assert YOLO_CLASS_PICKUP_TRUCK in FROZEN_VEHICLE_DETECTOR_CLASSES
        assert YOLO_CLASS_TRUCK in FROZEN_VEHICLE_DETECTOR_CLASSES
        assert YOLO_CLASS_PICKUP_TRUCK != YOLO_CLASS_TRUCK

    def test_suv_and_crossover_merge_into_car(self):
        assert YOLO_CLASS_SUV_CROSSOVER not in FROZEN_VEHICLE_DETECTOR_CLASSES
        assert YOLO_CLASS_VAN in FROZEN_VEHICLE_DETECTOR_CLASSES
        assert normalize_vehicle_class("suv") == "car"
        assert normalize_vehicle_class("suv_crossover") == "car"
        assert resolve_vehicle_label("suv").canonical_class == "car"
        assert resolve_vehicle_label("suv_crossover").canonical_class == "car"
        assert vehicle_hierarchy_key("suv") == "passenger_vehicle"
        assert vehicle_hierarchy_key("suv_crossover") == "passenger_vehicle"
        assert vehicle_hierarchy_key("van") == "passenger_vehicle"

    def test_tricycle_distinct_from_autorickshaw(self):
        assert YOLO_CLASS_TRICYCLE in FROZEN_VEHICLE_DETECTOR_CLASSES
        assert YOLO_CLASS_AUTORICKSHAW in FROZEN_VEHICLE_DETECTOR_CLASSES
        assert YOLO_CLASS_TRICYCLE != YOLO_CLASS_AUTORICKSHAW

    def test_hierarchy_updated(self):
        assert VEHICLE_HIERARCHY["public_utility_vehicle"] == (
            "jeepney",
            "tricycle",
            "autorickshaw",
            "bus",
        )
        assert VEHICLE_HIERARCHY["two_or_three_wheeled"] == (
            "motorcycle",
            "tricycle",
            "autorickshaw",
            "bicycle",
        )
        assert "uv_express_van" not in VEHICLE_HIERARCHY["public_utility_vehicle"]
        assert "piaggio" not in VEHICLE_HIERARCHY["two_or_three_wheeled"]

    def test_broad_categories_are_not_detector_labels(self):
        for key in BROAD_KEYS:
            assert key not in FROZEN_VEHICLE_DETECTOR_CLASSES

    def test_unknown_uncertain_are_not_detector_labels(self):
        assert "UNKNOWN" not in FROZEN_VEHICLE_DETECTOR_CLASSES
        assert "UNCERTAIN" not in FROZEN_VEHICLE_DETECTOR_CLASSES
        assert "unknown" not in FROZEN_VEHICLE_DETECTOR_CLASSES


class TestLegacyNormalization:
    def test_uv_express_van_normalizes_to_van(self):
        assert normalize_vehicle_class("uv_express_van") == "van"
        resolved = resolve_vehicle_label("uv_express_van")
        assert resolved.raw_class == "uv_express_van"
        assert resolved.canonical_class == "van"
        assert resolved.review_state is None
        assert resolved.is_canonical

    def test_piaggio_sidecar_resolves_to_tricycle(self):
        resolved = resolve_vehicle_label("piaggio", body_form="sidecar")
        assert resolved.raw_class == "piaggio"
        assert resolved.canonical_class == "tricycle"
        assert resolved.review_state is None

    def test_piaggio_integrated_resolves_to_autorickshaw(self):
        resolved = resolve_vehicle_label("piaggio", body_form="integrated")
        assert resolved.raw_class == "piaggio"
        assert resolved.canonical_class == "autorickshaw"
        assert resolved.review_state is None

    def test_ambiguous_piaggio_fail_closed_uncertain(self):
        resolved = resolve_vehicle_label("piaggio")
        assert resolved.raw_class == "piaggio"
        assert resolved.canonical_class is None
        assert resolved.review_state == REVIEW_STATE_UNCERTAIN
        assert resolved.fail_closed
        # Must not silently become either three-wheel class
        assert normalize_vehicle_class("piaggio") == "piaggio"

    def test_ambiguous_piaggio_does_not_drive_violation_evaluation(self):
        """UNCERTAIN piaggio detections are not treated as canonical vehicles."""
        zone = [[0, 0], [300, 0], [300, 300], [0, 300]]
        state = RuleEngineState()
        tracked = [
            {
                "class_label": "piaggio",  # unresolved ambiguous brand label
                "raw_class": "piaggio",
                "canonical_class": None,
                "class_review_state": REVIEW_STATE_UNCERTAIN,
                "track_id": 7,
                "bbox_x": 100.0,
                "bbox_y": 100.0,
                "bbox_w": 40.0,
                "bbox_h": 30.0,
                "confidence": 0.95,
                "timestamp_sec": 0.0,
                "speed_px_per_sec": 0.0,
                "direction_degrees": 90.0,
            }
        ]
        events = []
        for t in (0.0, 5.0, 12.0, 35.0):
            tracked[0]["timestamp_sec"] = t
            events.extend(
                evaluate_detection_rules(
                    tracked,
                    state,
                    frame_number=int(t),
                    zones={"no_parking": zone, "loading_unloading": zone, "active_lane": zone},
                    params={
                        "parking_dwell_sec": 1.0,
                        "loading_dwell_sec": 1.0,
                        "obstruction_dwell_sec": 1.0,
                        "stationary_px": 8.0,
                    },
                    enabled_violations=tuple(CANONICAL_VIOLATIONS),
                    model_classes=FROZEN_VEHICLE_DETECTOR_CLASSES,
                )
            )
        assert events == []


class TestSevenClassBaseline:
    def test_seven_class_baseline_is_valid_subset(self):
        coverage = seven_class_baseline_coverage(SEVEN_CLASS_BASELINE_VEHICLES)
        assert coverage["seven_class_baseline_complete"] is True
        assert coverage["is_valid_seven_class_subset"] is True
        assert set(coverage["missing_canonical"]) == set(SEVEN_CLASS_BASELINE_MISSING)

    def test_missing_three_production_classes_reported(self):
        assert SEVEN_CLASS_BASELINE_MISSING == (
            "van",
            "autorickshaw",
            "pickup_truck",
        )
        coverage = seven_class_baseline_coverage(SEVEN_CLASS_BASELINE_VEHICLES)
        for name in SEVEN_CLASS_BASELINE_MISSING:
            assert name in coverage["missing_canonical"]
            assert name not in coverage["present_canonical"]


class TestClassSchemaContract:
    def test_schema_version_and_status(self):
        schema = _load_schema()
        assert schema["schema_version"] == "1.3.0"
        assert schema["status"] == "frozen_for_phase2_pilot"

    def test_object_and_scene_counts(self):
        schema = _load_schema()
        objects = schema["object_classes"]
        scenes = schema["scene_classes"]
        assert len(objects) == 15
        assert len(scenes) == 9
        assert len(objects) + len(scenes) == 24
        assert schema["counts"]["vehicle_detector_classes"] == 10
        assert schema["counts"]["object_classes"] == 15
        assert schema["counts"]["scene_classes"] == 9
        assert schema["counts"]["total_labels"] == 24

    def test_vehicle_detector_classes_match_runtime(self):
        schema = _load_schema()
        assert tuple(schema["vehicle_detector_classes"]) == FROZEN_VEHICLE_DETECTOR_CLASSES
        assert set(schema["vehicle_detector_classes"]).issubset(set(schema["object_classes"]))

    def test_schema_excludes_legacy_canonical_names(self):
        schema = _load_schema()
        objects = set(schema["object_classes"])
        vehicles = set(schema["vehicle_detector_classes"])
        assert "uv_express_van" not in objects
        assert "piaggio" not in objects
        assert "uv_express_van" not in vehicles
        assert "piaggio" not in vehicles
        assert "suv_crossover" not in objects
        assert "suv_crossover" not in vehicles
        assert "uv_express_van" in schema["excluded_classes"]
        assert "piaggio" in schema["excluded_classes"]
        assert "suv_crossover" in schema["excluded_classes"]
        assert "autorickshaw" in objects
        assert "autorickshaw" in vehicles

    def test_hierarchy_in_schema(self):
        schema = _load_schema()
        assert schema["hierarchy"]["commercial_vehicle"] == ["truck", "pickup_truck"]
        assert schema["hierarchy"]["passenger_vehicle"] == ["car", "van"]
        assert "autorickshaw" in schema["hierarchy"]["public_utility_vehicle"]
        assert "uv_express_van" not in schema["hierarchy"]["public_utility_vehicle"]
        assert "piaggio" not in schema["hierarchy"]["two_or_three_wheeled"]

    def test_runtime_and_schema_agree_via_audit(self):
        contract = summarize_contract()
        assert contract["schema_version"] == "1.3.0"
        assert contract["runtime_matches_schema"] is True
        assert len(contract["vehicle_detector_classes"]) == 10


class TestMigrationManifest:
    def test_manifest_safe_and_review_actions(self):
        manifest = load_manifest()
        assert manifest["from_schema_version"] == "1.2.0"
        assert manifest["to_schema_version"] == "1.3.0"
        safe = {item["from"]: item["to"] for item in manifest["safe_consolidations"]}
        assert safe["suv"] == "car"
        assert safe["suv_crossover"] == "car"
        assert manifest["requires_image_review"] == []
        assert manifest["added_canonical_classes"] == []
        assert manifest["removed_canonical_classes"] == ["suv_crossover"]


class TestApplicabilityHelpers:
    def test_pickup_applicable_to_cargo_passenger_rule(self):
        assert is_cargo_passenger_applicable("pickup_truck")
        assert is_cargo_passenger_applicable("truck")
        assert set(CARGO_PASSENGER_APPLICABLE_CLASSES) == {"truck", "pickup_truck"}
        assert not is_cargo_passenger_applicable("van")
        assert not is_cargo_passenger_applicable("piaggio")

    def test_truck_ban_defaults_exclude_pickup(self):
        assert DEFAULT_TRUCK_BAN_CLASSES == ("truck",)
        assert is_truck_ban_applicable("truck")
        assert not is_truck_ban_applicable("pickup_truck")

    def test_legacy_suv_normalizes_to_car(self):
        assert normalize_vehicle_class("suv") == "car"
        assert normalize_vehicle_class("suv_crossover") == "car"
        assert vehicle_hierarchy_key("suv") == "passenger_vehicle"


class TestViolationRosterUnchanged:
    def test_canonical_violations_remain_exactly_twelve(self):
        assert len(CANONICAL_VIOLATIONS) == 12
        assert len(set(CANONICAL_VIOLATIONS)) == 12
        assert CANONICAL_VIOLATIONS == APPROVED_VIOLATIONS

    def test_parking_and_terminal_remain_separate(self):
        assert "Illegal Parking" in CANONICAL_VIOLATIONS
        assert "Illegal Terminal" in CANONICAL_VIOLATIONS
        assert "Illegal Parking / Illegal Terminal" not in CANONICAL_VIOLATIONS
