"""Main pipeline orchestrator — Phase 3. Context loading for annotations."""

from __future__ import annotations

from typing import Any

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
