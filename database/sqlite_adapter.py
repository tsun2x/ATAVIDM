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


def _users_table_allows_viewer(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone()
    return row is not None and "'viewer'" in (row[0] or "")


def _rebuild_users_table(conn: sqlite3.Connection) -> None:
    """Recreate users with the manuscript's three roles (admin/enforcer/viewer)."""
    # FK references to users(id) would block the DROP; the copy preserves ids.
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        """
        CREATE TABLE users_new (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          username TEXT UNIQUE NOT NULL,
          password_hash TEXT NOT NULL,
          full_name TEXT,
          role TEXT CHECK(role IN ('admin','enforcer','viewer')) DEFAULT 'enforcer',
          is_active BOOLEAN DEFAULT 1,
          created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO users_new (id, username, password_hash, role, created_at)
          SELECT id, username, password_hash, role, created_at FROM users;
        DROP TABLE users;
        ALTER TABLE users_new RENAME TO users;
        """
    )
    conn.execute("PRAGMA foreign_keys = ON")


def _run_migrations(conn: sqlite3.Connection) -> None:
    migration_path = Path(__file__).parent / "migrations" / "001_zone_templates.sql"
    if migration_path.is_file():
        conn.executescript(migration_path.read_text(encoding="utf-8"))

    migration_002 = Path(__file__).parent / "migrations" / "002_processing_and_evidence.sql"
    if migration_002.is_file():
        # Apply 002 additively and idempotently: schema.sql (the fresh schema)
        # already contains these columns, so blind ALTERs would fail on a
        # from-scratch DB. Guard each change with an existence check; the raw
        # script remains the canonical reference for manual/outside migrations.
        _apply_migration_002(conn)

    if _table_exists(conn, "users"):
        if not _users_table_allows_viewer(conn):
            _rebuild_users_table(conn)
        if not _column_exists(conn, "users", "full_name"):
            conn.execute("ALTER TABLE users ADD COLUMN full_name TEXT")
        if not _column_exists(conn, "users", "is_active"):
            conn.execute("ALTER TABLE users ADD COLUMN is_active BOOLEAN DEFAULT 1")

    if _table_exists(conn, "violations"):
        if not _column_exists(conn, "violations", "vehicle_class"):
            conn.execute("ALTER TABLE violations ADD COLUMN vehicle_class TEXT")

    if _table_exists(conn, "review_queue"):
        if not _column_exists(conn, "review_queue", "vehicle_class"):
            conn.execute("ALTER TABLE review_queue ADD COLUMN vehicle_class TEXT")
        if not _column_exists(conn, "review_queue", "timestamp_sec"):
            conn.execute("ALTER TABLE review_queue ADD COLUMN timestamp_sec REAL")

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS cameras (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          location TEXT,
          rtsp_url TEXT NOT NULL,
          zones_json TEXT NOT NULL DEFAULT '{}',
          is_active BOOLEAN DEFAULT 1,
          created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
          updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS system_settings (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL,
          updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS reports (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          title TEXT NOT NULL,
          report_format TEXT CHECK(report_format IN ('pdf','excel')) NOT NULL,
          filters_json TEXT NOT NULL DEFAULT '{}',
          file_path TEXT NOT NULL,
          generated_by INTEGER REFERENCES users(id),
          generated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_reports_generated_at ON reports(generated_at);
        """
    )


def _apply_migration_002(conn: sqlite3.Connection) -> None:
    """Idempotently apply the processing + evidence schema changes.

    Mirrors migrations/002_processing_and_evidence.sql but guards each ALTER
    so it is safe to run on a DB already built from the current schema.sql.
    """
    if _table_exists(conn, "videos"):
        if not _column_exists(conn, "videos", "file_size_bytes"):
            conn.execute("ALTER TABLE videos ADD COLUMN file_size_bytes INTEGER")

    for table in ("review_queue", "violations"):
        for col, col_type in (
            ("vehicle_evidence_path", "TEXT"),
            ("plate_evidence_path", "TEXT"),
            ("plate_text", "TEXT"),
            ("plate_status", "TEXT CHECK(plate_status IN ('not_attempted','unreadable','recognized')) DEFAULT 'not_attempted'"),
        ):
            if _table_exists(conn, table) and not _column_exists(conn, table, col):
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")

    if not _table_exists(conn, "processing_runs"):
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS processing_runs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              video_id INTEGER REFERENCES videos(id) ON DELETE CASCADE,
              started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
              finished_at DATETIME,
              status TEXT CHECK(status IN ('queued','running','completed','failed')) DEFAULT 'queued',
              enabled_violations_json TEXT NOT NULL DEFAULT '[]',
              error_message TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_processing_runs_video_id ON processing_runs(video_id);
            CREATE INDEX IF NOT EXISTS idx_processing_runs_status ON processing_runs(status);
            """
        )


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
                DROP TABLE IF EXISTS reports;
                DROP TABLE IF EXISTS system_settings;
                DROP TABLE IF EXISTS cameras;
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


def create_user(
    username: str,
    password_hash: str,
    role: str = "enforcer",
    full_name: str | None = None,
) -> int:
    with db_session() as conn:
        cursor = conn.execute(
            "INSERT INTO users (username, password_hash, role, full_name) VALUES (?, ?, ?, ?)",
            (username, password_hash, role, full_name),
        )
        return cursor.lastrowid


def get_user(user_id: int) -> dict[str, Any] | None:
    with db_session() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        return _row_to_dict(row)


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


def update_user(
    user_id: int,
    *,
    full_name: str | None = None,
    role: str | None = None,
    password_hash: str | None = None,
    is_active: bool | None = None,
) -> None:
    fields: list[str] = []
    params: list[Any] = []
    if full_name is not None:
        fields.append("full_name = ?")
        params.append(full_name)
    if role is not None:
        fields.append("role = ?")
        params.append(role)
    if password_hash is not None:
        fields.append("password_hash = ?")
        params.append(password_hash)
    if is_active is not None:
        fields.append("is_active = ?")
        params.append(int(is_active))
    if not fields:
        return
    params.append(user_id)
    with db_session() as conn:
        conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", params)


# --- Videos ---


def insert_video(
    filename: str,
    filepath: str,
    duration_sec: float | None = None,
    recorded_at: str | None = None,
    condition: str | None = None,
    status: str = "uploaded",
    file_size_bytes: int | None = None,
) -> int:
    with db_session() as conn:
        cursor = conn.execute(
            """
            INSERT INTO videos (
                filename, filepath, duration_sec, recorded_at, condition, status, file_size_bytes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (filename, filepath, duration_sec, recorded_at, condition, status, file_size_bytes),
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


# --- Processing runs (per-run metadata + sequential queue) ---


def create_processing_run(video_id: int, enabled_violations_json: str) -> int:
    with db_session() as conn:
        cursor = conn.execute(
            """
            INSERT INTO processing_runs (
                video_id, status, enabled_violations_json, started_at, finished_at
            ) VALUES (?, 'queued', ?, NULL, NULL)
            """,
            (video_id, enabled_violations_json),
        )
        return cursor.lastrowid


def start_processing_run(run_id: int) -> None:
    """Mark a queued run as running (called by the worker when it pops the job)."""
    with db_session() as conn:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """
            UPDATE processing_runs
            SET status = 'running', started_at = COALESCE(started_at, ?)
            WHERE id = ?
            """,
            (now, run_id),
        )


def finish_processing_run(run_id: int, *, status: str = "completed", error_message: str | None = None) -> None:
    with db_session() as conn:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """
            UPDATE processing_runs
            SET status = ?, error_message = ?, finished_at = ?
            WHERE id = ?
            """,
            (status, error_message, now, run_id),
        )


def get_processing_run(run_id: int) -> dict[str, Any] | None:
    with db_session() as conn:
        row = conn.execute(
            "SELECT * FROM processing_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        return _row_to_dict(row)


def list_processing_runs(video_id: int) -> list[dict[str, Any]]:
    with db_session() as conn:
        rows = conn.execute(
            """
            SELECT * FROM processing_runs
            WHERE video_id = ?
            ORDER BY started_at DESC, id DESC
            """,
            (video_id,),
        ).fetchall()
        return [_row_to_dict(row) for row in rows]


def recover_orphaned_processing() -> int:
    """Reset videos and runs left mid-flight after an unclean shutdown.

    On startup there is no worker process, so any run left in 'queued' or
    'running' is orphaned: mark it 'failed', fail its 'queued' siblings too,
    and reset the stuck video back to 'ready' so it can be reprocessed.
    Returns the number of videos reset.
    """
    recovered = 0
    with db_session() as conn:
        rows = conn.execute(
            "SELECT id FROM videos WHERE status = 'processing'"
        ).fetchall()
        for row in rows:
            video_id = row["id"]
            conn.execute(
                "UPDATE videos SET status = 'ready' WHERE id = ?",
                (video_id,),
            )
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                """
                UPDATE processing_runs
                SET status = 'failed', finished_at = ?,
                    error_message = COALESCE(error_message, 'Recovered on startup: process not running.')
                WHERE video_id = ? AND status IN ('queued', 'running')
                """,
                (now, video_id),
            )
            recovered += 1
    # Second pass: a job waiting in the in-memory FIFO queue can leave
    # processing_runs.status = 'queued' (with videos.status = 'ready') if the
    # app crashed before the worker popped it. The video is already 'ready' so
    # the first pass never sees it; fail any stale queued/running runs here.
    with db_session() as conn:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """
            UPDATE processing_runs
            SET status = 'failed', finished_at = ?,
                error_message = COALESCE(error_message, 'Recovered on startup: worker not running.')
            WHERE status IN ('queued', 'running')
            """,
            (now,),
        )
    return recovered



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
    vehicle_class: str | None = None,
    vehicle_evidence_path: str | None = None,
    plate_evidence_path: str | None = None,
    plate_text: str | None = None,
    plate_status: str = "not_attempted",
) -> int:
    with db_session() as conn:
        cursor = conn.execute(
            """
            INSERT INTO violations (
                video_id, track_id, violation_type, vehicle_class, confidence,
                frame_number, timestamp_sec, evidence_path, reason_log, status, reviewed_by,
                vehicle_evidence_path, plate_evidence_path, plate_text, plate_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                video_id,
                track_id,
                violation_type,
                vehicle_class,
                confidence,
                frame_number,
                timestamp_sec,
                evidence_path,
                reason_log,
                status,
                reviewed_by,
                vehicle_evidence_path,
                plate_evidence_path,
                plate_text,
                plate_status,
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
        if isinstance(violation_type, (list, tuple, set)):
            values = [v for v in violation_type if v]
            if values:
                placeholders = ", ".join("?" for _ in values)
                clauses.append(f"violation_type IN ({placeholders})")
                params.extend(values)
        else:
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
    vehicle_class: str | None = None,
    timestamp_sec: float | None = None,
    vehicle_evidence_path: str | None = None,
    plate_evidence_path: str | None = None,
    plate_text: str | None = None,
    plate_status: str = "not_attempted",
) -> int:
    with db_session() as conn:
        cursor = conn.execute(
            """
            INSERT INTO review_queue (
                video_id, track_id, violation_type, vehicle_class, confidence,
                frame_number, timestamp_sec, evidence_path, reason_log, status,
                vehicle_evidence_path, plate_evidence_path, plate_text, plate_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                video_id,
                track_id,
                violation_type,
                vehicle_class,
                confidence,
                frame_number,
                timestamp_sec,
                evidence_path,
                reason_log,
                status,
                vehicle_evidence_path,
                plate_evidence_path,
                plate_text,
                plate_status,
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
        
        row_status = row["status"] if "status" in row.keys() else None
        if row_status != "pending":
            raise ValueError(f"Review item {review_id} is not pending (status: {row_status})")

        reviewed_at = datetime.now().isoformat(sep=" ", timespec="seconds")
        row_keys = row.keys()
        timestamp_sec = (
            row["timestamp_sec"]
            if "timestamp_sec" in row_keys and row["timestamp_sec"] is not None
            else (row["frame_number"] or 0) / 30.0
        )

        def _pick(col):
            return row[col] if col in row_keys else None

        cursor = conn.execute(
            """
            INSERT INTO violations (
                video_id, track_id, violation_type, vehicle_class, confidence,
                frame_number, timestamp_sec, evidence_path, reason_log, status, reviewed_by,
                vehicle_evidence_path, plate_evidence_path, plate_text, plate_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', ?, ?, ?, ?, ?)
            """,
            (
                row["video_id"],
                row["track_id"],
                row["violation_type"],
                _pick("vehicle_class"),
                row["confidence"],
                row["frame_number"],
                timestamp_sec,
                _pick("evidence_path"),
                _pick("reason_log"),
                reviewed_by,
                _pick("vehicle_evidence_path"),
                _pick("plate_evidence_path"),
                _pick("plate_text"),
                _pick("plate_status"),
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


def get_review_item(review_id: int) -> dict[str, Any] | None:
    with db_session() as conn:
        row = conn.execute(
            "SELECT * FROM review_queue WHERE id = ?",
            (review_id,),
        ).fetchone()
        return _row_to_dict(row)


def count_review_pending() -> int:
    with db_session() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total FROM review_queue WHERE status = 'pending'"
        ).fetchone()
        return row["total"] if row else 0


# --- System settings ---


def get_all_settings() -> dict[str, str]:
    with db_session() as conn:
        rows = conn.execute("SELECT key, value FROM system_settings").fetchall()
        return {row["key"]: row["value"] for row in rows}


def get_setting(key: str, default: str | None = None) -> str | None:
    with db_session() as conn:
        row = conn.execute(
            "SELECT value FROM system_settings WHERE key = ?",
            (key,),
        ).fetchone()
        return row["value"] if row else default


def set_settings(values: dict[str, Any]) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db_session() as conn:
        conn.executemany(
            """
            INSERT INTO system_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            [(key, str(value), now) for key, value in values.items()],
        )


# --- Cameras ---


def list_cameras(active_only: bool = False) -> list[dict[str, Any]]:
    query = "SELECT * FROM cameras"
    if active_only:
        query += " WHERE is_active = 1"
    query += " ORDER BY created_at DESC"
    with db_session() as conn:
        rows = conn.execute(query).fetchall()
        return [_row_to_dict(row) for row in rows]


def get_camera(camera_id: int) -> dict[str, Any] | None:
    with db_session() as conn:
        row = conn.execute(
            "SELECT * FROM cameras WHERE id = ?",
            (camera_id,),
        ).fetchone()
        return _row_to_dict(row)


def create_camera(
    name: str,
    rtsp_url: str,
    location: str | None = None,
    zones_json: str = "{}",
) -> int:
    with db_session() as conn:
        cursor = conn.execute(
            """
            INSERT INTO cameras (name, location, rtsp_url, zones_json)
            VALUES (?, ?, ?, ?)
            """,
            (name, location, rtsp_url, zones_json),
        )
        return cursor.lastrowid


def update_camera(
    camera_id: int,
    *,
    name: str | None = None,
    location: str | None = None,
    rtsp_url: str | None = None,
    zones_json: str | None = None,
    is_active: bool | None = None,
) -> None:
    fields: list[str] = ["updated_at = ?"]
    params: list[Any] = [datetime.now().strftime("%Y-%m-%d %H:%M:%S")]
    if name is not None:
        fields.append("name = ?")
        params.append(name)
    if location is not None:
        fields.append("location = ?")
        params.append(location)
    if rtsp_url is not None:
        fields.append("rtsp_url = ?")
        params.append(rtsp_url)
    if zones_json is not None:
        fields.append("zones_json = ?")
        params.append(zones_json)
    if is_active is not None:
        fields.append("is_active = ?")
        params.append(int(is_active))
    params.append(camera_id)
    with db_session() as conn:
        conn.execute(f"UPDATE cameras SET {', '.join(fields)} WHERE id = ?", params)


def delete_camera(camera_id: int) -> None:
    with db_session() as conn:
        conn.execute("DELETE FROM cameras WHERE id = ?", (camera_id,))


# --- Reports ---


def insert_report(
    title: str,
    report_format: str,
    file_path: str,
    filters_json: str = "{}",
    generated_by: int | None = None,
) -> int:
    with db_session() as conn:
        cursor = conn.execute(
            """
            INSERT INTO reports (title, report_format, filters_json, file_path, generated_by)
            VALUES (?, ?, ?, ?, ?)
            """,
            (title, report_format, filters_json, file_path, generated_by),
        )
        return cursor.lastrowid


def get_report(report_id: int) -> dict[str, Any] | None:
    with db_session() as conn:
        row = conn.execute(
            "SELECT * FROM reports WHERE id = ?",
            (report_id,),
        ).fetchone()
        return _row_to_dict(row)


def list_reports(limit: int = 50) -> list[dict[str, Any]]:
    with db_session() as conn:
        rows = conn.execute(
            """
            SELECT r.*, u.username AS generated_by_username
            FROM reports r
            LEFT JOIN users u ON u.id = r.generated_by
            ORDER BY r.generated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [_row_to_dict(row) for row in rows]


# --- Analytics aggregations ---


def violations_per_day(days: int = 30) -> list[dict[str, Any]]:
    with db_session() as conn:
        rows = conn.execute(
            """
            SELECT date(detected_at) AS day, COUNT(*) AS count
            FROM violations
            WHERE status != 'dismissed'
              AND date(detected_at) >= date('now', ?)
            GROUP BY date(detected_at)
            ORDER BY day ASC
            """,
            (f"-{int(days)} days",),
        ).fetchall()
        return [_row_to_dict(row) for row in rows]


def violations_by_hour(day: str | None = None) -> list[dict[str, Any]]:
    query = """
        SELECT CAST(strftime('%H', detected_at) AS INTEGER) AS hour, COUNT(*) AS count
        FROM violations
        WHERE status != 'dismissed'
    """
    params: list[Any] = []
    if day:
        query += " AND date(detected_at) = date(?)"
        params.append(day)
    query += " GROUP BY hour ORDER BY hour ASC"
    with db_session() as conn:
        rows = conn.execute(query, params).fetchall()
        return [_row_to_dict(row) for row in rows]


def count_violations_by_type_on(day: str) -> dict[str, int]:
    with db_session() as conn:
        rows = conn.execute(
            """
            SELECT violation_type, COUNT(*) AS count
            FROM violations
            WHERE status != 'dismissed' AND date(detected_at) = date(?)
            GROUP BY violation_type
            """,
            (day,),
        ).fetchall()
        return {row["violation_type"]: row["count"] for row in rows}


def violations_by_vehicle_class() -> list[dict[str, Any]]:
    with db_session() as conn:
        rows = conn.execute(
            """
            SELECT COALESCE(vehicle_class, 'Unclassified') AS vehicle_class, COUNT(*) AS count
            FROM violations
            WHERE status != 'dismissed'
            GROUP BY vehicle_class
            ORDER BY count DESC
            """
        ).fetchall()
        return [_row_to_dict(row) for row in rows]


def violations_by_video() -> list[dict[str, Any]]:
    with db_session() as conn:
        rows = conn.execute(
            """
            SELECT v.video_id, vid.filename, COUNT(*) AS count
            FROM violations v
            LEFT JOIN videos vid ON vid.id = v.video_id
            WHERE v.status != 'dismissed'
            GROUP BY v.video_id
            ORDER BY count DESC
            """
        ).fetchall()
        return [_row_to_dict(row) for row in rows]


def count_all_violations(status: str | None = None) -> int:
    with db_session() as conn:
        if status:
            row = conn.execute(
                "SELECT COUNT(*) AS total FROM violations WHERE status = ?",
                (status,),
            ).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) AS total FROM violations").fetchone()
        return row["total"] if row else 0
