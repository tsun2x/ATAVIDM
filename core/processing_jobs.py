"""Presentation-safe processing-job snapshots for global and Live Monitor UX."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config
from database import db


ACTIVE_POLL_MS = 1500
IDLE_POLL_MS = 10000
MAX_RECENT = 100


def _artifact_is_available(item: dict[str, Any]) -> bool:
    path_value = item.get("_artifact_path")
    if not (
        item.get("status") == "completed"
        and item.get("is_current_result")
        and not item.get("results_removed")
        and item.get("annotated_video_ready")
        and path_value
    ):
        return False
    try:
        path = Path(path_value).resolve(strict=False)
        root = Path(config.ANNOTATED_FOLDER).resolve(strict=False)
        path.relative_to(root)
    except (OSError, ValueError):
        return False
    return path.is_file()


def _present(item: dict[str, Any]) -> dict[str, Any]:
    result = {key: value for key, value in item.items() if not key.startswith("_")}
    video_id = int(item["video_id"])
    run_id = int(item["id"])
    tab = "active" if item.get("status") in ("queued", "running") else "completed"
    result["live_monitor_url"] = (
        f"/live-monitor?video_id={video_id}&run_id={run_id}&jobs={tab}"
    )
    result["source_video_url"] = f"/api/videos/{video_id}/media"
    result["reference_frame_url"] = f"/api/videos/{video_id}/frame"
    if _artifact_is_available(item):
        annotated = f"/api/videos/{video_id}/annotated/{run_id}"
        result["annotated_video_url"] = annotated
        result["download_url"] = f"{annotated}?download=1"
    else:
        result["annotated_video_url"] = None
        result["download_url"] = None
        result["annotated_video_ready"] = False
    return result


def build_processing_jobs_snapshot(
    *, recent_limit: int = 20, video_id: int | None = None
) -> dict[str, Any]:
    limit = max(1, min(int(recent_limit), MAX_RECENT))
    grouped = db.list_processing_jobs(recent_limit=limit, video_id=video_id)
    active = [_present(item) for item in grouped["active"]]
    recent = [_present(item) for item in grouped["recent"]]
    return {
        "success": True,
        "server_time": datetime.now(timezone.utc).isoformat(),
        "poll_after_ms": ACTIVE_POLL_MS if active else IDLE_POLL_MS,
        "active": active,
        "recent": recent,
    }
