"""Pickup review UI never promotes evidence leads to violation cases."""

from __future__ import annotations

import json

import cv2
import numpy as np
import pytest


@pytest.fixture(autouse=True)
def isolated_bootstrap_password(monkeypatch):
    """Use disposable bootstrap credentials for the temporary test database."""
    monkeypatch.setenv("TAVIDM_BOOTSTRAP_ADMIN_PASSWORD", "PickupTestPass#2046")


@pytest.fixture
def pickup_candidate(test_db, enforcer_client, tmp_path, monkeypatch):
    import config

    evidence_root = tmp_path / "evidence"
    monkeypatch.setattr(config, "EVIDENCE_FOLDER", str(evidence_root))
    monkeypatch.setattr("core.evidence.EVIDENCE_FOLDER", str(evidence_root))
    user = test_db.get_user_by_username("enforcer_test")
    video_id = test_db.insert_video(
        "pickup-review-api.mp4", "/tmp/pickup-review-api.mp4", status="processed",
        uploaded_by=user["id"],
    )
    run_dir = evidence_root / "pickup_detail" / "run_8" / "pickup_1_g1_person_2_g1"
    run_dir.mkdir(parents=True)
    scene = run_dir / "scene_f1.jpg"
    crop = run_dir / "crop_f1.jpg"
    assert cv2.imwrite(str(scene), np.zeros((64, 64, 3), dtype=np.uint8))
    assert cv2.imwrite(str(crop), np.zeros((32, 32, 3), dtype=np.uint8))
    payload = {
        "video_id": video_id,
        "run_key": "run_8",
        "processing_run_id": 8,
        "pickup_track_id": 1,
        "person_track_id": 2,
        "pickup_identity_epoch": 1,
        "person_identity_epoch": 1,
        "occurrence_key": "pickup_1_g1_person_2_g1",
        "selector_version": "pickup-review-1",
        "timestamp_sec": 2.0,
        "episode_start_sec": 0.0,
        "frame_number": 20,
        "source_width": 64,
        "source_height": 64,
        "frame_count": 5,
        "frames_json": json.dumps([{
            "index": 1, "frame_number": 20, "timestamp_sec": 2.0,
            "scene_path": str(scene), "crop_path": str(crop),
        }]),
        "association_json": json.dumps({
            "state": "shared_motion_candidate", "evidence_source": "main_detector_and_bytetrack",
            "cargo_region_visible": None, "lower_body_visible": None, "observations": [],
        }),
        "size_bytes": 12,
    }
    candidate_id = test_db.upsert_pickup_detail_candidate(**payload)
    return enforcer_client, test_db, video_id, candidate_id


def test_enforcer_page_and_api_show_review_lead(pickup_candidate):
    client, _db, _video_id, candidate_id = pickup_candidate
    page = client.get("/pickup-detail-review")
    assert page.status_code == 200
    assert b"Evidence leads only" in page.data
    assert b"Cargo area visible: unknown" in page.data
    result = client.get("/api/pickup-detail-review").get_json()
    assert result["total"] == 1
    assert result["items"][0]["id"] == candidate_id
    assert result["items"][0]["association"]["evidence_source"] == "main_detector_and_bytetrack"


def test_evidence_is_served_from_private_pickup_path_only(pickup_candidate):
    client, _db, _video_id, candidate_id = pickup_candidate
    response = client.get(
        f"/api/pickup-detail-review/{candidate_id}/evidence/scene?index=1"
    )
    assert response.status_code == 200
    assert response.data[:2] == b"\xff\xd8"
    assert client.get(
        f"/api/pickup-detail-review/{candidate_id}/evidence/scene?index=2"
    ).status_code == 404


def test_human_outcome_does_not_create_ordinary_review_case(pickup_candidate):
    client, db, _video_id, candidate_id = pickup_candidate
    response = client.post(
        f"/api/pickup-detail-review/{candidate_id}/outcome",
        json={"outcome": "reviewed", "expected_outcome": "pending", "notes": "checked source"},
    )
    assert response.status_code == 200
    assert response.get_json()["outcome"] == "reviewed"
    with db.db_session() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM violations").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM review_queue").fetchone()["n"] == 0

    stale = client.post(
        f"/api/pickup-detail-review/{candidate_id}/outcome",
        json={"outcome": "uncertain", "expected_outcome": "pending"},
    )
    assert stale.status_code == 409


def test_api_filters_candidates_by_the_signed_in_video_owner(pickup_candidate):
    client, db, _video_id, _candidate_id = pickup_candidate
    owner_id = db.create_user("pickup_foreign_owner", "hash", role="viewer")
    video_id = db.insert_video(
        "foreign-pickup.mp4", "/tmp/foreign-pickup.mp4", status="processed", uploaded_by=owner_id
    )
    foreign_id = db.upsert_pickup_detail_candidate(
        video_id=video_id, run_key="run_9", processing_run_id=9,
        pickup_track_id=4, person_track_id=5, pickup_identity_epoch=1,
        person_identity_epoch=1, occurrence_key="foreign", selector_version="pickup-review-1",
        timestamp_sec=2, episode_start_sec=0, frame_number=20,
        source_width=64, source_height=64, frame_count=3,
        frames_json="[]", association_json="{}", size_bytes=0,
    )
    payload = client.get("/api/pickup-detail-review?outcome=all").get_json()
    assert payload["total"] == 1
    assert all(item["id"] != foreign_id for item in payload["items"])
    assert client.get(
        f"/api/pickup-detail-review/{foreign_id}/evidence/scene"
    ).status_code == 404


def test_invalid_outcome_and_viewer_are_rejected(pickup_candidate, test_db):
    client, _db, _video_id, candidate_id = pickup_candidate
    response = client.post(
        f"/api/pickup-detail-review/{candidate_id}/outcome", json={"outcome": "confirmed"}
    )
    assert response.status_code == 400

    import bcrypt
    from database import db
    from app import app

    password = "pickup-viewer-pass"
    db.create_user("pickup_viewer", bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(), role="viewer")
    viewer = app.test_client()
    viewer.post("/login", data={"username": "pickup_viewer", "password": password})
    assert viewer.get("/pickup-detail-review").status_code in (302, 403)
