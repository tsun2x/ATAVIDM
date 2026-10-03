"""Regressions in review-decision grouping, reuse, retention, and legacy routes."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
from pathlib import Path

import pytest

from core.case_review_service import materialize_case_from_review
from core.detection_config import (
    VIOLATION_COUNTERFLOW,
    VIOLATION_ILLEGAL_PARKING,
    VIOLATION_OBSTRUCTION,
)
from core.review_decision_service import (
    DECISION_CONFIRM,
    DECISION_CORRECT,
    DECISION_INSUFFICIENT,
    DECISION_NO_VIOLATION,
    ReviewDecisionConflict,
    apply_review_decision,
)

LEGACY_CONFIRM_REASON = "Legacy confirm action. No reviewer reason was supplied."
LEGACY_DISMISS_REASON = "Legacy dismiss action. No reviewer reason was supplied."
from database.sqlite_adapter import TemporalEvidenceNotReady


@pytest.fixture
def test_db_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_tavidm.db")
        os.environ["SQLITE_PATH"] = db_path
        os.environ["DATABASE_URL"] = db_path
        yield db_path


@pytest.fixture
def test_db(test_db_path):
    from database import sqlite_adapter

    sqlite_adapter.init_db(force=True)
    return sqlite_adapter


@pytest.fixture
def enforcer(test_db):
    return test_db.create_user("enf", "hash", role="enforcer", full_name="Enforcer")


@pytest.fixture
def viewer(test_db):
    return test_db.create_user("view", "hash", role="viewer", full_name="Viewer")


def _review(test_db, violation_type, **kwargs):
    video_id = kwargs.pop("video_id", None)
    if video_id is None:
        video_id = test_db.insert_video("clip.mp4", "/tmp/clip.mp4", status="ready")
    defaults = dict(
        video_id=video_id,
        track_id=7,
        violation_type=violation_type,
        confidence=0.91,
        frame_number=12,
        timestamp_sec=4.0,
        episode_start_sec=1.0,
        episode_end_sec=8.0,
        evidence_path="/tmp/scene.jpg",
        reason_log="system suggestion",
        processing_run_id=1,
    )
    defaults.update(kwargs)
    return video_id, test_db.insert_review_queue(**defaults)


def _rules(test_db, violation_id):
    return {row["canonical_rule"] for row in test_db.get_case_policy_records(violation_id)}


def _policy_versions(test_db, violation_id):
    return {row["policy_version_id"] for row in test_db.get_case_policy_records(violation_id)}


def _decide(test_db, review_id, enforcer, **kwargs):
    return apply_review_decision(test_db, review_id, enforcer, **kwargs)


def test_correction_survives_later_overlapping_obstruction(test_db, enforcer):
    video_id, parking_id = _review(test_db, VIOLATION_ILLEGAL_PARKING)
    corrected = _decide(
        test_db,
        parking_id,
        enforcer,
        decision=DECISION_CORRECT,
        selected_canonical_rule=VIOLATION_COUNTERFLOW,
        reason="The vehicle is opposing traffic, not parked.",
        idempotency_key="corr-park",
    )
    case_id = corrected["violation_id"]
    versions_before = _policy_versions(test_db, case_id)
    _, obstruction_id = _review(test_db, VIOLATION_OBSTRUCTION, video_id=video_id)
    confirmed = _decide(
        test_db,
        obstruction_id,
        enforcer,
        decision=DECISION_CONFIRM,
        reason="The vehicle is blocking the traveled lane.",
        idempotency_key="obs-confirm",
    )
    assert confirmed["violation_id"] != case_id
    assert confirmed["fused"] is False
    assert confirmed["reused_existing"] is False
    assert test_db.get_review_item(parking_id)["violation_type"] == VIOLATION_ILLEGAL_PARKING
    assert test_db.get_violation(case_id)["violation_type"] == VIOLATION_COUNTERFLOW
    assert _rules(test_db, case_id) == {VIOLATION_COUNTERFLOW}
    assert VIOLATION_ILLEGAL_PARKING not in _rules(test_db, case_id)
    assert _rules(test_db, confirmed["violation_id"]) == {VIOLATION_OBSTRUCTION}
    assert _policy_versions(test_db, case_id) == versions_before
    assert test_db.get_review_decision(parking_id)["selected_canonical_rule"] == (
        VIOLATION_COUNTERFLOW
    )


def test_legacy_materialization_after_correction_keeps_selected_rule(test_db, enforcer):
    _, review_id = _review(test_db, VIOLATION_COUNTERFLOW)
    corrected = _decide(
        test_db,
        review_id,
        enforcer,
        decision=DECISION_CORRECT,
        selected_canonical_rule=VIOLATION_OBSTRUCTION,
        reason="Stopped in the lane rather than opposing flow.",
        idempotency_key="corr-flow",
    )
    case_id = corrected["violation_id"]
    versions_before = _policy_versions(test_db, case_id)
    recovered = materialize_case_from_review(test_db, review_id, enforcer)
    assert recovered["violation_id"] == case_id
    assert _rules(test_db, case_id) == {VIOLATION_OBSTRUCTION}
    assert VIOLATION_COUNTERFLOW not in _rules(test_db, case_id)
    assert test_db.get_violation(case_id)["violation_type"] == VIOLATION_OBSTRUCTION
    assert test_db.get_review_item(review_id)["violation_type"] == VIOLATION_COUNTERFLOW
    assert _policy_versions(test_db, case_id) == versions_before


def test_no_case_decision_does_not_reenter_fusion(test_db, enforcer):
    video_id, parking_id = _review(test_db, VIOLATION_ILLEGAL_PARKING)
    _decide(
        test_db,
        parking_id,
        enforcer,
        decision=DECISION_NO_VIOLATION,
        reason="The vehicle was moving.",
        idempotency_key="no-park",
    )
    with test_db.db_session() as conn:
        conn.execute(
            "UPDATE review_queue SET status = 'pending' WHERE id = ?",
            (parking_id,),
        )
    _, obstruction_id = _review(test_db, VIOLATION_OBSTRUCTION, video_id=video_id)
    confirmed = _decide(
        test_db,
        obstruction_id,
        enforcer,
        decision=DECISION_CONFIRM,
        reason="The vehicle is blocking the lane.",
        idempotency_key="obs-only",
    )
    assert confirmed["fused"] is False
    assert parking_id not in confirmed["review_ids"]
    assert _rules(test_db, confirmed["violation_id"]) == {VIOLATION_OBSTRUCTION}
    assert test_db.get_review_decision(parking_id)["decision"] == DECISION_NO_VIOLATION
    _, total = test_db.list_violations(per_page=20)
    assert total == 1


def test_historical_parking_and_obstruction_still_fuse(test_db, enforcer):
    video_id, parking_id = _review(test_db, VIOLATION_ILLEGAL_PARKING)
    first = materialize_case_from_review(test_db, parking_id, enforcer)
    _, obstruction_id = _review(test_db, VIOLATION_OBSTRUCTION, video_id=video_id)
    second = materialize_case_from_review(test_db, obstruction_id, enforcer)
    assert second["reused_existing"] is True
    assert second["fused"] is True
    assert second["violation_id"] == first["violation_id"]
    assert _rules(test_db, first["violation_id"]) == {
        VIOLATION_ILLEGAL_PARKING,
        VIOLATION_OBSTRUCTION,
    }
    assert test_db.get_review_decision(parking_id) is None
    assert test_db.get_review_decision(obstruction_id) is None


def test_reused_case_decision_failure_leaves_review_pending(test_db, enforcer, monkeypatch):
    video_id, parking_id = _review(test_db, VIOLATION_ILLEGAL_PARKING)
    parked = _decide(
        test_db,
        parking_id,
        enforcer,
        decision=DECISION_CONFIRM,
        reason="Parked in the no-parking zone.",
        idempotency_key="park-ok",
    )
    rules_before = _rules(test_db, parked["violation_id"])
    _, obstruction_id = _review(test_db, VIOLATION_OBSTRUCTION, video_id=video_id)
    real_insert = test_db.insert_review_decision

    def boom(*_args, **_kwargs):
        raise RuntimeError("review decision boom")

    monkeypatch.setattr(test_db, "insert_review_decision", boom)
    with pytest.raises(RuntimeError, match="review decision boom"):
        _decide(
            test_db,
            obstruction_id,
            enforcer,
            decision=DECISION_CONFIRM,
            reason="Blocking the traveled lane.",
            idempotency_key="obs-boom",
        )
    row = test_db.get_review_item(obstruction_id)
    assert row["status"] == "pending"
    assert row["reviewed_by"] is None
    assert test_db.get_review_decision(obstruction_id) is None
    assert test_db.find_violation_linked_to_review(obstruction_id) is None
    assert _rules(test_db, parked["violation_id"]) == rules_before
    _, total = test_db.list_violations(per_page=20)
    assert total == 1

    monkeypatch.setattr(test_db, "insert_review_decision", real_insert)
    retried = _decide(
        test_db,
        obstruction_id,
        enforcer,
        decision=DECISION_CONFIRM,
        reason="Blocking the traveled lane.",
        idempotency_key="obs-boom",
    )
    assert retried["violation_id"] == parked["violation_id"]
    assert retried["fused"] is True
    assert retried["reused_existing"] is True
    assert test_db.get_review_item(obstruction_id)["status"] == "confirmed"
    assert test_db.get_review_decision(obstruction_id)["decision"] == DECISION_CONFIRM
    assert _rules(test_db, parked["violation_id"]) == {
        VIOLATION_ILLEGAL_PARKING,
        VIOLATION_OBSTRUCTION,
    }
    again = _decide(
        test_db,
        obstruction_id,
        enforcer,
        decision=DECISION_CONFIRM,
        reason="Blocking the traveled lane.",
        idempotency_key="obs-boom",
    )
    assert again["idempotent_retry"] is True
    assert again["violation_id"] == parked["violation_id"]
    assert len(test_db.list_recent_review_decisions(10)) == 2


def test_new_case_decision_failure_rolls_back_and_retries(test_db, enforcer, monkeypatch):
    _, review_id = _review(test_db, VIOLATION_COUNTERFLOW)
    real = test_db.create_case_with_materialization_intent

    def boom(*args, **kwargs):
        kwargs["_fail_after"] = "review_decision"
        return real(*args, **kwargs)

    monkeypatch.setattr(test_db, "create_case_with_materialization_intent", boom)
    with pytest.raises(RuntimeError, match="review decision boom"):
        _decide(
            test_db,
            review_id,
            enforcer,
            decision=DECISION_CONFIRM,
            reason="Opposing movement is visible.",
            idempotency_key="new-boom",
        )
    assert test_db.get_review_item(review_id)["status"] == "pending"
    assert test_db.get_review_decision(review_id) is None
    _, total = test_db.list_violations(per_page=20)
    assert total == 0

    monkeypatch.setattr(test_db, "create_case_with_materialization_intent", real)
    saved = _decide(
        test_db,
        review_id,
        enforcer,
        decision=DECISION_CONFIRM,
        reason="Opposing movement is visible.",
        idempotency_key="new-boom",
    )
    assert saved["violation_id"]
    before = test_db.get_review_item(review_id)
    decision = test_db.get_review_decision(review_id)
    with pytest.raises(ReviewDecisionConflict):
        _decide(
            test_db,
            review_id,
            enforcer,
            decision=DECISION_NO_VIOLATION,
            reason="Changed my mind.",
            idempotency_key="new-conflict",
        )
    assert test_db.get_review_item(review_id)["status"] == before["status"]
    assert test_db.get_review_item(review_id)["reviewed_at"] == before["reviewed_at"]
    assert test_db.get_review_decision(review_id)["id"] == decision["id"]
    assert test_db.get_review_decision(review_id)["decision"] == DECISION_CONFIRM


def test_conflicting_reuse_decision_does_not_mutate(test_db, enforcer):
    video_id, parking_id = _review(test_db, VIOLATION_ILLEGAL_PARKING)
    parked = _decide(
        test_db,
        parking_id,
        enforcer,
        decision=DECISION_CONFIRM,
        reason="Parked.",
        idempotency_key="park",
    )
    _, obstruction_id = _review(test_db, VIOLATION_OBSTRUCTION, video_id=video_id)
    saved = _decide(
        test_db,
        obstruction_id,
        enforcer,
        decision=DECISION_CONFIRM,
        reason="Blocking.",
        idempotency_key="obs",
    )
    before_row = test_db.get_review_item(obstruction_id)
    before_decision = test_db.get_review_decision(obstruction_id)
    before_rules = _rules(test_db, parked["violation_id"])
    before_actions = len(test_db.get_case_actions(parked["violation_id"]))
    with pytest.raises(ReviewDecisionConflict):
        _decide(
            test_db,
            obstruction_id,
            enforcer,
            decision=DECISION_NO_VIOLATION,
            reason="Not a violation.",
            idempotency_key="obs-no",
        )
    after_row = test_db.get_review_item(obstruction_id)
    after_decision = test_db.get_review_decision(obstruction_id)
    assert after_row["status"] == before_row["status"] == "confirmed"
    assert after_row["reviewed_by"] == before_row["reviewed_by"]
    assert after_row["reviewed_at"] == before_row["reviewed_at"]
    assert after_decision["id"] == before_decision["id"]
    assert after_decision["decision"] == DECISION_CONFIRM
    assert _rules(test_db, parked["violation_id"]) == before_rules
    assert len(test_db.get_case_actions(parked["violation_id"])) == before_actions
    assert saved["violation_id"] == parked["violation_id"]


def test_concurrent_case_and_no_case_on_fused_reuse(test_db, enforcer):
    video_id, parking_id = _review(test_db, VIOLATION_ILLEGAL_PARKING)
    parked = _decide(
        test_db,
        parking_id,
        enforcer,
        decision=DECISION_CONFIRM,
        reason="Parked.",
        idempotency_key="park-race",
    )
    _, obstruction_id = _review(test_db, VIOLATION_OBSTRUCTION, video_id=video_id)
    barrier = threading.Barrier(2)
    outcomes = []

    def _run(decision, key, reason):
        barrier.wait()
        try:
            result = _decide(
                test_db,
                obstruction_id,
                enforcer,
                decision=decision,
                reason=reason,
                idempotency_key=key,
            )
            outcomes.append(result["decision"])
        except ReviewDecisionConflict:
            outcomes.append("conflict")

    threads = [
        threading.Thread(
            target=_run,
            args=(DECISION_CONFIRM, "race-case", "Blocking the lane."),
        ),
        threading.Thread(
            target=_run,
            args=(DECISION_NO_VIOLATION, "race-none", "Not a violation."),
        ),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert "conflict" in outcomes
    assert len(outcomes) == 2
    decision = test_db.get_review_decision(obstruction_id)
    assert decision is not None
    assert decision["decision"] in {DECISION_CONFIRM, DECISION_NO_VIOLATION}
    _, total = test_db.list_violations(per_page=20)
    assert total == 1
    assert test_db.get_violation(parked["violation_id"])["status"] != "dismissed"
    if decision["decision"] == DECISION_CONFIRM:
        assert test_db.get_review_item(obstruction_id)["status"] == "confirmed"
        assert test_db.find_violation_linked_to_review(obstruction_id) == parked["violation_id"]
        assert _rules(test_db, parked["violation_id"]) == {
            VIOLATION_ILLEGAL_PARKING,
            VIOLATION_OBSTRUCTION,
        }
    else:
        assert test_db.get_review_item(obstruction_id)["status"] == "dismissed"
        assert test_db.find_violation_linked_to_review(obstruction_id) is None
        assert _rules(test_db, parked["violation_id"]) == {VIOLATION_ILLEGAL_PARKING}
    assert len(test_db.list_recent_review_decisions(10)) == 2


def test_no_case_decisions_survive_result_removal_and_video_deletion(test_db, enforcer):
    video_id, weak_id = _review(test_db, VIOLATION_COUNTERFLOW, track_id=3)
    saved = _decide(
        test_db,
        weak_id,
        enforcer,
        decision=DECISION_INSUFFICIENT,
        reason="The frames do not show the maneuver.",
        idempotency_key="weak",
    )
    _, none_id = _review(test_db, VIOLATION_OBSTRUCTION, video_id=video_id, track_id=4)
    _decide(
        test_db,
        none_id,
        enforcer,
        decision=DECISION_NO_VIOLATION,
        reason="The vehicle kept moving.",
        idempotency_key="none",
    )
    test_db.delete_unconfirmed_results_for_video(video_id)
    assert test_db.get_review_item(weak_id) is None
    assert test_db.get_review_item(none_id) is None
    kept = test_db.get_review_decision(weak_id)
    assert kept["id"] == saved["review_decision_id"]
    assert kept["decision"] == DECISION_INSUFFICIENT
    assert kept["original_violation_type"] == VIOLATION_COUNTERFLOW
    assert kept["source_video_id"] == video_id
    assert json.loads(kept["evidence_refs_json"])["evidence_path"] == "/tmp/scene.jpg"
    recent = test_db.list_recent_review_decisions(10)
    assert {row["review_id"] for row in recent} == {weak_id, none_id}
    assert all(row["original_violation_type"] for row in recent)
    with test_db.db_session() as conn:
        total = conn.execute("SELECT COUNT(*) AS n FROM review_decisions").fetchone()["n"]
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE review_decisions SET reason = ? WHERE id = ?",
                ("rewritten", kept["id"]),
            )
    assert total == 2
    queued, count = test_db.list_review_queue_by_decision(DECISION_INSUFFICIENT)
    assert queued == []
    assert count == 0

    other_video, other_id = _review(test_db, VIOLATION_COUNTERFLOW, track_id=8)
    _decide(
        test_db,
        other_id,
        enforcer,
        decision=DECISION_NO_VIOLATION,
        reason="Permitted movement.",
        idempotency_key="other-none",
    )
    test_db.delete_video_cascade_unprotected(other_video)
    assert test_db.get_video(other_video) is None
    orphan = test_db.get_review_decision(other_id)
    assert orphan["decision"] == DECISION_NO_VIOLATION
    assert orphan["original_violation_type"] == VIOLATION_COUNTERFLOW
    assert orphan["source_video_id"] == other_video
    with test_db.db_session() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM review_decisions WHERE review_id = ?", (other_id,))


def test_confirmed_case_stays_protected_during_result_removal(test_db, enforcer):
    from core.video_lifecycle import VideoLifecycleError, remove_processing_results

    video_id, review_id = _review(test_db, VIOLATION_ILLEGAL_PARKING)
    saved = _decide(
        test_db,
        review_id,
        enforcer,
        decision=DECISION_CONFIRM,
        reason="Parked.",
        idempotency_key="keep",
    )
    with pytest.raises(VideoLifecycleError):
        remove_processing_results(video_id, is_busy=lambda _video_id: False)
    assert test_db.get_review_item(review_id)["status"] == "confirmed"
    assert test_db.get_violation(saved["violation_id"])["status"] == "confirmed"
    assert test_db.get_review_decision(review_id)["decision"] == DECISION_CONFIRM


def test_upgrade_from_012_retains_decisions_without_queue_fk(tmp_path, monkeypatch):
    from database import sqlite_adapter

    db_path = tmp_path / "upgrade.db"
    monkeypatch.setenv("SQLITE_PATH", str(db_path))
    monkeypatch.setenv("DATABASE_URL", str(db_path))
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
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
        INSERT INTO users (id, username, password_hash, role, is_active)
        VALUES (1, 'enf', 'hash', 'enforcer', 1);
        INSERT INTO review_queue (id, video_id, track_id, violation_type, status, processing_run_id)
        VALUES (9, 4, 7, 'Counterflow', 'dismissed', 1);
        """
    )
    migration_012 = Path("database/migrations/012_review_decisions.sql").read_text(encoding="utf-8")
    conn.executescript(migration_012)
    conn.execute(
        """
        INSERT INTO review_decisions (
            review_id, decision, original_violation_type, selected_canonical_rule,
            reason, reviewer_user_id, decided_at, evidence_refs_json,
            policy_refs_json, idempotency_key
        ) VALUES (9, 'insufficient_evidence', 'Counterflow', NULL,
                  'Unclear frames.', 1, '2026-10-02T00:00:00+00:00',
                  '{"evidence_path":"/tmp/scene.jpg"}', '{}', 'legacy-012')
        """
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM review_queue WHERE id = 9")
        conn.commit()
    conn.rollback()
    conn.close()

    upgrade = sqlite3.connect(db_path)
    try:
        sqlite_adapter._apply_migration_013(upgrade)
    finally:
        upgrade.close()
    check = sqlite3.connect(db_path)
    check.row_factory = sqlite3.Row
    check.execute("PRAGMA foreign_keys = ON")
    ddl = check.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'review_decisions'"
    ).fetchone()["sql"]
    assert "REFERENCES review_queue" not in ddl
    check.execute("DELETE FROM review_queue WHERE id = 9")
    kept = check.execute("SELECT * FROM review_decisions WHERE review_id = 9").fetchone()
    assert kept["decision"] == "insufficient_evidence"
    assert kept["original_violation_type"] == "Counterflow"
    assert kept["reason"] == "Unclear frames."
    assert kept["source_video_id"] == 4
    assert kept["source_track_id"] == 7
    with pytest.raises(sqlite3.IntegrityError):
        check.execute("DELETE FROM review_decisions WHERE review_id = 9")
    check.rollback()
    sqlite_adapter._apply_migration_013(check)
    assert check.execute("SELECT COUNT(*) AS n FROM review_decisions").fetchone()["n"] == 1
    check.close()


def _client(test_db):
    import bcrypt

    os.environ["TAVIDM_BOOTSTRAP_ADMIN_PASSWORD"] = "isolated-review-regression"
    import app as flask_app
    from core import auth

    auth.ensure_default_admin()
    password = bcrypt.hashpw(b"enforcer123", bcrypt.gensalt()).decode("utf-8")
    enforcer_id = test_db.create_user(
        "enforcer_ui", password, role="enforcer", full_name="UI Enforcer"
    )
    viewer_password = bcrypt.hashpw(b"viewer123", bcrypt.gensalt()).decode("utf-8")
    test_db.create_user("viewer_ui", viewer_password, role="viewer", full_name="UI Viewer")
    flask_app.app.config["TESTING"] = True
    flask_app.app.config["WTF_CSRF_ENABLED"] = False
    return flask_app, enforcer_id


def test_legacy_routes_follow_the_decision_contract(test_db, enforcer):
    flask_app, enforcer_id = _client(test_db)
    _, pending_id = _review(test_db, VIOLATION_OBSTRUCTION, track_id=11)
    _, dismiss_id = _review(test_db, VIOLATION_COUNTERFLOW, track_id=12)
    _, corrected_id = _review(test_db, VIOLATION_COUNTERFLOW, track_id=13)
    corrected = _decide(
        test_db,
        corrected_id,
        enforcer,
        decision=DECISION_CORRECT,
        selected_canonical_rule=VIOLATION_OBSTRUCTION,
        reason="Stopped in the lane.",
        idempotency_key="ui-correct",
    )
    _, gated_id = _review(
        test_db,
        VIOLATION_COUNTERFLOW,
        track_id=14,
        evidence_pre_sec=3.0,
        evidence_clip_path=None,
        episode_end_sec=None,
    )
    rules_before = _rules(test_db, corrected["violation_id"])
    corrected_row = test_db.get_review_item(corrected_id)
    try:
        with flask_app.app.test_client() as client:
            login = client.post(
                "/login",
                data={"username": "enforcer_ui", "password": "enforcer123"},
            )
            assert login.status_code in (200, 302)

            confirmed = client.post(f"/api/review-queue/{pending_id}/confirm")
            body = confirmed.get_json()
            assert confirmed.status_code == 200
            assert body["success"] is True
            assert body["decision"] == DECISION_CONFIRM
            assert body["violation_id"]
            stored = test_db.get_review_decision(pending_id)
            assert stored["reason"] == LEGACY_CONFIRM_REASON
            assert stored["reviewer_user_id"] == enforcer_id
            assert stored["selected_canonical_rule"] == VIOLATION_OBSTRUCTION
            retry = client.post(f"/api/review-queue/{pending_id}/confirm")
            assert retry.status_code == 200
            assert retry.get_json()["idempotent_retry"] is True
            assert retry.get_json()["review_decision_id"] == stored["id"]

            dismissed = client.post(f"/api/review-queue/{dismiss_id}/dismiss")
            assert dismissed.status_code == 200
            assert dismissed.get_json()["success"] is True
            assert dismissed.get_json()["decision"] == DECISION_NO_VIOLATION
            none_row = test_db.get_review_decision(dismiss_id)
            assert none_row["reason"] == LEGACY_DISMISS_REASON
            assert none_row["reviewer_user_id"] == enforcer_id
            assert none_row["selected_canonical_rule"] is None
            assert test_db.get_review_item(dismiss_id)["violation_type"] == VIOLATION_COUNTERFLOW
            dismiss_retry = client.post(f"/api/review-queue/{dismiss_id}/dismiss")
            assert dismiss_retry.status_code == 200
            assert dismiss_retry.get_json()["idempotent_retry"] is True

            conflict_confirm = client.post(f"/api/review-queue/{corrected_id}/confirm")
            assert conflict_confirm.status_code == 409
            conflict_corrected_dismiss = client.post(f"/api/review-queue/{corrected_id}/dismiss")
            assert conflict_corrected_dismiss.status_code == 409
            conflict_dismiss = client.post(f"/api/review-queue/{dismiss_id}/confirm")
            assert conflict_dismiss.status_code == 409
            conflict_back = client.post(f"/api/review-queue/{pending_id}/dismiss")
            assert conflict_back.status_code == 409
            assert test_db.get_review_item(corrected_id)["status"] == corrected_row["status"]
            assert test_db.get_review_item(corrected_id)["reviewed_at"] == corrected_row["reviewed_at"]
            assert test_db.get_review_item(corrected_id)["reviewed_by"] == corrected_row["reviewed_by"]
            assert test_db.get_review_decision(corrected_id)["decision"] == DECISION_CORRECT
            assert _rules(test_db, corrected["violation_id"]) == rules_before
            assert test_db.get_review_decision(pending_id)["decision"] == DECISION_CONFIRM
            assert test_db.get_review_item(pending_id)["status"] == "confirmed"

            gated = client.post(f"/api/review-queue/{gated_id}/confirm")
            assert gated.status_code == 409
            assert test_db.get_review_item(gated_id)["status"] == "pending"
            assert test_db.get_review_decision(gated_id) is None

            client.post("/logout")
            viewer_login = client.post(
                "/login",
                data={"username": "viewer_ui", "password": "viewer123"},
            )
            assert viewer_login.status_code in (200, 302)
            _, viewer_review = _review(test_db, VIOLATION_COUNTERFLOW, track_id=15)
            assert client.post(f"/api/review-queue/{viewer_review}/confirm").status_code == 403
            assert client.post(f"/api/review-queue/{viewer_review}/dismiss").status_code == 403
            assert test_db.get_review_decision(viewer_review) is None

            client.post("/logout")
            client.post(
                "/login",
                data={"username": "enforcer_ui", "password": "enforcer123"},
            )
            test_db.update_user(enforcer_id, is_active=False)
            _, inactive_review = _review(test_db, VIOLATION_COUNTERFLOW, track_id=16)
            assert client.post(f"/api/review-queue/{inactive_review}/confirm").status_code == 401
            assert client.post(f"/api/review-queue/{inactive_review}/dismiss").status_code == 401
            assert test_db.get_review_decision(inactive_review) is None
    finally:
        flask_app.stop_processing_worker()


def test_inactive_user_cannot_apply_a_decision(test_db, enforcer):
    test_db.update_user(enforcer, is_active=False)
    _, review_id = _review(test_db, VIOLATION_COUNTERFLOW)
    with pytest.raises(PermissionError):
        _decide(
            test_db,
            review_id,
            enforcer,
            decision=DECISION_INSUFFICIENT,
            reason="Unclear.",
            idempotency_key="inactive",
        )
    assert test_db.get_review_item(review_id)["status"] == "pending"


def test_viewer_service_rejection(test_db, viewer):
    _, review_id = _review(test_db, VIOLATION_COUNTERFLOW)
    with pytest.raises(PermissionError):
        _decide(
            test_db,
            review_id,
            viewer,
            decision=DECISION_NO_VIOLATION,
            reason="Unclear.",
            idempotency_key="viewer-direct",
        )
