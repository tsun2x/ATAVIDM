"""SQLite adapter implementation for TAVIDM."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Generator

DEFAULT_SQLITE_PATH = "database/tavidm.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"

VIOLATION_SORT_OPTIONS = {
    "newest": "detected_at DESC",
    "oldest": "detected_at ASC",
    "confidence_desc": "confidence DESC",
    "confidence_asc": "confidence ASC",
    "type": "violation_type ASC, detected_at DESC",
}


def get_db_path() -> str:
    # DB_BACKEND defaults to sqlite; keep backward compatibility with SQLITE_PATH.
    database_url = os.environ.get("DATABASE_URL")
    if database_url and not database_url.startswith(("postgres://", "postgresql://")):
        return database_url
    return os.environ.get("SQLITE_PATH", DEFAULT_SQLITE_PATH)


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def get_connection() -> sqlite3.Connection:
    db_path = get_db_path()
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def close_connection(conn: sqlite3.Connection) -> None:
    conn.close()


@contextmanager
def db_session() -> Generator[sqlite3.Connection, None, None]:
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _column_exists(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(row[1] == column_name for row in rows)


def _run_migrations(conn: sqlite3.Connection) -> None:
    migration_path = Path(__file__).parent / "migrations" / "001_zone_templates.sql"
    if migration_path.is_file():
        conn.executescript(migration_path.read_text(encoding="utf-8"))

    if _table_exists(conn, "videos"):
        if not _column_exists(conn, "videos", "status"):
            conn.execute(
                "ALTER TABLE videos ADD COLUMN status TEXT DEFAULT 'uploaded'"
            )
        if not _column_exists(conn, "videos", "annotation_id"):
            conn.execute("ALTER TABLE videos ADD COLUMN annotation_id INTEGER")
        if not _column_exists(conn, "videos", "template_id"):
            conn.execute("ALTER TABLE videos ADD COLUMN template_id INTEGER")

        conn.execute(
            """
            UPDATE videos
            SET status = 'processed'
            WHERE processed = 1 AND (status IS NULL OR status = 'uploaded')
            """
        )
        conn.execute(
            """
            UPDATE videos
            SET status = 'uploaded'
            WHERE status IS NULL
            """
        )


def init_db(force: bool = False) -> None:
    with db_session() as conn:
        if force:
            conn.executescript(
                """
                DROP TABLE IF EXISTS review_queue;
                DROP TABLE IF EXISTS violations;
                DROP TABLE IF EXISTS detections;
                DROP TABLE IF EXISTS annotations;
                DROP TABLE IF EXISTS videos;
                DROP TABLE IF EXISTS zone_templates;
                DROP TABLE IF EXISTS users;
                """
            )
            schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
            conn.executescript(schema_sql)
        elif not _table_exists(conn, "users"):
            schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
            conn.executescript(schema_sql)
        _run_migrations(conn)


# --- Users ---


def create_user(username: str, password_hash: str, role: str = "enforcer") -> int:
    with db_session() as conn:
        cursor = conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            (username, password_hash, role),
        )
        return cursor.lastrowid


def get_user_by_username(username: str) -> dict[str, Any] | None:
    with db_session() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        return _row_to_dict(row)


def list_users() -> list[dict[str, Any]]:
    with db_session() as conn:
        rows = conn.execute(
            "SELECT * FROM users ORDER BY created_at DESC"
        ).fetchall()
        return [_row_to_dict(row) for row in rows]


# --- Videos ---


def insert_video(
    filename: str,
    filepath: str,
    duration_sec: float | None = None,
    recorded_at: str | None = None,
    condition: str | None = None,
    status: str = "uploaded",
) -> int:
    with db_session() as conn:
        cursor = conn.execute(
            """
            INSERT INTO videos (filename, filepath, duration_sec, recorded_at, condition, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (filename, filepath, duration_sec, recorded_at, condition, status),
        )
        return cursor.lastrowid


def update_video(
    video_id: int,
    *,
    status: str | None = None,
    annotation_id: int | None = None,
    template_id: int | None = None,
    processed: bool | None = None,
    clear_template: bool = False,
) -> None:
    fields: list[str] = []
    params: list[Any] = []

    if status is not None:
        fields.append("status = ?")
        params.append(status)
    if annotation_id is not None:
        fields.append("annotation_id = ?")
        params.append(annotation_id)
    if template_id is not None:
        fields.append("template_id = ?")
        params.append(template_id)
    elif clear_template:
        fields.append("template_id = NULL")
    if processed is not None:
        fields.append("processed = ?")
        params.append(int(processed))

    if not fields:
        return

    params.append(video_id)
    with db_session() as conn:
        conn.execute(
            f"UPDATE videos SET {', '.join(fields)} WHERE id = ?",
            params,
        )


def get_video(video_id: int) -> dict[str, Any] | None:
    with db_session() as conn:
        row = conn.execute(
            "SELECT * FROM videos WHERE id = ?",
            (video_id,),
        ).fetchone()
        return _row_to_dict(row)


def list_videos(processed: bool | None = None) -> list[dict[str, Any]]:
    with db_session() as conn:
        if processed is None:
            rows = conn.execute(
                "SELECT * FROM videos ORDER BY created_at DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM videos WHERE processed = ? ORDER BY created_at DESC",
                (int(processed),),
            ).fetchall()
        return [_row_to_dict(row) for row in rows]


def mark_video_processed(video_id: int) -> None:
    with db_session() as conn:
        conn.execute(
            "UPDATE videos SET processed = 1, status = 'processed' WHERE id = ?",
            (video_id,),
        )


# --- Zone Templates ---


def list_zone_templates() -> list[dict[str, Any]]:
    with db_session() as conn:
        rows = conn.execute(
            """
            SELECT * FROM zone_templates
            ORDER BY COALESCE(last_used_at, created_at) DESC, template_name ASC
            """
        ).fetchall()
        return [_row_to_dict(row) for row in rows]


def get_zone_template(template_id: int) -> dict[str, Any] | None:
    with db_session() as conn:
        row = conn.execute(
            "SELECT * FROM zone_templates WHERE id = ?",
            (template_id,),
        ).fetchone()
        return _row_to_dict(row)


def create_zone_template(
    template_name: str,
    zones_json: str,
    description: str | None = None,
) -> int:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db_session() as conn:
        cursor = conn.execute(
            """
            INSERT INTO zone_templates (template_name, description, zones_json, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (template_name, description, zones_json, now),
        )
        return cursor.lastrowid


def update_zone_template(
    template_id: int,
    *,
    template_name: str | None = None,
    description: str | None = None,
    zones_json: str | None = None,
) -> None:
    fields: list[str] = ["updated_at = ?"]
    params: list[Any] = [datetime.now().strftime("%Y-%m-%d %H:%M:%S")]

    if template_name is not None:
        fields.append("template_name = ?")
        params.append(template_name)
    if description is not None:
        fields.append("description = ?")
        params.append(description)
    if zones_json is not None:
        fields.append("zones_json = ?")
        params.append(zones_json)

    params.append(template_id)
    with db_session() as conn:
        conn.execute(
            f"UPDATE zone_templates SET {', '.join(fields)} WHERE id = ?",
            params,
        )


def delete_zone_template(template_id: int) -> None:
    with db_session() as conn:
        conn.execute(
            "UPDATE videos SET template_id = NULL WHERE template_id = ?",
            (template_id,),
        )
        conn.execute("DELETE FROM zone_templates WHERE id = ?", (template_id,))


def duplicate_zone_template(template_id: int, new_name: str) -> int:
    source = get_zone_template(template_id)
    if source is None:
        raise ValueError(f"Template {template_id} not found.")
    return create_zone_template(
        template_name=new_name,
        zones_json=source["zones_json"],
        description=source.get("description"),
    )


def record_template_usage(template_id: int) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db_session() as conn:
        conn.execute(
            """
            UPDATE zone_templates
            SET usage_count = usage_count + 1,
                last_used_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (now, now, template_id),
        )


def count_videos_for_template(template_id: int) -> int:
    with db_session() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total FROM videos WHERE template_id = ?",
            (template_id,),
        ).fetchone()
        return row["total"] if row else 0


# --- Annotations ---


def get_annotation(annotation_id: int) -> dict[str, Any] | None:
    with db_session() as conn:
        row = conn.execute(
            "SELECT * FROM annotations WHERE id = ?",
            (annotation_id,),
        ).fetchone()
        return _row_to_dict(row)


def get_annotation_by_video(video_id: int) -> dict[str, Any] | None:
    with db_session() as conn:
        row = conn.execute(
            "SELECT * FROM annotations WHERE video_id = ?",
            (video_id,),
        ).fetchone()
        return _row_to_dict(row)


def upsert_annotation(
    video_id: int,
    zones_json: str,
    reference_frame_path: str | None = None,
) -> int:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    existing = get_annotation_by_video(video_id)
    with db_session() as conn:
        if existing:
            conn.execute(
                """
                UPDATE annotations
                SET zones_json = ?, reference_frame_path = COALESCE(?, reference_frame_path), updated_at = ?
                WHERE video_id = ?
                """,
                (zones_json, reference_frame_path, now, video_id),
            )
            annotation_id = existing["id"]
        else:
            cursor = conn.execute(
                """
                INSERT INTO annotations (video_id, zones_json, reference_frame_path, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (video_id, zones_json, reference_frame_path, now),
            )
            annotation_id = cursor.lastrowid

        conn.execute(
            """
            UPDATE videos
            SET annotation_id = ?, status = 'ready'
            WHERE id = ?
            """,
            (annotation_id, video_id),
        )
        return annotation_id


# --- Detections ---


def insert_detection(
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
) -> int:
    with db_session() as conn:
        cursor = conn.execute(
            """
            INSERT INTO detections (
                video_id, frame_number, timestamp_sec, track_id, class_label,
                confidence, bbox_x, bbox_y, bbox_w, bbox_h
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                video_id,
                frame_number,
                timestamp_sec,
                track_id,
                class_label,
                confidence,
                bbox_x,
                bbox_y,
                bbox_w,
                bbox_h,
            ),
        )
        return cursor.lastrowid


def bulk_insert_detections(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with db_session() as conn:
        conn.executemany(
            """
            INSERT INTO detections (
                video_id, frame_number, timestamp_sec, track_id, class_label,
                confidence, bbox_x, bbox_y, bbox_w, bbox_h
            ) VALUES (
                :video_id, :frame_number, :timestamp_sec, :track_id, :class_label,
                :confidence, :bbox_x, :bbox_y, :bbox_w, :bbox_h
            )
            """,
            rows,
        )


def get_detections_by_video(
    video_id: int,
    frame_start: int | None = None,
    frame_end: int | None = None,
) -> list[dict[str, Any]]:
    query = "SELECT * FROM detections WHERE video_id = ?"
    params: list[Any] = [video_id]

    if frame_start is not None:
        query += " AND frame_number >= ?"
        params.append(frame_start)
    if frame_end is not None:
        query += " AND frame_number <= ?"
        params.append(frame_end)

    query += " ORDER BY frame_number ASC, track_id ASC"

    with db_session() as conn:
        rows = conn.execute(query, params).fetchall()
        return [_row_to_dict(row) for row in rows]


# --- Violations ---


def insert_violation(
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
) -> int:
    with db_session() as conn:
        cursor = conn.execute(
            """
            INSERT INTO violations (
                video_id, track_id, violation_type, confidence, frame_number,
                timestamp_sec, evidence_path, reason_log, status, reviewed_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                video_id,
                track_id,
                violation_type,
                confidence,
                frame_number,
                timestamp_sec,
                evidence_path,
                reason_log,
                status,
                reviewed_by,
            ),
        )
        return cursor.lastrowid


def get_violation(violation_id: int) -> dict[str, Any] | None:
    with db_session() as conn:
        row = conn.execute(
            "SELECT * FROM violations WHERE id = ?",
            (violation_id,),
        ).fetchone()
        return _row_to_dict(row)


def _build_violation_filters(filters: dict[str, Any]) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    search = filters.get("search")
    if search:
        clauses.append(
            "(violation_type LIKE ? OR CAST(id AS TEXT) LIKE ? OR reason_log LIKE ?)"
        )
        pattern = f"%{search}%"
        params.extend([pattern, pattern, pattern])

    violation_type = filters.get("violation_type")
    if violation_type:
        clauses.append("violation_type = ?")
        params.append(violation_type)

    video_id = filters.get("video_id")
    if video_id is not None:
        clauses.append("video_id = ?")
        params.append(video_id)

    date_from = filters.get("date_from")
    if date_from:
        clauses.append("date(detected_at) >= date(?)")
        params.append(date_from)

    date_to = filters.get("date_to")
    if date_to:
        clauses.append("date(detected_at) <= date(?)")
        params.append(date_to)

    status = filters.get("status")
    if status:
        clauses.append("status = ?")
        params.append(status)

    where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where_clause, params


def list_violations(
    filters: dict[str, Any] | None = None,
    page: int = 1,
    per_page: int = 8,
    sort: str = "newest",
) -> tuple[list[dict[str, Any]], int]:
    filters = filters or {}
    where_clause, params = _build_violation_filters(filters)
    order_by = VIOLATION_SORT_OPTIONS.get(sort, VIOLATION_SORT_OPTIONS["newest"])
    offset = max(page - 1, 0) * per_page

    with db_session() as conn:
        count_row = conn.execute(
            f"SELECT COUNT(*) AS total FROM violations {where_clause}",
            params,
        ).fetchone()
        total = count_row["total"] if count_row else 0

        rows = conn.execute(
            f"""
            SELECT * FROM violations
            {where_clause}
            ORDER BY {order_by}
            LIMIT ? OFFSET ?
            """,
            [*params, per_page, offset],
        ).fetchall()

    return [_row_to_dict(row) for row in rows], total


def update_violation_status(
    violation_id: int,
    status: str,
    reviewed_by: int | None = None,
) -> None:
    with db_session() as conn:
        conn.execute(
            """
            UPDATE violations
            SET status = ?, reviewed_by = ?
            WHERE id = ?
            """,
            (status, reviewed_by, violation_id),
        )


def count_violations_today() -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    with db_session() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM violations
            WHERE date(detected_at) = date(?)
            """,
            (today,),
        ).fetchone()
        return row["total"] if row else 0


def count_violations_by_type() -> list[dict[str, Any]]:
    with db_session() as conn:
        rows = conn.execute(
            """
            SELECT violation_type, COUNT(*) AS count
            FROM violations
            WHERE status != 'dismissed'
            GROUP BY violation_type
            ORDER BY count DESC
            """
        ).fetchall()
        return [_row_to_dict(row) for row in rows]


# --- Review Queue ---


def insert_review_queue(
    video_id: int,
    track_id: int,
    violation_type: str,
    confidence: float,
    frame_number: int,
    evidence_path: str | None = None,
    reason_log: str | None = None,
    status: str = "pending",
) -> int:
    with db_session() as conn:
        cursor = conn.execute(
            """
            INSERT INTO review_queue (
                video_id, track_id, violation_type, confidence, frame_number,
                evidence_path, reason_log, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                video_id,
                track_id,
                violation_type,
                confidence,
                frame_number,
                evidence_path,
                reason_log,
                status,
            ),
        )
        return cursor.lastrowid


def list_review_queue(
    status: str = "pending",
    page: int = 1,
    per_page: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    offset = max(page - 1, 0) * per_page
    with db_session() as conn:
        count_row = conn.execute(
            "SELECT COUNT(*) AS total FROM review_queue WHERE status = ?",
            (status,),
        ).fetchone()
        total = count_row["total"] if count_row else 0

        rows = conn.execute(
            """
            SELECT * FROM review_queue
            WHERE status = ?
            ORDER BY queued_at DESC
            LIMIT ? OFFSET ?
            """,
            (status, per_page, offset),
        ).fetchall()

    return [_row_to_dict(row) for row in rows], total


def confirm_review_item(review_id: int, reviewed_by: int) -> int:
    with db_session() as conn:
        row = conn.execute(
            "SELECT * FROM review_queue WHERE id = ?",
            (review_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Review item {review_id} not found")

        reviewed_at = datetime.now().isoformat(sep=" ", timespec="seconds")
        cursor = conn.execute(
            """
            INSERT INTO violations (
                video_id, track_id, violation_type, confidence, frame_number,
                timestamp_sec, evidence_path, reason_log, status, reviewed_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', ?)
            """,
            (
                row["video_id"],
                row["track_id"],
                row["violation_type"],
                row["confidence"],
                row["frame_number"],
                row["frame_number"] / 30.0,
                row["evidence_path"],
                row["reason_log"],
                reviewed_by,
            ),
        )
        violation_id = cursor.lastrowid

        conn.execute(
            """
            UPDATE review_queue
            SET status = 'confirmed', reviewed_by = ?, reviewed_at = ?
            WHERE id = ?
            """,
            (reviewed_by, reviewed_at, review_id),
        )

        return violation_id


def dismiss_review_item(review_id: int, reviewed_by: int) -> None:
    reviewed_at = datetime.now().isoformat(sep=" ", timespec="seconds")
    with db_session() as conn:
        conn.execute(
            """
            UPDATE review_queue
            SET status = 'dismissed', reviewed_by = ?, reviewed_at = ?
            WHERE id = ?
            """,
            (reviewed_by, reviewed_at, review_id),
        )


# --- Dev seed ---


def seed_demo_data() -> None:
    """Insert minimal demo records for local verification."""
    with db_session() as conn:
        existing = conn.execute("SELECT COUNT(*) AS total FROM users").fetchone()
        if existing and existing["total"] > 0:
            return

    create_user("admin", "changeme-hash", role="admin")
    insert_video("morning_traffic.mp4", "dataset/raw/morning_traffic.mp4", condition="morning")
    insert_video("peak_traffic.mp4", "dataset/raw/peak_traffic.mp4", condition="peak")
