"""Migration 013 (review_decisions retention rebuild): atomicity and recovery.

Faults are injected with a SQLite authorizer that refuses one specific
statement, so the interruption happens at a named point of the rebuild
without changing the migration code. After each failure the database is
closed and reopened, the durable state is inspected, and the migration is
retried.

Interrupted states produced by the earlier autocommit implementation are
built with that implementation's own statements.

Every database here is a temporary file. The operational database is never
opened.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from database import sqlite_adapter

MIGRATION_012 = Path(__file__).resolve().parents[1] / "database" / "migrations" / "012_review_decisions.sql"
RETAINED = "review_decisions__retained"

OLD_COLUMNS = (
    "id",
    "review_id",
    "decision",
    "original_violation_type",
    "selected_canonical_rule",
    "reason",
    "reviewer_user_id",
    "decided_at",
    "evidence_refs_json",
    "policy_refs_json",
    "idempotency_key",
    "created_at",
)
PROTECTIONS = {
    "idx_review_decisions_one_per_review",
    "idx_review_decisions_idempotency",
    "idx_review_decisions_decision",
    "review_decisions_no_update",
    "review_decisions_no_delete",
}

# Statements of the earlier, non-atomic migration 013, used only to build the
# partial states it could leave on disk.
OLD_CREATE_RETAINED = """
CREATE TABLE review_decisions__retained (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  review_id INTEGER NOT NULL,
  decision TEXT NOT NULL CHECK(decision IN (
    'confirm_proposed',
    'correct_canonical',
    'no_violation',
    'insufficient_evidence'
  )),
  original_violation_type TEXT NOT NULL,
  selected_canonical_rule TEXT,
  reason TEXT NOT NULL,
  reviewer_user_id INTEGER NOT NULL REFERENCES users(id),
  decided_at TEXT NOT NULL,
  evidence_refs_json TEXT NOT NULL DEFAULT '{}',
  policy_refs_json TEXT NOT NULL DEFAULT '{}',
  idempotency_key TEXT NOT NULL,
  source_video_id INTEGER,
  source_track_id INTEGER,
  source_processing_run_id INTEGER,
  queue_status_at_decision TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  CHECK (
    (
      decision IN ('confirm_proposed', 'correct_canonical')
      AND selected_canonical_rule IS NOT NULL
    )
    OR (
      decision IN ('no_violation', 'insufficient_evidence')
      AND selected_canonical_rule IS NULL
    )
  )
)
"""
OLD_COPY = """
INSERT INTO review_decisions__retained (
  id, review_id, decision, original_violation_type, selected_canonical_rule,
  reason, reviewer_user_id, decided_at, evidence_refs_json, policy_refs_json,
  idempotency_key, source_video_id, source_track_id, source_processing_run_id,
  queue_status_at_decision, created_at
)
SELECT
  d.id, d.review_id, d.decision, d.original_violation_type,
  d.selected_canonical_rule, d.reason, d.reviewer_user_id, d.decided_at,
  d.evidence_refs_json, d.policy_refs_json, d.idempotency_key,
  q.video_id, q.track_id, q.processing_run_id, q.status, d.created_at
FROM review_decisions d
LEFT JOIN review_queue q ON q.id = d.review_id
"""

QUEUE_ROWS = {
    9: (4, 7, "Counterflow", "dismissed", 1),
    10: (4, 8, "Obstruction", "confirmed", 1),
    11: (5, 2, "Illegal Parking", "confirmed", None),
    12: (6, 3, "Counterflow", "dismissed", 2),
}


def _open(path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _make_012_db(tmp_path) -> tuple[Path, dict[int, tuple]]:
    """A database at the original migration-012 shape with representative decisions."""
    path = tmp_path / "upgrade_012.db"
    conn = _open(path)
    conn.executescript(
        """
        CREATE TABLE users (
          id INTEGER PRIMARY KEY,
          username TEXT UNIQUE,
          password_hash TEXT,
          role TEXT,
          is_active INTEGER DEFAULT 1
        );
        CREATE TABLE review_queue (
          id INTEGER PRIMARY KEY,
          video_id INTEGER,
          track_id INTEGER,
          violation_type TEXT,
          status TEXT,
          processing_run_id INTEGER
        );
        INSERT INTO users (id, username, password_hash, role) VALUES
          (1, 'enf', 'hash', 'enforcer'),
          (2, 'adm', 'hash', 'admin');
        """
    )
    for rid, (video, track, vtype, status, run) in QUEUE_ROWS.items():
        conn.execute(
            "INSERT INTO review_queue VALUES (?, ?, ?, ?, ?, ?)",
            (rid, video, track, vtype, status, run),
        )
    conn.executescript(MIGRATION_012.read_text(encoding="utf-8"))
    decisions = [
        (9, "insufficient_evidence", "Counterflow", None, "Unclear frames.", 1,
         {"evidence_path": "/tmp/a.jpg", "frame_number": 12},
         {"materialized": False, "policy_version_id": 3, "snapshots": []}, "k9"),
        (10, "confirm_proposed", "Obstruction", "Obstruction", "Blocking the lane.", 1,
         {"evidence_clip_path": "/tmp/b.mp4"},
         {"policy_version_id": 3, "snapshots": [{"canonical_rule": "Obstruction"}],
          "computed_before_materialization": True}, "legacy:confirm"),
        (11, "correct_canonical", "Illegal Parking", "Counterflow", "Opposing flow.", 2,
         {"evidence_path": "/tmp/c.jpg"},
         {"policy_version_id": 4, "contributing_rules": ["Counterflow"]}, "corr-11"),
        (12, "no_violation", "Counterflow", None, "Permitted movement.", 2,
         {}, {"materialized": False, "policy_version_id": None, "snapshots": []},
         "legacy:dismiss"),
    ]
    for i, (rid, decision, original, selected, reason, reviewer, ev, pol, key) in enumerate(
        decisions, start=1
    ):
        conn.execute(
            """
            INSERT INTO review_decisions (
                review_id, decision, original_violation_type, selected_canonical_rule,
                reason, reviewer_user_id, decided_at, evidence_refs_json,
                policy_refs_json, idempotency_key, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rid, decision, original, selected, reason, reviewer,
                f"2026-10-0{i}T08:00:00+00:00", json.dumps(ev), json.dumps(pol), key,
                f"2026-10-0{i} 08:00:01",
            ),
        )
    conn.commit()
    original = _rows(conn, "review_decisions")
    conn.close()
    assert len(original) == 4
    return path, original


def _rows(conn, table) -> dict[int, tuple]:
    cols = ", ".join(OLD_COLUMNS)
    return {
        int(r[0]): tuple(r)
        for r in conn.execute(f"SELECT {cols} FROM {table} ORDER BY id").fetchall()
    }


def _tables(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _protections(conn) -> set[str]:
    return {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE tbl_name = 'review_decisions' "
            "AND type IN ('index', 'trigger')"
        )
    }


def _ddl(conn) -> str:
    return conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'review_decisions'"
    ).fetchone()[0]


def _assert_append_only(conn, some_id):
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE review_decisions SET reason = 'rewritten' WHERE id = ?", (some_id,))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM review_decisions WHERE id = ?", (some_id,))
    conn.rollback()


def _insert_decision(conn, review_id, reviewer, key):
    conn.execute(
        """
        INSERT INTO review_decisions (
            review_id, decision, original_violation_type, selected_canonical_rule,
            reason, reviewer_user_id, decided_at, idempotency_key
        ) VALUES (?, 'no_violation', 'Counterflow', NULL, 'probe', ?, 'now', ?)
        """,
        (review_id, reviewer, key),
    )


def _assert_untouched_012(path, original):
    """Durable state equals the pre-migration database."""
    conn = _open(path)
    try:
        assert RETAINED not in _tables(conn)
        assert "REFERENCES review_queue" in _ddl(conn)
        assert _rows(conn, "review_decisions") == original
        assert _protections(conn) >= PROTECTIONS
        _assert_append_only(conn, 1)
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            _insert_decision(conn, 999, 1, "fk-probe")
        conn.rollback()
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def _assert_migrated(path, original):
    conn = _open(path)
    try:
        assert RETAINED not in _tables(conn)
        assert "references review_queue" not in _ddl(conn).lower()
        assert _rows(conn, "review_decisions") == original
        for row in conn.execute("SELECT * FROM review_decisions"):
            video, track, _vtype, status, run = QUEUE_ROWS[row["review_id"]]
            assert (row["source_video_id"], row["source_track_id"]) == (video, track)
            assert row["source_processing_run_id"] == run
            assert row["queue_status_at_decision"] == status
        assert _protections(conn) == PROTECTIONS
        _assert_append_only(conn, 1)
        assert conn.execute("PRAGMA foreign_key_check(review_decisions)").fetchall() == []
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            _insert_decision(conn, 9_999, 77, "fk-reviewer")
        conn.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            _insert_decision(conn, 9, 1, "second-for-9")
        conn.rollback()
        # Queue removal no longer touches decision history.
        conn.execute("DELETE FROM review_queue WHERE id = 9")
        assert conn.execute(
            "SELECT reason FROM review_decisions WHERE review_id = 9"
        ).fetchone()[0] == "Unclear frames."
        conn.rollback()
        # A repeated run is a no-op.
        before = conn.execute("SELECT * FROM review_decisions ORDER BY id").fetchall()
        sqlite_adapter._apply_migration_013(conn)
        after = conn.execute("SELECT * FROM review_decisions ORDER BY id").fetchall()
        assert [tuple(r) for r in after] == [tuple(r) for r in before]
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


def _deny(action, name):
    def authorizer(code, arg1, arg2, _db, _source):
        if code == action and name in (arg1, arg2):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    return authorizer


FAULTS = {
    "after_retained_table_created": (sqlite3.SQLITE_INSERT, RETAINED),
    "after_rows_copied_before_drop": (sqlite3.SQLITE_DROP_TABLE, "review_decisions"),
    "after_drop_before_rename": (sqlite3.SQLITE_ALTER_TABLE, RETAINED),
    "after_rename_before_indexes": (
        sqlite3.SQLITE_CREATE_INDEX,
        "idx_review_decisions_one_per_review",
    ),
    "during_index_restoration": (sqlite3.SQLITE_CREATE_INDEX, "idx_review_decisions_decision"),
    "during_trigger_restoration": (sqlite3.SQLITE_CREATE_TRIGGER, "review_decisions_no_delete"),
}


@pytest.mark.parametrize("fault", list(FAULTS))
def test_interrupted_rebuild_rolls_back_then_retry_succeeds(tmp_path, fault):
    path, original = _make_012_db(tmp_path)
    conn = _open(path)
    conn.set_authorizer(_deny(*FAULTS[fault]))
    with pytest.raises(Exception) as failure:
        sqlite_adapter._apply_migration_013(conn)
    conn.set_authorizer(None)
    assert not conn.in_transaction
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.close()

    _assert_untouched_012(path, original)
    assert isinstance(failure.value, sqlite_adapter.MigrationError)
    assert "rolled back" in str(failure.value)

    retry = _open(path)
    sqlite_adapter._apply_migration_013(retry)
    assert retry.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    retry.close()
    _assert_migrated(path, original)


def test_clean_upgrade_from_012(tmp_path):
    path, original = _make_012_db(tmp_path)
    conn = _open(path)
    sqlite_adapter._apply_migration_013(conn)
    conn.close()
    _assert_migrated(path, original)


def test_rebuild_rolls_back_on_foreign_key_integrity_failure(tmp_path):
    path, _original = _make_012_db(tmp_path)
    conn = _open(path)
    conn.execute("PRAGMA foreign_keys = OFF")
    _insert_decision(conn, 9_000, 77, "orphan-reviewer")
    conn.execute("INSERT INTO review_queue VALUES (9000, 1, 1, 'Counterflow', 'dismissed', 1)")
    conn.commit()
    with_orphan = _rows(conn, "review_decisions")
    conn.execute("PRAGMA foreign_keys = ON")
    with pytest.raises(sqlite_adapter.MigrationError, match="foreign-key target"):
        sqlite_adapter._apply_migration_013(conn)
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.close()
    check = _open(path)
    assert RETAINED not in _tables(check)
    assert "REFERENCES review_queue" in _ddl(check)
    assert _rows(check, "review_decisions") == with_orphan
    assert _protections(check) >= PROTECTIONS
    check.close()


# ---------------------------------------------------------------------------
# Partial states the earlier autocommit migration could leave on disk.
# ---------------------------------------------------------------------------


def _old_partial(conn, stop_after):
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(OLD_CREATE_RETAINED)
    conn.commit()
    if stop_after == "create":
        return
    conn.execute(OLD_COPY)
    conn.commit()
    if stop_after == "copy":
        return
    conn.execute("DROP TABLE review_decisions")
    conn.commit()
    if stop_after == "drop":
        return
    if stop_after == "drop_then_012_restart":
        conn.executescript(MIGRATION_012.read_text(encoding="utf-8"))
        return
    conn.execute(f"ALTER TABLE {RETAINED} RENAME TO review_decisions")
    conn.commit()
    if stop_after == "rename":
        return
    raise AssertionError(stop_after)


@pytest.mark.parametrize("stop_after", ["create", "copy", "drop", "drop_then_012_restart", "rename"])
def test_recovers_partial_state_from_earlier_migration(tmp_path, stop_after):
    path, original = _make_012_db(tmp_path)
    conn = _open(path)
    _old_partial(conn, stop_after)
    conn.close()

    retry = _open(path)
    sqlite_adapter._apply_migration_013(retry)
    assert retry.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    retry.close()
    _assert_migrated(path, original)


def test_conflicting_histories_stop_without_changes(tmp_path):
    path, _original = _make_012_db(tmp_path)
    conn = _open(path)
    _old_partial(conn, "drop_then_012_restart")
    # A decision was written into the re-created table and reuses id 1.
    conn.execute("PRAGMA foreign_keys = ON")
    _insert_decision(conn, 12, 1, "after-restart")
    conn.commit()
    live = _rows(conn, "review_decisions")
    retained = _rows(conn, RETAINED)
    assert live[1] != retained[1]

    with pytest.raises(sqlite_adapter.MigrationError) as failure:
        sqlite_adapter._apply_migration_013(conn)
    message = str(failure.value)
    assert "both hold decision history" in message
    assert "Neither table was changed" in message
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.close()

    check = _open(path)
    assert _rows(check, "review_decisions") == live
    assert _rows(check, RETAINED) == retained
    check.close()


def test_retained_row_that_differs_from_live_stops(tmp_path):
    path, original = _make_012_db(tmp_path)
    conn = _open(path)
    _old_partial(conn, "copy")
    conn.execute(f"UPDATE {RETAINED} SET reason = 'edited copy' WHERE id = 2")
    conn.commit()
    retained = _rows(conn, RETAINED)
    with pytest.raises(sqlite_adapter.MigrationError, match="ids only in or changed in"):
        sqlite_adapter._apply_migration_013(conn)
    conn.close()
    check = _open(path)
    assert _rows(check, "review_decisions") == original
    assert _rows(check, RETAINED) == retained
    check.close()


# ---------------------------------------------------------------------------
# Application start-up path (init_db runs 012 before 013).
# ---------------------------------------------------------------------------


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    path = tmp_path / "app_upgrade.db"
    monkeypatch.setenv("SQLITE_PATH", str(path))
    monkeypatch.setenv("DATABASE_URL", str(path))
    sqlite_adapter.init_db(force=True)
    return path


def _downgrade_to_012_with_rows(path) -> dict[int, tuple]:
    user_id = sqlite_adapter.create_user("mig_enf", "hash", role="enforcer")
    video_id = sqlite_adapter.insert_video("clip.mp4", "/tmp/clip.mp4", status="ready")
    review_ids = [
        sqlite_adapter.insert_review_queue(
            video_id=video_id,
            track_id=track,
            violation_type="Counterflow",
            confidence=0.9,
            frame_number=1,
            timestamp_sec=1.0,
            processing_run_id=1,
        )
        for track in (1, 2)
    ]
    conn = _open(path)
    conn.execute("DROP TABLE review_decisions")
    conn.executescript(MIGRATION_012.read_text(encoding="utf-8"))
    for rid, key in zip(review_ids, ("a", "b")):
        _insert_decision(conn, rid, user_id, key)
    conn.commit()
    assert "REFERENCES review_queue" in _ddl(conn)
    original = _rows(conn, "review_decisions")
    conn.close()
    return original


def test_init_db_recovers_after_drop_interruption(app_db):
    original = _downgrade_to_012_with_rows(app_db)
    conn = _open(app_db)
    _old_partial(conn, "drop")
    conn.close()

    sqlite_adapter.init_db()
    check = _open(app_db)
    try:
        assert RETAINED not in _tables(check)
        assert "references review_queue" not in _ddl(check).lower()
        assert _rows(check, "review_decisions") == original
        assert _protections(check) == PROTECTIONS
        assert check.execute("PRAGMA foreign_key_check").fetchall() == []
        _assert_append_only(check, min(original))
    finally:
        check.close()
    sqlite_adapter.init_db()
    check = _open(app_db)
    assert _rows(check, "review_decisions") == original
    check.close()


def test_init_db_after_injected_failure_then_restart(app_db, monkeypatch):
    original = _downgrade_to_012_with_rows(app_db)
    real_connect = sqlite_adapter.get_connection

    def connect_with_fault():
        conn = real_connect()
        conn.set_authorizer(_deny(sqlite3.SQLITE_ALTER_TABLE, RETAINED))
        return conn

    monkeypatch.setattr(sqlite_adapter, "get_connection", connect_with_fault)
    with pytest.raises(Exception) as failure:
        sqlite_adapter.init_db()
    monkeypatch.setattr(sqlite_adapter, "get_connection", real_connect)

    check = _open(app_db)
    assert RETAINED not in _tables(check)
    assert "REFERENCES review_queue" in _ddl(check)
    assert _rows(check, "review_decisions") == original
    check.close()
    assert isinstance(failure.value, sqlite_adapter.MigrationError)

    sqlite_adapter.init_db()
    check = _open(app_db)
    try:
        assert "references review_queue" not in _ddl(check).lower()
        assert _rows(check, "review_decisions") == original
        assert _protections(check) == PROTECTIONS
    finally:
        check.close()


def test_fresh_schema_is_left_unchanged(app_db):
    conn = _open(app_db)
    before = conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()
    sqlite_adapter._apply_migration_013(conn)
    after = conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()
    assert [tuple(r) for r in after] == [tuple(r) for r in before]
    assert "references review_queue" not in _ddl(conn).lower()
    assert _protections(conn) == PROTECTIONS
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.close()
    sqlite_adapter.init_db()
    sqlite_adapter.init_db()
    check = _open(app_db)
    assert RETAINED not in _tables(check)
    assert _protections(check) == PROTECTIONS
    check.close()
