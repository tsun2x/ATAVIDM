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
) -> str:
    """
    Draw the offending detection on a copy of the frame and save it.

    Returns the saved path relative to the project root (e.g.
    ``static/evidence/video_3/illegal_parking_f001234_t007.jpg``) so it can be
    stored in the database and served by Flask.
    """
    out_dir = Path(EVIDENCE_FOLDER) / _slug(source_key)
    out_dir.mkdir(parents=True, exist_ok=True)

    x = int(detection["bbox_x"])
    y = int(detection["bbox_y"])
    w = int(detection["bbox_w"])
    h = int(detection["bbox_h"])
    confidence = float(detection.get("confidence", 0))
    track_id = int(detection.get("track_id", -1))

    annotated = frame.copy()
    cv2.rectangle(annotated, (x, y), (x + w, y + h), _BOX_COLOR, 2)
    label = f"{violation_type} {confidence * 100:.0f}%"
    (tw, th), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
    ty = max(y - 8, th + baseline)
    cv2.rectangle(annotated, (x, ty - th - baseline), (x + tw, ty + baseline), _BOX_COLOR, -1)
    cv2.putText(annotated, label, (x, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.6, _TEXT_COLOR, 2)

    filename = f"{_slug(violation_type)}_f{frame_number:06d}_t{track_id:03d}.jpg"
    out_path = out_dir / filename
    cv2.imwrite(str(out_path), annotated)

    base = Path(EVIDENCE_FOLDER).resolve().parent.parent  # project root
    try:
        return str(out_path.resolve().relative_to(base)).replace("\\", "/")
    except ValueError:
        return str(out_path)
