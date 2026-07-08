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
    def create_user(self, username: str, password_hash: str, role: str = "enforcer") -> int: ...
    def get_user_by_username(self, username: str) -> dict[str, Any] | None: ...
    def list_users(self) -> list[dict[str, Any]]: ...

    # Videos
    def insert_video(
        self,
        filename: str,
        filepath: str,
        duration_sec: float | None = None,
        recorded_at: str | None = None,
        condition: str | None = None,
        status: str = "uploaded",
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
    ) -> int: ...
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
    ) -> int: ...
    def list_review_queue(
        self,
        status: str = "pending",
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[dict[str, Any]], int]: ...
    def confirm_review_item(self, review_id: int, reviewed_by: int) -> int: ...
    def dismiss_review_item(self, review_id: int, reviewed_by: int) -> None: ...

    # Dev seed
    def seed_demo_data(self) -> None: ...
