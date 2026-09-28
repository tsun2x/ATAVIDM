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

    Returns a project-relative path when possible, or an absolute path for a
    separately configured evidence directory. Files are served through an
    authenticated endpoint rather than Flask's public static route.

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
    try:
        out_path.resolve().relative_to(Path(EVIDENCE_FOLDER).resolve())
    except ValueError as exc:
        raise ValueError("Unsafe evidence path rejected (directory traversal).") from exc
    cv2.imwrite(str(out_path), annotated)

    try:
        return str(out_path.resolve().relative_to(Path(__file__).resolve().parents[1])).replace("\\", "/")
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

    Returns the project-relative path when possible, or an absolute path for
    an external configured evidence directory. Returns ``None`` if the bbox
    is missing or empty. Evidence is served through authenticated routes.

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
    try:
        out_path.resolve().relative_to(Path(EVIDENCE_FOLDER).resolve())
    except ValueError as exc:
        raise ValueError("Unsafe evidence path rejected (directory traversal).") from exc
    ok = cv2.imwrite(str(out_path), crop)
    if not ok:
        return None

    try:
        return str(out_path.resolve().relative_to(Path(__file__).resolve().parents[1])).replace("\\", "/")
    except ValueError:
        return str(out_path)


# ---------------------------------------------------------------------------
# Motorcycle detail-review evidence (separate from violation evidence)
# ---------------------------------------------------------------------------


def detail_root() -> Path:
    """Root of the private motorcycle detail-evidence tree."""
    return Path(EVIDENCE_FOLDER) / "detail"


def _safe_detail_key(value: str, *, label: str) -> str:
    text = str(value or "").strip()
    if not text or ".." in text or "/" in text or "\\" in text:
        raise ValueError(f"Unsafe detail evidence {label} rejected.")
    slug = _slug(text)
    if not slug:
        raise ValueError(f"Unsafe detail evidence {label} rejected.")
    return slug


def detail_run_dir(run_key: str) -> Path:
    """Run-scoped private directory: ``<evidence>/detail/<run_key>``.

    Run-specific paths cannot collide across reprocessing of the same video.
    """
    root = detail_root()
    out_dir = root / _safe_detail_key(run_key, label="run key")
    resolved_root = root.resolve()
    try:
        out_dir.resolve().relative_to(resolved_root)
    except ValueError as exc:  # pragma: no cover - defensive
        raise ValueError("Unsafe evidence path rejected (directory traversal).") from exc
    return out_dir


def detail_occurrence_dir(run_key: str, occurrence_key: str) -> Path:
    return detail_run_dir(run_key) / _safe_detail_key(occurrence_key, label="occurrence key")


def _stored_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path(__file__).resolve().parents[1])).replace("\\", "/")
    except ValueError:
        return str(path.resolve()).replace("\\", "/")


def _write_detail_image(path: Path, image: Any, *, quality: int) -> int | None:
    ok = cv2.imwrite(str(path), image, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok or not path.is_file():
        return None
    return int(path.stat().st_size)


def save_detail_evidence(
    frame: Any,
    crop_rect: Any,
    *,
    run_key: str,
    occurrence_key: str,
    frame_index: int,
    scene_quality: int = 85,
    crop_quality: int = 92,
) -> dict[str, Any] | None:
    """Save a source-resolution scene and a padded crop for one selected frame.

    Returns ``{"scene_path", "crop_path", "size_bytes"}`` or ``None`` when the
    crop is unusable or either image write fails. A failed write is never
    reported as stored evidence.
    """
    out_dir = detail_occurrence_dir(run_key, occurrence_key)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None

    h_img, w_img = frame.shape[:2]
    x1 = max(0, int(crop_rect.x))
    y1 = max(0, int(crop_rect.y))
    x2 = min(w_img, int(crop_rect.x + crop_rect.w))
    y2 = min(h_img, int(crop_rect.y + crop_rect.h))
    if x2 <= x1 or y2 <= y1:
        return None

    index = max(1, int(frame_index))
    scene_path = out_dir / f"scene_f{index}.jpg"
    crop_path = out_dir / f"crop_f{index}.jpg"
    scene_bytes = _write_detail_image(scene_path, frame, quality=scene_quality)
    if scene_bytes is None:
        return None
    crop = frame[y1:y2, x1:x2]
    crop_bytes = _write_detail_image(crop_path, crop, quality=crop_quality)
    if crop_bytes is None:
        try:
            scene_path.unlink()
        except OSError:  # pragma: no cover - best effort
            pass
        return None
    return {
        "scene_path": _stored_path(scene_path),
        "crop_path": _stored_path(crop_path),
        "size_bytes": int(scene_bytes) + int(crop_bytes),
    }


def save_detail_overlay(
    image: Any,
    *,
    run_key: str,
    occurrence_key: str,
    frame_index: int,
    quality: int = 85,
) -> str | None:
    """Save the mapped-box overlay for one selected frame (scan-time artifact)."""
    out_dir = detail_occurrence_dir(run_key, occurrence_key)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    out_path = out_dir / f"overlay_f{max(1, int(frame_index))}.jpg"
    if _write_detail_image(out_path, image, quality=quality) is None:
        return None
    return _stored_path(out_path)


def remove_detail_run(run_key: str) -> bool:
    """Delete one run's detail evidence tree (never touches other runs)."""
    import shutil

    try:
        out_dir = detail_run_dir(run_key)
    except ValueError:
        return False
    if not out_dir.is_dir():
        return False
    res = out_dir.resolve()
    root = detail_root().resolve()
    if res == root or root not in res.parents:
        return False
    shutil.rmtree(res, ignore_errors=True)
    return True
