"""Shared defaults and safe parsing for administrator processing limits."""

from __future__ import annotations

from typing import Any

MAX_PROCESSING_LIMIT = 3
PROCESSING_LIMIT_DEFAULTS = {
    "max_concurrent_video_jobs": 3,
    "max_concurrent_jobs_per_account": 1,
}


def _stored_limit(raw: Any, default: int) -> int:
    try:
        if isinstance(raw, bool):
            return default
        value = int(raw)
        if str(raw).strip() != str(value):
            return default
        return min(MAX_PROCESSING_LIMIT, max(1, value))
    except (TypeError, ValueError):
        return default


def parse_processing_limits(values: dict[str, Any]) -> dict[str, int]:
    """Read persisted limits defensively and keep account caps under global cap."""
    global_limit = _stored_limit(
        values.get("max_concurrent_video_jobs"),
        PROCESSING_LIMIT_DEFAULTS["max_concurrent_video_jobs"],
    )
    account_limit = _stored_limit(
        values.get("max_concurrent_jobs_per_account"),
        PROCESSING_LIMIT_DEFAULTS["max_concurrent_jobs_per_account"],
    )
    return {
        "max_concurrent_video_jobs": global_limit,
        "max_concurrent_jobs_per_account": min(account_limit, global_limit),
    }
