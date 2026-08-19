"""Database adapter interface for pluggable backends.

This module defines the contract used by database.db facade.
Adapters (sqlite today, postgres/supabase in the future) should
implement the same callable surface so application code stays unchanged.
"""

from __future__ import annotations

from typing import Any, Protocol


class DatabaseAdapter(Protocol):
    def init_db(self, force: bool = False) -> None: ...

    # Users
    def create_user(
        self,
        username: str,
        password_hash: str,
        role: str = "enforcer",
        full_name: str | None = None,
    ) -> int: ...
    def get_user(self, user_id: int) -> dict[str, Any] | None: ...
    def get_user_by_username(self, username: str) -> dict[str, Any] | None: ...
    def list_users(self) -> list[dict[str, Any]]: ...
    def update_user(
        self,
        user_id: int,
        *,
        full_name: str | None = None,
        role: str | None = None,
        password_hash: str | None = None,
        is_active: bool | None = None,
    ) -> None: ...

    # Videos
    def insert_video(
        self,
        filename: str,
        filepath: str,
        duration_sec: float | None = None,
        recorded_at: str | None = None,
        condition: str | None = None,
        status: str = "uploaded",
        file_size_bytes: int | None = None,
    ) -> int: ...
    def update_video(
        self,
        video_id: int,
        *,
        status: str | None = None,
        annotation_id: int | None = None,
        template_id: int | None = None,
        processed: bool | None = None,
        clear_template: bool = False,
    ) -> None: ...
    def get_video(self, video_id: int) -> dict[str, Any] | None: ...
    def list_videos(self, processed: bool | None = None) -> list[dict[str, Any]]: ...
    def mark_video_processed(self, video_id: int) -> None: ...

    # Zone templates
    def list_zone_templates(self) -> list[dict[str, Any]]: ...
    def get_zone_template(self, template_id: int) -> dict[str, Any] | None: ...
    def create_zone_template(
        self,
        template_name: str,
        zones_json: str,
        description: str | None = None,
    ) -> int: ...
    def update_zone_template(
        self,
        template_id: int,
        *,
        template_name: str | None = None,
        description: str | None = None,
        zones_json: str | None = None,
    ) -> None: ...
    def delete_zone_template(self, template_id: int) -> None: ...
    def duplicate_zone_template(self, template_id: int, new_name: str) -> int: ...
    def record_template_usage(self, template_id: int) -> None: ...
    def count_videos_for_template(self, template_id: int) -> int: ...

    # Annotations
    def get_annotation(self, annotation_id: int) -> dict[str, Any] | None: ...
    def get_annotation_by_video(self, video_id: int) -> dict[str, Any] | None: ...
    def upsert_annotation(
        self,
        video_id: int,
        zones_json: str,
        reference_frame_path: str | None = None,
    ) -> int: ...

    # Detections
    def insert_detection(
        self,
        video_id: int,
        frame_number: int,
        timestamp_sec: float,
        track_id: int,
        class_label: str,
        confidence: float,
        bbox_x: float,
        bbox_y: float,
        bbox_w: float,
        bbox_h: float,
    ) -> int: ...
    def bulk_insert_detections(self, rows: list[dict[str, Any]]) -> None: ...
    def get_detections_by_video(
        self,
        video_id: int,
        frame_start: int | None = None,
        frame_end: int | None = None,
    ) -> list[dict[str, Any]]: ...

    # Violations
    def insert_violation(
        self,
        video_id: int,
        track_id: int,
        violation_type: str,
        confidence: float,
        frame_number: int,
        timestamp_sec: float,
        evidence_path: str | None = None,
        reason_log: str | None = None,
        status: str = "confirmed",
        reviewed_by: int | None = None,
        vehicle_class: str | None = None,
        vehicle_evidence_path: str | None = None,
        plate_evidence_path: str | None = None,
        plate_text: str | None = None,
        plate_status: str | None = None,
    ) -> int: ...
    def create_processing_run(self, video_id: int, enabled_violations_json: str) -> int: ...
    def start_processing_run(self, run_id: int) -> None: ...
    def finish_processing_run(
        self, run_id: int, *, status: str = "completed", error_message: str | None = None
    ) -> None: ...
    def get_processing_run(self, run_id: int) -> dict[str, Any] | None: ...
    def list_processing_runs(self, video_id: int) -> list[dict[str, Any]]: ...
    def recover_orphaned_processing(self) -> int: ...
    def get_violation(self, violation_id: int) -> dict[str, Any] | None: ...
    def list_violations(
        self,
        filters: dict[str, Any] | None = None,
        page: int = 1,
        per_page: int = 8,
        sort: str = "newest",
    ) -> tuple[list[dict[str, Any]], int]: ...
    def update_violation_status(
        self,
        violation_id: int,
        status: str,
        reviewed_by: int | None = None,
    ) -> None: ...
    def count_violations_today(self) -> int: ...
    def count_violations_by_type(self) -> list[dict[str, Any]]: ...

    # Review queue
    def insert_review_queue(
        self,
        video_id: int,
        track_id: int,
        violation_type: str,
        confidence: float,
        frame_number: int,
        evidence_path: str | None = None,
        reason_log: str | None = None,
        status: str = "pending",
        vehicle_class: str | None = None,
        timestamp_sec: float | None = None,
        vehicle_evidence_path: str | None = None,
        plate_evidence_path: str | None = None,
        plate_text: str | None = None,
        plate_status: str | None = None,
    ) -> int: ...
    def list_review_queue(
        self,
        status: str = "pending",
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[dict[str, Any]], int]: ...
    def get_review_item(self, review_id: int) -> dict[str, Any] | None: ...
    def count_review_pending(self) -> int: ...
    def confirm_review_item(self, review_id: int, reviewed_by: int) -> int: ...
    def dismiss_review_item(self, review_id: int, reviewed_by: int) -> None: ...

    # System settings
    def get_all_settings(self) -> dict[str, str]: ...
    def get_setting(self, key: str, default: str | None = None) -> str | None: ...
    def set_settings(self, values: dict[str, Any]) -> None: ...

    # Cameras
    def list_cameras(self, active_only: bool = False) -> list[dict[str, Any]]: ...
    def get_camera(self, camera_id: int) -> dict[str, Any] | None: ...
    def create_camera(
        self,
        name: str,
        rtsp_url: str,
        location: str | None = None,
        zones_json: str = "{}",
    ) -> int: ...
    def update_camera(
        self,
        camera_id: int,
        *,
        name: str | None = None,
        location: str | None = None,
        rtsp_url: str | None = None,
        zones_json: str | None = None,
        is_active: bool | None = None,
    ) -> None: ...
    def delete_camera(self, camera_id: int) -> None: ...

    # Reports
    def insert_report(
        self,
        title: str,
        report_format: str,
        file_path: str,
        filters_json: str = "{}",
        generated_by: int | None = None,
    ) -> int: ...
    def get_report(self, report_id: int) -> dict[str, Any] | None: ...
    def list_reports(self, limit: int = 50) -> list[dict[str, Any]]: ...

    # Analytics aggregations
    def violations_per_day(self, days: int = 30) -> list[dict[str, Any]]: ...
    def violations_by_hour(self) -> list[dict[str, Any]]: ...
    def violations_by_vehicle_class(self) -> list[dict[str, Any]]: ...
    def violations_by_video(self) -> list[dict[str, Any]]: ...
    def count_all_violations(self, status: str | None = None) -> int: ...
