"""Motorcycle detail queue: dedup, transitions, retries, access control, retention.

The crop-scan model is mocked everywhere; no checkpoint is loaded or activated.
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from core.motorcycle_detail import DETAIL_SELECTOR_VERSION

EXPECTED_15 = (
    "car", "van", "jeepney", "tricycle", "autorickshaw", "bus", "truck",
    "pickup_truck", "motorcycle", "bicycle", "person", "rider",
    "helmet_acceptable", "helmet_nut_shell", "side_mirror",
)


def _resolve(stored_path):
    """Resolve stored detail evidence inside the (patched) evidence root."""
    import config

    if not stored_path:
        return None
    candidate = Path(str(stored_path))
    if not candidate.is_absolute():
        candidate = Path(config.BASE_DIR) / candidate
    root = Path(config.EVIDENCE_FOLDER).resolve()
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    if resolved == root or root not in resolved.parents:
        return None
    return resolved if resolved.is_file() else None


@pytest.fixture
def evidence_root(tmp_path, monkeypatch):
    import config

    root = tmp_path / "evidence"
    monkeypatch.setattr("core.evidence.EVIDENCE_FOLDER", str(root))
    monkeypatch.setattr("core.motorcycle_detail_scan.resolve_detail_evidence_path", _resolve)
    monkeypatch.setattr(config, "EVIDENCE_FOLDER", str(root))
    return root


def _seed_candidate(test_db, *, video_id, run_key='run_1', processing_run_id=1, track_id=7,
                    occurrence_key="t7g1", selector=DETAIL_SELECTOR_VERSION,
                    frames=(1, 2), frame_number=3):
    """Insert a candidate whose crop files really exist on disk."""
    import config

    root = Path(config.EVIDENCE_FOLDER) / "detail" / run_key / occurrence_key
    root.mkdir(parents=True, exist_ok=True)
    entries = []
    for index in frames:
        crop_path = root / f"crop_f{index}.jpg"
        assert cv2.imwrite(str(crop_path), np.full((40, 30, 3), 60, dtype=np.uint8))
        entries.append(
            {
                "index": index,
                "frame_number": index * 10,
                "timestamp_sec": float(index),
                "scene_path": str(root / f"scene_f{index}.jpg"),
                "crop_path": str(crop_path),
                "crop": {"x": 100, "y": 50, "w": 30, "h": 40},
                "scan_scale_x": 1.0,
                "scan_scale_y": 1.0,
                "detection_bbox": {"x": 105.0, "y": 70.0, "w": 20.0, "h": 20.0},
                "rider_bbox": None,
                "score": {"total": 0.7, "components": {}, "reasons": []},
                "size_bytes": 100,
                "overlay_path": None,
            }
        )
    return test_db.upsert_motorcycle_detail_candidate(
        video_id=video_id,
        run_key=run_key,
        track_id=track_id,
        occurrence_index=1,
        occurrence_key=occurrence_key,
        selector_version=selector,
        processing_run_id=processing_run_id,
        frame_number=frame_number,
        timestamp_sec=1.0,
        frame_score=0.7,
        score_breakdown_json=json.dumps({"sharpness": 0.5}),
        frame_count=len(entries),
        frames_json=json.dumps(entries),
        source_width=640,
        source_height=480,
    )


@pytest.fixture
def video_row(test_db):
    return test_db.insert_video("detail.mp4", "/tmp/detail.mp4", status="processed")


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


class TestDeduplication:
    def test_same_run_occurrence_and_selector_updates_one_row(self, test_db, video_row):
        first = _seed_candidate(test_db, video_id=video_row)
        second = _seed_candidate(test_db, video_id=video_row)
        assert first == second
        _, total = test_db.list_motorcycle_detail_candidates()
        assert total == 1

    def test_different_selector_version_is_a_separate_candidate(self, test_db, video_row):
        first = _seed_candidate(test_db, video_id=video_row)
        second = _seed_candidate(test_db, video_id=video_row, selector="md-2.0")
        assert first != second
        _, total = test_db.list_motorcycle_detail_candidates()
        assert total == 2

    def test_different_processing_run_is_a_separate_candidate(self, test_db, video_row):
        first = _seed_candidate(
            test_db, video_id=video_row, run_key="run_1", processing_run_id=1
        )
        second = _seed_candidate(
            test_db, video_id=video_row, run_key="run_2", processing_run_id=2
        )
        assert first != second
        _, total = test_db.list_motorcycle_detail_candidates()
        assert total == 2

    def test_same_run_with_a_new_run_key_stays_one_row(self, test_db, video_row):
        # Reprocessing the same run must update the candidate, not duplicate it.
        first = _seed_candidate(
            test_db, video_id=video_row, run_key="run_1", processing_run_id=1
        )
        second = _seed_candidate(
            test_db, video_id=video_row, run_key="run_1b", processing_run_id=1
        )
        assert first == second
        _, total = test_db.list_motorcycle_detail_candidates()
        assert total == 1

    def test_rescanning_does_not_duplicate_the_row(self, test_db, video_row):
        candidate_id = _seed_candidate(test_db, video_id=video_row)
        test_db.claim_motorcycle_detail_scans(10)
        test_db.record_motorcycle_detail_scan_failure(
            candidate_id=candidate_id, error="boom", max_attempts=2
        )
        again = _seed_candidate(test_db, video_id=video_row)
        assert again == candidate_id
        _, total = test_db.list_motorcycle_detail_candidates()
        assert total == 1
        row = test_db.get_motorcycle_detail_candidate(candidate_id)
        # A fresh selection re-queues the scan and clears the previous error.
        assert row["scan_state"] == "queued"
        assert row["scan_attempts"] == 0
        assert row["scan_error"] is None

    def test_review_outcome_survives_a_reselection(self, test_db, video_row):
        candidate_id = _seed_candidate(test_db, video_id=video_row)
        test_db.record_motorcycle_detail_review(
            candidate_id=candidate_id, outcome="reviewed", reviewed_by=None
        )
        _seed_candidate(test_db, video_id=video_row)
        assert test_db.get_motorcycle_detail_candidate(candidate_id)["human_outcome"] == "reviewed"


# ---------------------------------------------------------------------------
# Scan-state transitions, retries, restart
# ---------------------------------------------------------------------------


class TestScanTransitions:
    def test_claim_moves_queued_to_scanning_and_counts_an_attempt(self, test_db, video_row):
        candidate_id = _seed_candidate(test_db, video_id=video_row)
        claimed = test_db.claim_motorcycle_detail_scans(10)
        assert [r["id"] for r in claimed] == [candidate_id]
        row = test_db.get_motorcycle_detail_candidate(candidate_id)
        assert row["scan_state"] == "scanning"
        assert row["scan_attempts"] == 1
        assert row["claimed_at"] is not None

    def test_claim_is_not_repeated_for_a_scanning_row(self, test_db, video_row):
        _seed_candidate(test_db, video_id=video_row)
        assert len(test_db.claim_motorcycle_detail_scans(10)) == 1
        assert test_db.claim_motorcycle_detail_scans(10) == []

    def test_failure_returns_to_queued_until_attempts_are_spent(self, test_db, video_row):
        candidate_id = _seed_candidate(test_db, video_id=video_row)
        test_db.claim_motorcycle_detail_scans(10)
        assert test_db.record_motorcycle_detail_scan_failure(
            candidate_id=candidate_id, error="crop unreadable", max_attempts=2
        ) == "queued"
        test_db.claim_motorcycle_detail_scans(10)
        assert test_db.record_motorcycle_detail_scan_failure(
            candidate_id=candidate_id, error="crop unreadable", max_attempts=2
        ) == "failed"
        assert test_db.claim_motorcycle_detail_scans(10) == []


    def test_finish_marks_ready_with_observations(self, test_db, video_row):
        candidate_id = _seed_candidate(test_db, video_id=video_row)
        test_db.claim_motorcycle_detail_scans(10)
        test_db.finish_motorcycle_detail_scan(
            candidate_id=candidate_id,
            observations_json=json.dumps({"scan": {"model": "YOLOv8m:mock"}}),
            association_json=json.dumps({"rider": {"state": "associated"}}),
            uncertainty_json=json.dumps(["mirror:absence_not_proven_unknown"]),
            scan_model="YOLOv8m:mock:abc123",
            scan_class_map_json=json.dumps(list(EXPECTED_15)),
            scan_seconds=0.25,
        )
        row = test_db.get_motorcycle_detail_candidate(candidate_id)
        assert row["scan_state"] == "ready"
        assert row["scanned_at"] is not None
        assert json.loads(row["observations_json"])["scan"]["model"] == "YOLOv8m:mock"
        assert json.loads(row["scan_class_map_json"])[0] == "car"

    def test_restart_recovers_abandoned_scans_to_queued(self, test_db, video_row):
        candidate_id = _seed_candidate(test_db, video_id=video_row)
        test_db.claim_motorcycle_detail_scans(10)
        with test_db.db_session() as conn:
            conn.execute(
                "UPDATE motorcycle_detail_candidates SET claimed_at = '2000-01-01 00:00:00' "
                "WHERE id = ?",
                (candidate_id,),
            )
        assert test_db.recover_stale_motorcycle_detail_scans(stale_sec=1, max_attempts=3) == 1
        row = test_db.get_motorcycle_detail_candidate(candidate_id)
        assert row["scan_state"] == "queued"
        assert row["scan_attempts"] == 1

    def test_restart_fails_scans_with_no_attempts_left(self, test_db, video_row):
        candidate_id = _seed_candidate(test_db, video_id=video_row)
        test_db.claim_motorcycle_detail_scans(10)
        with test_db.db_session() as conn:
            conn.execute(
                "UPDATE motorcycle_detail_candidates SET claimed_at = '2000-01-01 00:00:00' "
                "WHERE id = ?",
                (candidate_id,),
            )
        test_db.recover_stale_motorcycle_detail_scans(stale_sec=1, max_attempts=1)
        row = test_db.get_motorcycle_detail_candidate(candidate_id)
        assert row["scan_state"] == "failed"
        assert row["scan_error"] == "scan_interrupted"

    def test_startup_recovery_resumes_a_claim_from_a_dead_process(self, test_db, video_row):
        """Queued work resumes at start-up without a newly enqueued video."""
        candidate_id = _seed_candidate(test_db, video_id=video_row)
        test_db.claim_motorcycle_detail_scans(10)
        # Fresh claimed_at: only a process start-up may take this back, because
        # the process that claimed it can no longer exist.
        assert test_db.count_queued_motorcycle_detail_scans() == 0
        assert test_db.recover_stale_motorcycle_detail_scans(
            stale_sec=3600, max_attempts=3, include_recent=True
        ) == 1
        row = test_db.get_motorcycle_detail_candidate(candidate_id)
        assert row["scan_state"] == "queued"
        assert row["claimed_at"] is None
        # A recovery spends no attempt, so the work is genuinely resumed.
        assert row["scan_attempts"] == 1
        assert test_db.count_queued_motorcycle_detail_scans() == 1

    def test_stale_window_does_not_steal_a_fresh_claim(self, test_db, video_row):
        """Without the start-up flag, only genuinely stale rows are recovered."""
        candidate_id = _seed_candidate(test_db, video_id=video_row)
        test_db.claim_motorcycle_detail_scans(10)
        assert test_db.recover_stale_motorcycle_detail_scans(
            stale_sec=3600, max_attempts=3
        ) == 0
        row = test_db.get_motorcycle_detail_candidate(candidate_id)
        assert row["scan_state"] == "scanning"
        assert test_db.count_queued_motorcycle_detail_scans() == 0

    def test_gate_note_records_reason_without_consuming_attempts(self, test_db, video_row):
        candidate_id = _seed_candidate(test_db, video_id=video_row)
        assert test_db.note_motorcycle_detail_scan_gate(
            reason="no_designated_detail_checkpoint"
        ) == 1
        row = test_db.get_motorcycle_detail_candidate(candidate_id)
        assert row["scan_state"] == "queued"
        assert row["scan_attempts"] == 0
        assert row["scan_error"] == "no_designated_detail_checkpoint"



# ---------------------------------------------------------------------------
# Human outcomes: explicit transitions, no confirm path
# ---------------------------------------------------------------------------


class TestHumanOutcomes:
    @pytest.mark.parametrize("outcome", ["reviewed", "dismissed", "uncertain"])
    def test_allowed_outcomes_are_recorded(self, test_db, video_row, outcome):
        candidate_id = _seed_candidate(test_db, video_id=video_row)
        test_db.record_motorcycle_detail_review(
            candidate_id=candidate_id, outcome=outcome, reviewed_by=None
        )
        row = test_db.get_motorcycle_detail_candidate(candidate_id)
        assert row["human_outcome"] == outcome
        assert row["reviewed_at"] is not None

    def test_confirm_outcome_is_rejected(self, test_db, video_row):
        candidate_id = _seed_candidate(test_db, video_id=video_row)
        with pytest.raises(ValueError, match="Unsupported detail-review outcome"):
            test_db.record_motorcycle_detail_review(
                candidate_id=candidate_id, outcome="confirmed", reviewed_by=None
            )
        assert test_db.get_motorcycle_detail_candidate(candidate_id)["human_outcome"] == "pending"

    def test_unknown_candidate_raises(self, test_db):
        with pytest.raises(ValueError, match="not found"):
            test_db.record_motorcycle_detail_review(
                candidate_id=9999, outcome="reviewed", reviewed_by=None
            )

    def test_scan_never_creates_violations_or_review_rows(self, test_db, video_row):
        candidate_id = _seed_candidate(test_db, video_id=video_row)
        test_db.claim_motorcycle_detail_scans(10)
        test_db.finish_motorcycle_detail_scan(
            candidate_id=candidate_id,
            observations_json=json.dumps({"association": {"mirror": {"state": "none_visible"}}}),
            association_json=json.dumps({"mirror": {"state": "none_visible"}}),
            uncertainty_json=json.dumps(["mirror:absence_not_proven_unknown"]),
            scan_model="YOLOv8m:mock",
            scan_class_map_json=json.dumps(list(EXPECTED_15)),
        )
        with test_db.db_session() as conn:
            violations = conn.execute("SELECT COUNT(*) AS n FROM violations").fetchone()["n"]
            review = conn.execute("SELECT COUNT(*) AS n FROM review_queue").fetchone()["n"]
        assert violations == 0
        assert review == 0


# ---------------------------------------------------------------------------
# Retention / deletion
# ---------------------------------------------------------------------------


class TestRetention:
    def test_remove_results_deletes_candidates_and_run_keys(self, test_db, video_row):
        _seed_candidate(test_db, video_id=video_row, run_key="run_5")
        assert test_db.list_motorcycle_detail_run_keys_for_video(video_row) == ["run_5"]
        test_db.delete_unconfirmed_results_for_video(video_row)
        _, total = test_db.list_motorcycle_detail_candidates()
        assert total == 0

    def test_video_cascade_delete_removes_candidates(self, test_db, video_row):
        _seed_candidate(test_db, video_id=video_row)
        test_db.delete_video_cascade_unprotected(video_row)
        _, total = test_db.list_motorcycle_detail_candidates()
        assert total == 0

    def test_purge_reports_removed_rows(self, test_db, video_row):
        _seed_candidate(test_db, video_id=video_row)
        _seed_candidate(test_db, video_id=video_row, occurrence_key="t8g1")
        assert test_db.purge_motorcycle_detail_candidates_for_video(video_row) == 2
        assert test_db.count_motorcycle_detail_pending() == 0

    def test_counts_by_state_are_zero_filled(self, test_db, video_row):
        _seed_candidate(test_db, video_id=video_row)
        counts = test_db.count_motorcycle_detail_by_state()
        assert counts["scan_states"]["queued"] == 1
        assert counts["scan_states"]["ready"] == 0
        assert counts["human_outcomes"]["pending"] == 1
        assert test_db.count_motorcycle_detail_pending() == 1

    def test_listing_rejects_unknown_filters(self, test_db):
        with pytest.raises(ValueError, match="Unknown scan state"):
            test_db.list_motorcycle_detail_candidates(scan_state="nope")
        with pytest.raises(ValueError, match="Unknown human outcome"):
            test_db.list_motorcycle_detail_candidates(human_outcome="confirmed")
