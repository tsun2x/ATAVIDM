"""Mocked Stage C1 tests for the standalone FastALPR plate adapter.

No dependency install, model download, network access, real inference,
database writes, or application worker startup.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Upstream-shaped fixtures (mirror fast_alpr ALPRResult / OcrResult /
# DetectionResult / BoundingBox — not a fictional test-only API)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FakeBoundingBox:
    x1: int
    y1: int
    x2: int
    y2: int


@dataclass(frozen=True)
class FakeDetectionResult:
    label: str
    confidence: float
    bounding_box: FakeBoundingBox


@dataclass(frozen=True)
class FakeOcrResult:
    text: str
    confidence: float | list[float]
    region: str | None = None
    region_confidence: float | None = None


@dataclass(frozen=True)
class FakeALPRResult:
    detection: FakeDetectionResult
    ocr: FakeOcrResult | None


class RecordingBackend:
    """Injected backend compatible with ALPR.predict(ndarray) -> list[ALPRResult]."""

    def __init__(self, results: list[Any] | None = None, *, exc: Exception | None = None):
        self._results = results if results is not None else []
        self._exc = exc
        self.calls: list[np.ndarray] = []
        self.call_count = 0

    def predict(self, frame: np.ndarray) -> list[Any]:
        self.call_count += 1
        self.calls.append(frame)
        if self._exc is not None:
            raise self._exc
        return list(self._results)


def _sample_image(*, value: int = 7) -> np.ndarray:
    return np.full((40, 80, 3), value, dtype=np.uint8)


def _candidate(
    *,
    text: str = "ABC123",
    ocr_conf: float | list[float] = 0.97,
    det_conf: float = 0.91,
    label: str = "license_plate",
    box: tuple[int, int, int, int] = (10, 12, 60, 30),
    ocr: FakeOcrResult | None | object = ...,
) -> FakeALPRResult:
    detection = FakeDetectionResult(
        label=label,
        confidence=det_conf,
        bounding_box=FakeBoundingBox(*box),
    )
    if ocr is ...:
        ocr_result: FakeOcrResult | None = FakeOcrResult(text=text, confidence=ocr_conf)
    else:
        ocr_result = ocr  # type: ignore[assignment]
    return FakeALPRResult(detection=detection, ocr=ocr_result)


# ---------------------------------------------------------------------------
# Import / isolation
# ---------------------------------------------------------------------------


class TestImportSafety:
    def test_module_imports_without_fast_alpr(self):
        import core.plate_processing as pp

        # Re-import should still succeed whether or not the package exists.
        assert pp.PlateProcessor is not None
        assert hasattr(pp, "is_fast_alpr_available")

    def test_import_does_not_init_model_worker_or_database(self, monkeypatch):
        import sys

        # Ensure a fresh import path cannot pull application/db modules.
        blocked_app = {
            "database",
            "database.sqlite_adapter",
            "database.db",
            "app",
            "server",
        }
        for name in list(sys.modules):
            if (
                name in blocked_app
                or name.startswith("database.")
                or name.startswith("app")
                or name == "fast_alpr"
                or name.startswith("fast_alpr.")
            ):
                monkeypatch.delitem(sys.modules, name, raising=False)

        real_import = __import__

        def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
            root = name.split(".", 1)[0]
            if name == "fast_alpr" or name.startswith("fast_alpr."):
                raise ImportError("fast_alpr not installed (test guard)")
            if root in {"database", "app", "server"} or name in blocked_app:
                raise AssertionError(f"unexpected import during adapter load: {name}")
            return real_import(name, globals, locals, fromlist, level)

        monkeypatch.setattr("builtins.__import__", guarded_import)
        sys.modules.pop("core.plate_processing", None)
        import core.plate_processing as pp

        assert pp.is_fast_alpr_available() is False
        status = pp.real_fast_alpr_backend_factory_status()
        assert status["factory_available"] is False
        assert "download" in status["reason"].lower()


# ---------------------------------------------------------------------------
# Happy-path / candidate semantics
# ---------------------------------------------------------------------------


class TestCandidateResults:
    def test_fake_backend_produces_structured_candidate(self):
        from core.plate_processing import (
            BackendMetadata,
            PlateProcessingOutcome,
            PlateProcessor,
        )

        backend = RecordingBackend([_candidate(text="ABC 1234", ocr_conf=0.99)])
        processor = PlateProcessor(
            backend,
            metadata=BackendMetadata(
                provider="fast_alpr",
                model_name="yolo-v9-t-384-license-plate-end2end+cct-xs-v2-global-model",
                model_version="injected-test",
            ),
        )
        result = processor.read_plate(_sample_image(), evidence_ref="crop://case-1")

        assert result.outcome == PlateProcessingOutcome.CANDIDATE_FOUND
        assert len(result.candidates) == 1
        cand = result.candidates[0]
        assert cand.ocr_raw == "ABC 1234"
        assert cand.ocr_confidence == pytest.approx(0.99)
        assert cand.detection_confidence == pytest.approx(0.91)
        assert cand.bounding_box is not None
        assert cand.bounding_box.x1 == 10
        assert result.evidence_ref == "crop://case-1"
        assert result.provider == "fast_alpr"
        assert result.model_name is not None
        # No accepted identity field on machine result / candidate.
        assert not hasattr(cand, "accepted_plate_text")
        assert not hasattr(result, "accepted_plate_text")

    def test_multiple_candidates_retained_without_identity(self):
        from core.plate_processing import PlateProcessingOutcome, PlateProcessor

        backend = RecordingBackend(
            [
                _candidate(text="AAA111", box=(1, 1, 20, 10)),
                _candidate(text="BBB222", box=(30, 5, 70, 25), det_conf=0.8),
            ]
        )
        result = PlateProcessor(backend).process(_sample_image())
        assert result.outcome == PlateProcessingOutcome.CANDIDATE_FOUND
        assert len(result.candidates) == 2
        texts = {c.ocr_raw for c in result.candidates}
        assert texts == {"AAA111", "BBB222"}
        # Adapter does not choose a single vehicle/plate identity.
        assert not hasattr(result, "selected_candidate")
        assert not hasattr(result, "accepted_plate_text")

    def test_empty_backend_output_is_no_candidate_not_not_visible(self):
        from core.plate_processing import (
            HUMAN_PLATE_STATUS_NOT_VISIBLE,
            PlateProcessingOutcome,
            PlateProcessor,
        )

        result = PlateProcessor(RecordingBackend([])).process(_sample_image())
        assert result.outcome == PlateProcessingOutcome.NO_CANDIDATE_DETECTED
        assert result.candidates == ()
        assert result.outcome.value != HUMAN_PLATE_STATUS_NOT_VISIBLE
        assert "not_visible" not in result.diagnostics.get("reason", "")
        assert result.diagnostics.get("note") == (
            "no_candidate_detected_does_not_mean_plate_not_visible"
        )

    def test_high_confidence_ocr_remains_unverified_candidate(self):
        from core.plate_processing import (
            HUMAN_PLATE_STATUS_VERIFIED_READABLE,
            PlateProcessingOutcome,
            PlateProcessor,
        )

        result = PlateProcessor(
            RecordingBackend([_candidate(text="XYZ999", ocr_conf=0.999)])
        ).process(_sample_image())
        assert result.outcome == PlateProcessingOutcome.CANDIDATE_FOUND
        assert result.candidates[0].ocr_confidence == pytest.approx(0.999)
        assert result.grants_human_verified_readable is False
        assert result.outcome.value != HUMAN_PLATE_STATUS_VERIFIED_READABLE
        assert result.grants_recurrence_eligibility is False

    def test_missing_partial_ocr_text_preserved_without_guessing(self):
        from core.plate_processing import PlateProcessingOutcome, PlateProcessor

        # Detection with OCR None (upstream allows ocr: OcrResult | None)
        no_ocr = _candidate(ocr=None)
        # Empty OCR text preserved (not filled)
        empty_ocr = _candidate(text="", ocr_conf=0.5)
        result = PlateProcessor(RecordingBackend([no_ocr, empty_ocr])).process(
            _sample_image()
        )
        assert result.outcome == PlateProcessingOutcome.CANDIDATE_FOUND
        assert result.candidates[0].ocr_raw is None
        assert result.candidates[1].ocr_raw == ""
        joined = "".join(c.ocr_raw or "" for c in result.candidates)
        assert "A" not in joined  # no invented characters

    def test_missing_confidence_remains_explicitly_unavailable(self):
        from core.plate_processing import PlateCandidateObservation, PlateProcessor

        class OddOcr:
            text = "PARTIAL"
            # confidence attribute absent

        class OddDetection:
            label = "license_plate"
            bounding_box = FakeBoundingBox(0, 0, 5, 5)
            # confidence attribute absent

        class OddResult:
            detection = OddDetection()
            ocr = OddOcr()

        result = PlateProcessor(RecordingBackend([OddResult()])).process(_sample_image())
        cand = result.candidates[0]
        assert isinstance(cand, PlateCandidateObservation)
        assert cand.ocr_raw == "PARTIAL"
        assert cand.ocr_confidence is None
        assert cand.detection_confidence is None
        assert cand.ocr_confidence != 0
        assert cand.detection_confidence != 0


# ---------------------------------------------------------------------------
# Failure / contract handling
# ---------------------------------------------------------------------------


class TestFailureHandling:
    def test_invalid_input_contract(self):
        from core.plate_processing import PlateProcessingOutcome, PlateProcessor

        backend = RecordingBackend([_candidate()])
        processor = PlateProcessor(backend)
        for bad in (None, "not-an-image", np.array([]), np.zeros((2,), dtype=np.uint8)):
            result = processor.process(bad)
            assert result.outcome == PlateProcessingOutcome.INVALID_INPUT
            assert backend.call_count == 0

    def test_backend_exception_controlled_failure(self):
        from core.plate_processing import PlateProcessingOutcome, PlateProcessor

        backend = RecordingBackend(exc=RuntimeError("secret-plate-ABC123-boom"))
        result = PlateProcessor(backend).process(_sample_image())
        assert result.outcome == PlateProcessingOutcome.PROCESSING_FAILED
        assert result.diagnostics["reason"] == "backend_exception"
        assert result.diagnostics["exception_type"] == "RuntimeError"
        # Sensitive plate-like content from exception must not appear in diagnostics.
        blob = str(result.diagnostics)
        assert "ABC123" not in blob
        assert "secret-plate" not in blob

    def test_malformed_results_without_fabricated_success(self):
        from core.plate_processing import PlateProcessingOutcome, PlateProcessor

        all_bad = PlateProcessor(RecordingBackend([object(), {"x": 1}])).process(
            _sample_image()
        )
        assert all_bad.outcome == PlateProcessingOutcome.PROCESSING_FAILED
        assert all_bad.candidates == ()

        mixed = PlateProcessor(
            RecordingBackend([object(), _candidate(text="KEEPME"), {"bad": True}])
        ).process(_sample_image())
        assert mixed.outcome == PlateProcessingOutcome.CANDIDATE_FOUND
        assert len(mixed.candidates) == 1
        assert mixed.candidates[0].ocr_raw == "KEEPME"
        assert mixed.diagnostics["skipped_malformed_count"] == 2

    def test_processing_unavailable_without_backend(self):
        from core.plate_processing import PlateProcessingOutcome, PlateProcessor

        result = PlateProcessor(None).process(_sample_image())
        assert result.outcome == PlateProcessingOutcome.PROCESSING_UNAVAILABLE
        assert result.diagnostics["reason"] == "no_backend_configured"


# ---------------------------------------------------------------------------
# Isolation / reuse / persistence boundary
# ---------------------------------------------------------------------------


class TestIsolation:
    def test_original_image_not_mutated(self):
        from core.plate_processing import PlateProcessor

        class MutatingBackend:
            def __init__(self):
                self.call_count = 0

            def predict(self, frame: np.ndarray):
                self.call_count += 1
                frame[:] = 0
                return [_candidate()]

        original = _sample_image(value=42)
        snapshot = original.copy()
        backend = MutatingBackend()
        PlateProcessor(backend).process(original)
        assert np.array_equal(original, snapshot)
        assert backend.call_count == 1

    def test_repeated_calls_reuse_supplied_backend(self):
        from core.plate_processing import PlateProcessor

        backend = RecordingBackend([_candidate()])
        processor = PlateProcessor(backend)
        processor.process(_sample_image(value=1))
        processor.read_plate(_sample_image(value=2))
        processor.process(_sample_image(value=3))
        assert backend.call_count == 3
        assert processor.backend is backend

    def test_adapter_does_not_call_database_persistence(self, monkeypatch):
        from core import plate_processing as pp

        sentinel = MagicMock(side_effect=AssertionError("db must not be touched"))
        monkeypatch.setattr(pp, "record_plate_verification", sentinel, raising=False)
        backend = RecordingBackend([_candidate()])
        result = pp.PlateProcessor(backend).process(_sample_image())
        assert result.candidates
        sentinel.assert_not_called()

    def test_no_result_grants_verified_or_recurrence(self):
        from core.plate_processing import (
            HUMAN_PLATE_STATUS_VERIFIED_READABLE,
            PlateProcessingOutcome,
            PlateProcessor,
        )

        cases = [
            PlateProcessor(None).process(_sample_image()),
            PlateProcessor(RecordingBackend([])).process(_sample_image()),
            PlateProcessor(RecordingBackend([_candidate(ocr_conf=1.0)])).process(
                _sample_image()
            ),
            PlateProcessor(RecordingBackend(exc=ValueError("x"))).process(
                _sample_image()
            ),
            PlateProcessor(RecordingBackend([_candidate()])).process(None),
        ]
        for result in cases:
            assert result.grants_human_verified_readable is False
            assert result.grants_recurrence_eligibility is False
            assert result.outcome.value != HUMAN_PLATE_STATUS_VERIFIED_READABLE
            assert HUMAN_PLATE_STATUS_VERIFIED_READABLE not in {
                c.ocr_raw for c in result.candidates
            }
            assert result.outcome in PlateProcessingOutcome


# ---------------------------------------------------------------------------
# Malformed-result hardening regressions
# ---------------------------------------------------------------------------


class TestMalformedBoundingBox:
    """Invalid coordinates must not escape; policy = retain without box."""

    def test_infinite_and_nan_coordinates_do_not_raise(self):
        from core.plate_processing import PlateProcessingOutcome, PlateProcessor

        class InfBox:
            x1 = 1
            y1 = 2
            x2 = 3
            y2 = float("inf")

        class NanBox:
            x1 = float("nan")
            y1 = 2
            x2 = 3
            y2 = 4

        class Det:
            def __init__(self, box):
                self.label = "license_plate"
                self.confidence = 0.88
                self.bounding_box = box

        class Res:
            def __init__(self, box):
                self.detection = Det(box)
                self.ocr = FakeOcrResult(text="SAFE", confidence=0.9)

        result = PlateProcessor(
            RecordingBackend([Res(InfBox()), Res(NanBox())])
        ).process(_sample_image())
        assert result.outcome == PlateProcessingOutcome.CANDIDATE_FOUND
        assert len(result.candidates) == 2
        assert all(c.bounding_box is None for c in result.candidates)
        assert all(c.ocr_raw == "SAFE" for c in result.candidates)
        assert result.diagnostics["malformed_bounding_box_count"] == 2
        assert result.diagnostics["retained_without_bounding_box_count"] == 2
        assert result.diagnostics["malformed_bounding_box_policy"] == (
            "retain_candidate_without_box"
        )
        blob = str(result.diagnostics)
        assert "SAFE" not in blob
        assert "OverflowError" not in blob

    def test_unsupported_coordinates_controlled_result(self):
        from core.plate_processing import PlateProcessingOutcome, PlateProcessor

        class BadBox:
            x1 = "left"
            y1 = 1
            x2 = 2
            y2 = 3

        class Det:
            label = "license_plate"
            confidence = 0.5
            bounding_box = BadBox()

        class Res:
            detection = Det()
            ocr = None

        result = PlateProcessor(RecordingBackend([Res()])).process(_sample_image())
        assert result.outcome == PlateProcessingOutcome.CANDIDATE_FOUND
        assert len(result.candidates) == 1
        assert result.candidates[0].bounding_box is None
        assert result.candidates[0].detection_label == "license_plate"
        assert result.candidates[0].detection_confidence == pytest.approx(0.5)

    def test_valid_candidate_survives_beside_malformed(self):
        from core.plate_processing import PlateProcessingOutcome, PlateProcessor

        class InfBox:
            x1 = y1 = x2 = 0
            y2 = float("inf")

        class BadDet:
            label = "license_plate"
            confidence = 0.4
            bounding_box = InfBox()

        class BadRes:
            detection = BadDet()
            ocr = FakeOcrResult(text="IGNORE_ME_IN_DIAG", confidence=0.1)

        result = PlateProcessor(
            RecordingBackend([BadRes(), _candidate(text="KEEP", box=(5, 5, 15, 15))])
        ).process(_sample_image())
        assert result.outcome == PlateProcessingOutcome.CANDIDATE_FOUND
        assert len(result.candidates) == 2
        assert result.candidates[0].bounding_box is None
        assert result.candidates[1].bounding_box is not None
        assert result.candidates[1].ocr_raw == "KEEP"
        assert "IGNORE_ME_IN_DIAG" not in str(result.diagnostics)

    def test_entirely_unusable_result_set_is_controlled_failure(self):
        from core.plate_processing import PlateProcessingOutcome, PlateProcessor

        class EmptyDet:
            label = None
            confidence = None
            bounding_box = None

        class EmptyRes:
            detection = EmptyDet()
            ocr = None

        result = PlateProcessor(
            RecordingBackend([EmptyRes(), object(), {"x": 1}])
        ).process(_sample_image())
        assert result.outcome == PlateProcessingOutcome.PROCESSING_FAILED
        assert result.candidates == ()
        assert result.grants_human_verified_readable is False
        assert result.grants_recurrence_eligibility is False


class TestMalformedOcrText:
    def test_non_string_ocr_text_not_stringified(self):
        from core.plate_processing import PlateProcessingOutcome, PlateProcessor

        class Det:
            label = "license_plate"
            confidence = 0.7
            bounding_box = FakeBoundingBox(1, 2, 3, 4)

        class Res:
            def __init__(self, text):
                self.detection = Det()
                self.ocr = type("O", (), {"text": text, "confidence": 0.5})()

        payloads = [
            {"unexpected": "value"},
            ["A", "B"],
            12345,
            True,
            b"ABC123",
            object(),
        ]
        backend = RecordingBackend([Res(p) for p in payloads])
        result = PlateProcessor(backend).process(_sample_image())
        assert result.outcome == PlateProcessingOutcome.CANDIDATE_FOUND
        assert len(result.candidates) == len(payloads)
        for cand in result.candidates:
            assert cand.ocr_raw is None
            assert cand.bounding_box is not None
            assert cand.detection_label == "license_plate"
        assert result.diagnostics["malformed_ocr_text_count"] == len(payloads)
        blob = str(result.diagnostics)
        assert "unexpected" not in blob
        assert "ABC123" not in blob
        assert "{'unexpected'" not in blob

    def test_actual_and_empty_strings_preserved(self):
        from core.plate_processing import PlateProcessor

        result = PlateProcessor(
            RecordingBackend(
                [
                    _candidate(text="AB 12"),
                    _candidate(text=""),
                ]
            )
        ).process(_sample_image())
        assert result.candidates[0].ocr_raw == "AB 12"
        assert result.candidates[1].ocr_raw == ""
        assert result.diagnostics["malformed_ocr_text_count"] == 0

    def test_valid_detection_survives_malformed_ocr_as_detection_only(self):
        from core.plate_processing import PlateProcessingOutcome, PlateProcessor

        class Det:
            label = "license_plate"
            confidence = 0.81
            bounding_box = FakeBoundingBox(8, 8, 40, 20)

        class Res:
            detection = Det()
            ocr = type("O", (), {"text": {"bad": True}, "confidence": 0.9})()

        result = PlateProcessor(RecordingBackend([Res()])).process(_sample_image())
        assert result.outcome == PlateProcessingOutcome.CANDIDATE_FOUND
        cand = result.candidates[0]
        assert cand.ocr_raw is None
        assert cand.detection_label == "license_plate"
        assert cand.detection_confidence == pytest.approx(0.81)
        assert cand.bounding_box is not None
        assert result.diagnostics["malformed_ocr_text_count"] == 1
        assert "bad" not in str(result.diagnostics)


class TestConfidencePreservation:
    def test_partial_none_list_unavailable_not_shortened(self):
        from core.plate_processing import PlateProcessor, _preserve_confidence

        assert _preserve_confidence([0.9, None, 0.7]) is None
        result = PlateProcessor(
            RecordingBackend([_candidate(text="X", ocr_conf=[0.9, None, 0.7])])
        ).process(_sample_image())
        assert result.candidates[0].ocr_confidence is None
        assert result.diagnostics["confidence_unnormalizable_count"] == 1
        assert "X" not in str(result.diagnostics)

    def test_valid_confidence_lists_preserve_order_and_length(self):
        from core.plate_processing import PlateProcessor, _preserve_confidence

        values = [0.1, 0.2, 0.3, 0.4]
        assert _preserve_confidence(values) == values
        result = PlateProcessor(
            RecordingBackend([_candidate(text="Y", ocr_conf=list(values))])
        ).process(_sample_image())
        assert result.candidates[0].ocr_confidence == values
        assert len(result.candidates[0].ocr_confidence) == 4

    def test_non_finite_scalar_and_list_confidence_unavailable(self):
        from core.plate_processing import PlateProcessor, _preserve_confidence

        assert _preserve_confidence(float("inf")) is None
        assert _preserve_confidence(float("nan")) is None
        assert _preserve_confidence([0.5, float("inf"), 0.6]) is None

        class DetInf:
            label = "license_plate"
            confidence = float("inf")
            bounding_box = FakeBoundingBox(1, 1, 2, 2)

        class Res:
            detection = DetInf()
            ocr = FakeOcrResult(text="Z", confidence=float("nan"))

        result = PlateProcessor(RecordingBackend([Res()])).process(_sample_image())
        cand = result.candidates[0]
        assert cand.detection_confidence is None
        assert cand.ocr_confidence is None
        assert result.diagnostics["confidence_unnormalizable_count"] >= 1
        assert "Z" not in str(result.diagnostics)
        assert result.grants_human_verified_readable is False
        assert result.grants_recurrence_eligibility is False
