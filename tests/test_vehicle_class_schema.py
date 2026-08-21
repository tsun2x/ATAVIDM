"""Frozen vehicle detector class and Phase 2 class-schema contract tests."""

from __future__ import annotations

import json
from pathlib import Path

from core.detection_config import (
    CARGO_PASSENGER_APPLICABLE_CLASSES,
    CANONICAL_VIOLATIONS,
    DEFAULT_TRUCK_BAN_CLASSES,
    FROZEN_VEHICLE_DETECTOR_CLASSES,
    VEHICLE_HIERARCHY,
    YOLO_CLASS_PICKUP_TRUCK,
    YOLO_CLASS_TRUCK,
    is_cargo_passenger_applicable,
    is_truck_ban_applicable,
    normalize_vehicle_class,
    vehicle_hierarchy_key,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "config" / "training" / "class_schema.json"

FROZEN_VEHICLES = (
    "car",
    "suv_crossover",
    "van",
    "jeepney",
    "uv_express_van",
    "tricycle",
    "piaggio",
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


def _load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


class TestFrozenVehicleRoster:
    def test_exactly_twelve_unique_vehicle_classes(self):
        assert len(FROZEN_VEHICLE_DETECTOR_CLASSES) == 12
        assert len(set(FROZEN_VEHICLE_DETECTOR_CLASSES)) == 12

    def test_exact_membership_matches_frozen_list(self):
        assert FROZEN_VEHICLE_DETECTOR_CLASSES == FROZEN_VEHICLES

    def test_pickup_truck_is_distinct_from_truck(self):
        assert YOLO_CLASS_PICKUP_TRUCK in FROZEN_VEHICLE_DETECTOR_CLASSES
        assert YOLO_CLASS_TRUCK in FROZEN_VEHICLE_DETECTOR_CLASSES
        assert YOLO_CLASS_PICKUP_TRUCK != YOLO_CLASS_TRUCK

    def test_commercial_vehicle_includes_truck_and_pickup(self):
        commercial = VEHICLE_HIERARCHY["commercial_vehicle"]
        assert YOLO_CLASS_TRUCK in commercial
        assert YOLO_CLASS_PICKUP_TRUCK in commercial
        assert vehicle_hierarchy_key("pickup_truck") == "commercial_vehicle"
        assert vehicle_hierarchy_key("truck") == "commercial_vehicle"

    def test_broad_categories_are_not_detector_labels(self):
        for key in BROAD_KEYS:
            assert key not in FROZEN_VEHICLE_DETECTOR_CLASSES
        assert "private_vehicle" not in FROZEN_VEHICLE_DETECTOR_CLASSES
        assert "Private Vehicle" not in FROZEN_VEHICLE_DETECTOR_CLASSES

    def test_unknown_uncertain_are_not_detector_labels(self):
        assert "UNKNOWN" not in FROZEN_VEHICLE_DETECTOR_CLASSES
        assert "UNCERTAIN" not in FROZEN_VEHICLE_DETECTOR_CLASSES
        assert "unknown" not in FROZEN_VEHICLE_DETECTOR_CLASSES


class TestClassSchemaContract:
    def test_schema_version_and_status(self):
        schema = _load_schema()
        assert schema["schema_version"] == "1.1.0"
        assert schema["status"] == "frozen_for_phase2_pilot"

    def test_object_and_scene_counts(self):
        schema = _load_schema()
        objects = schema["object_classes"]
        scenes = schema["scene_classes"]
        assert len(objects) == 17
        assert len(scenes) == 9
        assert len(objects) + len(scenes) == 26
        assert len(set(objects) | set(scenes)) == 26

    def test_vehicle_detector_classes_match_runtime(self):
        schema = _load_schema()
        assert tuple(schema["vehicle_detector_classes"]) == FROZEN_VEHICLE_DETECTOR_CLASSES
        assert set(schema["vehicle_detector_classes"]).issubset(set(schema["object_classes"]))

    def test_commercial_hierarchy_in_schema(self):
        schema = _load_schema()
        assert schema["hierarchy"]["commercial_vehicle"] == ["truck", "pickup_truck"]
        # pickup must not appear under another hierarchy bucket
        for key, members in schema["hierarchy"].items():
            if key == "commercial_vehicle":
                continue
            assert "pickup_truck" not in members

    def test_excluded_non_detector_labels(self):
        schema = _load_schema()
        excluded = set(schema["excluded_classes"])
        objects = set(schema["object_classes"])
        for name in (
            "violation",
            "collision_vehicle",
            "private_vehicle",
            "public_utility_vehicle",
            "unknown",
        ):
            assert name in excluded
            assert name not in objects


class TestApplicabilityHelpers:
    def test_pickup_applicable_to_cargo_passenger_rule(self):
        assert is_cargo_passenger_applicable("pickup_truck")
        assert is_cargo_passenger_applicable("truck")
        assert set(CARGO_PASSENGER_APPLICABLE_CLASSES) == {"truck", "pickup_truck"}
        assert not is_cargo_passenger_applicable("van")
        assert not is_cargo_passenger_applicable("car")

    def test_truck_ban_defaults_exclude_pickup(self):
        assert DEFAULT_TRUCK_BAN_CLASSES == ("truck",)
        assert is_truck_ban_applicable("truck")
        assert not is_truck_ban_applicable("pickup_truck")
        assert is_truck_ban_applicable(
            "pickup_truck", ban_classes=("truck", "pickup_truck")
        )

    def test_legacy_suv_normalizes_to_suv_crossover(self):
        assert normalize_vehicle_class("suv") == "suv_crossover"
        assert vehicle_hierarchy_key("suv") == "passenger_vehicle"


class TestViolationRosterUnchanged:
    def test_canonical_violations_remain_twelve(self):
        assert len(CANONICAL_VIOLATIONS) == 12
        assert len(set(CANONICAL_VIOLATIONS)) == 12
        assert "Illegal Parking" in CANONICAL_VIOLATIONS
        assert "Illegal Terminal" in CANONICAL_VIOLATIONS
        assert "Illegal Parking / Illegal Terminal" not in CANONICAL_VIOLATIONS
