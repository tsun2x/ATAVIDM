"""YOLO class names and rule-engine tuning for the detection pipeline."""

from __future__ import annotations

# Classes required for helmet and overloading rules.
# TODO: Retrain YOLOv8 with helmet annotations — COCO default weights do not include "helmet".
YOLO_CLASS_MOTORCYCLE = "motorcycle"
YOLO_CLASS_PERSON = "person"
YOLO_CLASS_HELMET = "helmet"

REQUIRED_MODEL_CLASSES = (
    YOLO_CLASS_MOTORCYCLE,
    YOLO_CLASS_PERSON,
    YOLO_CLASS_HELMET,
)

# Seconds a condition must persist before confirming a violation (reduces false positives).
VIOLATION_PERSISTENCE_SEC = 1.5

# Expand motorcycle bbox by this factor when associating riders (width/height).
RIDER_ASSOCIATION_PADDING = 0.35

# Supported violation type strings (must match app.VIOLATION_TYPES).
NO_HELMET_VIOLATION = "No Helmet Violation"
MOTORCYCLE_OVERLOADING = "Motorcycle Overloading"
