"""Standalone FastALPR plate-processing adapter (Stage C1).

Machine localization/OCR observations only. This module does **not**:
  - import or initialize FastALPR at import time
  - download or load models
  - write to the database or start application workers
  - assign human verification statuses such as ``verified_readable``
  - claim "license plate not visible" from empty detections
  - invent OCR characters, confidence, or offender identity

Upstream interfaces inspected (ankandrew/fast-alpr, master):
  - ``ALPR.predict(frame: np.ndarray | str) -> list[ALPRResult]``
  - ``ALPRResult(detection: DetectionResult, ocr: OcrResult | None)``
  - ``OcrResult(text, confidence: float | list[float], region=..., region_confidence=...)``
  - ``DetectionResult(label, confidence, bounding_box)`` with
    ``BoundingBox(x1, y1, x2, y2)`` from open-image-models

Default ``ALPR(...)`` hub construction can download models. A real-backend
factory is therefore **deferred** until install + local model configuration
are separately approved. Callers inject a compatible backend for tests or a
pre-built ``ALPR`` instance constructed outside this module.
"""

from __future__ import annotations

import enum
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import numpy as np

# ---------------------------------------------------------------------------
# Machine processing outcomes (not human plate_verifications.plate_status)
# ---------------------------------------------------------------------------


class PlateProcessingOutcome(str, enum.Enum):
    """Adapter-level processing outcome.

    Distinct from persistence ``plate_status`` values used after human review.
    """

    CANDIDATE_FOUND = "candidate_found"
    NO_CANDIDATE_DETECTED = "no_candidate_detected"
    PROCESSING_UNAVAILABLE = "processing_unavailable"
    PROCESSING_FAILED = "processing_failed"
    INVALID_INPUT = "invalid_input"


# Human / persistence statuses from plate_verifications CHECK constraint.
# Documented here for integration mapping; the adapter never emits these as
# machine outcomes and never assigns VERIFIED_READABLE.
HUMAN_PLATE_STATUS_NOT_ATTEMPTED = "not_attempted"
HUMAN_PLATE_STATUS_PROCESSING_FAILED = "processing_failed"
HUMAN_PLATE_STATUS_UNCLEAR = "unclear"
HUMAN_PLATE_STATUS_NOT_VISIBLE = "not_visible"
HUMAN_PLATE_STATUS_CANDIDATE_AWAITING_VERIFICATION = (
    "candidate_awaiting_verification"
)
HUMAN_PLATE_STATUS_VERIFIED_READABLE = "verified_readable"
HUMAN_PLATE_STATUS_MIGRATED_UNVERIFIED = "migrated_unverified"

PROVIDER_FAST_ALPR = "fast_alpr"


# ---------------------------------------------------------------------------
# Result contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlateBoundingBox:
    """Axis-aligned plate box in pixel coordinates (x1, y1, x2, y2)."""

    x1: int
    y1: int
    x2: int
    y2: int


@dataclass(frozen=True)
class PlateCandidateObservation:
    """One machine plate candidate. Not an accepted offender identity.

    ``ocr_raw`` is raw OCR text only. There is no accepted-plate field.
    Missing confidence stays ``None`` (never coerced to 0.0).
    """

    ocr_raw: str | None
    detection_label: str | None = None
    detection_confidence: float | None = None
    ocr_confidence: float | list[float] | None = None
    bounding_box: PlateBoundingBox | None = None
    region: str | None = None
    region_confidence: float | None = None


@dataclass(frozen=True)
class PlateProcessingResult:
    """Structured machine-processing result for a single supplied image/crop.

    Ownership: the caller retains the original image. This result may include
    an optional ``evidence_ref`` string for later integration; no files are
    written by the adapter.
    """

    outcome: PlateProcessingOutcome
    candidates: tuple[PlateCandidateObservation, ...] = ()
    provider: str | None = None
    model_name: str | None = None
    model_version: str | None = None
    evidence_ref: str | None = None
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    @property
    def grants_human_verified_readable(self) -> bool:
        """Machine results never establish human-verified plate status."""
        return False

    @property
    def grants_recurrence_eligibility(self) -> bool:
        """Recurrence requires later human verification; adapter never grants it."""
        return False


# ---------------------------------------------------------------------------
# Backend protocol (injected; reusable across calls)
# ---------------------------------------------------------------------------


@runtime_checkable
class PlateAlprBackend(Protocol):
    """Minimal compatible surface for FastALPR ``ALPR.predict``."""

    def predict(self, frame: np.ndarray) -> Sequence[Any]:
        """Return a sequence of ALPRResult-like objects for a BGR ndarray."""


@dataclass(frozen=True)
class BackendMetadata:
    """Optional caller-supplied backend identity (never invented)."""

    provider: str | None = PROVIDER_FAST_ALPR
    model_name: str | None = None
    model_version: str | None = None


# ---------------------------------------------------------------------------
# Import probe (no model init)
# ---------------------------------------------------------------------------


def is_fast_alpr_available() -> bool:
    """Return True if the ``fast_alpr`` package can be imported.

    Does not construct ``ALPR``, load models, or download weights.
    """
    try:
        import fast_alpr  # noqa: F401
    except ImportError:
        return False
    return True


def real_fast_alpr_backend_factory_status() -> dict[str, Any]:
    """Document why an automatic real-backend factory is deferred.

    Upstream ``ALPR(...)`` defaults resolve hub model names and may download
    weights when local paths are omitted (``ocr_model_path`` / detector hub).
    This Stage C1 module therefore does not construct a live backend.
    """
    return {
        "factory_available": False,
        "reason": (
            "Deferred: default FastALPR construction can download hub models. "
            "Inject a preconfigured backend after separate install and local "
            "model-configuration approval."
        ),
        "required_dependency": "fast-alpr with an ONNX Runtime extra "
        "(e.g. fast-alpr[onnx]) — not installed by this task",
        "required_configuration": (
            "Caller-supplied detector/OCR models or local model_path/"
            "config_path; no silent hub download"
        ),
        "upstream_predict": "ALPR.predict(np.ndarray | str) -> list[ALPRResult]",
        "package_importable": is_fast_alpr_available(),
    }


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class PlateProcessor:
    """Isolated plate processor. Construction is separate from processing.

    Pass an injected ``backend`` once and reuse it across ``process`` /
    ``read_plate`` calls. ``backend=None`` yields ``PROCESSING_UNAVAILABLE``.
    """

    def __init__(
        self,
        backend: PlateAlprBackend | None = None,
        *,
        metadata: BackendMetadata | None = None,
    ) -> None:
        self._backend = backend
        self._metadata = metadata or BackendMetadata(
            provider=PROVIDER_FAST_ALPR if backend is not None else None
        )

    @property
    def backend(self) -> PlateAlprBackend | None:
        return self._backend

    def read_plate(
        self,
        crop_image: Any,
        *,
        evidence_ref: str | None = None,
    ) -> PlateProcessingResult:
        """Process an explicitly supplied crop/image associated with a case."""
        return self.process(crop_image, evidence_ref=evidence_ref)

    def process(
        self,
        image: Any,
        *,
        evidence_ref: str | None = None,
    ) -> PlateProcessingResult:
        """Run machine plate localization/OCR on a caller-owned image.

        The original ``image`` array is not mutated. A defensive copy is passed
        to the backend. No database or filesystem writes occur.
        """
        meta = self._metadata
        base_diag: dict[str, Any] = {
            "evidence_ref": evidence_ref,
            "input_shape": _safe_shape(image),
        }

        validated = _validate_image(image)
        if validated is None:
            return PlateProcessingResult(
                outcome=PlateProcessingOutcome.INVALID_INPUT,
                provider=meta.provider,
                model_name=meta.model_name,
                model_version=meta.model_version,
                evidence_ref=evidence_ref,
                diagnostics={**base_diag, "reason": "unsupported_or_empty_image"},
            )

        if self._backend is None:
            return PlateProcessingResult(
                outcome=PlateProcessingOutcome.PROCESSING_UNAVAILABLE,
                provider=meta.provider,
                model_name=meta.model_name,
                model_version=meta.model_version,
                evidence_ref=evidence_ref,
                diagnostics={
                    **base_diag,
                    "reason": "no_backend_configured",
                    "fast_alpr_importable": is_fast_alpr_available(),
                    "factory": real_fast_alpr_backend_factory_status(),
                },
            )

        # Defensive copy: preserve caller ownership even if a backend mutates.
        frame = np.array(validated, copy=True)
        try:
            raw_results = self._backend.predict(frame)
        except Exception as exc:  # noqa: BLE001 — bounded failure contract
            return PlateProcessingResult(
                outcome=PlateProcessingOutcome.PROCESSING_FAILED,
                provider=meta.provider,
                model_name=meta.model_name,
                model_version=meta.model_version,
                evidence_ref=evidence_ref,
                diagnostics={
                    **base_diag,
                    "reason": "backend_exception",
                    "exception_type": type(exc).__name__,
                    # Intentionally omit exception args/message: may contain paths
                    # or plate-like strings from caller context.
                },
            )

        return _normalize_backend_results(
            raw_results,
            metadata=meta,
            evidence_ref=evidence_ref,
            diagnostics=base_diag,
        )


def _safe_shape(image: Any) -> tuple[int, ...] | None:
    shape = getattr(image, "shape", None)
    if shape is None:
        return None
    try:
        return tuple(int(x) for x in shape)
    except (TypeError, ValueError):
        return None


def _validate_image(image: Any) -> np.ndarray | None:
    if image is None:
        return None
    if not isinstance(image, np.ndarray):
        return None
    if image.size == 0:
        return None
    if image.ndim not in (2, 3):
        return None
    if image.ndim == 3 and image.shape[2] not in (1, 3, 4):
        return None
    return image


def _normalize_backend_results(
    raw_results: Any,
    *,
    metadata: BackendMetadata,
    evidence_ref: str | None,
    diagnostics: dict[str, Any],
) -> PlateProcessingResult:
    if raw_results is None:
        return PlateProcessingResult(
            outcome=PlateProcessingOutcome.PROCESSING_FAILED,
            provider=metadata.provider,
            model_name=metadata.model_name,
            model_version=metadata.model_version,
            evidence_ref=evidence_ref,
            diagnostics={**diagnostics, "reason": "malformed_null_result"},
        )

    if not isinstance(raw_results, Sequence) or isinstance(
        raw_results, (str, bytes, bytearray)
    ):
        return PlateProcessingResult(
            outcome=PlateProcessingOutcome.PROCESSING_FAILED,
            provider=metadata.provider,
            model_name=metadata.model_name,
            model_version=metadata.model_version,
            evidence_ref=evidence_ref,
            diagnostics={
                **diagnostics,
                "reason": "malformed_non_sequence_result",
                "result_type": type(raw_results).__name__,
            },
        )

    if len(raw_results) == 0:
        return PlateProcessingResult(
            outcome=PlateProcessingOutcome.NO_CANDIDATE_DETECTED,
            candidates=(),
            provider=metadata.provider,
            model_name=metadata.model_name,
            model_version=metadata.model_version,
            evidence_ref=evidence_ref,
            diagnostics={
                **diagnostics,
                "reason": "empty_detection_list",
                "note": (
                    "no_candidate_detected_does_not_mean_plate_not_visible"
                ),
            },
        )

    candidates: list[PlateCandidateObservation] = []
    skipped_malformed = 0
    malformed_bounding_box_count = 0
    retained_without_bounding_box_count = 0
    malformed_ocr_text_count = 0
    confidence_unnormalizable_count = 0
    for item in raw_results:
        parsed, notes = _parse_alpr_result_item(item)
        if notes.malformed_bounding_box:
            malformed_bounding_box_count += 1
        if notes.retained_without_bounding_box:
            retained_without_bounding_box_count += 1
        if notes.malformed_ocr_text:
            malformed_ocr_text_count += 1
        if notes.confidence_unnormalizable:
            confidence_unnormalizable_count += 1
        if parsed is None:
            skipped_malformed += 1
            continue
        candidates.append(parsed)

    diag = {
        **diagnostics,
        "raw_result_count": len(raw_results),
        "parsed_candidate_count": len(candidates),
        "skipped_malformed_count": skipped_malformed,
        "malformed_bounding_box_count": malformed_bounding_box_count,
        "retained_without_bounding_box_count": retained_without_bounding_box_count,
        "malformed_ocr_text_count": malformed_ocr_text_count,
        "confidence_unnormalizable_count": confidence_unnormalizable_count,
        # Explicit contract: invalid box → drop box only; keep candidate when
        # other usable fields remain. Never invent replacement coordinates.
        "malformed_bounding_box_policy": "retain_candidate_without_box",
    }

    if not candidates:
        return PlateProcessingResult(
            outcome=PlateProcessingOutcome.PROCESSING_FAILED,
            candidates=(),
            provider=metadata.provider,
            model_name=metadata.model_name,
            model_version=metadata.model_version,
            evidence_ref=evidence_ref,
            diagnostics={**diag, "reason": "all_candidates_malformed"},
        )

    return PlateProcessingResult(
        outcome=PlateProcessingOutcome.CANDIDATE_FOUND,
        candidates=tuple(candidates),
        provider=metadata.provider,
        model_name=metadata.model_name,
        model_version=metadata.model_version,
        evidence_ref=evidence_ref,
        diagnostics=diag,
    )


@dataclass(frozen=True)
class _ItemParseNotes:
    """Bounded parse flags — no OCR text or exception messages."""

    malformed_bounding_box: bool = False
    retained_without_bounding_box: bool = False
    malformed_ocr_text: bool = False
    confidence_unnormalizable: bool = False


def _parse_alpr_result_item(
    item: Any,
) -> tuple[PlateCandidateObservation | None, _ItemParseNotes]:
    """Map one upstream ``ALPRResult``-like object without inventing fields.

    Malformed bounding-box policy
    -----------------------------
    Invalid/non-finite coordinates yield ``bounding_box=None``. The candidate
    is **retained** when other usable information exists (label, confidence,
    OCR). Replacement coordinates are never invented. The item is skipped only
    when no usable surface remains after discarding the invalid box.
    """
    notes = _ItemParseNotes()
    if item is None:
        return None, notes

    try:
        detection = getattr(item, "detection", _MISSING)
        if detection is _MISSING:
            # Dict-shaped fixtures / alternate serializers
            if isinstance(item, Mapping):
                detection = item.get("detection")
                ocr = item.get("ocr")
            else:
                return None, notes
        else:
            ocr = getattr(item, "ocr", None)

        if detection is None:
            return None, notes

        label = getattr(detection, "label", None)
        det_conf = getattr(detection, "confidence", _MISSING)
        if det_conf is _MISSING and isinstance(detection, Mapping):
            label = detection.get("label", label)
            det_conf = detection.get("confidence", _MISSING)
            bbox_obj = detection.get("bounding_box")
        else:
            bbox_obj = getattr(detection, "bounding_box", None)

        detection_label = label if isinstance(label, str) else None

        detection_confidence, det_conf_bad = _normalize_scalar_confidence(det_conf)
        confidence_bad = det_conf_bad

        bounding_box, box_malformed = _parse_bounding_box(bbox_obj)

        ocr_raw: str | None = None
        ocr_confidence: float | list[float] | None = None
        region: str | None = None
        region_confidence: float | None = None
        ocr_text_malformed = False

        if ocr is not None:
            text_val = getattr(ocr, "text", _MISSING)
            if text_val is _MISSING and isinstance(ocr, Mapping):
                text_val = ocr.get("text", _MISSING)
                conf_val = ocr.get("confidence", _MISSING)
                region_val = ocr.get("region", _MISSING)
                region_conf = ocr.get("region_confidence", _MISSING)
            else:
                conf_val = getattr(ocr, "confidence", _MISSING)
                region_val = getattr(ocr, "region", _MISSING)
                region_conf = getattr(ocr, "region_confidence", _MISSING)

            ocr_raw, ocr_text_malformed = _preserve_ocr_text(text_val)
            ocr_confidence, ocr_conf_bad = _normalize_ocr_confidence(conf_val)
            confidence_bad = confidence_bad or ocr_conf_bad

            if region_val is not _MISSING and region_val is not None:
                region = region_val if isinstance(region_val, str) else None

            region_confidence, region_conf_bad = _normalize_scalar_confidence(
                region_conf
            )
            confidence_bad = confidence_bad or region_conf_bad

        notes = _ItemParseNotes(
            malformed_bounding_box=box_malformed,
            retained_without_bounding_box=False,
            malformed_ocr_text=ocr_text_malformed,
            confidence_unnormalizable=confidence_bad,
        )

        # A detection without OCR is still a candidate observation.
        usable = (
            bounding_box is not None
            or detection_label is not None
            or detection_confidence is not None
            or ocr_raw is not None
            or ocr_confidence is not None
            or region is not None
            or region_confidence is not None
        )
        if not usable:
            return None, notes

        if box_malformed and bounding_box is None:
            notes = _ItemParseNotes(
                malformed_bounding_box=True,
                retained_without_bounding_box=True,
                malformed_ocr_text=ocr_text_malformed,
                confidence_unnormalizable=confidence_bad,
            )

        return (
            PlateCandidateObservation(
                ocr_raw=ocr_raw,
                detection_label=detection_label,
                detection_confidence=detection_confidence,
                ocr_confidence=ocr_confidence,
                bounding_box=bounding_box,
                region=region,
                region_confidence=region_confidence,
            ),
            notes,
        )
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        # Contained parse failure — never escape as an unhandled processing error.
        return None, _ItemParseNotes(malformed_bounding_box=False)


def _preserve_ocr_text(text_val: Any) -> tuple[str | None, bool]:
    """Return ``(ocr_raw, malformed)``.

    Actual strings (including empty) are preserved exactly. Missing/None stay
    unavailable. Dicts, lists, numbers, booleans, bytes, and other objects are
    rejected without stringification or character guessing.
    """
    if text_val is _MISSING or text_val is None:
        return None, False
    if isinstance(text_val, str):
        return text_val, False
    return None, True


def _normalize_scalar_confidence(conf_val: Any) -> tuple[float | None, bool]:
    """Normalize a scalar confidence field.

    Returns ``(value, unnormalizable)``. Missing/None → ``(None, False)``.
    Present but malformed/non-finite/list-shaped → ``(None, True)``.
    """
    if conf_val is _MISSING or conf_val is None:
        return None, False
    preserved = _preserve_confidence(conf_val)
    if isinstance(preserved, list):
        return None, True
    if preserved is None:
        return None, True
    return preserved, False


def _normalize_ocr_confidence(
    conf_val: Any,
) -> tuple[float | list[float] | None, bool]:
    """Normalize OCR confidence (scalar or per-character list)."""
    if conf_val is _MISSING or conf_val is None:
        return None, False
    preserved = _preserve_confidence(conf_val)
    if preserved is None:
        return None, True
    return preserved, False


def _preserve_confidence(
    conf_val: Any,
) -> float | list[float] | None:
    """Preserve confidence meaning without inventing or shifting values.

    - Valid finite scalars are returned as ``float``.
    - Valid numeric lists keep original order and length.
    - If any list entry is missing, malformed, or non-finite, the entire
      confidence field is unavailable (``None``) — entries are not deleted,
      zero-filled, averaged, or reordered.
    - Non-finite scalars are unavailable.
    """
    if conf_val is _MISSING or conf_val is None:
        return None
    if isinstance(conf_val, bool):
        return None
    if isinstance(conf_val, (int, float)):
        value = float(conf_val)
        return value if math.isfinite(value) else None
    if isinstance(conf_val, list):
        out: list[float] = []
        for item in conf_val:
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                return None
            value = float(item)
            if not math.isfinite(value):
                return None
            out.append(value)
        return out
    return None


def _parse_bounding_box(bbox_obj: Any) -> tuple[PlateBoundingBox | None, bool]:
    """Parse a box or report malformation.

    Returns ``(box, malformed)``. ``malformed=True`` means coordinates were
    present but invalid (non-finite, non-numeric, conversion failure). Missing
    ``None`` input is ``(None, False)``. Never invents replacement coordinates.
    """
    if bbox_obj is None:
        return None, False
    try:
        if isinstance(bbox_obj, Mapping):
            raw = (
                bbox_obj["x1"],
                bbox_obj["y1"],
                bbox_obj["x2"],
                bbox_obj["y2"],
            )
        else:
            raw = (
                getattr(bbox_obj, "x1"),
                getattr(bbox_obj, "y1"),
                getattr(bbox_obj, "x2"),
                getattr(bbox_obj, "y2"),
            )
    except (AttributeError, KeyError, TypeError):
        return None, True

    coords: list[int] = []
    for value in raw:
        parsed = _coordinate_to_int(value)
        if parsed is None:
            return None, True
        coords.append(parsed)
    return PlateBoundingBox(*coords), False


def _coordinate_to_int(value: Any) -> int | None:
    """Convert one box coordinate; reject non-finite / non-numeric values."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    try:
        return int(number)
    except (OverflowError, ValueError):
        return None


class _MissingType:
    pass


_MISSING = _MissingType()
