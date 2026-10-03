"""Local ONNX Runtime backend for experimental plate detection + OCR.

Design constraints (all deliberate):

* **No import-time model work.** ``onnxruntime`` is imported inside
  :meth:`PlateOnnxBackend._ensure_sessions`, sessions are created on first
  use, and reused afterwards. Importing this module, building a
  :class:`PlateProcessor`, or serializing a response never touches a model.
* **No hub, no download, no fallback.** Every artifact path and SHA-256 comes
  from :mod:`core.plate_settings`. A missing, unreadable, hash-mismatched, or
  provider-unsupported artifact raises :class:`PlateBackendUnavailable` /
  :class:`PlateBackendError`, which the orchestration layer converts into a
  controlled outcome.
* **Explicit provider only.** The caller names one execution provider. There is
  no automatic selection and no undocumented CPU fallback.
* **Two separate stages.** :meth:`detect_plates` and :meth:`recognize_plate`
  are distinct calls with distinct budgets, so plate quality checks and OCR
  limits can be enforced between them.

Artifacts
---------
Detector ``yolo-v9-t-384-license-plate-end2end``
    Publisher: ankandrew (open-image-models). Asset
    ``yolo-v9-t-384-license-plates-end2end.onnx`` published in the
    ``assets`` release of https://github.com/ankandrew/open-image-models.
    Registry key is singular ("plate"), published filename is plural
    ("plates"); the registry maps one to the other.

    ONNX signature (verified locally with onnxruntime 1.30.0):
      input  ``images``  float32 [1, 3, 384, 384]
      output ``output0`` float32 [batch, 7]

    Rows are ``[batch_index, x1, y1, x2, y2, class_id, score]``. Non-maximum
    suppression is embedded in the exported graph ("end2end"); this backend
    never runs a second NMS and never reorders or rescales scores.
    Preprocessing is the upstream letterbox: pad value 114, aspect-preserving
    resize, BGR->RGB, /255, float32, NCHW, batch 1.

OCR ``cct-s-v2-global-model``
    Publisher: ankandrew (fast-plate-ocr). Asset ``cct_s_v2_global.onnx`` plus
    ``cct_s_v2_global_plate_config.yaml`` published in the ``arg-plates``
    release of https://github.com/ankandrew/fast-plate-ocr.

    ONNX signature (verified locally with onnxruntime 1.30.0):
      input   ``input``   uint8 [batch, 64, 128, 3]
      output  ``plate``   float32 [batch, 10, 37]
      output  ``region``  float32 [batch, 66]

    The ``plate`` head is **raw logits**, not a softmax: upstream's own
    decoder takes ``argmax`` over the last axis and reports ``np.max`` as
    "char prob". Those numbers are therefore reported as uncalibrated model
    scores and never as probabilities.

    Known upstream discrepancy: the published ``plate_config.yaml`` omits
    ``image_color_mode``, whose library default is ``"grayscale"`` (1 input
    channel), but this ONNX export requires 3 channels. The colour handling
    is therefore an explicit, declared setting
    (``PlateOcrSettings.ocr_color_mode``) rather than an inferred default.
"""

from __future__ import annotations

import hashlib
import math
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from core.plate_processing import PlateBoundingBox, PlateCandidateObservation
from core.plate_settings import PlateOcrSettings, verify_artifact_hashes

#: Detection letterbox geometry is fixed by the exported graph ([1,3,384,384]).
DETECTOR_IMG_SIZE = 384
DETECTOR_PAD_VALUE = (114, 114, 114)


class PlateBackendError(RuntimeError):
    """The backend could not produce a result (model or runtime failure)."""


class PlateBackendUnavailable(PlateBackendError):
    """Artifacts or the requested provider are not usable. Fail closed."""


# ----------------------------------------------------------------------
# Result types
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class PlateBox:
    """One detector plate box in original-frame pixel coordinates."""

    x1: int
    y1: int
    x2: int
    y2: int
    score: float
    label: str = "License Plate"

    @property
    def width(self) -> int:
        return self.x2 - self.x1

    @property
    def height(self) -> int:
        return self.y2 - self.y1

    def as_plate_bounding_box(self) -> PlateBoundingBox:
        return PlateBoundingBox(x1=self.x1, y1=self.y1, x2=self.x2, y2=self.y2)


@dataclass(frozen=True)
class OcrRead:
    """One OCR read.

    ``char_scores`` are raw per-slot maximum logits (uncalibrated). ``text`` is
    the raw decoded string with only the declared padding character removed -
    no character repair, no plate-format completion.
    """

    text: str
    char_scores: list[float] | None = None
    plate_shape: tuple[int, int] | None = None

    @property
    def mean_score(self) -> float | None:
        if not self.char_scores:
            return None
        return float(sum(self.char_scores) / len(self.char_scores))

    @property
    def min_score(self) -> float | None:
        if not self.char_scores:
            return None
        return float(min(self.char_scores))

    @property
    def is_empty(self) -> bool:
        return not self.text


@dataclass(frozen=True)
class PlateQualityReport:
    """Measured crop/plate quality. ``accepted`` never implies readability."""

    accepted: bool
    reasons: tuple[str, ...] = ()
    metrics: dict[str, float | int | None] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.metrics is None:
            object.__setattr__(self, "metrics", {})


@dataclass(frozen=True)
class AlprDetectionShim:
    """Adapter-contract shim (``detection.label/confidence/bounding_box``)."""

    label: str
    confidence: float
    bounding_box: PlateBoundingBox


@dataclass(frozen=True)
class AlprOcrShim:
    """Adapter-contract shim (``ocr.text/confidence``)."""

    text: str
    confidence: list[float] | None = None


@dataclass(frozen=True)
class AlprResultShim:
    detection: AlprDetectionShim
    ocr: AlprOcrShim | None = None


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def letterbox_detector_input(
    image_bgr: np.ndarray,
    *,
    new_shape: int = DETECTOR_IMG_SIZE,
    pad_value: tuple[int, int, int] = DETECTOR_PAD_VALUE,
) -> tuple[np.ndarray, tuple[float, float], tuple[float, float]]:
    """Reproduce the upstream YOLOv9 letterbox exactly.

    Returns ``(tensor_NCHW_float32, ratio, (dw, dh))`` where ``dw``/``dh`` are
    the *pre-round* padding offsets used by the upstream un-letterbox step.
    """
    shape = image_bgr.shape[:2]
    r = min(new_shape / shape[0], new_shape / shape[1])
    new_unpad = int(round(shape[1] * r)), int(round(shape[0] * r))
    dw = (new_shape - new_unpad[0]) / 2
    dh = (new_shape - new_unpad[1]) / 2
    resized = image_bgr
    if shape[::-1] != new_unpad:
        resized = cv2.resize(image_bgr, new_unpad, interpolation=cv2.INTER_LINEAR)
    top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
    left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
    padded = cv2.copyMakeBorder(
        resized, top, bottom, left, right, cv2.BORDER_CONSTANT, value=pad_value
    )
    tensor = padded.transpose((2, 0, 1))[::-1]
    tensor = (tensor / 255.0).astype(np.float32)
    return np.expand_dims(tensor, 0), (r, r), (dw, dh)


def unletterbox_box(
    box: Sequence[float], ratio: tuple[float, float], padding: tuple[float, float]
) -> tuple[float, float, float, float]:
    x1, y1, x2, y2 = (float(v) for v in box)
    return (
        (x1 - padding[0]) / ratio[0],
        (y1 - padding[1]) / ratio[1],
        (x2 - padding[0]) / ratio[0],
        (y2 - padding[1]) / ratio[1],
    )


def preprocess_ocr_input(crop_bgr: np.ndarray, *, height: int, width: int, color_mode: str) -> np.ndarray:
    """Resize a plate crop to the exported OCR input contract.

    ``color_mode='gray'`` converts to single-channel grayscale and replicates it
    across three channels; ``color_mode='rgb'`` converts BGR->RGB. The result is
    a ``(1, height, width, 3)`` uint8 array; the graph performs its own /255.
    """
    if crop_bgr.ndim == 3 and crop_bgr.shape[2] == 3:
        source = crop_bgr
    elif crop_bgr.ndim == 2:
        source = cv2.cvtColor(crop_bgr, cv2.COLOR_GRAY2BGR)
    else:
        raise PlateBackendError(f"unsupported crop shape for OCR: {crop_bgr.shape}")

    if color_mode == "rgb":
        channels = cv2.cvtColor(source, cv2.COLOR_BGR2RGB)
    elif color_mode == "gray":
        gray = cv2.cvtColor(source, cv2.COLOR_BGR2GRAY)
        channels = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    else:
        raise PlateBackendError(f"unsupported ocr_color_mode: {color_mode!r}")

    resized = cv2.resize(channels, (width, height), interpolation=cv2.INTER_LINEAR)
    return np.expand_dims(resized.astype(np.uint8), 0)


# ----------------------------------------------------------------------
# Quality gate
# ----------------------------------------------------------------------


def crop_from_box(
    frame: np.ndarray, box: Sequence[float]
) -> tuple[np.ndarray | None, tuple[int, int, int, int]]:
    """Clamp ``box`` to ``frame`` and return ``(crop, (x1, y1, x2, y2))``.

    The returned rectangle is the *effective* one actually sliced, so callers
    can record crop origin and clamp amounts instead of assuming the requested
    box was fully inside the frame.
    """
    h_img, w_img = frame.shape[:2]
    x1 = max(0, int(round(float(box[0]))))
    y1 = max(0, int(round(float(box[1]))))
    x2 = min(w_img, int(round(float(box[2]))))
    y2 = min(h_img, int(round(float(box[3]))))
    if x2 <= x1 or y2 <= y1:
        return None, (x1, y1, x2, y2)
    return frame[y1:y2, x1:x2], (x1, y1, x2, y2)


def check_plate_quality(
    plate_crop: np.ndarray | None,
    *,
    settings: PlateOcrSettings,
    requested_box: Sequence[float] | None = None,
    effective_rect: tuple[int, int, int, int] | None = None,
) -> PlateQualityReport:
    """Reject unusable plate geometry before any OCR call.

    Rejections cover: missing crop, invalid/clipped geometry, unusable native
    size, implausible aspect ratio, severe blur, and specular blow-out.

    Occlusion is **not** detected here. A partially occluded plate can pass
    this gate and produce a wrong string; that uncertainty is carried by the
    orchestration outcome, not by a claim of occlusion handling.
    """
    q = settings.quality
    metrics: dict[str, float | int | None] = {
        "plate_width_px": None,
        "plate_height_px": None,
        "laplacian_variance": None,
        "blown_highlight_ratio": None,
        "aspect_ratio": None,
        "area_retention": None,
        "clipped": None,
    }
    reasons: list[str] = []

    if plate_crop is None or plate_crop.size == 0:
        return PlateQualityReport(False, ("plate_crop_unavailable",), metrics)

    h, w = plate_crop.shape[:2]
    metrics["plate_width_px"] = int(w)
    metrics["plate_height_px"] = int(h)

    if requested_box is not None and effective_rect is not None:
        req_area = max(1.0, (float(requested_box[2]) - float(requested_box[0])) * (float(requested_box[3]) - float(requested_box[1])))
        got_area = float(max(0, w * h))
        retention = got_area / req_area
        metrics["area_retention"] = round(retention, 4)
        clipped = (
            int(requested_box[0]) != effective_rect[0]
            or int(requested_box[1]) != effective_rect[1]
            or int(requested_box[2]) != effective_rect[2]
            or int(requested_box[3]) != effective_rect[3]
        )
        metrics["clipped"] = 1 if clipped else 0
        if retention < float(q["min_plate_area_retention"]):
            reasons.append("plate_box_clipped")
        if w <= 1 or h <= 1:
            reasons.append("plate_box_degenerate")

    if w < int(q["min_plate_width_px"]) or h < int(q["min_plate_height_px"]):
        reasons.append("plate_too_small")
        metrics["aspect_ratio"] = round(float(w) / float(max(1, h)), 4)

    gray = cv2.cvtColor(plate_crop, cv2.COLOR_BGR2GRAY) if plate_crop.ndim == 3 else plate_crop
    aspect = float(w) / float(max(1, h))
    metrics["aspect_ratio"] = round(aspect, 4)
    if aspect < float(q["min_aspect_ratio"]) or aspect > float(q["max_aspect_ratio"]):
        reasons.append("plate_aspect_ratio_implausible")

    variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    metrics["laplacian_variance"] = round(variance, 3)
    if variance < float(q["min_laplacian_variance"]):
        reasons.append("plate_blur_severe")

    blown = float(np.count_nonzero(gray >= 250)) / float(max(1, gray.size))
    metrics["blown_highlight_ratio"] = round(blown, 5)
    if blown > float(q["max_blown_highlight_ratio"]):
        reasons.append("plate_glare_severe")

    return PlateQualityReport(not reasons, tuple(reasons), metrics)


def iou(a: Sequence[float], b: Sequence[float]) -> float:
    ax1, ay1, ax2, ay2 = (float(v) for v in a)
    bx1, by1, bx2, by2 = (float(v) for v in b)
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return float(inter / union) if union > 0 else 0.0


# ----------------------------------------------------------------------
# Backend
# ----------------------------------------------------------------------


class PlateOnnxBackend:
    """Reusable, lazily-created ONNX Runtime backend for plates.

    One instance is shared for the whole process. Construction performs **no**
    runtime work beyond reading the settings object; the first
    :meth:`detect_plates` / :meth:`recognize_plate` call validates artifacts and
    builds sessions, and every later call reuses them.
    """

    def __init__(self, settings: PlateOcrSettings) -> None:
        self.settings = settings
        self._lock = threading.Lock()
        self._detector = None
        self._ocr = None
        self._alphabet = ""
        self._pad_char = ""
        self._max_slots = 0
        self._ocr_size: tuple[int, int] = (0, 0)
        self._runtime_error: str | None = None
        self._artifact_report: list[dict[str, Any]] = []
        self.detector_calls = 0
        self.ocr_calls = 0

    # -- session lifecycle ------------------------------------------
    def _ensure_sessions(self) -> None:
        with self._lock:
            if self._runtime_error is not None:
                raise PlateBackendUnavailable(self._runtime_error)
            if self._detector is not None and self._ocr is not None:
                return
            try:
                self._build_sessions()
            except PlateBackendError:
                raise
            except Exception as exc:  # noqa: BLE001 - bounded failure contract
                self._runtime_error = f"backend_initialization_failed:{type(exc).__name__}"
                raise PlateBackendUnavailable(self._runtime_error) from exc

    def _build_sessions(self) -> None:
        settings = self.settings
        if not settings.is_enabled:
            raise PlateBackendUnavailable(settings.disabled_reason or "plate_ocr_disabled")
        if not settings.provider:
            raise PlateBackendUnavailable("plate_ocr_provider_not_configured")

        report = verify_artifact_hashes(settings)
        self._artifact_report = report
        failed = [row for row in report if not row["ok"]]
        if failed:
            reasons = ",".join(sorted({str(row.get("reason")) for row in failed}))
            self._runtime_error = f"plate_artifact_rejected:{reasons}"
            raise PlateBackendUnavailable(self._runtime_error)

        try:
            import onnxruntime as ort
        except ImportError as exc:
            self._runtime_error = "onnxruntime_not_installed"
            raise PlateBackendUnavailable(self._runtime_error) from exc

        available = list(ort.get_available_providers())
        if settings.provider not in available:
            # No automatic substitution, not even to CPU.
            self._runtime_error = f"provider_unavailable:{settings.provider}"
            raise PlateBackendUnavailable(self._runtime_error)

        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        opts.intra_op_num_threads = max(1, int(settings.bounds.get("worker_count", 1)))
        opts.inter_op_num_threads = 1

        try:
            detector = ort.InferenceSession(
                str(settings.detector_path), providers=[settings.provider], sess_options=opts
            )
            ocr = ort.InferenceSession(
                str(settings.ocr_path), providers=[settings.provider], sess_options=opts
            )
        except Exception as exc:  # noqa: BLE001 - corrupt/unsupported graph
            self._runtime_error = f"session_creation_failed:{type(exc).__name__}"
            raise PlateBackendError(self._runtime_error) from exc

        det_in = detector.get_inputs()
        det_out = detector.get_outputs()
        if len(det_in) != 1 or len(det_out) != 1:
            self._runtime_error = "detector_signature_unsupported"
            raise PlateBackendUnavailable(self._runtime_error)
        shape = list(det_in[0].shape)
        if len(shape) == 4 and shape[1:] == [3, DETECTOR_IMG_SIZE, DETECTOR_IMG_SIZE]:
            pass
        elif len(shape) == 4 and shape[0] in (1, "batch", None) and shape[1] == 3:
            # Non-square export is still usable; use the model's own size.
            if shape[2] != shape[3]:
                self._runtime_error = "detector_input_not_square"
                raise PlateBackendUnavailable(self._runtime_error)
        else:
            self._runtime_error = f"detector_input_unsupported:{shape}"
            raise PlateBackendUnavailable(self._runtime_error)

        ocr_in = ocr.get_inputs()
        if len(ocr_in) != 1:
            self._runtime_error = "ocr_signature_unsupported"
            raise PlateBackendUnavailable(self._runtime_error)
        ocr_shape = list(ocr_in[0].shape)
        if len(ocr_shape) != 4 or ocr_in[0].type != "tensor(uint8)":
            self._runtime_error = f"ocr_input_unsupported:{ocr_shape}"
            raise PlateBackendUnavailable(self._runtime_error)
        self._ocr_size = (int(ocr_shape[1]), int(ocr_shape[2]))

        alphabet, pad_char, max_slots = _read_plate_config(settings.ocr_config_path)
        if not alphabet or not pad_char or pad_char not in alphabet:
            self._runtime_error = "ocr_config_invalid"
            raise PlateBackendUnavailable(self._runtime_error)

        self._detector = detector
        self._ocr = ocr
        self._alphabet = alphabet
        self._pad_char = pad_char
        self._max_slots = int(max_slots)
        self._detector_input_name = det_in[0].name
        self._detector_output_name = det_out[0].name
        self._ocr_input_name = ocr_in[0].name
        self._ocr_output_names = [o.name for o in ocr.get_outputs()]

    # -- introspection ----------------------------------------------
    @property
    def runtime_ready(self) -> bool:
        return self._detector is not None and self._ocr is not None

    def metadata(self) -> dict[str, Any]:
        """Artifact/runtime identity recorded in every manifest."""
        ort_version = None
        if self.runtime_ready:
            try:
                import onnxruntime as ort

                ort_version = getattr(ort, "__version__", None)
            except ImportError:  # pragma: no cover - defensive
                ort_version = None
        return {
            "backend": "plate_onnx_backend",
            "requested_provider": self.settings.provider,
            "active_providers": list(self._detector.get_providers()) if self._detector else [],
            "detector_artifact": {
                "path": self.settings.detector_path,
                "sha256": self.settings.detector_sha256,
                "input_name": getattr(self, "_detector_input_name", None),
                "output_name": getattr(self, "_detector_output_name", None),
                "input_shape": list(self._detector.get_inputs()[0].shape) if self._detector else None,
                "output_shape": list(self._detector.get_outputs()[0].shape) if self._detector else None,
            },
            "ocr_artifact": {
                "path": self.settings.ocr_path,
                "sha256": self.settings.ocr_sha256,
                "input_name": getattr(self, "_ocr_input_name", None),
                "output_names": list(getattr(self, "_ocr_output_names", []) or []),
                "input_shape": list(self._ocr.get_inputs()[0].shape) if self._ocr else None,
                "alphabet": self._alphabet,
                "pad_char": self._pad_char,
                "max_plate_slots": self._max_slots,
                "img_hw": list(self._ocr_size),
                "ocr_config_path": self.settings.ocr_config_path,
                "ocr_config_sha256": self.settings.ocr_config_sha256,
                "color_mode": self.settings.ocr_color_mode,
                "confidence_semantics": (
                    "raw per-slot maximum logit from the plate head; uncalibrated, "
                    "not a probability and not an accuracy estimate"
                ),
            },
            "runtime": {
                "onnxruntime_version": ort_version,
                "numpy_version": np.__version__,
                "opencv_version": cv2.__version__,
                "python_cv2_unicode_build": True,
            },
            "config": self.settings.provenance(),
        }

    # -- stage 1: detection ------------------------------------------
    def detect_plates(
        self, frame_bgr: np.ndarray, *, conf_threshold: float | None = None
    ) -> list[PlateBox]:
        """Run the plate detector on a full BGR frame.

        Returns boxes in original-frame pixel coordinates. Embedded NMS is
        already applied by the exported graph; only the caller's score
        threshold is applied here.
        """
        self._ensure_sessions()
        threshold = (
            float(self.settings.detector_conf_threshold)
            if conf_threshold is None
            else float(conf_threshold)
        )
        tensor, ratio, padding = letterbox_detector_input(frame_bgr)
        try:
            raw = self._detector.run(
                [self._detector_output_name], {self._detector_input_name: tensor}
            )[0]
        except Exception as exc:  # noqa: BLE001
            raise PlateBackendError(f"detector_inference_failed:{type(exc).__name__}") from exc
        finally:
            self.detector_calls += 1

        arr = np.asarray(raw, dtype=np.float32)
        if arr.ndim == 3:
            arr = arr.reshape(arr.shape[0], -1)
        if arr.ndim != 2 or arr.shape[1] < 7:
            raise PlateBackendError(f"detector_output_unsupported:{arr.shape}")

        h_img, w_img = frame_bgr.shape[:2]
        boxes: list[PlateBox] = []
        for row in arr:
            if not np.all(np.isfinite(row)):
                continue
            score = float(row[6])
            if score < threshold:
                continue
            x1, y1, x2, y2 = unletterbox_box(row[1:5], ratio, padding)
            ix1 = max(0, min(w_img, int(round(x1))))
            iy1 = max(0, min(h_img, int(round(y1))))
            ix2 = max(0, min(w_img, int(round(x2))))
            iy2 = max(0, min(h_img, int(round(y2))))
            if ix2 - ix1 < 2 or iy2 - iy1 < 2:
                continue
            class_id = int(row[5])
            boxes.append(
                PlateBox(
                    x1=ix1,
                    y1=iy1,
                    x2=ix2,
                    y2=iy2,
                    score=score,
                    label="License Plate" if class_id == 0 else f"class_{class_id}",
                )
            )
        boxes.sort(key=lambda b: -b.score)
        return boxes

    # -- stage 2: recognition ----------------------------------------
    def recognize_plate(self, plate_crop_bgr: np.ndarray) -> OcrRead:
        """Run the OCR head on one plate crop. Counts as one OCR call."""
        self._ensure_sessions()
        if plate_crop_bgr is None or plate_crop_bgr.size == 0:
            return OcrRead(text="", plate_shape=None)
        height, width = self._ocr_size
        batch = preprocess_ocr_input(
            plate_crop_bgr,
            height=height,
            width=width,
            color_mode=self.settings.ocr_color_mode,
        )
        try:
            outputs = self._ocr.run(
                ["plate"], {self._ocr_input_name: batch}
            )[0]
        except Exception as exc:  # noqa: BLE001
            raise PlateBackendError(f"ocr_inference_failed:{type(exc).__name__}") from exc
        finally:
            self.ocr_calls += 1

        arr = np.asarray(outputs, dtype=np.float32)
        try:
            arr = arr.reshape((-1, self._max_slots, len(self._alphabet)))
        except ValueError as exc:
            raise PlateBackendError(f"ocr_output_unsupported:{np.asarray(outputs).shape}") from exc
        if arr.shape[0] < 1:
            return OcrRead(text="", plate_shape=(int(plate_crop_bgr.shape[1]), int(plate_crop_bgr.shape[0])))

        logits = arr[0]
        if not np.all(np.isfinite(logits)):
            return OcrRead(
                text="",
                plate_shape=(int(plate_crop_bgr.shape[1]), int(plate_crop_bgr.shape[0])),
            )
        indices = np.argmax(logits, axis=-1)
        scores = np.max(logits, axis=-1)
        alphabet = np.array(list(self._alphabet))
        chars = alphabet[indices]
        raw_text = "".join(str(ch) for ch in chars)
        # Only the declared padding character is removed, from the right. No
        # character substitution, no reordering, no format completion.
        text = raw_text.rstrip(self._pad_char)
        char_scores = [float(v) for v in scores[: len(text)]]
        return OcrRead(
            text=text,
            char_scores=char_scores or None,
            plate_shape=(int(plate_crop_bgr.shape[1]), int(plate_crop_bgr.shape[0])),
        )

    # -- adapter contract --------------------------------------------
    def predict(self, frame: np.ndarray) -> Sequence[Any]:
        """``PlateAlprBackend`` protocol: detect then OCR each plate box.

        Used by the injected-adapter path and by tests. The orchestration layer
        uses :meth:`detect_plates` / :meth:`recognize_plate` directly so quality
        gating and call budgets apply between the stages.
        """
        boxes = self.detect_plates(frame)
        results: list[AlprResultShim] = []
        for box in boxes:
            crop, rect = crop_from_box(frame, (box.x1, box.y1, box.x2, box.y2))
            report = check_plate_quality(
                crop,
                settings=self.settings,
                requested_box=(box.x1, box.y1, box.x2, box.y2),
                effective_rect=rect,
            )
            if not report.accepted:
                # A rejected plate is still a detection without OCR. Text is
                # never invented to fill a rejected crop.
                results.append(
                    AlprResultShim(
                        detection=AlprDetectionShim(box.label, box.score, box.as_plate_bounding_box())
                    )
                )
                continue
            read = self.recognize_plate(crop)
            results.append(
                AlprResultShim(
                    detection=AlprDetectionShim(box.label, box.score, box.as_plate_bounding_box()),
                    ocr=AlprOcrShim(text=read.text, confidence=read.char_scores),
                )
            )
        return results

    # -- adapter-shaped observations ---------------------------------
    @staticmethod
    def to_candidate_observation(result: Any) -> PlateCandidateObservation:
        """Map one shim result onto the shared adapter observation type."""
        detection = getattr(result, "detection", None)
        ocr = getattr(result, "ocr", None)
        if detection is None:
            raise PlateBackendError("result_missing_detection")
        box = getattr(detection, "bounding_box", None)
        return PlateCandidateObservation(
            ocr_raw=(getattr(ocr, "text", None) if ocr is not None else None),
            detection_label=getattr(detection, "label", None),
            detection_confidence=getattr(detection, "confidence", None),
            ocr_confidence=(getattr(ocr, "confidence", None) if ocr is not None else None),
            bounding_box=box,
        )


def _read_plate_config(path: str | Path) -> tuple[str, str, int]:
    """Read ``(alphabet, pad_char, max_plate_slots)`` from a plate config YAML.

    A tiny, strict reader is used on purpose: the application environment has
    no YAML dependency, and a hand-rolled strict parse of the three scalar keys
    we need avoids adding one. Anything unexpected raises rather than
    defaulting, so a changed upstream config cannot silently change decoding.
    """
    import re

    text = Path(path).read_text(encoding="utf-8")
    scalars: dict[str, str] = {}
    for key in ("alphabet", "pad_char", "max_plate_slots"):
        match = re.search(rf"^\s*{key}\s*:\s*(.+?)\s*$", text, re.MULTILINE)
        if not match:
            raise PlateBackendUnavailable(f"ocr_config_missing_key:{key}")
        scalars[key] = match.group(1).strip().strip("'\"")

    alphabet = scalars["alphabet"]
    pad_char = scalars["pad_char"]
    if len(pad_char) != 1:
        raise PlateBackendUnavailable("ocr_config_pad_char_invalid")
    try:
        max_slots = int(scalars["max_plate_slots"])
    except ValueError as exc:
        raise PlateBackendUnavailable("ocr_config_max_slots_invalid") from exc
    if max_slots <= 0:
        raise PlateBackendUnavailable("ocr_config_max_slots_invalid")
    return alphabet, pad_char, max_slots


def timed_call(fn, *args, **kwargs):  # pragma: no cover - trivial helper
    start = time.perf_counter()
    value = fn(*args, **kwargs)
    return value, (time.perf_counter() - start) * 1000.0


def finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None