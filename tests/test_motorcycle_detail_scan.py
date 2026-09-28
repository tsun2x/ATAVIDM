"""Motorcycle detail crop scan: checkpoint gate, mapping, association, boundaries."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from core.motorcycle_detail_scan import (
    DetailCheckpoint,
    DetailScanError,
    MotorcycleDetailScanner,
    associate_scan_detections,
    to_pipeline_detection,
)

EXPECTED_15 = (
    "car", "van", "jeepney", "tricycle", "autorickshaw", "bus", "truck",
    "pickup_truck", "motorcycle", "bicycle", "person", "rider",
    "helmet_acceptable", "helmet_nut_shell", "side_mirror",
)


def _model(names):
    return SimpleNamespace(names=names)


def _ok_checkpoint(loader_calls=None):
    def loader(path):
        if loader_calls is not None:
            loader_calls.append(path)
        return _model({i: n for i, n in enumerate(EXPECTED_15)})

    return loader


@pytest.fixture(autouse=True)
def _clear_model_cache():
    from core.motorcycle_detail_scan import clear_model_cache

    clear_model_cache()
    yield
    clear_model_cache()


# ---------------------------------------------------------------------------
# Checkpoint resolution and the exact 15-class contract
# ---------------------------------------------------------------------------


class TestCheckpointGate:
    def test_exact_fifteen_class_order_is_accepted(self, tmp_path):
        from core.motorcycle_detail_scan import load_detail_checkpoint

        weights = tmp_path / "detail.pt"
        weights.write_bytes(b"mock-weights")
        cp = load_detail_checkpoint(str(weights), model_loader=_ok_checkpoint())
        assert cp.ok is True
        assert cp.class_names == EXPECTED_15
        assert cp.identity.startswith("YOLOv8m:detail.pt:")

    def test_missing_designation_fails_closed(self):
        from core.motorcycle_detail_scan import load_detail_checkpoint

        cp = load_detail_checkpoint(None, model_loader=_ok_checkpoint())
        # No designation is only a gate when nothing is configured at all.
        assert cp.ok in (True, False)
        if not cp.ok:
            assert cp.reason == "no_designated_detail_checkpoint"

    def test_missing_file_fails_closed(self, tmp_path):
        from core.motorcycle_detail_scan import load_detail_checkpoint

        cp = load_detail_checkpoint(str(tmp_path / "nope.pt"), model_loader=_ok_checkpoint())
        assert cp.ok is False
        assert cp.reason == "detail_checkpoint_missing"

    def test_unreadable_checkpoint_fails_closed(self, tmp_path):
        from core.motorcycle_detail_scan import load_detail_checkpoint

        weights = tmp_path / "broken.pt"
        weights.write_bytes(b"x")

        def boom(path):
            raise RuntimeError("corrupt checkpoint")

        cp = load_detail_checkpoint(str(weights), model_loader=boom)
        assert cp.ok is False
        assert "detail_checkpoint_unreadable" in cp.reason

    def test_reordered_class_map_is_rejected(self, tmp_path):
        from core.motorcycle_detail_scan import load_detail_checkpoint

        weights = tmp_path / "swapped.pt"
        weights.write_bytes(b"mock")
        names = list(EXPECTED_15)
        names[12], names[13] = names[13], names[12]
        loader = lambda path: _model({i: n for i, n in enumerate(names)})  # noqa: E731
        cp = load_detail_checkpoint(str(weights), model_loader=loader)
        assert cp.ok is False
        assert "detail_class_map_rejected" in cp.reason

    def test_legacy_ten_class_checkpoint_is_rejected(self, tmp_path):
        from core.motorcycle_detail_scan import load_detail_checkpoint

        weights = tmp_path / "legacy.pt"
        weights.write_bytes(b"mock")
        names = EXPECTED_15[:10]
        loader = lambda path: _model({i: n for i, n in enumerate(names)})  # noqa: E731
        cp = load_detail_checkpoint(str(weights), model_loader=loader)
        assert cp.ok is False
        assert "detail_class_map_rejected" in cp.reason

    def test_missing_class_map_fails_closed(self, tmp_path):
        from core.motorcycle_detail_scan import load_detail_checkpoint

        weights = tmp_path / "empty.pt"
        weights.write_bytes(b"mock")
        loader = lambda path: _model({})  # noqa: E731
        cp = load_detail_checkpoint(str(weights), model_loader=loader)
        assert cp.ok is False
        assert "detail_class_map_rejected" in cp.reason

    def test_renamed_class_is_rejected(self, tmp_path):
        from core.motorcycle_detail_scan import load_detail_checkpoint

        weights = tmp_path / "renamed.pt"
        weights.write_bytes(b"mock")
        names = list(EXPECTED_15)
        names[11] = "cyclist"  # 'rider' renamed
        loader = lambda path: _model({i: n for i, n in enumerate(names)})  # noqa: E731
        cp = load_detail_checkpoint(str(weights), model_loader=loader)
        assert cp.ok is False
        assert "detail_class_map_rejected" in cp.reason

    def test_validate_helper_reports_exact_order(self):
        from core.motorcycle_detail_scan import validate_detail_class_map

        names, error = validate_detail_class_map({i: n for i, n in enumerate(EXPECTED_15)})
        assert error is None and names == EXPECTED_15
        swapped = list(EXPECTED_15)
        swapped[0], swapped[1] = swapped[1], swapped[0]
        _, error = validate_detail_class_map({i: n for i, n in enumerate(swapped)})
        assert error is not None


# ---------------------------------------------------------------------------
# Association of scan detections (mapped into source space)
# ---------------------------------------------------------------------------


BIKE_BOX = (100.0, 50.0, 160.0, 130.0)  # x1, y1, x2, y2


def _d(label, box, conf=0.8):
    return to_pipeline_detection(label, conf, box)


class TestScanAssociation:
    def test_rider_and_acceptable_helmet_are_associated(self):
        rider = _d("rider", (110.0, 10.0, 150.0, 100.0))
        helmet = _d("helmet_acceptable", (115.0, 5.0, 145.0, 35.0))
        result = associate_scan_detections(
            [rider, helmet], motorcycle_box=BIKE_BOX, frame_w=640, frame_h=480
        )
        assert result.rider.state == "associated"
        assert result.helmet.state == "acceptable"

    def test_person_is_never_used_as_a_rider(self):
        person = _d("person", (110.0, 10.0, 150.0, 100.0))
        helmet = _d("helmet_acceptable", (115.0, 5.0, 145.0, 35.0))
        result = associate_scan_detections(
            [person, helmet], motorcycle_box=BIKE_BOX, frame_w=640, frame_h=480
        )
        assert result.rider.state == "unassociated"
        assert result.helmet.state == "unknown"
        assert "helmet:rider_not_associated" in result.uncertainty

    def test_missing_mirror_is_unknown_not_absence(self):
        result = associate_scan_detections(
            [], motorcycle_box=BIKE_BOX, frame_w=640, frame_h=480
        )
        assert result.mirror.state == "none_visible"
        assert "mirror:absence_not_proven_unknown" in result.uncertainty
        assert result.mirror.observations == ()

    def test_two_mirrors_map_to_both_mounting_areas(self):
        left = _d("side_mirror", (102.0, 55.0, 116.0, 65.0))
        right = _d("side_mirror", (144.0, 55.0, 158.0, 65.0))
        result = associate_scan_detections(
            [left, right], motorcycle_box=BIKE_BOX, frame_w=640, frame_h=480
        )
        assert result.mirror.state == "both_visible"

    def test_target_redetection_inside_its_own_crop_is_not_an_overlap(self):
        # A padded crop usually re-detects the very motorcycle it came from
        # (IoU 0.77 here). That is the *same object*, not a second bike, so the
        # mirror is attributed instead of being reported ambiguous.
        left = _d("side_mirror", (102.0, 55.0, 116.0, 65.0))
        self_bike = _d("motorcycle", (108.0, 50.0, 168.0, 130.0))
        result = associate_scan_detections(
            [left, self_bike], motorcycle_box=BIKE_BOX, frame_w=640, frame_h=480
        )
        assert result.mirror.state == "one_left"
        assert len(result.mirror.observations) == 1

    def test_overlapping_motorcycles_make_mirrors_ambiguous(self):
        left = _d("side_mirror", (102.0, 55.0, 116.0, 65.0))
        # A *different* bike beside the target: IoU 0.21 with the target box,
        # i.e. above the ambiguity floor (0.20) but below the target-match
        # threshold (0.30), so it cannot be the target's own re-detection.
        other_bike = _d("motorcycle", (139.0, 50.0, 199.0, 130.0))
        result = associate_scan_detections(
            [left, other_bike], motorcycle_box=BIKE_BOX, frame_w=640, frame_h=480
        )
        assert result.mirror.state == "ambiguous"
        assert result.mirror.observations == ()
        assert "mirror:overlapping_motorcycles_mounting_area_unresolved" in result.uncertainty

    def test_to_pipeline_detection_uses_xywh(self):
        det = to_pipeline_detection("rider", 0.5, (10.0, 20.0, 40.0, 70.0))
        assert (det["bbox_x"], det["bbox_y"]) == (10.0, 20.0)
        assert (det["bbox_w"], det["bbox_h"]) == (30.0, 50.0)
        assert det["class_label"] == "rider"


def _write_crop(root, name, w, h, colour=90):
    import cv2

    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    assert cv2.imwrite(str(path), np.full((h, w, 3), colour, dtype=np.uint8))
    return str(path)


def _row(crop_path, *, crop, scale_x=1.0, scale_y=1.0, source_w=640, source_h=480):
    return {
        "id": 1,
        "run_key": "run_1",
        "occurrence_key": "t7g1",
        "frame_number": 10,
        "source_width": source_w,
        "source_height": source_h,
        "frames_json": json.dumps(
            [
                {
                    "index": 1,
                    "frame_number": 10,
                    "crop": crop,
                    "crop_path": crop_path,
                    "scene_path": None,
                    "scan_scale_x": scale_x,
                    "scan_scale_y": scale_y,
                    "detection_bbox": {
                        "x": crop["x"] + crop["w"] * 0.2,
                        "y": crop["y"] + crop["h"] * 0.3,
                        "w": crop["w"] * 0.5,
                        "h": crop["h"] * 0.5,
                    },
                }
            ]
        ),
    }


def _mock_scanner(predictor):
    """Scanner with a mocked checkpoint and a mocked crop predictor."""
    checkpoint = DetailCheckpoint(
        path="mock", class_names=EXPECTED_15, ok=True, identity="YOLOv8m:mock"
    )
    return MotorcycleDetailScanner(
        checkpoint_loader=lambda: checkpoint,
        predictor_factory=lambda cp: predictor,
    )


# ---------------------------------------------------------------------------
# Coordinate mapping through the scan, including edge-clamped crops
# ---------------------------------------------------------------------------


class TestScanMapping:
    def test_scan_boxes_are_mapped_into_source_coordinates(self, tmp_path, monkeypatch):
        import config

        root = tmp_path / "evidence"
        monkeypatch.setattr(config, "EVIDENCE_FOLDER", str(root))
        crop_path = _write_crop(root / "run_1" / "t7g1", "crop_f1.jpg", 40, 40)
        crop = {"x": 200, "y": 100, "w": 40, "h": 40}
        row = _row(crop_path, crop=crop)
        seen = {}

        def predictor(image):
            seen["shape"] = image.shape
            # A box covering the top-left quadrant of the crop, in crop pixels.
            return [
                {"class_label": "helmet_acceptable", "confidence": 0.7,
                 "bbox": (0.0, 0.0, 20.0, 20.0)}
            ]

        scanner = _mock_scanner(predictor)
        result = scanner._scan_candidate(
            row, scanner.checkpoint(), scanner._predictor(scanner.checkpoint())
        )
        observations = json.loads(result["observations_json"])
        detection = observations["frames"][0]["detections"][0]
        # crop origin (200,100) + local box (0,0)-(20,20)
        assert detection["source_bbox"][0] == pytest.approx(200.0)
        assert detection["source_bbox"][1] == pytest.approx(100.0)
        assert detection["source_bbox"][2] == pytest.approx(220.0)
        assert detection["source_bbox"][3] == pytest.approx(120.0)
        assert observations["frames"][0]["overlay_path"] is not None

    def test_resized_scan_scale_is_divided_out(self, tmp_path, monkeypatch):
        import config

        root = tmp_path / "evidence"
        monkeypatch.setattr(config, "EVIDENCE_FOLDER", str(root))
        crop_path = _write_crop(root / "run_1" / "t7g1", "crop_f1.jpg", 40, 40)
        crop = {"x": 10, "y": 20, "w": 40, "h": 40}
        row = _row(crop_path, crop=crop, scale_x=2.0, scale_y=2.0)
        seen = {}

        def predictor(image):
            seen["shape"] = image.shape
            # Measured on the 2x-upscaled crop.
            return [
                {"class_label": "side_mirror", "confidence": 0.5,
                 "bbox": (10.0, 10.0, 30.0, 30.0)}
            ]

        scanner = _mock_scanner(predictor)
        result = scanner._scan_candidate(
            row, scanner.checkpoint(), scanner._predictor(scanner.checkpoint())
        )
        assert seen["shape"][0] == 80 and seen["shape"][1] == 80
        bbox = json.loads(result["observations_json"])["frames"][0]["detections"][0]["source_bbox"]
        # crop origin (10,20) + local (10/2,10/2)-(30/2,30/2)
        assert bbox[0] == pytest.approx(15.0)
        assert bbox[1] == pytest.approx(25.0)
        assert bbox[2] == pytest.approx(25.0)
        assert bbox[3] == pytest.approx(35.0)

    def test_edge_clamped_crop_still_yields_in_frame_boxes(self, tmp_path, monkeypatch):
        import config

        root = tmp_path / "evidence"
        monkeypatch.setattr(config, "EVIDENCE_FOLDER", str(root))
        crop_path = _write_crop(root / "run_1" / "t7g1", "crop_f1.jpg", 30, 30)
        crop = {"x": 0, "y": 0, "w": 30, "h": 30}
        row = _row(crop_path, crop=crop)

        def predictor(image):
            # A detection partly outside the crop, as a real model may report.
            return [
                {"class_label": "helmet_nut_shell", "confidence": 0.4,
                 "bbox": (-10.0, -5.0, 45.0, 40.0)}
            ]

        scanner = _mock_scanner(predictor)
        result = scanner._scan_candidate(
            row, scanner.checkpoint(), scanner._predictor(scanner.checkpoint())
        )
        observations = json.loads(result["observations_json"])
        bbox = observations["frames"][0]["detections"][0]["source_bbox"]
        assert bbox[0] >= 0.0 and bbox[1] >= 0.0
        assert bbox[2] <= 640.0 and bbox[3] <= 480.0
        assert "crop_clamped_at_frame_edge" in observations["frames"][0]["uncertainty"]

    def test_missing_crop_fails_the_scan(self, tmp_path, monkeypatch):
        import config

        root = tmp_path / "evidence"
        monkeypatch.setattr(config, "EVIDENCE_FOLDER", str(root))
        row = _row(str(root / "nope.jpg"), crop={"x": 0, "y": 0, "w": 30, "h": 30})
        scanner = _mock_scanner(lambda image: [])
        with pytest.raises(DetailScanError, match="crop_unreadable"):
            scanner._scan_candidate(
                row, scanner.checkpoint(), scanner._predictor(scanner.checkpoint())
            )

    def test_crop_size_mismatch_refuses_to_map(self, tmp_path, monkeypatch):
        import config

        root = tmp_path / "evidence"
        monkeypatch.setattr(config, "EVIDENCE_FOLDER", str(root))
        crop_path = _write_crop(root / "run_1" / "t7g1", "crop_f1.jpg", 20, 20)
        # Recorded rectangle disagrees with the stored file.
        row = _row(crop_path, crop={"x": 0, "y": 0, "w": 30, "h": 30})
        scanner = _mock_scanner(lambda image: [])
        with pytest.raises(DetailScanError, match="size_mismatch"):
            scanner._scan_candidate(
                row, scanner.checkpoint(), scanner._predictor(scanner.checkpoint())
            )


# ---------------------------------------------------------------------------
# Scheduling: the shared GPU reservation, queue state, and start-up recovery
# ---------------------------------------------------------------------------


class _FakeAdapter:
    """Minimal adapter double recording what the scanner asked the database."""

    def __init__(self, rows=(), *, queued=1):
        self.rows = list(rows)
        self.queued = queued
        self.claims: list[int] = []
        self.finished: list[int] = []
        self.failures: list[tuple[int, str]] = []
        self.recover_calls: list[bool] = []
        self.gate_notes: list[str] = []
        self.crop_predicted = 0

    def count_queued_motorcycle_detail_scans(self):
        return self.queued

    def claim_motorcycle_detail_scans(self, *, limit):
        batch = self.rows[:limit]
        self.claims.append(len(batch))
        return batch

    def finish_motorcycle_detail_scan(self, *, candidate_id, **kwargs):
        self.finished.append(candidate_id)

    def record_motorcycle_detail_scan_failure(self, *, candidate_id, error, max_attempts):
        self.failures.append((candidate_id, error))
        return "failed"

    def recover_stale_motorcycle_detail_scans(self, *, stale_sec, max_attempts, include_recent=False):
        self.recover_calls.append(bool(include_recent))
        return 0

    def note_motorcycle_detail_scan_gate(self, *, reason, limit=500):
        self.gate_notes.append(reason)
        return 1


def _scanner_with(adapter, predictor, **kwargs):
    checkpoint = DetailCheckpoint(
        path="mock", class_names=EXPECTED_15, ok=True, identity="YOLOv8m:mock"
    )
    kwargs.setdefault("checkpoint_fingerprint", lambda: "fp-1")
    kwargs.setdefault("gpu_wait_sec", 0.0)
    return MotorcycleDetailScanner(
        adapter=adapter,
        checkpoint_loader=lambda: checkpoint,
        predictor_factory=lambda cp: predictor,
        **kwargs,
    )


def _tracking_predictor(counter):
    def predictor(image):
        counter.append(1)
        return []

    return predictor


class TestSharedGpuReservation:
    def test_busy_gpu_skips_the_batch_without_claiming_or_spending_an_attempt(
        self, tmp_path, monkeypatch
    ):
        import config
        from core.gpu_inference_slot import GpuInferenceSlot

        monkeypatch.setattr(config, "EVIDENCE_FOLDER", str(tmp_path / "evidence"))
        adapter = _FakeAdapter(rows=[])
        calls = []
        scanner = _scanner_with(adapter, _tracking_predictor(calls), gpu_slot=GpuInferenceSlot())
        held = scanner.gpu_slot.acquire("video-job:1", timeout=0.0, priority=0)
        try:
            report = scanner.run_once()
        finally:
            scanner.gpu_slot.release(held)
        assert report["skipped"] == "gpu_slot_busy"
        # Nothing may be claimed: a busy device must not burn a retry attempt.
        assert adapter.claims == []
        assert calls == []
        assert scanner.gpu_slot.is_free()

    def test_batch_holds_the_reservation_for_every_crop_and_releases_it(
        self, tmp_path, monkeypatch
    ):
        import config
        from core.gpu_inference_slot import GpuInferenceSlot

        monkeypatch.setattr(config, "EVIDENCE_FOLDER", str(tmp_path / "evidence"))
        root = tmp_path / "evidence"
        crop_path = _write_crop(root / "run_1" / "t7g1", "crop_f1.jpg", 40, 40)
        slot = GpuInferenceSlot()
        observed: list[str | None] = []

        def predictor(image):
            observed.append(slot.holder())
            return []

        adapter = _FakeAdapter(rows=[_row(crop_path, crop={"x": 0, "y": 0, "w": 40, "h": 40})])
        scanner = _scanner_with(adapter, predictor, gpu_slot=slot)
        report = scanner.run_once()
        assert report["claimed"] == 1 and report["ready"] == 1
        # The whole batch ran inside the reservation, and it was handed back.
        assert observed == [scanner._slot_owner]
        assert slot.is_free()
        assert slot.waiter_count() == 0

    def test_empty_queue_never_touches_the_database_claim(self, tmp_path, monkeypatch):
        import config
        from core.gpu_inference_slot import GpuInferenceSlot

        monkeypatch.setattr(config, "EVIDENCE_FOLDER", str(tmp_path / "evidence"))
        adapter = _FakeAdapter(rows=[], queued=0)
        scanner = _scanner_with(adapter, lambda image: [], gpu_slot=GpuInferenceSlot())
        report = scanner.run_once()
        assert report == {
            "claimed": 0,
            "ready": 0,
            "deferred": 0,
            "failed": 0,
            "skipped": None,
            "gate_reason": None,
        }
        assert adapter.claims == []

    def test_failed_crop_releases_the_reservation_and_reports_failure(
        self, tmp_path, monkeypatch
    ):
        import config
        from core.gpu_inference_slot import GpuInferenceSlot

        monkeypatch.setattr(config, "EVIDENCE_FOLDER", str(tmp_path / "evidence"))
        adapter = _FakeAdapter(rows=[_row("missing.jpg", crop={"x": 0, "y": 0, "w": 30, "h": 30})])
        slot = GpuInferenceSlot()
        scanner = _scanner_with(adapter, lambda image: [], gpu_slot=slot)
        report = scanner.run_once()
        assert report["failed"] == 1
        assert adapter.failures and adapter.failures[0][0] == 1
        assert slot.is_free()


class TestStartupRecovery:
    def test_first_pass_recovers_abandoned_scans_without_a_new_video(
        self, tmp_path, monkeypatch
    ):
        import config
        from core.gpu_inference_slot import GpuInferenceSlot

        monkeypatch.setattr(config, "EVIDENCE_FOLDER", str(tmp_path / "evidence"))
        adapter = _FakeAdapter(rows=[], queued=0)
        scanner = _scanner_with(adapter, lambda image: [], gpu_slot=GpuInferenceSlot())
        scanner.recover_abandoned(include_recent=True)
        scanner.run_once()
        # Recovery happens once, and the ordinary stale-only window is used after.
        assert adapter.recover_calls == [True]

    def test_recovery_is_idempotent_across_passes(self, tmp_path, monkeypatch):
        import config
        from core.gpu_inference_slot import GpuInferenceSlot

        monkeypatch.setattr(config, "EVIDENCE_FOLDER", str(tmp_path / "evidence"))
        adapter = _FakeAdapter(rows=[], queued=0)
        scanner = _scanner_with(adapter, lambda image: [], gpu_slot=GpuInferenceSlot())
        for _ in range(5):
            scanner.run_once()
        # One stale-only pass; later passes are free, and a second explicit
        # call does not re-run recovery.
        assert adapter.recover_calls == [False]
        assert scanner.recover_abandoned() == 0
        assert adapter.recover_calls == [False]

    def test_recovery_runs_before_the_checkpoint_gate(self, tmp_path, monkeypatch):
        """A crash must be resumable even with no designated checkpoint."""
        import config
        from core.gpu_inference_slot import GpuInferenceSlot

        monkeypatch.setattr(config, "EVIDENCE_FOLDER", str(tmp_path / "evidence"))
        adapter = _FakeAdapter(rows=[], queued=0)
        unavailable = DetailCheckpoint(
            path="", class_names=(), ok=False, identity="unavailable", reason="not_designated"
        )
        scanner = MotorcycleDetailScanner(
            adapter=adapter,
            checkpoint_loader=lambda: unavailable,
            checkpoint_fingerprint=lambda: "fp-1",
            predictor_factory=lambda cp: (lambda image: []),
            gpu_slot=GpuInferenceSlot(),
        )
        report = scanner.run_once()
        assert report["skipped"] == "detail_checkpoint_unavailable"
        assert adapter.recover_calls == [False]
        assert adapter.gate_notes == ["not_designated"]

    def test_startup_recovery_reaches_the_database(self, tmp_path, monkeypatch):
        """``bootstrap_detail_queue`` must resume rows from a dead process."""
        import config
        from core.gpu_inference_slot import GpuInferenceSlot

        monkeypatch.setattr(config, "EVIDENCE_FOLDER", str(tmp_path / "evidence"))
        adapter = _FakeAdapter(rows=[], queued=0)
        scanner = _scanner_with(adapter, lambda image: [], gpu_slot=GpuInferenceSlot())
        assert scanner.recover_abandoned(include_recent=True) == 0
        assert adapter.recover_calls == [True]

    def test_startup_recovery_still_runs_after_an_early_stale_only_pass(
        self, tmp_path, monkeypatch
    ):
        """The loop's first pass must not consume the one-shot start-up pass.

        A thread that runs ``run_once()`` before the process-level start-up
        recovery would perform the stale-only pass, which skips rows that are
        still inside the stale window -- exactly the rows a just-dead process
        abandoned. The broader start-up pass must therefore still be allowed.
        """
        import config
        from core.gpu_inference_slot import GpuInferenceSlot

        monkeypatch.setattr(config, "EVIDENCE_FOLDER", str(tmp_path / "evidence"))
        adapter = _FakeAdapter(rows=[], queued=0)
        scanner = _scanner_with(adapter, lambda image: [], gpu_slot=GpuInferenceSlot())
        scanner.run_once()
        assert adapter.recover_calls == [False]
        # Start-up recovery is still permitted, and then is itself one-shot.
        scanner.recover_abandoned(include_recent=True)
        assert adapter.recover_calls == [False, True]
        scanner.recover_abandoned(include_recent=True)
        assert scanner.recover_abandoned() == 0
        assert adapter.recover_calls == [False, True]

    def test_failed_startup_recovery_remains_retryable(self, tmp_path, monkeypatch):
        import config
        from core.gpu_inference_slot import GpuInferenceSlot

        monkeypatch.setattr(config, "EVIDENCE_FOLDER", str(tmp_path / "evidence"))

        class _TransientAdapter(_FakeAdapter):
            def recover_stale_motorcycle_detail_scans(
                self, *, stale_sec, max_attempts, include_recent=False
            ):
                self.recover_calls.append(bool(include_recent))
                if len(self.recover_calls) == 1:
                    raise RuntimeError("temporary database error")
                return 1

        adapter = _TransientAdapter(rows=[], queued=0)
        scanner = _scanner_with(adapter, lambda image: [], gpu_slot=GpuInferenceSlot())
        assert scanner.recover_abandoned(include_recent=True) is None
        assert scanner.recover_abandoned(include_recent=True) == 1
        assert adapter.recover_calls == [True, True]


class TestCheckpointFingerprint:
    def test_unavailable_checkpoint_is_revalidated_when_its_designation_changes(
        self, tmp_path, monkeypatch
    ):
        import config
        from core.gpu_inference_slot import GpuInferenceSlot

        monkeypatch.setattr(config, "EVIDENCE_FOLDER", str(tmp_path / "evidence"))
        root = tmp_path / "evidence"
        crop_path = _write_crop(root / "run_1" / "t7g1", "crop_f1.jpg", 40, 40)
        adapter = _FakeAdapter(
            rows=[_row(crop_path, crop={"x": 0, "y": 0, "w": 40, "h": 40})]
        )
        designated = {"value": ""}
        unavailable = DetailCheckpoint(
            path="", class_names=(), ok=False, identity="unavailable", reason="not_designated"
        )
        ready = DetailCheckpoint(
            path="mock", class_names=EXPECTED_15, ok=True, identity="YOLOv8m:mock"
        )
        scanner = MotorcycleDetailScanner(
            adapter=adapter,
            checkpoint_loader=lambda: ready if designated["value"] else unavailable,
            checkpoint_fingerprint=lambda: designated["value"] or "none",
            predictor_factory=lambda cp: (lambda image: []),
            gpu_slot=GpuInferenceSlot(),
        )
        first = scanner.run_once()
        assert first["skipped"] == "detail_checkpoint_unavailable"
        assert adapter.claims == []

        # An operator designates a checkpoint: the stale "unavailable" cache must
        # not strand the queued candidate.
        designated["value"] = "runs/detail/best.pt"
        second = scanner.run_once()
        assert second["skipped"] is None
        assert adapter.claims == [1]
        assert adapter.finished == [1]

    def test_rewritten_checkpoint_file_is_not_served_from_the_model_cache(
        self, tmp_path, monkeypatch
    ):
        from core.motorcycle_detail_scan import (
            clear_model_cache,
            designated_weights_fingerprint,
            load_detail_checkpoint,
        )
        import config

        weights = tmp_path / "best.pt"
        weights.write_bytes(b"generation-1")
        monkeypatch.setattr(
            config, "MOTORCYCLE_DETAIL_WEIGHTS", str(weights), raising=False
        )
        loads: list[str] = []

        def model_loader(path):
            loads.append(path)
            return _model({i: n for i, n in enumerate(EXPECTED_15)})

        first = load_detail_checkpoint(str(weights), model_loader=model_loader)
        assert first.ok
        stamp_before = designated_weights_fingerprint()
        clear_model_cache()

        # Same path, different bytes: the designation fingerprint includes the
        # file stamp, so a replaced/corrected checkpoint is re-validated and
        # never served from the model cache.
        weights.write_bytes(b"generation-2-longer")
        assert designated_weights_fingerprint() != stamp_before
        second = load_detail_checkpoint(str(weights), model_loader=model_loader)
        assert second.ok
        assert len(loads) == 2
        clear_model_cache()

    def test_cached_model_is_reused_while_the_checkpoint_is_unchanged(self, tmp_path):
        from core.motorcycle_detail_scan import cached_model

        weights = tmp_path / "best.pt"
        weights.write_bytes(b"stable-weights")
        loads: list[str] = []

        def model_loader(path):
            loads.append(path)
            return _model({i: n for i, n in enumerate(EXPECTED_15)})

        first = cached_model(str(weights), model_loader)
        second = cached_model(str(weights), model_loader)
        assert first is second
        assert len(loads) == 1
