"""YOLOv8m inference + ByteTrack tracking wrapper (Ultralytics).

Project decision: TAVIDM uses YOLOv8m as the primary object detector and
ByteTrack for multi-object tracking (Ch3 System Architecture, Layers 3-4).

Weights resolution:
1. Custom fine-tuned YOLOv8m weights in ``models/`` (the seven-class traffic
   dataset baseline that is currently selected, or a future 15-class object-roster
   checkpoint) are loaded when present.
2. Otherwise the pretrained COCO YOLOv8m checkpoint is used as the development
   fallback (covers car, motorcycle, bus, truck, bicycle, person). Rules that
   require the helmet class remain inactive until custom weights are provided.

Class-roster contract:
A future 15-class checkpoint must expose the ordered 15-class object roster
declared in ``config/training/class_schema.json`` (``OBJECT_DETECTOR_CLASSES``).
``enforce_object_class_contract`` runs before that checkpoint processes any
video and rejects missing, duplicate, unknown, reordered, or malformed names.
Genuine legacy rosters (the frozen 10-class vehicle roster, the seven-class
baseline, the 13-class attribute checkpoint, COCO) keep working unchanged, but a
truncated or renamed object-roster export is rejected rather than demoted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.detection_config import (
    CUSTOM_WEIGHTS_CANDIDATES,
    DETECTION_CLASSES,
    LEGACY_ACCEPTED_MODEL_LABELS,
    MODELS_DIR,
    MODEL_FAMILY,
    OBJECT_DETECTOR_CLASSES,
    PRETRAINED_WEIGHTS,
    REQUIRED_MODEL_CLASSES,
    ObjectClassMapReport,
    ordered_class_names,
    resolve_vehicle_label,
    validate_object_class_map,
)

_ALLOWED = set(DETECTION_CLASSES) | set(LEGACY_ACCEPTED_MODEL_LABELS)


def _is_numeric_class_key(key: Any) -> bool:
    if isinstance(key, bool):
        return False
    if isinstance(key, int):
        return True
    if isinstance(key, str):
        text = key.strip()
        if not text:
            return False
        if text[0] in "+-":
            text = text[1:]
        return text.isdigit()
    return False


class DetectorError(RuntimeError):
    """Raised when the YOLOv8m model cannot be loaded or run."""


class DetectorClassContractError(DetectorError):
    """Raised when a checkpoint's ID->name map violates the class contract.

    Carries the structured ``report`` so callers can log the exact rejection
    reason (missing / duplicate / unknown / reordered / malformed / wrong count).
    """

    def __init__(self, message: str, report: ObjectClassMapReport) -> None:
        super().__init__(message)
        self.report = report


def resolve_weights_path() -> tuple[str, bool]:
    """Return (weights_path, is_custom)."""
    for candidate in CUSTOM_WEIGHTS_CANDIDATES:
        path = MODELS_DIR / candidate
        if path.is_file():
            return str(path), True
    return PRETRAINED_WEIGHTS, False


def model_classes_available(detected_labels: set[str]) -> bool:
    """True when all classes needed for helmet/overloading rules are present.

    Requires ``rider`` (never ``person``): rider association and the helmet
    taxonomy rules in ``core.violation_engine`` only consume ``rider`` boxes.
    """
    return all(cls in detected_labels for cls in REQUIRED_MODEL_CLASSES)


def object_class_contract_for(
    detector: Any,
    *,
    require_roster: bool = False,
) -> ObjectClassMapReport:
    """Resolve and validate ``detector``'s class map without assuming its type.

    A real ``Detector`` exposes ``raw_class_map`` (the checkpoint's own
    ``model.names``), so ID order is verified. Legacy duck-typed detectors that
    only expose the unordered ``class_names`` collection are still checked for
    exact membership, with the reordering check skipped because ID order is not
    observable from a set.
    """
    raw = getattr(detector, "raw_class_map", None)
    if callable(raw):
        try:
            raw = raw()
        except Exception:
            raw = None
    if isinstance(raw, (dict, list, tuple)):
        return validate_object_class_map(
            raw,
            expected=OBJECT_DETECTOR_CLASSES,
            require_roster=require_roster,
        )

    names = getattr(detector, "class_names", None)
    if names is None:
        return ObjectClassMapReport(
            class_count=0,
            is_object_roster=False,
            expected=OBJECT_DETECTOR_CLASSES,
            actual=(),
        )
    if isinstance(names, str):
        return validate_object_class_map(
            None,
            expected=OBJECT_DETECTOR_CLASSES,
            require_roster=require_roster,
            order_known=False,
        )
    try:
        members = sorted(
            {str(name).strip().lower() for name in names if str(name).strip()}
        )
    except TypeError:
        return ObjectClassMapReport(
            class_count=0,
            is_object_roster=False,
            expected=OBJECT_DETECTOR_CLASSES,
            actual=(),
        )
    return validate_object_class_map(
        members,
        expected=OBJECT_DETECTOR_CLASSES,
        require_roster=require_roster,
        order_known=False,
    )


def enforce_object_class_contract(
    detector: Any,
    *,
    require_roster: bool = False,
) -> ObjectClassMapReport:
    """Fail closed before a checkpoint processes video.

    A checkpoint that exposes the 15-class object roster must reproduce the exact
    ID order declared in ``config/training/class_schema.json``. Missing,
    duplicate, unknown, reordered, malformed, or mis-sized names raise
    ``DetectorClassContractError`` carrying the structured report.

    Genuine legacy rosters (the frozen 10-class vehicle roster, the seven-class
    baseline, the 13-class attribute checkpoint, COCO) return an accepted report
    and are unaffected. A mis-sized map that is built from object-roster names but
    is not a known legacy roster is a broken export, not a legacy model, and is
    rejected in the default auto mode as well.

    Set ``require_roster=True`` when the caller already knows the checkpoint is an
    object-roster checkpoint, to assert the roster up front regardless of size.
    """
    report = object_class_contract_for(detector, require_roster=require_roster)
    if not report.ok:
        raise DetectorClassContractError(
            f"{MODEL_FAMILY} checkpoint rejected before processing: {report.summary}",
            report,
        )
    return report


class Detector:
    """One detector+tracker instance per video/stream (ByteTrack is stateful)."""

    def __init__(self, weights: str | Path | None = None) -> None:
        self._model = None
        self._weights: str | None = str(weights) if weights else None
        self.is_custom = False

    def load(self) -> None:
        if self._model is not None:
            return
        try:
            from ultralytics import YOLO
        except ImportError as exc:  # pragma: no cover
            raise DetectorError(
                "ultralytics is not installed. Run: pip install -r requirements.txt"
            ) from exc

        if self._weights is None:
            self._weights, self.is_custom = resolve_weights_path()
        else:
            self.is_custom = Path(self._weights).is_file() and Path(self._weights).parent == MODELS_DIR

        try:
            self._model = YOLO(self._weights)
        except Exception as exc:
            raise DetectorError(f"Failed to load {MODEL_FAMILY} weights '{self._weights}': {exc}") from exc

    @property
    def class_names(self) -> set[str]:
        """Public class-name contract. Malformed mappings fail closed as empty."""
        try:
            self.load()
            raw = getattr(self._model, "names", None)
            if isinstance(raw, dict):
                if any(not _is_numeric_class_key(k) for k in raw):
                    return set()
                values = raw.values()
            elif isinstance(raw, (list, tuple, set)):
                values = raw
            else:
                return set()
            out: set[str] = set()
            for name in values:
                text = str(name).strip()
                if text:
                    out.add(text)
            return out
        except Exception:
            return set()

    def raw_class_map(self) -> Any:
        """The checkpoint's own ID->name map (``model.names``) or None.

        Returns the mapping unfiltered so the strict class-roster contract can
        see non-numeric keys, gaps, duplicates, and order. Never raises.
        """
        try:
            self.load()
            return getattr(self._model, "names", None)
        except Exception:
            return None

    def ordered_class_names(self) -> tuple[str, ...]:
        """Class names ordered by detector ID. Malformed maps yield ``()``."""
        return ordered_class_names(self.raw_class_map())

    def object_class_contract(
        self,
        *,
        require_roster: bool = False,
    ) -> ObjectClassMapReport:
        """Validate this checkpoint's ID->name map against the 15-class roster.

        ``require_roster=True`` asserts up front that the checkpoint *is* an
        object-roster checkpoint, so a truncated or unreadable map is rejected
        instead of being mistaken for a legacy vehicle-only checkpoint.
        """
        return object_class_contract_for(
            self, require_roster=require_roster
        )

    def description(self) -> str:
        self.load()
        return f"{MODEL_FAMILY} ({'custom-trained' if self.is_custom else 'COCO pretrained'})"

    def enforce_object_class_contract(
        self,
        *,
        require_roster: bool = False,
    ) -> ObjectClassMapReport:
        """Raise before any frame is processed if the class map is invalid."""
        report = enforce_object_class_contract(
            self, require_roster=require_roster
        )
        return report

    def track_frame(
        self,
        frame: Any,
        conf: float = 0.6,
        timestamp_sec: float = 0.0,
    ) -> list[dict[str, Any]]:
        """
        Run YOLOv8m detection + ByteTrack association on one frame.

        Returns detections in the pipeline format expected by ``TrackState``:
            track_id, class_label, confidence,
            bbox_x, bbox_y, bbox_w, bbox_h, timestamp_sec
        """
        self.load()
        results = self._model.track(
            source=frame,
            conf=conf,
            persist=True,
            tracker="bytetrack.yaml",
            verbose=False,
        )
        if not results:
            return []

        result = results[0]
        boxes = getattr(result, "boxes", None)
        if boxes is None or len(boxes) == 0:
            return []

        names = result.names or {}
        detections: list[dict[str, Any]] = []
        for box in boxes:
            if box.id is None:
                continue
            track_id = int(box.id[0])
            cls_id = int(box.cls[0]) if box.cls is not None else -1
            class_label = str(names.get(cls_id, cls_id)).lower()
            if class_label not in _ALLOWED:
                continue
            conf_val = float(box.conf[0]) if box.conf is not None else 0.0
            xyxy = box.xyxy[0].tolist() if box.xyxy is not None else [0.0, 0.0, 0.0, 0.0]
            x1, y1, x2, y2 = (float(v) for v in xyxy)
            resolved = resolve_vehicle_label(class_label)
            # Pipeline class_label is canonical when known; ambiguous piaggio keeps
            # raw label and is marked UNCERTAIN (fail-closed for vehicle rules).
            pipeline_label = (
                resolved.canonical_class
                if resolved.canonical_class is not None
                else resolved.raw_class
            )
            detections.append(
                {
                    "track_id": track_id,
                    "class_label": pipeline_label,
                    "raw_class": resolved.raw_class,
                    "canonical_class": resolved.canonical_class,
                    "class_review_state": resolved.review_state,
                    "confidence": conf_val,
                    "bbox_x": x1,
                    "bbox_y": y1,
                    "bbox_w": max(0.0, x2 - x1),
                    "bbox_h": max(0.0, y2 - y1),
                    "timestamp_sec": timestamp_sec,
                }
            )
        return detections
