"""Evidence recording (manuscript Ch3, Layer 4/5).

When a violation is detected the system captures the annotated frame
(bounding box, violation type, confidence) as an image snapshot whose path
is stored with the violation record.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import cv2

from config import EVIDENCE_FOLDER

_BOX_COLOR = (54, 57, 230)   # BGR red — offending object
_TEXT_COLOR = (255, 255, 255)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def save_evidence_snapshot(
    frame: Any,
    detection: dict[str, Any],
    violation_type: str,
    source_key: str,
    frame_number: int,
    *,
    violation_confidence: float | None = None,
    detection_confidence: float | None = None,
) -> str:
    """
    Draw the offending detection on a copy of the frame and save it.

    Returns the saved path relative to the project root (e.g.
    ``static/evidence/video_3/illegal_parking_f001234_t007.jpg``) so it can be
    stored in the database and served by Flask.

    Label shows violation_confidence when provided (not raw detector score).
    """
    out_dir = Path(EVIDENCE_FOLDER) / _slug(source_key)
    out_dir.mkdir(parents=True, exist_ok=True)

    x = int(detection["bbox_x"])
    y = int(detection["bbox_y"])
    w = int(detection["bbox_w"])
    h = int(detection["bbox_h"])
    if violation_confidence is not None:
        confidence = float(violation_confidence)
    else:
        confidence = float(detection.get("confidence", 0))
    track_id = int(detection.get("track_id", -1))

    annotated = frame.copy()
    cv2.rectangle(annotated, (x, y), (x + w, y + h), _BOX_COLOR, 2)
    label = f"{violation_type} {confidence * 100:.0f}%"
    if detection_confidence is not None:
        label = f"{violation_type} v{confidence * 100:.0f}% d{float(detection_confidence) * 100:.0f}%"
    (tw, th), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
    ty = max(y - 8, th + baseline)
    cv2.rectangle(annotated, (x, ty - th - baseline), (x + tw, ty + baseline), _BOX_COLOR, -1)
    cv2.putText(annotated, label, (x, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.55, _TEXT_COLOR, 2)

    filename = f"{_slug(violation_type)}_f{frame_number:06d}_t{track_id:03d}.jpg"
    # Reject traversal in filename components.
    if ".." in filename or "/" in filename or "\\" in filename:
        raise ValueError("Unsafe evidence filename rejected.")
    out_path = out_dir / filename
    # Ensure resolved path stays under evidence root.
    if not str(out_path.resolve()).startswith(str(Path(EVIDENCE_FOLDER).resolve())):
        raise ValueError("Unsafe evidence path rejected (directory traversal).")
    cv2.imwrite(str(out_path), annotated)

    base = Path(EVIDENCE_FOLDER).resolve().parent.parent  # project root
    try:
        return str(out_path.resolve().relative_to(base)).replace("\\", "/")
    except ValueError:
        return str(out_path)


def _crop_bbox(frame: Any, detection: dict[str, Any]) -> Any | None:
    """Return the cropped region for a detection's bbox, or None if invalid."""
    h_img, w_img = frame.shape[:2]
    x = int(round(float(detection["bbox_x"])))
    y = int(round(float(detection["bbox_y"])))
    w = int(round(float(detection["bbox_w"])))
    hh = int(round(float(detection["bbox_h"])))
    if w <= 0 or hh <= 0:
        return None
    # Clamp to frame bounds (detections are occasionally slightly out of frame).
    x1 = max(0, x)
    y1 = max(0, y)
    x2 = min(w_img, x + w)
    y2 = min(h_img, y + hh)
    if x2 <= x1 or y2 <= y1:
        return None
    return frame[y1:y2, x1:x2]


def save_vehicle_crop(
    frame: Any,
    detection: dict[str, Any],
    source_key: str,
    frame_number: int,
) -> str | None:
    """
    Save a tight crop of the detected vehicle/object (no annotation overlay).

    Returns the project-relative path (``static/evidence/<source>/vehicle_*.jpg``)
    or ``None`` if the bbox is missing or empty. This is the vehicle evidence
    image surfaced in the violations/review detail + evidence modals.

    NOTE: This is NOT license-plate recognition. No OCR or plate text is
    generated here; plate fields are managed separately and intentionally left
    as 'not_attempted' until an ALPR module is added.
    """
    crop = _crop_bbox(frame, detection)
    if crop is None:
        return None

    out_dir = Path(EVIDENCE_FOLDER) / _slug(source_key)
    out_dir.mkdir(parents=True, exist_ok=True)

    track_id = int(detection.get("track_id", -1))
    filename = f"vehicle_f{frame_number:06d}_t{track_id:03d}.jpg"
    out_path = out_dir / filename
    ok = cv2.imwrite(str(out_path), crop)
    if not ok:
        return None

    base = Path(EVIDENCE_FOLDER).resolve().parent.parent  # project root
    try:
        return str(out_path.resolve().relative_to(base)).replace("\\", "/")
    except ValueError:
        return str(out_path)
