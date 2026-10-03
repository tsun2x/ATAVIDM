"""Focused tests for the experimental local plate detection/OCR feature.

Isolation contract for this module
----------------------------------
* The canonical user database is never touched: the shared ``test_db`` /
  ``client`` fixtures point ``SQLITE_PATH`` at a temporary file *before*
  ``database`` is imported.
* Machine crops and manifests go to a temporary directory through
  ``TAVIDM_PLATE_OCR_EVIDENCE_ROOT``; the real evidence root is never written.
* No real footage and no real model weights are used here. Real-ONNX and
  offline-socket behaviour lives in ``tests/test_plate_ocr_offline.py``.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from core import plate_manifest as manifests
from core.plate_jobs import (
    CropMemoryBudget,
    PlateAttemptOutcome,
    PlateInferenceWorker,
    PlateObservationCollector,
    select_primary_candidate,
)
from core.plate_manifest import PlateManifestError
from core.plate_onnx_backend import (
    PlateBackendUnavailable,
    PlateOnnxBackend,
    crop_from_box,
    check_plate_quality,
    iou,
    letterbox_detector_input,
    preprocess_ocr_input,
    unletterbox_box,
)
from core.plate_settings import (
    ALLOWED_PROVIDERS,
    CONTRACT_VERSION,
    DEFAULT_BOUNDS,
    DEFAULT_QUALITY,
    PlateOcrConfigError,
    PlateOcrSettings,
    demo_gate_status,
    load_plate_ocr_settings,
    verify_artifact_hashes,
)
from core.plate_review import current_attempt_for_review


def _jpeg_params(quality: int) -> list[int]:
    import cv2

    return [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)]


@pytest.fixture
def plate_admin_env(monkeypatch):
    """Bootstrap secret for the shared ``client`` fixture.

    Scoped to this module's HTTP tests only, so the canonical environment and
    every other test module are left untouched. The database is the shared
    temporary one; the canonical user database is never written.
    """
    monkeypatch.setenv("TAVIDM_BOOTSTRAP_ADMIN_PASSWORD", "isolated-plate-ocr-test-pw")
    return "isolated-plate-ocr-test-pw"


# ----------------------------------------------------------------------
# Fixtures / helpers
# ----------------------------------------------------------------------


@pytest.fixture
def plate_root_env(tmp_path, monkeypatch):
    root = tmp_path / "plate_evidence"
    root.mkdir()
    monkeypatch.setenv(manifests.ROOT_OVERRIDE_ENV_VAR, str(root))
    return root


def _write_artifact(directory: Path, name: str, content: bytes) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(content)
    return path


def enabled_settings(tmp_path: Path, **overrides: Any) -> PlateOcrSettings:
    detector = _write_artifact(tmp_path / "w", "det.onnx", b"detector-bytes")
    ocr = _write_artifact(tmp_path / "w", "ocr.onnx", b"ocr-bytes")
    cfg = _write_artifact(
        tmp_path / "w",
        "plate.yaml",
        b"max_plate_slots: 10\nalphabet: '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ_'\npad_char: '_'\n",
    )
    record = _write_artifact(
        tmp_path / "w", "evaluation.json", json.dumps({"result": "pass"}).encode("utf-8")
    )
    from core.plate_onnx_backend import sha256_file

    settings = PlateOcrSettings(
        enabled=True,
        disabled_reason="",
        provider="CPUExecutionProvider",
        detector_path=str(detector),
        detector_sha256=sha256_file(detector),
        ocr_path=str(ocr),
        ocr_sha256=sha256_file(ocr),
        ocr_config_path=str(cfg),
        ocr_config_sha256=sha256_file(cfg),
        evaluation_record_path=str(record),
    )
    return _replace(settings, **overrides) if overrides else settings


def _replace(settings: PlateOcrSettings, **overrides: Any) -> PlateOcrSettings:
    from dataclasses import replace

    return replace(settings, **overrides)


#: Fake detector box in vehicle-crop coordinates (see make_vehicle_frame).
FAKE_PLATE_BOX = (20, 10, 120, 40)


class FakeBox:
    def __init__(self, x1, y1, x2, y2, score=0.8, label="License Plate"):
        self.x1, self.y1, self.x2, self.y2 = int(x1), int(y1), int(x2), int(y2)
        self.score = float(score)
        self.label = label

    @property
    def width(self):
        return self.x2 - self.x1

    @property
    def height(self):
        return self.y2 - self.y1

    def as_plate_bounding_box(self):
        from core.plate_processing import PlateBoundingBox

        return PlateBoundingBox(self.x1, self.y1, self.x2, self.y2)


class FakeRead:
    def __init__(self, text, char_scores=None):
        self.text = text
        self.char_scores = char_scores

    @property
    def mean_score(self):
        if not self.char_scores:
            return None
        return sum(self.char_scores) / len(self.char_scores)

    @property
    def min_score(self):
        return min(self.char_scores) if self.char_scores else None

    @property
    def is_empty(self):
        return not self.text


class FakeBackend:
    """Deterministic stand-in for :class:`PlateOnnxBackend`."""

    def __init__(self, settings=None, boxes=None, reads=None, errors=None, delay=0.0):
        self.settings = settings
        self._boxes = boxes if boxes is not None else [FakeBox(*FAKE_PLATE_BOX)]
        self._reads = reads if reads is not None else [FakeRead("ABC123", [4.0] * 6)]
        self.errors = errors or {}
        self.delay = delay
        self.detector_calls = 0
        self.ocr_calls = 0
        self.detect_shapes: list[tuple[int, ...]] = []
        self.ocr_shapes: list[tuple[int, ...]] = []
        self.runtime_ready = True

    def detect_plates(self, image, **kwargs):
        self.detector_calls += 1
        self.detect_shapes.append(tuple(image.shape))
        err = self.errors.get("detect")
        if err:
            raise err
        if self.delay:
            time.sleep(self.delay)
        return list(self._boxes)

    def recognize_plate(self, crop):
        self.ocr_calls += 1
        self.ocr_shapes.append(tuple(crop.shape))
        err = self.errors.get("ocr")
        if err:
            raise err
        reads = self._reads
        idx = min(self.ocr_calls - 1, len(reads) - 1)
        return reads[idx]

    def metadata(self):
        return {
            "backend": "fake",
            "requested_provider": "CPUExecutionProvider",
            "active_providers": ["CPUExecutionProvider"],
            "detector_artifact": {"sha256": "d" * 64},
            "ocr_artifact": {"sha256": "o" * 64, "ocr_config_sha256": "c" * 64},
            "runtime": {"onnxruntime_version": "fake"},
        }


def make_vehicle_frame(width=320, height=240):
    """A vehicle region at (80,100)-(240,200) holding one sharp plate.

    In crop coordinates (the vehicle box origin) the plate occupies
    (20,10)-(120,40), which is what the fake detector box refers to.
    """
    frame = np.full((height, width, 3), 40, np.uint8)
    frame[100:200, 80:240] = (200, 200, 200)
    plate = make_plate_image(100, 30)
    frame[110:140, 100:200] = plate
    return frame


def make_plate_image(width=160, height=48):
    """A sharp, plate-shaped image with real high-frequency content."""
    import cv2

    plate = np.full((height, width, 3), 235, np.uint8)
    cv2.rectangle(plate, (1, 1), (width - 2, height - 2), (60, 60, 60), 2)
    cv2.putText(plate, "ABC 123", (10, height - 14), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (10, 10, 10), 2)
    return plate


def tracked(track_id=7, epoch=1, x=80, y=100, w=160, h=100):
    return {
        "track_id": track_id,
        "track_identity_epoch": epoch,
        "class_label": "car",
        "confidence": 0.9,
        "bbox_x": x,
        "bbox_y": y,
        "bbox_w": w,
        "bbox_h": h,
        "timestamp_sec": 0.0,
    }


def build_worker(settings, backend, limit_bytes=64 * 1024 * 1024):
    memory = CropMemoryBudget(limit_bytes)
    worker = PlateInferenceWorker(settings, backend, memory=memory)
    worker.start()
    return worker, memory


def drain(worker: PlateInferenceWorker, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if worker._queue.unfinished_tasks == 0:
            return True
        time.sleep(0.01)
    return False


# ----------------------------------------------------------------------
# 1. Disabled by default
# ----------------------------------------------------------------------


class TestDisabledByDefault:
    def test_no_config_env_means_disabled(self, monkeypatch):
        monkeypatch.delenv("TAVIDM_PLATE_OCR_CONFIG", raising=False)
        settings = load_plate_ocr_settings()
        assert settings.is_enabled is False
        assert settings.disabled_reason == "plate_ocr_disabled_by_default"

    def test_runtime_is_none_when_disabled(self, monkeypatch):
        from core import plate_runtime

        monkeypatch.delenv("TAVIDM_PLATE_OCR_CONFIG", raising=False)
        plate_runtime.reset_runtime()
        assert plate_runtime.get_runtime() is None
        report = plate_runtime.status_report()
        assert report["enabled"] is False
        assert report["state"] == "disabled"

    def test_enabled_config_does_not_start_runtime_without_explicit_mode(self, tmp_path, monkeypatch):
        from core import plate_runtime

        settings = enabled_settings(tmp_path)
        monkeypatch.setattr(plate_runtime, "load_plate_ocr_settings", lambda: settings)
        monkeypatch.delenv("TAVIDM_PLATE_OCR_DEMO", raising=False)
        monkeypatch.delenv("TAVIDM_PLATE_OCR_EVALUATION", raising=False)
        monkeypatch.setattr(plate_runtime, "PlateRuntime", lambda _settings: pytest.fail("runtime started"))
        plate_runtime.reset_runtime()
        try:
            assert plate_runtime.get_runtime() is None
            assert "explicit_isolated_mode_required" in plate_runtime.status_report()["reason"]
        finally:
            plate_runtime.reset_runtime()

    def test_demo_gate_failure_and_repository_paths_fail_closed(self, tmp_path, monkeypatch):
        from core import plate_runtime, plate_settings

        settings = enabled_settings(tmp_path)
        monkeypatch.setattr(plate_runtime, "load_plate_ocr_settings", lambda: settings)
        monkeypatch.setattr(plate_settings, "demo_gate_status", lambda _settings: {"ok": False, "reason": "failed_record"})
        monkeypatch.setattr(plate_runtime, "PlateRuntime", lambda _settings: pytest.fail("runtime started"))
        root = tmp_path / "isolated"
        root.mkdir()
        monkeypatch.setenv("TAVIDM_PLATE_OCR_DEMO", "1")
        monkeypatch.delenv("TAVIDM_PLATE_OCR_EVALUATION", raising=False)
        monkeypatch.setenv("TAVIDM_PLATE_OCR_ISOLATED_ROOT", str(root))
        monkeypatch.setenv("SQLITE_PATH", str(root / "db.sqlite"))
        monkeypatch.setenv("DATABASE_URL", str(root / "db.sqlite"))
        monkeypatch.setenv("EVIDENCE_FOLDER", str(root / "evidence"))
        monkeypatch.setenv("TAVIDM_PLATE_OCR_EVIDENCE_ROOT", str(root / "evidence" / "plate_ocr"))
        for name in ("UPLOAD_FOLDER", "FRAMES_FOLDER", "ANNOTATED_FOLDER", "REPORTS_FOLDER"):
            monkeypatch.setenv(name, str(root / name.lower()))
        plate_runtime.reset_runtime()
        try:
            assert plate_runtime.get_runtime() is None
            repo = Path(__file__).resolve().parents[1]
            monkeypatch.setenv("TAVIDM_PLATE_OCR_DEMO", "")
            monkeypatch.setenv("TAVIDM_PLATE_OCR_EVALUATION", "1")
            monkeypatch.setenv("TAVIDM_PLATE_OCR_ISOLATED_ROOT", str(repo))
            monkeypatch.setenv("SQLITE_PATH", str(repo / "database" / "canonical-probe.sqlite"))
            monkeypatch.setenv("DATABASE_URL", str(repo / "database" / "canonical-probe.sqlite"))
            monkeypatch.setenv("EVIDENCE_FOLDER", str(repo / "dataset" / "evidence"))
            assert plate_runtime._authorized_mode()[0] is None
        finally:
            plate_runtime.reset_runtime()

    def test_explicit_isolated_evaluation_mode_starts_mocked_runtime(self, tmp_path, monkeypatch):
        from core import plate_runtime

        settings = enabled_settings(tmp_path)
        root = tmp_path / "isolated"
        root.mkdir()
        monkeypatch.setenv("TAVIDM_PLATE_OCR_DEMO", "")
        monkeypatch.setenv("TAVIDM_PLATE_OCR_EVALUATION", "1")
        monkeypatch.setenv("TAVIDM_PLATE_OCR_ISOLATED_ROOT", str(root))
        monkeypatch.setenv("SQLITE_PATH", str(root / "db.sqlite"))
        monkeypatch.setenv("DATABASE_URL", str(root / "db.sqlite"))
        monkeypatch.setenv("EVIDENCE_FOLDER", str(root / "evidence"))
        monkeypatch.setenv("TAVIDM_PLATE_OCR_EVIDENCE_ROOT", str(root / "evidence" / "plate_ocr"))
        for name in ("UPLOAD_FOLDER", "FRAMES_FOLDER", "ANNOTATED_FOLDER", "REPORTS_FOLDER"):
            monkeypatch.setenv(name, str(root / name.lower()))
        assert plate_runtime._authorized_mode() == ("evaluation", None)
        monkeypatch.setattr(plate_runtime, "load_plate_ocr_settings", lambda: settings)

        class MockRuntime:
            def __init__(self, _settings):
                self.started = False
            def start(self):
                self.started = True
            def shutdown(self):
                self.started = False

        monkeypatch.setattr(plate_runtime, "PlateRuntime", MockRuntime)
        plate_runtime.reset_runtime()
        try:
            runtime = plate_runtime.get_runtime()
            assert runtime is not None and runtime.started is True
        finally:
            plate_runtime.reset_runtime()

    def test_runtime_rejects_inherited_external_manifest_override(self, tmp_path, monkeypatch):
        from core import plate_runtime

        isolated = tmp_path / "isolated"
        evidence = isolated / "evidence"
        isolated.mkdir()
        monkeypatch.setenv("TAVIDM_PLATE_OCR_DEMO", "")
        monkeypatch.setenv("TAVIDM_PLATE_OCR_EVALUATION", "1")
        monkeypatch.setenv("TAVIDM_PLATE_OCR_ISOLATED_ROOT", str(isolated))
        monkeypatch.setenv("SQLITE_PATH", str(isolated / "database.sqlite"))
        monkeypatch.setenv("DATABASE_URL", str(isolated / "database.sqlite"))
        monkeypatch.setenv("EVIDENCE_FOLDER", str(evidence))
        for name in ("UPLOAD_FOLDER", "FRAMES_FOLDER", "ANNOTATED_FOLDER", "REPORTS_FOLDER"):
            monkeypatch.setenv(name, str(isolated / name.lower()))
        plate_runtime.reset_runtime()
        try:
            for override in (tmp_path / "external", Path(__file__).resolve().parents[1] / "dataset" / "evidence"):
                monkeypatch.setenv("TAVIDM_PLATE_OCR_EVIDENCE_ROOT", str(override))
                mode, reason = plate_runtime._authorized_mode()
                assert mode is None
                assert reason == "plate_evidence_outside_isolated_evidence_root"
            monkeypatch.setenv("TAVIDM_PLATE_OCR_EVIDENCE_ROOT", str(evidence / "plate_ocr"))
            assert plate_runtime._authorized_mode() == ("evaluation", None)
            assert plate_runtime._validated_manifest_root((str(evidence / "plate_ocr" / "model.onnx"),))[1] == "plate_evidence_overlaps_protected_input"
            monkeypatch.setenv("SQLITE_PATH", str(evidence / "plate_ocr" / "database.sqlite"))
            assert plate_runtime._validated_manifest_root()[1] == "plate_evidence_overlaps_database"
            monkeypatch.setenv("SQLITE_PATH", str(isolated / "database.sqlite"))
            monkeypatch.setenv("TAVIDM_PLATE_OCR_EVIDENCE_ROOT", str(tmp_path / "external"))
            monkeypatch.setattr(plate_runtime, "PlateRuntime", lambda *_args: pytest.fail("runtime must not start"))
            assert plate_runtime.get_runtime() is None
        finally:
            plate_runtime.reset_runtime()

    def test_explicitly_disabled_config_file(self, tmp_path, monkeypatch):
        path = tmp_path / "plate.json"
        path.write_text(json.dumps({"enabled": False, "disabled_reason": "demo_off"}), "utf-8")
        monkeypatch.setenv("TAVIDM_PLATE_OCR_CONFIG", str(path))
        settings = load_plate_ocr_settings()
        assert settings.is_enabled is False
        assert settings.disabled_reason == "demo_off"

    def test_status_report_shape_when_disabled(self, monkeypatch):
        from core import plate_runtime

        monkeypatch.delenv("TAVIDM_PLATE_OCR_CONFIG", raising=False)
        plate_runtime.reset_runtime()
        report = plate_runtime.status_report()
        for key in ("experimental", "enabled", "state", "reason"):
            assert key in report
        assert report["experimental"] is True


# ----------------------------------------------------------------------
# 2. Configuration validation
# ----------------------------------------------------------------------


class TestConfigValidation:
    def _write_config(self, tmp_path: Path, **extra) -> Path:
        settings = enabled_settings(tmp_path / "src")
        base = Path(tmp_path / "cfg.json")
        payload = {
            "enabled": True,
            "provider": "CPUExecutionProvider",
            "detector_path": settings.detector_path,
            "detector_sha256": settings.detector_sha256,
            "ocr_path": settings.ocr_path,
            "ocr_sha256": settings.ocr_sha256,
            "ocr_config_path": settings.ocr_config_path,
            "ocr_config_sha256": settings.ocr_config_sha256,
            "evaluation_record": settings.evaluation_record_path,
        }
        payload.update(extra)
        base.write_text(json.dumps(payload), "utf-8")
        return base

    def test_valid_config_loads(self, tmp_path, monkeypatch):
        cfg = self._write_config(tmp_path)
        monkeypatch.setenv("TAVIDM_PLATE_OCR_CONFIG", str(cfg))
        settings = load_plate_ocr_settings()
        assert settings.is_enabled
        assert settings.provider in ALLOWED_PROVIDERS
        assert settings.config_sha256

    @pytest.mark.parametrize(
        "missing",
        [
            "detector_path",
            "detector_sha256",
            "ocr_path",
            "ocr_sha256",
            "ocr_config_path",
            "ocr_config_sha256",
            "provider",
            "evaluation_record",
        ],
    )
    def test_missing_required_key_fails_closed(self, tmp_path, monkeypatch, missing):
        cfg = self._write_config(tmp_path)
        payload = json.loads(cfg.read_text("utf-8"))
        payload.pop(missing)
        cfg.write_text(json.dumps(payload), "utf-8")
        monkeypatch.setenv("TAVIDM_PLATE_OCR_CONFIG", str(cfg))
        with pytest.raises(PlateOcrConfigError):
            load_plate_ocr_settings()

    def test_bad_hash_fails_closed(self, tmp_path, monkeypatch):
        cfg = self._write_config(tmp_path, detector_sha256="nothex")
        monkeypatch.setenv("TAVIDM_PLATE_OCR_CONFIG", str(cfg))
        with pytest.raises(PlateOcrConfigError):
            load_plate_ocr_settings()

    def test_short_hash_fails_closed(self, tmp_path, monkeypatch):
        cfg = self._write_config(tmp_path, ocr_sha256="ab")
        monkeypatch.setenv("TAVIDM_PLATE_OCR_CONFIG", str(cfg))
        with pytest.raises(PlateOcrConfigError):
            load_plate_ocr_settings()

    def test_auto_provider_rejected(self, tmp_path, monkeypatch):
        cfg = self._write_config(tmp_path, provider="auto")
        monkeypatch.setenv("TAVIDM_PLATE_OCR_CONFIG", str(cfg))
        with pytest.raises(PlateOcrConfigError):
            load_plate_ocr_settings()

    def test_unknown_provider_rejected(self, tmp_path, monkeypatch):
        cfg = self._write_config(tmp_path, provider="TensorrtExecutionProvider")
        monkeypatch.setenv("TAVIDM_PLATE_OCR_CONFIG", str(cfg))
        with pytest.raises(PlateOcrConfigError):
            load_plate_ocr_settings()

    def test_unknown_quality_key_rejected(self, tmp_path, monkeypatch):
        cfg = self._write_config(tmp_path, quality={"made_up_threshold": 1})
        monkeypatch.setenv("TAVIDM_PLATE_OCR_CONFIG", str(cfg))
        with pytest.raises(PlateOcrConfigError):
            load_plate_ocr_settings()

    def test_missing_artifact_file_rejected(self, tmp_path, monkeypatch):
        cfg = self._write_config(tmp_path)
        payload = json.loads(cfg.read_text("utf-8"))
        payload["detector_path"] = str(tmp_path / "nope.onnx")
        cfg.write_text(json.dumps(payload), "utf-8")
        monkeypatch.setenv("TAVIDM_PLATE_OCR_CONFIG", str(cfg))
        with pytest.raises(PlateOcrConfigError):
            load_plate_ocr_settings()

    def test_unreadable_config_rejected(self, tmp_path, monkeypatch):
        cfg = tmp_path / "broken.json"
        cfg.write_text("{not json", "utf-8")
        monkeypatch.setenv("TAVIDM_PLATE_OCR_CONFIG", str(cfg))
        with pytest.raises(PlateOcrConfigError):
            load_plate_ocr_settings()

    def test_default_bounds_and_quality_are_frozen(self, tmp_path, monkeypatch):
        cfg = self._write_config(tmp_path)
        monkeypatch.setenv("TAVIDM_PLATE_OCR_CONFIG", str(cfg))
        settings = load_plate_ocr_settings()
        assert settings.bounds == DEFAULT_BOUNDS
        assert settings.quality == DEFAULT_QUALITY
        assert settings.bounds["collection_window_sec"] == 2.0
        assert settings.bounds["sample_fps"] == 2.0
        assert settings.bounds["max_crops_per_observation"] == 3
        assert settings.bounds["max_detector_calls"] == 3
        assert settings.bounds["max_ocr_calls"] == 6
        assert settings.bounds["worker_count"] == 1
        assert settings.bounds["max_queued_jobs"] == 16
        assert settings.bounds["retained_crop_memory_budget_bytes"] == 64 * 1024 * 1024
        assert settings.bounds["schedule_budget_sec"] == 1.0


# ----------------------------------------------------------------------
# 3. Artifact qualification
# ----------------------------------------------------------------------


class TestArtifacts:
    def test_evaluator_environment_overrides_inherited_manifest_root(self, tmp_path, monkeypatch):
        import importlib.util

        monkeypatch.setenv("TAVIDM_PLATE_OCR_EVIDENCE_ROOT", str(tmp_path / "inherited-external"))
        path = Path(__file__).resolve().parents[1] / "scripts" / "plate_ocr_evaluate.py"
        spec = importlib.util.spec_from_file_location("plate_evaluator_env_under_test", path)
        evaluator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(evaluator)
        evidence = tmp_path / "isolated" / "evidence"
        evaluator.configure_environment(
            tmp_path / "isolated" / "database.sqlite", evidence, tmp_path / "config.json",
            tmp_path / "isolated", create_roots=False,
        )
        assert Path(os.environ["TAVIDM_PLATE_OCR_EVIDENCE_ROOT"]).resolve() == (evidence / "plate_ocr").resolve()

    def test_demo_launcher_sets_child_manifest_root_without_mutating_parent(self, tmp_path, monkeypatch):
        import importlib.util
        import sys
        import types

        from core import plate_settings

        config = tmp_path / "demo.json"
        config.write_text("{}", "utf-8")
        isolated = tmp_path / "demo-isolated"
        isolated.mkdir()
        settings = types.SimpleNamespace(
            is_enabled=True, provider="CPUExecutionProvider", config_path=str(config),
            evaluation_record_path=str(tmp_path / "evaluation.json"), artifact_paths=lambda: [],
        )
        monkeypatch.setattr(plate_settings, "load_plate_ocr_settings", lambda *_args: settings)
        monkeypatch.setattr(plate_settings, "verify_artifact_hashes", lambda _settings: [])
        monkeypatch.setattr(plate_settings, "demo_gate_status", lambda _settings: {"ok": True})
        monkeypatch.setitem(sys.modules, "onnxruntime", types.ModuleType("onnxruntime"))
        captured = {}
        monkeypatch.setattr("subprocess.run", lambda *_args, **kwargs: (captured.update(kwargs) or types.SimpleNamespace(returncode=0)))
        original = str(tmp_path / "inherited-external-plate-root")
        monkeypatch.setenv("TAVIDM_PLATE_OCR_EVIDENCE_ROOT", original)

        path = Path(__file__).resolve().parents[1] / "scripts" / "run_plate_ocr_demo.py"
        spec = importlib.util.spec_from_file_location("plate_demo_launcher_under_test", path)
        launcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(launcher)
        try:
            result = launcher.main([
                "--config", str(config), "--db", str(isolated / "demo.sqlite"),
                "--evidence-root", str(isolated / "evidence"), "--no-browser",
            ])
            assert result == 0
            assert captured["env"]["TAVIDM_PLATE_OCR_EVIDENCE_ROOT"] == str(isolated / "evidence" / "plate_ocr")
            assert os.environ["TAVIDM_PLATE_OCR_EVIDENCE_ROOT"] == original
        finally:
            import shutil
            shutil.rmtree(isolated, ignore_errors=True)

    def test_gate_rejects_incomplete_record_without_trusting_pass_label(self, tmp_path):
        import hashlib

        settings = _replace(enabled_settings(tmp_path), config_sha256="c" * 64)
        package_body = {"schema_version": "plate-ocr-frozen-units/1", "units": [{"unit_id": f"u{i}", "source": "video", "run_key": "run", "live_session_id": None, "track_id": i, "track_identity_epoch": 1} for i in range(10)], "frozen_units": {}}
        for i, unit in enumerate(package_body["units"]):
            unit["scored_candidate"] = {"ocr_raw": "ABC123" if i < 4 else "WRONG1"}
        package_sha = hashlib.sha256(json.dumps(package_body, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        package_body["frozen_package_sha256"] = package_sha
        package_path = tmp_path / "frozen_units.json"
        package_path.write_text(json.dumps(package_body), "utf-8")
        labels_path = tmp_path / "labels.json"
        labels_path.write_text(json.dumps({f"u{i}": {"readable": True, "uncertain": False, "text": "ABC123"} for i in range(10)}), "utf-8")
        record = {
            "schema_version": "plate-ocr-evaluation/1", "result": "pass",
            "model_hashes": settings.expected_hashes(), "config_sha256": settings.config_sha256,
            "settings_sha256": settings.config_sha256, "provider": settings.provider,
            "ocr_color_mode": settings.ocr_color_mode, "evaluation_set_id": package_sha,
            "frozen_package_sha256": package_sha, "frozen_package_path": str(package_path),
            "labels_path": str(labels_path), "labels_sha256": hashlib.sha256(labels_path.read_bytes()).hexdigest(),
            "counts": {"total_units": 10, "labels_complete": 10, "human_readable_denominator": 10, "exact_matches": 4, "human_unreadable": 0, "human_uncertain": 0, "human_unreadable_or_uncertain": 0},
            "read_rate": 0.4,
            "association_audit": {"audited": True, "unresolved_errors": 0, "results": [{"unit_id": f"u{i}", "status": "no_candidate"} for i in range(10)]},
            "verification_checks": {"offline": True, "isolation": True, "admin_authorization": True, "retry_preservation": True, "browser_review": True},
            "blockers": [],
        }
        path = Path(settings.evaluation_record_path)
        path.write_text(json.dumps(record), "utf-8")
        assert demo_gate_status(settings)["ok"] is True
        for key, value in (("read_rate", 0.0), ("blockers", ["license unresolved"])):
            altered = dict(record)
            altered[key] = value
            path.write_text(json.dumps(altered), "utf-8")
            assert demo_gate_status(settings)["ok"] is False

        for malformed in (None, 7, "package", True, []):
            package_path.write_text(json.dumps(malformed), "utf-8")
            result = demo_gate_status(settings)
            assert result["ok"] is False
            assert isinstance(result["reason"], str)

        malformed = {"units": [], "frozen_units": {}}
        malformed_sha = hashlib.sha256(json.dumps(malformed, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        malformed["frozen_package_sha256"] = malformed_sha
        package_path.write_text(json.dumps(malformed), "utf-8")
        malformed_record = dict(record, frozen_package_sha256=malformed_sha, evaluation_set_id=malformed_sha)
        path.write_text(json.dumps(malformed_record), "utf-8")
        result = demo_gate_status(settings)
        assert result["ok"] is False
        assert isinstance(result["reason"], str)

    def test_frozen_scoring_counts_readable_no_read_and_requires_boolean_complete_labels(self):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "scripts" / "plate_ocr_evaluate.py"
        spec = importlib.util.spec_from_file_location("plate_ocr_evaluate_under_test", path)
        evaluator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(evaluator)
        units = [{
            "unit_id": "stable-unit-1",
            "scored_candidate": None,
            "machine_outcome": "no_candidate_detected",
            "machine_association_uncertain": False,
            "machine_quality_rejections": [],
            "all_attempts": [],
        }]
        scored = evaluator.score(units, {"stable-unit-1": {"readable": True, "uncertain": False, "text": "ABC123"}})
        assert scored["human_readable_denominator"] == 1
        assert scored["no_read_or_unreadable_machine_outcomes"] == 1
        assert scored["read_rate"] == 0.0
        with pytest.raises(ValueError):
            evaluator.score(units, {})
        with pytest.raises(ValueError):
            evaluator.score(units, {"stable-unit-1": {"readable": "true", "uncertain": False, "text": "ABC123"}})
        assert evaluator.gate_verdict(scored)["verdict"] == "fail"

    def test_evaluator_saves_exercised_runtime_metrics_before_shutdown(self, tmp_path, monkeypatch):
        import importlib.util
        import sys
        import types

        from core.plate_onnx_backend import sha256_file

        artifact_settings = enabled_settings(tmp_path / "artifacts")
        config = tmp_path / "plate.json"
        config.write_text(json.dumps({
            "enabled": True,
            "provider": "CPUExecutionProvider",
            "detector_path": artifact_settings.detector_path,
            "detector_sha256": artifact_settings.detector_sha256,
            "ocr_path": artifact_settings.ocr_path,
            "ocr_sha256": artifact_settings.ocr_sha256,
            "ocr_config_path": artifact_settings.ocr_config_path,
            "ocr_config_sha256": artifact_settings.ocr_config_sha256,
            "evaluation_record": artifact_settings.evaluation_record_path,
        }), "utf-8")
        video = tmp_path / "mock.mp4"
        video.write_bytes(b"mock footage placeholder")
        output_root = tmp_path / "outputs"
        report_path = output_root / "report.json"

        spec = importlib.util.spec_from_file_location(
            "plate_ocr_evaluate_metrics_test",
            Path(__file__).resolve().parents[1] / "scripts" / "plate_ocr_evaluate.py",
        )
        evaluator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(evaluator)
        monkeypatch.setattr(evaluator, "block_network", lambda: None)
        monkeypatch.setattr(evaluator, "configure_environment", lambda *_args, **_kwargs: None)
        monkeypatch.setenv("TAVIDM_PLATE_OCR_CONFIG", str(config))

        class Database:
            def init_db(self):
                pass
            def insert_video(self, **_kwargs):
                return 1
            def upsert_annotation(self, *_args):
                pass
            def list_review_items_for_video(self, *_args, **_kwargs):
                return []

        class Queue:
            unfinished_tasks = 0

        class Worker:
            _queue = Queue()

        class Runtime:
            worker = Worker()
            closed = False
            def status(self):
                return {"worker": {"processed_jobs": 7 if not self.closed else 0}}

        runtime = Runtime()
        database_module = types.ModuleType("database")
        database_module.db = Database()
        processor_module = types.ModuleType("core.video_processor")
        processor_module.process_video = lambda *_args, **_kwargs: types.SimpleNamespace(events=[])
        monkeypatch.setitem(sys.modules, "database", database_module)
        monkeypatch.setitem(sys.modules, "core.video_processor", processor_module)
        from core import plate_runtime
        monkeypatch.setattr(plate_runtime, "get_runtime", lambda: runtime)
        monkeypatch.setattr(plate_runtime, "shutdown_runtime", lambda: setattr(runtime, "closed", True))

        result = evaluator.main([
            "--video", str(video), "--config", str(config),
            "--isolation-root", str(output_root),
            "--db", str(output_root / "database.sqlite"),
            "--evidence-root", str(output_root / "evidence"),
            "--out", str(report_path),
        ])
        saved = json.loads(report_path.read_text("utf-8"))
        assert result == 0
        assert runtime.closed is True
        assert saved["status_report"]["worker"]["processed_jobs"] == 7

    def test_hash_mismatch_reported(self, tmp_path):
        settings = enabled_settings(tmp_path)
        Path(settings.detector_path).write_bytes(b"tampered")
        report = verify_artifact_hashes(settings)
        detector_row = next(r for r in report if r["path"] == settings.detector_path)
        assert detector_row["ok"] is False
        assert detector_row["reason"] == "artifact_hash_mismatch"

    def test_missing_artifact_reported(self, tmp_path):
        settings = enabled_settings(tmp_path)
        Path(settings.ocr_path).unlink()
        report = verify_artifact_hashes(settings)
        ocr_row = next(r for r in report if r["path"] == settings.ocr_path)
        assert ocr_row["ok"] is False
        assert ocr_row["reason"] == "artifact_missing"

    def test_backend_refuses_mismatched_artifact(self, tmp_path):
        settings = enabled_settings(tmp_path)
        Path(settings.detector_path).write_bytes(b"tampered")
        backend = PlateOnnxBackend(settings)
        with pytest.raises(PlateBackendUnavailable) as exc:
            backend.detect_plates(make_vehicle_frame())
        assert "artifact_hash_mismatch" in str(exc.value)

    def test_backend_construction_does_not_touch_runtime(self, tmp_path):
        settings = enabled_settings(tmp_path)
        backend = PlateOnnxBackend(settings)
        assert backend.runtime_ready is False
        assert backend.detector_calls == 0 and backend.ocr_calls == 0
        assert backend.metadata()["active_providers"] == []

    def test_disabled_settings_refused(self):
        backend = PlateOnnxBackend(PlateOcrSettings())
        with pytest.raises(PlateBackendUnavailable):
            backend.detect_plates(make_vehicle_frame())


# ----------------------------------------------------------------------
# 4. Preprocessing and geometry
# ----------------------------------------------------------------------


class TestPreprocessing:
    def test_letterbox_shape_and_dtype(self):
        frame = make_vehicle_frame(640, 480)
        tensor, ratio, (dw, dh) = letterbox_detector_input(frame)
        assert tensor.shape == (1, 3, 384, 384)
        assert tensor.dtype == np.float32
        assert 0.0 <= float(tensor.min()) and float(tensor.max()) <= 1.0
        # 640x480 letterboxes to 384x288 with symmetric padding on the short axis.
        assert ratio[0] == pytest.approx(384 / 640)
        assert dh == pytest.approx(48.0)
        assert dw == pytest.approx(0.0)

    def test_letterbox_padding_value(self):
        frame = np.full((100, 400, 3), 255, np.uint8)
        tensor, _ratio, _pad = letterbox_detector_input(frame)
        # Corners are padding, not image content.
        assert float(tensor[0, 0, 0, 0]) == pytest.approx(114 / 255.0, abs=1e-3)

    def test_unletterbox_roundtrip(self):
        frame = np.zeros((480, 640, 3), np.uint8)
        frame[100:200, 200:400] = 255
        _tensor, ratio, padding = letterbox_detector_input(frame)
        original = (200.0, 100.0, 400.0, 200.0)
        scaled = (
            original[0] * ratio[0] + padding[0],
            original[1] * ratio[1] + padding[1],
            original[2] * ratio[0] + padding[0],
            original[3] * ratio[1] + padding[1],
        )
        back = unletterbox_box(scaled, ratio, padding)
        assert back == pytest.approx(original, abs=1.0)

    def test_ocr_input_uint8_nhwc_three_channels(self):
        crop = make_plate_image(120, 40)
        batch = preprocess_ocr_input(crop, height=64, width=128, color_mode="gray")
        assert batch.shape == (1, 64, 128, 3)
        assert batch.dtype == np.uint8
        rgb_batch = preprocess_ocr_input(crop, height=64, width=128, color_mode="rgb")
        assert rgb_batch.shape == (1, 64, 128, 3)
        # Grayscale replication means all three channels agree.
        assert np.array_equal(batch[0, :, :, 0], batch[0, :, :, 2])

    def test_ocr_input_rejects_bad_color_mode(self):
        crop = make_plate_image(120, 40)
        with pytest.raises(Exception):
            preprocess_ocr_input(crop, height=64, width=128, color_mode="cmyk")

    def test_crop_from_box_clamps_and_reports_origin(self):
        frame = np.full((100, 200, 3), 30, np.uint8)
        crop, rect = crop_from_box(frame, (-10, -5, 60, 40))
        assert rect == (0, 0, 60, 40)
        assert crop.shape[:2] == (40, 60)
        _crop, rect2 = crop_from_box(frame, (500, 500, 600, 600))
        assert rect2[2] <= 200 and rect2[3] <= 100

    def test_crop_from_box_rejects_degenerate(self):
        frame = make_vehicle_frame()
        crop, _rect = crop_from_box(frame, (10, 10, 10, 50))
        assert crop is None

    def test_iou(self):
        assert iou((0, 0, 10, 10), (0, 0, 10, 10)) == pytest.approx(1.0)
        assert iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0
        assert 0 < iou((0, 0, 10, 10), (5, 0, 15, 10)) < 1


# ----------------------------------------------------------------------
# 5. Quality gate
# ----------------------------------------------------------------------


class TestQualityGate:
    def test_good_plate_accepted(self, tmp_path):
        settings = enabled_settings(tmp_path)
        crop = make_plate_image(160, 48)
        report = check_plate_quality(crop, settings=settings)
        assert report.accepted is True
        assert report.reasons == ()

    def test_missing_crop_rejected(self, tmp_path):
        settings = enabled_settings(tmp_path)
        report = check_plate_quality(None, settings=settings)
        assert report.accepted is False
        assert "plate_crop_unavailable" in report.reasons

    def test_tiny_plate_rejected(self, tmp_path):
        settings = enabled_settings(tmp_path)
        report = check_plate_quality(make_plate_image(12, 6), settings=settings)
        assert report.accepted is False
        assert "plate_too_small" in report.reasons

    def test_blurred_plate_rejected(self, tmp_path):
        settings = enabled_settings(tmp_path)
        flat = np.full((48, 160, 3), 128, np.uint8)
        report = check_plate_quality(flat, settings=settings)
        assert report.accepted is False
        assert "plate_blur_severe" in report.reasons

    def test_implausible_aspect_rejected(self, tmp_path):
        settings = enabled_settings(tmp_path)
        report = check_plate_quality(make_plate_image(60, 60), settings=settings)
        assert report.accepted is False
        assert "plate_aspect_ratio_implausible" in report.reasons

    def test_clipped_box_rejected(self, tmp_path):
        settings = enabled_settings(tmp_path)
        frame = np.full((100, 200, 3), 30, np.uint8)
        crop, rect = crop_from_box(frame, (150, 20, 400, 60))
        report = check_plate_quality(
            crop,
            settings=settings,
            requested_box=(150, 20, 400, 60),
            effective_rect=rect,
        )
        assert report.accepted is False
        assert "plate_box_clipped" in report.reasons
        assert report.metrics["clipped"] == 1

    def test_glare_rejected(self, tmp_path):
        settings = enabled_settings(tmp_path)
        blown = np.full((48, 200, 3), 255, np.uint8)
        blown[10:30, 10:190] = 0
        report = check_plate_quality(blown, settings=settings)
        assert report.accepted is False
        assert "plate_glare_severe" in report.reasons


# ----------------------------------------------------------------------
# 6. Manifest store
# ----------------------------------------------------------------------


class TestManifestStore:
    def _manifest(self, attempt_id="att_test", review_id=42, candidates=None):
        return {
            "contract_version": CONTRACT_VERSION,
            "attempt_id": attempt_id,
            "review_id": review_id,
            "case_id": None,
            "outcome": "candidate_found",
            "candidates": candidates or [],
            "samples": [],
            "machine_provenance": {},
        }

    def test_write_and_load(self, plate_root_env):
        path = manifests.write_manifest(self._manifest())
        assert path.is_file()
        loaded = manifests.load_manifest("att_test")
        assert loaded.ok
        assert loaded.data["review_id"] == 42

    def test_attempts_are_immutable(self, plate_root_env):
        manifests.write_manifest(self._manifest())
        with pytest.raises(PlateManifestError):
            manifests.write_manifest(self._manifest())

    def test_oversize_manifest_refused(self, plate_root_env):
        payload = self._manifest()
        payload["blob"] = "x" * (600 * 1024)
        with pytest.raises(PlateManifestError):
            manifests.write_manifest(payload)

    def test_wrong_contract_version_refused(self, plate_root_env):
        payload = self._manifest()
        payload["contract_version"] = "something-else/9"
        with pytest.raises(PlateManifestError):
            manifests.write_manifest(payload)

    def test_missing_manifest_status(self, plate_root_env):
        loaded = manifests.load_manifest("att_absent")
        assert loaded.status == "missing"
        assert loaded.ok is False

    def test_corrupt_manifest_status(self, plate_root_env):
        manifests.write_manifest(self._manifest())
        path = manifests.attempt_manifest_path("att_test")
        path.write_text("{ broken", encoding="utf-8")
        loaded = manifests.load_manifest("att_test")
        assert loaded.status == "corrupt"

    def test_unsafe_identifier_refused(self, plate_root_env):
        with pytest.raises(PlateManifestError):
            manifests.attempt_manifest_path("../../etc/passwd")
        loaded = manifests.load_manifest("../secrets")
        assert loaded.status == "unsafe_identifier"

    def test_path_traversal_in_crop_ref_refused(self, plate_root_env):
        assert manifests.resolve_crop_ref("../../../Windows/win.ini") is None
        assert manifests.resolve_crop_ref("C:/Windows/win.ini") is None
        assert manifests.resolve_crop_ref("/etc/passwd") is None
        assert manifests.resolve_crop_ref(None) is None

    def test_index_is_replaceable_and_atomic(self, plate_root_env):
        manifests.write_manifest(self._manifest(attempt_id="att_a", review_id=7))
        manifests.write_index(manifests.review_key(7), {"attempt_id": "att_a"})
        manifests.write_manifest(self._manifest(attempt_id="att_b", review_id=7))
        manifests.write_index(manifests.review_key(7), {"attempt_id": "att_b"})
        entry = manifests.read_index(manifests.review_key(7))
        assert entry["attempt_id"] == "att_b"
        # History is preserved: both attempts still exist.
        assert set(manifests.list_attempt_ids()) >= {"att_a", "att_b"}

    def test_attempts_for_review_uses_index_then_scan(self, plate_root_env):
        manifests.write_manifest(self._manifest(attempt_id="att_a", review_id=11))
        manifests.write_manifest(self._manifest(attempt_id="att_b", review_id=11))
        manifests.write_index(manifests.review_key(11), {"attempt_id": "att_b"})
        found = {m.attempt_id for m in manifests.attempts_for_review(11)}
        assert found == {"att_a", "att_b"}
        assert manifests.attempts_for_review(999) == []

    def test_orphan_report_flags_deleted_crops(self, plate_root_env):
        ref = manifests.save_crop(
            "att_c", "vehicle_s1.jpg", make_plate_image(40, 16), params=_jpeg_params(92)
        )
        manifests.write_manifest(
            {
                **self._manifest(attempt_id="att_c"),
                "crops": [ref],
                "samples": [{"sample_id": "s1"}],
            }
        )
        report = manifests.orphan_report()
        assert report["attempts"] == 1
        assert report["attempts_with_missing_crops"] == 0
        manifests.crop_path("att_c", ref["name"]).unlink()
        report = manifests.orphan_report()
        assert report["attempts_with_missing_crops"] == 1

    def test_partial_write_sweep_only_touches_tmp(self, plate_root_env):
        manifests.write_manifest(self._manifest())
        stray = manifests.attempts_dir() / ".att_test.json.deadbeef.tmp"
        stray.write_bytes(b"partial")
        result = manifests.sweep_partial_writes()
        assert result["removed_tmp_files"] == 1
        assert manifests.load_manifest("att_test").ok
        assert not stray.exists()

    def test_crop_names_are_server_generated(self, plate_root_env):
        with pytest.raises(PlateManifestError):
            manifests.save_crop("att_x", "../../evil.jpg", make_plate_image(40, 16))
        with pytest.raises(PlateManifestError):
            manifests.save_crop("../att_x", "ok.jpg", make_plate_image(40, 16))

    def test_deleted_evidence_resolves_to_none(self, plate_root_env):
        ref = manifests.save_crop("att_d", "plate_c.png", make_plate_image(40, 16))
        assert manifests.resolve_crop_ref(ref["stored_path"]) is not None
        manifests.crop_path("att_d", "plate_c.png").unlink()
        assert manifests.resolve_crop_ref(ref["stored_path"]) is None


# ----------------------------------------------------------------------
# 7. Attribution
# ----------------------------------------------------------------------


class TestAttribution:
    def test_epoch_mismatch_stops_collection(self, plate_root_env, tmp_path):
        settings = enabled_settings(tmp_path)
        backend = FakeBackend(settings)
        worker, memory = build_worker(settings, backend)
        try:
            collector = PlateObservationCollector(
                settings, backend, worker, source="video", run_key="run_1"
            )
            frame = make_vehicle_frame()
            collector.register(
                review_id=1,
                track_id=7,
                identity_epoch=1,
                violation_type="no_parking",
                frame_number=10,
                timestamp_sec=1.0,
            )
            # A different identity epoch for the same ByteTrack id: never sampled.
            collector.observe(frame, 11, 1.5, [tracked(epoch=2)])
            collector.flush()
            assert drain(worker)
            loaded = current_attempt_for_review(1)
            assert loaded is not None
            assert loaded.data["outcome"] == PlateAttemptOutcome.NO_CANDIDATE_DETECTED.value
        finally:
            worker.stop()
            memory.release(memory.used_bytes)

    def test_each_frame_uses_its_own_box(self, plate_root_env, tmp_path):
        settings = enabled_settings(tmp_path)
        backend = FakeBackend(settings)
        worker, memory = build_worker(settings, backend)
        try:
            collector = PlateObservationCollector(
                settings, backend, worker, source="video", run_key="run_1"
            )
            frame = make_vehicle_frame()
            collector.register(
                review_id=2,
                track_id=7,
                identity_epoch=1,
                violation_type="no_parking",
                frame_number=10,
                timestamp_sec=1.0,
            )
            collector.observe(frame, 11, 1.0, [tracked(x=80, y=100, w=160, h=100)])
            collector.observe(frame, 13, 2.0, [tracked(x=10, y=20, w=60, h=40)])
            collector.flush()
            assert drain(worker)
            loaded = current_attempt_for_review(2)
            samples = loaded.data["samples"]
            assert len(samples) == 2
            assert samples[0]["frame_number"] == 11
            assert samples[1]["frame_number"] == 13
            # The second sample carries the second frame's own box, not the first's.
            assert samples[0]["vehicle_box_frame_px"] == [80, 100, 240, 200]
            assert samples[1]["vehicle_box_frame_px"] == [10, 20, 70, 60]
            assert samples[0]["crop_origin_frame_px"] == [80, 100]
            assert samples[1]["crop_origin_frame_px"] == [10, 20]
            assert samples[0]["timestamp_sec"] == 1.0
            assert samples[1]["timestamp_sec"] == 2.0
        finally:
            worker.stop()
            memory.release(memory.used_bytes)

    def test_overlapping_vehicle_is_association_uncertain(self, plate_root_env, tmp_path):
        settings = enabled_settings(tmp_path)
        backend = FakeBackend(settings)
        worker, memory = build_worker(settings, backend)
        try:
            collector = PlateObservationCollector(
                settings, backend, worker, source="video", run_key="run_1"
            )
            frame = make_vehicle_frame()
            collector.register(
                review_id=3,
                track_id=7,
                identity_epoch=1,
                violation_type="no_parking",
                frame_number=10,
                timestamp_sec=1.0,
            )
            # A second tracked vehicle almost on top of the first.
            collector.observe(
                frame,
                11,
                1.0,
                [tracked(track_id=7, x=80, y=100, w=160, h=100), tracked(track_id=8, x=85, y=100, w=160, h=100)],
            )
            collector.flush()
            assert drain(worker)
            loaded = current_attempt_for_review(3)
            data = loaded.data
            assert data["outcome"] == PlateAttemptOutcome.ASSOCIATION_UNCERTAIN.value
            assert data["association_uncertain"] is True
            assert data["candidates"] == []
            assert "vehicle_overlap_ambiguous" in data["quality_rejections"]
        finally:
            worker.stop()
            memory.release(memory.used_bytes)

    def test_live_reconnect_uses_new_session_identity(self, tmp_path):
        settings = enabled_settings(tmp_path)
        backend = FakeBackend(settings)
        memory = CropMemoryBudget(1 << 20)
        worker = PlateInferenceWorker(settings, backend, memory=memory)
        first = PlateObservationCollector(
            settings,
            backend,
            worker,
            source="camera",
            run_key="camera_1",
            live_session_id="1000_1",
        )
        second = PlateObservationCollector(
            settings,
            backend,
            worker,
            source="camera",
            run_key="camera_1",
            live_session_id="2000_1",
        )
        assert first.live_session_id != second.live_session_id

    def test_duplicate_registration_is_deduplicated(self, plate_root_env, tmp_path):
        settings = enabled_settings(tmp_path)
        backend = FakeBackend(settings)
        memory = CropMemoryBudget(1 << 20)
        worker = PlateInferenceWorker(settings, backend, memory=memory)
        collector = PlateObservationCollector(
            settings, backend, worker, source="video", run_key="run_1"
        )
        collector.register(
            review_id=9,
            track_id=7,
            identity_epoch=1,
            violation_type="no_parking",
            frame_number=1,
            timestamp_sec=0.0,
        )
        second = collector.register(
            review_id=9,
            track_id=7,
            identity_epoch=1,
            violation_type="no_parking",
            frame_number=1,
            timestamp_sec=0.0,
        )
        assert second is None
        assert collector.duplicates_skipped == 1

    def test_register_is_noop_when_disabled(self, tmp_path):
        backend = FakeBackend(PlateOcrSettings())
        memory = CropMemoryBudget(1 << 20)
        worker = PlateInferenceWorker(PlateOcrSettings(), backend, memory=memory)
        collector = PlateObservationCollector(
            PlateOcrSettings(), backend, worker, source="video", run_key="run_1"
        )
        assert (
            collector.register(
                review_id=1,
                track_id=1,
                identity_epoch=1,
                violation_type="no_parking",
                frame_number=1,
                timestamp_sec=0.0,
            )
            is None
        )


# ----------------------------------------------------------------------
# 8. Bounds
# ----------------------------------------------------------------------


class TestBounds:
    def _collector(self, plate_root_env, tmp_path, backend=None, **bound_overrides):
        settings = _replace(
            enabled_settings(tmp_path), bounds={**DEFAULT_BOUNDS, **bound_overrides}
        )
        backend = backend or FakeBackend(settings)
        memory = CropMemoryBudget(int(settings.bounds["retained_crop_memory_budget_bytes"]))
        worker = PlateInferenceWorker(settings, backend, memory=memory)
        collector = PlateObservationCollector(
            settings, backend, worker, source="video", run_key="run_1"
        )
        return settings, backend, worker, memory, collector

    def test_max_crops_per_observation(self, plate_root_env, tmp_path):
        settings, backend, worker, memory, collector = self._collector(
            plate_root_env, tmp_path, max_crops_per_observation=2
        )
        worker.start()
        try:
            frame = make_vehicle_frame()
            collector.register(
                review_id=20,
                track_id=7,
                identity_epoch=1,
                violation_type="no_parking",
                frame_number=1,
                timestamp_sec=0.0,
            )
            for i in range(6):
                collector.observe(frame, 10 + i, float(i), [tracked()])
            collector.flush()
            assert drain(worker)
            loaded = current_attempt_for_review(20)
            assert len(loaded.data["samples"]) == 2
        finally:
            worker.stop()
            memory.release(memory.used_bytes)


    def test_sample_rate_limit(self, plate_root_env, tmp_path):
        settings, backend, worker, memory, collector = self._collector(
            plate_root_env, tmp_path, sample_fps=2.0
        )
        worker.start()
        try:
            frame = make_vehicle_frame()
            collector.register(
                review_id=21,
                track_id=7,
                identity_epoch=1,
                violation_type="no_parking",
                frame_number=1,
                timestamp_sec=0.0,
            )
            # Ten frames inside 0.4 s -> only the first may be sampled at 2 fps.
            for i in range(10):
                collector.observe(frame, 10 + i, i * 0.04, [tracked()])
            collector.flush()
            assert drain(worker)
            loaded = current_attempt_for_review(21)
            assert len(loaded.data["samples"]) == 1
        finally:
            worker.stop()
            memory.release(memory.used_bytes)

    def test_collection_window_expiry(self, plate_root_env, tmp_path):
        settings, backend, worker, memory, collector = self._collector(
            plate_root_env, tmp_path, collection_window_sec=0.05
        )
        worker.start()
        try:
            frame = make_vehicle_frame()
            collector.register(
                review_id=22,
                track_id=7,
                identity_epoch=1,
                violation_type="no_parking",
                frame_number=1,
                timestamp_sec=0.0,
            )
            time.sleep(0.12)
            collector.observe(frame, 12, 1.0, [tracked()])
            collector.flush()
            assert drain(worker)
            loaded = current_attempt_for_review(22)
            # The collection window had already expired, so nothing was sampled.
            assert loaded.data["samples"] == []
            assert loaded.data["outcome"] == PlateAttemptOutcome.NO_CANDIDATE_DETECTED.value
        finally:
            worker.stop()
            memory.release(memory.used_bytes)

    def test_detector_and_ocr_call_limits(self, plate_root_env, tmp_path):
        settings, backend, worker, memory, collector = self._collector(
            plate_root_env,
            tmp_path,
            max_detector_calls=2,
            max_ocr_calls=3,
            max_crops_per_observation=3,
        )
        worker.start()
        try:
            frame = make_vehicle_frame()
            collector.register(
                review_id=23,
                track_id=7,
                identity_epoch=1,
                violation_type="no_parking",
                frame_number=1,
                timestamp_sec=0.0,
            )
            for i in range(3):
                collector.observe(frame, 10 + i, float(i), [tracked()])
            collector.flush()
            assert drain(worker)
            loaded = current_attempt_for_review(23)
            assert loaded.data["calls"]["detector"] == 2
            assert loaded.data["calls"]["ocr"] == 2
            assert loaded.data["truncated"]["detector"] is True
        finally:
            worker.stop()
            memory.release(memory.used_bytes)

    def test_memory_budget_rejects_and_records(self, plate_root_env, tmp_path):
        settings, backend, worker, memory, collector = self._collector(
            plate_root_env, tmp_path, retained_crop_memory_budget_bytes=1024
        )
        worker.start()
        try:
            frame = make_vehicle_frame()
            collector.register(
                review_id=24,
                track_id=7,
                identity_epoch=1,
                violation_type="no_parking",
                frame_number=1,
                timestamp_sec=0.0,
            )
            collector.observe(frame, 11, 1.0, [tracked()])
            collector.flush()
            assert drain(worker)
            loaded = current_attempt_for_review(24)
            assert "retained_crop_memory_budget_exhausted" in loaded.data["quality_rejections"]
            assert loaded.data["truncated"]["collection"] is True
        finally:
            worker.stop()
            memory.release(memory.used_bytes)

    def test_queue_saturation_is_reported(self, plate_root_env, tmp_path):
        settings, backend, worker, memory, collector = self._collector(
            plate_root_env, tmp_path, max_queued_jobs=1
        )
        worker.start()
        try:
            frame = make_vehicle_frame()
            # First job occupies the single queue slot while the worker is slow.
            for review_id in (30, 31, 32):
                collector.register(
                    review_id=review_id,
                    track_id=7,
                    identity_epoch=1,
                    violation_type="no_parking",
                    frame_number=1,
                    timestamp_sec=0.0,
                )
                collector.observe(frame, 11, 1.0, [tracked()])
                collector._finish(collector._pending[review_id], None, None)
            collector.flush()
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if all(
                    current_attempt_for_review(r) is not None for r in (30, 31, 32)
                ):
                    break
                time.sleep(0.02)
            outcomes = [
                current_attempt_for_review(r).data["outcome"] for r in (30, 31, 32)
            ]
            assert PlateAttemptOutcome.BUDGET_EXHAUSTED.value in outcomes
            assert worker.snapshot()["rejected_saturated"] >= 1
        finally:
            worker.stop()
            memory.release(memory.used_bytes)

    def test_schedule_budget_reports_partial_results(self, plate_root_env, tmp_path):
        settings, backend, worker, memory, collector = self._collector(
            plate_root_env, tmp_path, schedule_budget_sec=0.0
        )
        worker.start()
        try:
            frame = make_vehicle_frame()
            collector.register(
                review_id=25,
                track_id=7,
                identity_epoch=1,
                violation_type="no_parking",
                frame_number=1,
                timestamp_sec=0.0,
            )
            collector.observe(frame, 11, 1.0, [tracked()])
            collector.flush()
            assert drain(worker)
            loaded = current_attempt_for_review(25)
            assert loaded.data["outcome"] == PlateAttemptOutcome.BUDGET_EXHAUSTED.value
            assert loaded.data["budget"]["schedule_budget_exhausted"] is True
            assert "cannot interrupt" in loaded.data["budget"]["note"]
        finally:
            worker.stop()
            memory.release(memory.used_bytes)

    def test_overrunning_detector_does_not_extend_attempt_deadline(self, plate_root_env, tmp_path):
        backend = FakeBackend(delay=0.12)
        settings, _backend, worker, memory, collector = self._collector(
            plate_root_env, tmp_path, backend=backend, schedule_budget_sec=0.05
        )
        worker.start()
        try:
            frame = make_vehicle_frame()
            collector.register(review_id=125, track_id=7, identity_epoch=1, violation_type="no_parking", frame_number=1, timestamp_sec=0.0)
            collector.observe(frame, 1, 0.5, [tracked()])
            collector.observe(frame, 2, 1.0, [tracked()])
            collector.flush()
            assert drain(worker)
            assert backend.detector_calls == 1
            assert backend.ocr_calls == 0
            loaded = current_attempt_for_review(125)
            assert loaded.data["budget"]["schedule_budget_exhausted"] is True
            assert loaded.data["outcome"] == PlateAttemptOutcome.BUDGET_EXHAUSTED.value
        finally:
            worker.stop()
            memory.release(memory.used_bytes)

    def test_cancelled_collection_records_reason(self, plate_root_env, tmp_path):
        settings, backend, worker, memory, collector = self._collector(plate_root_env, tmp_path)
        worker.start()
        try:
            frame = make_vehicle_frame()
            collector.register(
                review_id=26,
                track_id=7,
                identity_epoch=1,
                violation_type="no_parking",
                frame_number=1,
                timestamp_sec=0.0,
            )
            collector.observe(frame, 11, 1.0, [tracked()])
            collector.flush("cancelled")
            assert drain(worker)
            loaded = current_attempt_for_review(26)
            assert "collection_cancelled" in loaded.data["quality_rejections"]
        finally:
            worker.stop()
            memory.release(memory.used_bytes)

    def test_memory_budget_accounting(self):
        budget = CropMemoryBudget(100)
        assert budget.try_reserve(60) is True
        assert budget.used_bytes == 60
        assert budget.try_reserve(60) is False
        budget.release(60)
        assert budget.used_bytes == 0


# ----------------------------------------------------------------------
class TestLivePlateLifecycle:
    def test_startup_and_reconnect_use_distinct_collection_sessions_without_camera_io(self, monkeypatch):
        import contextlib
        from core import live_stream, plate_runtime

        collectors = []

        class Collector:
            def __init__(self, session):
                self.live_session_id = session
                self.flushes = []
            def flush(self, reason):
                self.flushes.append(reason)
            def register(self, **_kwargs):
                return None
            def observe(self, *_args):
                return None

        class Runtime:
            def new_collector(self, **kwargs):
                collector = Collector(kwargs["live_session_id"])
                collectors.append(collector)
                return collector

        class Detector:
            def load(self):
                pass
            def track_frame(self, *_args, **_kwargs):
                return []

        class Capture:
            def __init__(self, opened):
                self.opened = opened
            def isOpened(self):
                return self.opened
            def read(self):
                return False, None
            def release(self):
                pass

        caps = iter([Capture(True), Capture(True), Capture(False)])
        monkeypatch.setattr(live_stream, "Detector", Detector)
        monkeypatch.setattr(live_stream, "enforce_object_class_contract", lambda _detector: None)
        monkeypatch.setattr(live_stream, "extract_model_class_names", lambda _detector: [])
        monkeypatch.setattr(live_stream, "load_rule_parameters", lambda: {"stationary_px": 1.0, "frame_skip": 1, "confidence_threshold": 0.25})
        monkeypatch.setattr(live_stream, "load_enabled_violations", lambda: [])
        monkeypatch.setattr(live_stream, "gpu_inference_slot", lambda: None)
        monkeypatch.setattr(live_stream.cv2, "VideoCapture", lambda _url: next(caps))
        monkeypatch.setattr(plate_runtime, "get_runtime", lambda: Runtime())
        worker = live_stream.LiveStreamWorker({"id": 41, "rtsp_url": "mock://camera", "zones_json": None})
        sleep_count = 0
        def stop_after_reconnects(_seconds):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count == 2:
                worker.stop()
        monkeypatch.setattr(live_stream.time, "sleep", stop_after_reconnects)
        worker.run()
        assert len(collectors) == 2
        assert collectors[0].live_session_id != collectors[1].live_session_id
        assert collectors[0].flushes[0] == "stream_reconnected"
        assert "stream_stopped" in collectors[1].flushes



# 9. Outcomes and candidate selection
# ----------------------------------------------------------------------


class TestOutcomes:
    def _run(self, plate_root_env, tmp_path, backend, review_id=40, frame_count=1, **bound_overrides):
        settings = _replace(
            enabled_settings(tmp_path), bounds={**DEFAULT_BOUNDS, **bound_overrides}
        )
        backend.settings = settings
        memory = CropMemoryBudget(int(settings.bounds["retained_crop_memory_budget_bytes"]))
        worker = PlateInferenceWorker(settings, backend, memory=memory)
        worker.start()
        collector = PlateObservationCollector(
            settings, backend, worker, source="video", run_key="run_1"
        )
        frame = make_vehicle_frame()
        collector.register(
            review_id=review_id,
            track_id=7,
            identity_epoch=1,
            violation_type="no_parking",
            frame_number=1,
            timestamp_sec=0.0,
        )
        for i in range(int(frame_count)):
            collector.observe(frame, 11 + i, 1.0 + i, [tracked()])
        collector.flush()
        drain(worker)
        worker.stop()
        memory.release(memory.used_bytes)
        return current_attempt_for_review(review_id)

    def test_candidate_found(self, plate_root_env, tmp_path):
        backend = FakeBackend(boxes=[FakeBox(*FAKE_PLATE_BOX)])
        loaded = self._run(plate_root_env, tmp_path, backend)
        assert loaded.data["outcome"] == PlateAttemptOutcome.CANDIDATE_FOUND.value
        assert loaded.data["candidates"][0]["ocr_raw"] == "ABC123"
        assert loaded.data["primary_candidate_id"]

    def test_no_candidate_detected(self, plate_root_env, tmp_path):
        backend = FakeBackend(boxes=[])
        loaded = self._run(plate_root_env, tmp_path, backend)
        assert loaded.data["outcome"] == PlateAttemptOutcome.NO_CANDIDATE_DETECTED.value
        assert loaded.data["outcome_is_ambiguous"] is True

    def test_detected_but_unreadable(self, plate_root_env, tmp_path):
        backend = FakeBackend(boxes=[FakeBox(*FAKE_PLATE_BOX)], reads=[FakeRead("", None)])
        loaded = self._run(plate_root_env, tmp_path, backend)
        assert loaded.data["outcome"] == PlateAttemptOutcome.DETECTED_UNREADABLE.value

    def test_quality_rejected(self, plate_root_env, tmp_path):
        backend = FakeBackend(boxes=[FakeBox(20, 10, 40, 22, score=0.5)])
        loaded = self._run(plate_root_env, tmp_path, backend)
        assert loaded.data["outcome"] == PlateAttemptOutcome.QUALITY_REJECTED.value
        assert loaded.data["quality_rejections"]
        assert backend.ocr_calls == 0  # quality gate runs before recognition

    def test_unavailable_backend_is_controlled(self, plate_root_env, tmp_path):
        backend = FakeBackend(errors={"detect": PlateBackendUnavailable("artifact_rejected")})
        loaded = self._run(plate_root_env, tmp_path, backend)
        assert loaded.data["outcome"] == PlateAttemptOutcome.UNAVAILABLE.value
        assert "artifact_rejected" in json.dumps(loaded.data)

    def test_failed_backend_is_controlled(self, plate_root_env, tmp_path):
        backend = FakeBackend(errors={"detect": RuntimeError("boom")})
        loaded = self._run(plate_root_env, tmp_path, backend)
        assert loaded.data["outcome"] == PlateAttemptOutcome.FAILED.value

    def test_raw_text_and_scores_preserved(self, plate_root_env, tmp_path):
        backend = FakeBackend(
            boxes=[FakeBox(*FAKE_PLATE_BOX)],
            reads=[FakeRead("AB0I23", [3.0, 3.5, -1.0, 3.2, 3.1, 2.9])],
        )
        loaded = self._run(plate_root_env, tmp_path, backend)
        candidate = loaded.data["candidates"][0]
        assert candidate["ocr_raw"] == "AB0I23"
        assert candidate["ocr_char_scores"] == [3.0, 3.5, -1.0, 3.2, 3.1, 2.9]
        assert candidate["ocr_scalar_score"] == pytest.approx(2.45, abs=1e-6)
        assert "uncalibrated" in candidate["ocr_score_semantics"]

    def test_absent_confidence_stays_null(self, plate_root_env, tmp_path):
        backend = FakeBackend(boxes=[FakeBox(*FAKE_PLATE_BOX)], reads=[FakeRead("ABC123", None)])
        loaded = self._run(plate_root_env, tmp_path, backend)
        candidate = loaded.data["candidates"][0]
        assert candidate["ocr_char_scores"] is None
        assert candidate["ocr_scalar_score"] is None

    def test_primary_selection_prefers_agreement(self, plate_root_env, tmp_path):
        settings = enabled_settings(tmp_path)
        backend = FakeBackend(
            boxes=[FakeBox(*FAKE_PLATE_BOX)],
            reads=[
                FakeRead("ABC123", [4.0] * 6),
                FakeRead("ABC123", [3.0] * 6),
                FakeRead("XYZ999", [9.9] * 6),
            ],
        )
        loaded = self._run(plate_root_env, tmp_path, backend, review_id=41, frame_count=3, max_crops_per_observation=3)
        assert loaded.data["primary_candidate_id"]
        chosen = next(
            c for c in loaded.data["candidates"] if c["candidate_id"] == loaded.data["primary_candidate_id"]
        )
        # Two agreeing samples beat the single higher-scoring conflicting read.
        assert chosen["ocr_raw"] == "ABC123"
        assert len(loaded.data["candidates"]) == 3

    def test_conflicting_reads_are_preserved(self, plate_root_env, tmp_path):
        backend = FakeBackend(
            boxes=[FakeBox(*FAKE_PLATE_BOX)],
            reads=[FakeRead("ABC123", [4.0] * 6), FakeRead("ABC124", [4.0] * 6)],
        )
        loaded = self._run(plate_root_env, tmp_path, backend, review_id=42, frame_count=2, max_crops_per_observation=2)
        texts = {c["ocr_raw"] for c in loaded.data["candidates"]}
        assert texts == {"ABC123", "ABC124"}

    def test_select_primary_candidate_is_deterministic(self):
        class C:
            def __init__(self, cid, text, sample, score):
                self.candidate_id = cid
                self.ocr_raw = text
                self.sample_id = sample
                self.ocr_scalar_score = score

            def display_text(self):
                from core.plate_jobs import _DISPLAY_NORMALIZE

                return _DISPLAY_NORMALIZE.sub("", (self.ocr_raw or "").upper())

        chosen = select_primary_candidate(
            [C("b", "ABC 123", "s1", 1.0), C("a", "ABC-123", "s2", 0.5)]
        )
        assert chosen is not None
        assert chosen.display_text() == "ABC123"
        assert select_primary_candidate([]) is None
        assert select_primary_candidate([C("c", "", "s1", 1.0)]) is None


# ----------------------------------------------------------------------
# 10. Admin-only confirmation boundary
# ----------------------------------------------------------------------


def _make_violation(test_db, reviewer_id):
    rid = test_db.insert_review_queue(
        video_id=None,
        track_id=3,
        violation_type="no_parking",
        confidence=0.8,
        frame_number=12,
        evidence_path="/tmp/e.jpg",
    )
    return test_db.confirm_review_item(rid, reviewer_id)


class TestPlateAdminOnlyBoundary:
    def test_capability_requires_active_admin(self, test_db):
        admin = test_db.create_user("p_admin", "hash", role="admin")
        enforcer = test_db.create_user("p_enf", "hash", role="enforcer")
        viewer = test_db.create_user("p_view", "hash", role="viewer")
        inactive_admin = test_db.create_user("p_off", "hash", role="admin")
        test_db.update_user(inactive_admin, is_active=0)
        assert test_db.can_confirm_plate_identity(admin) is True
        assert test_db.can_confirm_plate_identity(enforcer) is False
        assert test_db.can_confirm_plate_identity(viewer) is False
        assert test_db.can_confirm_plate_identity(inactive_admin) is False
        assert test_db.can_confirm_plate_identity(99999) is False

    def test_explicit_grant_does_not_bypass(self, test_db):
        enforcer = test_db.create_user("p_grant", "hash", role="enforcer")
        admin = test_db.create_user("p_granter", "hash", role="admin")
        test_db.assign_policy_permission(
            enforcer, "verify_plate", admin, reason="explicit grant"
        )
        assert test_db.user_has_permission(enforcer, "verify_plate") is True
        # The narrow admin-only capability is unaffected by the explicit grant.
        assert test_db.can_confirm_plate_identity(enforcer) is False
        # can_verify_plate itself is unchanged.
        assert test_db.can_verify_plate(enforcer) is True

    def test_direct_service_call_cannot_bypass(self, test_db):
        from core.case_review_service import verify_plate_identity
        from core.plate_processing import HUMAN_PLATE_STATUS_VERIFIED_READABLE

        admin = test_db.create_user("p_admin", "hash", role="admin")
        enforcer = test_db.create_user("p_enf", "hash", role="enforcer")
        vid = _make_violation(test_db, admin)
        with pytest.raises(PermissionError):
            verify_plate_identity(
                test_db,
                vid,
                enforcer,
                plate_status=HUMAN_PLATE_STATUS_VERIFIED_READABLE,
                accepted_plate_text="ABC123",
            )
        assert test_db.get_plate_verification(vid) is None

    def test_adapter_without_capability_is_refused(self, test_db):
        from core.case_review_service import CaseReviewError, verify_plate_identity
        from core.plate_processing import HUMAN_PLATE_STATUS_VERIFIED_READABLE

        class Legacy:
            def can_verify_plate(self, user_id):
                return True

        with pytest.raises(CaseReviewError):
            verify_plate_identity(
                Legacy(),
                1,
                1,
                plate_status=HUMAN_PLATE_STATUS_VERIFIED_READABLE,
                accepted_plate_text="ABC123",
            )

    def test_unrelated_capabilities_preserved(self, test_db):
        admin = test_db.create_user("p_admin", "hash", role="admin")
        enforcer = test_db.create_user("p_enf", "hash", role="enforcer")
        assert test_db.can_confirm_case(enforcer) is True
        assert test_db.can_confirm_event_time(enforcer) is True
        assert test_db.can_verify_plate(enforcer) is True
        assert test_db.can_confirm_plate_identity(enforcer) is False
        assert test_db.can_confirm_case(admin) is True


# ----------------------------------------------------------------------
# 11. Machine provenance retention and races
# ----------------------------------------------------------------------


class TestProvenanceRetention:
    def test_client_review_id_cannot_extend_case_authority(self, test_db):
        from core.plate_review import review_ids_for_case

        admin = test_db.create_user("p_admin", "hash", role="admin")
        case_a = _make_violation(test_db, admin)
        case_b = _make_violation(test_db, admin)
        review_a = test_db.get_case_actions(case_a)[0]["review_id"]
        review_b = test_db.get_case_actions(case_b)[0]["review_id"]
        assert review_ids_for_case(test_db, case_b, review_id=review_a) == []
        assert review_ids_for_case(test_db, case_b, review_id=review_b) == [review_b]

    def test_human_confirmation_retains_machine_provenance(
        self, plate_root_env, test_db
    ):
        from core.case_review_service import verify_plate_identity
        from core.plate_review import resolve_candidate
        from core.plate_processing import HUMAN_PLATE_STATUS_VERIFIED_READABLE
        from core.plate_review import manifest_provenance_for_verification

        admin = test_db.create_user("p_admin", "hash", role="admin")
        vid = _make_violation(test_db, admin)
        linked_review_id = test_db.get_case_actions(vid)[0]["review_id"]
        crop_ref = manifests.save_crop("att_p1", "plate_cand_1.png", make_plate_image(80, 24))
        manifests.write_manifest(
            {
                "contract_version": CONTRACT_VERSION,
                "attempt_id": "att_p1",
                "review_id": linked_review_id,
                "case_id": None,
                "outcome": "candidate_found",
                "candidates": [
                    {
                        "candidate_id": "cand_1",
                        "sample_id": "s1",
                        "ocr_raw": "ABC123",
                        "ocr_display_normalized": "ABC123",
                        "detection_confidence": 0.71,
                        "ocr_scalar_score": 4.2,
                        "ocr_char_scores": [4.0] * 6,
                        "ocr_score_semantics": "raw logit",
                        "plate_box_in_crop": [1, 2, 3, 4],
                        "plate_box_in_frame": [11, 12, 13, 14],
                        "plate_crop_ref": crop_ref,
                    }
                ],
                "samples": [
                    {
                        "sample_id": "s1",
                        "frame_number": 12,
                        "timestamp_sec": 1.5,
                        "frame_width": 320,
                        "frame_height": 240,
                        "vehicle_box_frame_px": [80, 100, 240, 200],
                        "crop_origin_frame_px": [80, 100],
                        "crop_width": 160,
                        "crop_height": 100,
                    }
                ],
                "machine_provenance": {
                    "requested_provider": "CPUExecutionProvider",
                    "active_providers": ["CPUExecutionProvider"],
                    "detector_artifact": {"sha256": "d" * 64},
                    "ocr_artifact": {"sha256": "o" * 64, "ocr_config_sha256": "c" * 64},
                    "runtime": {"onnxruntime_version": "1.30.0"},
                },
            }
        )
        # The manifest is linked through the existing case_action_events.review_id
        # column, so link it to a review row the case was materialized from.
        manifests.write_index(manifests.review_key(linked_review_id), {"attempt_id": "att_p1"})

        resolved = resolve_candidate(test_db, vid, "cand_1", review_id=linked_review_id)
        provenance = manifest_provenance_for_verification(resolved)
        verify_plate_identity(
            test_db,
            vid,
            admin,
            plate_status=HUMAN_PLATE_STATUS_VERIFIED_READABLE,
            accepted_plate_text="ABC123",
            candidate_ocr_raw=resolved["ocr_raw"],
            candidate_reference="att_p1:cand_1",
            machine_provenance=provenance,
            review_id=linked_review_id,
        )
        row = test_db.get_plate_verification(vid)
        diagnostics = json.loads(row["processing_diagnostics_json"])
        assert diagnostics["machine_attempt_id"] == "att_p1"
        assert diagnostics["machine_candidate_id"] == "cand_1"
        assert diagnostics["machine_ocr_raw"] == "ABC123"
        assert diagnostics["machine_detector_sha256"] == "d" * 64
        assert diagnostics["machine_provider_requested"] == "CPUExecutionProvider"
        assert row["accepted_plate_text"] == "ABC123"

        # A later correction that names no candidate must not erase provenance.
        verify_plate_identity(
            test_db,
            vid,
            admin,
            plate_status=HUMAN_PLATE_STATUS_VERIFIED_READABLE,
            accepted_plate_text="ABC124",
        )
        diagnostics = json.loads(
            test_db.get_plate_verification(vid)["processing_diagnostics_json"]
        )
        assert diagnostics["machine_attempt_id"] == "att_p1"

    def test_machine_job_cannot_write_identity(self, plate_root_env, test_db):
        admin = test_db.create_user("p_admin", "hash", role="admin")
        vid = _make_violation(test_db, admin)
        # A machine attempt exists and is linked, but nothing in the machine path
        # touches plate_verifications or the legacy violation columns.
        manifests.write_manifest(
            {
                "contract_version": CONTRACT_VERSION,
                "attempt_id": "att_m",
                "review_id": 1,
                "case_id": None,
                "outcome": "candidate_found",
                "candidates": [{"candidate_id": "c1", "ocr_raw": "ABC123"}],
                "samples": [],
                "machine_provenance": {},
            }
        )
        assert test_db.get_plate_verification(vid) is None
        assert test_db.get_violation(vid)["plate_status"] == "not_attempted"
        assert test_db.get_violation(vid)["plate_text"] is None

    def test_late_machine_result_cannot_overwrite_accepted_plate(
        self, plate_root_env, test_db
    ):
        from core.case_review_service import verify_plate_identity
        from core.plate_processing import HUMAN_PLATE_STATUS_VERIFIED_READABLE

        admin = test_db.create_user("p_admin", "hash", role="admin")
        vid = _make_violation(test_db, admin)
        verify_plate_identity(
            test_db,
            vid,
            admin,
            plate_status=HUMAN_PLATE_STATUS_VERIFIED_READABLE,
            accepted_plate_text="HUMAN1",
        )
        before = dict(test_db.get_plate_verification(vid))
        # A late machine attempt lands for the same review.
        manifests.write_manifest(
            {
                "contract_version": CONTRACT_VERSION,
                "attempt_id": "att_late",
                "review_id": 1,
                "case_id": vid,
                "outcome": "candidate_found",
                "candidates": [{"candidate_id": "c1", "ocr_raw": "MACHINE1"}],
                "samples": [],
                "machine_provenance": {},
            }
        )
        manifests.write_index(manifests.review_key(1), {"attempt_id": "att_late"})
        after = test_db.get_plate_verification(vid)
        assert after["accepted_plate_text"] == before["accepted_plate_text"] == "HUMAN1"
        assert after["plate_status"] == HUMAN_PLATE_STATUS_VERIFIED_READABLE
        assert after["verified_by"] == admin

    def test_cross_case_candidate_reference_refused(self, plate_root_env, test_db):
        from core.plate_review import PlateReviewError, resolve_candidate

        admin = test_db.create_user("p_admin", "hash", role="admin")
        case_a = _make_violation(test_db, admin)
        case_b = _make_violation(test_db, admin)
        manifests.write_manifest(
            {
                "contract_version": CONTRACT_VERSION,
                "attempt_id": "att_a",
                "review_id": 1,
                "case_id": case_a,
                "outcome": "candidate_found",
                "candidates": [{"candidate_id": "c_secret", "ocr_raw": "SECRET"}],
                "samples": [],
                "machine_provenance": {},
            }
        )
        manifests.write_index(manifests.review_key(1), {"attempt_id": "att_a"})
        with pytest.raises(PlateReviewError):
            resolve_candidate(test_db, case_b, "c_secret", review_id=2)

    def test_spoofed_candidate_id_is_refused(self, plate_root_env, test_db):
        from core.plate_review import PlateReviewError, resolve_candidate

        admin = test_db.create_user("p_admin", "hash", role="admin")
        case_a = _make_violation(test_db, admin)
        with pytest.raises(PlateReviewError):
            resolve_candidate(test_db, case_a, "made_up", review_id=1)


# ----------------------------------------------------------------------
# 12. HTTP surface
# ----------------------------------------------------------------------


class TestHttpSurface:
    @staticmethod
    def _login_admin(client):
        password = "isolated-plate-ocr-test-pw"
        resp = client.post("/login", data={"username": "admin", "password": password})
        assert resp.status_code in (200, 302), resp.status_code

    def test_plate_route_is_admin_only(self, plate_admin_env, client, test_db):
        import bcrypt

        from database import db

        db.create_user(
            "http_enf", bcrypt.hashpw(b"enforcer123", bcrypt.gensalt()).decode("utf-8"), role="enforcer"
        )
        client.post("/login", data={"username": "http_enf", "password": "enforcer123"})
        resp = client.post("/api/cases/1/plate", json={"plate_status": "unclear"})
        assert resp.status_code == 403

    def test_viewer_cannot_confirm_plate(self, plate_admin_env, client, test_db):
        import bcrypt

        from database import db

        db.create_user(
            "http_view", bcrypt.hashpw(b"viewer123", bcrypt.gensalt()).decode("utf-8"), role="viewer"
        )
        client.post("/login", data={"username": "http_view", "password": "viewer123"})
        resp = client.post("/api/cases/1/plate", json={"plate_status": "unclear"})
        assert resp.status_code == 403

    def test_inactive_admin_cannot_confirm_plate(self, plate_admin_env, client, test_db):
        import bcrypt

        from database import db

        uid = db.create_user(
            "http_off", bcrypt.hashpw(b"off12345", bcrypt.gensalt()).decode("utf-8"), role="admin"
        )
        db.update_user(uid, is_active=0)
        client.post("/login", data={"username": "http_off", "password": "off12345"})
        resp = client.post("/api/cases/1/plate", json={"plate_status": "unclear"})
        assert resp.status_code in (401, 403)

    def test_status_endpoint_is_safe_when_disabled(self, plate_admin_env, client):
        self._login_admin(client)
        resp = client.get("/api/plate-ocr/status")
        assert resp.status_code == 200
        payload = resp.get_json()
        assert payload["success"] is True
        assert "plate_ocr" in payload
        assert payload["plate_ocr"]["experimental"] is True
        assert payload["plate_ocr"]["can_confirm_plate"] is True

    def test_status_endpoint_requires_login(self, plate_admin_env, client):
        resp = client.get("/api/plate-ocr/status")
        assert resp.status_code == 401

    def test_plate_crop_traversal_refused(self, plate_admin_env, client, test_db):
        self._login_admin(client)
        resp = client.get("/api/review-queue/1/plate-crop/..%2F..%2Fetc/passwd/x.jpg")
        assert resp.status_code in (401, 403, 404)

    def test_plate_crop_missing_evidence_is_404(self, plate_admin_env, client, test_db):
        self._login_admin(client)
        resp = client.get("/api/cases/1/plate-crop/att_missing/plate_c.png")
        assert resp.status_code in (404,)

    def test_admin_can_still_use_the_route(self, plate_admin_env, client, test_db):
        import bcrypt

        from database import db

        db.create_user(
            "http_admin2",
            bcrypt.hashpw(b"admin12345", bcrypt.gensalt()).decode("utf-8"),
            role="admin",
        )
        client.post("/login", data={"username": "http_admin2", "password": "admin12345"})
        resp = client.post("/api/cases/1/plate", json={"plate_status": "unclear"})
        # A missing case is a 400, not a 403: authorization passed.
        assert resp.status_code == 400

class TestReviewFlowEndToEnd:
    """The serialized review-queue payload a browser receives.

    This is the automated form of the browser review-flow check: it asserts the
    exact machine block the review queue renders, that OCR text is present only
    as escaped data, and that the confirmation affordance is admin-only.
    """

    def _seed(self, plate_root_env, test_db, admin):
        rid = test_db.insert_review_queue(
            video_id=None,
            track_id=4,
            violation_type="no_parking",
            confidence=0.8,
            frame_number=20,
            evidence_path="/tmp/e.jpg",
        )
        crop = manifests.save_crop(
            "att_flow",
            "plate_cand_1.png",
            make_plate_image(60, 20),
        )
        manifests.write_manifest(
            {
                "contract_version": CONTRACT_VERSION,
                "attempt_id": "att_flow",
                "review_id": rid,
                "case_id": None,
                "outcome": "candidate_found",
                "outcome_is_ambiguous": False,
                "source": {
                    "kind": "video",
                    "run_key": "run_1",
                    "live_session_id": None,
                    "track_id": 4,
                    "track_identity_epoch": 1,
                    "violation_type": "no_parking",
                },
                "trigger": {"frame_number": 20, "timestamp_sec": 2.0},
                "candidates": [
                    {
                        "candidate_id": "cand_1",
                        "sample_id": "s1",
                        "ocr_raw": '<img src=x onerror="alert(1)">ABC',
                        "ocr_display_normalized": "IMG",
                        "detection_label": "License Plate",
                        "detection_confidence": 0.61,
                        "ocr_scalar_score": 3.9,
                        "ocr_char_scores": [3.9],
                        "ocr_score_semantics": "raw uncalibrated logit",
                        "plate_box_in_crop": [1, 2, 40, 12],
                        "plate_box_in_frame": [81, 102, 120, 112],
                        "plate_crop_ref": crop,
                    }
                ],
                "primary_candidate_id": "cand_1",
                "samples": [
                    {
                        "sample_id": "s1",
                        "frame_number": 20,
                        "timestamp_sec": 2.0,
                        "frame_width": 320,
                        "frame_height": 240,
                        "vehicle_box_frame_px": [80, 100, 240, 200],
                        "crop_origin_frame_px": [80, 100],
                        "crop_width": 160,
                        "crop_height": 100,
                    }
                ],
                "association_uncertain": False,
                "quality_rejections": [],
                "calls": {"detector": 1, "ocr": 1, "detector_limit": 3, "ocr_limit": 6},
                "truncated": {"detector": False, "ocr": False, "collection": False},
                "machine_provenance": {
                    "requested_provider": "CPUExecutionProvider",
                    "active_providers": ["CPUExecutionProvider"],
                    "detector_artifact": {"sha256": "d" * 64},
                    "ocr_artifact": {"sha256": "o" * 64, "ocr_config_sha256": "c" * 64},
                    "runtime": {"onnxruntime_version": "1.30.0"},
                },
            }
        )
        manifests.write_index(manifests.review_key(rid), {"attempt_id": "att_flow"})
        return rid, admin

    def test_enforcer_sees_candidate_without_confirmation(self, plate_root_env, plate_admin_env, client, test_db):
        import bcrypt

        from database import db

        admin = self._seed(plate_root_env, test_db, None)
        rid, _ = admin
        db.create_user(
            "flow_enf", bcrypt.hashpw(b"enforcer123", bcrypt.gensalt()).decode("utf-8"), role="enforcer"
        )
        client.post("/login", data={"username": "flow_enf", "password": "enforcer123"})
        payload = client.get("/api/review-queue").get_json()
        item = next(i for i in payload["items"] if i["id"] == rid)
        machine = item["plate_machine"]
        assert machine["experimental"] is True
        assert machine["human_review_required"] is True
        assert machine["state"] == "candidate_found"
        assert machine["eligible_for_confirmation"] is True
        assert machine["association_uncertain"] is False
        assert machine["primary_candidate_id"] == "cand_1"
        candidate = machine["candidates"][0]
        # Raw text is transported as data; the JS layer escapes it.
        assert candidate["ocr_raw"] == '<img src=x onerror="alert(1)">ABC'
        assert candidate["detection_confidence"] == 0.61
        assert candidate["track_id"] == 4
        assert candidate["frame_number"] == 20
        assert candidate["vehicle_box_in_frame"] == [80, 100, 240, 200]
        assert candidate["plate_crop_available"] is True
        assert machine["artifact"]["provider_active"] == ["CPUExecutionProvider"]
        # The queue row itself is untouched by OCR.
        assert item["plate_status"] == "not_attempted"
        assert item["plate_text"] is None
        assert item["status"] == "pending"

    def test_association_uncertain_blocks_confirmation(self, plate_root_env, plate_admin_env, client, test_db):
        from core import plate_manifest as pm

        rid, _ = self._seed(plate_root_env, test_db, None)
        data = pm.load_manifest("att_flow").data
        data["association_uncertain"] = True
        data["outcome"] = "association_uncertain"
        path = pm.attempt_manifest_path("att_flow")
        path.unlink()
        pm.write_manifest(data)
        pm.write_index(pm.review_key(rid), {"attempt_id": "att_flow"})
        client.post("/login", data={"username": "admin", "password": plate_admin_env})
        payload = client.get("/api/review-queue").get_json()
        item = next(i for i in payload["items"] if i["id"] == rid)
        machine = item["plate_machine"]
        assert machine["association_uncertain"] is True
        assert machine["eligible_for_confirmation"] is False
        assert machine["state"] == "association_uncertain"

    def test_admin_can_confirm_a_resolved_candidate(self, plate_root_env, plate_admin_env, client, test_db):
        rid, _ = self._seed(plate_root_env, test_db, None)
        client.post("/login", data={"username": "admin", "password": plate_admin_env})
        payload = client.get("/api/review-queue").get_json()
        item = next(i for i in payload["items"] if i["id"] == rid)
        assert item["plate_machine"]["eligible_for_confirmation"] is True

    def test_plate_crop_endpoint_is_record_scoped(self, plate_root_env, plate_admin_env, client, test_db):
        rid, _ = self._seed(plate_root_env, test_db, None)
        client.post("/login", data={"username": "admin", "password": plate_admin_env})
        ok = client.get(f"/api/review-queue/{rid}/plate-crop/att_flow/plate_cand_1.png")
        assert ok.status_code == 200
        assert ok.mimetype == "image/png"
        # Another review id must not reach this attempt.
        other = test_db.insert_review_queue(
            video_id=None,
            track_id=5,
            violation_type="no_parking",
            confidence=0.8,
            frame_number=1,
        )
        denied = client.get(f"/api/review-queue/{other}/plate-crop/att_flow/plate_cand_1.png")
        assert denied.status_code == 404
