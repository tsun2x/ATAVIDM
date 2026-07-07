"""YOLOv8 inference wrapper — Phase 3.

TODO: Load custom weights from models/ after training with helmet annotations.
Until the model exposes motorcycle, person, and helmet classes, detect_frame returns [].
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.detection_config import REQUIRED_MODEL_CLASSES

_MODEL_PATH: Path | None = None
_MODEL_LOADED = False


def set_model_path(path: str | Path) -> None:
    global _MODEL_PATH
    _MODEL_PATH = Path(path)


def model_classes_available(detected_labels: set[str]) -> bool:
    """Return True when all classes needed for helmet/overloading rules are present."""
    return all(cls in detected_labels for cls in REQUIRED_MODEL_CLASSES)


def detect_frame(frame: Any, timestamp_sec: float = 0.0) -> list[dict[str, Any]]:
    """
    Run YOLOv8 on a single frame.

    Returns a list of detections:
        track_id, class_label, confidence, bbox_x, bbox_y, bbox_w, bbox_h, timestamp_sec

    Does not fabricate detections — returns [] until inference is wired.
    """
    # TODO: Phase 3 — import ultralytics YOLO, run inference, map class names.
    # TODO: Verify model_classes_available() before enabling helmet rules.
    _ = frame, timestamp_sec, _MODEL_PATH, _MODEL_LOADED
    return []
