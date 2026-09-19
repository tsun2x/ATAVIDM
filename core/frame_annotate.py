"""Shared frame annotation for live preview and annotated MP4 output."""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

import cv2
import numpy as np

_BOX_COLOR = (246, 130, 59)  # BGR — detections
_VIOLATION_COLOR = (40, 40, 220)  # BGR — rule-fired tracks
_ZONE_COLOR = (57, 57, 230)
_LABEL_BG = (20, 20, 20)
_SCENE_COLORS = {
    "zones": (57, 57, 230),
    "lanes": (230, 150, 35),
    "flow_arrows": (45, 200, 255),
    "threshold_lines": (180, 70, 220),
    "markings": (220, 220, 60),
    "signs": (70, 200, 70),
    "activity_regions": (200, 100, 200),
}


def annotate_frame(
    frame: Any,
    detections: Sequence[Mapping[str, Any]],
    zones: Mapping[str, Sequence[Sequence[float]]] | None = None,
    violation_track_ids: Iterable[int] | None = None,
    scene: Any | None = None,
) -> Any:
    """Draw zones, detection boxes/labels, and highlight violation candidates.

    Labels include canonical class, confidence, and track ID when present.
    Violation-candidate tracks use a visually distinct box color.
    """
    annotated = frame.copy()
    viol_ids = {int(t) for t in (violation_track_ids or [])}

    if zones:
        for polygon in zones.values():
            if not polygon or len(polygon) < 3:
                continue
            pts = [(int(p[0]), int(p[1])) for p in polygon]
            for i in range(len(pts)):
                cv2.line(annotated, pts[i], pts[(i + 1) % len(pts)], _ZONE_COLOR, 2)
    if scene is not None:
        _draw_scene_document(annotated, scene)

    for det in detections:
        try:
            x = int(det.get("bbox_x", 0))
            y = int(det.get("bbox_y", 0))
            w = int(det.get("bbox_w", 0))
            h = int(det.get("bbox_h", 0))
        except (TypeError, ValueError):
            continue
        track_id = det.get("track_id")
        try:
            tid = int(track_id) if track_id is not None else None
        except (TypeError, ValueError):
            tid = None
        is_viol = tid is not None and tid in viol_ids
        color = _VIOLATION_COLOR if is_viol else _BOX_COLOR
        thickness = 3 if is_viol else 2
        cv2.rectangle(annotated, (x, y), (x + w, y + h), color, thickness)

        label_parts = [str(det.get("class_label") or "object")]
        if tid is not None:
            label_parts.append(f"#{tid}")
        try:
            conf = float(det.get("confidence", 0.0))
            label_parts.append(f"{conf * 100:.0f}%")
        except (TypeError, ValueError):
            pass
        if is_viol:
            label_parts.append("VIOLATION")
        label = " ".join(label_parts)
        _draw_label(annotated, label, x, max(y - 6, 12), color)

    return annotated


def _draw_scene_document(frame: Any, scene: Any) -> None:
    """Render the complete saved v2 scene, not only its legacy projection."""
    for collection_name, color in _SCENE_COLORS.items():
        objects = getattr(scene, collection_name, ()) or ()
        for obj in objects:
            points = getattr(obj, "points", ()) or ()
            if len(points) < 2:
                continue
            pts = [(int(p[0]), int(p[1])) for p in points]
            is_polygon = collection_name in {
                "zones", "lanes", "signs", "activity_regions"
            } and len(pts) >= 3
            if collection_name == "flow_arrows":
                cv2.arrowedLine(frame, pts[0], pts[-1], color, 2, tipLength=0.12)
                for start, end in zip(pts, pts[1:]):
                    cv2.line(frame, start, end, color, 2)
            else:
                for start, end in zip(pts, pts[1:]):
                    cv2.line(frame, start, end, color, 2)
                if is_polygon:
                    cv2.line(frame, pts[-1], pts[0], color, 2)


def _draw_label(frame: Any, text: str, x: int, y: int, color: tuple[int, int, int]) -> None:
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.5
    thickness = 1
    (tw, th), baseline = cv2.getTextSize(text, font, scale, thickness)
    pad = 3
    x1 = max(x, 0)
    y1 = max(y - th - pad, 0)
    x2 = min(x1 + tw + pad * 2, frame.shape[1] - 1)
    y2 = min(y1 + th + baseline + pad * 2, frame.shape[0] - 1)
    overlay = frame.copy()
    cv2.rectangle(overlay, (x1, y1), (x2, y2), _LABEL_BG, -1)
    cv2.addWeighted(overlay, 0.65, frame, 0.35, 0, frame)
    cv2.putText(frame, text, (x1 + pad, y2 - baseline - pad), font, scale, color, thickness, cv2.LINE_AA)


def encode_jpeg(frame: Any, quality: int = 80) -> bytes | None:
    ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        return None
    return buffer.tobytes()


def blank_frame(width: int = 640, height: int = 360, color: tuple[int, int, int] = (32, 32, 32)) -> Any:
    return np.full((height, width, 3), color, dtype=np.uint8)
