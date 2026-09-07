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
    _apply_migration_008(conn)
    _apply_migration_009(conn)

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


_MIGRATION_008_PATH = Path(__file__).parent / "migrations" / "008_legal_policy_persistence.sql"


def _apply_migration_008(conn: sqlite3.Connection) -> None:
    """Apply migration 008: legal-policy persistence tables.

    Additive only — idempotent via IF NOT EXISTS. Loads the canonical SQL
    from migrations/008_legal_policy_persistence.sql so the script and the
    fresh-schema DDL stay in sync. Existing variant tables are repaired by
    migration 009.
    """
    if _MIGRATION_008_PATH.is_file():
        try:
            conn.executescript(_MIGRATION_008_PATH.read_text(encoding="utf-8"))
        except sqlite3.OperationalError:
            # Table may already exist with different CHECK constraints from
            # schema.sql; fall through to the inline guarded DDL below.
            _apply_migration_008_inline(conn)
    else:
        _apply_migration_008_inline(conn)


def _table_create_sql(conn: sqlite3.Connection, table_name: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return (row[0] or "") if row else ""


def _case_policy_has_multi_behavior_unique(conn: sqlite3.Connection) -> bool:
    """True when case_policy_records uniqueness includes canonical_rule."""
    sql = _table_create_sql(conn, "case_policy_records").upper().replace(" ", "")
    return "UNIQUE(VIOLATION_ID,CANONICAL_RULE)" in sql


def _case_actions_violation_id_nullable(conn: sqlite3.Connection) -> bool:
    """True when case_action_events.violation_id allows NULL (policy audit)."""
    sql = _table_create_sql(conn, "case_action_events").upper().replace(" ", "")
    # NOT NULL present on violation_id means repair is required.
    return "VIOLATION_IDINTEGERNOTNULL" not in sql and "VIOLATION_IDINTEGERREFERENCES" in sql


def _apply_migration_009(conn: sqlite3.Connection) -> None:
    """Repair schema variants left by earlier migration 008 drafts.

    Preserves all existing rows. Safe to re-run.
    """
    if _table_exists(conn, "legal_behavior_mappings"):
        if not _column_exists(conn, "legal_behavior_mappings", "source_document_id"):
            conn.execute(
                "ALTER TABLE legal_behavior_mappings "
                "ADD COLUMN source_document_id TEXT"
            )

    if _table_exists(conn, "case_policy_records") and not _case_policy_has_multi_behavior_unique(
        conn
    ):
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            conn.executescript(
                """
                CREATE TABLE case_policy_records_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    violation_id INTEGER NOT NULL REFERENCES violations(id) ON DELETE CASCADE,
                    review_id INTEGER REFERENCES review_queue(id) ON DELETE SET NULL,
                    policy_version_id INTEGER NOT NULL REFERENCES legal_policy_versions(id),
                    canonical_rule TEXT NOT NULL,
                    official_category TEXT,
                    legal_status TEXT CHECK(legal_status IN (
                        'verified','partially_verified','unverified','flag_only'
                    )),
                    behavior_details_json TEXT NOT NULL DEFAULT '[]',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(violation_id, canonical_rule)
                );
                INSERT OR IGNORE INTO case_policy_records_new (
                    id, violation_id, review_id, policy_version_id, canonical_rule,
                    official_category, legal_status, behavior_details_json, created_at
                )
                SELECT id, violation_id, review_id, policy_version_id, canonical_rule,
                       official_category, legal_status, behavior_details_json, created_at
                FROM case_policy_records;
                DROP TABLE case_policy_records;
                ALTER TABLE case_policy_records_new RENAME TO case_policy_records;
                CREATE INDEX IF NOT EXISTS idx_case_policy_violation
                    ON case_policy_records(violation_id);
                CREATE INDEX IF NOT EXISTS idx_case_policy_review
                    ON case_policy_records(review_id);
                CREATE INDEX IF NOT EXISTS idx_case_policy_version
                    ON case_policy_records(policy_version_id);
                """
            )
        finally:
            conn.execute("PRAGMA foreign_keys = ON")

    if _table_exists(conn, "case_action_events") and not _case_actions_violation_id_nullable(
        conn
    ):
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            conn.executescript(
                """
                CREATE TABLE case_action_events_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    violation_id INTEGER REFERENCES violations(id) ON DELETE CASCADE,
                    review_id INTEGER REFERENCES review_queue(id) ON DELETE SET NULL,
                    action_type TEXT NOT NULL CHECK(action_type IN (
                        'review_confirmed','case_confirmed','notice_printed',
                        'notice_printer_attested','plate_verified','policy_proposed',
                        'policy_approved','policy_rejected','recurrence_evaluated',
                        'event_time_confirmed'
                    )),
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    actor_user_id INTEGER REFERENCES users(id),
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                INSERT INTO case_action_events_new (
                    id, violation_id, review_id, action_type, detail_json,
                    actor_user_id, created_at
                )
                SELECT id,
                       CASE WHEN violation_id = 0 THEN NULL ELSE violation_id END,
                       review_id, action_type, detail_json, actor_user_id, created_at
                FROM case_action_events;
                DROP TABLE case_action_events;
                ALTER TABLE case_action_events_new RENAME TO case_action_events;
                CREATE INDEX IF NOT EXISTS idx_case_actions_violation
                    ON case_action_events(violation_id);
                CREATE INDEX IF NOT EXISTS idx_case_actions_actor
                    ON case_action_events(actor_user_id);
                CREATE INDEX IF NOT EXISTS idx_case_actions_created_at
                    ON case_action_events(created_at);
                """
            )
        finally:
            conn.execute("PRAGMA foreign_keys = ON")


def _apply_migration_008_inline(conn: sqlite3.Connection) -> None:
    """Guarded inline DDL fallback for migration 008 (canonical shape)."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS legal_policy_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version TEXT NOT NULL,
            created_by INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            status TEXT CHECK(status IN ('proposed','approved','rejected'))
                NOT NULL DEFAULT 'proposed',
            lookback_days INTEGER NOT NULL DEFAULT 365,
            schedule_json TEXT NOT NULL DEFAULT '{}',
            detail_json TEXT NOT NULL DEFAULT '{}',
            approved_by INTEGER,
            approved_at DATETIME,
            rejected_by INTEGER,
            rejected_at DATETIME,
            UNIQUE(version)
        );
        CREATE INDEX IF NOT EXISTS idx_legal_policy_versions_status
            ON legal_policy_versions(status);
        CREATE INDEX IF NOT EXISTS idx_legal_policy_versions_created_at
            ON legal_policy_versions(created_at);

        CREATE TABLE IF NOT EXISTS legal_behavior_mappings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            policy_version_id INTEGER NOT NULL
                REFERENCES legal_policy_versions(id) ON DELETE CASCADE,
            canonical_rule TEXT NOT NULL,
            official_category TEXT,
            legal_status TEXT CHECK(legal_status IN (
                'verified','partially_verified','unverified','flag_only'
            )) NOT NULL DEFAULT 'unverified',
            verified_elements_json TEXT NOT NULL DEFAULT '[]',
            unresolved_elements_json TEXT NOT NULL DEFAULT '[]',
            behavior_details_json TEXT NOT NULL DEFAULT '[]',
            provision_reference TEXT,
            penalty_schedule_json TEXT,
            source_url TEXT,
            source_document_id TEXT,
            mapping_version TEXT,
            is_grouped_with_json TEXT NOT NULL DEFAULT '[]',
            notes TEXT,
            UNIQUE(policy_version_id, canonical_rule)
        );
        CREATE INDEX IF NOT EXISTS idx_legal_mappings_version
            ON legal_behavior_mappings(policy_version_id);
        CREATE INDEX IF NOT EXISTS idx_legal_mappings_rule
            ON legal_behavior_mappings(canonical_rule);

        CREATE TABLE IF NOT EXISTS case_policy_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            violation_id INTEGER NOT NULL REFERENCES violations(id) ON DELETE CASCADE,
            review_id INTEGER REFERENCES review_queue(id) ON DELETE SET NULL,
            policy_version_id INTEGER NOT NULL REFERENCES legal_policy_versions(id),
            canonical_rule TEXT NOT NULL,
            official_category TEXT,
            legal_status TEXT CHECK(legal_status IN (
                'verified','partially_verified','unverified','flag_only'
            )),
            behavior_details_json TEXT NOT NULL DEFAULT '[]',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(violation_id, canonical_rule)
        );
        CREATE INDEX IF NOT EXISTS idx_case_policy_violation
            ON case_policy_records(violation_id);
        CREATE INDEX IF NOT EXISTS idx_case_policy_review
            ON case_policy_records(review_id);
        CREATE INDEX IF NOT EXISTS idx_case_policy_version
            ON case_policy_records(policy_version_id);

        CREATE TABLE IF NOT EXISTS plate_verifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            violation_id INTEGER NOT NULL REFERENCES violations(id) ON DELETE CASCADE,
            review_id INTEGER REFERENCES review_queue(id) ON DELETE SET NULL,
            ocr_raw TEXT,
            accepted_plate_text TEXT,
            plate_status TEXT CHECK(plate_status IN (
                'not_attempted','processing_failed','unclear','not_visible',
                'candidate_awaiting_verification','verified_readable',
                'migrated_unverified'
            )) NOT NULL DEFAULT 'not_attempted',
            alpr_model TEXT,
            alpr_version TEXT,
            ocr_confidence REAL,
            processing_diagnostics_json TEXT NOT NULL DEFAULT '{}',
            verified_by INTEGER REFERENCES users(id),
            verified_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(violation_id)
        );
        CREATE INDEX IF NOT EXISTS idx_plate_verifications_violation
            ON plate_verifications(violation_id);
        CREATE INDEX IF NOT EXISTS idx_plate_verifications_status
            ON plate_verifications(plate_status);

        CREATE TABLE IF NOT EXISTS case_action_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            violation_id INTEGER REFERENCES violations(id) ON DELETE CASCADE,
            review_id INTEGER REFERENCES review_queue(id) ON DELETE SET NULL,
            action_type TEXT NOT NULL CHECK(action_type IN (
                'review_confirmed','case_confirmed','notice_printed',
                'notice_printer_attested','plate_verified','policy_proposed',
                'policy_approved','policy_rejected','recurrence_evaluated',
                'event_time_confirmed'
            )),
            detail_json TEXT NOT NULL DEFAULT '{}',
            actor_user_id INTEGER REFERENCES users(id),
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_case_actions_violation
            ON case_action_events(violation_id);
        CREATE INDEX IF NOT EXISTS idx_case_actions_actor
            ON case_action_events(actor_user_id);
        CREATE INDEX IF NOT EXISTS idx_case_actions_created_at
            ON case_action_events(created_at);

        CREATE TABLE IF NOT EXISTS recurrence_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            violation_id INTEGER NOT NULL REFERENCES violations(id) ON DELETE CASCADE,
            policy_version_id INTEGER NOT NULL REFERENCES legal_policy_versions(id),
            lookback_days INTEGER NOT NULL DEFAULT 365,
            matched_violation_ids_json TEXT NOT NULL DEFAULT '[]',
            eligible_match_ids_json TEXT NOT NULL DEFAULT '[]',
            suggested_recurrence_count INTEGER NOT NULL DEFAULT 0,
            evaluation_time DATETIME DEFAULT CURRENT_TIMESTAMP,
            evaluated_by INTEGER REFERENCES users(id),
            detail_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE(violation_id, policy_version_id)
        );
        CREATE INDEX IF NOT EXISTS idx_recurrence_reviews_violation
            ON recurrence_reviews(violation_id);
        CREATE INDEX IF NOT EXISTS idx_recurrence_reviews_version
            ON recurrence_reviews(policy_version_id);

        CREATE TABLE IF NOT EXISTS policy_permission_assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            permission TEXT NOT NULL CHECK(permission IN (
                'confirm_case','attest_print','propose_policy','approve_policy',
                'verify_plate','confirm_event_time'
            )),
            granted_by INTEGER REFERENCES users(id),
            granted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            revoked_at DATETIME,
            reason TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_permission_unique_active
            ON policy_permission_assignments(user_id, permission)
            WHERE revoked_at IS NULL;
        CREATE INDEX IF NOT EXISTS idx_permission_user
            ON policy_permission_assignments(user_id);
        CREATE INDEX IF NOT EXISTS idx_permission_perm
            ON policy_permission_assignments(permission);
        """
    )


def init_db(force: bool = False) -> None:
    with db_session() as conn:
        if force:
            conn.executescript(
                """
                DROP TABLE IF EXISTS case_action_events;
                DROP TABLE IF EXISTS recurrence_reviews;
                DROP TABLE IF EXISTS plate_verifications;
                DROP TABLE IF EXISTS case_policy_records;
                DROP TABLE IF EXISTS legal_behavior_mappings;
                DROP TABLE IF EXISTS legal_policy_versions;
                DROP TABLE IF EXISTS policy_permission_assignments;
                DROP TABLE IF EXISTS system_audit_events;
                DROP TABLE IF EXISTS video_history_events;
                DROP TABLE IF EXISTS review_queue;
                DROP TABLE IF EXISTS violations;
                DROP TABLE IF EXISTS detections;
                DROP TABLE IF EXISTS annotations;
                DROP TABLE IF EXISTS reports;
                DROP TABLE IF EXISTS processing_runs;
                DROP TABLE IF EXISTS videos;
                DROP TABLE IF EXISTS zone_templates;
                DROP TABLE IF EXISTS system_settings;
                DROP TABLE IF EXISTS cameras;
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
        # Prefer recorded timestamp_sec; do not invent identity timing from FPS.
        if "timestamp_sec" in row_keys and row["timestamp_sec"] is not None:
            timestamp_sec = row["timestamp_sec"]
        else:
            timestamp_sec = None

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

        # Atomic review→case link inside the same transaction.
        if _table_exists(conn, "case_action_events"):
            conn.execute(
                """
                INSERT INTO case_action_events
                    (violation_id, review_id, action_type, detail_json, actor_user_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    violation_id,
                    review_id,
                    ACTION_REVIEW_CONFIRMED,
                    json.dumps({"link": "review_to_violation"}),
                    reviewed_by,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )

        return violation_id


def create_case_with_materialization_intent(
    primary_review_id: int,
    reviewed_by: int,
    *,
    policy_version_id: int,
    contributing_rules: list[str] | tuple[str, ...],
    review_ids: list[int] | tuple[int, ...],
    additional_link_review_ids: list[int] | tuple[int, ...] | None = None,
    _fail_after: str | None = None,
) -> int:
    """Atomically create a case, confirm/link initial reviews, and record intent.

    One SQLite transaction covers:
      - violation insert from the primary pending review
      - primary review confirmation + review→case link
      - optional additional pending review confirmations/links
      - materialization_intent audit carrying the selected policy version

    If intent insertion fails (or ``_fail_after='intent'``), the whole boundary
    rolls back. Later evidence/snapshot completion remains a staged recoverable
    path outside this function.

    ``_fail_after`` is a test-only fault-injection seam; production callers omit it.
    """
    extra_ids = [int(r) for r in (additional_link_review_ids or ()) if int(r) != int(primary_review_id)]
    now_link = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    reviewed_at = datetime.now().isoformat(sep=" ", timespec="seconds")

    with db_session() as conn:
        row = conn.execute(
            "SELECT * FROM review_queue WHERE id = ?",
            (primary_review_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Review item {primary_review_id} not found")

        row_status = row["status"] if "status" in row.keys() else None
        if row_status != "pending":
            raise ValueError(
                f"Review item {primary_review_id} is not pending (status: {row_status})"
            )

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

        if "timestamp_sec" in row_keys and row["timestamp_sec"] is not None:
            timestamp_sec = row["timestamp_sec"]
        else:
            timestamp_sec = None

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
        violation_id = int(cursor.lastrowid)

        conn.execute(
            """
            UPDATE review_queue
            SET status = 'confirmed', reviewed_by = ?, reviewed_at = ?
            WHERE id = ?
            """,
            (reviewed_by, reviewed_at, primary_review_id),
        )

        if _table_exists(conn, "case_action_events"):
            conn.execute(
                """
                INSERT INTO case_action_events
                    (violation_id, review_id, action_type, detail_json, actor_user_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    violation_id,
                    primary_review_id,
                    ACTION_REVIEW_CONFIRMED,
                    json.dumps({"link": "review_to_violation"}),
                    reviewed_by,
                    now_link,
                ),
            )

            for extra_id in extra_ids:
                extra = conn.execute(
                    "SELECT status FROM review_queue WHERE id = ?",
                    (extra_id,),
                ).fetchone()
                if extra is None:
                    raise ValueError(f"Review item {extra_id} not found")
                if extra["status"] == "pending":
                    conn.execute(
                        """
                        UPDATE review_queue
                        SET status = 'confirmed', reviewed_by = ?, reviewed_at = ?
                        WHERE id = ?
                        """,
                        (reviewed_by, reviewed_at, extra_id),
                    )
                existing_link = conn.execute(
                    """
                    SELECT id FROM case_action_events
                    WHERE violation_id = ? AND review_id = ? AND action_type = ?
                    LIMIT 1
                    """,
                    (violation_id, extra_id, ACTION_REVIEW_CONFIRMED),
                ).fetchone()
                if existing_link is None:
                    conn.execute(
                        """
                        INSERT INTO case_action_events
                            (violation_id, review_id, action_type, detail_json,
                             actor_user_id, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            violation_id,
                            extra_id,
                            ACTION_REVIEW_CONFIRMED,
                            json.dumps({"link": "review_to_violation"}),
                            reviewed_by,
                            now_link,
                        ),
                    )

            if _fail_after == "intent":
                raise RuntimeError("intent boom")

            # Durable selected-policy intent — same transaction as case creation.
            actor_user_id = reviewed_by
            user_row = conn.execute(
                "SELECT id FROM users WHERE id = ?", (actor_user_id,)
            ).fetchone()
            if user_row is None:
                actor_user_id = None
            conn.execute(
                """
                INSERT INTO case_action_events
                    (violation_id, review_id, action_type, detail_json,
                     actor_user_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    violation_id,
                    None,
                    ACTION_REVIEW_CONFIRMED,
                    json.dumps(
                        {
                            "materialization_intent": True,
                            "policy_version_id": int(policy_version_id),
                            "contributing_rules": list(contributing_rules),
                            "review_ids": [int(r) for r in review_ids],
                        }
                    ),
                    actor_user_id,
                    now_link,
                ),
            )
        else:
            if _fail_after == "intent":
                raise RuntimeError("intent boom")
            raise ValueError("case_action_events table does not exist")

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


# ---------------------------------------------------------------------------
# Stage B: Legal-policy persistence, case linkage, audit, permissions
# ---------------------------------------------------------------------------

# Action-type constants for case_action_events
ACTION_REVIEW_CONFIRMED = "review_confirmed"
ACTION_CASE_CONFIRMED = "case_confirmed"
ACTION_NOTICE_PRINTED = "notice_printed"
ACTION_NOTICE_PRINTER_ATTESTED = "notice_printer_attested"
ACTION_PLATE_VERIFIED = "plate_verified"
ACTION_POLICY_PROPOSED = "policy_proposed"
ACTION_POLICY_APPROVED = "policy_approved"
ACTION_POLICY_REJECTED = "policy_rejected"
ACTION_RECURRENCE_EVALUATED = "recurrence_evaluated"
ACTION_EVENT_TIME_CONFIRMED = "event_time_confirmed"

# Permission constants
PERM_CONFIRM_CASE = "confirm_case"
PERM_ATTEST_PRINT = "attest_print"
PERM_PROPOSE_POLICY = "propose_policy"
PERM_APPROVE_POLICY = "approve_policy"
PERM_VERIFY_PLATE = "verify_plate"
PERM_CONFIRM_EVENT_TIME = "confirm_event_time"

# Roles that may perform case-review actions by role: confirm/materialize,
# plate verification, event-time confirmation, and notice-print attestation.
# Active System Administrators are included for case review. Legal-policy
# approval is never role-aliased — only explicit approve_policy grants.
ENFORCEMENT_ROLES = ("enforcer", "admin")
# Policy approval is never role-aliased; only explicit approve_policy grants.
SUPERVISOR_ROLES: tuple[str, ...] = ()


def get_active_legal_policy_version() -> dict[str, Any] | None:
    """Return the single active (approved) legal policy version, or None."""
    with db_session() as conn:
        if not _table_exists(conn, "legal_policy_versions"):
            return None
        row = conn.execute(
            """
            SELECT * FROM legal_policy_versions
            WHERE status = 'approved'
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
        return _row_to_dict(row)


def get_legal_policy_version(version_id: int) -> dict[str, Any] | None:
    with db_session() as conn:
        if not _table_exists(conn, "legal_policy_versions"):
            return None
        row = conn.execute(
            "SELECT * FROM legal_policy_versions WHERE id = ?", (version_id,)
        ).fetchone()
        return _row_to_dict(row)


def propose_legal_policy_version(
    version: str,
    created_by: int,
    *,
    lookback_days: int = 365,
    schedule_json: str | dict = "{}",
    detail_json: str | dict = "{}",
) -> int:
    """Create a proposed (not-yet-approved) legal policy version.

    The proposed record does NOT overwrite the active approved policy.
    An explicit approval step is required before activation. Requires an
    active proposer with propose authority (admin role or explicit grant).
    """
    if not can_propose_policy(created_by):
        raise PermissionError(
            f"User {created_by} lacks propose_policy authority"
        )
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(schedule_json, dict):
        schedule_json = json.dumps(schedule_json)
    if isinstance(detail_json, dict):
        detail_json = json.dumps(detail_json)
    with db_session() as conn:
        if not _table_exists(conn, "legal_policy_versions"):
            raise ValueError("legal_policy_versions table does not exist")
        # Validate created_by exists (FK enforced by schema, but guard for clarity).
        if created_by is not None:
            user_row = conn.execute(
                "SELECT id FROM users WHERE id = ?", (created_by,)
            ).fetchone()
            if user_row is None:
                raise ValueError(f"User {created_by} does not exist")
        cursor = conn.execute(
            """
            INSERT INTO legal_policy_versions
                (version, created_by, created_at, status, lookback_days,
                 schedule_json, detail_json)
            VALUES (?, ?, ?, 'proposed', ?, ?, ?)
            """,
            (version, created_by, now, lookback_days, schedule_json, detail_json),
        )
        pid = cursor.lastrowid
        # Record the proposal action in the audit trail (policy-level: no case).
        conn.execute(
            """
            INSERT INTO case_action_events
                (violation_id, action_type, detail_json, actor_user_id, created_at)
            VALUES (NULL, ?, ?, ?, ?)
            """,
            (
                ACTION_POLICY_PROPOSED,
                json.dumps({"version": version, "policy_version_id": pid}),
                created_by,
                now,
            ),
        )
        return int(pid)


def approve_legal_policy_version(version_id: int, approved_by: int) -> None:
    """Approve a proposed policy version.

    Requires an explicit ``approve_policy`` capability (CTEU Head/Supervisor
    grant). Administrator status alone does not authorize approval. The check
    runs inside this operation before any status or audit write.
    """
    if not can_approve_policy(approved_by):
        raise PermissionError(
            f"User {approved_by} lacks approve_policy authority"
        )
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db_session() as conn:
        if not _table_exists(conn, "legal_policy_versions"):
            raise ValueError("legal_policy_versions table does not exist")
        user_row = conn.execute(
            "SELECT id FROM users WHERE id = ?", (approved_by,)
        ).fetchone()
        if user_row is None:
            raise ValueError(f"User {approved_by} does not exist")
        row = conn.execute(
            "SELECT status FROM legal_policy_versions WHERE id = ?", (version_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Policy version {version_id} not found")
        if row["status"] == "approved":
            raise ValueError(f"Policy version {version_id} is already approved")
        if row["status"] == "rejected":
            raise ValueError(f"Policy version {version_id} was rejected")
        conn.execute(
            """
            UPDATE legal_policy_versions
            SET status = 'approved', approved_by = ?, approved_at = ?
            WHERE id = ?
            """,
            (approved_by, now, version_id),
        )
        conn.execute(
            """
            INSERT INTO case_action_events
                (violation_id, action_type, detail_json, actor_user_id, created_at)
            VALUES (NULL, ?, ?, ?, ?)
            """,
            (
                ACTION_POLICY_APPROVED,
                json.dumps({"version_id": version_id, "approved_by": approved_by}),
                approved_by,
                now,
            ),
        )


def create_case_policy_record(
    violation_id: int,
    policy_version_id: int,
    canonical_rule: str,
    *,
    official_category: str | None = None,
    legal_status: str | None = None,
    behavior_details: list[str] | None = None,
) -> int:
    """Record the legal-policy snapshot applied at review time for a case.

    This preserves the policy context at the moment of confirmation so later
    policy changes do not rewrite the case's historical mapping.
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db_session() as conn:
        if not _table_exists(conn, "case_policy_records"):
            raise ValueError("case_policy_records table does not exist")
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO case_policy_records
                (violation_id, review_id, policy_version_id, canonical_rule,
                 official_category, legal_status, behavior_details_json, created_at)
            VALUES (?, NULL, ?, ?, ?, ?, ?, ?)
            """,
            (
                violation_id,
                policy_version_id,
                canonical_rule,
                official_category,
                legal_status,
                json.dumps(behavior_details or []),
                now,
            ),
        )
        return int(cursor.lastrowid) if cursor.lastrowid else 0


def record_case_action(
    violation_id: int,
    action_type: str,
    *,
    detail: dict[str, Any] | None = None,
    actor_user_id: int | None = None,
    review_id: int | None = None,
) -> int:
    """Insert a durable audit event for a case action.

    Used for: case confirmation, print attestation, plate verification,
    event-time confirmation, recurrence evaluation.
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db_session() as conn:
        if not _table_exists(conn, "case_action_events"):
            raise ValueError("case_action_events table does not exist")
        # Validate actor exists if provided.
        if actor_user_id is not None:
            user_row = conn.execute(
                "SELECT id FROM users WHERE id = ?", (actor_user_id,)
            ).fetchone()
            if user_row is None:
                actor_user_id = None
        cursor = conn.execute(
            """
            INSERT INTO case_action_events
                (violation_id, review_id, action_type, detail_json,
                 actor_user_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                violation_id,
                review_id,
                action_type,
                json.dumps(detail or {}),
                actor_user_id,
                now,
            ),
        )
        return int(cursor.lastrowid)


def get_case_actions(violation_id: int) -> list[dict[str, Any]]:
    """Return all audit events for a violation, ordered by time."""
    with db_session() as conn:
        if not _table_exists(conn, "case_action_events"):
            return []
        rows = conn.execute(
            """
            SELECT * FROM case_action_events
            WHERE violation_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (violation_id,),
        ).fetchall()
        return [_row_to_dict(row) for row in rows]


def user_has_permission(user_id: int, permission: str) -> bool:
    """Check if a user has an explicitly granted (non-revoked) permission."""
    with db_session() as conn:
        if not _table_exists(conn, "policy_permission_assignments"):
            return False
        row = conn.execute(
            """
            SELECT 1 FROM policy_permission_assignments
            WHERE user_id = ? AND permission = ? AND revoked_at IS NULL
            LIMIT 1
            """,
            (user_id, permission),
        ).fetchone()
        return row is not None


def assign_policy_permission(
    user_id: int,
    permission: str,
    granted_by: int,
    *,
    reason: str | None = None,
) -> int:
    """Grant an explicit policy permission to a user.

    Requires the granter to have the corresponding granting authority.
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db_session() as conn:
        if not _table_exists(conn, "policy_permission_assignments"):
            raise ValueError("policy_permission_assignments table does not exist")
        # Validate both users exist.
        for uid in (user_id, granted_by):
            user_row = conn.execute(
                "SELECT id FROM users WHERE id = ?", (uid,)
            ).fetchone()
            if user_row is None:
                raise ValueError(f"User {uid} does not exist")
        cursor = conn.execute(
            """
            INSERT INTO policy_permission_assignments
                (user_id, permission, granted_by, granted_at, reason)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, permission, granted_by, now, reason),
        )
        return int(cursor.lastrowid)


def revoke_policy_permission(user_id: int, permission: str) -> None:
    """Revoke a previously granted permission (soft delete)."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db_session() as conn:
        if not _table_exists(conn, "policy_permission_assignments"):
            raise ValueError("policy_permission_assignments table does not exist")
        conn.execute(
            """
            UPDATE policy_permission_assignments
            SET revoked_at = ?
            WHERE user_id = ? AND permission = ? AND revoked_at IS NULL
            """,
            (now, user_id, permission),
        )


def get_user_permissions(user_id: int) -> list[str]:
    """Return all active (non-revoked) permission names for a user."""
    with db_session() as conn:
        if not _table_exists(conn, "policy_permission_assignments"):
            return []
        rows = conn.execute(
            """
            SELECT permission FROM policy_permission_assignments
            WHERE user_id = ? AND revoked_at IS NULL
            """,
            (user_id,),
        ).fetchall()
        return [row["permission"] for row in rows]


def _user_account_is_active(user: dict[str, Any]) -> bool:
    """True when the account exists and is marked active.

    Matches authentication: missing ``is_active`` defaults to active (legacy
    rows). Explicit grants never override inactivity — callers must check
    activity before role or capability evaluation.
    """
    return bool(user.get("is_active", 1))


def can_confirm_case(user_id: int) -> bool:
    """True when an active enforcer/admin or explicit confirm_case grant."""
    user = get_user(user_id)
    if user is None or not _user_account_is_active(user):
        return False
    if user.get("role") in ENFORCEMENT_ROLES:
        return True
    return user_has_permission(user_id, PERM_CONFIRM_CASE)


def can_attest_print(user_id: int) -> bool:
    """True when an active enforcer/admin or explicit attest_print grant.

    Inactive accounts are rejected even if explicit grants remain stored.
    Administrator role alone still does not authorize legal-policy approval.
    """
    user = get_user(user_id)
    if user is None or not _user_account_is_active(user):
        return False
    if user.get("role") in ENFORCEMENT_ROLES:
        return True
    return user_has_permission(user_id, PERM_ATTEST_PRINT)


def can_approve_policy(user_id: int) -> bool:
    """True only for an active user with explicit approve_policy grant.

    Administrator status alone never confers supervisory approval authority.
    Stored grants do not authorize inactive accounts.
    """
    user = get_user(user_id)
    if user is None or not _user_account_is_active(user):
        return False
    return user_has_permission(user_id, PERM_APPROVE_POLICY)


def can_propose_policy(user_id: int) -> bool:
    """True when an active user can propose policy (admin or explicit grant)."""
    user = get_user(user_id)
    if user is None or not _user_account_is_active(user):
        return False
    if user.get("role") == "admin":
        return True
    return user_has_permission(user_id, PERM_PROPOSE_POLICY)


# ---------------------------------------------------------------------------
# Case-level confirmations (distinct from review-queue confirmation)
# ---------------------------------------------------------------------------

def reject_legal_policy_version(version_id: int, rejected_by: int,
                                *, reason: str | None = None) -> None:
    """Reject a proposed policy version without activating it."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db_session() as conn:
        if not _table_exists(conn, "legal_policy_versions"):
            raise ValueError("legal_policy_versions table does not exist")
        user_row = conn.execute(
            "SELECT id FROM users WHERE id = ?", (rejected_by,)
        ).fetchone()
        if user_row is None:
            raise ValueError(f"User {rejected_by} does not exist")
        row = conn.execute(
            "SELECT status FROM legal_policy_versions WHERE id = ?", (version_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Policy version {version_id} not found")
        if row["status"] == "approved":
            raise ValueError(
                f"Policy version {version_id} is already approved; cannot reject"
            )
        conn.execute(
            """
            UPDATE legal_policy_versions
            SET status = 'rejected',
                rejected_by = ?,
                rejected_at = ?
            WHERE id = ?
            """,
            (rejected_by, now, version_id),
        )
        conn.execute(
            """
            INSERT INTO case_action_events
                (violation_id, action_type, detail_json, actor_user_id, created_at)
            VALUES (NULL, ?, ?, ?, ?)
            """,
            (
                ACTION_POLICY_REJECTED,
                json.dumps({"version_id": version_id, "reason": reason}),
                rejected_by,
                now,
            ),
        )


def link_review_to_case(review_id: int, violation_id: int) -> None:
    """Record the stable case identity linking a review queue item to its
    resulting violation. This preserves provenance across confirmation,
    reporting, and recurrence.
    """
    with db_session() as conn:
        if not _table_exists(conn, "case_action_events"):
            return
        existing = conn.execute(
            """
            SELECT id FROM case_action_events
            WHERE violation_id = ? AND review_id = ? AND action_type = ?
            LIMIT 1
            """,
            (violation_id, review_id, ACTION_REVIEW_CONFIRMED),
        ).fetchone()
        if existing is not None:
            return
        conn.execute(
            """
            INSERT INTO case_action_events
                (violation_id, review_id, action_type, detail_json, actor_user_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                violation_id,
                review_id,
                ACTION_REVIEW_CONFIRMED,
                json.dumps({"link": "review_to_violation"}),
                None,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )


def confirm_case(violation_id: int, officer_id: int) -> bool:
    """Mark a violation as case-confirmed by an authorized officer.

    Requires an active enforcer (or explicit confirm_case grant). A
    legacy/default ``violations.status = 'confirmed'`` value alone is not
    sufficient and is not written by this function. Dismissed cases are
    rejected before any idempotent success path. Idempotent: repeated calls
    on a non-dismissed case do not insert duplicate case_confirmed rows.

    Returns True if the case is (already or newly) case-confirmed.
    """
    if not can_confirm_case(officer_id):
        raise PermissionError(
            f"User {officer_id} lacks confirm_case authority"
        )
    now = datetime.now().isoformat(sep=" ", timespec="seconds")
    with db_session() as conn:
        if not _table_exists(conn, "violations"):
            raise ValueError("violations table does not exist")
        row = conn.execute(
            "SELECT status FROM violations WHERE id = ?", (violation_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Violation {violation_id} not found")
        # Current-status gate runs before idempotent "already confirmed" return.
        if row["status"] == "dismissed":
            raise ValueError(
                f"Violation {violation_id} is dismissed; cannot confirm case"
            )
        existing = conn.execute(
            """
            SELECT id FROM case_action_events
            WHERE violation_id = ? AND action_type = ?
            LIMIT 1
            """,
            (violation_id, ACTION_CASE_CONFIRMED),
        ).fetchone()
        if existing is not None:
            return True
        # Case confirmation is separate from the existing 'status' lifecycle.
        conn.execute(
            """
            INSERT INTO case_action_events
                (violation_id, action_type, detail_json, actor_user_id, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                violation_id,
                ACTION_CASE_CONFIRMED,
                json.dumps({"case_confirmed_at": now}),
                officer_id,
                now,
            ),
        )
    return True


def _attest_notice_print_action(
    violation_id: int,
    printer_id: int,
    action_type: str,
    *,
    document_reference: str | None = None,
) -> bool:
    """Shared checked path for notice-print attestation actions.

    Requires:
      1. Current case status is not dismissed (checked before idempotent return).
      2. An authenticated officer ``case_confirmed`` audit event (not merely
         a legacy violations.status value).
      3. Active enforcer role or explicit attest_print grant for the printer.
    Records actor identity and timestamp. Retries are idempotent for the same
    action_type on non-dismissed cases; deliberate reprint is not modeled.
    """
    if action_type not in (ACTION_NOTICE_PRINTED, ACTION_NOTICE_PRINTER_ATTESTED):
        raise ValueError(f"Unsupported print action type: {action_type}")
    if not can_attest_print(printer_id):
        raise PermissionError(
            f"User {printer_id} lacks attest_print authority"
        )
    now = datetime.now().isoformat(sep=" ", timespec="seconds")
    with db_session() as conn:
        if not _table_exists(conn, "case_action_events"):
            raise ValueError("case_action_events table does not exist")
        viol = conn.execute(
            "SELECT id, status FROM violations WHERE id = ?", (violation_id,)
        ).fetchone()
        if viol is None:
            raise ValueError(f"Violation {violation_id} not found")
        # Current-status gate runs before idempotent "already printed" return.
        if viol["status"] == "dismissed":
            raise ValueError(
                f"Violation {violation_id} is dismissed; cannot attest notice printing"
            )
        confirmed = conn.execute(
            """
            SELECT id FROM case_action_events
            WHERE violation_id = ? AND action_type = ?
            LIMIT 1
            """,
            (violation_id, ACTION_CASE_CONFIRMED),
        ).fetchone()
        if confirmed is None:
            raise ValueError(
                "Case must be confirmed before notice printing can be attested. "
                "Use confirm_case() first."
            )
        existing = conn.execute(
            """
            SELECT id FROM case_action_events
            WHERE violation_id = ? AND action_type = ?
            LIMIT 1
            """,
            (violation_id, action_type),
        ).fetchone()
        if existing is not None:
            return True
        detail: dict[str, Any] = {"notice_printed_at": now}
        if document_reference:
            detail["document_reference"] = document_reference
        conn.execute(
            """
            INSERT INTO case_action_events
                (violation_id, action_type, detail_json, actor_user_id, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                violation_id,
                action_type,
                json.dumps(detail),
                printer_id,
                now,
            ),
        )
    return True


def confirm_notice_printed(
    violation_id: int,
    printer_id: int,
    *,
    document_reference: str | None = None,
) -> bool:
    """Record that an authorized officer attested to notice printing.

    Requires the printer to have the attest_print capability. This is a
    separate action from case confirmation — the same officer may perform
    both, or different officers may act.

    A case becomes Notice Printed only when BOTH:
      1. An authorized officer has confirmed the case (case_confirmed event).
      2. An authorized officer explicitly confirms printing (this call).

    Preview, PDF generation, download, and browser print dialog do NOT
    constitute Notice Printed — only this explicit attestation does.
    """
    return _attest_notice_print_action(
        violation_id,
        printer_id,
        ACTION_NOTICE_PRINTED,
        document_reference=document_reference,
    )


def confirm_notice_printer_attested(
    violation_id: int,
    printer_id: int,
    *,
    document_reference: str | None = None,
) -> bool:
    """Record the printing confirmer's attestation separately.

    The printing confirmer need not be the case confirmer. Uses the same
    prerequisite checks as confirm_notice_printed so this path cannot bypass
    case confirmation.
    """
    return _attest_notice_print_action(
        violation_id,
        printer_id,
        ACTION_NOTICE_PRINTER_ATTESTED,
        document_reference=document_reference,
    )


def record_plate_verification(
    violation_id: int,
    *,
    review_id: int | None = None,
    processing_run_id: int | None = None,
    ocr_raw: str | None = None,
    accepted_plate_text: str | None = None,
    plate_status: str,
    alpr_model: str | None = None,
    alpr_version: str | None = None,
    ocr_confidence: float | None = None,
    processing_diagnostics: dict[str, Any] | None = None,
    verified_by: int | None = None,
    verified_at: str | None = None,
    evidence_crop_ref: str | None = None,
    clear_accepted_identity: bool = False,
) -> int:
    """Record a plate verification event (non-atomic alone — prefer apply_*).

    If a plate_verifications row already exists for this violation_id, it is
    updated. Historical accepted values are preserved via case_action_events
    audits written by ``apply_plate_verification``, not by ``created_at``.
    """
    return apply_plate_verification(
        violation_id,
        review_id=review_id,
        processing_run_id=processing_run_id,
        ocr_raw=ocr_raw,
        accepted_plate_text=accepted_plate_text,
        clear_accepted_identity=clear_accepted_identity,
        plate_status=plate_status,
        alpr_model=alpr_model,
        alpr_version=alpr_version,
        ocr_confidence=ocr_confidence,
        processing_diagnostics=processing_diagnostics,
        verified_by=verified_by,
        verified_at=verified_at,
        evidence_crop_ref=evidence_crop_ref,
        legacy_plate_text=None,
        legacy_plate_status=None,
        audit_detail=None,
        actor_user_id=verified_by,
        write_audit=False,
        write_legacy=False,
    )


def apply_plate_verification(
    violation_id: int,
    *,
    review_id: int | None = None,
    processing_run_id: int | None = None,
    ocr_raw: str | None = None,
    accepted_plate_text: str | None = None,
    clear_accepted_identity: bool = False,
    plate_status: str,
    alpr_model: str | None = None,
    alpr_version: str | None = None,
    ocr_confidence: float | None = None,
    processing_diagnostics: dict[str, Any] | None = None,
    verified_by: int | None = None,
    verified_at: str | None = None,
    evidence_crop_ref: str | None = None,
    legacy_plate_text: str | None = None,
    legacy_plate_status: str | None = None,
    audit_detail: dict[str, Any] | None = None,
    actor_user_id: int | None = None,
    write_audit: bool = True,
    write_legacy: bool = True,
    _fail_after: str | None = None,
) -> int:
    """Atomically apply plate verification, audit, and legacy field sync.

    One SQLite transaction: plate_verifications + case_action_events +
    violations.plate_* either all commit or all roll back.

    ``_fail_after`` is a test-only fault-injection seam
    (``verification`` / ``audit`` / ``legacy``); production callers omit it.
    """
    now = datetime.now().isoformat(sep=" ", timespec="seconds")
    diagnostics = dict(processing_diagnostics or {})
    if processing_run_id is not None:
        diagnostics.setdefault("processing_run_id", processing_run_id)
    if evidence_crop_ref is not None:
        diagnostics.setdefault("evidence_crop_ref", evidence_crop_ref)
    diagnostics_json = json.dumps(diagnostics)
    with db_session() as conn:
        if not _table_exists(conn, "plate_verifications"):
            raise ValueError("plate_verifications table does not exist")
        existing = conn.execute(
            "SELECT id FROM plate_verifications WHERE violation_id = ?",
            (violation_id,),
        ).fetchone()
        if existing:
            set_clause = [
                "plate_status = ?",
                "processing_diagnostics_json = ?",
            ]
            params: list[Any] = [plate_status, diagnostics_json]
            # Distinguish omitted OCR update from intentional identity clear.
            if ocr_raw is not None:
                set_clause.append("ocr_raw = ?")
                params.append(ocr_raw)
            if clear_accepted_identity or accepted_plate_text is not None:
                set_clause.append("accepted_plate_text = ?")
                params.append(None if clear_accepted_identity else accepted_plate_text)
            if review_id is not None:
                set_clause.append("review_id = ?")
                params.append(review_id)
            if alpr_model is not None:
                set_clause.append("alpr_model = ?")
                params.append(alpr_model)
            if alpr_version is not None:
                set_clause.append("alpr_version = ?")
                params.append(alpr_version)
            if ocr_confidence is not None:
                set_clause.append("ocr_confidence = ?")
                params.append(ocr_confidence)
            if verified_by is not None:
                set_clause.append("verified_by = ?")
                params.append(verified_by)
            if verified_at is not None:
                set_clause.append("verified_at = ?")
                params.append(verified_at)
            params.append(violation_id)
            conn.execute(
                f"UPDATE plate_verifications SET {', '.join(set_clause)} WHERE violation_id = ?",
                params,
            )
            record_id = int(existing["id"])
        else:
            cursor = conn.execute(
                """
                INSERT INTO plate_verifications
                    (violation_id, review_id, ocr_raw,
                     accepted_plate_text, plate_status, alpr_model, alpr_version,
                     ocr_confidence, processing_diagnostics_json, verified_by,
                     verified_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    violation_id,
                    review_id,
                    ocr_raw,
                    None if clear_accepted_identity else accepted_plate_text,
                    plate_status,
                    alpr_model,
                    alpr_version,
                    ocr_confidence,
                    diagnostics_json,
                    verified_by,
                    verified_at,
                    now,
                ),
            )
            record_id = int(cursor.lastrowid)

        if _fail_after == "verification":
            raise RuntimeError("verification boom")

        if write_audit:
            if not _table_exists(conn, "case_action_events"):
                raise ValueError("case_action_events table does not exist")
            detail = dict(audit_detail or {})
            detail["plate_verification_id"] = record_id
            actor = actor_user_id if actor_user_id is not None else verified_by
            if actor is not None:
                user_row = conn.execute(
                    "SELECT id FROM users WHERE id = ?", (actor,)
                ).fetchone()
                if user_row is None:
                    actor = None
            conn.execute(
                """
                INSERT INTO case_action_events
                    (violation_id, review_id, action_type, detail_json,
                     actor_user_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    violation_id,
                    review_id,
                    ACTION_PLATE_VERIFIED,
                    json.dumps(detail),
                    actor,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )

        if _fail_after == "audit":
            raise RuntimeError("audit boom")

        if write_legacy and legacy_plate_status is not None:
            conn.execute(
                """
                UPDATE violations
                SET plate_text = ?, plate_status = ?
                WHERE id = ?
                """,
                (legacy_plate_text, legacy_plate_status, violation_id),
            )

        if _fail_after == "legacy":
            raise RuntimeError("legacy boom")

        return record_id


def get_plate_verification(violation_id: int) -> dict[str, Any] | None:
    """Return the plate verification record for a violation, if any."""
    with db_session() as conn:
        if not _table_exists(conn, "plate_verifications"):
            return None
        row = conn.execute(
            "SELECT * FROM plate_verifications WHERE violation_id = ?",
            (violation_id,),
        ).fetchone()
        return _row_to_dict(row)


def record_recurrence_review(
    violation_id: int,
    policy_version_id: int,
    *,
    lookback_days: int = 365,
    matched_violation_ids: list[int] | None = None,
    eligible_match_ids: list[int] | None = None,
    suggested_recurrence_count: int = 0,
    evaluation_time: str | None = None,
    evaluated_by: int | None = None,
    detail: dict[str, Any] | None = None,
) -> int:
    """Record a recurrence evaluation snapshot for a violation.

    Preserves the matches, eligibility, and policy version at review time.
    Past snapshots are preserved when policy changes — this UPSERT updates
    the existing row rather than creating duplicates.
    """
    now = datetime.now().isoformat(sep=" ", timespec="seconds")
    with db_session() as conn:
        if not _table_exists(conn, "recurrence_reviews"):
            raise ValueError("recurrence_reviews table does not exist")
        existing = conn.execute(
            "SELECT id FROM recurrence_reviews WHERE violation_id = ? AND policy_version_id = ?",
            (violation_id, policy_version_id),
        ).fetchone()
        matched_json = json.dumps(matched_violation_ids or [])
        eligible_json = json.dumps(eligible_match_ids or [])
        detail_json = json.dumps(detail or {})
        eval_time = evaluation_time or now
        if existing:
            conn.execute(
                """
                UPDATE recurrence_reviews
                SET lookback_days = ?, matched_violation_ids_json = ?,
                    eligible_match_ids_json = ?, suggested_recurrence_count = ?,
                    evaluation_time = ?, evaluated_by = ?, detail_json = ?
                WHERE violation_id = ? AND policy_version_id = ?
                """,
                (
                    lookback_days,
                    matched_json,
                    eligible_json,
                    suggested_recurrence_count,
                    eval_time,
                    evaluated_by,
                    detail_json,
                    violation_id,
                    policy_version_id,
                ),
            )
            return int(existing["id"])
        cursor = conn.execute(
            """
            INSERT INTO recurrence_reviews
                (violation_id, policy_version_id, lookback_days,
                 matched_violation_ids_json, eligible_match_ids_json,
                 suggested_recurrence_count, evaluation_time, evaluated_by,
                 detail_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                violation_id,
                policy_version_id,
                lookback_days,
                matched_json,
                eligible_json,
                suggested_recurrence_count,
                eval_time,
                evaluated_by,
                detail_json,
            ),
        )
        return int(cursor.lastrowid)


def record_event_time(
    violation_id: int,
    event_time: str,
    source: str,
    *,
    confirmed_by: int | None = None,
    original_metadata: str | None = None,
    video_timestamp_sec: float | None = None,
    timezone_offset: str | None = None,
) -> None:
    """Record event-time provenance for a case.

    Distinguishes event time from processing/upload time. Source must be one
    of: 'cctv_timestamp', 'video_metadata', 'user_entry', 'user_confirmation'.
    """
    valid_sources = (
        "cctv_timestamp",
        "video_metadata",
        "user_entry",
        "user_confirmation",
    )
    if source not in valid_sources:
        raise ValueError(f"Invalid event-time source: {source}")
    now = datetime.now().isoformat(sep=" ", timespec="seconds")
    detail: dict[str, Any] = {
        "event_time": event_time,
        "source": source,
        "confirmed_by": confirmed_by,
        "original_metadata": original_metadata,
        "video_timestamp_sec": video_timestamp_sec,
        "timezone_offset": timezone_offset or "",
    }
    record_case_action(
        violation_id,
        ACTION_EVENT_TIME_CONFIRMED,
        detail=detail,
        actor_user_id=confirmed_by,
    )


def insert_case_policy_from_mapping(
    violation_id: int,
    policy_version_id: int,
    canonical_rule: str,
) -> int:
    """Insert a case_policy_records row from the legal_behavior_mappings config.

    Looks up the mapping for the canonical rule under the given policy version
    and persists the official category, legal status, and behavior details.
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db_session() as conn:
        if not _table_exists(conn, "legal_behavior_mappings"):
            raise ValueError("legal_behavior_mappings table does not exist")
        mapping = conn.execute(
            """
            SELECT * FROM legal_behavior_mappings
            WHERE policy_version_id = ? AND canonical_rule = ?
            """,
            (policy_version_id, canonical_rule),
        ).fetchone()
        if mapping is None:
            # No mapping for this rule under this policy version —
            # fail safe: record the case with null category and unverified status.
            return create_case_policy_record(
                violation_id=violation_id,
                policy_version_id=policy_version_id,
                canonical_rule=canonical_rule,
                official_category=None,
                legal_status=None,
                behavior_details=[],
            )
        return create_case_policy_record(
            violation_id=violation_id,
            policy_version_id=policy_version_id,
            canonical_rule=canonical_rule,
            official_category=mapping["official_category"],
            legal_status=mapping["legal_status"],
            behavior_details=json.loads(mapping["behavior_details_json"] or "[]"),
        )


def record_policy_mapping(policy_version_id: int, mappings_data: list[dict[str, Any]]) -> None:
    """Bulk-insert behavior mappings from the JSON config for a policy version.

    Called when a policy version is approved to materialize the mapping rows.
    """
    with db_session() as conn:
        if not _table_exists(conn, "legal_behavior_mappings"):
            raise ValueError("legal_behavior_mappings table does not exist")
        rows = []
        for m in mappings_data:
            rows.append((
                policy_version_id,
                m["canonical_rule"],
                m.get("official_category"),
                m.get("legal_status", "unverified"),
                json.dumps(m.get("verified_elements", [])),
                json.dumps(m.get("unresolved_elements", [])),
                json.dumps(m.get("behavior_details", [])),
                m.get("provision_reference"),
                json.dumps(m.get("penalty_schedule")),
                m.get("source_url"),
                m.get("source_document_id"),
                m.get("mapping_version"),
                json.dumps(m.get("is_grouped_with", [])),
                m.get("notes"),
            ))
        conn.executemany(
            """
            INSERT OR REPLACE INTO legal_behavior_mappings
                (policy_version_id, canonical_rule, official_category,
                 legal_status, verified_elements_json, unresolved_elements_json,
                 behavior_details_json, provision_reference, penalty_schedule_json,
                 source_url, source_document_id, mapping_version,
                 is_grouped_with_json, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def is_case_confirmed(violation_id: int) -> bool:
    """Return True if a case_confirmed audit event exists for this violation."""
    actions = get_case_actions(violation_id)
    return any(a.get("action_type") == ACTION_CASE_CONFIRMED for a in actions)


def is_notice_printed(violation_id: int) -> bool:
    """Return True if a notice_printed audit event exists for this violation."""
    actions = get_case_actions(violation_id)
    return any(a.get("action_type") == ACTION_NOTICE_PRINTED for a in actions)


def get_violation_with_policy(violation_id: int) -> dict[str, Any] | None:
    """Return a violation joined with its case policy record and plate
    verification, if present. This is the enriched view for the UI.
    """
    with db_session() as conn:
        row = conn.execute(
            """
            SELECT v.*, cpr.official_category, cpr.legal_status AS policy_legal_status,
                   pv.plate_status AS verification_plate_status,
                   pv.accepted_plate_text AS verified_plate_text,
                   pv.alpr_model, pv.alpr_version
            FROM violations v
            LEFT JOIN case_policy_records cpr ON cpr.violation_id = v.id
            LEFT JOIN plate_verifications pv ON pv.violation_id = v.id
            WHERE v.id = ?
            """,
            (violation_id,),
        ).fetchone()
        return _row_to_dict(row)


def get_recurrence_review(
    violation_id: int, policy_version_id: int
) -> dict[str, Any] | None:
    """Return the recurrence review for a violation + policy version pair."""
    with db_session() as conn:
        if not _table_exists(conn, "recurrence_reviews"):
            return None
        row = conn.execute(
            """
            SELECT * FROM recurrence_reviews
            WHERE violation_id = ? AND policy_version_id = ?
            """,
            (violation_id, policy_version_id),
        ).fetchone()
        return _row_to_dict(row)


# ---------------------------------------------------------------------------
# Schema introspection helpers (used by tests)
# ---------------------------------------------------------------------------


def list_tables() -> list[str]:
    """Return all user-defined table names in the current database."""
    with db_session() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return [row["name"] for row in rows]


def get_table_columns(table_name: str) -> list[str]:
    """Return column names for a given table."""
    with db_session() as conn:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return [row["name"] for row in rows]


# ---------------------------------------------------------------------------
# Stage C/D/E integration helpers (case identity, plate, event-time, print)
# ---------------------------------------------------------------------------

CONFIG_MAPPING_POLICY_VERSION = "config:violation_legal_mappings.json"


def ensure_config_mapping_policy_version() -> int:
    """Ensure a non-activating policy version exists for JSON-backed snapshots.

    Status remains ``proposed`` so it never becomes the active lookback policy
    and does not silently enable offense-level suggestions.
    """
    with db_session() as conn:
        if not _table_exists(conn, "legal_policy_versions"):
            raise ValueError("legal_policy_versions table does not exist")
        row = conn.execute(
            "SELECT id FROM legal_policy_versions WHERE version = ?",
            (CONFIG_MAPPING_POLICY_VERSION,),
        ).fetchone()
        if row is not None:
            return int(row["id"])
        cursor = conn.execute(
            """
            INSERT INTO legal_policy_versions
                (version, status, lookback_days, schedule_json, detail_json, created_at)
            VALUES (?, 'proposed', 365, '{}', ?, ?)
            """,
            (
                CONFIG_MAPPING_POLICY_VERSION,
                json.dumps(
                    {
                        "source": "config/violation_legal_mappings.json",
                        "offense_suggestions_enabled": False,
                        "note": "Runtime snapshot only; not CTEU-activated",
                    }
                ),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        return int(cursor.lastrowid)


def can_verify_plate(user_id: int) -> bool:
    """True when an active enforcer/admin or explicit verify_plate grant may act."""
    user = get_user(user_id)
    if user is None or not _user_account_is_active(user):
        return False
    if user.get("role") in ENFORCEMENT_ROLES:
        return True
    return user_has_permission(user_id, PERM_VERIFY_PLATE)


def can_confirm_event_time(user_id: int) -> bool:
    """True when an active enforcer/admin or explicit confirm_event_time grant."""
    user = get_user(user_id)
    if user is None or not _user_account_is_active(user):
        return False
    if user.get("role") in ENFORCEMENT_ROLES:
        return True
    return user_has_permission(user_id, PERM_CONFIRM_EVENT_TIME)


def list_pending_reviews_for_event(
    *,
    video_id: int,
    track_id: int,
    processing_run_id: int | None,
) -> list[dict[str, Any]]:
    """Pending review rows sharing source/run/track (not sufficient alone)."""
    return [
        r
        for r in list_reviews_for_event(
            video_id=video_id,
            track_id=track_id,
            processing_run_id=processing_run_id,
        )
        if r.get("status") == "pending"
    ]


def list_reviews_for_event(
    *,
    video_id: int,
    track_id: int,
    processing_run_id: int | None,
) -> list[dict[str, Any]]:
    """Pending and confirmed reviews sharing source/run/track event key.

    Dismissed reviews are excluded. Callers must still apply episode overlap
    before treating rows as the same logical case.
    """
    with db_session() as conn:
        if processing_run_id is None:
            rows = conn.execute(
                """
                SELECT * FROM review_queue
                WHERE status IN ('pending', 'confirmed')
                  AND video_id = ?
                  AND track_id = ?
                  AND processing_run_id IS NULL
                ORDER BY id ASC
                """,
                (video_id, track_id),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM review_queue
                WHERE status IN ('pending', 'confirmed')
                  AND video_id = ?
                  AND track_id = ?
                  AND processing_run_id = ?
                ORDER BY id ASC
                """,
                (video_id, track_id, processing_run_id),
            ).fetchall()
        return [_row_to_dict(row) for row in rows]


def find_violation_linked_to_review(review_id: int) -> int | None:
    """Return violation_id linked via review_confirmed audit, if any."""
    with db_session() as conn:
        if not _table_exists(conn, "case_action_events"):
            return None
        row = conn.execute(
            """
            SELECT violation_id FROM case_action_events
            WHERE review_id = ? AND action_type = ? AND violation_id IS NOT NULL
            ORDER BY id ASC
            LIMIT 1
            """,
            (review_id, ACTION_REVIEW_CONFIRMED),
        ).fetchone()
        return int(row["violation_id"]) if row else None


def find_orphan_violation_for_review(review_id: int) -> int | None:
    """Recover a violation created for a confirmed review when the link is missing."""
    with db_session() as conn:
        review = conn.execute(
            "SELECT * FROM review_queue WHERE id = ?",
            (review_id,),
        ).fetchone()
        if review is None or review["status"] != "confirmed":
            return None
        run_id = review["processing_run_id"] if "processing_run_id" in review.keys() else None
        if run_id is None:
            rows = conn.execute(
                """
                SELECT id FROM violations
                WHERE video_id = ? AND track_id = ?
                  AND processing_run_id IS NULL
                  AND violation_type = ?
                  AND status != 'dismissed'
                ORDER BY id DESC
                """,
                (review["video_id"], review["track_id"], review["violation_type"]),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id FROM violations
                WHERE video_id = ? AND track_id = ?
                  AND processing_run_id = ?
                  AND violation_type = ?
                  AND status != 'dismissed'
                ORDER BY id DESC
                """,
                (
                    review["video_id"],
                    review["track_id"],
                    run_id,
                    review["violation_type"],
                ),
            ).fetchall()
        for row in rows:
            vid = int(row["id"])
            linked = conn.execute(
                """
                SELECT 1 FROM case_action_events
                WHERE violation_id = ? AND review_id = ? AND action_type = ?
                LIMIT 1
                """,
                (vid, review_id, ACTION_REVIEW_CONFIRMED),
            ).fetchone()
            if linked:
                return vid
            # Prefer violations that share episode bounds when present.
            viol = conn.execute(
                "SELECT * FROM violations WHERE id = ?", (vid,)
            ).fetchone()
            if viol is None:
                continue
            ep_match = True
            for col in ("episode_start_sec", "episode_end_sec", "timestamp_sec"):
                if col in review.keys() and review[col] is not None:
                    if col not in viol.keys() or viol[col] != review[col]:
                        ep_match = False
                        break
            if ep_match:
                return vid
        return None


def _episode_windows_overlap(
    a_start: float | None,
    a_end: float | None,
    a_ts: float | None,
    b_start: float | None,
    b_end: float | None,
    b_ts: float | None,
) -> bool:
    """Episode overlap without inventing proximity merges."""
    if a_start is not None and a_end is None and a_ts is not None:
        a_end = a_ts
    if a_end is not None and a_start is None and a_ts is not None:
        a_start = a_ts
    if b_start is not None and b_end is None and b_ts is not None:
        b_end = b_ts
    if b_end is not None and b_start is None and b_ts is not None:
        b_start = b_ts

    if (
        a_start is not None
        and a_end is not None
        and b_start is not None
        and b_end is not None
    ):
        return a_start <= b_end and b_start <= a_end
    if a_ts is not None and b_start is not None and b_end is not None:
        return b_start <= a_ts <= b_end
    if b_ts is not None and a_start is not None and a_end is not None:
        return a_start <= b_ts <= a_end
    return False


def find_fused_case_violation(
    *,
    video_id: int,
    processing_run_id: int | None,
    track_id: int,
    contributing_rules: tuple[str, ...] | list[str],
    episode_start_sec: float | None = None,
    episode_end_sec: float | None = None,
    timestamp_sec: float | None = None,
    require_fusion_pair: bool = False,
) -> int | None:
    """Find an existing non-dismissed violation for an overlapping episode.

    Source/run/track alone is never sufficient — episode windows must overlap.
    Dismissed cases are never returned.
    """
    from core.detection_config import (
        VIOLATION_ILLEGAL_PARKING,
        VIOLATION_OBSTRUCTION,
    )

    rules = set(contributing_rules)
    fusion_pair = {VIOLATION_ILLEGAL_PARKING, VIOLATION_OBSTRUCTION}
    with db_session() as conn:
        if processing_run_id is None:
            rows = conn.execute(
                """
                SELECT * FROM violations
                WHERE video_id = ? AND track_id = ?
                  AND processing_run_id IS NULL
                  AND status != 'dismissed'
                ORDER BY id ASC
                """,
                (video_id, track_id),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM violations
                WHERE video_id = ? AND track_id = ?
                  AND processing_run_id = ?
                  AND status != 'dismissed'
                ORDER BY id ASC
                """,
                (video_id, track_id, processing_run_id),
            ).fetchall()
        for row in rows:
            vid = int(row["id"])
            if not _episode_windows_overlap(
                episode_start_sec,
                episode_end_sec,
                timestamp_sec,
                row["episode_start_sec"] if "episode_start_sec" in row.keys() else None,
                row["episode_end_sec"] if "episode_end_sec" in row.keys() else None,
                row["timestamp_sec"] if "timestamp_sec" in row.keys() else None,
            ):
                continue
            vtype = row["violation_type"]
            stored: set[str] = set()
            if _table_exists(conn, "case_policy_records"):
                policy_rows = conn.execute(
                    """
                    SELECT canonical_rule FROM case_policy_records
                    WHERE violation_id = ?
                    """,
                    (vid,),
                ).fetchall()
                stored = {r["canonical_rule"] for r in policy_rows}
            if require_fusion_pair:
                if rules.issubset(stored) or (
                    vtype in fusion_pair and (stored & fusion_pair or vtype in rules)
                ):
                    return vid
                continue
            # Late fusion attach: parking-only or obstruction-only case that
            # overlaps may accept the complementary fusion member.
            if vtype in fusion_pair or (stored & fusion_pair):
                if not rules or rules & fusion_pair or rules.issubset(stored | {vtype}):
                    return vid
            elif vtype in rules or rules.issubset(stored):
                return vid
    return None


def mark_review_confirmed_linked(
    review_id: int,
    violation_id: int,
    reviewed_by: int,
) -> None:
    """Mark a pending review confirmed and linked without inserting a violation."""
    reviewed_at = datetime.now().isoformat(sep=" ", timespec="seconds")
    with db_session() as conn:
        row = conn.execute(
            "SELECT status FROM review_queue WHERE id = ?",
            (review_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Review item {review_id} not found")
        if row["status"] == "pending":
            conn.execute(
                """
                UPDATE review_queue
                SET status = 'confirmed', reviewed_by = ?, reviewed_at = ?
                WHERE id = ?
                """,
                (reviewed_by, reviewed_at, review_id),
            )
    link_review_to_case(review_id, violation_id)


def update_violation_canonical_type(violation_id: int, violation_type: str) -> None:
    with db_session() as conn:
        conn.execute(
            "UPDATE violations SET violation_type = ? WHERE id = ?",
            (violation_type, violation_id),
        )


def merge_violation_evidence(violation_id: int, evidence: dict[str, Any]) -> None:
    """Fill empty evidence fields from grouped observations; never wipe existing."""
    allowed = (
        "evidence_path",
        "vehicle_evidence_path",
        "plate_evidence_path",
        "evidence_clip_path",
        "reason_log",
    )
    with db_session() as conn:
        row = conn.execute(
            "SELECT * FROM violations WHERE id = ?", (violation_id,)
        ).fetchone()
        if row is None:
            return
        fields: list[str] = []
        params: list[Any] = []
        for key in allowed:
            incoming = evidence.get(key)
            if not incoming:
                continue
            current = row[key] if key in row.keys() else None
            if current:
                continue
            fields.append(f"{key} = ?")
            params.append(incoming)
        if not fields:
            return
        params.append(violation_id)
        conn.execute(
            f"UPDATE violations SET {', '.join(fields)} WHERE id = ?",
            params,
        )


def update_violation_plate_fields(
    violation_id: int,
    *,
    plate_text: str | None,
    plate_status: str,
) -> None:
    with db_session() as conn:
        conn.execute(
            """
            UPDATE violations
            SET plate_text = ?, plate_status = ?
            WHERE id = ?
            """,
            (plate_text, plate_status, violation_id),
        )


def get_case_policy_records(violation_id: int) -> list[dict[str, Any]]:
    with db_session() as conn:
        if not _table_exists(conn, "case_policy_records"):
            return []
        rows = conn.execute(
            """
            SELECT * FROM case_policy_records
            WHERE violation_id = ?
            ORDER BY id ASC
            """,
            (violation_id,),
        ).fetchall()
        return [_row_to_dict(row) for row in rows]


def get_confirmed_event_time(violation_id: int) -> str | None:
    """Latest effective event time from event_time_confirmed audit details."""
    snap = get_event_time_snapshot(violation_id)
    if snap is None:
        return None
    return snap.get("event_time")


def get_event_time_snapshot(violation_id: int) -> dict[str, Any] | None:
    """Latest persisted event-time confirmation with provenance metadata."""
    actions = get_case_actions(violation_id)
    latest: dict[str, Any] | None = None
    for action in actions:
        if action.get("action_type") != ACTION_EVENT_TIME_CONFIRMED:
            continue
        try:
            detail = json.loads(action.get("detail_json") or "{}")
        except json.JSONDecodeError:
            continue
        value = detail.get("event_time") or detail.get("effective_confirmed")
        if not value and detail.get("persisted_effective") is False:
            # Unresolved marker — keep scanning for a later effective value.
            continue
        if not value:
            continue
        meta = detail.get("original_metadata")
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except json.JSONDecodeError:
                meta = {}
        elif not isinstance(meta, dict):
            meta = {}
        # When original_metadata holds the rich service detail, prefer it.
        if meta.get("effective_confirmed") or meta.get("contributing_claims"):
            rich = meta
        else:
            rich = detail
        latest = {
            "event_time": str(value),
            "source": detail.get("source") or rich.get("original_source"),
            "original_metadata": rich,
            "confirmed_by": detail.get("confirmed_by") or rich.get("confirmed_by"),
            "confirmed_at": (
                action.get("created_at")
                or rich.get("confirmed_at")
                or rich.get("original_confirmed_at")
            ),
            "video_timestamp_sec": detail.get("video_timestamp_sec")
            or rich.get("video_relative_sec"),
            "timezone_offset": detail.get("timezone_offset"),
            "action_id": action.get("id"),
        }
    return latest


def list_recurrence_candidate_violations(
    *,
    plate_text: str,
    official_category: str,
    exclude_violation_id: int,
) -> list[dict[str, Any]]:
    """Candidate prior violations sharing verified plate + official category."""
    needle = plate_text.strip().upper()
    with db_session() as conn:
        if not _table_exists(conn, "plate_verifications"):
            return []
        rows = conn.execute(
            """
            SELECT DISTINCT v.*
            FROM violations v
            INNER JOIN plate_verifications pv ON pv.violation_id = v.id
            INNER JOIN case_policy_records cpr ON cpr.violation_id = v.id
            WHERE v.id != ?
              AND v.status NOT IN ('dismissed', 'rejected')
              AND pv.plate_status = 'verified_readable'
              AND UPPER(TRIM(pv.accepted_plate_text)) = ?
              AND cpr.official_category = ?
            ORDER BY v.id ASC
            """,
            (exclude_violation_id, needle, official_category),
        ).fetchall()
        return [_row_to_dict(row) for row in rows]


def record_print_batch(
    violation_ids: list[int],
    printer_id: int,
    *,
    document_reference: str | None = None,
) -> dict[str, Any]:
    """Attest notice printing for an explicit batch membership set.

    Returns per-case results. Never claims full-batch success when any case
    failed. Preview/PDF/download are not attestation.
    """
    membership = [int(v) for v in violation_ids]
    results: list[dict[str, Any]] = []
    succeeded = 0
    failed = 0
    for vid in membership:
        try:
            ok = confirm_notice_printed(
                vid,
                printer_id,
                document_reference=document_reference,
            )
            results.append(
                {
                    "violation_id": vid,
                    "success": True,
                    "notice_printed": bool(ok),
                }
            )
            succeeded += 1
        except (PermissionError, ValueError) as exc:
            results.append(
                {
                    "violation_id": vid,
                    "success": False,
                    "error": str(exc),
                }
            )
            failed += 1
    return {
        "membership": membership,
        "results": results,
        "succeeded": succeeded,
        "failed": failed,
        "all_succeeded": failed == 0 and succeeded == len(membership),
        "document_reference": document_reference,
        "printer_id": printer_id,
    }


def offense_suggestions_enabled_for_active_policy() -> bool:
    """True only when the active approved policy explicitly enables suggestions."""
    active = get_active_legal_policy_version()
    if active is None:
        return False
    try:
        detail = json.loads(active.get("detail_json") or "{}")
    except json.JSONDecodeError:
        return False
    return bool(detail.get("offense_suggestions_enabled"))
