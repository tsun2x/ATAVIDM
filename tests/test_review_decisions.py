"""Review-queue human decisions: confirm, correct, no violation, insufficient evidence."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import threading

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
    ReviewDecisionError,
    apply_review_decision,
    disposition_label,
)
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


def test_labels_keep_insufficient_evidence_distinct():
    assert disposition_label(DECISION_INSUFFICIENT) == "Insufficient evidence"
    assert disposition_label(DECISION_NO_VIOLATION) == "No violation"
    assert disposition_label(DECISION_INSUFFICIENT) != disposition_label(DECISION_NO_VIOLATION)


def test_insufficient_evidence_closes_without_a_case(test_db, enforcer):
    _, review_id = _review(test_db, VIOLATION_COUNTERFLOW)
    before, _ = test_db.list_violations(per_page=50)
    result = apply_review_decision(
        test_db,
        review_id,
        enforcer,
        decision=DECISION_INSUFFICIENT,
        reason="The clip does not show the claimed movement.",
        idempotency_key="ins-1",
    )
    after, total = test_db.list_violations(per_page=50)
    row = test_db.get_review_item(review_id)
    stored = test_db.get_review_decision(review_id)
    assert result["creates_case"] is False
    assert result["violation_id"] is None
    assert result["disposition_label"] == "Insufficient evidence"
    assert result["disposition_label"] != "No violation"
    assert len(after) == len(before)
    assert total == 0
    assert row["status"] == "dismissed"
    assert row["violation_type"] == VIOLATION_COUNTERFLOW
    assert stored["original_violation_type"] == VIOLATION_COUNTERFLOW
    assert stored["selected_canonical_rule"] is None
    assert stored["reviewer_user_id"] == enforcer
    pending, _ = test_db.list_review_queue(status="pending")
    assert review_id not in {item["id"] for item in pending}
    closed, count = test_db.list_review_queue_by_decision(DECISION_INSUFFICIENT)
    assert count == 1
    assert closed[0]["human_decision"] == DECISION_INSUFFICIENT
    assert closed[0]["human_decision"] != DECISION_NO_VIOLATION


def test_no_violation_is_not_insufficient_evidence(test_db, enforcer):
    _, review_id = _review(test_db, VIOLATION_OBSTRUCTION)
    result = apply_review_decision(
        test_db,
        review_id,
        enforcer,
        decision=DECISION_NO_VIOLATION,
        reason="The vehicle kept moving in the permitted lane.",
        idempotency_key="none-1",
    )
    assert result["disposition_label"] == "No violation"
    assert result["creates_case"] is False
    _, total = test_db.list_violations(per_page=20)
    assert total == 0
    assert test_db.get_review_item(review_id)["violation_type"] == VIOLATION_OBSTRUCTION


def test_correction_uses_selected_rule_and_keeps_original(test_db, enforcer):
    _, review_id = _review(test_db, VIOLATION_COUNTERFLOW)
    result = apply_review_decision(
        test_db,
        review_id,
        enforcer,
        decision=DECISION_CORRECT,
        selected_canonical_rule=VIOLATION_OBSTRUCTION,
        reason="The track is stopped in the active lane, not opposing flow.",
        idempotency_key="corr-1",
    )
    row = test_db.get_review_item(review_id)
    violation = test_db.get_violation(result["violation_id"])
    records = test_db.get_case_policy_records(result["violation_id"])
    stored = test_db.get_review_decision(review_id)
    assert row["violation_type"] == VIOLATION_COUNTERFLOW
    assert row["status"] == "confirmed"
    assert violation["violation_type"] == VIOLATION_OBSTRUCTION
    assert stored["original_violation_type"] == VIOLATION_COUNTERFLOW
    assert stored["selected_canonical_rule"] == VIOLATION_OBSTRUCTION
    assert {record["canonical_rule"] for record in records} == {VIOLATION_OBSTRUCTION}
    policy = __import__("json").loads(stored["policy_refs_json"])
    assert policy["computed_before_materialization"] is True
    assert policy["primary_canonical_rule"] == VIOLATION_OBSTRUCTION
    assert policy["snapshots"][0]["canonical_rule"] == VIOLATION_OBSTRUCTION


def test_correction_groups_with_selected_rule_not_original(test_db, enforcer):
    video_id, review_id = _review(test_db, VIOLATION_COUNTERFLOW)
    _, sibling_id = _review(
        test_db,
        VIOLATION_OBSTRUCTION,
        video_id=video_id,
        track_id=7,
        processing_run_id=1,
    )
    result = apply_review_decision(
        test_db,
        review_id,
        enforcer,
        decision=DECISION_CORRECT,
        selected_canonical_rule=VIOLATION_ILLEGAL_PARKING,
        reason="Stopped in a no-parking area; the sibling is the obstruction observation.",
        idempotency_key="fuse-1",
    )
    assert result["fused"] is True
    assert set(result["contributing_rules"]) == {
        VIOLATION_ILLEGAL_PARKING,
        VIOLATION_OBSTRUCTION,
    }
    assert test_db.get_review_item(review_id)["violation_type"] == VIOLATION_COUNTERFLOW
    records = test_db.get_case_policy_records(result["violation_id"])
    assert {record["canonical_rule"] for record in records} == {
        VIOLATION_ILLEGAL_PARKING,
        VIOLATION_OBSTRUCTION,
    }


def test_duplicate_retry_does_not_create_another_case(test_db, enforcer):
    _, review_id = _review(test_db, VIOLATION_COUNTERFLOW)
    kwargs = dict(
        decision=DECISION_CONFIRM,
        reason="The opposing movement is visible.",
        idempotency_key="same-key",
    )
    first = apply_review_decision(test_db, review_id, enforcer, **kwargs)
    second = apply_review_decision(test_db, review_id, enforcer, **kwargs)
    _, total = test_db.list_violations(per_page=20)
    assert first["violation_id"] == second["violation_id"]
    assert second["idempotent_retry"] is True
    assert total == 1
    assert test_db.list_recent_review_decisions(10)[0]["id"] == first["review_decision_id"]


def test_different_decision_conflicts(test_db, enforcer):
    _, review_id = _review(test_db, VIOLATION_COUNTERFLOW)
    apply_review_decision(
        test_db,
        review_id,
        enforcer,
        decision=DECISION_NO_VIOLATION,
        reason="Permitted movement.",
        idempotency_key="first",
    )
    with pytest.raises(ReviewDecisionConflict):
        apply_review_decision(
            test_db,
            review_id,
            enforcer,
            decision=DECISION_INSUFFICIENT,
            reason="Actually the frames are unclear.",
            idempotency_key="second",
        )


def test_temporal_gate_blocks_confirmation(test_db, enforcer):
    _, review_id = _review(
        test_db,
        VIOLATION_COUNTERFLOW,
        evidence_pre_sec=3.0,
        evidence_clip_path=None,
        episode_end_sec=None,
    )
    with pytest.raises(TemporalEvidenceNotReady):
        apply_review_decision(
            test_db,
            review_id,
            enforcer,
            decision=DECISION_CONFIRM,
            reason="Ready to confirm.",
            idempotency_key="gate",
        )
    assert test_db.get_review_item(review_id)["status"] == "pending"
    assert test_db.get_review_decision(review_id) is None


def test_viewer_cannot_decide(test_db, viewer):
    _, review_id = _review(test_db, VIOLATION_COUNTERFLOW)
    with pytest.raises(PermissionError):
        apply_review_decision(
            test_db,
            review_id,
            viewer,
            decision=DECISION_INSUFFICIENT,
            reason="Unclear.",
            idempotency_key="viewer",
        )


def test_close_rolls_back_when_decision_insert_fails(test_db, enforcer):
    _, review_id = _review(test_db, VIOLATION_COUNTERFLOW)
    with pytest.raises(RuntimeError, match="review decision boom"):
        test_db.close_review_with_decision(
            review_id,
            enforcer,
            decision=DECISION_INSUFFICIENT,
            original_violation_type=VIOLATION_COUNTERFLOW,
            reason="Unclear frames.",
            idempotency_key="rollback-close",
            evidence_refs={"evidence_path": "/tmp/scene.jpg"},
            policy_refs={"materialized": False},
            _fail_after="decision",
        )
    assert test_db.get_review_item(review_id)["status"] == "pending"
    assert test_db.get_review_decision(review_id) is None


def test_case_decision_rolls_back_with_the_violation(test_db, enforcer):
    _, review_id = _review(test_db, VIOLATION_COUNTERFLOW)
    policy_id = test_db.ensure_config_mapping_policy_version()
    with pytest.raises(RuntimeError, match="review decision boom"):
        test_db.create_case_with_materialization_intent(
            review_id,
            enforcer,
            policy_version_id=policy_id,
            contributing_rules=[VIOLATION_OBSTRUCTION],
            review_ids=[review_id],
            violation_type_override=VIOLATION_OBSTRUCTION,
            decision_insert={
                "review_id": review_id,
                "decision": DECISION_CORRECT,
                "original_violation_type": VIOLATION_COUNTERFLOW,
                "selected_canonical_rule": VIOLATION_OBSTRUCTION,
                "reason": "Stopped in lane.",
                "reviewer_user_id": enforcer,
                "idempotency_key": "rollback-case",
                "evidence_refs": {"evidence_path": "/tmp/scene.jpg"},
                "policy_refs": {"computed_before_materialization": True},
            },
            _fail_after="review_decision",
        )
    row = test_db.get_review_item(review_id)
    _, total = test_db.list_violations(per_page=20)
    assert row["status"] == "pending"
    assert row["violation_type"] == VIOLATION_COUNTERFLOW
    assert test_db.get_review_decision(review_id) is None
    assert total == 0


def test_decision_history_is_append_only(test_db, enforcer):
    _, review_id = _review(test_db, VIOLATION_COUNTERFLOW)
    apply_review_decision(
        test_db,
        review_id,
        enforcer,
        decision=DECISION_CONFIRM,
        reason="Visible opposing movement.",
        idempotency_key="append",
    )
    with test_db.db_session() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE review_decisions SET reason = ? WHERE review_id = ?",
                ("rewritten", review_id),
            )


def test_concurrent_decisions_keep_one_outcome(test_db, enforcer):
    _, review_id = _review(test_db, VIOLATION_COUNTERFLOW)
    barrier = threading.Barrier(2)
    outcomes = []

    def _run(decision, key):
        barrier.wait()
        try:
            outcomes.append(
                apply_review_decision(
                    test_db,
                    review_id,
                    enforcer,
                    decision=decision,
                    reason="Concurrent reviewer note.",
                    idempotency_key=key,
                )["decision"]
            )
        except ReviewDecisionConflict:
            outcomes.append("conflict")

    threads = [
        threading.Thread(target=_run, args=(DECISION_NO_VIOLATION, "race-a")),
        threading.Thread(target=_run, args=(DECISION_INSUFFICIENT, "race-b")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["conflict", DECISION_NO_VIOLATION] or sorted(outcomes) == [
        "conflict",
        DECISION_INSUFFICIENT,
    ]
    _, total = test_db.list_violations(per_page=20)
    assert total == 0
    assert test_db.get_review_item(review_id)["violation_type"] == VIOLATION_COUNTERFLOW
    assert len(test_db.list_recent_review_decisions(10)) == 1


def test_existing_confirm_endpoint_still_materializes(test_db, enforcer):
    _, review_id = _review(test_db, VIOLATION_OBSTRUCTION)
    result = materialize_case_from_review(test_db, review_id, enforcer)
    assert result["violation_id"]
    assert test_db.get_review_decision(review_id) is None
    assert test_db.get_review_item(review_id)["status"] == "confirmed"


def test_blank_reason_and_same_rule_correction_rejected(test_db, enforcer):
    _, review_id = _review(test_db, VIOLATION_COUNTERFLOW)
    with pytest.raises(ReviewDecisionError):
        apply_review_decision(
            test_db,
            review_id,
            enforcer,
            decision=DECISION_CONFIRM,
            reason="   ",
            idempotency_key="blank",
        )
    with pytest.raises(ReviewDecisionError):
        apply_review_decision(
            test_db,
            review_id,
            enforcer,
            decision=DECISION_CORRECT,
            selected_canonical_rule=VIOLATION_COUNTERFLOW,
            reason="Same rule.",
            idempotency_key="same-rule",
        )
    assert test_db.get_review_item(review_id)["status"] == "pending"


def test_review_page_and_decision_api(test_db):
    import bcrypt

    os.environ["TAVIDM_BOOTSTRAP_ADMIN_PASSWORD"] = "isolated-review-decision-test"
    import app as flask_app
    from core import auth

    auth.ensure_default_admin()

    password = bcrypt.hashpw(b"enforcer123", bcrypt.gensalt()).decode("utf-8")
    test_db.create_user("enforcer_ui", password, role="enforcer", full_name="UI Enforcer")
    _, review_id = _review(test_db, VIOLATION_COUNTERFLOW)
    flask_app.app.config["TESTING"] = True
    flask_app.app.config["WTF_CSRF_ENABLED"] = False
    try:
        with flask_app.app.test_client() as client:
            login = client.post(
                "/login",
                data={"username": "enforcer_ui", "password": "enforcer123"},
            )
            assert login.status_code in (200, 302)
            page = client.get("/review-queue")
            body = page.get_data(as_text=True)
            assert page.status_code == 200
            assert "Insufficient evidence" in body
            assert "No violation" in body
            assert "The original system suggestion stays on the queue item." in body
            denied = client.post(
                f"/api/review-queue/{review_id}/decision",
                json={
                    "decision": DECISION_INSUFFICIENT,
                    "reason": "Frames do not show the maneuver.",
                    "reviewer_user_id": 1,
                    "idempotency_key": "api-1",
                },
            )
            assert denied.status_code == 400
            saved = client.post(
                f"/api/review-queue/{review_id}/decision",
                json={
                    "decision": DECISION_INSUFFICIENT,
                    "reason": "Frames do not show the maneuver.",
                    "idempotency_key": "api-1",
                },
            )
            payload = saved.get_json()
            assert saved.status_code == 200
            assert payload["disposition_label"] == "Insufficient evidence"
            assert payload["creates_case"] is False
            listed = client.get("/api/review-queue?decision=insufficient_evidence").get_json()
            assert listed["items"][0]["disposition_label"] == "Insufficient evidence"
            assert listed["items"][0]["violation_type"] == VIOLATION_COUNTERFLOW
            legacy = client.post(f"/api/review-queue/{review_id}/confirm")
            assert legacy.status_code in (404, 409)
            assert test_db.get_review_decision(review_id)["decision"] == DECISION_INSUFFICIENT
            assert test_db.get_review_item(review_id)["violation_type"] == VIOLATION_COUNTERFLOW
    finally:
        flask_app.stop_processing_worker()
