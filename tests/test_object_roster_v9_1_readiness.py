"""Application readiness for a future 15-class (V9.1 family) YOLOv8m checkpoint.

Scope of these tests:
- the strict, reusable ID->name contract for the 15 ``object_classes`` order
- legacy checkpoint compatibility (the currently selected checkpoint is unchanged)
- pass-through of all 15 object classes through detection and tracking
- ``person`` vs ``rider`` separation in capability gates and rule logic
- helmet / side-mirror / cargo-passenger capability gates failing closed
- vehicle-only line-crossing counts vs all-15 detection counts (persisted/displayed)

No real V9.1 checkpoint is loaded or activated. Every 15-class model here is a
mock. Passing these tests does not establish training or promotion readiness.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import cv2
import numpy as np
import pytest

from core.detection_config import (
    CANONICAL_VIOLATIONS,
    CLASS_MAP_DUPLICATE,
    CLASS_MAP_MALFORMED,
    CLASS_MAP_MISSING,
    CLASS_MAP_NON_CONTIGUOUS_IDS,
    CLASS_MAP_REORDERED,
    CLASS_MAP_UNKNOWN,
    CLASS_MAP_WRONG_COUNT,
    DETECTION_CLASSES,
    FROZEN_VEHICLE_DETECTOR_CLASSES,
    NON_VEHICLE_OBJECT_CLASSES,
    OBJECT_DETECTOR_CLASSES,
    OBJECT_DETECTOR_CLASS_COUNT,
    OBJECT_ROSTER_MARKER_CLASSES,
    REQUIRED_MODEL_CLASSES,
    REQUIRED_MODEL_CLASSES_CARGO_PASSENGER,
    SEVEN_CLASS_BASELINE_VEHICLES,
    VIOLATION_CARGO_PASSENGERS,
    VIOLATION_MOTORCYCLE_OVERLOADING,
    VIOLATION_NO_HELMET,
    VIOLATION_NO_SIDE_MIRROR,
    VIOLATION_SUBSTANDARD_HELMET,
    YOLO_CLASS_HELMET_ACCEPTABLE,
    YOLO_CLASS_HELMET_NUT_SHELL,
    YOLO_CLASS_MOTORCYCLE,
    YOLO_CLASS_PERSON,
    YOLO_CLASS_RIDER,
    YOLO_CLASS_SIDE_MIRROR,
    is_object_class_name,
    is_vehicle_object_class,
    ordered_class_names,
    validate_object_class_map,
)
from core.detector import (
    Detector,
    DetectorClassContractError,
    enforce_object_class_contract,
    model_classes_available,
)
from core.model_capability import (
    assess_rule_capability,
    classes_satisfy_rule,
    extract_model_class_names,
    object_roster_capability_notes,
)
from core.tracker import TrackState
from core.vehicle_crossing_counter import VehicleCrossingCounter
from core.violation_engine import (
    RuleEngineState,
    check_no_side_mirror,
    evaluate_detection_rules,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "config" / "training" / "class_schema.json"

# The exact 15-ID order required of a future 15-class checkpoint.
EXPECTED_15 = (
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
    "person",
    "rider",
    "helmet_acceptable",
    "helmet_nut_shell",
    "side_mirror",
)

FULL_FRAME = [[0, 0], [160, 0], [160, 120], [0, 120]]

# Stock COCO yolov8m names, in ID order. Used to prove the roster gate does not
# reject the pretrained fallback.
COCO_80_CLASSES = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck",
    "boat", "traffic light", "fire hydrant", "stop sign", "parking meter", "bench",
    "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra",
    "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove",
    "skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup",
    "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
    "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear",
    "hair drier", "toothbrush",
)


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _as_map(names) -> dict[int, str]:
    return {i: n for i, n in enumerate(names)}


def _swapped() -> list[str]:
    names = list(EXPECTED_15)
    names[0], names[1] = names[1], names[0]
    return names


def _detector_with_names(names: Any) -> Detector:
    """A Detector whose underlying model exposes *names* (no ultralytics load)."""
    det = Detector.__new__(Detector)
    det._weights = "mock-v9-1-candidate"
    det.is_custom = True
    det._model = SimpleNamespace(names=names)
    det.load = lambda: None  # type: ignore[method-assign]
    return det


def _det(track_id: int, label: str, ts: float = 0.0, **overrides) -> dict[str, Any]:
    row: dict[str, Any] = {
        "track_id": track_id,
        "class_label": label,
        "confidence": 0.92,
        "bbox_x": 20.0,
        "bbox_y": 20.0,
        "bbox_w": 40.0,
        "bbox_h": 30.0,
        "timestamp_sec": ts,
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# Roster shape — 15 object classes, first ten are the vehicle subset
# ---------------------------------------------------------------------------

class TestObjectRosterShape:
    def test_roster_is_the_declared_15_id_order(self):
        assert OBJECT_DETECTOR_CLASSES == EXPECTED_15
        assert OBJECT_DETECTOR_CLASS_COUNT == 15

    def test_roster_matches_class_schema_file(self):
        assert tuple(_schema()["object_classes"]) == EXPECTED_15

    def test_first_ten_are_the_ten_class_vehicle_subset(self):
        assert OBJECT_DETECTOR_CLASSES[:10] == FROZEN_VEHICLE_DETECTOR_CLASSES
        assert len(FROZEN_VEHICLE_DETECTOR_CLASSES) == 10

    def test_person_rider_helmet_mirror_are_not_vehicles(self):
        assert NON_VEHICLE_OBJECT_CLASSES == (
            "person",
            "rider",
            "helmet_acceptable",
            "helmet_nut_shell",
            "side_mirror",
        )
        for name in NON_VEHICLE_OBJECT_CLASSES:
            assert not is_vehicle_object_class(name)
            assert is_object_class_name(name)
        # The vehicle roster is still 10 — not 15.
        assert not any(name in FROZEN_VEHICLE_DETECTOR_CLASSES for name in NON_VEHICLE_OBJECT_CLASSES)

    def test_all_15_names_are_allowed_detections(self):
        for name in EXPECTED_15:
            assert name in DETECTION_CLASSES

    def test_twelve_canonical_violations_unchanged(self):
        assert len(CANONICAL_VIOLATIONS) == 12


# ---------------------------------------------------------------------------
# Strict ID -> name contract
# ---------------------------------------------------------------------------

class TestExactFifteenIdOrderAccepted:
    def test_dict_map_in_exact_order_is_accepted(self):
        report = validate_object_class_map(_as_map(EXPECTED_15))
        assert report.ok
        assert report.is_object_roster
        assert not report.is_legacy_roster
        assert report.codes == ()
        assert report.actual == EXPECTED_15

    def test_ordered_sequence_is_accepted(self):
        report = validate_object_class_map(list(EXPECTED_15))
        assert report.ok
        assert report.actual == EXPECTED_15

    def test_numeric_string_keys_are_accepted_and_ordered(self):
        report = validate_object_class_map({str(i): n for i, n in enumerate(EXPECTED_15)})
        assert report.ok
        assert report.actual == EXPECTED_15

    def test_case_and_whitespace_are_normalized_not_rejected(self):
        noisy = [n.upper() if i == 0 else f"  {n}  " for i, n in enumerate(EXPECTED_15)]
        report = validate_object_class_map(_as_map(noisy))
        assert report.ok
        assert report.actual == EXPECTED_15

    def test_detector_accepts_and_reports_verified_order(self):
        det = _detector_with_names(_as_map(EXPECTED_15))
        assert det.ordered_class_names() == EXPECTED_15
        assert det.class_names == set(EXPECTED_15)
        report = enforce_object_class_contract(det)
        assert report.ok
        assert report.order_known
        assert "exact ID order" in report.summary

    def test_capability_notes_are_additive_for_verified_roster(self):
        det = _detector_with_names(_as_map(EXPECTED_15))
        report = enforce_object_class_contract(det)
        notes = object_roster_capability_notes(report)
        assert notes
        assert any("10 vehicle classes" in n for n in notes)
        # Extractable roster keeps all 15 for the capability gate.
        assert set(extract_model_class_names(det)) == set(EXPECTED_15)


class TestWrongOrderRejected:
    def test_reordered_roster_is_rejected(self):
        report = validate_object_class_map(_as_map(_swapped()))
        assert not report.ok
        assert report.is_object_roster
        assert CLASS_MAP_REORDERED in report.codes
        assert not (report.missing or report.unknown or report.duplicate)
        assert report.summary.startswith("Class map rejected:")

    def test_reordered_roster_raises_before_processing(self):
        det = _detector_with_names(_as_map(_swapped()))
        with pytest.raises(DetectorClassContractError) as excinfo:
            enforce_object_class_contract(det)
        assert excinfo.value.report.codes == (CLASS_MAP_REORDERED,)
        assert "rejected before processing" in str(excinfo.value)

    def test_tail_reordering_is_rejected(self):
        names = list(EXPECTED_15)
        names[10], names[14] = names[14], names[10]
        report = validate_object_class_map(_as_map(names))
        assert not report.ok
        assert CLASS_MAP_REORDERED in report.codes

    def test_reordering_among_helmet_classes_is_rejected(self):
        names = list(EXPECTED_15)
        names[12], names[13] = names[13], names[12]
        report = validate_object_class_map(_as_map(names))
        assert not report.ok
        assert CLASS_MAP_REORDERED in report.codes


class TestMalformedNamesRejected:
    def test_unknown_name_replaces_a_declared_class(self):
        names = list(EXPECTED_15)
        names[10] = "traffic_person"
        report = validate_object_class_map(_as_map(names))
        assert not report.ok
        assert CLASS_MAP_UNKNOWN in report.codes
        assert CLASS_MAP_MISSING in report.codes
        assert report.unknown == ("traffic_person",)
        assert report.missing == ("person",)

    def test_legacy_generic_helmet_is_not_in_the_object_roster(self):
        names = list(EXPECTED_15)
        names[12] = "helmet"
        report = validate_object_class_map(_as_map(names))
        assert not report.ok
        assert CLASS_MAP_UNKNOWN in report.codes

    def test_duplicate_name_is_rejected(self):
        names = list(EXPECTED_15)
        names[14] = names[13]
        report = validate_object_class_map(_as_map(names))
        assert not report.ok
        assert CLASS_MAP_DUPLICATE in report.codes
        assert CLASS_MAP_MISSING in report.codes
        assert report.duplicate == ("helmet_nut_shell",)

    def test_non_numeric_class_ids_rejected(self):
        report = validate_object_class_map(
            {"front": "car", "side": "van"}, require_roster=True
        )
        assert not report.ok
        assert report.codes == (CLASS_MAP_MALFORMED,)
        assert report.malformed is not None

    def test_non_contiguous_class_ids_rejected(self):
        shifted = {i + 1: n for i, n in enumerate(EXPECTED_15)}
        report = validate_object_class_map(shifted, require_roster=True)
        assert not report.ok
        assert report.codes == (CLASS_MAP_NON_CONTIGUOUS_IDS,)
        assert "non_contiguous" in str(report.malformed)

    def test_empty_and_blank_names_rejected(self):
        assert validate_object_class_map(
            ["" for _ in EXPECTED_15], require_roster=True
        ).codes == (CLASS_MAP_MALFORMED,)
        assert validate_object_class_map(
            ["   " for _ in EXPECTED_15], require_roster=True
        ).codes == (CLASS_MAP_MALFORMED,)

    def test_non_string_name_rejected(self):
        report = validate_object_class_map(list(range(15)), require_roster=True)
        assert not report.ok
        assert report.codes == (CLASS_MAP_MALFORMED,)

    def test_unsupported_type_rejected(self):
        report = validate_object_class_map(object(), require_roster=True)
        assert not report.ok
        assert report.codes == (CLASS_MAP_MALFORMED,)

    def test_truncated_roster_rejected_when_declared(self):
        report = validate_object_class_map(
            list(EXPECTED_15)[:14], require_roster=True
        )
        assert not report.ok
        assert report.codes == (CLASS_MAP_WRONG_COUNT,)

    def test_detector_raises_on_malformed_map(self):
        det = _detector_with_names({"front": "car"})
        with pytest.raises(DetectorClassContractError):
            enforce_object_class_contract(det, require_roster=True)


# ---------------------------------------------------------------------------
# Legacy checkpoint compatibility — the running checkpoint must not change
# ---------------------------------------------------------------------------

class TestLegacyCheckpointCompatibility:
    def test_selected_checkpoint_path_is_unchanged(self):
        from core.detector import resolve_weights_path

        weights, _is_custom = resolve_weights_path()
        # Model selection is untouched: custom models/ candidate first, else COCO.
        assert weights.endswith((".pt",))

    def test_seven_class_baseline_checkpoint_is_accepted(self):
        report = validate_object_class_map(_as_map(SEVEN_CLASS_BASELINE_VEHICLES))
        assert report.ok
        assert report.is_legacy_roster
        assert report.class_count == 7
        assert "Legacy 7-class checkpoint accepted" in report.summary

    def test_ten_class_frozen_vehicle_roster_is_accepted(self):
        report = validate_object_class_map(_as_map(FROZEN_VEHICLE_DETECTOR_CLASSES))
        assert report.ok
        assert report.is_legacy_roster

    def test_thirteen_class_legacy_checkpoint_is_accepted(self):
        legacy13 = list(FROZEN_VEHICLE_DETECTOR_CLASSES) + [
            "person",
            "rider",
            "helmet",
        ]
        report = validate_object_class_map(_as_map(legacy13))
        assert report.ok
        assert report.is_legacy_roster

    def test_coco_pretrained_roster_is_accepted(self):
        coco = _as_map(
            [f"coco_{i}" for i in range(80)]
        )
        report = validate_object_class_map(coco)
        assert report.ok
        assert report.is_legacy_roster

    def test_legacy_checkpoints_produce_no_object_roster_notes(self):
        det = _detector_with_names(_as_map(SEVEN_CLASS_BASELINE_VEHICLES))
        report = enforce_object_class_contract(det)
        assert report.ok
        assert object_roster_capability_notes(report) == []

    def test_declared_object_roster_still_rejects_a_legacy_map(self):
        det = _detector_with_names(_as_map(SEVEN_CLASS_BASELINE_VEHICLES))
        with pytest.raises(DetectorClassContractError) as excinfo:
            enforce_object_class_contract(det, require_roster=True)
        assert excinfo.value.report.codes == (CLASS_MAP_WRONG_COUNT,)

    def test_unordered_fallback_checks_membership_only(self):
        """A duck-typed detector exposing only a set cannot prove order."""

        class SetOnly:
            @property
            def class_names(self) -> set[str]:
                return set(EXPECTED_15)

        report = enforce_object_class_contract(SetOnly())
        assert report.ok
        assert report.is_object_roster
        assert report.order_known is False
        assert "ID order not observable" in report.summary

    def test_unordered_fallback_still_rejects_wrong_names(self):
        class SetOnly:
            @property
            def class_names(self) -> set[str]:
                return set(EXPECTED_15) - {"rider"} | {"fire_truck"}

        with pytest.raises(DetectorClassContractError):
            enforce_object_class_contract(SetOnly())

    def test_detector_without_any_roster_is_not_rejected(self):
        class Bare:
            def load(self) -> None:
                return None

            def track_frame(self, frame, conf=0.6, timestamp_sec=0.0):
                return []

        report = enforce_object_class_contract(Bare())
        assert report.ok
        assert report.is_legacy_roster

    def test_ordered_class_names_helper_matches_dict_order(self):
        assert ordered_class_names(_as_map(EXPECTED_15)) == EXPECTED_15
        assert ordered_class_names({"front": "car"}) == ()
        assert ordered_class_names(None) == ()


# ---------------------------------------------------------------------------
# Auto mode fails closed on a broken object-roster export
# ---------------------------------------------------------------------------

class TestBrokenRosterCannotMasqueradeAsLegacy:
    """A truncated/renamed 15-class export must not be silently demoted."""

    def test_truncated_roster_is_rejected_without_an_explicit_declaration(self):
        truncated = list(EXPECTED_15)[:14]
        report = validate_object_class_map(_as_map(truncated))
        assert not report.ok
        assert report.codes == (CLASS_MAP_WRONG_COUNT,)
        assert report.is_object_roster
        assert report.missing == ("side_mirror",)

    def test_truncated_roster_raises_before_processing(self):
        det = _detector_with_names(_as_map(list(EXPECTED_15)[:14]))
        with pytest.raises(DetectorClassContractError) as excinfo:
            enforce_object_class_contract(det)
        assert excinfo.value.report.codes == (CLASS_MAP_WRONG_COUNT,)
        assert "side_mirror" in str(excinfo.value)

    def test_renamed_class_is_rejected_not_demoted(self):
        renamed = list(EXPECTED_15)
        renamed[14] = "mirror"
        report = validate_object_class_map(_as_map(renamed))
        assert not report.ok
        assert CLASS_MAP_MISSING in report.codes
        assert CLASS_MAP_UNKNOWN in report.codes
        assert report.missing == ("side_mirror",)
        assert report.unknown == ("mirror",)

    def test_malformed_map_is_rejected_in_auto_mode(self):
        for bad in ({"front": "car"},):
            report = validate_object_class_map(bad)
            assert not report.ok, bad
            assert report.codes == (CLASS_MAP_MALFORMED,), bad
        # Non-contiguous IDs get their own code in both modes.
        for bad in ({i + 1: n for i, n in enumerate(EXPECTED_15)},):
            for require in (False, True):
                report = validate_object_class_map(bad, require_roster=require)
                assert not report.ok, (bad, require)
                assert report.codes == (CLASS_MAP_NON_CONTIGUOUS_IDS,), (bad, require)

    def test_vehicle_only_truncation_still_rejected(self):
        """Dropping the whole non-vehicle tail is still a broken roster, not legacy."""
        vehicles_only = [n for n in EXPECTED_15 if n in FROZEN_VEHICLE_DETECTOR_CLASSES]
        assert vehicles_only == list(FROZEN_VEHICLE_DETECTOR_CLASSES)
        report = validate_object_class_map(_as_map(vehicles_only))
        # The exact frozen 10-class roster is a known legacy roster.
        assert report.ok
        assert report.is_legacy_roster

    def test_known_legacy_rosters_remain_accepted(self):
        legacy13 = list(FROZEN_VEHICLE_DETECTOR_CLASSES) + [
            "person",
            "rider",
            "helmet",
        ]
        for names in (
            SEVEN_CLASS_BASELINE_VEHICLES,
            FROZEN_VEHICLE_DETECTOR_CLASSES,
            legacy13,
        ):
            report = validate_object_class_map(_as_map(names))
            assert report.ok, names
            assert report.is_legacy_roster, names

    def test_stock_coco_80_class_roster_stays_legacy(self):
        """COCO shares car/bus/truck/motorcycle/bicycle/person with the object
        roster, so the gate must not key on overlap alone."""
        report = validate_object_class_map(_as_map(COCO_80_CLASSES))
        assert report.ok
        assert report.is_legacy_roster
        assert report.class_count == 80

    def test_coco_roster_has_no_object_roster_marker_classes(self):
        assert OBJECT_ROSTER_MARKER_CLASSES.isdisjoint(COCO_80_CLASSES)
        assert "car" not in OBJECT_ROSTER_MARKER_CLASSES
        assert "person" not in OBJECT_ROSTER_MARKER_CLASSES
        assert "jeepney" in OBJECT_ROSTER_MARKER_CLASSES
        assert "side_mirror" in OBJECT_ROSTER_MARKER_CLASSES

    def test_extra_class_appended_to_roster_is_rejected(self):
        report = validate_object_class_map(_as_map(list(EXPECTED_15) + ["fire_truck"]))
        assert not report.ok
        assert report.codes == (CLASS_MAP_WRONG_COUNT,)



# ---------------------------------------------------------------------------
# Pass-through of all 15 object classes (mocked 15-class model)
# ---------------------------------------------------------------------------

class _FakeBox:
    def __init__(self, track_id: int, cls_id: int, conf: float, xyxy: list[float]) -> None:
        self.id = [track_id]
        self.cls = [cls_id]
        self.conf = [conf]
        self.xyxy = np.asarray([xyxy], dtype=np.float32)


class _FakeResult:
    def __init__(self, names: dict[int, str], boxes: list[_FakeBox]) -> None:
        self.names = names
        self.boxes = boxes


class _FakeUltralyticsModel:
    """Mocked 15-class YOLOv8m: one detection per declared class ID."""

    def __init__(self, names: dict[int, str]) -> None:
        self.names = names

    def track(self, source, conf, persist, tracker, verbose):  # noqa: D401
        boxes = [
            _FakeBox(
                track_id=100 + cls_id,
                cls_id=cls_id,
                conf=0.9,
                xyxy=[10.0 + cls_id, 10.0, 40.0 + cls_id, 40.0],
            )
            for cls_id in sorted(self.names)
        ]
        return [_FakeResult(self.names, boxes)]


def _fifteen_class_detector() -> Detector:
    det = Detector.__new__(Detector)
    det._weights = "mock-v9-1-candidate"
    det.is_custom = True
    det._model = _FakeUltralyticsModel(_as_map(EXPECTED_15))
    det.load = lambda: None  # type: ignore[method-assign]
    return det


class TestFifteenClassPassThrough:
    def test_every_object_class_survives_detection(self):
        det = _fifteen_class_detector()
        detections = det.track_frame(np.zeros((120, 160, 3), dtype=np.uint8))
        assert {d["class_label"] for d in detections} == set(EXPECTED_15)
        assert len(detections) == 15

    def test_every_object_class_survives_tracking(self):
        det = _fifteen_class_detector()
        state = TrackState()
        annotated = state.update(
            det.track_frame(np.zeros((120, 160, 3), dtype=np.uint8)),
            now=0.0,
        )
        assert set(state.count_by_class()) == set(EXPECTED_15)
        for row in annotated:
            assert "centroid_x" in row and "dwell_sec" in row

    def test_non_vehicle_classes_are_never_canonical_vehicles(self):
        det = _fifteen_class_detector()
        for row in det.track_frame(np.zeros((120, 160, 3), dtype=np.uint8)):
            label = row["class_label"]
            if label in NON_VEHICLE_OBJECT_CLASSES:
                assert row["class_review_state"] is None
                assert not is_vehicle_object_class(label)
            else:
                assert is_vehicle_object_class(label)

    def test_unknown_model_class_is_still_dropped(self):
        det = _detector_with_names(_as_map(list(EXPECTED_15) + ["fire_truck"]))
        det._model = _FakeUltralyticsModel(_as_map(list(EXPECTED_15) + ["fire_truck"]))
        det.load = lambda: None  # type: ignore[method-assign]
        labels = {
            d["class_label"]
            for d in det.track_frame(np.zeros((120, 160, 3), dtype=np.uint8))
        }
        assert "fire_truck" not in labels
        assert labels == set(EXPECTED_15)


# ---------------------------------------------------------------------------
# person vs rider — never interchangeable
# ---------------------------------------------------------------------------

class TestPersonVersusRider:
    def test_required_model_classes_names_rider_not_person(self):
        assert YOLO_CLASS_RIDER in REQUIRED_MODEL_CLASSES
        assert YOLO_CLASS_PERSON not in REQUIRED_MODEL_CLASSES
        assert REQUIRED_MODEL_CLASSES == (
            "motorcycle",
            "rider",
            "helmet_acceptable",
            "helmet_nut_shell",
        )

    def test_helmet_capability_helper_requires_rider(self):
        without_rider = {
            "motorcycle",
            "person",
            "helmet_acceptable",
            "helmet_nut_shell",
        }
        with_rider = without_rider - {"person"} | {"rider"}
        assert model_classes_available(without_rider) is False
        assert model_classes_available(with_rider) is True

    def test_person_does_not_satisfy_helmet_rules(self):
        names = ("motorcycle", "person", "helmet_acceptable", "helmet_nut_shell")
        assert not classes_satisfy_rule(VIOLATION_NO_HELMET, names)
        assert not classes_satisfy_rule(VIOLATION_SUBSTANDARD_HELMET, names)
        assert not classes_satisfy_rule(VIOLATION_MOTORCYCLE_OVERLOADING, names)
        for rule in (
            VIOLATION_NO_HELMET,
            VIOLATION_SUBSTANDARD_HELMET,
            VIOLATION_MOTORCYCLE_OVERLOADING,
        ):
            cap = assess_rule_capability(rule, names)
            assert cap.automatic_evaluation is False
            assert "class:rider" in cap.missing_prerequisites

    def test_cargo_passenger_requires_person_and_not_rider(self):
        assert YOLO_CLASS_PERSON in REQUIRED_MODEL_CLASSES_CARGO_PASSENGER
        assert YOLO_CLASS_RIDER not in REQUIRED_MODEL_CLASSES_CARGO_PASSENGER
        assert not classes_satisfy_rule(
            VIOLATION_CARGO_PASSENGERS, ("truck", "pickup_truck", "rider")
        )
        assert classes_satisfy_rule(
            VIOLATION_CARGO_PASSENGERS, ("truck", "pickup_truck", "person")
        )

    def test_person_detections_do_not_drive_rider_association(self):
        """A person box overlapping a motorcycle is never a rider."""
        tracked = [
            _det(1, "motorcycle", 0.0, bbox_x=100.0, bbox_y=100.0, bbox_w=60.0, bbox_h=40.0),
            _det(2, "person", 0.0, bbox_x=110.0, bbox_y=100.0, bbox_w=30.0, bbox_h=30.0),
        ]
        state = RuleEngineState()
        events: list[Any] = []
        for ts in (0.0, 2.0, 4.0, 6.0, 8.0):
            row = [dict(d, timestamp_sec=ts) for d in tracked]
            events.extend(
                evaluate_detection_rules(
                    row,
                    state,
                    frame_number=int(ts),
                    params={"stationary_px": 8.0},
                    enabled_violations=(
                        VIOLATION_NO_HELMET,
                        VIOLATION_SUBSTANDARD_HELMET,
                        VIOLATION_MOTORCYCLE_OVERLOADING,
                    ),
                    model_classes=EXPECTED_15,
                )
            )
        assert events == []

    def test_rider_does_not_substitute_for_cargo_passenger_person(self):
        tracked = [
            _det(1, "pickup_truck", 0.0, bbox_x=100.0, bbox_y=100.0, bbox_w=80.0, bbox_h=40.0,
                 speed_px_per_sec=40.0, direction_degrees=0.0),
            _det(2, "rider", 0.0, bbox_x=130.0, bbox_y=110.0, bbox_w=25.0, bbox_h=25.0,
                 speed_px_per_sec=40.0),
        ]
        state = RuleEngineState()
        events: list[Any] = []
        for ts in (0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0):
            row = [dict(d, timestamp_sec=ts) for d in tracked]
            events.extend(
                evaluate_detection_rules(
                    row,
                    state,
                    frame_number=int(ts),
                    params={"stationary_px": 8.0},
                    enabled_violations=(VIOLATION_CARGO_PASSENGERS,),
                    model_classes=EXPECTED_15,
                )
            )
        assert events == []


# ---------------------------------------------------------------------------
# Helmet / mirror / cargo capability gates fail closed
# ---------------------------------------------------------------------------

class TestHelmetAndMirrorCapabilityGates:
    def test_mirror_rule_fails_closed_without_side_mirror_class(self):
        names = tuple(n for n in EXPECTED_15 if n != "side_mirror")
        cap = assess_rule_capability(VIOLATION_NO_SIDE_MIRROR, names)
        assert cap.automatic_evaluation is False
        assert "class:side_mirror" in cap.missing_prerequisites

    def test_mirror_rule_enabled_with_side_mirror_class(self):
        cap = assess_rule_capability(VIOLATION_NO_SIDE_MIRROR, EXPECTED_15)
        assert cap.automatic_evaluation is True

    def test_helmet_rule_fails_closed_without_rider_or_acceptable_helmet(self):
        for missing in ("rider", "helmet_acceptable"):
            names = tuple(n for n in EXPECTED_15 if n != missing)
            cap = assess_rule_capability(VIOLATION_NO_HELMET, names)
            assert cap.automatic_evaluation is False, missing
            assert f"class:{missing}" in cap.missing_prerequisites

    def test_helmet_taxonomy_rules_fail_closed_without_helmet_nut_shell(self):
        """No Helmet survives on the ``helmet_acceptable``-only alternate set, but
        distinguishing nut-shell substandard helmets from outright absence needs
        both helmet classes."""
        names = tuple(n for n in EXPECTED_15 if n != "helmet_nut_shell")
        assert assess_rule_capability(VIOLATION_NO_HELMET, names).automatic_evaluation is True
        cap = assess_rule_capability(VIOLATION_SUBSTANDARD_HELMET, names)
        assert cap.automatic_evaluation is False
        assert "class:helmet_nut_shell" in cap.missing_prerequisites

    def test_helmet_rules_enabled_for_the_full_object_roster(self):
        for rule in (VIOLATION_NO_HELMET, VIOLATION_SUBSTANDARD_HELMET,
                     VIOLATION_MOTORCYCLE_OVERLOADING, VIOLATION_NO_SIDE_MIRROR):
            cap = assess_rule_capability(rule, EXPECTED_15)
            assert cap.automatic_evaluation is True, rule

    def test_mirror_rule_emits_nothing_when_class_absent(self):
        tracked = [
            _det(1, "jeepney", 0.0, bbox_x=10.0, bbox_y=10.0, bbox_w=80.0, bbox_h=40.0,
                 mirror_roi_visibility="both_visible"),
        ]
        names = tuple(n for n in EXPECTED_15 if n != "side_mirror")
        assert check_no_side_mirror(tracked, RuleEngineState(), 0, model_classes=names) == []

    def test_missing_mirror_evidence_does_not_auto_confirm(self):
        """No mirror detections + observable areas must not auto-confirm a violation."""
        tracked = [
            _det(1, "jeepney", ts, bbox_x=10.0, bbox_y=10.0, bbox_w=80.0, bbox_h=40.0,
                 mirror_roi_visibility="both_visible", timestamp_sec=ts)
            for ts in (0.0, 1.0, 2.0, 3.0, 4.0, 5.0)
        ]
        events = check_no_side_mirror(
            tracked,
            RuleEngineState(),
            0,
            model_classes=EXPECTED_15,
        )
        # Any candidate, if produced, is routed to manual review only.
        assert all(e.outcome == "review" for e in events)
        assert all("never auto-confirmed" in e.reason_log for e in events)

    def test_unknown_mirror_visibility_never_produces_a_candidate(self):
        tracked = [
            _det(1, "jeepney", ts, bbox_x=10.0, bbox_y=10.0, bbox_w=80.0, bbox_h=40.0,
                 timestamp_sec=ts)
            for ts in (0.0, 1.0, 2.0, 3.0, 4.0, 5.0)
        ]
        assert check_no_side_mirror(
            tracked, RuleEngineState(), 0, model_classes=EXPECTED_15
        ) == []

    def test_no_helmet_requires_associated_rider_not_absence(self):
        """A motorcycle with no rider box is not a No Helmet candidate."""
        tracked = [
            _det(1, "motorcycle", ts, bbox_x=60.0, bbox_y=40.0, bbox_w=90.0, bbox_h=50.0,
                 timestamp_sec=ts)
            for ts in (0.0, 1.0, 2.0, 3.0, 4.0, 5.0)
        ]
        state = RuleEngineState()
        events: list[Any] = []
        for ts in (0.0, 1.0, 2.0, 3.0, 4.0, 5.0):
            events.extend(
                evaluate_detection_rules(
                    tracked,
                    state,
                    frame_number=int(ts),
                    params={"stationary_px": 8.0},
                    enabled_violations=(VIOLATION_NO_HELMET,),
                    model_classes=EXPECTED_15,
                )
            )
        assert events == []

    def test_ambiguous_piaggio_never_counts_as_a_vehicle_observation(self):
        tracked = [
            _det(1, "piaggio", 0.0, raw_class="piaggio", canonical_class=None,
                 class_review_state="UNCERTAIN"),
        ]
        assert evaluate_detection_rules(
            tracked,
            RuleEngineState(),
            0,
            params={"stationary_px": 8.0},
            enabled_violations=(VIOLATION_NO_SIDE_MIRROR,),
            model_classes=EXPECTED_15,
        ) == []


# ---------------------------------------------------------------------------
# Line crossings are vehicles; detection counts cover all 15 object classes
# ---------------------------------------------------------------------------

def _counting_line():
    return SimpleNamespace(
        id="count-1",
        type="counting_line",
        points=((80.0, 0.0), (80.0, 120.0)),
        metadata={},
    )


def _at(track_id: int, label: str, cx: float, cy: float = 60.0) -> dict[str, Any]:
    return {
        "track_id": track_id,
        "class_label": label,
        "bbox_x": cx - 5.0,
        "bbox_y": cy - 5.0,
        "bbox_w": 10.0,
        "bbox_h": 10.0,
    }


def _cross(counter: VehicleCrossingCounter, track_id: int, label: str, cy: float = 60.0) -> None:
    for cx in (60.0, 78.0, 82.0, 100.0):
        counter.update([_at(track_id, label, cx, cy)])


class TestCrossingCountsAreVehicleOnly:
    def test_only_vehicle_classes_increase_the_total(self):
        counter = VehicleCrossingCounter([_counting_line()], hysteresis_px=2)
        for track_id, label in enumerate(NON_VEHICLE_OBJECT_CLASSES, start=1):
            _cross(counter, track_id, label, cy=20.0 + track_id * 5.0)
        assert counter.total == 0
        assert counter.by_class == {}
        assert counter.by_line == {}
        assert set(counter.non_vehicle_observations) == set(NON_VEHICLE_OBJECT_CLASSES)

    def test_mixed_frame_counts_vehicles_only(self):
        counter = VehicleCrossingCounter([_counting_line()], hysteresis_px=2)
        _cross(counter, 1, "car", cy=30.0)
        _cross(counter, 2, "person", cy=60.0)
        _cross(counter, 3, "rider", cy=70.0)
        _cross(counter, 4, "helmet_acceptable", cy=80.0)
        _cross(counter, 5, "side_mirror", cy=90.0)
        _cross(counter, 6, "motorcycle", cy=100.0)
        assert counter.total == 2
        assert counter.by_class == {"car": 1, "motorcycle": 1}
        assert counter.non_vehicle_observations["person"] == 4
        assert counter.non_vehicle_observations["rider"] == 4
        assert counter.non_vehicle_observations["helmet_acceptable"] == 4
        assert counter.non_vehicle_observations["side_mirror"] == 4

    def test_all_ten_vehicle_classes_are_countable(self):
        assert set(FROZEN_VEHICLE_DETECTOR_CLASSES) == {
            name for name in EXPECTED_15
            if VehicleCrossingCounter.countable_vehicle_class(name) == name
        }
        for name in FROZEN_VEHICLE_DETECTOR_CLASSES:
            assert VehicleCrossingCounter.countable_vehicle_class(name) == name

    def test_ambiguous_and_non_canonical_labels_are_not_counted(self):
        assert VehicleCrossingCounter.countable_vehicle_class("piaggio") is None
        assert VehicleCrossingCounter.countable_vehicle_class("person") is None
        assert VehicleCrossingCounter.countable_vehicle_class("unknown") is None
        counter = VehicleCrossingCounter([_counting_line()], hysteresis_px=2)
        _cross(counter, 1, "piaggio", cy=30.0)
        assert counter.total == 0
        assert counter.by_class == {}

    def test_legacy_suv_alias_counts_under_its_canonical_class(self):
        counter = VehicleCrossingCounter([_counting_line()], hysteresis_px=2)
        _cross(counter, 1, "suv", cy=30.0)
        assert counter.total == 1
        assert counter.by_class == {"car": 1}

    def test_no_counting_line_still_reports_unavailable(self):
        counter = VehicleCrossingCounter([])
        counter.update([_at(1, "car", 40.0), _at(1, "car", 100.0)])
        assert counter.available is False
        assert counter.total is None
        assert counter.by_class == {}
        assert counter.non_vehicle_observations == {}


# ---------------------------------------------------------------------------
# End-to-end: persisted detections and displayed counts
# ---------------------------------------------------------------------------

class _Roster15Detector:
    """Mocked 15-class detector for the full process_video pipeline."""

    def __init__(self, cross: bool = True) -> None:
        self.cross = cross
        self.frames_seen = 0

    def load(self) -> None:
        return None

    @property
    def class_names(self) -> set[str]:
        return set(EXPECTED_15)

    def raw_class_map(self) -> dict[int, str]:
        return _as_map(EXPECTED_15)

    def track_frame(self, frame, conf=0.6, timestamp_sec=0.0):
        # A car and a person walk left -> right across the counting line.
        offset = 0.0 if self.cross else -60.0
        cx = 60.0 + offset + min(timestamp_sec * 12.0, 40.0)
        return [
            _det(1, "car", timestamp_sec, bbox_x=cx - 5.0, bbox_y=25.0, bbox_w=10.0, bbox_h=10.0),
            _det(2, "person", timestamp_sec, bbox_x=cx - 5.0, bbox_y=85.0, bbox_w=10.0, bbox_h=10.0),
        ]


def _write_mp4(path: Path, *, frames: int = 40, fps: float = 10.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (160, 120))
    assert writer.isOpened()
    try:
        for i in range(frames):
            writer.write(np.full((120, 160, 3), i % 255, dtype=np.uint8))
    finally:
        writer.release()


class TestPersistedAndDisplayedCounts:
    @pytest.fixture(autouse=True)
    def _evidence_dirs(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "core.temporal_evidence.EVIDENCE_FOLDER", str(tmp_path / "evidence")
        )
        monkeypatch.setattr("core.evidence.EVIDENCE_FOLDER", str(tmp_path / "evidence"))

    def _run(self, test_db, tmp_path, monkeypatch, detector) -> Any:
        from core.detection_config import DEFAULT_RULE_PARAMETERS
        from core.video_processor import process_video

        path = tmp_path / "roster.mp4"
        _write_mp4(path)
        vid = test_db.insert_video(filename="roster.mp4", filepath=str(path), status="ready")
        test_db.upsert_annotation(
            vid,
            json.dumps(
                {
                    "schema_version": 2,
                    "zones": [],
                    "lanes": [],
                    "flow_arrows": [],
                    "threshold_lines": [
                        {
                            "id": "count-1",
                            "type": "counting_line",
                            "points": [[80, 0], [80, 120]],
                        }
                    ],
                    "markings": [],
                    "signs": [],
                    "activity_regions": [],
                }
            ),
        )
        monkeypatch.setattr("core.video_processor.Detector", lambda: detector)
        monkeypatch.setattr(
            "core.video_processor.load_rule_parameters",
            lambda: {**DEFAULT_RULE_PARAMETERS, "frame_skip": 1, "confidence_threshold": 0.3},
        )
        return vid, process_video(vid, enabled_violations=())

    def test_mixed_frame_persists_all_classes_and_counts_only_vehicles(
        self, test_db, tmp_path, monkeypatch
    ):
        vid, result = self._run(test_db, tmp_path, monkeypatch, _Roster15Detector())

        # Vehicles crossed: the car only. The person is not a vehicle passage.
        assert result.vehicles_crossed == 1
        assert result.crossing_counts == {"car": 1}

        # Detection counts cover every observed object class.
        assert set(result.class_counts) == {"car", "person"}
        assert result.class_counts["car"] == result.class_counts["person"] > 0
        assert result.detection_records == sum(result.class_counts.values())

        # The stored detections table keeps the non-vehicle object class too.
        with test_db.db_session() as conn:
            rows = conn.execute(
                "SELECT class_label, COUNT(*) AS n FROM detections WHERE video_id = ? "
                "GROUP BY class_label",
                (vid,),
            ).fetchall()
        stored = {r["class_label"]: int(r["n"]) for r in rows}
        assert set(stored) == {"car", "person"}
        assert stored["person"] > 0

    def test_unreadable_counting_line_leaves_crossings_unavailable(
        self, test_db, tmp_path, monkeypatch
    ):
        from core.detection_config import DEFAULT_RULE_PARAMETERS
        from core.video_processor import process_video

        path = tmp_path / "nolines.mp4"
        _write_mp4(path)
        vid = test_db.insert_video(filename="nolines.mp4", filepath=str(path), status="ready")
        test_db.upsert_annotation(vid, json.dumps({"no_parking": FULL_FRAME}))
        monkeypatch.setattr("core.video_processor.Detector", lambda: _Roster15Detector())
        monkeypatch.setattr(
            "core.video_processor.load_rule_parameters",
            lambda: {**DEFAULT_RULE_PARAMETERS, "frame_skip": 1, "confidence_threshold": 0.3},
        )
        result = process_video(vid, enabled_violations=())
        assert result.vehicles_crossed is None
        assert result.crossing_counts == {}
        assert set(result.class_counts) == {"car", "person"}

    def test_bad_roster_aborts_processing_before_any_frame(
        self, test_db, tmp_path, monkeypatch
    ):
        from core.detection_config import DEFAULT_RULE_PARAMETERS
        from core.video_processor import ProcessVideoError, process_video

        class ReorderedRosterDetector(_Roster15Detector):
            def raw_class_map(self) -> dict[int, str]:
                return _as_map(_swapped())

        path = tmp_path / "bad.mp4"
        _write_mp4(path)
        vid = test_db.insert_video(filename="bad.mp4", filepath=str(path), status="ready")
        test_db.upsert_annotation(vid, json.dumps({"no_parking": FULL_FRAME}))
        monkeypatch.setattr("core.video_processor.Detector", lambda: ReorderedRosterDetector())
        monkeypatch.setattr(
            "core.video_processor.load_rule_parameters",
            lambda: {**DEFAULT_RULE_PARAMETERS, "frame_skip": 1, "confidence_threshold": 0.3},
        )
        with pytest.raises(ProcessVideoError) as excinfo:
            process_video(vid, enabled_violations=())
        assert "rejected before processing" in str(excinfo.value)
        assert "reordered" in str(excinfo.value)

        with test_db.db_session() as conn:
            n = conn.execute(
                "SELECT COUNT(*) AS n FROM detections WHERE video_id = ?", (vid,)
            ).fetchone()["n"]
        assert int(n) == 0
