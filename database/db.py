"""SQLite data access layer for TAVIDM."""

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


def init_db(force: bool = False) -> None:
    with db_session() as conn:
        if force:
            conn.executescript(
                """
                DROP TABLE IF EXISTS review_queue;
                DROP TABLE IF EXISTS violations;
                DROP TABLE IF EXISTS detections;
                DROP TABLE IF EXISTS videos;
                DROP TABLE IF EXISTS users;
                """
            )
        elif _table_exists(conn, "users"):
            return
        schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
        conn.executescript(schema_sql)


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
) -> int:
    with db_session() as conn:
        cursor = conn.execute(
            """
            INSERT INTO videos (filename, filepath, duration_sec, recorded_at, condition)
            VALUES (?, ?, ?, ?, ?)
            """,
            (filename, filepath, duration_sec, recorded_at, condition),
        )
        return cursor.lastrowid


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
            "UPDATE videos SET processed = 1 WHERE id = ?",
            (video_id,),
        )


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
