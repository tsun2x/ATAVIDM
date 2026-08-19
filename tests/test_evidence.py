"""Tests for evidence extension: vehicle crop + plate_status default."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from core import evidence


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
        assert "/evidence/video_1/" in out
        assert out.endswith(".jpg")
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
        assert "/evidence/video_1/" in out


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
