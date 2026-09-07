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
get_user = _adapter.get_user
get_user_by_username = _adapter.get_user_by_username
list_users = _adapter.list_users
update_user = _adapter.update_user
insert_video = _adapter.insert_video
update_video = _adapter.update_video
get_video = _adapter.get_video
list_videos = _adapter.list_videos
mark_video_processed = _adapter.mark_video_processed
create_processing_run = _adapter.create_processing_run
start_processing_run = _adapter.start_processing_run
finish_processing_run = _adapter.finish_processing_run
get_processing_run = _adapter.get_processing_run
list_processing_runs = _adapter.list_processing_runs
recover_orphaned_processing = _adapter.recover_orphaned_processing
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
get_review_item = _adapter.get_review_item
count_review_pending = _adapter.count_review_pending
confirm_review_item = _adapter.confirm_review_item
dismiss_review_item = _adapter.dismiss_review_item
get_all_settings = _adapter.get_all_settings
get_setting = _adapter.get_setting
set_settings = _adapter.set_settings
list_cameras = _adapter.list_cameras
get_camera = _adapter.get_camera
create_camera = _adapter.create_camera
update_camera = _adapter.update_camera
delete_camera = _adapter.delete_camera
insert_report = _adapter.insert_report
get_report = _adapter.get_report
list_reports = _adapter.list_reports
violations_per_day = _adapter.violations_per_day
violations_by_hour = _adapter.violations_by_hour
violations_by_vehicle_class = _adapter.violations_by_vehicle_class
violations_by_video = _adapter.violations_by_video
count_all_violations = _adapter.count_all_violations

# Stage B: Legal-policy persistence, case linkage, audit, permissions
get_active_legal_policy_version = _adapter.get_active_legal_policy_version
get_legal_policy_version = _adapter.get_legal_policy_version
propose_legal_policy_version = _adapter.propose_legal_policy_version
approve_legal_policy_version = _adapter.approve_legal_policy_version
create_case_policy_record = _adapter.create_case_policy_record
record_case_action = _adapter.record_case_action
get_case_actions = _adapter.get_case_actions
user_has_permission = _adapter.user_has_permission
assign_policy_permission = _adapter.assign_policy_permission
revoke_policy_permission = _adapter.revoke_policy_permission
get_user_permissions = _adapter.get_user_permissions
can_confirm_case = _adapter.can_confirm_case
can_attest_print = _adapter.can_attest_print
can_approve_policy = _adapter.can_approve_policy
can_propose_policy = _adapter.can_propose_policy


def active_backend() -> str:
    return os.environ.get("DB_BACKEND", "sqlite").strip().lower() or "sqlite"


def __getattr__(name: str) -> Any:
    # Extension point: exposes backend-specific helper methods when needed.
    return getattr(_adapter_module, name)
