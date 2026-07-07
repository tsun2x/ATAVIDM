"""Main pipeline orchestrator — Phase 3."""

from __future__ import annotations

from typing import Any

from core.detector import detect_frame
from core.tracker import TrackState
from core.violation_engine import RuleEngineState, ViolationEvent, evaluate_detection_rules
from core.zone_config import parse_zones_json
from database import db


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
        "zones": zones,
        "annotation_id": annotation["id"] if annotation else None,
        "template_id": video.get("template_id"),
        "template_name": template["template_name"] if template else None,
    }


def process_video(video_id: int) -> list[ViolationEvent]:
    """
    Orchestrate YOLOv8 → ByteTrack → rule engine for one video.

    TODO: Phase 3 — open video with OpenCV, iterate frames, persist violations to DB.
    Returns [] until frame iteration is implemented.
    """
    ctx = get_processing_context(video_id)
    _ = ctx

    track_state = TrackState()
    rule_state = RuleEngineState()
    violations: list[ViolationEvent] = []

    # TODO: cap = cv2.VideoCapture(ctx["video_path"])
    # for frame_number, frame in enumerate(frames):
    #     raw = detect_frame(frame, timestamp_sec=frame_number / fps)
    #     tracked = track_state.update(raw)
    #     violations.extend(
    #         evaluate_detection_rules(tracked, rule_state, frame_number, ctx["zones"])
    #     )

    _ = track_state, rule_state, detect_frame, evaluate_detection_rules
    return violations
