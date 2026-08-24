"""SQLite adapter implementation for TAVIDM."""

from __future__ import annotations

import json
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


class TemporalEvidenceNotReady(ValueError):
    """Review confirmation blocked until temporal evidence is finalized."""


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

    _apply_migration_003(conn)
    _apply_migration_004(conn)
    _apply_migration_005(conn)
    _apply_migration_006(conn)
    _apply_migration_007(conn)

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


def _apply_migration_003(conn: sqlite3.Connection) -> None:
    """Additive dual-confidence + temporal evidence columns (migration 003)."""
    dual_cols = (
        ("detection_confidence", "REAL"),
        ("violation_confidence", "REAL"),
        ("evidence_sufficiency", "REAL"),
        ("evidence_clip_path", "TEXT"),
        ("evidence_sequence_dir", "TEXT"),
        ("evidence_pre_sec", "REAL"),
        ("evidence_post_sec", "REAL"),
        ("episode_start_sec", "REAL"),
        ("episode_end_sec", "REAL"),
        ("contributing_factors_json", "TEXT"),
        ("unavailable_factors_json", "TEXT"),
    )
    for table in ("review_queue", "violations"):
        if not _table_exists(conn, table):
            continue
        for col, col_type in dual_cols:
            if not _column_exists(conn, table, col):
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")

    if _table_exists(conn, "processing_runs"):
        if not _column_exists(conn, "processing_runs", "diagnostics_json"):
            conn.execute("ALTER TABLE processing_runs ADD COLUMN diagnostics_json TEXT")
        if not _column_exists(conn, "processing_runs", "geometry_snapshot_json"):
            conn.execute(
                "ALTER TABLE processing_runs ADD COLUMN geometry_snapshot_json TEXT"
            )


def _apply_migration_004(conn: sqlite3.Connection) -> None:
    """Upload/processing UX: progress, artifacts, history, uploaded_by."""
    if _table_exists(conn, "videos") and not _column_exists(conn, "videos", "uploaded_by"):
        conn.execute("ALTER TABLE videos ADD COLUMN uploaded_by INTEGER")

    run_cols = (
        ("stage", "TEXT"),
        ("viewer_mode", "TEXT DEFAULT 'background'"),
        ("queued_at", "DATETIME"),
        ("frames_processed", "INTEGER DEFAULT 0"),
        ("total_frames", "INTEGER"),
        ("progress_percent", "REAL DEFAULT 0"),
        ("elapsed_sec", "REAL"),
        ("processing_fps", "REAL"),
        ("detection_records", "INTEGER DEFAULT 0"),
        ("unique_tracks", "INTEGER"),
        ("class_counts_json", "TEXT DEFAULT '{}'"),
        ("violation_candidates", "INTEGER DEFAULT 0"),
        ("annotated_video_path", "TEXT"),
        ("annotated_video_ready", "INTEGER DEFAULT 0"),
        ("model_identifier", "TEXT"),
        ("source_duration_sec", "REAL"),
        ("results_removed_at", "DATETIME"),
        ("effective_output_fps", "REAL"),
    )
    if _table_exists(conn, "processing_runs"):
        for col, col_type in run_cols:
            if not _column_exists(conn, "processing_runs", col):
                conn.execute(f"ALTER TABLE processing_runs ADD COLUMN {col} {col_type}")

    if not _table_exists(conn, "video_history_events"):
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS video_history_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
              run_id INTEGER REFERENCES processing_runs(id) ON DELETE SET NULL,
              event_type TEXT NOT NULL,
              detail_json TEXT NOT NULL DEFAULT '{}',
              actor_user_id INTEGER REFERENCES users(id),
              created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_video_history_video_id
              ON video_history_events(video_id);
            CREATE INDEX IF NOT EXISTS idx_video_history_created_at
              ON video_history_events(created_at);
            """
        )

    if not _table_exists(conn, "system_audit_events"):
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS system_audit_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              event_type TEXT NOT NULL,
              detail_json TEXT NOT NULL DEFAULT '{}',
              created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """
        )


def _apply_migration_005(conn: sqlite3.Connection) -> None:
    """Add processing_run_id attribution to detections and dependent result tables."""
    for table in ("detections", "review_queue", "violations"):
        if not _table_exists(conn, table):
            continue
        if not _column_exists(conn, table, "processing_run_id"):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN processing_run_id INTEGER")

    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_detections_run_id
          ON detections(processing_run_id);
        CREATE INDEX IF NOT EXISTS idx_detections_video_run
          ON detections(video_id, processing_run_id);
        CREATE INDEX IF NOT EXISTS idx_review_queue_run_id
          ON review_queue(processing_run_id);
        CREATE INDEX IF NOT EXISTS idx_violations_run_id
          ON violations(processing_run_id);
        """
    )


def _attributed_child_exists_clauses(conn: sqlite3.Connection) -> list[str]:
    """SQL EXISTS clauses proving a run owns attributed result rows."""
    clauses: list[str] = []
    if _table_exists(conn, "detections") and _column_exists(
        conn, "detections", "processing_run_id"
    ):
        clauses.append(
            "EXISTS (SELECT 1 FROM detections d WHERE d.processing_run_id = processing_runs.id)"
        )
    if _table_exists(conn, "review_queue") and _column_exists(
        conn, "review_queue", "processing_run_id"
    ):
        clauses.append(
            "EXISTS (SELECT 1 FROM review_queue rq WHERE rq.processing_run_id = processing_runs.id)"
        )
    if _table_exists(conn, "violations") and _column_exists(
        conn, "violations", "processing_run_id"
    ):
        clauses.append(
            "EXISTS (SELECT 1 FROM violations v WHERE v.processing_run_id = processing_runs.id)"
        )
    return clauses


def _apply_migration_006(conn: sqlite3.Connection) -> None:
    """Mark processing runs as run-scoped vs legacy for authoritative current results.

    results_run_scoped=1 means the run owns its result population even when empty.
    Fresh/backfill ownership is proven only by attributed child rows
    (processing_run_id). Progress, stage, diagnostics, and geometry are NOT
    provenance — migration 007 repairs earlier false positives from those signals.
    """
    if not _table_exists(conn, "processing_runs"):
        return
    if not _column_exists(conn, "processing_runs", "results_run_scoped"):
        conn.execute(
            "ALTER TABLE processing_runs "
            "ADD COLUMN results_run_scoped INTEGER NOT NULL DEFAULT 0"
        )

    child_exists = _attributed_child_exists_clauses(conn)
    if not child_exists:
        return
    where_bits = " OR ".join(child_exists)
    conn.execute(
        f"""
        UPDATE processing_runs
        SET results_run_scoped = 1
        WHERE COALESCE(results_run_scoped, 0) = 0
          AND ({where_bits})
        """
    )


def _apply_migration_007(conn: sqlite3.Connection) -> None:
    """Record explicit result-scope provenance and repair false-scoped legacy runs.

    results_scope_explicit=1 means ownership was recorded at create time or proven
    by attributed child rows. Ambiguous completed runs (diagnostics/geometry/stage
    only, zero attributed children) must remain legacy so NULL-attributed rows stay
    visible. Never deletes or rewrites detections/review/violations.
    """
    if not _table_exists(conn, "processing_runs"):
        return
    # Ensure 006 column exists before provenance repair.
    if not _column_exists(conn, "processing_runs", "results_run_scoped"):
        _apply_migration_006(conn)

    if not _column_exists(conn, "processing_runs", "results_scope_explicit"):
        conn.execute(
            "ALTER TABLE processing_runs "
            "ADD COLUMN results_scope_explicit INTEGER NOT NULL DEFAULT 0"
        )
    if not _column_exists(conn, "processing_runs", "results_scope_origin"):
        conn.execute(
            "ALTER TABLE processing_runs ADD COLUMN results_scope_origin TEXT"
        )

    child_exists = _attributed_child_exists_clauses(conn)
    attributed_sql = " OR ".join(child_exists) if child_exists else "0"

    # Historical runs with real attributed children: mark scoped + explicit.
    if child_exists:
        conn.execute(
            f"""
            UPDATE processing_runs
            SET results_run_scoped = 1,
                results_scope_explicit = 1,
                results_scope_origin = COALESCE(
                    NULLIF(results_scope_origin, ''),
                    'attributed_children'
                )
            WHERE COALESCE(results_scope_explicit, 0) = 0
              AND ({attributed_sql})
            """
        )

    # Repair false positives from migration 006 metadata backfill: scoped without
    # explicit provenance and without attributed children → legacy again.
    conn.execute(
        f"""
        UPDATE processing_runs
        SET results_run_scoped = 0
        WHERE COALESCE(results_run_scoped, 0) = 1
          AND COALESCE(results_scope_explicit, 0) = 0
          AND NOT ({attributed_sql})
        """
    )


def init_db(force: bool = False) -> None:
    with db_session() as conn:
        if force:
            conn.executescript(
                """
                DROP TABLE IF EXISTS system_audit_events;
                DROP TABLE IF EXISTS video_history_events;
                DROP TABLE IF EXISTS processing_runs;
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
    uploaded_by: int | None = None,
) -> int:
    with db_session() as conn:
        if uploaded_by is not None:
            row = conn.execute(
                "SELECT id FROM users WHERE id = ?",
                (uploaded_by,),
            ).fetchone()
            if row is None:
                uploaded_by = None
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor = conn.execute(
            """
            INSERT INTO videos (
                filename, filepath, duration_sec, recorded_at, condition,
                status, file_size_bytes, uploaded_by, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                filename,
                filepath,
                duration_sec,
                recorded_at,
                condition,
                status,
                file_size_bytes,
                uploaded_by,
                now,
            ),
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


def create_processing_run(
    video_id: int,
    enabled_violations_json: str,
    *,
    viewer_mode: str = "background",
) -> int:
    with db_session() as conn:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        has_scoped = _column_exists(conn, "processing_runs", "results_run_scoped")
        has_explicit = _column_exists(conn, "processing_runs", "results_scope_explicit")
        has_origin = _column_exists(conn, "processing_runs", "results_scope_origin")
        if has_scoped and has_explicit and has_origin:
            cursor = conn.execute(
                """
                INSERT INTO processing_runs (
                    video_id, status, enabled_violations_json, started_at, finished_at,
                    stage, viewer_mode, queued_at,
                    results_run_scoped, results_scope_explicit, results_scope_origin
                ) VALUES (?, 'queued', ?, NULL, NULL, 'queued', ?, ?, 1, 1, 'create')
                """,
                (video_id, enabled_violations_json, viewer_mode, now),
            )
        elif has_scoped and has_explicit:
            cursor = conn.execute(
                """
                INSERT INTO processing_runs (
                    video_id, status, enabled_violations_json, started_at, finished_at,
                    stage, viewer_mode, queued_at,
                    results_run_scoped, results_scope_explicit
                ) VALUES (?, 'queued', ?, NULL, NULL, 'queued', ?, ?, 1, 1)
                """,
                (video_id, enabled_violations_json, viewer_mode, now),
            )
        elif has_scoped:
            cursor = conn.execute(
                """
                INSERT INTO processing_runs (
                    video_id, status, enabled_violations_json, started_at, finished_at,
                    stage, viewer_mode, queued_at, results_run_scoped
                ) VALUES (?, 'queued', ?, NULL, NULL, 'queued', ?, ?, 1)
                """,
                (video_id, enabled_violations_json, viewer_mode, now),
            )
        else:
            cursor = conn.execute(
                """
                INSERT INTO processing_runs (
                    video_id, status, enabled_violations_json, started_at, finished_at,
                    stage, viewer_mode, queued_at
                ) VALUES (?, 'queued', ?, NULL, NULL, 'queued', ?, ?)
                """,
                (video_id, enabled_violations_json, viewer_mode, now),
            )
        return cursor.lastrowid


def start_processing_run(run_id: int) -> None:
    """Mark a queued run as running (called by the worker when it pops the job)."""
    with db_session() as conn:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """
            UPDATE processing_runs
            SET status = 'running',
                stage = COALESCE(NULLIF(stage, 'queued'), 'loading_model'),
                started_at = COALESCE(started_at, ?)
            WHERE id = ?
            """,
            (now, run_id),
        )


def update_processing_run_progress(run_id: int, progress: dict[str, Any]) -> None:
    """Persist a progress snapshot (called periodically from the worker)."""
    fields = []
    params: list[Any] = []
    mapping = {
        "stage": "stage",
        "frames_processed": "frames_processed",
        "total_frames": "total_frames",
        "progress_percent": "progress_percent",
        "elapsed_sec": "elapsed_sec",
        "processing_fps": "processing_fps",
        "detection_records": "detection_records",
        "unique_tracks": "unique_tracks",
        "violation_candidates": "violation_candidates",
        "model_identifier": "model_identifier",
        "source_duration_sec": "source_duration_sec",
        "effective_output_fps": "effective_output_fps",
        "annotated_video_path": "annotated_video_path",
        "annotated_video_ready": "annotated_video_ready",
        "error_message": "error_message",
    }
    for src, col in mapping.items():
        if src in progress and progress[src] is not None:
            fields.append(f"{col} = ?")
            val = progress[src]
            if src == "annotated_video_ready":
                val = int(bool(val))
            params.append(val)
    if "class_counts" in progress and progress["class_counts"] is not None:
        fields.append("class_counts_json = ?")
        params.append(json.dumps(progress["class_counts"]))
    if not fields:
        return
    params.append(run_id)
    with db_session() as conn:
        conn.execute(
            f"UPDATE processing_runs SET {', '.join(fields)} WHERE id = ?",
            params,
        )


def finish_processing_run(
    run_id: int,
    *,
    status: str = "completed",
    error_message: str | None = None,
    diagnostics_json: str | None = None,
    geometry_snapshot_json: str | None = None,
    progress: dict[str, Any] | None = None,
) -> None:
    with db_session() as conn:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        stage = "completed" if status == "completed" else "failed"
        ann_path = None
        ann_ready = 0
        class_counts_json = None
        extra_sets = ""
        extra_params: list[Any] = []
        if progress:
            if progress.get("stage"):
                stage = progress["stage"]
            ann_path = progress.get("annotated_video_path")
            ann_ready = int(bool(progress.get("annotated_video_ready")))
            if progress.get("class_counts") is not None:
                class_counts_json = json.dumps(progress["class_counts"])
            for key, col in (
                ("frames_processed", "frames_processed"),
                ("total_frames", "total_frames"),
                ("progress_percent", "progress_percent"),
                ("elapsed_sec", "elapsed_sec"),
                ("processing_fps", "processing_fps"),
                ("detection_records", "detection_records"),
                ("unique_tracks", "unique_tracks"),
                ("violation_candidates", "violation_candidates"),
                ("model_identifier", "model_identifier"),
                ("source_duration_sec", "source_duration_sec"),
                ("effective_output_fps", "effective_output_fps"),
            ):
                if progress.get(key) is not None:
                    extra_sets += f", {col} = ?"
                    extra_params.append(progress[key])
            if class_counts_json is not None:
                extra_sets += ", class_counts_json = ?"
                extra_params.append(class_counts_json)
            if ann_path is not None:
                extra_sets += ", annotated_video_path = ?"
                extra_params.append(ann_path)
            extra_sets += ", annotated_video_ready = ?"
            extra_params.append(ann_ready)

        conn.execute(
            f"""
            UPDATE processing_runs
            SET status = ?, error_message = ?, finished_at = ?, stage = ?,
                diagnostics_json = COALESCE(?, diagnostics_json),
                geometry_snapshot_json = COALESCE(?, geometry_snapshot_json)
                {extra_sets}
            WHERE id = ?
            """,
            (
                status,
                error_message,
                now,
                stage,
                diagnostics_json,
                geometry_snapshot_json,
                *extra_params,
                run_id,
            ),
        )


def get_processing_run(run_id: int) -> dict[str, Any] | None:
    with db_session() as conn:
        row = conn.execute(
            "SELECT * FROM processing_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        return _row_to_dict(row)


def get_latest_processing_run(video_id: int) -> dict[str, Any] | None:
    """Latest processing attempt (any status) — activity / failure surface."""
    with db_session() as conn:
        row = conn.execute(
            """
            SELECT * FROM processing_runs
            WHERE video_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (video_id,),
        ).fetchone()
        return _row_to_dict(row)


def get_authoritative_completed_run(video_id: int) -> dict[str, Any] | None:
    """Latest completed run-scoped result (authoritative even with zero detections)."""
    with db_session() as conn:
        if not _column_exists(conn, "processing_runs", "results_run_scoped"):
            return None
        row = conn.execute(
            """
            SELECT * FROM processing_runs
            WHERE video_id = ?
              AND status = 'completed'
              AND COALESCE(results_run_scoped, 0) = 1
            ORDER BY COALESCE(finished_at, started_at) DESC, id DESC
            LIMIT 1
            """,
            (video_id,),
        ).fetchone()
        return _row_to_dict(row)


def get_latest_completed_processing_run(video_id: int) -> dict[str, Any] | None:
    """Latest successfully completed run (any scoping) — artifact fallback."""
    with db_session() as conn:
        row = conn.execute(
            """
            SELECT * FROM processing_runs
            WHERE video_id = ? AND status = 'completed'
            ORDER BY COALESCE(finished_at, started_at) DESC, id DESC
            LIMIT 1
            """,
            (video_id,),
        ).fetchone()
        return _row_to_dict(row)


def get_current_completed_result_run(video_id: int) -> dict[str, Any] | None:
    """Completed run whose artifacts/results are current for UI.

    Prefer an authoritative run-scoped completed run. If none exists, fall back
    to the latest completed run (legacy / pre-flag era annotated replay).
    """
    authoritative = get_authoritative_completed_run(video_id)
    if authoritative is not None:
        return authoritative
    return get_latest_completed_processing_run(video_id)


def resolve_current_detection_scope(video_id: int) -> tuple[str, int | None]:
    """Return ('run', run_id) for authoritative scoped results, else ('legacy', None).

    Authority is never inferred from detection row counts.
    """
    run = get_authoritative_completed_run(video_id)
    if run is not None:
        return "run", int(run["id"])
    return "legacy", None


def list_processing_runs(video_id: int) -> list[dict[str, Any]]:
    with db_session() as conn:
        rows = conn.execute(
            """
            SELECT * FROM processing_runs
            WHERE video_id = ?
            ORDER BY COALESCE(queued_at, started_at) DESC, id DESC
            """,
            (video_id,),
        ).fetchall()
        return [_row_to_dict(row) for row in rows]


def clear_processing_run_artifact(run_id: int) -> None:
    with db_session() as conn:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """
            UPDATE processing_runs
            SET annotated_video_path = NULL,
                annotated_video_ready = 0,
                results_removed_at = ?
            WHERE id = ?
            """,
            (now, run_id),
        )


def insert_video_history_event(
    video_id: int,
    event_type: str,
    *,
    detail: dict[str, Any] | None = None,
    run_id: int | None = None,
    actor_user_id: int | None = None,
) -> int:
    with db_session() as conn:
        # Drop actor if it would violate FK (stale session / deleted user).
        if actor_user_id is not None:
            row = conn.execute(
                "SELECT id FROM users WHERE id = ?",
                (actor_user_id,),
            ).fetchone()
            if row is None:
                actor_user_id = None
        cursor = conn.execute(
            """
            INSERT INTO video_history_events (
                video_id, run_id, event_type, detail_json, actor_user_id
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                video_id,
                run_id,
                event_type,
                json.dumps(detail or {}),
                actor_user_id,
            ),
        )
        return cursor.lastrowid


def list_video_history_events(video_id: int) -> list[dict[str, Any]]:
    with db_session() as conn:
        rows = conn.execute(
            """
            SELECT * FROM video_history_events
            WHERE video_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (video_id,),
        ).fetchall()
        return [_row_to_dict(row) for row in rows]


def insert_system_audit_event(event_type: str, *, detail: dict[str, Any] | None = None) -> int:
    with db_session() as conn:
        cursor = conn.execute(
            """
            INSERT INTO system_audit_events (event_type, detail_json)
            VALUES (?, ?)
            """,
            (event_type, json.dumps(detail or {})),
        )
        return cursor.lastrowid


def list_system_audit_events(
    *,
    event_type: str | None = None,
) -> list[dict[str, Any]]:
    with db_session() as conn:
        if event_type:
            rows = conn.execute(
                """
                SELECT * FROM system_audit_events
                WHERE event_type = ?
                ORDER BY id ASC
                """,
                (event_type,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM system_audit_events ORDER BY id ASC"
            ).fetchall()
        return [_row_to_dict(row) for row in rows]


def count_confirmed_violations_for_video(video_id: int) -> int:
    with db_session() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS n FROM violations
            WHERE video_id = ? AND status = 'confirmed'
            """,
            (video_id,),
        ).fetchone()
        return int(row["n"] if row else 0)


def count_reports_referencing_video(video_id: int) -> int:
    """Best-effort: reports store filters_json which may mention video_id."""
    needle = f'"video_id": {int(video_id)}'
    alt = f'"video_id":{int(video_id)}'
    with db_session() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS n FROM reports
            WHERE filters_json LIKE ? OR filters_json LIKE ?
            """,
            (f"%{needle}%", f"%{alt}%"),
        ).fetchone()
        return int(row["n"] if row else 0)


def list_review_items_for_video(
    video_id: int,
    *,
    status: str | None = "pending",
) -> list[dict[str, Any]]:
    with db_session() as conn:
        if status is None:
            rows = conn.execute(
                "SELECT * FROM review_queue WHERE video_id = ?",
                (video_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM review_queue WHERE video_id = ? AND status = ?",
                (video_id, status),
            ).fetchall()
        return [_row_to_dict(row) for row in rows]


def list_violation_rows_for_video(video_id: int) -> list[dict[str, Any]]:
    with db_session() as conn:
        rows = conn.execute(
            "SELECT * FROM violations WHERE video_id = ?",
            (video_id,),
        ).fetchall()
        return [_row_to_dict(row) for row in rows]


def delete_unconfirmed_results_for_video(video_id: int) -> None:
    """Remove detections and pending/dismissed review rows; keep confirmed violations."""
    with db_session() as conn:
        conn.execute("DELETE FROM detections WHERE video_id = ?", (video_id,))
        conn.execute(
            """
            DELETE FROM review_queue
            WHERE video_id = ? AND status IN ('pending', 'dismissed')
            """,
            (video_id,),
        )


def delete_video_cascade_unprotected(video_id: int) -> None:
    """Delete video row and unprotected dependents (no confirmed violations expected)."""
    with db_session() as conn:
        _cascade_delete_video_rows(conn, video_id)


def _cascade_delete_video_rows(conn: sqlite3.Connection, video_id: int) -> None:
    conn.execute("DELETE FROM detections WHERE video_id = ?", (video_id,))
    conn.execute("DELETE FROM review_queue WHERE video_id = ?", (video_id,))
    conn.execute("DELETE FROM violations WHERE video_id = ?", (video_id,))
    conn.execute("DELETE FROM annotations WHERE video_id = ?", (video_id,))
    conn.execute("DELETE FROM processing_runs WHERE video_id = ?", (video_id,))
    conn.execute("DELETE FROM video_history_events WHERE video_id = ?", (video_id,))
    conn.execute("DELETE FROM videos WHERE id = ?", (video_id,))


def delete_video_cascade_with_audit(
    video_id: int,
    *,
    event_type: str,
    detail: dict[str, Any] | None = None,
) -> int:
    """Atomically cascade-delete a video and insert a durable system audit event.

    Uses one SQLite transaction: either both succeed or neither commits.
    """
    with db_session() as conn:
        _cascade_delete_video_rows(conn, video_id)
        cursor = conn.execute(
            """
            INSERT INTO system_audit_events (event_type, detail_json)
            VALUES (?, ?)
            """,
            (event_type, json.dumps(detail or {})),
        )
        return int(cursor.lastrowid)


def detection_summary_for_video(video_id: int) -> dict[str, Any]:
    """Current detection summary for a video.

    Authoritative run-scoped completed runs own the current population even when
    they contain zero detections. Legacy NULL rows are used only when no such
    authoritative completed run exists. Failed/queued/running attempts never
    replace a prior completed result.
    """
    mode, run_id = resolve_current_detection_scope(video_id)

    with db_session() as conn:
        if mode == "run" and run_id is not None:
            where = "video_id = ? AND processing_run_id = ?"
            params: tuple[Any, ...] = (video_id, run_id)
            cand_where = "video_id = ? AND processing_run_id = ?"
            cand_params: tuple[Any, ...] = (video_id, run_id)
        else:
            where = "video_id = ? AND processing_run_id IS NULL"
            params = (video_id,)
            cand_where = "video_id = ? AND processing_run_id IS NULL"
            cand_params = (video_id,)

        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM detections WHERE {where}",
            params,
        ).fetchone()["n"]
        tracks = conn.execute(
            f"""
            SELECT COUNT(DISTINCT track_id) AS n FROM detections
            WHERE {where} AND track_id IS NOT NULL
            """,
            params,
        ).fetchone()["n"]
        class_rows = conn.execute(
            f"""
            SELECT class_label, COUNT(*) AS n FROM detections
            WHERE {where}
            GROUP BY class_label
            ORDER BY n DESC
            """,
            params,
        ).fetchall()
        candidates = conn.execute(
            f"SELECT COUNT(*) AS n FROM review_queue WHERE {cand_where}",
            cand_params,
        ).fetchone()["n"]
    return {
        "detection_records": int(total or 0),
        "unique_tracks": int(tracks or 0),
        "class_counts": {r["class_label"]: int(r["n"]) for r in class_rows},
        "violation_candidates": int(candidates or 0),
        "processing_run_id": run_id,
        "result_scope": mode,
    }


def upload_processing_analytics(start_s: str, end_s: str, *, grain: str = "day") -> dict[str, Any]:
    """Aggregate upload + processing metrics. Upload counts use videos.created_at only."""
    with db_session() as conn:
        unique_uploads = conn.execute(
            """
            SELECT COUNT(*) AS n FROM videos
            WHERE created_at >= ? AND created_at < ?
            """,
            (start_s, end_s),
        ).fetchone()["n"]

        status_rows = conn.execute(
            """
            SELECT status, COUNT(*) AS n FROM videos
            WHERE created_at >= ? AND created_at < ?
            GROUP BY status
            """,
            (start_s, end_s),
        ).fetchall()
        by_status = {r["status"]: int(r["n"]) for r in status_rows}

        # Current inventory (not limited to period) for operational queues
        inv = conn.execute(
            """
            SELECT status, COUNT(*) AS n FROM videos GROUP BY status
            """
        ).fetchall()
        inventory = {r["status"]: int(r["n"]) for r in inv}

        run_stats = conn.execute(
            """
            SELECT
              COUNT(*) AS total_runs,
              SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS successful_runs,
              SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_runs,
              AVG(
                CASE
                  WHEN status = 'completed' AND started_at IS NOT NULL AND finished_at IS NOT NULL
                  THEN (julianday(finished_at) - julianday(started_at)) * 86400.0
                  ELSE NULL
                END
              ) AS avg_duration_sec,
              SUM(COALESCE(detection_records, 0)) AS detection_records,
              SUM(COALESCE(violation_candidates, 0)) AS violation_candidates
            FROM processing_runs
            WHERE COALESCE(queued_at, started_at, finished_at) >= ?
              AND COALESCE(queued_at, started_at, finished_at) < ?
            """,
            (start_s, end_s),
        ).fetchone()

        class_rows = conn.execute(
            """
            SELECT d.class_label, COUNT(*) AS n
            FROM detections d
            JOIN processing_runs pr ON pr.id = d.processing_run_id
            WHERE pr.status = 'completed'
              AND COALESCE(pr.results_run_scoped, 0) = 1
              AND COALESCE(pr.queued_at, pr.started_at, pr.finished_at) >= ?
              AND COALESCE(pr.queued_at, pr.started_at, pr.finished_at) < ?
            GROUP BY d.class_label
            """,
            (start_s, end_s),
        ).fetchall()
        # Legacy NULL-attributed detections only for in-period uploads that still
        # have no authoritative run-scoped completed result.
        legacy_rows = conn.execute(
            """
            SELECT d.class_label, COUNT(*) AS n
            FROM detections d
            JOIN videos v ON v.id = d.video_id
            WHERE d.processing_run_id IS NULL
              AND v.created_at >= ? AND v.created_at < ?
              AND NOT EXISTS (
                SELECT 1 FROM processing_runs pr
                WHERE pr.video_id = d.video_id
                  AND pr.status = 'completed'
                  AND COALESCE(pr.results_run_scoped, 0) = 1
              )
            GROUP BY d.class_label
            """,
            (start_s, end_s),
        ).fetchall()
        class_counts: dict[str, int] = {}
        for r in class_rows:
            if r["class_label"]:
                class_counts[r["class_label"]] = class_counts.get(r["class_label"], 0) + int(r["n"])
        for r in legacy_rows:
            if r["class_label"]:
                class_counts[r["class_label"]] = class_counts.get(r["class_label"], 0) + int(r["n"])

        # Keep detection_records aligned with the same population as class_counts
        # (run counters for scoped completed runs + legacy NULL counts).
        scoped_det = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM detections d
            JOIN processing_runs pr ON pr.id = d.processing_run_id
            WHERE pr.status = 'completed'
              AND COALESCE(pr.results_run_scoped, 0) = 1
              AND COALESCE(pr.queued_at, pr.started_at, pr.finished_at) >= ?
              AND COALESCE(pr.queued_at, pr.started_at, pr.finished_at) < ?
            """,
            (start_s, end_s),
        ).fetchone()["n"]
        legacy_det = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM detections d
            JOIN videos v ON v.id = d.video_id
            WHERE d.processing_run_id IS NULL
              AND v.created_at >= ? AND v.created_at < ?
              AND NOT EXISTS (
                SELECT 1 FROM processing_runs pr
                WHERE pr.video_id = d.video_id
                  AND pr.status = 'completed'
                  AND COALESCE(pr.results_run_scoped, 0) = 1
              )
            """,
            (start_s, end_s),
        ).fetchone()["n"]
        aligned_detection_records = int(scoped_det or 0) + int(legacy_det or 0)

        scoped_cand = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM review_queue rq
            JOIN processing_runs pr ON pr.id = rq.processing_run_id
            WHERE pr.status = 'completed'
              AND COALESCE(pr.results_run_scoped, 0) = 1
              AND COALESCE(pr.queued_at, pr.started_at, pr.finished_at) >= ?
              AND COALESCE(pr.queued_at, pr.started_at, pr.finished_at) < ?
            """,
            (start_s, end_s),
        ).fetchone()["n"]
        legacy_cand = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM review_queue rq
            JOIN videos v ON v.id = rq.video_id
            WHERE rq.processing_run_id IS NULL
              AND v.created_at >= ? AND v.created_at < ?
              AND NOT EXISTS (
                SELECT 1 FROM processing_runs pr
                WHERE pr.video_id = rq.video_id
                  AND pr.status = 'completed'
                  AND COALESCE(pr.results_run_scoped, 0) = 1
              )
            """,
            (start_s, end_s),
        ).fetchone()["n"]
        aligned_candidates = int(scoped_cand or 0) + int(legacy_cand or 0)

        if grain == "month":
            trunc = "strftime('%Y-%m', created_at)"
            run_trunc = "strftime('%Y-%m', COALESCE(queued_at, started_at, finished_at))"
        elif grain == "week":
            trunc = "strftime('%Y-%W', created_at)"
            run_trunc = "strftime('%Y-%W', COALESCE(queued_at, started_at, finished_at))"
        else:
            trunc = "date(created_at)"
            run_trunc = "date(COALESCE(queued_at, started_at, finished_at))"

        upload_trend = conn.execute(
            f"""
            SELECT {trunc} AS bucket, COUNT(*) AS n
            FROM videos
            WHERE created_at >= ? AND created_at < ?
            GROUP BY bucket
            ORDER BY bucket
            """,
            (start_s, end_s),
        ).fetchall()
        run_trend = conn.execute(
            f"""
            SELECT {run_trunc} AS bucket, COUNT(*) AS n
            FROM processing_runs
            WHERE COALESCE(queued_at, started_at, finished_at) >= ?
              AND COALESCE(queued_at, started_at, finished_at) < ?
            GROUP BY bucket
            ORDER BY bucket
            """,
            (start_s, end_s),
        ).fetchall()

    return {
        "unique_videos_uploaded": int(unique_uploads or 0),
        "videos_awaiting_annotation": int(inventory.get("annotating", 0) + inventory.get("uploaded", 0)),
        "ready_videos": int(inventory.get("ready", 0)),
        "queued_videos": int(inventory.get("processing", 0)),  # in-flight inventory; queue detail from API
        "processed_videos": int(inventory.get("processed", 0)),
        "failed_runs": int(run_stats["failed_runs"] or 0),
        "total_processing_runs": int(run_stats["total_runs"] or 0),
        "successful_runs": int(run_stats["successful_runs"] or 0),
        "average_processing_duration_sec": (
            float(run_stats["avg_duration_sec"])
            if run_stats["avg_duration_sec"] is not None
            else None
        ),
        "detection_records": aligned_detection_records,
        "violation_candidates": aligned_candidates,
        "class_counts": class_counts,
        "uploads_by_status_in_period": by_status,
        "trend": {
            "uploads": [{"bucket": r["bucket"], "count": int(r["n"])} for r in upload_trend],
            "processing_runs": [{"bucket": r["bucket"], "count": int(r["n"])} for r in run_trend],
        },
        "labels": {
            "unique_videos_uploaded": "Unique videos uploaded (by upload time)",
            "total_processing_runs": "Processing runs (includes reprocessing)",
        },
    }


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
                SET status = 'failed', stage = 'failed', finished_at = ?,
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
            SET status = 'failed', stage = 'failed', finished_at = ?,
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
    processing_run_id: int | None = None,
) -> int:
    with db_session() as conn:
        cursor = conn.execute(
            """
            INSERT INTO detections (
                video_id, frame_number, timestamp_sec, track_id, class_label,
                confidence, bbox_x, bbox_y, bbox_w, bbox_h, processing_run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                processing_run_id,
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
                confidence, bbox_x, bbox_y, bbox_w, bbox_h, processing_run_id
            ) VALUES (
                :video_id, :frame_number, :timestamp_sec, :track_id, :class_label,
                :confidence, :bbox_x, :bbox_y, :bbox_w, :bbox_h, :processing_run_id
            )
            """,
            [
                {
                    **row,
                    "processing_run_id": row.get("processing_run_id"),
                }
                for row in rows
            ],
        )


def get_detections_by_video(
    video_id: int,
    frame_start: int | None = None,
    frame_end: int | None = None,
    *,
    processing_run_id: int | None = None,
    current_only: bool = False,
) -> list[dict[str, Any]]:
    """Return detections for a video.

    When ``current_only`` is True, scope to the authoritative run-scoped completed
    run (including empty zero-detection runs), or legacy NULL rows when no such
    run exists. Authority is never inferred from detection counts.
    """
    params: list[Any] = [video_id]
    query = "SELECT * FROM detections WHERE video_id = ?"

    if processing_run_id is not None:
        query += " AND processing_run_id = ?"
        params.append(processing_run_id)
    elif current_only:
        mode, run_id = resolve_current_detection_scope(video_id)
        if mode == "run" and run_id is not None:
            query += " AND processing_run_id = ?"
            params.append(run_id)
        else:
            query += " AND processing_run_id IS NULL"

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
    detection_confidence: float | None = None,
    violation_confidence: float | None = None,
    evidence_sufficiency: float | None = None,
    evidence_clip_path: str | None = None,
    evidence_sequence_dir: str | None = None,
    evidence_pre_sec: float | None = None,
    evidence_post_sec: float | None = None,
    episode_start_sec: float | None = None,
    episode_end_sec: float | None = None,
    contributing_factors_json: str | None = None,
    unavailable_factors_json: str | None = None,
) -> int:
    viol_conf = (
        float(violation_confidence)
        if violation_confidence is not None
        else float(confidence)
    )
    with db_session() as conn:
        cursor = conn.execute(
            """
            INSERT INTO violations (
                video_id, track_id, violation_type, vehicle_class, confidence,
                frame_number, timestamp_sec, evidence_path, reason_log, status, reviewed_by,
                vehicle_evidence_path, plate_evidence_path, plate_text, plate_status,
                detection_confidence, violation_confidence, evidence_sufficiency,
                evidence_clip_path, evidence_sequence_dir,
                evidence_pre_sec, evidence_post_sec,
                episode_start_sec, episode_end_sec,
                contributing_factors_json, unavailable_factors_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                video_id,
                track_id,
                violation_type,
                vehicle_class,
                viol_conf,
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
                detection_confidence,
                viol_conf,
                evidence_sufficiency,
                evidence_clip_path,
                evidence_sequence_dir,
                evidence_pre_sec,
                evidence_post_sec,
                episode_start_sec,
                episode_end_sec,
                contributing_factors_json,
                unavailable_factors_json,
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
    detection_confidence: float | None = None,
    violation_confidence: float | None = None,
    evidence_sufficiency: float | None = None,
    evidence_clip_path: str | None = None,
    evidence_sequence_dir: str | None = None,
    evidence_pre_sec: float | None = None,
    evidence_post_sec: float | None = None,
    episode_start_sec: float | None = None,
    episode_end_sec: float | None = None,
    contributing_factors_json: str | None = None,
    unavailable_factors_json: str | None = None,
    processing_run_id: int | None = None,
) -> int:
    # Compatibility: legacy ``confidence`` stores violation_confidence when provided.
    viol_conf = (
        float(violation_confidence)
        if violation_confidence is not None
        else float(confidence)
    )
    det_conf = detection_confidence
    with db_session() as conn:
        cursor = conn.execute(
            """
            INSERT INTO review_queue (
                video_id, track_id, violation_type, vehicle_class, confidence,
                frame_number, timestamp_sec, evidence_path, reason_log, status,
                vehicle_evidence_path, plate_evidence_path, plate_text, plate_status,
                detection_confidence, violation_confidence, evidence_sufficiency,
                evidence_clip_path, evidence_sequence_dir,
                evidence_pre_sec, evidence_post_sec,
                episode_start_sec, episode_end_sec,
                contributing_factors_json, unavailable_factors_json,
                processing_run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                video_id,
                track_id,
                violation_type,
                vehicle_class,
                viol_conf,
                frame_number,
                timestamp_sec,
                evidence_path,
                reason_log,
                status,
                vehicle_evidence_path,
                plate_evidence_path,
                plate_text,
                plate_status,
                det_conf,
                viol_conf,
                evidence_sufficiency,
                evidence_clip_path,
                evidence_sequence_dir,
                evidence_pre_sec,
                evidence_post_sec,
                episode_start_sec,
                episode_end_sec,
                contributing_factors_json,
                unavailable_factors_json,
                processing_run_id,
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

        row_keys = row.keys()

        def _early(col):
            return row[col] if col in row_keys else None

        temporal_tagged = _early("evidence_pre_sec") is not None
        has_clip = bool(_early("evidence_clip_path") or _early("evidence_sequence_dir"))
        episode_closed = _early("episode_end_sec") is not None
        if temporal_tagged and (not has_clip or not episode_closed):
            raise TemporalEvidenceNotReady(
                "Temporal evidence is still being finalized; confirmation is blocked "
                "until the post-roll clip/sequence is written."
            )

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
                vehicle_evidence_path, plate_evidence_path, plate_text, plate_status,
                detection_confidence, violation_confidence, evidence_sufficiency,
                evidence_clip_path, evidence_sequence_dir,
                evidence_pre_sec, evidence_post_sec,
                episode_start_sec, episode_end_sec,
                contributing_factors_json, unavailable_factors_json,
                processing_run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                _pick("plate_status") or "not_attempted",
                _pick("detection_confidence"),
                _pick("violation_confidence"),
                _pick("evidence_sufficiency"),
                _pick("evidence_clip_path"),
                _pick("evidence_sequence_dir"),
                _pick("evidence_pre_sec"),
                _pick("evidence_post_sec"),
                _pick("episode_start_sec"),
                _pick("episode_end_sec"),
                _pick("contributing_factors_json"),
                _pick("unavailable_factors_json"),
                _pick("processing_run_id"),
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


def update_review_temporal_evidence(
    review_id: int,
    *,
    evidence_clip_path: str | None = None,
    evidence_sequence_dir: str | None = None,
    episode_start_sec: float | None = None,
    episode_end_sec: float | None = None,
    evidence_pre_sec: float | None = None,
    evidence_post_sec: float | None = None,
) -> None:
    """Write finalized temporal evidence paths/timing back onto a review row."""
    fields: list[str] = []
    params: list[Any] = []
    if evidence_clip_path is not None:
        fields.append("evidence_clip_path = ?")
        params.append(evidence_clip_path)
    if evidence_sequence_dir is not None:
        fields.append("evidence_sequence_dir = ?")
        params.append(evidence_sequence_dir)
    if episode_start_sec is not None:
        fields.append("episode_start_sec = ?")
        params.append(episode_start_sec)
    if episode_end_sec is not None:
        fields.append("episode_end_sec = ?")
        params.append(episode_end_sec)
    if evidence_pre_sec is not None:
        fields.append("evidence_pre_sec = ?")
        params.append(evidence_pre_sec)
    if evidence_post_sec is not None:
        fields.append("evidence_post_sec = ?")
        params.append(evidence_post_sec)
    if not fields:
        return
    params.append(review_id)
    with db_session() as conn:
        conn.execute(
            f"UPDATE review_queue SET {', '.join(fields)} WHERE id = ?",
            params,
        )
        review = conn.execute(
            "SELECT * FROM review_queue WHERE id = ?",
            (review_id,),
        ).fetchone()
        if review is None:
            return
        review_keys = review.keys()
        status = review["status"] if "status" in review_keys else None
        if status != "confirmed":
            return
        viol_fields: list[str] = []
        viol_params: list[Any] = []
        mapping = (
            ("evidence_clip_path", evidence_clip_path),
            ("evidence_sequence_dir", evidence_sequence_dir),
            ("episode_start_sec", episode_start_sec),
            ("episode_end_sec", episode_end_sec),
            ("evidence_pre_sec", evidence_pre_sec),
            ("evidence_post_sec", evidence_post_sec),
        )
        for col, value in mapping:
            if value is not None:
                viol_fields.append(f"{col} = ?")
                viol_params.append(value)
        if not viol_fields:
            return
        video_id = review["video_id"] if "video_id" in review_keys else None
        track_id = review["track_id"] if "track_id" in review_keys else None
        vtype = review["violation_type"] if "violation_type" in review_keys else None
        frame_number = review["frame_number"] if "frame_number" in review_keys else None
        viol_params.extend([video_id, track_id, vtype, frame_number])
        conn.execute(
            f"""
            UPDATE violations
            SET {', '.join(viol_fields)}
            WHERE video_id = ? AND track_id = ? AND violation_type = ? AND frame_number = ?
            """,
            viol_params,
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
