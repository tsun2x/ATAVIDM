"""Public database facade used by the rest of the app.

This module keeps the existing `database.db` import path stable while routing
calls to the active backend adapter. Today only sqlite is implemented.
"""

from __future__ import annotations

import os
from types import ModuleType
from typing import Any, cast

from .adapter_base import DatabaseAdapter


def _load_backend_module() -> ModuleType:
    backend = os.environ.get("DB_BACKEND", "sqlite").strip().lower()
    if backend in {"", "sqlite"}:
        from . import sqlite_adapter

        return sqlite_adapter
    raise ValueError(
        f"Unsupported DB_BACKEND '{backend}'. Only 'sqlite' is available in this build."
    )


_adapter_module = _load_backend_module()
_adapter = cast(DatabaseAdapter, _adapter_module)

# Preserve existing db.* API by exposing the same callables from selected adapter.
init_db = _adapter.init_db
create_user = _adapter.create_user
get_user_by_username = _adapter.get_user_by_username
list_users = _adapter.list_users
insert_video = _adapter.insert_video
update_video = _adapter.update_video
get_video = _adapter.get_video
list_videos = _adapter.list_videos
mark_video_processed = _adapter.mark_video_processed
list_zone_templates = _adapter.list_zone_templates
get_zone_template = _adapter.get_zone_template
create_zone_template = _adapter.create_zone_template
update_zone_template = _adapter.update_zone_template
delete_zone_template = _adapter.delete_zone_template
duplicate_zone_template = _adapter.duplicate_zone_template
record_template_usage = _adapter.record_template_usage
count_videos_for_template = _adapter.count_videos_for_template
get_annotation = _adapter.get_annotation
get_annotation_by_video = _adapter.get_annotation_by_video
upsert_annotation = _adapter.upsert_annotation
insert_detection = _adapter.insert_detection
bulk_insert_detections = _adapter.bulk_insert_detections
get_detections_by_video = _adapter.get_detections_by_video
insert_violation = _adapter.insert_violation
get_violation = _adapter.get_violation
list_violations = _adapter.list_violations
update_violation_status = _adapter.update_violation_status
count_violations_today = _adapter.count_violations_today
count_violations_by_type = _adapter.count_violations_by_type
insert_review_queue = _adapter.insert_review_queue
list_review_queue = _adapter.list_review_queue
confirm_review_item = _adapter.confirm_review_item
dismiss_review_item = _adapter.dismiss_review_item
seed_demo_data = _adapter.seed_demo_data


def active_backend() -> str:
    return os.environ.get("DB_BACKEND", "sqlite").strip().lower() or "sqlite"


def __getattr__(name: str) -> Any:
    # Extension point: exposes backend-specific helper methods when needed.
    return getattr(_adapter_module, name)
