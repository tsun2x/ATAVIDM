"""Tests for evidence extension: vehicle crop + plate_status default."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from core import evidence


@pytest.fixture(autouse=True)
def isolated_bootstrap_password(monkeypatch):
    monkeypatch.setenv("TAVIDM_BOOTSTRAP_ADMIN_PASSWORD", "evidence-test-bootstrap-2026")


def _fake_frame(height=120, width=160):
    return np.zeros((height, width, 3), dtype=np.uint8)


def _fake_detection():
    return {
        "bbox_x": 10,
        "bbox_y": 10,
        "bbox_w": 40,
        "bbox_h": 30,
        "confidence": 0.9,
        "track_id": 7,
    }


@pytest.fixture
def evidence_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence, "EVIDENCE_FOLDER", str(tmp_path / "evidence"))
    return tmp_path


class TestVehicleCrop:
    def test_returns_path_within_evidence_dir(self, evidence_dir):
        out = evidence.save_vehicle_crop(
            _fake_frame(), _fake_detection(), source_key="video_1", frame_number=100
        )
        assert out is not None
        saved = Path(out)
        assert saved.suffix == ".jpg"
        assert saved.parent == Path(evidence.EVIDENCE_FOLDER) / "video_1"
        # Verify the file actually exists on disk (computed from EVIDENCE_FOLDER).
        expected = Path(evidence.EVIDENCE_FOLDER) / "video_1" / "vehicle_f000100_t007.jpg"
        assert expected.exists()

    def test_none_when_bbox_invalid(self, evidence_dir):
        bad = _fake_detection()
        bad["bbox_w"] = 0
        assert evidence.save_vehicle_crop(_fake_frame(), bad, "video_1", 1) is None

    def test_scene_snapshot_unchanged(self, evidence_dir):
        out = evidence.save_evidence_snapshot(
            _fake_frame(), _fake_detection(), "Illegal Parking", "video_1", 100
        )
        assert out is not None
        saved = Path(out)
        if not saved.is_absolute():
            saved = Path(__file__).resolve().parents[1] / saved
        raw = saved.with_name("raw_" + saved.name)
        assert raw.exists()
        assert evidence.raw_scene_sidecar_path(saved) == raw

    def test_snapshot_write_failure_is_not_returned_as_saved_evidence(self, evidence_dir, monkeypatch):
        monkeypatch.setattr(evidence.cv2, "imwrite", lambda *_args, **_kwargs: False)
        out = evidence.save_evidence_snapshot(
            _fake_frame(), _fake_detection(), "Illegal Parking", "video_1", 100
        )
        assert out is None

    def test_annotated_write_failure_removes_the_raw_sidecar(self, evidence_dir, monkeypatch):
        calls = {"n": 0}

        def write_sidecar_then_fail_annotation(path, _image):
            calls["n"] += 1
            if calls["n"] == 1:
                Path(path).write_bytes(b"raw")
                return True
            return False

        monkeypatch.setattr(evidence.cv2, "imwrite", write_sidecar_then_fail_annotation)
        out = evidence.save_evidence_snapshot(
            _fake_frame(), _fake_detection(), "Illegal Parking", "video_1", 100
        )
        assert out is None
        assert not list(Path(evidence.EVIDENCE_FOLDER).rglob("raw_*.jpg"))


class TestPlateStatusDefault:
    def test_insert_review_queue_defaults_not_attempted(self, test_db):
        vid = test_db.insert_video(filename="c.mp4", filepath="/tmp/c.mp4", status="processed")
        rid = test_db.insert_review_queue(
            video_id=vid, track_id=1, violation_type="Illegal Parking",
            confidence=0.9, frame_number=5, vehicle_class="car",
        )
        row = test_db.get_review_item(rid)
        assert row["plate_status"] == "not_attempted"
        assert row["plate_text"] is None
        assert row["vehicle_evidence_path"] is None

    def test_review_queue_stores_vehicle_evidence_and_plate(self, test_db):
        vid = test_db.insert_video(filename="c.mp4", filepath="/tmp/c.mp4", status="processed")
        rid = test_db.insert_review_queue(
            video_id=vid, track_id=1, violation_type="Counterflow",
            confidence=0.9, frame_number=5, vehicle_class="car",
            vehicle_evidence_path="evidence/video_1/vehicle_f000005_t001.jpg",
            plate_status="unreadable",
        )
        row = test_db.get_review_item(rid)
        assert row["vehicle_evidence_path"].endswith("vehicle_f000005_t001.jpg")
        assert row["plate_status"] == "unreadable"

    def test_violation_plate_fields_carried_from_confirm(self, test_db):
        import bcrypt

        user_id = test_db.create_user("u", bcrypt.hashpw(b"x", bcrypt.gensalt()).decode(), role="enforcer")
        vid = test_db.insert_video(filename="c.mp4", filepath="/tmp/c.mp4", status="processed")
        rid = test_db.insert_review_queue(
            video_id=vid, track_id=1, violation_type="Illegal Parking",
            confidence=0.9, frame_number=5, vehicle_class="car",
            vehicle_evidence_path="evidence/video_1/vehicle_f000005_t001.jpg",
            plate_status="not_attempted",
        )
        violation_id = test_db.confirm_review_item(rid, reviewed_by=user_id)
        vrow = test_db.get_violation(violation_id)
        assert vrow["vehicle_evidence_path"] == "evidence/video_1/vehicle_f000005_t001.jpg"
        assert vrow["plate_status"] == "not_attempted"
        assert vrow["plate_text"] is None

    def test_temporal_finalization_writes_first_clip_but_keeps_existing_evidence(self, test_db):
        vid = test_db.insert_video(filename="temporal-rerun.mp4", filepath="/tmp/temporal-rerun.mp4", status="processed")
        rid = test_db.insert_review_queue(
            video_id=vid, track_id=4, violation_type="Counterflow", confidence=0.8,
            frame_number=12, evidence_pre_sec=2.0, evidence_post_sec=3.0,
        )

        test_db.update_review_temporal_evidence(
            rid, evidence_clip_path="run-1.mp4", evidence_sequence_dir="run-1-seq",
            episode_start_sec=1.0, episode_end_sec=4.0, only_if_unfinalized=True,
        )
        test_db.update_review_temporal_evidence(
            rid, evidence_clip_path="run-2.mp4", evidence_sequence_dir="run-2-seq",
            episode_start_sec=8.0, episode_end_sec=12.0, only_if_unfinalized=True,
        )

        item = test_db.get_review_item(rid)
        assert item["evidence_clip_path"] == "run-1.mp4"
        assert item["evidence_sequence_dir"] == "run-1-seq"
        assert item["episode_start_sec"] == 1.0
        assert item["episode_end_sec"] == 4.0

    def test_reused_pending_episode_preserves_any_existing_temporal_evidence(self, test_db):
        vid = test_db.insert_video(filename="partial-temporal.mp4", filepath="/tmp/partial-temporal.mp4", status="processed")
        rid = test_db.insert_review_queue(
            video_id=vid, track_id=8, violation_type="Counterflow", confidence=0.8,
            frame_number=16, evidence_clip_path="partial.mp4",
        )

        test_db.update_review_temporal_evidence(
            rid, evidence_clip_path="recovered.mp4", evidence_sequence_dir="recovered-seq",
            only_if_unfinalized=True,
        )
        item = test_db.get_review_item(rid)
        assert item["evidence_clip_path"] == "partial.mp4"
        assert item["evidence_sequence_dir"] is None

    def test_temporal_finalization_does_not_replace_confirmed_review_evidence(self, test_db):
        vid = test_db.insert_video(filename="confirmed-temporal.mp4", filepath="/tmp/confirmed-temporal.mp4", status="processed")
        rid = test_db.insert_review_queue(
            video_id=vid, track_id=5, violation_type="Counterflow", confidence=0.8,
            frame_number=13, evidence_pre_sec=2.0, evidence_post_sec=3.0,
            evidence_clip_path="reviewed.mp4", evidence_sequence_dir="reviewed-seq",
            episode_start_sec=1.0, episode_end_sec=4.0,
        )
        reviewer = test_db.create_user("temporal-reviewer", "hash", role="enforcer")
        violation_id = test_db.confirm_review_item(rid, reviewed_by=reviewer)

        test_db.update_review_temporal_evidence(
            rid, evidence_clip_path="rerun.mp4", evidence_sequence_dir="rerun-seq",
            episode_start_sec=8.0, episode_end_sec=12.0, only_if_unfinalized=True,
        )

        item = test_db.get_review_item(rid)
        violation = test_db.get_violation(violation_id)
        assert item["evidence_clip_path"] == "reviewed.mp4"
        assert item["evidence_sequence_dir"] == "reviewed-seq"
        assert violation["evidence_clip_path"] == "reviewed.mp4"
        assert violation["evidence_sequence_dir"] == "reviewed-seq"

    def test_reused_episode_guard_preserves_closed_row_and_allows_first_finalize(self, test_db):
        from core import video_processor
        from core.temporal_evidence import TemporalEvidenceBuffer
        from core.violation_engine import ViolationEvent

        from core.video_processor import _apply_finalized_episode
        from core.temporal_evidence import EvidenceEpisode

        vid = test_db.insert_video(filename="reused-temporal.mp4", filepath="/tmp/reused-temporal.mp4", status="processed")
        reviewed = test_db.insert_review_queue(
            video_id=vid, track_id=6, violation_type="Counterflow", confidence=0.8,
            frame_number=14, timestamp_sec=1.0,
            evidence_pre_sec=2.0, evidence_post_sec=3.0,
            evidence_clip_path="reviewed-only-clip.mp4", evidence_sequence_dir="reviewed-seq",
            episode_start_sec=1.0, episode_end_sec=4.0,
        )
        reviewer = test_db.create_user("reused-reviewer", "hash", role="enforcer")
        test_db.confirm_review_item(reviewed, reviewed_by=reviewer)

        buffer = TemporalEvidenceBuffer(source_key=f"video_{vid}", run_id=2)
        event = ViolationEvent(
            violation_type="Counterflow", track_id=6, confidence=0.8,
            frame_number=14, timestamp_sec=1.0, reason_log="Opposing flow",
            vehicle_class="car", detection_confidence=0.9,
            violation_confidence=0.8, evidence_sufficiency=0.7,
        )
        assert video_processor._persist_event(event, None, None, vid, buffer, processing_run_id=2) == reviewed
        rerun_episode = buffer._open_episodes[("Counterflow", 6)]
        assert rerun_episode.review_id is None

        _apply_finalized_episode(EvidenceEpisode(
            violation_type="Counterflow", track_id=6, confirmed_at_sec=1.0,
            episode_start_sec=1.0, episode_end_sec=4.0, clip_path="rerun.mp4",
            sequence_dir="rerun-seq", review_id=reviewed,
            protect_reused_review_evidence=True,
        ))
        assert test_db.get_review_item(reviewed)["evidence_clip_path"] == "reviewed-only-clip.mp4"

        pending = test_db.insert_review_queue(
            video_id=vid, track_id=7, violation_type="Counterflow", confidence=0.8,
            frame_number=15, evidence_pre_sec=2.0, evidence_post_sec=3.0,
        )
        _apply_finalized_episode(EvidenceEpisode(
            violation_type="Counterflow", track_id=7, confirmed_at_sec=2.0,
            episode_start_sec=2.0, episode_end_sec=5.0, clip_path="first-run.mp4",
            sequence_dir="first-run-seq", review_id=pending,
            protect_reused_review_evidence=True,
        ))
        assert test_db.get_review_item(pending)["evidence_clip_path"] == "first-run.mp4"


def test_raw_scene_endpoint_is_authenticated_and_pairs_with_annotated_snapshot(
    evidence_dir, client, test_db, monkeypatch
):
    import config

    monkeypatch.setattr(config, "EVIDENCE_FOLDER", str(evidence.EVIDENCE_FOLDER))
    scene = _fake_frame()
    stored_path = evidence.save_evidence_snapshot(
        scene, _fake_detection(), "Counterflow", "video_1_run_12", 100
    )
    video_id = test_db.insert_video("paired.mp4", "paired.mp4", status="processed")
    violation_id = test_db.insert_violation(
        video_id, 7, "Counterflow", 0.0, 100, 1.0, evidence_path=stored_path
    )
    endpoint = f"/api/violations/{violation_id}/evidence/raw-scene"
    assert client.get(endpoint).status_code in (302, 401)

    login = client.post(
        "/login", data={"username": "admin", "password": "evidence-test-bootstrap-2026"}
    )
    assert login.status_code == 302
    response = client.get(endpoint)
    assert response.status_code == 200
    assert response.mimetype == "image/jpeg"
    items = client.get("/api/violations").get_json()["items"]
    assert items[0]["raw_evidence_url"] == endpoint
