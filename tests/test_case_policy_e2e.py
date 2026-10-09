"""End-to-end isolated tests for case policy review workflow.

Uses fake ALPR-free paths and deterministic timestamps. Does not start the
operational Flask server, download models, or touch database/tavidm.db.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from core.case_review_service import (
    materialize_case_from_review,
    persist_event_time_review,
    verify_plate_identity,
)
from core.detection_config import VIOLATION_ILLEGAL_PARKING, VIOLATION_OBSTRUCTION
from core.plate_processing import HUMAN_PLATE_STATUS_VERIFIED_READABLE
from core.recurrence_policy import (
    evaluate_recurrence_eligibility,
    persist_recurrence_evaluation,
)


@pytest.fixture(autouse=True)
def isolated_bootstrap_password(monkeypatch):
    monkeypatch.setenv("TAVIDM_BOOTSTRAP_ADMIN_PASSWORD", "case-policy-test-only-2026")


@pytest.fixture
def test_db_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_tavidm.db")
        assert "database/tavidm.db" not in db_path.replace("\\", "/")
        os.environ["SQLITE_PATH"] = db_path
        os.environ["DATABASE_URL"] = db_path
        yield db_path


@pytest.fixture
def test_db(test_db_path):
    from database import sqlite_adapter

    sqlite_adapter.init_db(force=True)
    assert sqlite_adapter.get_db_path() == test_db_path
    return sqlite_adapter


@pytest.fixture
def enforcer(test_db):
    return test_db.create_user("enf", "hash", role="enforcer", full_name="Enforcer")


@pytest.fixture
def viewer(test_db):
    return test_db.create_user("view", "hash", role="viewer", full_name="Viewer")


@pytest.fixture
def inactive(test_db):
    uid = test_db.create_user("gone", "hash", role="enforcer", full_name="Gone")
    test_db.update_user(uid, is_active=0)
    return uid


@pytest.fixture
def admin(test_db):
    """Plate confirmation is an active-admin action in the experimental demo."""
    return test_db.create_user("adm", "hash", role="admin", full_name="Admin")


def test_full_review_pipeline_grouped_case(test_db, enforcer, inactive, viewer, admin):
    video = test_db.insert_video("e2e.mp4", "/tmp/e2e.mp4", status="ready")
    park = test_db.insert_review_queue(
        video_id=video,
        track_id=9,
        violation_type=VIOLATION_ILLEGAL_PARKING,
        confidence=0.92,
        frame_number=100,
        timestamp_sec=10.0,
        episode_start_sec=8.0,
        episode_end_sec=20.0,
        processing_run_id=5,
        evidence_path="/tmp/park.jpg",
    )
    obs = test_db.insert_review_queue(
        video_id=video,
        track_id=9,
        violation_type=VIOLATION_OBSTRUCTION,
        confidence=0.93,
        frame_number=110,
        timestamp_sec=12.0,
        episode_start_sec=9.0,
        episode_end_sec=21.0,
        processing_run_id=5,
        vehicle_evidence_path="/tmp/veh.jpg",
        plate_evidence_path="/tmp/plate.jpg",
    )

    # Detection → grouped pending case
    result = materialize_case_from_review(test_db, park, enforcer)
    assert result["fused"] is True
    vid = result["violation_id"]
    materialize_case_from_review(test_db, obs, enforcer)
    assert test_db.find_violation_linked_to_review(park) == vid
    assert test_db.find_violation_linked_to_review(obs) == vid
    policies = test_db.get_case_policy_records(vid)
    assert {p["canonical_rule"] for p in policies} == {
        VIOLATION_ILLEGAL_PARKING,
        VIOLATION_OBSTRUCTION,
    }

    # Evidence retained
    viol = test_db.get_violation(vid)
    assert viol["evidence_path"] == "/tmp/park.jpg"
    assert viol["vehicle_evidence_path"] == "/tmp/veh.jpg"
    assert viol["plate_evidence_path"] == "/tmp/plate.jpg"

    # Unauthorized / inactive / non-admin blocked
    with pytest.raises(PermissionError):
        verify_plate_identity(
            test_db,
            vid,
            viewer,
            plate_status=HUMAN_PLATE_STATUS_VERIFIED_READABLE,
            accepted_plate_text="E2E123",
        )
    with pytest.raises(PermissionError):
        verify_plate_identity(
            test_db,
            vid,
            inactive,
            plate_status=HUMAN_PLATE_STATUS_VERIFIED_READABLE,
            accepted_plate_text="E2E123",
        )
    # An enforcement officer is no longer allowed to confirm a plate identity.
    with pytest.raises(PermissionError):
        verify_plate_identity(
            test_db,
            vid,
            enforcer,
            plate_status=HUMAN_PLATE_STATUS_VERIFIED_READABLE,
            accepted_plate_text="E2E123",
        )

    # Authorized plate (admin) + event-time review (enforcer, unchanged)
    verify_plate_identity(
        test_db,
        vid,
        admin,
        plate_status=HUMAN_PLATE_STATUS_VERIFIED_READABLE,
        accepted_plate_text="E2E123",
    )
    persist_event_time_review(
        test_db,
        vid,
        enforcer,
        user_entry_raw="2026-03-01T09:00:00+08:00",
        video_relative_sec=12.0,
    )
    persist_event_time_review(
        test_db,
        vid,
        enforcer,
        user_entry_raw="2026-03-01T09:00:00+08:00",
        correction_raw="2026-03-01T09:15:00+08:00",
    )
    assert "09:15:00" in (test_db.get_confirmed_event_time(vid) or "")

    # Case confirmation
    assert test_db.confirm_case(vid, enforcer) is True

    # Historical matching with policy gates (suggestions disabled)
    prior_video = test_db.insert_video("prior.mp4", "/tmp/prior.mp4", status="ready")
    prior_rid = test_db.insert_review_queue(
        video_id=prior_video,
        track_id=1,
        violation_type=VIOLATION_OBSTRUCTION,
        confidence=0.9,
        frame_number=1,
        timestamp_sec=1.0,
    )
    prior_vid = test_db.confirm_review_item(prior_rid, enforcer)
    version_id = test_db.ensure_config_mapping_policy_version()
    test_db.create_case_policy_record(
        prior_vid,
        version_id,
        VIOLATION_OBSTRUCTION,
        official_category="Obstruction of Traffic Flow",
        legal_status="unverified",
        behavior_details=[],
    )
    verify_plate_identity(
        test_db,
        prior_vid,
        admin,
        plate_status=HUMAN_PLATE_STATUS_VERIFIED_READABLE,
        accepted_plate_text="E2E123",
    )
    persist_event_time_review(
        test_db,
        prior_vid,
        enforcer,
        user_entry_raw="2025-06-01T09:00:00+08:00",
    )
    test_db.confirm_case(prior_vid, enforcer)

    evaluation = evaluate_recurrence_eligibility(
        test_db,
        violation_id=vid,
        lookback_policy_active=False,
    )
    assert evaluation.offense_suggestion_enabled is False
    assert any(m.violation_id == prior_vid for m in evaluation.matched)
    persist_recurrence_evaluation(
        test_db, vid, evaluation, evaluated_by=enforcer, reviewer_decision="deferred"
    )

    # Document generation path without printed status
    assert test_db.is_notice_printed(vid) is False
    # Separate printing attestation
    assert test_db.confirm_notice_printed(vid, enforcer) is True
    assert test_db.is_notice_printed(vid) is True

    # Retry attestation is idempotent
    assert test_db.confirm_notice_printed(vid, enforcer) is True

    # Dismissed case cannot be confirmed/printed
    bad_video = test_db.insert_video("bad.mp4", "/tmp/bad.mp4", status="ready")
    bad_rid = test_db.insert_review_queue(
        video_id=bad_video,
        track_id=2,
        violation_type=VIOLATION_OBSTRUCTION,
        confidence=0.9,
        frame_number=1,
        timestamp_sec=1.0,
    )
    bad_vid = test_db.confirm_review_item(bad_rid, enforcer)
    test_db.update_violation_status(bad_vid, "dismissed")
    with pytest.raises(ValueError):
        test_db.confirm_case(bad_vid, enforcer)
    with pytest.raises(ValueError):
        test_db.confirm_notice_printed(bad_vid, enforcer)

    # Audit history preserved
    actions = test_db.get_case_actions(vid)
    types = {a["action_type"] for a in actions}
    assert "review_confirmed" in types
    assert "plate_verified" in types
    assert "event_time_confirmed" in types
    assert "case_confirmed" in types
    assert "notice_printed" in types
    assert "recurrence_evaluated" in types


def test_flask_case_apis_isolated(client, enforcer_client):
    """Authenticated Flask APIs on an isolated test DB (no operational DB)."""
    from database import db

    video = db.insert_video("api.mp4", "/tmp/api.mp4", status="ready")
    rid = db.insert_review_queue(
        video_id=video,
        track_id=1,
        violation_type=VIOLATION_OBSTRUCTION,
        confidence=0.9,
        frame_number=1,
        timestamp_sec=1.0,
        evidence_path="/tmp/e.jpg",
    )
    confirm = enforcer_client.post(f"/api/review-queue/{rid}/confirm")
    assert confirm.status_code == 200
    payload = confirm.get_json()
    assert payload["success"] is True
    vid = payload["violation_id"]

    # Plate confirmation is an active-admin action while the experimental plate
    # demo is in scope. The enforcer is refused, and the spoofed verified_by is
    # ignored when an administrator does it.
    plate_forbidden = enforcer_client.post(
        f"/api/cases/{vid}/plate",
        json={
            "plate_status": "verified_readable",
            "accepted_plate_text": "API999",
        },
    )
    assert plate_forbidden.status_code == 403

    import bcrypt

    admin_password = "case-api-admin-pw"
    admin_id = db.create_user(
        "case_api_admin",
        bcrypt.hashpw(admin_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8"),
        role="admin",
        full_name="Case API Admin",
    )
    assert admin_id
    client.post("/login", data={"username": "case_api_admin", "password": admin_password})
    plate = client.post(
        f"/api/cases/{vid}/plate",
        json={
            "plate_status": "verified_readable",
            "accepted_plate_text": "API999",
            "verified_by": 999999,  # must be ignored
        },
    )
    assert plate.status_code == 200
    assert plate.get_json()["verified_by"] != 999999

    # Event-time review remains an enforcer capability.
    client.post("/logout")
    evt = enforcer_client.post(
        f"/api/cases/{vid}/event-time",
        json={"event_time": "2026-04-01T08:00:00+08:00"},
    )
    assert evt.status_code == 200
    assert evt.get_json()["persisted"] is True

    case = enforcer_client.post(f"/api/cases/{vid}/confirm-case")
    assert case.status_code == 200

    printable = enforcer_client.get(f"/api/cases/{vid}/printable")
    assert printable.status_code == 200
    body = printable.get_json()
    assert body["preview_is_not_printed"] is True
    assert body["notice_printed"] is False

    printed = enforcer_client.post(f"/api/cases/{vid}/notice-printed", json={})
    assert printed.status_code == 200
    assert printed.get_json()["notice_printed"] is True

    # Active System Administrator may perform case-review actions.
    client.post(
        "/login",
        data={"username": "admin", "password": "case-policy-test-only-2026"},
    )
    video2 = db.insert_video("adminok.mp4", "/tmp/adminok.mp4", status="ready")
    rid2 = db.insert_review_queue(
        video_id=video2,
        track_id=1,
        violation_type=VIOLATION_ILLEGAL_PARKING,
        confidence=0.9,
        frame_number=1,
        timestamp_sec=1.0,
        episode_start_sec=0.0,
        episode_end_sec=2.0,
    )
    admin_mat = client.post(f"/api/review-queue/{rid2}/confirm")
    assert admin_mat.status_code == 200
    assert admin_mat.get_json()["proposed_official_category"] == (
        "Obstruction of Traffic Flow"
    )

    # Viewer remains denied.
    import bcrypt

    viewer_hash = bcrypt.hashpw(b"viewer123", bcrypt.gensalt()).decode("utf-8")
    db.create_user("viewer", viewer_hash, role="viewer", full_name="Viewer")
    client.get("/logout")
    client.post("/login", data={"username": "viewer", "password": "viewer123"})
    denied = client.post(f"/api/cases/{vid}/confirm-case")
    assert denied.status_code in (403, 302, 401)
    # Admin materialization above already confirmed rid2 — remains confirmed.
    assert db.get_review_item(rid2)["status"] == "confirmed"
