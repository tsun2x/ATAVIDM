"""Detection pipeline orchestrator (manuscript Ch3 workflow).

Video Acquisition -> Preprocessing (frame extraction/skip) -> YOLOv8m Detection
-> ByteTrack Tracking -> Rule-Based Violation Detection -> Evidence Recording
-> Review Queue (manual validation) -> Centralized Database.

Every violation event is routed to the manual review queue — the manuscript's
human-in-the-loop policy means violations are only confirmed by an operator.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from datetime import time as dtime
from typing import Any, Callable

import cv2

from core.detection_config import DEFAULT_RULE_PARAMETERS, confidence_band
from core.detector import Detector
from core.evidence import save_evidence_snapshot
from core.tracker import TrackState
from core.violation_engine import RuleEngineState, ViolationEvent, evaluate_detection_rules
from core.violation_config import load_enabled_violations
from core.zone_config import parse_zones_json
from database import db

_DETECTION_BATCH_SIZE = 500


def load_rule_parameters() -> dict[str, Any]:
    """Merge persisted system settings over the manuscript defaults."""
    params: dict[str, Any] = dict(DEFAULT_RULE_PARAMETERS)
    stored = db.get_all_settings()
    for key, default in DEFAULT_RULE_PARAMETERS.items():
        if key not in stored:
            continue
        raw = stored[key]
        try:
            if isinstance(default, bool):
                params[key] = raw.lower() in ("1", "true", "yes")
            elif isinstance(default, int):
                params[key] = int(float(raw))
            elif isinstance(default, float):
                params[key] = float(raw)
            else:
                params[key] = raw
        except (TypeError, ValueError):
            pass  # keep the default when a stored value is malformed
    return params


def get_processing_context(video_id: int) -> dict[str, Any]:
    """
    Load everything the detection pipeline needs for a video.
    Zones always come from the video annotation — never hardcoded.
    """
    video = db.get_video(video_id)
    if video is None:
        raise ValueError(f"Video {video_id} not found.")

    annotation = db.get_annotation_by_video(video_id)
    zones = parse_zones_json(annotation["zones_json"]) if annotation else {}

    template = None
    if video.get("template_id"):
        template = db.get_zone_template(video["template_id"])

    return {
        "video_id": video_id,
        "video_path": video["filepath"],
        "status": video.get("status"),
        "recorded_at": video.get("recorded_at"),
        "zones": zones,
        "annotation_id": annotation["id"] if annotation else None,
        "template_id": video.get("template_id"),
        "template_name": template["template_name"] if template else None,
    }


def _base_datetime(recorded_at: str | None) -> datetime | None:
    """Parse the video's recording start so time-based rules use scene time."""
    if not recorded_at:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(recorded_at, fmt)
        except ValueError:
            continue
    return None


def _scene_time(base: datetime | None, timestamp_sec: float) -> dtime:
    if base is not None:
        return (base + timedelta(seconds=timestamp_sec)).time()
    return datetime.now().time()


def _persist_event(
    event: ViolationEvent,
    frame: Any,
    detection: dict[str, Any] | None,
    video_id: int,
) -> None:
    """Save the evidence snapshot and queue the event for manual review."""
    evidence_path = None
    if detection is not None:
        evidence_path = save_evidence_snapshot(
            frame,
            detection,
            event.violation_type,
            source_key=f"video_{video_id}",
            frame_number=event.frame_number,
        )
    band = confidence_band(event.confidence)
    db.insert_review_queue(
        video_id=video_id,
        track_id=event.track_id,
        violation_type=event.violation_type,
        confidence=event.confidence,
        frame_number=event.frame_number,
        evidence_path=evidence_path,
        reason_log=f"[{band}] {event.reason_log}",
        vehicle_class=event.vehicle_class,
        timestamp_sec=event.timestamp_sec,
    )


def process_video(
    video_id: int,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[ViolationEvent]:
    """
    Run the full YOLOv8m -> ByteTrack -> rule engine pipeline on one video.

    Detections are persisted per processed frame; every violation event is
    snapshotted and queued for manual review. Returns the violation events.
    """
    ctx = get_processing_context(video_id)
    params = load_rule_parameters()
    enabled_violations = load_enabled_violations()
    base_dt = _base_datetime(ctx["recorded_at"])

    cap = cv2.VideoCapture(ctx["video_path"])
    if not cap.isOpened():
        raise ValueError(f"Cannot open video file: {ctx['video_path']}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    frame_skip = max(int(params["frame_skip"]), 1)
    conf_threshold = float(params["confidence_threshold"])

    detector = Detector()
    detector.load()
    track_state = TrackState(stationary_px=float(params["stationary_px"]))
    rule_state = RuleEngineState()

    db.update_video(video_id, status="processing")

    events: list[ViolationEvent] = []
    detection_batch: list[dict[str, Any]] = []
    frame_number = -1

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frame_number += 1
            if frame_number % frame_skip != 0:
                continue

            timestamp_sec = frame_number / fps
            raw = detector.track_frame(frame, conf=conf_threshold, timestamp_sec=timestamp_sec)
            tracked = track_state.update(raw)

            for det in tracked:
                detection_batch.append(
                    {
                        "video_id": video_id,
                        "frame_number": frame_number,
                        "timestamp_sec": timestamp_sec,
                        "track_id": det["track_id"],
                        "class_label": det["class_label"],
                        "confidence": det["confidence"],
                        "bbox_x": det["bbox_x"],
                        "bbox_y": det["bbox_y"],
                        "bbox_w": det["bbox_w"],
                        "bbox_h": det["bbox_h"],
                    }
                )
            if len(detection_batch) >= _DETECTION_BATCH_SIZE:
                db.bulk_insert_detections(detection_batch)
                detection_batch = []

            frame_events = evaluate_detection_rules(
                tracked,
                rule_state,
                frame_number,
                zones=ctx["zones"],
                params=params,
                now_time=_scene_time(base_dt, timestamp_sec),
                enabled_violations=enabled_violations,
            )
            by_track = {int(d["track_id"]): d for d in tracked}
            for event in frame_events:
                _persist_event(event, frame, by_track.get(event.track_id), video_id)
            events.extend(frame_events)

            if progress_callback and total_frames:
                progress_callback(frame_number + 1, total_frames)
    finally:
        cap.release()

    if detection_batch:
        db.bulk_insert_detections(detection_batch)

    db.mark_video_processed(video_id)
    return events
