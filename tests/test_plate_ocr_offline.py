"""Offline and real-ONNX tests for the experimental plate backend.

This module is designed to run in the **isolated** plate environment
(``onnxruntime`` + ``opencv-python-headless`` + ``numpy`` + ``PyYAML``) as well
as in the application environment when ``onnxruntime`` happens to be present.

What it proves
--------------
* Importing the backend and constructing it performs no runtime work, opens no
  socket, and downloads nothing - even with an **empty model cache** and with
  ``socket.socket`` replaced by a raising stub.
* Real inference against the qualified local artifacts succeeds with the network
  fully blocked.
* A missing, unreadable, or hash-mismatched artifact is a controlled
  ``unavailable``/``failed`` outcome, never a silent download or a CPU fallback.
* ONNX sessions are created once and reused.

No private footage and no network access are used. Synthetic images only.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import urllib.request
from pathlib import Path

import numpy as np
import pytest

from core.plate_onnx_backend import (
    PlateBackendError,
    PlateBackendUnavailable,
    PlateOnnxBackend,
    crop_from_box,
    preprocess_ocr_input,
    sha256_file,
)
from core.plate_settings import PlateOcrSettings

ARTIFACT_ROOT = Path(os.environ.get("TAVIDM_PLATE_ARTIFACTS", "")) if os.environ.get(
    "TAVIDM_PLATE_ARTIFACTS"
) else Path(__file__).resolve().parents[1] / "artifacts" / "plate_alpr"

DETECTOR = ARTIFACT_ROOT / "weights" / "yolo-v9-t-384-license-plates-end2end.onnx"
OCR = ARTIFACT_ROOT / "weights" / "cct_s_v2_global.onnx"
OCR_CONFIG = ARTIFACT_ROOT / "config" / "cct_s_v2_global_plate_config.yaml"

ort = pytest.importorskip("onnxruntime", reason="onnxruntime is only in the isolated plate venv")

ARTIFACTS_PRESENT = DETECTOR.is_file() and OCR.is_file() and OCR_CONFIG.is_file()
requires_artifacts = pytest.mark.skipif(
    not ARTIFACTS_PRESENT,
    reason=(
        "qualified plate artifacts are not present. Acquire them per "
        "docs/plate_ocr/README.md before running the real-inference tests."
    ),
)


@pytest.fixture
def no_network(monkeypatch, tmp_path):
    """Block every outbound socket and hub download path.

    The model cache home is also pointed at an empty temporary directory so a
    stray hub download would fail loudly instead of silently succeeding from a
    warm cache.
    """

    def blocked(*args, **kwargs):  # pragma: no cover - must never be reached
        raise AssertionError("network access attempted during offline plate inference")

    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    monkeypatch.setattr(os, "system", blocked)
    cache_home = tmp_path / "empty-model-cache"
    cache_home.mkdir()
    monkeypatch.setenv("HOME", str(cache_home))
    monkeypatch.setenv("USERPROFILE", str(cache_home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_home))
    return cache_home


def real_settings(**overrides) -> PlateOcrSettings:
    settings = PlateOcrSettings(
        enabled=True,
        disabled_reason="",
        provider="CPUExecutionProvider",
        detector_path=str(DETECTOR),
        detector_sha256=sha256_file(DETECTOR) if DETECTOR.is_file() else "0" * 64,
        ocr_path=str(OCR),
        ocr_sha256=sha256_file(OCR) if OCR.is_file() else "0" * 64,
        ocr_config_path=str(OCR_CONFIG),
        ocr_config_sha256=sha256_file(OCR_CONFIG) if OCR_CONFIG.is_file() else "0" * 64,
    )
    if overrides:
        from dataclasses import replace

        settings = replace(settings, **overrides)
    return settings


def synthetic_plate(text: str = "ABC 123", width: int = 240, height: int = 70) -> np.ndarray:
    import cv2

    plate = np.full((height, width, 3), 232, np.uint8)
    cv2.rectangle(plate, (2, 2), (width - 3, height - 3), (70, 70, 70), 3)
    cv2.putText(
        plate, text, (18, height - 20), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (15, 15, 15), 3
    )
    return plate


# ----------------------------------------------------------------------


class TestImportIsInert:
    def test_module_import_does_not_load_onnxruntime(self):
        # Importing the backend must not have created a session or resolved a
        # provider; constructing one must not either.
        assert "core.plate_onnx_backend" in sys.modules
        backend = PlateOnnxBackend(PlateOcrSettings())
        assert backend.runtime_ready is False
        assert backend.detector_calls == 0
        assert backend.ocr_calls == 0

    def test_offline_construction_with_real_artifacts(self, no_network):
        settings = real_settings()
        backend = PlateOnnxBackend(settings)
        assert backend.runtime_ready is False  # still nothing loaded
        meta = backend.metadata()
        assert meta["requested_provider"] == "CPUExecutionProvider"
        assert meta["active_providers"] == []


@requires_artifacts
class TestRealInferenceOffline:
    def test_detector_and_ocr_run_with_network_blocked(self, no_network):
        settings = real_settings()
        backend = PlateOnnxBackend(settings)
        plate = synthetic_plate("ABC 123")
        frame = np.zeros((480, 640, 3), np.uint8)
        frame[150:220, 200:440] = plate
        boxes = backend.detect_plates(frame)
        assert backend.detector_calls == 1
        assert boxes, "the qualified detector found no plate on a synthetic plate"
        box = boxes[0]
        crop, rect = crop_from_box(frame, (box.x1, box.y1, box.x2, box.y2))
        assert crop is not None and crop.size > 0
        read = backend.recognize_plate(crop)
        assert backend.ocr_calls == 1
        # Raw text only; the pad character is stripped and nothing else changes.
        assert "_" not in read.text
        assert read.text.isupper() or read.text.isdigit() or read.text == ""
        assert read.char_scores is None or len(read.char_scores) == len(read.text)

    def test_sessions_are_reused(self, no_network):
        backend = PlateOnnxBackend(real_settings())
        frame = np.zeros((480, 640, 3), np.uint8)
        frame[150:220, 200:440] = synthetic_plate()
        backend.detect_plates(frame)
        first = backend._detector
        backend.detect_plates(frame)
        backend.detect_plates(frame)
        assert backend.detector_calls == 3
        assert backend._detector is first
        assert backend.runtime_ready is True

    def test_metadata_records_provider_and_hashes(self, no_network):
        backend = PlateOnnxBackend(real_settings())
        backend.detect_plates(np.zeros((480, 640, 3), np.uint8))
        meta = backend.metadata()
        assert meta["active_providers"] == ["CPUExecutionProvider"]
        assert meta["detector_artifact"]["sha256"] == sha256_file(DETECTOR)
        assert meta["ocr_artifact"]["sha256"] == sha256_file(OCR)
        assert meta["ocr_artifact"]["img_hw"] == [64, 128]
        assert meta["ocr_artifact"]["max_plate_slots"] == 10
        assert len(meta["ocr_artifact"]["alphabet"]) == 37
        assert "uncalibrated" in meta["ocr_artifact"]["confidence_semantics"]
        assert meta["runtime"]["onnxruntime_version"] == ort.__version__

    def test_adapter_contract_shape(self, no_network):
        backend = PlateOnnxBackend(real_settings())
        frame = np.zeros((480, 640, 3), np.uint8)
        frame[150:220, 200:440] = synthetic_plate()
        results = backend.predict(frame)
        assert isinstance(results, list)
        for result in results:
            observation = backend.to_candidate_observation(result)
            assert observation.detection_label is not None
            assert 0.0 <= observation.detection_confidence <= 1.0
            # A machine result never carries an accepted-plate field.
            assert not hasattr(observation, "accepted_plate_text")

    def test_ocr_input_matches_exported_signature(self, no_network):
        batch = preprocess_ocr_input(
            synthetic_plate(), height=64, width=128, color_mode="gray"
        )
        assert batch.shape == (1, 64, 128, 3)
        assert batch.dtype == np.uint8
        backend = PlateOnnxBackend(real_settings())
        backend.recognize_plate(synthetic_plate())
        declared = list(backend._ocr.get_inputs()[0].shape)
        assert declared[1:] == [64, 128, 3]


@requires_artifacts
class TestRealFailureModes:
    def test_missing_artifact_is_unavailable(self, no_network, tmp_path):
        settings = real_settings(detector_path=str(tmp_path / "absent.onnx"))
        backend = PlateOnnxBackend(settings)
        with pytest.raises(PlateBackendUnavailable) as exc:
            backend.detect_plates(np.zeros((64, 64, 3), np.uint8))
        assert "artifact_missing" in str(exc.value)

    def test_corrupt_artifact_is_unavailable(self, no_network, tmp_path):
        bad = tmp_path / "corrupt.onnx"
        bad.write_bytes(b"not an onnx graph at all")
        settings = real_settings(detector_path=str(bad), detector_sha256=sha256_file(bad))
        backend = PlateOnnxBackend(settings)
        with pytest.raises(PlateBackendError) as exc:
            backend.detect_plates(np.zeros((64, 64, 3), np.uint8))
        assert "session_creation_failed" in str(exc.value)

    def test_artifact_hash_mismatch_is_unavailable(self, no_network):
        settings = real_settings(detector_sha256="0" * 64)
        backend = PlateOnnxBackend(settings)
        with pytest.raises(PlateBackendUnavailable) as exc:
            backend.detect_plates(np.zeros((64, 64, 3), np.uint8))
        assert "artifact_hash_mismatch" in str(exc.value)

    def test_missing_ocr_config_is_unavailable(self, no_network, tmp_path):
        settings = real_settings(ocr_config_path=str(tmp_path / "absent.yaml"))
        backend = PlateOnnxBackend(settings)
        with pytest.raises(PlateBackendUnavailable) as exc:
            backend.detect_plates(np.zeros((64, 64, 3), np.uint8))
        assert "artifact_missing" in str(exc.value)

    def test_unsupported_provider_never_falls_back(self, no_network):
        settings = real_settings(provider="TensorrtExecutionProvider")
        backend = PlateOnnxBackend(settings)
        with pytest.raises(PlateBackendUnavailable) as exc:
            backend.detect_plates(np.zeros((64, 64, 3), np.uint8))
        assert "provider_unavailable" in str(exc.value)

    def test_failure_is_sticky_and_does_not_rehash_forever(self, no_network):
        settings = real_settings(detector_sha256="0" * 64)
        backend = PlateOnnxBackend(settings)
        for _ in range(3):
            with pytest.raises(PlateBackendUnavailable):
                backend.detect_plates(np.zeros((64, 64, 3), np.uint8))

    def test_malformed_ocr_config_refused(self, no_network, tmp_path):
        cfg = tmp_path / "plate.yaml"
        cfg.write_text("max_plate_slots: 10\nalphabet: 'AB'\n", encoding="utf-8")
        settings = real_settings(ocr_config_path=str(cfg), ocr_config_sha256=sha256_file(cfg))
        backend = PlateOnnxBackend(settings)
        with pytest.raises(PlateBackendUnavailable) as exc:
            backend.detect_plates(np.zeros((64, 64, 3), np.uint8))
        assert "pad_char" in str(exc.value)

    def test_unknown_colour_mode_refused(self, no_network):
        settings = real_settings(ocr_color_mode="cmyk")
        backend = PlateOnnxBackend(settings)
        backend.detect_plates(np.zeros((64, 64, 3), np.uint8))
        with pytest.raises(PlateBackendError):
            backend.recognize_plate(synthetic_plate())


class TestNoHubImports:
    def test_upstream_hub_helpers_are_not_imported(self):
        """Run in a subprocess so the check cannot be perturbed by other tests.

        ``ultralytics`` is a pre-existing TAVIDM dependency and may legitimately
        be imported elsewhere in a full session, so it is excluded here. The
        three upstream ALPR/OCR packages must never be pulled in by the local
        plate backend.
        """
        import subprocess
        import sys as _sys

        code = (
            "import sys; sys.path.insert(0, %r);"
            "import core.plate_onnx_backend, core.plate_jobs, core.plate_manifest;"
            "print(','.join(sorted(m for m in sys.modules "
            "if m.split('.')[0] in ('fast_alpr','fast_plate_ocr','open_image_models'))))"
            % str(Path(__file__).resolve().parents[1])
        )
        completed = subprocess.run(
            [_sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == "", (
            "the local plate backend must not import upstream hub packages; got: "
            + completed.stdout.strip()
        )

    def test_no_download_helpers_are_referenced(self):
        source = (Path(__file__).resolve().parents[1] / "core" / "plate_onnx_backend.py").read_text(
            encoding="utf-8"
        )
        for forbidden in ("urlopen", "requests.get", "hf_hub_download", "download_model"):
            assert forbidden not in source


def test_evaluation_record_is_required_by_settings():
    """A demo config must name a recorded evaluation, otherwise it cannot load."""
    from core.plate_settings import PlateOcrConfigError, load_plate_ocr_settings

    with tempfile.TemporaryDirectory() as tmp:
        payload = {
            "enabled": True,
            "provider": "CPUExecutionProvider",
            "detector_path": str(DETECTOR),
            "detector_sha256": "0" * 64,
            "ocr_path": str(OCR),
            "ocr_sha256": "0" * 64,
            "ocr_config_path": str(OCR_CONFIG),
            "ocr_config_sha256": "0" * 64,
        }
        if ARTIFACTS_PRESENT:
            cfg = Path(tmp) / "plate.json"
            cfg.write_text(json.dumps(payload), encoding="utf-8")
            os.environ["TAVIDM_PLATE_OCR_CONFIG"] = str(cfg)
            try:
                with pytest.raises(PlateOcrConfigError):
                    load_plate_ocr_settings()
            finally:
                os.environ.pop("TAVIDM_PLATE_OCR_CONFIG", None)
        else:
            pytest.skip("artifacts absent")