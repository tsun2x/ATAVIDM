"""Consistent snapshot coverage for bounded report exports."""

from __future__ import annotations

import threading
import uuid

import pytest


@pytest.fixture(autouse=True)
def _isolated_runtime(monkeypatch):
    monkeypatch.setenv("TAVIDM_BOOTSTRAP_ADMIN_PASSWORD", "ExportTest-" + uuid.uuid4().hex)
    monkeypatch.setenv("TAVIDM_DISABLE_BACKGROUND_WORKERS", "1")
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")


def _insert_cases(adapter, video_id: int, count: int) -> None:
    conn = adapter.get_connection()
    try:
        conn.executemany(
            "INSERT INTO violations(video_id, track_id, violation_type, confidence, detected_at, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                (video_id, index, "Illegal Parking", 0.8, f"2026-01-01 00:{index // 60:02d}:{index % 60:02d}", "pending")
                for index in range(1, count + 1)
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_exact_cap_exports_all_rows_and_next_match_rejects_before_artifacts(
    test_db, tmp_path, monkeypatch
):
    from core import reports
    import config

    video_id = test_db.insert_video("cap.mp4", "/tmp/cap.mp4", status="processed")
    _insert_cases(test_db, video_id, reports._MAX_ROWS)
    monkeypatch.setattr(config, "REPORTS_FOLDER", tmp_path)
    monkeypatch.setattr(reports, "REPORTS_FOLDER", tmp_path)
    monkeypatch.setattr(reports, "_enrich_report_row", lambda row: row)
    rows = reports._fetch_rows({})
    assert len(rows) == reports._MAX_ROWS
    assert len({row["id"] for row in rows}) == reports._MAX_ROWS

    test_db.insert_violation(video_id, reports._MAX_ROWS + 1, "Illegal Parking", 0.8, 1, 1.0)
    with pytest.raises(reports.ReportTooLargeError) as caught:
        reports.generate_report("pdf")
    assert caught.value.total_cases == reports._MAX_ROWS + 1
    assert list(tmp_path.iterdir()) == []
    assert test_db.list_reports() == []


def test_count_rows_and_policy_outcome_metadata_share_wal_snapshot(
    test_db, test_db_path, monkeypatch
):
    from database import sqlite_adapter
    from core import reports

    enrich_snapshot_row = reports._enrich_report_row
    monkeypatch.setattr(reports, "_enrich_report_row", lambda row: row)

    video_id = test_db.insert_video("snapshot.mp4", "/tmp/snapshot.mp4", status="processed")
    _insert_cases(test_db, video_id, 5000)
    conn = sqlite_adapter.get_connection()
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        first_case = conn.execute("SELECT id FROM violations ORDER BY id LIMIT 1").fetchone()[0]
        policy_version_id = conn.execute(
            "INSERT INTO legal_policy_versions(version, status) VALUES ('test-v1', 'approved')"
        ).lastrowid
        conn.execute(
            "INSERT INTO case_policy_records(violation_id, policy_version_id, canonical_rule, official_category, legal_status) "
            "VALUES (?, ?, 'parking', 'Test category', 'verified')",
            (first_case, policy_version_id),
        )
        conn.execute(
            "INSERT INTO case_action_events(violation_id, action_type) VALUES (?, 'case_confirmed')",
            (first_case,),
        )
        conn.commit()
    finally:
        conn.close()

    count_seen = threading.Event()
    writer_done = threading.Event()
    original_get_connection = sqlite_adapter.get_connection

    class CursorGate:
        def __init__(self, cursor):
            self.cursor = cursor

        def fetchone(self):
            row = self.cursor.fetchone()
            count_seen.set()
            assert writer_done.wait(10), "concurrent writer did not finish"
            return row

        def __getattr__(self, name):
            return getattr(self.cursor, name)

    class ConnectionGate:
        def __init__(self, conn):
            self.conn = conn

        def execute(self, sql, *args):
            cursor = self.conn.execute(sql, *args)
            if threading.current_thread().name == "snapshot-reader" and "SELECT COUNT(*) AS total FROM violations" in sql:
                return CursorGate(cursor)
            return cursor

        def __getattr__(self, name):
            return getattr(self.conn, name)

    monkeypatch.setattr(
        sqlite_adapter,
        "get_connection",
        lambda: ConnectionGate(original_get_connection())
        if threading.current_thread().name == "snapshot-reader"
        else original_get_connection(),
    )
    result_box = {}
    errors = []

    def read_snapshot():
        try:
            from core import reports

            result_box["value"] = reports._fetch_rows({})
        except BaseException as exc:  # propagate thread failures to the assertion below
            errors.append(exc)

    reader = threading.Thread(target=read_snapshot, name="snapshot-reader")
    reader.start()
    assert count_seen.wait(10), "reader did not reach the count query"
    writer = original_get_connection()
    try:
        writer.execute(
            "INSERT INTO violations(video_id, track_id, violation_type, confidence, detected_at, status) "
            "VALUES (?, 6000, 'Illegal Parking', 0.9, '2026-01-02 00:00:00', 'pending')",
            (video_id,),
        )
        writer.execute(
            "INSERT INTO case_policy_records(violation_id, policy_version_id, canonical_rule, official_category, legal_status) "
            "VALUES (?, ?, 'new-rule', 'Later category', 'verified')",
            (first_case, policy_version_id),
        )
        writer.execute(
            "INSERT INTO case_action_events(violation_id, action_type) VALUES (?, 'notice_printed')",
            (first_case,),
        )
        writer.commit()
    finally:
        writer.close()
        writer_done.set()
    reader.join(15)
    assert not reader.is_alive(), "snapshot reader did not finish"
    assert not errors, repr(errors)

    snapshot = result_box["value"]
    assert len(snapshot) == 5000
    assert 6000 not in {row["track_id"] for row in snapshot}
    case = next(row for row in snapshot if row["id"] == first_case)
    assert [policy["canonical_rule"] for policy in case["policy_records"]] == ["parking"]
    assert case["case_action_types"] == ["case_confirmed"]
    assert case["case_outcome"] == "case_confirmed"
    monkeypatch.setattr(reports.db, "get_case_policy_records", lambda _case_id: pytest.fail("post-snapshot policy read"))
    monkeypatch.setattr(reports.db, "is_case_confirmed", lambda _case_id: pytest.fail("post-snapshot action read"))
    monkeypatch.setattr(reports.db, "is_notice_printed", lambda _case_id: pytest.fail("post-snapshot action read"))
    enriched = enrich_snapshot_row(case)
    assert enriched["official_category"] == "parking: Test category [verified]"
    assert enriched["legal_status"] == "parking: verified"
    assert enriched["case_outcome"] == "case_confirmed"
