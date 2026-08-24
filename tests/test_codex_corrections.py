"""Codex CHANGES REQUIRED regressions: temporal evidence, process_video, worker."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

from core.detection_config import (
    DEFAULT_RULE_PARAMETERS,
    VIOLATION_OBSTRUCTION,
    VIOLATION_TRUCK_BAN,
)
from core.detector import Detector
from core.model_capability import extract_model_class_names
from core.temporal_evidence import (
    EVIDENCE_POST_SEC,
    EVIDENCE_PRE_SEC,
    TemporalEvidenceBuffer,
    TemporalEvidenceFinalizationError,
    clip_window_start,
)
from core.tracker import TRACK_EXPIRY_SEC, TrackState
from core.video_processor import ProcessVideoError, ProcessVideoResult, process_video
from core.violation_engine import RuleEngineState, evaluate_detection_rules
from database.sqlite_adapter import TemporalEvidenceNotReady


FULL_FRAME = [[0, 0], [64, 0], [64, 48], [0, 48]]


def _write_mp4(path: Path, *, frames: int = 30, fps: float = 10.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (64, 48))
    assert writer.isOpened()
    try:
        for i in range(frames):
            writer.write(np.full((48, 64, 3), i % 255, dtype=np.uint8))
    finally:
        writer.release()


def _car(ts: float, track_id: int = 9, label: str = "car") -> dict[str, Any]:
    return {
        "track_id": track_id,
        "class_label": label,
        "confidence": 0.93,
        "bbox_x": 8.0,
        "bbox_y": 8.0,
        "bbox_w": 40.0,
        "bbox_h": 28.0,
        "timestamp_sec": ts,
        "speed_px_per_sec": 0.0,
        "speed_px": 0.0,
        "is_stationary": True,
    }


class PresentThenGone:
    def __init__(self, until: float, payload: dict[str, Any] | None = None) -> None:
        self.until = until
        self.payload = payload

    def load(self) -> None:
        return None

    @property
    def class_names(self) -> set[str]:
        if self.payload:
            return {str(self.payload.get("class_label", "car"))}
        return {"car", "truck"}

    def track_frame(self, frame, conf=0.6, timestamp_sec=0.0):
        if timestamp_sec <= self.until:
            row = dict(self.payload or _car(timestamp_sec))
            row["timestamp_sec"] = timestamp_sec
            return [row]
        return []


def _params(**overrides) -> dict[str, Any]:
    out = dict(DEFAULT_RULE_PARAMETERS)
    out.update(overrides)
    return out


class TestEmptyFrameTrackExpiry:
    def test_tracker_prunes_when_detections_empty(self):
        st = TrackState()
        st.update([_car(0.0, 4)], now=0.0)
        assert 4 in st.tracks
        st.update([], now=TRACK_EXPIRY_SEC - 0.2)
        assert 4 in st.tracks
        st.update([], now=TRACK_EXPIRY_SEC + 0.5)
        assert 4 not in st.tracks

    def test_rule_engine_clears_on_empty_clock(self):
        state = RuleEngineState()
        state.observe_tracks([_car(0.0, 9)], 0.0)
        state.mark_fired(VIOLATION_OBSTRUCTION, 9, 0.0)
        assert state.already_fired(VIOLATION_OBSTRUCTION, 9)
        evaluate_detection_rules(
            [],
            state,
            frame_number=99,
            zones={"active_lane": FULL_FRAME},
            params=dict(DEFAULT_RULE_PARAMETERS),
            enabled_violations=(VIOLATION_OBSTRUCTION,),
            model_classes=("car",),
            now_sec=TRACK_EXPIRY_SEC + 1.0,
        )
        assert not state.already_fired(VIOLATION_OBSTRUCTION, 9)
        assert state.consume_cleared()


class TestTemporalEvidenceCorrections:
    def test_pre_roll_is_six_seconds(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.temporal_evidence.EVIDENCE_FOLDER", str(tmp_path / "ev"))
        fps = 5.0
        buf = TemporalEvidenceBuffer(source_key="pre", fps=fps, max_frames=80)
        confirmed = 10.0
        for i in range(int(confirmed * fps) + 1):
            buf.push(np.zeros((20, 20, 3), dtype=np.uint8), i, i / fps)
        ep = buf.begin_episode(
            violation_type=VIOLATION_OBSTRUCTION,
            track_id=1,
            confirmed_at_sec=confirmed,
            episode_start_sec=confirmed,
        )
        start = clip_window_start(
            episode_start_sec=ep.episode_start_sec,
            confirmed_at_sec=ep.confirmed_at_sec,
            pre_sec=EVIDENCE_PRE_SEC,
        )
        assert start == pytest.approx(confirmed - EVIDENCE_PRE_SEC)
        pre = [f for f in ep.spill if f.timestamp_sec < confirmed]
        assert pre
        assert pre[0].timestamp_sec == pytest.approx(start, abs=1.0 / fps)

    def test_long_episode_spools_without_keeping_jpegs(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.temporal_evidence.EVIDENCE_FOLDER", str(tmp_path / "ev"))
        buf = TemporalEvidenceBuffer(source_key="long", fps=10.0, max_frames=8, run_id=7)
        for i in range(5):
            buf.push(np.zeros((24, 24, 3), dtype=np.uint8), i, i / 10.0)
        ep = buf.begin_episode(
            violation_type=VIOLATION_OBSTRUCTION,
            track_id=1,
            confirmed_at_sec=0.4,
            episode_start_sec=0.4,
        )
        for i in range(5, 250):
            buf.push(np.zeros((24, 24, 3), dtype=np.uint8), i, i / 10.0)
        assert len(buf._frames) <= 8
        assert len(ep.spill) > 80
        assert all(f.jpeg == b"" for f in ep.spill)
        files = list(Path(ep.spool_dir).glob("*.jpg"))
        assert len(files) > 80
        assert "run_7" in str(ep.spool_dir)

    def test_abort_is_run_namespaced(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.temporal_evidence.EVIDENCE_FOLDER", str(tmp_path / "ev"))
        older = TemporalEvidenceBuffer(source_key="vid", fps=5.0, max_frames=30, run_id=10)
        for i in range(12):
            older.push(np.zeros((16, 16, 3), dtype=np.uint8), i, i / 5.0)
        older.begin_episode(
            violation_type=VIOLATION_OBSTRUCTION,
            track_id=1,
            confirmed_at_sec=1.0,
            episode_start_sec=1.0,
        )
        older.end_episode(VIOLATION_OBSTRUCTION, 1, episode_end_sec=1.2)
        for i in range(12, 35):
            older.push(np.zeros((16, 16, 3), dtype=np.uint8), i, i / 5.0)
        older.finalize_all(8.0)
        kept = [p for p in older.root_dir.iterdir() if p.is_dir() and (p / ".finalized").exists()]
        assert kept

        current = TemporalEvidenceBuffer(source_key="vid", fps=5.0, max_frames=30, run_id=11)
        for i in range(8):
            current.push(np.zeros((16, 16, 3), dtype=np.uint8), i, i / 5.0)
        ep = current.begin_episode(
            violation_type=VIOLATION_OBSTRUCTION,
            track_id=2,
            confirmed_at_sec=0.8,
            episode_start_sec=0.8,
        )
        spool = Path(ep.spool_dir)
        assert spool.exists()
        current.abort()
        assert not spool.exists()
        for d in kept:
            assert d.exists()

    def test_missing_run_id_uses_unique_namespace(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.temporal_evidence.EVIDENCE_FOLDER", str(tmp_path / "ev"))
        first = TemporalEvidenceBuffer(source_key="samevid", fps=5.0, max_frames=20)
        second = TemporalEvidenceBuffer(source_key="samevid", fps=5.0, max_frames=20)
        assert first.run_id != second.run_id
        assert first.root_dir != second.root_dir
        for i in range(6):
            first.push(np.zeros((12, 12, 3), dtype=np.uint8), i, i / 5.0)
            second.push(np.zeros((12, 12, 3), dtype=np.uint8), i, i / 5.0)
        ep_a = first.begin_episode(
            violation_type=VIOLATION_OBSTRUCTION,
            track_id=1,
            confirmed_at_sec=0.6,
            episode_start_sec=0.6,
        )
        ep_b = second.begin_episode(
            violation_type=VIOLATION_OBSTRUCTION,
            track_id=1,
            confirmed_at_sec=0.6,
            episode_start_sec=0.6,
        )
        spool_a = Path(ep_a.spool_dir)
        spool_b = Path(ep_b.spool_dir)
        assert spool_a.exists() and spool_b.exists()
        assert spool_a != spool_b
        first.abort()
        assert not spool_a.exists()
        assert spool_b.exists()

    def test_spill_dedup_scales_linearly(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.temporal_evidence.EVIDENCE_FOLDER", str(tmp_path / "ev"))
        buf = TemporalEvidenceBuffer(source_key="linear", fps=10.0, max_frames=8)
        for i in range(4):
            buf.push(np.zeros((16, 16, 3), dtype=np.uint8), i, i / 10.0)
        ep = buf.begin_episode(
            violation_type=VIOLATION_OBSTRUCTION,
            track_id=1,
            confirmed_at_sec=0.3,
            episode_start_sec=0.3,
        )

        class VisitList(list):
            visits = 0

            def __iter__(self):
                for item in list.__iter__(self):
                    type(self).visits += 1
                    yield item

        VisitList.visits = 0
        ep.spill = VisitList(ep.spill)
        extra = 360
        for i in range(4, 4 + extra):
            buf.push(np.zeros((16, 16, 3), dtype=np.uint8), i, i / 10.0)
        # Linear membership uses spilled_ids; a per-frame scan of ep.spill
        # would visit ~extra^2/2 items (tens of thousands).
        assert VisitList.visits < extra * 4
        assert len(ep.spill) > 300

    def test_long_finalize_retained_jpeg_payload_stays_bounded(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("core.temporal_evidence.EVIDENCE_FOLDER", str(tmp_path / "ev"))
        fps = 10.0
        buf = TemporalEvidenceBuffer(source_key="peak", fps=fps, max_frames=12)
        n = 220
        for i in range(8):
            buf.push(np.zeros((20, 20, 3), dtype=np.uint8), i, i / fps)
        ep = buf.begin_episode(
            violation_type=VIOLATION_OBSTRUCTION,
            track_id=1,
            confirmed_at_sec=0.5,
            episode_start_sec=0.5,
        )
        for i in range(8, n):
            buf.push(np.zeros((20, 20, 3), dtype=np.uint8), i, i / fps)
        spool_files = list(Path(ep.spool_dir).glob("*.jpg"))
        assert len(spool_files) > 150
        sizes = [p.stat().st_size for p in spool_files]
        total = sum(sizes)
        max_one = max(sizes)

        held = {"peak": 0}
        orig = TemporalEvidenceBuffer._iter_window_frames

        def spy(self, episode, now_sec):
            seen: list = []
            for bf in orig(self, episode, now_sec):
                seen.append(bf)
                live = sum(len(x.jpeg) for x in seen)
                held["peak"] = max(held["peak"], live)
                yield bf

        monkeypatch.setattr(TemporalEvidenceBuffer, "_iter_window_frames", spy)
        buf.end_episode(VIOLATION_OBSTRUCTION, 1, episode_end_sec=(n - 1) / fps)
        done = buf.finalize_all((n - 1) / fps)
        assert done and done[0].finalized
        assert held["peak"] > 0
        assert held["peak"] <= max_one * 4
        assert held["peak"] < total * 0.10

    def test_corrupt_spool_does_not_finalize(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.temporal_evidence.EVIDENCE_FOLDER", str(tmp_path / "ev"))
        buf = TemporalEvidenceBuffer(source_key="corrupt", fps=10.0, max_frames=6)
        for i in range(6):
            buf.push(np.zeros((16, 16, 3), dtype=np.uint8), i, i / 10.0)
        ep = buf.begin_episode(
            violation_type=VIOLATION_OBSTRUCTION,
            track_id=1,
            confirmed_at_sec=0.4,
            episode_start_sec=0.4,
        )
        for i in range(6, 40):
            buf.push(np.zeros((16, 16, 3), dtype=np.uint8), i, i / 10.0)
        buf.end_episode(VIOLATION_OBSTRUCTION, 1, episode_end_sec=3.5)
        spool = Path(ep.spool_dir)
        assert spool.exists()
        for jpg in spool.glob("*.jpg"):
            jpg.unlink()
        with pytest.raises(TemporalEvidenceFinalizationError, match="no evidence frames"):
            buf.finalize_all(6.0)
        assert ep.finalized is False
        assert ep.sequence_dir is None
        assert ep.clip_path is None
        assert (VIOLATION_OBSTRUCTION, 1) in buf._open_episodes
        assert list(buf.root_dir.rglob(".finalized")) == []
        assert buf._finalized == []

    def test_write_failure_keeps_episode_open(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.temporal_evidence.EVIDENCE_FOLDER", str(tmp_path / "ev"))
        buf = TemporalEvidenceBuffer(source_key="writefail", fps=10.0, max_frames=8)
        for i in range(8):
            buf.push(np.zeros((16, 16, 3), dtype=np.uint8), i, i / 10.0)
        ep = buf.begin_episode(
            violation_type=VIOLATION_OBSTRUCTION,
            track_id=1,
            confirmed_at_sec=0.5,
            episode_start_sec=0.5,
        )
        for i in range(8, 25):
            buf.push(np.zeros((16, 16, 3), dtype=np.uint8), i, i / 10.0)
        buf.end_episode(VIOLATION_OBSTRUCTION, 1, episode_end_sec=2.0)

        orig = Path.write_bytes

        def boom(self, data):
            if self.suffix == ".jpg" and "_n" in self.name:
                raise OSError("simulated sequence write failure")
            return orig(self, data)

        monkeypatch.setattr(Path, "write_bytes", boom)
        with pytest.raises(OSError, match="simulated sequence write failure"):
            buf.finalize_all(5.0)
        assert (VIOLATION_OBSTRUCTION, 1) in buf._open_episodes
        kept = buf._open_episodes[(VIOLATION_OBSTRUCTION, 1)]
        assert kept is ep
        assert kept.finalized is False
        assert kept.sequence_dir is None
        assert kept.clip_path is None
        assert list(buf.root_dir.rglob(".finalized")) == []
        assert Path(ep.spool_dir).exists()

    def test_three_second_post_roll(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.temporal_evidence.EVIDENCE_FOLDER", str(tmp_path / "ev"))
        fps = 10.0
        buf = TemporalEvidenceBuffer(source_key="post", fps=fps, max_frames=80)
        for i in range(20):
            buf.push(np.zeros((16, 16, 3), dtype=np.uint8), i, i / fps)
        buf.begin_episode(
            violation_type=VIOLATION_OBSTRUCTION,
            track_id=1,
            confirmed_at_sec=1.0,
            episode_start_sec=1.0,
        )
        buf.end_episode(VIOLATION_OBSTRUCTION, 1, episode_end_sec=1.5)
        assert buf.push(np.zeros((16, 16, 3), dtype=np.uint8), 20, 1.5 + 2.9) == []
        done = buf.push(np.zeros((16, 16, 3), dtype=np.uint8), 21, 1.5 + EVIDENCE_POST_SEC)
        assert done and done[0].finalized

    def test_fps_below_one_written_to_clip(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.temporal_evidence.EVIDENCE_FOLDER", str(tmp_path / "ev"))
        buf = TemporalEvidenceBuffer(source_key="slow", fps=0.5, max_frames=20)
        assert buf.fps == pytest.approx(0.5)
        for i in range(8):
            buf.push(np.zeros((18, 18, 3), dtype=np.uint8), i, i * 2.0)
        buf.begin_episode(
            violation_type=VIOLATION_OBSTRUCTION,
            track_id=1,
            confirmed_at_sec=6.0,
            episode_start_sec=6.0,
        )
        buf.end_episode(VIOLATION_OBSTRUCTION, 1, episode_end_sec=6.0)
        finalized: list = []
        for i in range(8, 12):
            finalized.extend(buf.push(np.zeros((18, 18, 3), dtype=np.uint8), i, i * 2.0))
        if not finalized:
            finalized = buf.finalize_all(24.0)
        assert finalized and finalized[0].clip_path
        clips = list((tmp_path / "ev").rglob("*.mp4"))
        assert clips, "finalized clip artifact was not written to disk"
        cap = cv2.VideoCapture(str(clips[0]))
        try:
            assert cap.isOpened(), f"could not open clip {clips[0]}"
            assert float(cap.get(cv2.CAP_PROP_FPS)) == pytest.approx(0.5, abs=0.1)
        finally:
            cap.release()


class TestReviewConfirmGate:
    def test_blocks_until_finalized_then_allows(self, test_db):
        user_id = test_db.create_user("reviewer_a", "hash", role="enforcer", full_name="A")
        vid = test_db.insert_video(filename="a.mp4", filepath="/tmp/a.mp4", status="processed")
        rid = test_db.insert_review_queue(
            video_id=vid,
            track_id=1,
            violation_type=VIOLATION_OBSTRUCTION,
            confidence=0.9,
            frame_number=10,
            timestamp_sec=2.0,
            evidence_pre_sec=EVIDENCE_PRE_SEC,
            evidence_post_sec=EVIDENCE_POST_SEC,
            episode_start_sec=2.0,
        )
        with pytest.raises(TemporalEvidenceNotReady):
            test_db.confirm_review_item(rid, reviewed_by=user_id)
        test_db.update_review_temporal_evidence(
            rid,
            evidence_clip_path="evidence/clip.mp4",
            evidence_sequence_dir="evidence/seq",
            episode_end_sec=5.0,
        )
        assert test_db.confirm_review_item(rid, reviewed_by=user_id) > 0

    def test_end_only_evidence_is_blocked(self, test_db):
        user_id = test_db.create_user("reviewer_end", "hash", role="enforcer", full_name="E")
        vid = test_db.insert_video(filename="end.mp4", filepath="/tmp/end.mp4", status="processed")
        rid = test_db.insert_review_queue(
            video_id=vid,
            track_id=1,
            violation_type=VIOLATION_OBSTRUCTION,
            confidence=0.9,
            frame_number=10,
            timestamp_sec=2.0,
            evidence_pre_sec=EVIDENCE_PRE_SEC,
            evidence_post_sec=EVIDENCE_POST_SEC,
            episode_start_sec=2.0,
            episode_end_sec=5.0,
        )
        with pytest.raises(TemporalEvidenceNotReady):
            test_db.confirm_review_item(rid, reviewed_by=user_id)

    def test_artifact_only_evidence_is_blocked(self, test_db):
        user_id = test_db.create_user("reviewer_art", "hash", role="enforcer", full_name="R")
        vid = test_db.insert_video(filename="art.mp4", filepath="/tmp/art.mp4", status="processed")
        rid = test_db.insert_review_queue(
            video_id=vid,
            track_id=1,
            violation_type=VIOLATION_OBSTRUCTION,
            confidence=0.9,
            frame_number=10,
            timestamp_sec=2.0,
            evidence_clip_path="evidence/clip.mp4",
            evidence_sequence_dir="evidence/seq",
            evidence_pre_sec=EVIDENCE_PRE_SEC,
            evidence_post_sec=EVIDENCE_POST_SEC,
            episode_start_sec=2.0,
        )
        with pytest.raises(TemporalEvidenceNotReady):
            test_db.confirm_review_item(rid, reviewed_by=user_id)

    def test_artifact_plus_end_is_allowed(self, test_db):
        user_id = test_db.create_user("reviewer_both", "hash", role="enforcer", full_name="B")
        vid = test_db.insert_video(filename="both.mp4", filepath="/tmp/both.mp4", status="processed")
        rid = test_db.insert_review_queue(
            video_id=vid,
            track_id=1,
            violation_type=VIOLATION_OBSTRUCTION,
            confidence=0.9,
            frame_number=10,
            timestamp_sec=2.0,
            evidence_clip_path="evidence/clip.mp4",
            evidence_sequence_dir="evidence/seq",
            evidence_pre_sec=EVIDENCE_PRE_SEC,
            evidence_post_sec=EVIDENCE_POST_SEC,
            episode_start_sec=2.0,
            episode_end_sec=5.0,
        )
        assert test_db.confirm_review_item(rid, reviewed_by=user_id) > 0

    def test_confirm_api_end_only_is_409(self, test_db, enforcer_client):
        vid = test_db.insert_video(filename="api_end.mp4", filepath="/tmp/api_end.mp4", status="processed")
        rid = test_db.insert_review_queue(
            video_id=vid,
            track_id=1,
            violation_type=VIOLATION_OBSTRUCTION,
            confidence=0.9,
            frame_number=10,
            timestamp_sec=2.0,
            evidence_pre_sec=EVIDENCE_PRE_SEC,
            evidence_post_sec=EVIDENCE_POST_SEC,
            episode_start_sec=2.0,
            episode_end_sec=5.0,
        )
        resp = enforcer_client.post(f"/api/review-queue/{rid}/confirm")
        assert resp.status_code == 409

    def test_confirm_api_artifact_only_is_409(self, test_db, enforcer_client):
        vid = test_db.insert_video(filename="api_art.mp4", filepath="/tmp/api_art.mp4", status="processed")
        rid = test_db.insert_review_queue(
            video_id=vid,
            track_id=1,
            violation_type=VIOLATION_OBSTRUCTION,
            confidence=0.9,
            frame_number=10,
            timestamp_sec=2.0,
            evidence_clip_path="evidence/clip.mp4",
            evidence_sequence_dir="evidence/seq",
            evidence_pre_sec=EVIDENCE_PRE_SEC,
            evidence_post_sec=EVIDENCE_POST_SEC,
            episode_start_sec=2.0,
        )
        resp = enforcer_client.post(f"/api/review-queue/{rid}/confirm")
        assert resp.status_code == 409

    def test_confirm_api_artifact_plus_end_is_allowed(self, test_db, enforcer_client):
        vid = test_db.insert_video(filename="api_ok.mp4", filepath="/tmp/api_ok.mp4", status="processed")
        rid = test_db.insert_review_queue(
            video_id=vid,
            track_id=1,
            violation_type=VIOLATION_OBSTRUCTION,
            confidence=0.9,
            frame_number=10,
            timestamp_sec=2.0,
            evidence_clip_path="evidence/clip.mp4",
            evidence_sequence_dir="evidence/seq",
            evidence_pre_sec=EVIDENCE_PRE_SEC,
            evidence_post_sec=EVIDENCE_POST_SEC,
            episode_start_sec=2.0,
            episode_end_sec=5.0,
        )
        resp = enforcer_client.post(f"/api/review-queue/{rid}/confirm")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body.get("violation_id")

    def test_propagates_to_already_confirmed_violation(self, test_db):
        user_id = test_db.create_user("reviewer_b", "hash", role="enforcer", full_name="B")
        vid = test_db.insert_video(filename="b.mp4", filepath="/tmp/b.mp4", status="processed")
        rid = test_db.insert_review_queue(
            video_id=vid,
            track_id=3,
            violation_type=VIOLATION_OBSTRUCTION,
            confidence=0.9,
            frame_number=4,
            timestamp_sec=1.0,
            evidence_clip_path="tmp/placeholder.mp4",
            evidence_pre_sec=EVIDENCE_PRE_SEC,
            evidence_post_sec=EVIDENCE_POST_SEC,
            episode_start_sec=1.0,
            episode_end_sec=1.0,
        )
        violation_id = test_db.confirm_review_item(rid, reviewed_by=user_id)
        test_db.update_review_temporal_evidence(
            rid,
            evidence_clip_path="evidence/final.mp4",
            evidence_sequence_dir="evidence/final_seq",
            episode_end_sec=4.5,
        )
        row = test_db.get_violation(violation_id)
        assert row is not None
        assert "final.mp4" in (row.get("evidence_clip_path") or "")


class TestProcessVideoAndWorker:
    def test_disappearance_finalizes_before_eov(self, test_db, tmp_path, monkeypatch):
        path = tmp_path / "gone.mp4"
        fps = 10.0
        _write_mp4(path, frames=140, fps=fps)
        monkeypatch.setattr("core.temporal_evidence.EVIDENCE_FOLDER", str(tmp_path / "evidence"))
        monkeypatch.setattr("core.evidence.EVIDENCE_FOLDER", str(tmp_path / "evidence"))
        vid = test_db.insert_video(filename="gone.mp4", filepath=str(path), status="ready")
        test_db.upsert_annotation(vid, json.dumps({"active_lane": FULL_FRAME}))
        monkeypatch.setattr("core.video_processor.Detector", lambda: PresentThenGone(1.8))
        monkeypatch.setattr(
            "core.video_processor.load_rule_parameters",
            lambda: {
                **DEFAULT_RULE_PARAMETERS,
                "frame_skip": 1,
                "obstruction_dwell_sec": 0.4,
                "stationary_px": 80.0,
                "confidence_threshold": 0.3,
            },
        )
        result = process_video(vid, enabled_violations=(VIOLATION_OBSTRUCTION,))
        assert list(result) == list(result.events)
        reviews, total = test_db.list_review_queue(status="pending", page=1, per_page=20)
        assert total >= 1
        item = test_db.get_review_item(reviews[0]["id"])
        last_ts = 139 / fps
        assert item["episode_end_sec"] is not None
        assert item["episode_end_sec"] < last_ts - 1.0
        assert item.get("evidence_clip_path") or item.get("evidence_sequence_dir")

    def test_clear_rearm_and_expiry(self, test_db, tmp_path, monkeypatch):
        path = tmp_path / "rearm.mp4"
        _write_mp4(path, frames=180, fps=10.0)
        monkeypatch.setattr("core.temporal_evidence.EVIDENCE_FOLDER", str(tmp_path / "evidence"))
        monkeypatch.setattr("core.evidence.EVIDENCE_FOLDER", str(tmp_path / "evidence"))
        vid = test_db.insert_video(filename="rearm.mp4", filepath=str(path), status="ready")
        test_db.upsert_annotation(vid, json.dumps({"active_lane": FULL_FRAME}))

        class Pulse(PresentThenGone):
            def track_frame(self, frame, conf=0.6, timestamp_sec=0.0):
                if timestamp_sec <= 1.2 or timestamp_sec >= 11.0:
                    return [_car(timestamp_sec)]
                return []

        monkeypatch.setattr("core.video_processor.Detector", lambda: Pulse(1.2))
        monkeypatch.setattr(
            "core.video_processor.load_rule_parameters",
            lambda: {
                **DEFAULT_RULE_PARAMETERS,
                "frame_skip": 1,
                "obstruction_dwell_sec": 0.3,
                "stationary_px": 80.0,
                "confidence_threshold": 0.3,
            },
        )
        process_video(vid, enabled_violations=(VIOLATION_OBSTRUCTION,))
        reviews, total = test_db.list_review_queue(status="pending", page=1, per_page=50)
        assert total == 2
        starts = sorted(
            float(r["episode_start_sec"] if r.get("episode_start_sec") is not None else r["timestamp_sec"])
            for r in reviews
        )
        assert starts[1] - starts[0] >= 5.0
        assert reviews[0]["id"] != reviews[1]["id"]

    def test_truck_ban_known_vs_unknown(self, test_db, tmp_path, monkeypatch):
        monkeypatch.setattr("core.temporal_evidence.EVIDENCE_FOLDER", str(tmp_path / "evidence"))
        monkeypatch.setattr("core.evidence.EVIDENCE_FOLDER", str(tmp_path / "evidence"))
        monkeypatch.setattr(
            "core.video_processor.load_rule_parameters",
            lambda: _params(frame_skip=1, confidence_threshold=0.3),
        )

        def _count(*, recorded_at: str | None) -> int:
            tag = "known" if recorded_at else "unknown"
            path = tmp_path / f"{tag}.mp4"
            _write_mp4(path, frames=40, fps=10.0)
            vid = test_db.insert_video(
                filename=f"{tag}.mp4",
                filepath=str(path),
                status="ready",
                recorded_at=recorded_at,
            )
            test_db.upsert_annotation(vid, json.dumps({"truck_ban_zone": FULL_FRAME}))
            payload = _car(0.0, track_id=2, label="truck")
            monkeypatch.setattr(
                "core.video_processor.Detector",
                lambda: PresentThenGone(4.0, payload=payload),
            )
            before = test_db.list_review_queue(status="pending", page=1, per_page=50)[1]
            process_video(vid, enabled_violations=(VIOLATION_TRUCK_BAN,))
            after = test_db.list_review_queue(status="pending", page=1, per_page=50)[1]
            return after - before

        assert _count(recorded_at="2026-08-20 07:15:00") >= 1
        assert _count(recorded_at=None) == 0

    def test_frame_skip_gt_source_fps(self, test_db, tmp_path, monkeypatch):
        path = tmp_path / "skip.mp4"
        _write_mp4(path, frames=30, fps=10.0)
        monkeypatch.setattr("core.temporal_evidence.EVIDENCE_FOLDER", str(tmp_path / "evidence"))
        monkeypatch.setattr("core.evidence.EVIDENCE_FOLDER", str(tmp_path / "evidence"))
        vid = test_db.insert_video(filename="skip.mp4", filepath=str(path), status="ready")
        test_db.upsert_annotation(vid, json.dumps({"active_lane": FULL_FRAME}))
        monkeypatch.setattr("core.video_processor.Detector", lambda: PresentThenGone(3.0))
        monkeypatch.setattr(
            "core.video_processor.load_rule_parameters",
            lambda: {
                **DEFAULT_RULE_PARAMETERS,
                "frame_skip": 20,
                "obstruction_dwell_sec": 0.01,
                "confidence_threshold": 0.3,
            },
        )
        result = process_video(vid, enabled_violations=(VIOLATION_OBSTRUCTION,))
        assert isinstance(result, ProcessVideoResult)

    def test_geometry_preserved_on_detector_failure(self, test_db, tmp_path, monkeypatch):
        path = tmp_path / "geo.mp4"
        _write_mp4(path, frames=6, fps=10.0)
        vid = test_db.insert_video(filename="geo.mp4", filepath=str(path), status="ready")
        test_db.upsert_annotation(vid, json.dumps({"active_lane": FULL_FRAME}))

        class Boom:
            def load(self):
                raise RuntimeError("weights missing")

        monkeypatch.setattr("core.video_processor.Detector", Boom)
        with pytest.raises(ProcessVideoError) as err:
            process_video(vid)
        assert err.value.geometry_snapshot_json
        snap = json.loads(err.value.geometry_snapshot_json)
        assert snap.get("frame_width") or snap.get("mode")

    def test_unopened_capture_released(self, test_db, tmp_path, monkeypatch):
        vid = test_db.insert_video(
            filename="missing.mp4",
            filepath=str(tmp_path / "nope.mp4"),
            status="ready",
        )
        test_db.upsert_annotation(vid, json.dumps({"active_lane": FULL_FRAME}))
        released = {"n": 0}
        orig = cv2.VideoCapture

        class Spy:
            def __init__(self, *a, **k):
                self._cap = orig(*a, **k)

            def isOpened(self):
                return self._cap.isOpened()

            def release(self):
                released["n"] += 1
                return self._cap.release()

            def __getattr__(self, name):
                return getattr(self._cap, name)

        monkeypatch.setattr("core.video_processor.cv2.VideoCapture", Spy)
        with pytest.raises(ProcessVideoError):
            process_video(vid)
        assert released["n"] >= 1

    def test_list_compatible_result(self, test_db, tmp_path, monkeypatch):
        path = tmp_path / "list.mp4"
        _write_mp4(path, frames=10, fps=10.0)
        monkeypatch.setattr("core.temporal_evidence.EVIDENCE_FOLDER", str(tmp_path / "evidence"))
        monkeypatch.setattr("core.evidence.EVIDENCE_FOLDER", str(tmp_path / "evidence"))
        vid = test_db.insert_video(filename="list.mp4", filepath=str(path), status="ready")
        test_db.upsert_annotation(vid, json.dumps({"active_lane": FULL_FRAME}))
        monkeypatch.setattr("core.video_processor.Detector", lambda: PresentThenGone(1.0))
        monkeypatch.setattr(
            "core.video_processor.load_rule_parameters",
            lambda: _params(frame_skip=1, confidence_threshold=0.3),
        )
        result = process_video(vid, enabled_violations=())
        assert list(result) == list(result.events)
        assert len(result) == len(result.events)

    def test_run_row_failure_keeps_processed_status(
        self, enforcer_client, test_db, monkeypatch
    ):
        import app as app_module

        vid = test_db.insert_video(filename="ok.mp4", filepath="/tmp/ok.mp4", status="ready")
        test_db.upsert_annotation(vid, json.dumps({"z": [[0, 0], [1, 0], [1, 1], [0, 1]]}))

        def fake_process(video_id, enabled_violations=None, **kwargs):
            test_db.mark_video_processed(video_id)
            return ProcessVideoResult(
                events=[],
                diagnostics_json='{"ok": true}',
                geometry_snapshot_json='{"mode": "normalized"}',
            )

        def boom(*a, **k):
            raise RuntimeError("cannot write processing run row")

        monkeypatch.setattr(app_module, "process_video", fake_process)
        monkeypatch.setattr("database.db.finish_processing_run", boom)
        monkeypatch.setattr(test_db, "finish_processing_run", boom)
        resp = enforcer_client.post(
            f"/api/videos/{vid}/process",
            json={"enabled_violations": []},
            content_type="application/json",
        )
        assert resp.status_code == 200
        deadline = time.time() + 5.0
        while time.time() < deadline:
            row = test_db.get_video(vid)
            if row and row["status"] == "processed":
                break
            time.sleep(0.05)
        assert test_db.get_video(vid)["status"] == "processed"

    def test_pipeline_typeerror_is_not_retried(
        self, enforcer_client, test_db, monkeypatch
    ):
        import app as app_module

        calls = {"n": 0}

        def boom(video_id, enabled_violations=None, **kwargs):
            calls["n"] += 1
            raise TypeError("genuine pipeline TypeError")

        vid = test_db.insert_video(filename="boom.mp4", filepath="/tmp/boom.mp4", status="ready")
        test_db.upsert_annotation(vid, json.dumps({"z": [[0, 0], [1, 0], [1, 1], [0, 1]]}))
        monkeypatch.setattr(app_module, "process_video", boom)
        resp = enforcer_client.post(
            f"/api/videos/{vid}/process",
            json={"enabled_violations": []},
            content_type="application/json",
        )
        assert resp.status_code == 200
        deadline = time.time() + 5.0
        while time.time() < deadline and calls["n"] == 0:
            time.sleep(0.05)
        time.sleep(0.2)
        assert calls["n"] == 1
        row = test_db.get_video(vid)
        assert row["status"] != "processed"

    def test_worker_persists_real_process_video_result(
        self, enforcer_client, test_db, tmp_path, monkeypatch
    ):
        path = tmp_path / "worker.mp4"
        _write_mp4(path, frames=20, fps=10.0)
        monkeypatch.setattr("core.temporal_evidence.EVIDENCE_FOLDER", str(tmp_path / "evidence"))
        monkeypatch.setattr("core.evidence.EVIDENCE_FOLDER", str(tmp_path / "evidence"))
        vid = test_db.insert_video(
            filename="worker.mp4",
            filepath=str(path),
            status="ready",
            recorded_at="2026-08-20 07:10:00",
        )
        test_db.upsert_annotation(vid, json.dumps({"truck_ban_zone": FULL_FRAME}))
        monkeypatch.setattr(
            "core.video_processor.Detector",
            lambda: PresentThenGone(2.5, payload=_car(0.0, 2, "truck")),
        )
        monkeypatch.setattr(
            "core.video_processor.load_rule_parameters",
            lambda: _params(frame_skip=1, confidence_threshold=0.3),
        )
        resp = enforcer_client.post(
            f"/api/videos/{vid}/process",
            json={"enabled_violations": [VIOLATION_TRUCK_BAN]},
            content_type="application/json",
        )
        assert resp.status_code == 200
        deadline = time.time() + 8.0
        run = None
        while time.time() < deadline:
            runs = test_db.list_processing_runs(vid)
            if runs and runs[0]["status"] in ("completed", "failed"):
                run = runs[0]
                break
            time.sleep(0.05)
        assert run is not None
        assert run["status"] == "completed"
        assert test_db.get_video(vid)["status"] == "processed"
        assert run.get("diagnostics_json")
        assert run.get("geometry_snapshot_json")


class TestDetectorClassNamesContract:
    def test_nonnumeric_keys_fail_closed_without_raise(self):
        det = Detector.__new__(Detector)
        det._weights = "x"
        det.is_custom = False
        det._model = type("M", (), {"names": {"cam": "Car", 3: "truck"}})()
        det.load = lambda: None  # type: ignore[method-assign]
        assert det.class_names == set()
        assert extract_model_class_names(det) == ()
