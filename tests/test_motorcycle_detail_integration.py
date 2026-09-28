"""Integration: uploaded-video processing produces detail candidates.

The full-frame detector is mocked, so no checkpoint is loaded or activated. This
proves the pipeline hook, the queued crop pass, and that neither of them can
create or confirm a violation.
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from core.detection_config import OBJECT_DETECTOR_CLASSES

FRAME_W, FRAME_H, FPS = 320, 240, 10.0

EXPECTED_15 = tuple(OBJECT_DETECTOR_CLASSES)


def _det(track_id, label, ts, x, y, w, h, conf=0.9):
    return {
        "track_id": track_id,
        "class_label": label,
        "confidence": conf,
        "bbox_x": float(x),
        "bbox_y": float(y),
        "bbox_w": float(w),
        "bbox_h": float(h),
        "timestamp_sec": float(ts),
    }


class _MotorcycleDetector:
    """Mocked full-frame detector: one motorcycle + rider crossing the frame."""

    def __init__(self, with_rider: bool = True) -> None:
        self.with_rider = with_rider

    def load(self) -> None:
        return None

    @property
    def class_names(self) -> set[str]:
        return set(EXPECTED_15)

    def raw_class_map(self) -> dict[int, str]:
        return {i: n for i, n in enumerate(EXPECTED_15)}

    def track_frame(self, frame, conf=0.6, timestamp_sec=0.0):
        cx = 40.0 + min(timestamp_sec * 12.0, 120.0)
        dets = [_det(4, "motorcycle", timestamp_sec, cx, 110.0, 26.0, 60.0)]
        if self.with_rider:
            dets.append(_det(5, "rider", timestamp_sec, cx + 2.0, 70.0, 22.0, 52.0))
        return dets


class _BicycleOnlyDetector:
    """Bicycles are excluded from detail candidates by design."""

    def load(self) -> None:
        return None

    @property
    def class_names(self) -> set[str]:
        return set(EXPECTED_15)

    def raw_class_map(self) -> dict[int, str]:
        return {i: n for i, n in enumerate(EXPECTED_15)}

    def track_frame(self, frame, conf=0.6, timestamp_sec=0.0):
        cx = 40.0 + min(timestamp_sec * 12.0, 120.0)
        return [_det(9, "bicycle", timestamp_sec, cx, 110.0, 40.0, 60.0)]


def _write_mp4(path: Path, frames: int = 30) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (FRAME_W, FRAME_H)
    )
    assert writer.isOpened()
    try:
        rng = np.random.default_rng(7)
        for i in range(frames):
            noise = rng.integers(0, 255, size=(FRAME_H, FRAME_W, 3), dtype=np.uint8)
            writer.write(noise)
    finally:
        writer.release()
    return path


def _resolver(root):
    base = Path(root).resolve()

    def resolve(stored_path):
        if not stored_path:
            return None
        candidate = Path(str(stored_path))
        if not candidate.is_absolute():
            import config as cfg

            candidate = Path(cfg.BASE_DIR) / candidate
        try:
            resolved = candidate.resolve()
        except OSError:
            return None
        if resolved == base or base not in resolved.parents:
            return None
        return resolved if resolved.is_file() else None

    return resolve


@pytest.fixture
def pipeline(test_db, tmp_path, monkeypatch):
    """Uploaded-video processing with a private evidence root."""
    import config

    evidence = tmp_path / "evidence"
    monkeypatch.setattr("core.evidence.EVIDENCE_FOLDER", str(evidence))
    monkeypatch.setattr("core.temporal_evidence.EVIDENCE_FOLDER", str(evidence))
    monkeypatch.setattr(config, "EVIDENCE_FOLDER", str(evidence))
    monkeypatch.setattr(
        "core.motorcycle_detail_scan.resolve_detail_evidence_path", _resolver(evidence)
    )

    def _params():
        from core.detection_config import DEFAULT_RULE_PARAMETERS

        return {
            **dict(DEFAULT_RULE_PARAMETERS),
            "frame_skip": 1,
            "confidence_threshold": 0.3,
        }

    monkeypatch.setattr("core.video_processor.load_rule_parameters", _params)
    return evidence


def _run(test_db, tmp_path, monkeypatch, detector, *, video_name="detail.mp4"):
    from core.video_processor import process_video

    path = _write_mp4(tmp_path / video_name)
    video_id = test_db.insert_video(filename=video_name, filepath=str(path), status="ready")
    test_db.upsert_annotation(video_id, json.dumps({"no_parking": []}))
    monkeypatch.setattr("core.video_processor.Detector", lambda: detector)
    return video_id, process_video(video_id, enabled_violations=())


class TestPipelineIntegration:
    def test_processing_creates_bounded_detail_candidates(
        self, test_db, tmp_path, monkeypatch, pipeline
    ):
        video_id, result = _run(test_db, tmp_path, monkeypatch, _MotorcycleDetector())
        assert result.motorcycle_detail_candidates == 1
        rows, total = test_db.list_motorcycle_detail_candidates(video_id=video_id)
        assert total == 1
        row = rows[0]
        assert row["track_id"] == 4
        assert row["occurrence_key"] == "t4g1"
        assert row["scan_state"] == "queued"
        assert row["human_outcome"] == "pending"
        assert row["source_width"] == FRAME_W
        frames = json.loads(row["frames_json"])
        assert 1 <= len(frames) <= 2
        for frame in frames:
            assert Path(frame["scene_path"]).is_file()
            assert Path(frame["crop_path"]).is_file()

    def test_candidates_do_not_enter_the_violation_path(
        self, test_db, tmp_path, monkeypatch, pipeline
    ):
        _video_id, result = _run(test_db, tmp_path, monkeypatch, _MotorcycleDetector())
        assert result.motorcycle_detail_candidates == 1
        with test_db.db_session() as conn:
            assert conn.execute("SELECT COUNT(*) AS n FROM violations").fetchone()["n"] == 0
            assert conn.execute("SELECT COUNT(*) AS n FROM review_queue").fetchone()["n"] == 0
        assert test_db.count_review_pending() == 0

    def test_bicycles_produce_no_candidates(self, test_db, tmp_path, monkeypatch, pipeline):
        video_id, result = _run(
            test_db, tmp_path, monkeypatch, _BicycleOnlyDetector(), video_name="bicycle.mp4"
        )
        assert result.motorcycle_detail_candidates == 0
        _, total = test_db.list_motorcycle_detail_candidates(video_id=video_id)
        assert total == 0

    def test_disabled_collection_leaves_the_pipeline_unchanged(
        self, test_db, tmp_path, monkeypatch, pipeline
    ):
        monkeypatch.setattr("core.video_processor._detail_collection_enabled", lambda: False)
        video_id, result = _run(test_db, tmp_path, monkeypatch, _MotorcycleDetector())
        assert result.motorcycle_detail_candidates == 0
        _, total = test_db.list_motorcycle_detail_candidates(video_id=video_id)
        assert total == 0

    def test_reprocessing_creates_a_new_run_scoped_candidate(
        self, test_db, tmp_path, monkeypatch, pipeline
    ):
        from core.video_processor import process_video

        path = _write_mp4(tmp_path / "twice.mp4")
        video_id = test_db.insert_video("twice.mp4", str(path), status="ready")
        test_db.upsert_annotation(video_id, json.dumps({"no_parking": []}))
        monkeypatch.setattr("core.video_processor.Detector", lambda: _MotorcycleDetector())
        first_run = test_db.create_processing_run(
            video_id, json.dumps([]), viewer_mode="background"
        )
        process_video(video_id, enabled_violations=(), processing_run_id=first_run)
        second_run = test_db.create_processing_run(
            video_id, json.dumps([]), viewer_mode="background"
        )
        process_video(video_id, enabled_violations=(), processing_run_id=second_run)
        rows, total = test_db.list_motorcycle_detail_candidates(video_id=video_id)
        # One candidate per processing run, each with its own run-scoped evidence.
        assert total == 2
        assert {row["processing_run_id"] for row in rows} == {first_run, second_run}
        assert len({row["run_key"] for row in rows}) == 2

    def test_remove_results_removes_candidates_and_evidence(
        self, test_db, tmp_path, monkeypatch, pipeline
    ):
        from core.video_lifecycle import remove_processing_results
        from core.video_processor import process_video

        path = _write_mp4(tmp_path / "cleanup.mp4")
        video_id = test_db.insert_video("cleanup.mp4", str(path), status="ready")
        test_db.upsert_annotation(video_id, json.dumps({"no_parking": []}))
        monkeypatch.setattr("core.video_processor.Detector", lambda: _MotorcycleDetector())
        run = test_db.create_processing_run(
            video_id, json.dumps([]), viewer_mode="background"
        )
        process_video(video_id, enabled_violations=(), processing_run_id=run)

        run_dir = Path(pipeline) / "detail" / f"run_{run}"
        assert run_dir.is_dir(), "run-scoped detail evidence should exist"
        _, total = test_db.list_motorcycle_detail_candidates(video_id=video_id)
        assert total == 1

        remove_processing_results(video_id, is_busy=lambda _v: False)

        _, total = test_db.list_motorcycle_detail_candidates(video_id=video_id)
        assert total == 0
        assert not run_dir.exists()
