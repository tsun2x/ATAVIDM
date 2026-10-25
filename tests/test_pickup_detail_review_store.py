"""Dedicated pickup review persistence stays separate and account scoped."""

from __future__ import annotations

import json


def _payload(video_id: int, *, occurrence_key: str = "pickup_1_g1_person_2_g1"):
    return {
        "video_id": video_id,
        "run_key": "run_5",
        "processing_run_id": 5,
        "pickup_track_id": 1,
        "person_track_id": 2,
        "pickup_identity_epoch": 1,
        "person_identity_epoch": 1,
        "occurrence_key": occurrence_key,
        "selector_version": "pickup-review-1",
        "timestamp_sec": 2.0,
        "episode_start_sec": 0.0,
        "frame_number": 20,
        "source_width": 640,
        "source_height": 480,
        "frame_count": 5,
        "frames_json": json.dumps([{"scene_path": "scene.jpg", "crop_path": "crop.jpg"}]),
        "association_json": json.dumps({"cargo_region_visible": None, "lower_body_visible": None}),
        "size_bytes": 12,
    }


def test_pickup_candidates_deduplicate_and_review_outcome_is_persisted(test_db):
    video_id = test_db.insert_video("pickup.mp4", "/tmp/pickup.mp4", status="processed")
    first = test_db.upsert_pickup_detail_candidate(**_payload(video_id))
    retry = test_db.upsert_pickup_detail_candidate(**_payload(video_id))

    assert first == retry
    rows, total = test_db.list_pickup_detail_candidates(human_outcome="pending")
    assert total == 1
    assert rows[0]["pickup_track_id"] == 1
    assert test_db.record_pickup_detail_review(
        candidate_id=first, outcome="uncertain", reviewed_by=None, notes="visibility unclear"
    ) is True
    updated = test_db.get_pickup_detail_candidate(first)
    assert updated["human_outcome"] == "uncertain"
    assert updated["reviewer_notes"] == "visibility unclear"


def test_pickup_candidate_lists_follow_video_owner_scope(test_db):
    owner_a = test_db.create_user("pickup_owner_a", "hash", role="viewer")
    owner_b = test_db.create_user("pickup_owner_b", "hash", role="viewer")
    video_a = test_db.insert_video("a.mp4", "/tmp/a.mp4", status="processed", uploaded_by=owner_a)
    video_b = test_db.insert_video("b.mp4", "/tmp/b.mp4", status="processed", uploaded_by=owner_b)
    test_db.upsert_pickup_detail_candidate(**_payload(video_a, occurrence_key="a"))
    test_db.upsert_pickup_detail_candidate(**_payload(video_b, occurrence_key="b"))

    rows, total = test_db.list_pickup_detail_candidates(owner_user_id=owner_a)
    assert total == 1
    assert rows[0]["video_id"] == video_a
