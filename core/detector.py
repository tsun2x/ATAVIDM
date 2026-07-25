"""YOLOv8m inference + ByteTrack tracking wrapper (Ultralytics).

Project decision: TAVIDM uses YOLOv8m as the primary object detector and
ByteTrack for multi-object tracking (Ch3 System Architecture, Layers 3-4).

Weights resolution:
1. Custom fine-tuned YOLOv8m weights in ``models/`` (13-class traffic dataset,
   including helmet/rider classes) are loaded when present.
2. Otherwise the pretrained COCO YOLOv8m checkpoint is used as the development
   fallback (covers car, motorcycle, bus, truck, bicycle, person). Rules that
   require the helmet class remain inactive until custom weights are provided.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.detection_config import (
    CUSTOM_WEIGHTS_CANDIDATES,
    DETECTION_CLASSES,
    MODELS_DIR,
    MODEL_FAMILY,
    PRETRAINED_WEIGHTS,
    REQUIRED_MODEL_CLASSES,
)

_ALLOWED = set(DETECTION_CLASSES)


class DetectorError(RuntimeError):
    """Raised when the YOLOv8m model cannot be loaded or run."""


def resolve_weights_path() -> tuple[str, bool]:
    """Return (weights_path, is_custom)."""
    for candidate in CUSTOM_WEIGHTS_CANDIDATES:
        path = MODELS_DIR / candidate
        if path.is_file():
            return str(path), True
    return PRETRAINED_WEIGHTS, False


def model_classes_available(detected_labels: set[str]) -> bool:
    """True when all classes needed for helmet/overloading rules are present."""
    return all(cls in detected_labels for cls in REQUIRED_MODEL_CLASSES)


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
        self.load()
        return {str(name) for name in self._model.names.values()}

    def description(self) -> str:
        self.load()
        return f"{MODEL_FAMILY} ({'custom-trained' if self.is_custom else 'COCO pretrained'})"

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
            detections.append(
                {
                    "track_id": track_id,
                    "class_label": class_label,
                    "confidence": conf_val,
                    "bbox_x": x1,
                    "bbox_y": y1,
                    "bbox_w": max(0.0, x2 - x1),
                    "bbox_h": max(0.0, y2 - y1),
                    "timestamp_sec": timestamp_sec,
                }
            )
        return detections
