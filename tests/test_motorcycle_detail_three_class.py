"""Three-class motorcycle detail submodel: contract, attribution, and boundaries.

The detail checkpoint is always mocked; no weights are designated, loaded for
inference, copied, or promoted. The one real-file check below only reads the
main detector's class-map metadata (and is skipped when the file is absent).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from core.motorcycle_detail import (
    DETAIL_SELECTOR_VERSION,
    MainDetectorContext,
    MotorcycleDetailCollector,
)
from core.motorcycle_detail_contract import (
    DETAIL_MODEL_CLASSES,
    PILOT_DETAIL_CLASS_ORDER,
    check_detail_class_map,
    detail_architecture_hint,
)
from core.motorcycle_detail_scan import (
    DetailCheckpoint,
    DetailScanError,
    MotorcycleDetailScanner,
    UltralyticsCropPredictor,
    associate_scan_detections,
    load_detail_checkpoint,
    to_pipeline_detection,
)

REPO = Path(__file__).resolve().parent.parent
DETAIL_3 = ("helmet_nut_shell", "helmet_acceptable", "side_mirror")
PILOT_3 = ("side_mirror", "helmet_nut_shell", "helmet_acceptable")
MAIN_15 = (
    "car", "van", "jeepney", "tricycle", "autorickshaw", "bus", "truck",
    "pickup_truck", "motorcycle", "bicycle", "person", "rider",
    "helmet_acceptable", "helmet_nut_shell", "side_mirror",
)

FRAME_W, FRAME_H = 640, 480
# Source-space geometry shared by the collector and scan tests.
BIKE = (130.0, 110.0, 190.0, 190.0)          # target motorcycle (track 7)
RIDER = (135.0, 60.0, 185.0, 150.0)          # its rider (track 11)
OVERLAP_BIKE = (140.0, 112.0, 200.0, 192.0)  # different bike, IoU ~0.68 with BIKE
ADJACENT_BIKE = (180.0, 110.0, 240.0, 190.0)  # different bike, IoU ~0.09 with BIKE
CROP = {"x": 100, "y": 40, "w": 120, "h": 170}
HELMET_SRC = (145.0, 58.0, 175.0, 85.0)      # on RIDER's head
LEFT_MIRROR_SRC = (132.0, 112.0, 146.0, 122.0)
RIGHT_MIRROR_SRC = (176.0, 112.0, 188.0, 122.0)  # also inside ADJACENT_BIKE's mount band


def _xywh(box):
    return {"x": box[0], "y": box[1], "w": box[2] - box[0], "h": box[3] - box[1]}


def _det(track_id, label, box, conf=0.9):
    return {
        "track_id": track_id,
        "class_label": label,
        "confidence": conf,
        "bbox_x": box[0],
        "bbox_y": box[1],
        "bbox_w": box[2] - box[0],
        "bbox_h": box[3] - box[1],
    }


def _to_crop(box):
    return (box[0] - CROP["x"], box[1] - CROP["y"], box[2] - CROP["x"], box[3] - CROP["y"])


def _model(names, **extra):
    extra.setdefault("task", "detect")
    return SimpleNamespace(names=names, **extra)


def _map(names):
    return {i: n for i, n in enumerate(names)}


def _ok_checkpoint():
    return DetailCheckpoint(
        path="mock", class_names=DETAIL_3, ok=True, identity="md3c:mock", architecture="yolov8n"
    )


@pytest.fixture(autouse=True)
def _clear_model_cache():
    from core.motorcycle_detail_scan import clear_model_cache

    clear_model_cache()
    yield
    clear_model_cache()


# ---------------------------------------------------------------------------
# The main detector contract is untouched
# ---------------------------------------------------------------------------


class TestMainDetectorContractUnchanged:
    def test_main_roster_is_the_ordered_fifteen_class_schema(self):
        from core.detection_config import OBJECT_DETECTOR_CLASS_COUNT, OBJECT_DETECTOR_CLASSES

        schema = json.loads((REPO / "config" / "training" / "class_schema.json").read_text("utf-8"))
        assert tuple(schema["object_classes"]) == MAIN_15
        assert OBJECT_DETECTOR_CLASSES == MAIN_15
        assert OBJECT_DETECTOR_CLASS_COUNT == 15 == schema["counts"]["object_classes"]
        assert schema["model_family"] == "YOLOv8m" and schema["tracker"] == "ByteTrack"

    def test_detail_classes_are_attribute_labels_of_the_main_roster(self):
        from core.detection_config import OBJECT_DETECTOR_CLASSES

        assert DETAIL_MODEL_CLASSES == DETAIL_3
        assert set(DETAIL_MODEL_CLASSES) <= set(OBJECT_DETECTOR_CLASSES)
        assert "rider" not in DETAIL_MODEL_CLASSES and "motorcycle" not in DETAIL_MODEL_CLASSES

    def test_main_checkpoint_gate_still_rejects_a_detail_checkpoint(self):
        from core.detector import DetectorClassContractError, enforce_object_class_contract

        detail = SimpleNamespace(raw_class_map=lambda: _map(DETAIL_3))
        with pytest.raises(DetectorClassContractError):
            enforce_object_class_contract(detail)
        main = SimpleNamespace(raw_class_map=lambda: _map(MAIN_15))
        assert enforce_object_class_contract(main).ok

    def test_selected_main_checkpoint_metadata_is_fifteen_class(self):
        from core.detector import resolve_weights_path

        path, is_custom = resolve_weights_path()
        if not is_custom or Path(path).name != "tavidm_yolov8m.pt":
            pytest.skip("15-class main checkpoint not present in this checkout")
        from ultralytics import YOLO

        model = YOLO(path)
        assert tuple(model.names[i] for i in range(len(model.names))) == MAIN_15

    def test_detail_modules_never_reach_the_rule_engine_or_tracker(self):
        for module in ("core/violation_engine.py", "core/detector.py", "core/tracker.py"):
            assert "motorcycle_detail" not in (REPO / module).read_text("utf-8")
        for module in (
            "core/motorcycle_detail.py",
            "core/motorcycle_detail_scan.py",
            "core/motorcycle_detail_contract.py",
        ):
            source = (REPO / module).read_text("utf-8")
            assert "evaluate_detection_rules" not in source
            assert "violation_engine" not in source.replace("``core.violation_engine``", "")
            assert "insert_violation" not in source and "insert_review_queue" not in source


# ---------------------------------------------------------------------------
# Detail class-map contract
# ---------------------------------------------------------------------------


class TestDetailClassMapContract:
    @pytest.mark.parametrize(
        "raw",
        [
            _map(DETAIL_3),
            {str(i): n for i, n in enumerate(DETAIL_3)},
            list(DETAIL_3),
        ],
        ids=["int-keys", "string-keys", "ordered-list"],
    )
    def test_future_three_class_map_passes(self, raw):
        report = check_detail_class_map(raw)
        assert report.ok, report.reason
        assert report.class_names == DETAIL_3
        assert report.class_map() == {"0": "helmet_nut_shell", "1": "helmet_acceptable", "2": "side_mirror"}

    def test_pilot_order_is_rejected_and_never_relabeled(self):
        assert PILOT_DETAIL_CLASS_ORDER == PILOT_3
        report = check_detail_class_map(_map(PILOT_3))
        assert not report.ok
        assert report.code == "reordered_names"
        assert "pilot" in report.reason and "never relabeled" in report.reason
        # The pilot order is reported as-is, not silently remapped.
        assert report.class_names == PILOT_3

    @pytest.mark.parametrize(
        ("raw", "code"),
        [
            (None, "missing"),
            ({}, "malformed"),
            ("helmet_nut_shell", "malformed"),
            ({0: "helmet_nut_shell", 1: "helmet_acceptable", 3: "side_mirror"}, "non_contiguous_class_ids"),
            ({-1: "helmet_nut_shell", 0: "helmet_acceptable", 1: "side_mirror"}, "malformed"),
            ({0: "helmet_nut_shell", 1: 7, 2: "side_mirror"}, "malformed"),
            ({0: "helmet_nut_shell", 1: "", 2: "side_mirror"}, "malformed"),
            (_map(("helmet_nut_shell", "helmet_nut_shell", "side_mirror")), "duplicate_names"),
            (_map(("helmet_nutshell", "helmet_acceptable", "side_mirror")), "renamed_names"),
            (_map(("Helmet_Nut_Shell", "helmet_acceptable", "side_mirror")), "renamed_names"),
            (_map(("helmet_nut_shell", "helmet_acceptable")), "wrong_class_count"),
            (_map(DETAIL_3 + ("rider",)), "wrong_class_count"),
            (_map(MAIN_15), "wrong_class_count"),
            (_map(("side_mirror", "helmet_acceptable", "helmet_nut_shell")), "reordered_names"),
        ],
    )
    def test_malformed_and_mismatched_maps_fail_with_a_reason(self, raw, code):
        report = check_detail_class_map(raw)
        assert not report.ok
        assert report.code == code
        assert report.reason

    def test_checkpoint_identity_is_a_detail_model_not_the_main_detector(self, tmp_path):
        weights = tmp_path / "detail.pt"
        weights.write_bytes(b"three-class")
        model = _model(
            _map(DETAIL_3),
            task="detect",
            ckpt={"train_args": {"model": "D:\\runs\\base\\yolov8n.pt"}},
        )
        cp = load_detail_checkpoint(str(weights), model_loader=lambda path: model)
        assert cp.ok, cp.reason
        assert cp.identity.startswith("md3c:detail.pt:")
        assert cp.architecture == "yolov8n"
        described = cp.describe()
        assert described["model_kind"] == "motorcycle_detail_3class"
        assert described["class_map"] == {"0": "helmet_nut_shell", "1": "helmet_acceptable", "2": "side_mirror"}
        assert "YOLOv8m" not in json.dumps(described)

    def test_architecture_hint_never_exposes_a_path_and_tolerates_missing_metadata(self):
        assert detail_architecture_hint(_model({}, ckpt={"train_args": {"model": "/x/y/yolov8s.pt"}})) == "yolov8s"
        assert detail_architecture_hint(_model({})) is None
        assert detail_architecture_hint(_model({}, ckpt={"train_args": {"model": "/secret/custom.pt"}})) is None

    def test_non_detection_checkpoint_is_rejected(self, tmp_path):
        weights = tmp_path / "seg.pt"
        weights.write_bytes(b"x")
        cp = load_detail_checkpoint(
            str(weights), model_loader=lambda path: _model(_map(DETAIL_3), task="segment")
        )
        assert not cp.ok and cp.reason == "detail_checkpoint_wrong_task:segment"

    def test_pilot_checkpoint_fails_with_a_visible_reason(self, tmp_path):
        weights = tmp_path / "pilot_best.pt"
        weights.write_bytes(b"pilot")
        cp = load_detail_checkpoint(str(weights), model_loader=lambda path: _model(_map(PILOT_3)))
        assert not cp.ok
        assert cp.reason.startswith("detail_class_map_rejected:reordered_names")
        assert "pilot" in cp.reason


class TestCropPredictorUsesTheValidatedMap:
    @staticmethod
    def _result(names, cls_ids):
        boxes = [
            SimpleNamespace(
                cls=np.array([k]), conf=np.array([0.8]), xyxy=np.array([[1.0, 2.0, 11.0, 12.0]])
            )
            for k in cls_ids
        ]
        model = SimpleNamespace(
            predict=lambda **kwargs: [SimpleNamespace(names=names, boxes=boxes)]
        )
        return UltralyticsCropPredictor(model, DETAIL_3, conf=0.25)

    def test_class_ids_map_through_the_validated_contract(self):
        out = self._result(_map(DETAIL_3), [2, 0]).predict(np.zeros((8, 8, 3), np.uint8))
        assert [d["class_label"] for d in out] == ["side_mirror", "helmet_nut_shell"]

    def test_runtime_class_map_mismatch_fails_the_candidate(self):
        with pytest.raises(DetailScanError, match="class_map_mismatch"):
            self._result(_map(PILOT_3), [0]).predict(np.zeros((8, 8, 3), np.uint8))

    def test_out_of_range_class_id_fails_the_candidate(self):
        with pytest.raises(DetailScanError, match="out_of_range"):
            self._result(_map(DETAIL_3), [3]).predict(np.zeros((8, 8, 3), np.uint8))


# ---------------------------------------------------------------------------
# Collector: main-detector rider/motorcycle context saved per selected frame
# ---------------------------------------------------------------------------


def _collect(detections_per_frame, *, target_track=7):
    frame = np.random.default_rng(3).integers(0, 255, (FRAME_H, FRAME_W, 3), dtype=np.uint8)
    written = []

    def writer(frame_arg, crop, *, run_key, occurrence_key, frame_index):
        written.append(occurrence_key)
        return {"scene_path": f"s_{occurrence_key}_{frame_index}", "crop_path": f"c_{occurrence_key}_{frame_index}", "size_bytes": 10}

    collector = MotorcycleDetailCollector(
        video_id=1, run_key="run_1", frame_w=FRAME_W, frame_h=FRAME_H, writer=writer
    )
    for i, dets in enumerate(detections_per_frame):
        collector.observe(frame, dets, frame_number=i, timestamp_sec=i * 1.0)
    collector.wrap_up()
    for payload in collector.candidates():
        if payload["track_id"] == target_track:
            return json.loads(payload["frames_json"])
    raise AssertionError("no candidate for the target track")


class TestCollectorMainContext:
    def test_main_rider_is_saved_with_its_association_state(self):
        frames = _collect([[_det(7, "motorcycle", BIKE), _det(11, "rider", RIDER)]])
        frame = frames[0]
        assert frame["rider_bbox"] == _xywh(RIDER)
        context = frame["main_context"]
        assert context["source"] == "main_detector"
        assert context["rider"] == {"state": "associated", "track_id": 11, "reasons": []}
        assert context["motorcycles"] == [] and context["riders"] == []

    def test_person_is_never_saved_as_a_rider_or_context_rider(self):
        frames = _collect([[_det(7, "motorcycle", BIKE), _det(21, "person", RIDER)]])
        frame = frames[0]
        assert frame["rider_bbox"] is None
        assert frame["main_context"]["rider"]["state"] == "unassociated"
        assert frame["main_context"]["riders"] == []

    def test_overlapping_motorcycle_is_saved_as_a_known_other_bike(self):
        frames = _collect(
            [[_det(7, "motorcycle", BIKE), _det(8, "motorcycle", OVERLAP_BIKE), _det(11, "rider", RIDER)]]
        )
        others = frames[0]["main_context"]["motorcycles"]
        assert [o["track_id"] for o in others] == [8]
        assert others[0]["bbox"] == _xywh(OVERLAP_BIKE)

    def test_context_is_bounded_and_marks_truncation(self):
        crowd = [_det(100 + k, "motorcycle", (120.0 + k, 100.0, 170.0 + k, 180.0)) for k in range(12)]
        frames = _collect([[_det(7, "motorcycle", BIKE), *crowd]])
        context = frames[0]["main_context"]
        assert len(context["motorcycles"]) == 8
        assert context["truncated"] is True


# ---------------------------------------------------------------------------
# Association with main-detector context (three-class scan output only)
# ---------------------------------------------------------------------------


_MISSING = object()


def _ctx(frame_extra=None, *, rider_state="associated", motorcycles=(), riders=(),
         truncated=False, truncated_lists=None):
    main_context = {
        "version": 1,
        "source": "main_detector",
        "target_track_id": 7,
        "rider": {"state": rider_state, "track_id": 11 if rider_state == "associated" else None, "reasons": []},
        "motorcycles": [{"track_id": 8 + i, "confidence": 0.9, "bbox": _xywh(b)} for i, b in enumerate(motorcycles)],
        "riders": [{"track_id": 30 + i, "confidence": 0.9, "bbox": _xywh(b)} for i, b in enumerate(riders)],
    }
    if truncated is not _MISSING:
        main_context["truncated"] = truncated
    if truncated_lists is not None:
        main_context["truncated_lists"] = truncated_lists
    frame = {
        "detection_bbox": _xywh(BIKE),
        "rider_bbox": _xywh(RIDER) if rider_state == "associated" else None,
        "main_context": main_context,
    }
    frame.update(frame_extra or {})
    return MainDetectorContext.from_frame(frame)


def _assoc(detections, context):
    return associate_scan_detections(
        [to_pipeline_detection(label, 0.8, box) for label, box in detections],
        motorcycle_box=BIKE,
        frame_w=FRAME_W,
        frame_h=FRAME_H,
        main_context=context,
    )


class TestMainContextAssociation:
    def test_helmet_attaches_to_the_main_detector_rider(self):
        result = _assoc([("helmet_acceptable", HELMET_SRC)], _ctx())
        assert result.rider.state == "associated"
        assert result.rider.source == "main_detector"
        assert result.rider.rider_track_id == 11
        assert result.helmet.state == "acceptable"
        stored = result.as_dict()
        assert stored["context"]["rider_state"] == "associated"
        assert stored["motorcycle_box"] == list(BIKE)

    def test_scan_rider_or_person_output_is_ignored(self):
        # Even if a model emitted them, rider attribution comes only from the
        # main detector, and a main-detector "unassociated" stays unknown.
        result = _assoc(
            [("rider", RIDER), ("person", RIDER), ("helmet_nut_shell", HELMET_SRC)],
            _ctx(rider_state="unassociated"),
        )
        assert result.rider.state == "unassociated"
        assert result.helmet.state == "unknown"
        assert "helmet:rider_not_associated" in result.uncertainty

    def test_ambiguous_main_rider_keeps_the_helmet_unknown(self):
        result = _assoc([("helmet_acceptable", HELMET_SRC)], _ctx(rider_state="ambiguous"))
        assert result.rider.state == "ambiguous"
        assert result.helmet.state == "unknown"
        assert "helmet:rider_ambiguous" in result.uncertainty

    def test_helmet_shared_with_another_rider_is_ambiguous(self):
        pillion = (140.0, 55.0, 180.0, 140.0)
        result = _assoc([("helmet_acceptable", HELMET_SRC)], _ctx(riders=[pillion]))
        assert result.helmet.state == "ambiguous"
        assert result.helmet.observations == ()

    def test_heavily_overlapping_different_bike_keeps_mirrors_ambiguous(self):
        detections = [("side_mirror", LEFT_MIRROR_SRC)]
        result = _assoc(detections, _ctx(motorcycles=[OVERLAP_BIKE]))
        assert result.mirror.state == "ambiguous"
        assert "mirror:overlapping_motorcycles_mounting_area_unresolved" in result.uncertainty
        # Spatial-only matching would have mistaken the same box for the
        # target's own re-detection and attributed the mirror.
        spatial = associate_scan_detections(
            [to_pipeline_detection("side_mirror", 0.8, LEFT_MIRROR_SRC),
             to_pipeline_detection("motorcycle", 0.8, OVERLAP_BIKE)],
            motorcycle_box=BIKE, frame_w=FRAME_W, frame_h=FRAME_H,
        )
        assert spatial.mirror.state == "one_left"

    def test_mirror_in_a_neighbours_mounting_area_is_not_attributed(self):
        only_shared = _assoc([("side_mirror", RIGHT_MIRROR_SRC)], _ctx(motorcycles=[ADJACENT_BIKE]))
        assert only_shared.mirror.state == "ambiguous"
        assert "mirror:mirror_in_shared_mounting_area_unattributed" in only_shared.uncertainty
        mixed = _assoc(
            [("side_mirror", LEFT_MIRROR_SRC), ("side_mirror", RIGHT_MIRROR_SRC)],
            _ctx(motorcycles=[ADJACENT_BIKE]),
        )
        assert mixed.mirror.state == "one_left"
        assert [o.side for o in mixed.mirror.observations] == ["left"]
        assert "mirror:mirror_in_shared_mounting_area_unattributed" in mixed.uncertainty

    def test_missing_detections_stay_unknown(self):
        result = _assoc([], _ctx())
        assert result.helmet.state == "unknown"
        assert result.mirror.state == "none_visible"
        assert "helmet:no_observation_does_not_prove_absence" in result.uncertainty
        assert "mirror:absence_not_proven_unknown" in result.uncertainty


class TestOldRowFallback:
    def test_row_without_context_or_rider_is_unknown_not_guessed(self):
        context = MainDetectorContext.from_frame({"detection_bbox": _xywh(BIKE), "rider_bbox": None})
        assert context.recorded is False
        result = _assoc([("helmet_acceptable", HELMET_SRC), ("side_mirror", LEFT_MIRROR_SRC)], context)
        assert result.rider.state == "unassociated"
        assert "rider:main_detector_rider_state_not_recorded" in result.uncertainty
        assert result.helmet.state == "unknown"
        assert "context:nearby_motorcycles_not_recorded_for_this_row" in result.uncertainty

    def test_row_with_a_stored_rider_box_uses_it_but_cannot_attribute(self):
        # Nearby riders/motorcycles were never recorded for older rows, so a
        # helmet or mirror could belong to an unseen neighbour: not definite.
        context = MainDetectorContext.from_frame({"detection_bbox": _xywh(BIKE), "rider_bbox": _xywh(RIDER)})
        result = _assoc([("helmet_nut_shell", HELMET_SRC), ("side_mirror", LEFT_MIRROR_SRC)], context)
        assert result.rider.state == "associated" and result.rider.source == "legacy_row"
        assert result.helmet.state == "ambiguous"
        assert result.mirror.state == "ambiguous"
        assert "context:nearby_motorcycles_not_recorded_for_this_row" in result.uncertainty
        assert "context:nearby_riders_not_recorded_for_this_row" in result.uncertainty

    @pytest.mark.parametrize(
        "main_context",
        ["garbage", {"rider": "x", "motorcycles": "nope", "riders": None}, {"rider": {"state": "maybe"}}],
    )
    def test_malformed_context_is_not_trusted(self, main_context):
        context = MainDetectorContext.from_frame(
            {"detection_bbox": _xywh(BIKE), "rider_bbox": _xywh(RIDER), "main_context": main_context}
        )
        assert context.recorded is False
        assert context.rider_association().state in ("associated", "unassociated")
        if isinstance(main_context, dict):
            assert context.rider_association().state == "unassociated"


# ---------------------------------------------------------------------------
# Scanner against an isolated SQLite database
# ---------------------------------------------------------------------------


def _resolve(stored_path):
    import config

    if not stored_path:
        return None
    candidate = Path(str(stored_path))
    root = Path(config.EVIDENCE_FOLDER).resolve()
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    if resolved == root or root not in resolved.parents:
        return None
    return resolved if resolved.is_file() else None


@pytest.fixture
def evidence_root(tmp_path, monkeypatch):
    import config

    root = tmp_path / "evidence"
    monkeypatch.setattr("core.evidence.EVIDENCE_FOLDER", str(root))
    monkeypatch.setattr("core.motorcycle_detail_scan.resolve_detail_evidence_path", _resolve)
    monkeypatch.setattr(config, "EVIDENCE_FOLDER", str(root))
    return root


@pytest.fixture
def video_id(test_db):
    return test_db.insert_video("three_class.mp4", "/tmp/three_class.mp4", status="processed")


def _seed(test_db, video_id, *, occurrence_key="t7g1", frames=(1,), crop=CROP,
          with_context=True, motorcycles=(), rider=True, context_extra=None,
          frame_context_extra=None):
    import config

    root = Path(config.EVIDENCE_FOLDER) / "detail" / "run_1" / occurrence_key
    root.mkdir(parents=True, exist_ok=True)
    entries = []
    for index in frames:
        crop_path = root / f"crop_f{index}.jpg"
        assert cv2.imwrite(str(crop_path), np.full((crop["h"], crop["w"], 3), 90, dtype=np.uint8))
        entry = {
            "index": index,
            "frame_number": index * 10,
            "timestamp_sec": float(index),
            "scene_path": None,
            "crop_path": str(crop_path),
            "crop": dict(crop),
            "scan_scale_x": 1.0,
            "scan_scale_y": 1.0,
            "detection_bbox": _xywh(BIKE),
            "rider_bbox": _xywh(RIDER) if rider else None,
            "score": {"total": 0.7, "components": {}, "reasons": []},
            "size_bytes": 100,
            "overlay_path": None,
        }
        if with_context:
            entry["main_context"] = {
                "version": 1,
                "source": "main_detector",
                "target_track_id": 7,
                "rider": {"state": "associated" if rider else "unassociated",
                          "track_id": 11 if rider else None, "reasons": []},
                "motorcycles": [{"track_id": 8, "confidence": 0.9, "bbox": _xywh(b)} for b in motorcycles],
                "riders": [],
                "truncated": False,
            }
            entry["main_context"].update(context_extra or {})
            entry["main_context"].update((frame_context_extra or {}).get(index, {}))
        entries.append(entry)
    return test_db.upsert_motorcycle_detail_candidate(
        video_id=video_id,
        run_key="run_1",
        track_id=7,
        occurrence_index=1,
        occurrence_key=occurrence_key,
        selector_version=DETAIL_SELECTOR_VERSION,
        processing_run_id=1,
        frame_number=frames[0] * 10,
        timestamp_sec=1.0,
        frame_score=0.7,
        frame_count=len(entries),
        frames_json=json.dumps(entries),
        source_width=FRAME_W,
        source_height=FRAME_H,
    )


def _scanner(test_db, predictor, *, slot=None, loader=None, factory_calls=None, **kwargs):
    from core.gpu_inference_slot import GpuInferenceSlot

    def factory(cp):
        if factory_calls is not None:
            factory_calls.append(cp)
        return predictor

    return MotorcycleDetailScanner(
        adapter=test_db,
        checkpoint_loader=loader or _ok_checkpoint,
        checkpoint_fingerprint=lambda: "fp",
        predictor_factory=factory,
        gpu_slot=slot or GpuInferenceSlot(),
        gpu_wait_sec=0.0,
        **kwargs,
    )


def _three_class_predictor(calls=None, detections=None):
    detections = detections if detections is not None else [
        ("helmet_acceptable", HELMET_SRC),
        ("side_mirror", LEFT_MIRROR_SRC),
    ]

    def predictor(image):
        if calls is not None:
            calls.append(image.shape)
        return [
            {"class_label": label, "confidence": 0.8, "bbox": _to_crop(box)}
            for label, box in detections
        ]

    return predictor


def _protected_counts(test_db):
    tables = (
        "violations", "review_queue", "case_policy_records",
        "case_action_events", "plate_verifications", "recurrence_reviews",
    )
    with test_db.db_session() as conn:
        return {t: conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"] for t in tables}


class TestScannerThreeClass:
    def test_successful_scan_uses_main_context_and_maps_boxes(self, test_db, video_id, evidence_root):
        candidate_id = _seed(test_db, video_id)
        report = _scanner(test_db, _three_class_predictor()).run_once()
        assert report["ready"] == 1
        row = test_db.get_motorcycle_detail_candidate(candidate_id)
        assert row["scan_state"] == "ready"
        assert row["scan_model"] == "md3c:mock"
        assert json.loads(row["scan_class_map_json"]) == list(DETAIL_3)
        observations = json.loads(row["observations_json"])
        assert observations["scan"]["model_kind"] == "motorcycle_detail_3class"
        assert observations["scan"]["attribution_source"] == "main_detector"
        frame = observations["frames"][0]
        helmet = next(d for d in frame["detections"] if d["class_label"] == "helmet_acceptable")
        assert helmet["source_bbox"] == [pytest.approx(v) for v in HELMET_SRC]
        association = json.loads(row["association_json"])
        assert association["rider"]["source"] == "main_detector"
        assert association["helmet"]["state"] == "acceptable"
        assert association["mirror"]["state"] == "one_left"
        assert association["context"]["recorded"] is True
        assert Path(frame["overlay_path"]).is_file()

    def test_overlapping_bike_context_survives_the_scan_as_ambiguity(self, test_db, video_id, evidence_root):
        candidate_id = _seed(test_db, video_id, motorcycles=[OVERLAP_BIKE])
        _scanner(test_db, _three_class_predictor()).run_once()
        association = json.loads(test_db.get_motorcycle_detail_candidate(candidate_id)["association_json"])
        assert association["mirror"]["state"] == "ambiguous"
        assert association["context"]["nearby_motorcycles"][0]["track_id"] == 8

    def test_edge_crop_maps_and_clamps_into_the_source_frame(self, test_db, video_id, evidence_root):
        edge = {"x": 0, "y": 0, "w": 220, "h": 210}
        candidate_id = _seed(test_db, video_id, crop=edge)

        def predictor(image):
            # A helmet box spilling past the crop's top-left edge.
            return [{"class_label": "helmet_acceptable", "confidence": 0.7,
                     "bbox": (-20.0, -10.0, 175.0, 85.0)},
                    {"class_label": "helmet_nut_shell", "confidence": 0.6,
                     "bbox": HELMET_SRC}]

        _scanner(test_db, predictor).run_once()
        observations = json.loads(test_db.get_motorcycle_detail_candidate(candidate_id)["observations_json"])
        frame = observations["frames"][0]
        spill = frame["detections"][0]["source_bbox"]
        assert spill[0] == 0.0 and spill[1] == 0.0
        assert frame["detections"][1]["source_bbox"] == [pytest.approx(v) for v in HELMET_SRC]
        assert "crop_clamped_at_frame_edge" in frame["uncertainty"]
        assert frame["association"]["helmet"]["state"] == "nut_shell"

    def test_old_row_without_context_is_scanned_with_explicit_uncertainty(self, test_db, video_id, evidence_root):
        candidate_id = _seed(test_db, video_id, with_context=False, rider=False)
        report = _scanner(test_db, _three_class_predictor()).run_once()
        assert report["ready"] == 1
        row = test_db.get_motorcycle_detail_candidate(candidate_id)
        association = json.loads(row["association_json"])
        assert association["rider"]["state"] == "unassociated"
        assert association["helmet"]["state"] == "unknown"
        assert "context:nearby_motorcycles_not_recorded_for_this_row" in json.loads(row["uncertainty_json"])

    @pytest.mark.parametrize(
        ("names", "expected"),
        [
            (_map(PILOT_3), "reordered_names"),
            (_map(MAIN_15), "wrong_class_count"),
            ({0: "helmet_nut_shell", 2: "side_mirror"}, "non_contiguous_class_ids"),
            (None, "missing"),
        ],
        ids=["pilot-order", "main-15-class", "non-contiguous", "no-class-map"],
    )
    def test_checkpoint_gate_failure_scans_zero_crops(
        self, test_db, video_id, evidence_root, tmp_path, names, expected
    ):
        candidate_id = _seed(test_db, video_id)
        weights = tmp_path / "designated.pt"
        weights.write_bytes(b"weights")
        calls, factory_calls = [], []
        scanner = _scanner(
            test_db,
            _three_class_predictor(calls),
            loader=lambda: load_detail_checkpoint(str(weights), model_loader=lambda p: _model(names)),
            factory_calls=factory_calls,
        )
        report = scanner.run_once()
        assert report["skipped"] == "detail_checkpoint_unavailable"
        assert expected in report["gate_reason"]
        assert calls == [] and factory_calls == []
        row = test_db.get_motorcycle_detail_candidate(candidate_id)
        assert row["scan_state"] == "queued"
        assert row["scan_attempts"] == 0
        assert row["scan_error"].startswith("detail_class_map_rejected:")
        assert row["observations_json"] == "{}"

    def test_one_reservation_covers_every_crop_in_the_batch(self, test_db, video_id, evidence_root):
        from core.gpu_inference_slot import GpuInferenceSlot

        _seed(test_db, video_id, occurrence_key="t7g1", frames=(1, 2))
        _seed(test_db, video_id, occurrence_key="t9g1", frames=(1, 2))
        slot = GpuInferenceSlot()
        holders = []

        def predictor(image):
            holders.append(slot.holder())
            return []

        scanner = _scanner(test_db, predictor, slot=slot)
        before = slot.stats["acquired"]
        report = scanner.run_once()
        assert report["ready"] == 2
        assert len(holders) == 4 and set(holders) == {scanner._slot_owner}
        assert slot.stats["acquired"] - before == 1
        assert slot.is_free()

    def test_busy_gpu_claims_nothing_and_spends_no_attempt(self, test_db, video_id, evidence_root):
        from core.gpu_inference_slot import PRIORITY_VIDEO_JOB, GpuInferenceSlot

        candidate_id = _seed(test_db, video_id)
        slot = GpuInferenceSlot()
        calls = []
        scanner = _scanner(test_db, _three_class_predictor(calls), slot=slot)
        token = slot.acquire("video-job:1", timeout=0.0, priority=PRIORITY_VIDEO_JOB)
        try:
            report = scanner.run_once()
        finally:
            slot.release(token)
        assert report["skipped"] == "gpu_slot_busy"
        assert calls == []
        row = test_db.get_motorcycle_detail_candidate(candidate_id)
        assert row["scan_state"] == "queued" and row["scan_attempts"] == 0
        assert slot.is_free()

    def test_inference_failures_retry_then_fail_with_the_slot_released(self, test_db, video_id, evidence_root):
        candidate_id = _seed(test_db, video_id)
        calls = []

        def broken(image):
            calls.append(1)
            raise RuntimeError("cuda error")

        scanner = _scanner(test_db, broken, max_attempts=2)
        first = scanner.run_once()
        assert first["deferred"] == 1
        assert test_db.get_motorcycle_detail_candidate(candidate_id)["scan_state"] == "queued"
        second = scanner.run_once()
        assert second["failed"] == 1
        row = test_db.get_motorcycle_detail_candidate(candidate_id)
        assert row["scan_state"] == "failed" and row["scan_attempts"] == 2
        assert "cuda error" in row["scan_error"]
        assert scanner.run_once()["claimed"] == 0
        assert calls == [1, 1]
        assert scanner.gpu_slot.is_free()

    def test_startup_recovery_resumes_a_scan_abandoned_by_a_dead_process(
        self, test_db, video_id, evidence_root
    ):
        candidate_id = _seed(test_db, video_id)
        assert len(test_db.claim_motorcycle_detail_scans(10)) == 1  # the process then dies
        scanner = _scanner(test_db, _three_class_predictor())
        assert scanner.recover_abandoned(include_recent=True) == 1
        assert scanner.run_once()["ready"] == 1
        row = test_db.get_motorcycle_detail_candidate(candidate_id)
        assert row["scan_state"] == "ready" and row["scan_attempts"] == 2

    def test_designation_change_refreshes_a_failed_gate(self, test_db, video_id, evidence_root, tmp_path):
        from core.gpu_inference_slot import GpuInferenceSlot
        from core.motorcycle_detail_scan import clear_model_cache

        candidate_id = _seed(test_db, video_id)
        weights = tmp_path / "detail.pt"
        weights.write_bytes(b"pilot")
        names = {"value": _map(PILOT_3)}
        fingerprint = {"value": "pilot"}
        scanner = MotorcycleDetailScanner(
            adapter=test_db,
            checkpoint_loader=lambda: load_detail_checkpoint(
                str(weights), model_loader=lambda p: _model(names["value"])
            ),
            checkpoint_fingerprint=lambda: fingerprint["value"],
            predictor_factory=lambda cp: _three_class_predictor(),
            gpu_wait_sec=0.0,
            gpu_slot=GpuInferenceSlot(),
        )
        assert scanner.run_once()["skipped"] == "detail_checkpoint_unavailable"
        # Same file path: the operator corrects the checkpoint and the
        # designation fingerprint changes, so the gate is re-validated.
        clear_model_cache()
        names["value"] = _map(DETAIL_3)
        fingerprint["value"] = "corrected"
        assert scanner.run_once()["ready"] == 1
        assert test_db.get_motorcycle_detail_candidate(candidate_id)["scan_state"] == "ready"


# ---------------------------------------------------------------------------
# Detail output never creates or confirms a violation
# ---------------------------------------------------------------------------


@pytest.fixture
def app_workers(test_db_path):
    """Stop the app's background threads before the temp DB is deleted.

    The first request starts the detail scanner, which would otherwise keep a
    Windows file lock on the temporary SQLite database.
    """
    yield
    import app as flask_app

    flask_app.stop_processing_worker()
    flask_app.stop_detail_scanner()


def _enforcer_client(username="md3_enforcer", password="md3-enforcer-pass"):
    import bcrypt
    from database import db

    import app as flask_app

    db.create_user(username, bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(), role="enforcer")
    flask_app.app.config["TESTING"] = True
    flask_app.app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.app.test_client()
    client.post("/login", data={"username": username, "password": password})
    return client


class TestNoViolationSideEffects:
    def test_scan_and_every_human_outcome_write_zero_protected_rows(
        self, test_db, video_id, evidence_root, app_workers
    ):
        ids = [_seed(test_db, video_id, occurrence_key=f"t{k}g1") for k in (7, 8, 9)]
        baseline = _protected_counts(test_db)
        assert set(baseline.values()) == {0}

        report = _scanner(
            test_db,
            _three_class_predictor(detections=[("helmet_nut_shell", HELMET_SRC)]),
            batch_limit=3,
        ).run_once()
        assert report["ready"] == 3
        assert _protected_counts(test_db) == baseline

        client = _enforcer_client()
        for candidate_id, outcome in zip(ids, ("reviewed", "dismissed", "uncertain")):
            response = client.post(
                f"/api/motorcycle-detail-review/{candidate_id}/outcome", json={"outcome": outcome}
            )
            assert response.status_code == 200, response.get_data(as_text=True)
            assert test_db.get_motorcycle_detail_candidate(candidate_id)["human_outcome"] == outcome
        assert client.post(
            f"/api/motorcycle-detail-review/{ids[0]}/outcome", json={"outcome": "confirmed"}
        ).status_code == 400
        assert _protected_counts(test_db) == baseline
        assert test_db.count_review_pending() == 0


class TestReviewPageAndOldRows:
    def test_page_shows_the_three_class_contract_and_old_rows_stay_readable(
        self, test_db, video_id, evidence_root, app_workers
    ):
        old_id = _seed(test_db, video_id, occurrence_key="t5g1", with_context=False)
        test_db.claim_motorcycle_detail_scans(10)
        # A row scanned before this change: 15-class map, YOLOv8m identity, no context.
        test_db.finish_motorcycle_detail_scan(
            candidate_id=old_id,
            observations_json=json.dumps(
                {"scan": {"model": "YOLOv8m:old.pt:abc", "class_names": list(MAIN_15), "conf": 0.25},
                 "frames": [], "association": {"rider": {"state": "associated"}}, "uncertainty": []}
            ),
            association_json=json.dumps(
                {"rider": {"state": "associated"}, "helmet": {"state": "acceptable"},
                 "mirror": {"state": "none_visible"}}
            ),
            uncertainty_json=json.dumps(["mirror:absence_not_proven_unknown"]),
            scan_model="YOLOv8m:old.pt:abc",
            scan_class_map_json=json.dumps(list(MAIN_15)),
        )
        client = _enforcer_client("md3_page", "md3-page-password")
        page = client.get("/motorcycle-detail-review?outcome=all")
        assert page.status_code == 200
        body = page.get_data(as_text=True)
        assert "15-class object roster" not in body
        assert "three-class" in body
        assert "0=helmet_nut_shell" in body and "2=side_mirror" in body

        payload = client.get("/api/motorcycle-detail-review?outcome=all").get_json()
        assert payload["gate"]["contract"]["classes"] == list(DETAIL_3)
        old = next(item for item in payload["items"] if item["id"] == old_id)
        assert old["scan_model"] == "YOLOv8m:old.pt:abc"
        assert old["scan_class_map"] == list(MAIN_15)
        assert old["association"]["helmet"]["state"] == "acceptable"
        assert old["can_confirm"] is False


# ---------------------------------------------------------------------------
# Fail-closed: the checkpoint must declare a detection task
# ---------------------------------------------------------------------------

_NO_TASK_ATTR = object()


def _task_model(task):
    if task is _NO_TASK_ATTR:
        return SimpleNamespace(names=_map(DETAIL_3))
    return SimpleNamespace(names=_map(DETAIL_3), task=task)


_BAD_TASKS = [
    (_NO_TASK_ATTR, "detail_checkpoint_task_missing"),
    (None, "detail_checkpoint_task_missing"),
    ("", "detail_checkpoint_task_missing"),
    ("   ", "detail_checkpoint_task_missing"),
    (3, "detail_checkpoint_task_malformed"),
    (["detect"], "detail_checkpoint_task_malformed"),
    (b"detect", "detail_checkpoint_task_malformed"),
    ("classify", "detail_checkpoint_wrong_task:classify"),
    ("segment", "detail_checkpoint_wrong_task:segment"),
    ("pose", "detail_checkpoint_wrong_task:pose"),
    ("obb", "detail_checkpoint_wrong_task:obb"),
]
_BAD_TASK_IDS = [
    "no-attribute", "none", "empty", "blank", "int", "list", "bytes",
    "classify", "segment", "pose", "obb",
]


class TestDetailTaskGate:
    def test_valid_three_class_detect_checkpoint_passes(self, tmp_path):
        weights = tmp_path / "detail.pt"
        weights.write_bytes(b"w")
        cp = load_detail_checkpoint(str(weights), model_loader=lambda p: _task_model("detect"))
        assert cp.ok, cp.reason
        assert cp.class_names == DETAIL_3

    @pytest.mark.parametrize(("task", "expected"), _BAD_TASKS, ids=_BAD_TASK_IDS)
    def test_missing_or_non_detection_task_is_rejected(self, tmp_path, task, expected):
        weights = tmp_path / "detail.pt"
        weights.write_bytes(b"w")
        cp = load_detail_checkpoint(str(weights), model_loader=lambda p: _task_model(task))
        assert not cp.ok
        assert cp.reason.startswith(expected), cp.reason
        assert cp.class_names == ()

    @pytest.mark.parametrize(
        ("task", "expected"),
        [_BAD_TASKS[0], _BAD_TASKS[1], _BAD_TASKS[3], _BAD_TASKS[4], _BAD_TASKS[7]],
        ids=["no-attribute", "none", "blank", "int", "classify"],
    )
    def test_task_rejection_reads_zero_crops_and_spends_no_attempt(
        self, test_db, video_id, evidence_root, tmp_path, monkeypatch, task, expected
    ):
        candidate_id = _seed(test_db, video_id)
        weights = tmp_path / "designated.pt"
        weights.write_bytes(b"weights")
        crop_reads, calls, factory_calls = [], [], []
        original_reader = MotorcycleDetailScanner._read_crop_image
        monkeypatch.setattr(
            MotorcycleDetailScanner,
            "_read_crop_image",
            staticmethod(lambda *a, **k: crop_reads.append(a) or original_reader(*a, **k)),
        )
        scanner = _scanner(
            test_db,
            _three_class_predictor(calls),
            loader=lambda: load_detail_checkpoint(str(weights), model_loader=lambda p: _task_model(task)),
            factory_calls=factory_calls,
        )
        report = scanner.run_once()
        assert report["skipped"] == "detail_checkpoint_unavailable"
        assert report["gate_reason"].startswith(expected)
        assert crop_reads == [] and calls == [] and factory_calls == []
        row = test_db.get_motorcycle_detail_candidate(candidate_id)
        assert row["scan_state"] == "queued"
        assert row["scan_attempts"] == 0
        assert row["scan_error"].startswith(expected)


# ---------------------------------------------------------------------------
# Fail-closed: truncated or unrecorded context never yields a definite owner
# ---------------------------------------------------------------------------

# Nearby objects that intersect the crop but cannot own HELMET_SRC or the
# target's mirrors (no head region / mounting area covers them).
FAR_RIDERS = [(100.0 + k, 150.0, 115.0 + k, 200.0) for k in range(8)]
FAR_BIKES = [(100.0 + k, 195.0, 125.0 + k, 210.0) for k in range(8)]
TARGET_ITEMS = [("helmet_acceptable", HELMET_SRC), ("side_mirror", LEFT_MIRROR_SRC)]


class TestCollectorRecordsWhichListWasTruncated:
    def test_more_than_eight_motorcycles_marks_only_the_motorcycle_list(self):
        crowd = [_det(100 + k, "motorcycle", (120.0 + k, 100.0, 170.0 + k, 180.0)) for k in range(12)]
        frames = _collect([[_det(7, "motorcycle", BIKE), _det(11, "rider", RIDER), *crowd]])
        context = frames[0]["main_context"]
        assert len(context["motorcycles"]) == 8
        assert context["truncated_lists"] == {"motorcycles": True, "riders": False}
        assert context["truncated"] is True

    def test_more_than_eight_other_riders_marks_only_the_rider_list(self):
        # Inside the padded crop, below the target bike (no overlap with it).
        crowd = [_det(200 + k, "rider", (120.0 + k, 191.0, 135.0 + k, 205.0)) for k in range(10)]
        frames = _collect([[_det(7, "motorcycle", BIKE), _det(11, "rider", RIDER), *crowd]])
        context = frames[0]["main_context"]
        assert len(context["riders"]) == 8
        assert context["truncated_lists"] == {"motorcycles": False, "riders": True}

    def test_person_detections_never_count_toward_rider_truncation(self):
        crowd = [_det(300 + k, "person", (100.0 + k, 150.0, 115.0 + k, 200.0)) for k in range(12)]
        frames = _collect([[_det(7, "motorcycle", BIKE), _det(11, "rider", RIDER), *crowd]])
        context = frames[0]["main_context"]
        assert context["riders"] == []
        assert context["truncated_lists"] == {"motorcycles": False, "riders": False}
        assert context["truncated"] is False

    def test_stored_context_round_trips_the_per_list_flags(self):
        crowd = [_det(100 + k, "motorcycle", (120.0 + k, 100.0, 170.0 + k, 180.0)) for k in range(12)]
        frames = _collect([[_det(7, "motorcycle", BIKE), _det(11, "rider", RIDER), *crowd]])
        context = MainDetectorContext.from_frame(frames[0])
        assert context.motorcycles_truncated is True
        assert context.riders_truncated is False


class TestTruncatedContextFailsClosed:
    def test_complete_context_keeps_definite_attribution(self):
        context = _ctx(
            motorcycles=FAR_BIKES, riders=FAR_RIDERS,
            truncated=False, truncated_lists={"motorcycles": False, "riders": False},
        )
        result = _assoc(TARGET_ITEMS, context)
        assert result.helmet.state == "acceptable"
        assert result.mirror.state == "one_left"
        assert not any(u.startswith("context:") for u in result.uncertainty)

    def test_truncated_motorcycles_make_the_mirror_ambiguous_only(self):
        context = _ctx(
            motorcycles=FAR_BIKES, riders=FAR_RIDERS,
            truncated=True, truncated_lists={"motorcycles": True, "riders": False},
        )
        result = _assoc(TARGET_ITEMS, context)
        assert result.mirror.state == "ambiguous"
        assert result.mirror.observations == ()
        assert "mirror:nearby_motorcycle_context_incomplete_mirror_unattributed" in result.uncertainty
        assert "context:nearby_motorcycles_truncated" in result.uncertainty
        # The rider list is complete, so the helmet owner is still established.
        assert result.helmet.state == "acceptable"

    def test_truncated_riders_make_the_helmet_ambiguous_only(self):
        context = _ctx(
            motorcycles=FAR_BIKES, riders=FAR_RIDERS,
            truncated=True, truncated_lists={"motorcycles": False, "riders": True},
        )
        result = _assoc(TARGET_ITEMS, context)
        assert result.helmet.state == "ambiguous"
        assert result.helmet.observations == ()
        assert "helmet:nearby_rider_context_incomplete_helmet_unattributed" in result.uncertainty
        assert "context:nearby_riders_truncated" in result.uncertainty
        assert result.mirror.state == "one_left"

    def test_older_generic_truncated_flag_is_treated_as_both_lists(self):
        context = _ctx(motorcycles=FAR_BIKES, riders=FAR_RIDERS, truncated=True)
        assert context.motorcycles_truncated and context.riders_truncated
        result = _assoc(TARGET_ITEMS, context)
        assert result.helmet.state == "ambiguous"
        assert result.mirror.state == "ambiguous"

    def test_older_generic_flag_false_keeps_definite_attribution(self):
        result = _assoc(TARGET_ITEMS, _ctx(truncated=False))
        assert result.helmet.state == "acceptable"
        assert result.mirror.state == "one_left"

    @pytest.mark.parametrize(
        ("truncated", "truncated_lists"),
        [
            (_MISSING, None),
            ("yes", None),
            (_MISSING, {"motorcycles": "no", "riders": 0}),
            (_MISSING, "both"),
        ],
        ids=["no-flag", "string-flag", "non-bool-lists", "string-lists"],
    )
    def test_unverifiable_truncation_metadata_is_treated_as_truncated(self, truncated, truncated_lists):
        context = _ctx(truncated=truncated, truncated_lists=truncated_lists)
        result = _assoc(TARGET_ITEMS, context)
        assert result.helmet.state == "ambiguous"
        assert result.mirror.state == "ambiguous"

    def test_missing_detections_stay_unknown_under_truncation(self):
        # Truncation affects ownership, not existence: nothing seen is still
        # unknown / none_visible, never ambiguous and never proof of absence.
        context = _ctx(truncated=True, truncated_lists={"motorcycles": True, "riders": True})
        result = _assoc([], context)
        assert result.helmet.state == "unknown"
        assert result.mirror.state == "none_visible"
        assert "mirror:absence_not_proven_unknown" in result.uncertainty

    def test_overlap_and_shared_head_ambiguity_is_preserved_with_complete_context(self):
        pillion = (140.0, 55.0, 180.0, 140.0)
        result = _assoc(
            TARGET_ITEMS,
            _ctx(motorcycles=[OVERLAP_BIKE], riders=[pillion],
                 truncated=False, truncated_lists={"motorcycles": False, "riders": False}),
        )
        assert result.helmet.state == "ambiguous"
        assert result.mirror.state == "ambiguous"

    def test_scan_of_a_truncated_row_stores_ambiguity(self, test_db, video_id, evidence_root):
        candidate_id = _seed(
            test_db, video_id,
            context_extra={"truncated": True, "truncated_lists": {"motorcycles": True, "riders": False}},
        )
        report = _scanner(test_db, _three_class_predictor()).run_once()
        assert report["ready"] == 1
        row = test_db.get_motorcycle_detail_candidate(candidate_id)
        association = json.loads(row["association_json"])
        assert association["mirror"]["state"] == "ambiguous"
        assert association["helmet"]["state"] == "acceptable"
        assert association["context"]["truncated_lists"] == {"motorcycles": True, "riders": False}
        assert "context:nearby_motorcycles_truncated" in json.loads(row["uncertainty_json"])
        assert _protected_counts(test_db) == {t: 0 for t in _protected_counts(test_db)}


DETAIL_4 = DETAIL_3 + ("no_helmet",)


def _ok_4c_checkpoint():
    return DetailCheckpoint(
        path="mock4",
        class_names=DETAIL_4,
        ok=True,
        identity="md4c:mock4",
        architecture="yolov8n",
        contract_version="md-detail-4c-v1",
        model_kind="motorcycle_detail_4class",
        identity_prefix="md4c",
    )


class TestFourClassContract:
    def test_both_exact_orders_resolve_and_keep_their_identity(self):
        from core.motorcycle_detail_contract import resolve_detail_contract

        three = resolve_detail_contract(_map(DETAIL_3))
        four = resolve_detail_contract(_map(DETAIL_4))
        assert three.ok and three.version == "md-detail-3c-v1"
        assert three.identity_prefix == "md3c" and three.class_names == DETAIL_3
        assert four.ok and four.version == "md-detail-4c-v1"
        assert four.identity_prefix == "md4c"
        assert four.class_map()["3"] == "no_helmet"
        assert "no_helmet" not in three.class_names

    @pytest.mark.parametrize(
        ("names", "code"),
        [
            (PILOT_3, "reordered_names"),
            (MAIN_15, "wrong_class_count"),
            (("helmet_nut_shell", "helmet_acceptable", "side_mirror", "person"), "renamed_names"),
            (("no_helmet", "helmet_nut_shell", "helmet_acceptable", "side_mirror"), "reordered_names"),
            (("side_mirror", "helmet_nut_shell", "helmet_acceptable", "no_helmet"), "reordered_names"),
            (("helmet_nut_shell", "helmet_acceptable", "side_mirror", "no_helmet", "rider"), "wrong_class_count"),
            (("helmet_nut_shell", "helmet_nut_shell", "side_mirror", "no_helmet"), "duplicate_names"),
            (None, "missing"),
            ({}, "malformed"),
            ({0: "helmet_nut_shell", 1: "helmet_acceptable", 3: "side_mirror"}, "non_contiguous_class_ids"),
        ],
    )
    def test_bad_maps_are_rejected_with_a_visible_reason(self, names, code):
        from core.motorcycle_detail_contract import resolve_detail_contract

        if names is None or names == {} or isinstance(names, dict):
            raw = names
        else:
            raw = _map(names)
        report = resolve_detail_contract(raw)
        assert not report.ok
        assert report.code == code
        assert report.reason
        if code == "reordered_names" and names is not None and tuple(names[:3]) == PILOT_3:
            assert "pilot" in report.reason and "never relabeled" in report.reason

    def test_class_id_3_is_no_helmet_only_on_a_four_class_checkpoint(self):
        boxes = [
            SimpleNamespace(cls=np.array([3]), conf=np.array([0.66]), xyxy=np.array([[4.0, 5.0, 14.0, 16.0]]))
        ]
        model = SimpleNamespace(predict=lambda **kwargs: [SimpleNamespace(names=_map(DETAIL_4), boxes=boxes)])
        out = UltralyticsCropPredictor(model, DETAIL_4, conf=0.25).predict(np.zeros((8, 8, 3), np.uint8))
        assert out[0]["class_label"] == "no_helmet"
        three = SimpleNamespace(predict=lambda **kwargs: [SimpleNamespace(names=_map(DETAIL_3), boxes=boxes)])
        with pytest.raises(DetailScanError, match="out_of_range"):
            UltralyticsCropPredictor(three, DETAIL_3, conf=0.25).predict(np.zeros((8, 8, 3), np.uint8))
        mismatched = SimpleNamespace(predict=lambda **kwargs: [SimpleNamespace(names=_map(DETAIL_4), boxes=boxes)])
        with pytest.raises(DetailScanError, match="class_map_mismatch"):
            UltralyticsCropPredictor(mismatched, DETAIL_3, conf=0.25).predict(np.zeros((8, 8, 3), np.uint8))

    def test_four_class_checkpoint_identity_is_not_described_as_three_class(self, tmp_path):
        weights = tmp_path / "detail4.pt"
        weights.write_bytes(b"four-class")
        cp = load_detail_checkpoint(str(weights), model_loader=lambda path: _model(_map(DETAIL_4)))
        assert cp.ok, cp.reason
        assert cp.identity.startswith("md4c:detail4.pt:")
        assert cp.contract_version == "md-detail-4c-v1"
        assert cp.model_kind == "motorcycle_detail_4class"
        described = cp.describe()
        assert described["contract"] == "md-detail-4c-v1"
        assert described["identity_prefix"] == "md4c"
        assert described["class_map"]["3"] == "no_helmet"
        assert "motorcycle_detail_3class" not in json.dumps(described)

    def test_no_helmet_is_a_positive_main_rider_head_observation(self):
        from core.detection_config import OBJECT_DETECTOR_CLASSES

        assert "no_helmet" not in OBJECT_DETECTOR_CLASSES
        result = _assoc([("no_helmet", HELMET_SRC)], _ctx())
        assert result.rider.source == "main_detector"
        assert result.rider.rider_track_id == 11
        assert result.helmet.state == "uncovered_head"
        assert [o.label for o in result.helmet.observations] == ["no_helmet"]
        assert result.helmet.state != "unknown"

    def test_missing_detection_stays_unknown_on_both_contracts(self):
        result = _assoc([], _ctx())
        assert result.helmet.state == "unknown"
        assert result.helmet.observations == ()
        assert "helmet:no_observation_does_not_prove_absence" in result.uncertainty

    def test_person_is_not_the_rider_for_an_uncovered_head(self):
        unowned = _assoc(
            [("person", RIDER), ("no_helmet", HELMET_SRC)],
            _ctx(rider_state="unassociated"),
        )
        assert unowned.rider.state == "unassociated"
        assert unowned.helmet.state == "unknown"
        owned = _assoc([("person", RIDER), ("rider", RIDER), ("no_helmet", HELMET_SRC)], _ctx())
        assert owned.rider.source == "main_detector"
        assert owned.rider.rider_track_id == 11
        assert owned.helmet.state == "uncovered_head"

    def test_nearby_rider_truncated_context_and_clipped_box_are_not_definite(self):
        pillion = (140.0, 55.0, 180.0, 140.0)
        shared = _assoc([("no_helmet", HELMET_SRC)], _ctx(riders=[pillion]))
        assert shared.helmet.state == "ambiguous"
        assert shared.helmet.observations == ()
        truncated = _assoc(
            [("no_helmet", HELMET_SRC)],
            _ctx(truncated=True, truncated_lists={"motorcycles": False, "riders": True}),
        )
        assert truncated.helmet.state == "ambiguous"
        assert truncated.helmet.state != "uncovered_head"
        clipped = to_pipeline_detection("no_helmet", 0.9, HELMET_SRC)
        clipped["evidence_limited"] = True
        limited = associate_scan_detections(
            [clipped], motorcycle_box=BIKE, frame_w=FRAME_W, frame_h=FRAME_H, main_context=_ctx()
        )
        assert limited.helmet.state == "ambiguous"
        assert limited.helmet.observations[0].label == "no_helmet"
        assert "uncovered_head_box_clipped_or_unclear" in limited.helmet.reasons

    def test_contradictory_head_labels_stay_ambiguous_and_keep_both_boxes(self):
        detections = [
            to_pipeline_detection("helmet_nut_shell", 0.99, HELMET_SRC),
            to_pipeline_detection("no_helmet", 0.4, (148.0, 60.0, 172.0, 84.0)),
        ]
        result = associate_scan_detections(
            detections, motorcycle_box=BIKE, frame_w=FRAME_W, frame_h=FRAME_H, main_context=_ctx()
        )
        assert result.helmet.state == "ambiguous"
        labels = {o.label for o in result.helmet.observations}
        assert labels == {"helmet_nut_shell", "no_helmet"}
        assert "contradictory_head_labels" in result.helmet.reasons

    def test_scan_maps_no_helmet_and_keeps_frame_conflicts_ambiguous(
        self, test_db, video_id, evidence_root
    ):
        candidate_id = _seed(test_db, video_id, frames=(1, 2))
        calls = []

        def predictor(image):
            calls.append(1)
            label = "helmet_acceptable" if len(calls) == 1 else "no_helmet"
            return [{"class_label": label, "confidence": 0.8, "bbox": _to_crop(HELMET_SRC)}]

        report = _scanner(test_db, predictor, loader=_ok_4c_checkpoint).run_once()
        assert report["ready"] == 1 and len(calls) == 2
        row = test_db.get_motorcycle_detail_candidate(candidate_id)
        assert row["scan_model"] == "md4c:mock4"
        assert json.loads(row["scan_class_map_json"]) == list(DETAIL_4)
        observations = json.loads(row["observations_json"])
        assert observations["scan"]["contract"] == "md-detail-4c-v1"
        assert observations["scan"]["model_kind"] == "motorcycle_detail_4class"
        assert observations["scan"]["no_helmet_policy"] == "positive_visible_uncovered_head_review_only"
        assert observations["frames"][0]["association"]["helmet"]["state"] == "acceptable"
        uncovered = observations["frames"][1]["detections"][0]
        assert uncovered["class_label"] == "no_helmet"
        assert uncovered["class_id"] == 3
        assert uncovered["contract"] == "md-detail-4c-v1"
        assert uncovered["source_bbox"] == [pytest.approx(v) for v in HELMET_SRC]
        summary = json.loads(row["association_json"])
        assert summary["helmet"]["state"] == "ambiguous"
        assert "contradictory_helmet_observations_across_frames" in summary["helmet"]["reasons"]
        assert {o["label"] for o in summary["helmet"]["observations"]} == {"helmet_acceptable", "no_helmet"}
        assert "helmet:contradictory_helmet_observations_across_frames" in json.loads(row["uncertainty_json"])
        assert _protected_counts(test_db) == {t: 0 for t in _protected_counts(test_db)}

    def test_edge_no_helmet_box_is_clamped_and_not_a_definite_attribution(
        self, test_db, video_id, evidence_root
    ):
        edge = {"x": 0, "y": 0, "w": 220, "h": 210}
        candidate_id = _seed(test_db, video_id, crop=edge)

        def predictor(image):
            return [{"class_label": "no_helmet", "confidence": 0.7, "bbox": (145.0, -15.0, 175.0, 85.0)}]

        _scanner(test_db, predictor, loader=_ok_4c_checkpoint).run_once()
        observations = json.loads(test_db.get_motorcycle_detail_candidate(candidate_id)["observations_json"])
        detection = observations["frames"][0]["detections"][0]
        assert detection["class_id"] == 3
        assert detection["source_bbox"][0] == pytest.approx(145.0)
        assert detection["source_bbox"][1] == 0.0
        assert detection["evidence_limited"] is True
        assert observations["frames"][0]["association"]["helmet"]["state"] == "ambiguous"

    def test_rejected_four_class_map_reads_zero_crops(self, test_db, video_id, evidence_root, tmp_path):
        candidate_id = _seed(test_db, video_id)
        weights = tmp_path / "mixed.pt"
        weights.write_bytes(b"weights")
        calls, factory_calls, crop_reads = [], [], []
        original = MotorcycleDetailScanner._read_crop_image

        def _counting_reader(*args, **kwargs):
            crop_reads.append(args)
            return original(*args, **kwargs)

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(MotorcycleDetailScanner, "_read_crop_image", staticmethod(_counting_reader))
        try:
            scanner = _scanner(
                test_db,
                _three_class_predictor(calls),
                loader=lambda: load_detail_checkpoint(
                    str(weights),
                    model_loader=lambda p: _model(_map(("helmet_nut_shell", "no_helmet", "side_mirror", "helmet_acceptable"))),
                ),
                factory_calls=factory_calls,
            )
            report = scanner.run_once()
        finally:
            monkeypatch.undo()
        assert report["skipped"] == "detail_checkpoint_unavailable"
        assert "reordered_names" in report["gate_reason"]
        assert calls == [] and factory_calls == [] and crop_reads == []
        row = test_db.get_motorcycle_detail_candidate(candidate_id)
        assert row["scan_state"] == "queued" and row["scan_attempts"] == 0

    def test_four_class_scan_and_human_outcomes_write_zero_violations(
        self, test_db, video_id, evidence_root, app_workers
    ):
        ids = [_seed(test_db, video_id, occurrence_key=f"t{k}g1") for k in (17, 18, 19)]
        baseline = _protected_counts(test_db)

        def predictor(image):
            return [{"class_label": "no_helmet", "confidence": 0.8, "bbox": _to_crop(HELMET_SRC)}]

        report = _scanner(test_db, predictor, loader=_ok_4c_checkpoint, batch_limit=3).run_once()
        assert report["ready"] == 3
        for candidate_id in ids:
            association = json.loads(test_db.get_motorcycle_detail_candidate(candidate_id)["association_json"])
            assert association["helmet"]["state"] == "uncovered_head"
        assert _protected_counts(test_db) == baseline
        client = _enforcer_client("md4_enforcer", "md4-enforcer-pass")
        for candidate_id, outcome in zip(ids, ("reviewed", "dismissed", "uncertain")):
            response = client.post(
                f"/api/motorcycle-detail-review/{candidate_id}/outcome", json={"outcome": outcome}
            )
            assert response.status_code == 200
        assert _protected_counts(test_db) == baseline
        assert test_db.count_review_pending() == 0

    def test_page_shows_both_contracts_and_old_rows_remain_readable(
        self, test_db, video_id, evidence_root, app_workers
    ):
        old_id = _seed(test_db, video_id, occurrence_key="t4g1", with_context=False)
        test_db.claim_motorcycle_detail_scans(10)
        test_db.finish_motorcycle_detail_scan(
            candidate_id=old_id,
            observations_json=json.dumps({"scan": {"model": "md3c:old.pt:abc", "contract": "md-detail-3c-v1"}}),
            association_json=json.dumps({"helmet": {"state": "unknown"}, "rider": {"state": "unassociated"}}),
            uncertainty_json=json.dumps(["helmet:no_observation_does_not_prove_absence"]),
            scan_model="md3c:old.pt:abc",
            scan_class_map_json=json.dumps(list(DETAIL_3)),
        )
        client = _enforcer_client("md4_page", "md4-page-password")
        body = client.get("/motorcycle-detail-review?outcome=all").get_data(as_text=True)
        assert "three-class" in body and "four-class" in body
        assert "md-detail-4c-v1" in body and "3=no_helmet" in body
        assert "Uncovered head observed" in body
        assert "15-class object roster" not in body
        payload = client.get("/api/motorcycle-detail-review?outcome=all").get_json()
        assert payload["gate"]["contract"]["version"] == "md-detail-3c-v1"
        assert payload["gate"]["contract"]["classes"] == list(DETAIL_3)
        versions = [item["version"] for item in payload["gate"]["supported_contracts"]]
        assert versions == ["md-detail-3c-v1", "md-detail-4c-v1"]
        old = next(item for item in payload["items"] if item["id"] == old_id)
        assert old["scan_class_map"] == list(DETAIL_3)
        assert old["association"]["helmet"]["state"] == "unknown"
        assert old["can_confirm"] is False


# ---------------------------------------------------------------------------
# Top-level helmet summary aggregates every selected frame
# ---------------------------------------------------------------------------

PILLION = (140.0, 55.0, 180.0, 140.0)  # second rider whose head region holds HELMET_SRC
SECOND_HEAD_SRC = (148.0, 60.0, 172.0, 84.0)
_PILLION_CONTEXT = {"riders": [{"track_id": 30, "confidence": 0.9, "bbox": _xywh(PILLION)}]}
_DEFINITE = ("acceptable", "nut_shell", "uncovered_head")

# Per-frame scenario -> (detail detections, per-frame main-context override).
_FRAME_SPECS = {
    "unknown": ([], None),
    "acceptable": ([("helmet_acceptable", HELMET_SRC)], None),
    "nut_shell": ([("helmet_nut_shell", HELMET_SRC)], None),
    "uncovered_head": ([("no_helmet", HELMET_SRC)], None),
    # Helmet on a head shared with another main-detector rider: unattributed.
    "ambiguous": ([("helmet_acceptable", HELMET_SRC)], _PILLION_CONTEXT),
    # Helmet label and uncovered head on the same head (four-class only).
    "ambiguous_labels": ([("helmet_nut_shell", HELMET_SRC), ("no_helmet", SECOND_HEAD_SRC)], None),
}


def _frame_state(scenario):
    return "ambiguous" if scenario.startswith("ambiguous") else scenario


def _scan_frames(test_db, video_id, scenarios, *, four_class, occurrence_key="t7g1"):
    frames = tuple(range(1, len(scenarios) + 1))
    per_frame_context = {
        index: _FRAME_SPECS[name][1]
        for index, name in zip(frames, scenarios)
        if _FRAME_SPECS[name][1]
    }
    candidate_id = _seed(
        test_db, video_id, occurrence_key=occurrence_key, frames=frames,
        frame_context_extra=per_frame_context,
    )
    calls = []

    def predictor(image):
        detections = _FRAME_SPECS[scenarios[len(calls)]][0]
        calls.append(1)
        return [{"class_label": label, "confidence": 0.8, "bbox": _to_crop(box)} for label, box in detections]

    loader = _ok_4c_checkpoint if four_class else _ok_checkpoint
    report = _scanner(test_db, predictor, loader=loader).run_once()
    assert report["ready"] == 1 and len(calls) == len(scenarios)
    return candidate_id, test_db.get_motorcycle_detail_candidate(candidate_id)


def _rendered_helmet(body):
    match = re.search(r"Helmet:\s*(.*?)\s*·", body, re.S)
    assert match, "review row does not render a helmet state"
    return match.group(1).strip()


def _function_frame(scenario):
    detections, extra = _FRAME_SPECS[scenario]
    return _assoc(detections, _ctx(riders=[PILLION]) if extra else _ctx())


def _function_summary(scenarios):
    """Mirror the scanner: the primary (first) frame unless a summary is returned."""
    from core.motorcycle_detail_scan import _reconcile_helmet_across_frames

    associations = [_function_frame(name) for name in scenarios]
    before = [a.as_dict() for a in associations]
    reconciled, reason = _reconcile_helmet_across_frames(associations)
    assert [a.as_dict() for a in associations] == before
    summary = reconciled if reconciled is not None else associations[0].helmet
    return summary, reason, associations


_TWO_FRAME_TABLE = [
    # (selected-frame scenarios in scan order; first is primary, top-level state, four-class)
    (("unknown", "unknown"), "unknown", False),
    (("unknown", "unknown"), "unknown", True),
    (("unknown", "uncovered_head"), "uncovered_head", True),
    (("uncovered_head", "unknown"), "uncovered_head", True),
    (("unknown", "acceptable"), "acceptable", False),
    (("acceptable", "unknown"), "acceptable", False),
    (("unknown", "nut_shell"), "nut_shell", False),
    (("nut_shell", "unknown"), "nut_shell", True),
    (("unknown", "ambiguous"), "ambiguous", False),
    (("ambiguous", "unknown"), "ambiguous", False),
    (("unknown", "ambiguous_labels"), "ambiguous", True),
    (("ambiguous_labels", "unknown"), "ambiguous", True),
    (("ambiguous", "ambiguous_labels"), "ambiguous", True),
    (("acceptable", "acceptable"), "acceptable", False),
    (("uncovered_head", "uncovered_head"), "uncovered_head", True),
    (("acceptable", "nut_shell"), "ambiguous", False),
    (("nut_shell", "acceptable"), "ambiguous", False),
    (("acceptable", "uncovered_head"), "ambiguous", True),
    (("uncovered_head", "acceptable"), "ambiguous", True),
    (("nut_shell", "uncovered_head"), "ambiguous", True),
    (("acceptable", "ambiguous"), "ambiguous", False),
    (("ambiguous", "acceptable"), "ambiguous", False),
    (("uncovered_head", "ambiguous"), "ambiguous", True),
    (("ambiguous", "uncovered_head"), "ambiguous", True),
    (("ambiguous_labels", "nut_shell"), "ambiguous", True),
]

_THREE_FRAME_TABLE = [
    (("unknown", "unknown", "unknown"), "unknown"),
    (("unknown", "uncovered_head", "unknown"), "uncovered_head"),
    (("unknown", "unknown", "ambiguous"), "ambiguous"),
    (("acceptable", "unknown", "acceptable"), "acceptable"),
    (("unknown", "nut_shell", "ambiguous"), "ambiguous"),
    (("uncovered_head", "unknown", "nut_shell"), "ambiguous"),
    (("ambiguous", "unknown", "ambiguous_labels"), "ambiguous"),
    (("nut_shell", "uncovered_head", "acceptable"), "ambiguous"),
]


def _check_summary_against_frames(summary, frame_helmets, expected):
    """Shared assertions on a top-level summary built from per-frame helmet dicts."""
    states = [f["state"] for f in frame_helmets]
    assert summary["state"] == expected
    if expected == "unknown":
        assert set(states) == {"unknown"}
        assert summary == frame_helmets[0]
        return
    contributing = [f for f in frame_helmets if f["state"] != "unknown"]
    summary_labels = {o["label"] for o in summary["observations"]}
    for frame in contributing:
        assert {o["label"] for o in frame["observations"]} <= summary_labels
        assert set(frame["reasons"]) <= set(summary["reasons"])
    definite = {s for s in states if s in _DEFINITE}
    conflict = len(definite) > 1 or (definite and "ambiguous" in states)
    if conflict:
        assert "contradictory_helmet_observations_across_frames" in summary["reasons"]
    else:
        assert "contradictory_helmet_observations_across_frames" not in summary["reasons"]
    if "unknown" in states:
        assert "evidence_on_some_selected_frames_only" in summary["reasons"]
    if expected == "ambiguous":
        assert summary["reasons"]


class TestCrossFrameHelmetSummary:
    def test_unknown_primary_then_uncovered_head_reaches_saved_json_and_reviewer(
        self, test_db, video_id, evidence_root, app_workers
    ):
        candidate_id, row = _scan_frames(
            test_db, video_id, ("unknown", "uncovered_head"), four_class=True
        )
        observations = json.loads(row["observations_json"])
        frames = observations["frames"]
        assert row["frame_number"] == frames[0]["frame_number"] == 10
        assert frames[0]["detections"] == []
        assert frames[0]["association"]["helmet"]["state"] == "unknown"
        assert frames[0]["association"]["helmet"]["observations"] == []
        assert frames[1]["association"]["helmet"]["state"] == "uncovered_head"
        assert frames[1]["detections"][0]["class_label"] == "no_helmet"
        assert frames[1]["detections"][0]["class_id"] == 3

        summary = json.loads(row["association_json"])
        assert summary == observations["association"]
        assert summary["helmet"]["state"] == "uncovered_head"
        assert [o["label"] for o in summary["helmet"]["observations"]] == ["no_helmet"]
        assert summary["helmet"]["observations"][0]["box"] == [pytest.approx(v) for v in HELMET_SRC]
        assert "evidence_on_some_selected_frames_only" in summary["helmet"]["reasons"]
        uncertainty = json.loads(row["uncertainty_json"])
        assert "helmet:evidence_on_some_selected_frames_only" in uncertainty
        assert "helmet:no_observation_does_not_prove_absence" in uncertainty

        client = _enforcer_client("md_xf_uncovered", "md-xf-uncovered-pass")
        item = next(
            i for i in client.get("/api/motorcycle-detail-review?outcome=all").get_json()["items"]
            if i["id"] == candidate_id
        )
        assert item["association"]["helmet"]["state"] == "uncovered_head"
        assert item["can_confirm"] is False
        body = client.get("/motorcycle-detail-review?outcome=all").get_data(as_text=True)
        assert _rendered_helmet(body) == "Uncovered head observed (review only)"
        assert _protected_counts(test_db) == {t: 0 for t in _protected_counts(test_db)}

    @pytest.mark.parametrize(
        "scenarios,four_class,frame_reason",
        [
            (("unknown", "ambiguous"), False, "helmet_in_other_rider_head_region_unattributed"),
            (("unknown", "ambiguous_labels"), True, "contradictory_head_labels"),
        ],
        ids=["three-class-shared-head", "four-class-contradictory-labels"],
    )
    def test_unknown_primary_then_ambiguous_reaches_saved_json_and_reviewer(
        self, test_db, video_id, evidence_root, app_workers, scenarios, four_class, frame_reason
    ):
        candidate_id, row = _scan_frames(test_db, video_id, scenarios, four_class=four_class)
        observations = json.loads(row["observations_json"])
        frames = observations["frames"]
        assert frames[0]["association"]["helmet"]["state"] == "unknown"
        later = frames[1]["association"]["helmet"]
        assert later["state"] == "ambiguous"
        assert frame_reason in later["reasons"]

        summary = json.loads(row["association_json"])
        assert summary == observations["association"]
        helmet = summary["helmet"]
        assert helmet["state"] == "ambiguous"
        assert frame_reason in helmet["reasons"]
        assert "evidence_on_some_selected_frames_only" in helmet["reasons"]
        assert helmet["observations"] == later["observations"]
        assert "helmet:evidence_on_some_selected_frames_only" in json.loads(row["uncertainty_json"])

        client = _enforcer_client("md_xf_ambiguous", "md-xf-ambiguous-pass")
        item = next(
            i for i in client.get("/api/motorcycle-detail-review?outcome=all").get_json()["items"]
            if i["id"] == candidate_id
        )
        assert item["association"]["helmet"]["state"] == "ambiguous"
        body = client.get("/motorcycle-detail-review?outcome=all").get_data(as_text=True)
        assert _rendered_helmet(body) == "ambiguous"
        assert _protected_counts(test_db) == {t: 0 for t in _protected_counts(test_db)}

    @pytest.mark.parametrize(
        "scenarios,expected,four_class",
        _TWO_FRAME_TABLE,
        ids=["-".join(s) + ("-4c" if fc else "-3c") for s, _e, fc in _TWO_FRAME_TABLE],
    )
    def test_scanned_summary_follows_the_aggregation_table(
        self, test_db, video_id, evidence_root, scenarios, expected, four_class
    ):
        _candidate_id, row = _scan_frames(test_db, video_id, scenarios, four_class=four_class)
        observations = json.loads(row["observations_json"])
        frame_helmets = [f["association"]["helmet"] for f in observations["frames"]]
        assert [f["state"] for f in frame_helmets] == [_frame_state(s) for s in scenarios]
        for scenario, frame in zip(scenarios, observations["frames"]):
            if scenario == "unknown":
                assert frame["detections"] == []
                assert frame["association"]["helmet"]["observations"] == []
        summary = json.loads(row["association_json"])
        assert summary == observations["association"]
        _check_summary_against_frames(summary["helmet"], frame_helmets, expected)
        uncertainty = json.loads(row["uncertainty_json"])
        if "contradictory_helmet_observations_across_frames" in summary["helmet"]["reasons"]:
            assert "helmet:contradictory_helmet_observations_across_frames" in uncertainty
        if not four_class:
            assert observations["scan"]["contract"] == "md-detail-3c-v1"
            assert observations["scan"]["no_helmet_policy"] == "class_not_in_contract"
            assert "uncovered_head" not in [f["state"] for f in frame_helmets]
            assert summary["helmet"]["state"] != "uncovered_head"
        assert _protected_counts(test_db) == {t: 0 for t in _protected_counts(test_db)}

    @pytest.mark.parametrize(
        "scenarios,expected",
        [(s, e) for s, e, _fc in _TWO_FRAME_TABLE] + _THREE_FRAME_TABLE,
        ids=["-".join(s) for s, _e, _fc in _TWO_FRAME_TABLE] + ["-".join(s) for s, _e in _THREE_FRAME_TABLE],
    )
    def test_aggregation_covers_every_selected_frame(self, scenarios, expected):
        summary, reason, associations = _function_summary(scenarios)
        frame_helmets = [a.helmet.as_dict() for a in associations]
        assert [f["state"] for f in frame_helmets] == [_frame_state(s) for s in scenarios]
        _check_summary_against_frames(summary.as_dict(), frame_helmets, expected)
        if reason is not None:
            assert reason in summary.reasons

    def test_no_detection_on_every_frame_stays_unknown_on_both_contracts(
        self, test_db, video_id, evidence_root
    ):
        for four_class, key in ((False, "t31g1"), (True, "t32g1")):
            _candidate_id, row = _scan_frames(
                test_db, video_id, ("unknown", "unknown"), four_class=four_class, occurrence_key=key
            )
            summary = json.loads(row["association_json"])
            assert summary["helmet"]["state"] == "unknown"
            assert summary["helmet"]["observations"] == []
            uncertainty = json.loads(row["uncertainty_json"])
            assert "helmet:no_observation_does_not_prove_absence" in uncertainty
            assert not any("selected_frames" in u for u in uncertainty)

    def test_cross_frame_scans_and_every_human_outcome_write_zero_protected_rows(
        self, test_db, video_id, evidence_root, app_workers
    ):
        baseline = _protected_counts(test_db)
        assert set(baseline.values()) == {0}
        ids = []
        for scenarios, key in (
            (("unknown", "uncovered_head"), "t41g1"),
            (("unknown", "ambiguous_labels"), "t42g1"),
            (("acceptable", "uncovered_head"), "t43g1"),
        ):
            candidate_id, _row = _scan_frames(
                test_db, video_id, scenarios, four_class=True, occurrence_key=key
            )
            ids.append(candidate_id)
            assert _protected_counts(test_db) == baseline
        client = _enforcer_client("md_xf_outcomes", "md-xf-outcomes-pass")
        for candidate_id, outcome in zip(ids, ("reviewed", "dismissed", "uncertain")):
            response = client.post(
                f"/api/motorcycle-detail-review/{candidate_id}/outcome", json={"outcome": outcome}
            )
            assert response.status_code == 200, response.get_data(as_text=True)
            assert test_db.get_motorcycle_detail_candidate(candidate_id)["human_outcome"] == outcome
        assert client.post(
            f"/api/motorcycle-detail-review/{ids[0]}/outcome", json={"outcome": "confirmed"}
        ).status_code == 400
        assert _protected_counts(test_db) == baseline
        assert test_db.count_review_pending() == 0
