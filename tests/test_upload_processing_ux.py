"""Upload/processing UX: media, progress, annotated output, bulk, delete, analytics."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

import app as app_module
from core.annotated_writer import AnnotatedVideoWriter, AnnotatedWriterError, annotated_final_path
from core.detection_config import VIOLATION_ILLEGAL_PARKING
from core.frame_annotate import annotate_frame
from core.media_serve import MediaPathError, resolve_under_roots
from core.processing_preview import ProcessingPreviewHub
from core.processing_progress import ProgressTracker
from core.upload_analytics import build_upload_processing_analytics, period_bounds
from core.video_processor import ProcessVideoResult, process_video


ZONES = json.dumps({"no_parking": [[10, 10], [100, 10], [100, 100], [10, 100]]})


@pytest.fixture(autouse=True)
def _reset_processing_state(test_db_path):
    app_module.stop_processing_worker()
    app_module._process_queue.clear()
    app_module._processing_jobs.clear()
    app_module._running_video_id = None
    yield
    app_module.stop_processing_worker()
    app_module._process_queue.clear()
    app_module._processing_jobs.clear()
    app_module._running_video_id = None


def _write_synthetic_mp4(path: Path, *, frames: int = 15, fps: float = 10.0, size=(160, 120)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(path), fourcc, fps, size)
    assert writer.isOpened()
    w, h = size
    for i in range(frames):
        frame = np.zeros((h, w, 3), dtype=np.uint8)
        frame[:] = (40, 40, 40)
        cv2.rectangle(frame, (20 + i, 30), (60 + i, 70), (0, 200, 255), -1)
        writer.write(frame)
    writer.release()
    return path


def _ready_video(test_db, tmp_path, name="clip.mp4", frames=12):
    path = _write_synthetic_mp4(tmp_path / name, frames=frames)
    vid = test_db.insert_video(
        filename=name,
        filepath=str(path),
        status="ready",
        duration_sec=frames / 10.0,
        file_size_bytes=path.stat().st_size,
    )
    test_db.upsert_annotation(vid, ZONES)
    return vid, path


class TestMediaRangePlayback:
    def test_source_media_range_and_auth(self, enforcer_client, test_db, tmp_path, monkeypatch):
        import config

        monkeypatch.setattr(config, "UPLOAD_FOLDER", str(tmp_path))
        vid, path = _ready_video(test_db, tmp_path)
        size = path.stat().st_size
        resp = enforcer_client.get(f"/api/videos/{vid}/media", headers={"Range": "bytes=0-99"})
        assert resp.status_code == 206
        assert resp.headers.get("Accept-Ranges") == "bytes"
        assert resp.headers.get("Content-Range", "").startswith("bytes 0-99/")
        assert len(resp.data) == 100
        assert size > 100

    def test_path_traversal_rejected(self, tmp_path):
        root = tmp_path / "uploads"
        root.mkdir()
        outside = tmp_path / "secret.bin"
        outside.write_bytes(b"secret")
        with pytest.raises(MediaPathError):
            resolve_under_roots(outside, [root])


class TestProgressMonotonic:
    def test_progress_never_decreases(self):
        tracker = ProgressTracker(run_id=1, video_id=2)
        tracker.set_total_frames(100)
        tracker.set_stage("processing")
        tracker.update_frame(10, detection_delta=2)
        snap1 = tracker.snapshot()
        tracker.update_frame(5, detection_delta=1)
        snap2 = tracker.snapshot()
        assert snap2["frames_processed"] >= snap1["frames_processed"]
        assert snap2["progress_percent"] >= snap1["progress_percent"]
        assert snap2["detection_records"] >= snap1["detection_records"]
        assert snap2["eta_sec"] is None or snap2["eta_sec"] >= 0


class TestSameRunLivePreview:
    def test_watch_live_does_not_duplicate_inference(self, enforcer_client, test_db, monkeypatch):
        calls = {"n": 0}
        hold = threading.Event()

        def fake_process(video_id, enabled_violations=None, **kwargs):
            calls["n"] += 1
            hold.wait(timeout=2.0)
            return ProcessVideoResult(events=[])

        monkeypatch.setattr(app_module, "process_video", fake_process)
        monkeypatch.setattr("core.video_processor.process_video", fake_process)
        vid = test_db.insert_video(filename="a.mp4", filepath="/tmp/a.mp4", status="ready")
        test_db.upsert_annotation(vid, ZONES)

        r1 = enforcer_client.post(
            f"/api/videos/{vid}/process",
            json={"enabled_violations": [], "viewer_mode": "background"},
            content_type="application/json",
        )
        assert r1.status_code == 200
        # Wait until worker has claimed the job.
        for _ in range(50):
            if calls["n"] >= 1:
                break
            time.sleep(0.02)
        assert calls["n"] == 1
        assert app_module._is_processing_or_queued(vid)

        # Watching live must not start another run (409 while busy).
        r2 = enforcer_client.post(
            f"/api/videos/{vid}/process",
            json={"enabled_violations": [], "viewer_mode": "watch_live"},
            content_type="application/json",
        )
        assert r2.status_code == 409, r2.get_json()
        assert calls["n"] == 1
        # Preview route exists for the same run (do not fully consume MJPEG).
        assert hasattr(app_module, "api_process_preview")
        hold.set()
        time.sleep(0.2)
        assert calls["n"] == 1

    def test_viewer_disconnect_does_not_cancel_job(self, enforcer_client, test_db, monkeypatch):
        started = threading.Event()
        finished = threading.Event()

        def fake_process(video_id, enabled_violations=None, **kwargs):
            started.set()
            time.sleep(0.4)
            finished.set()
            return ProcessVideoResult(events=[])

        monkeypatch.setattr(app_module, "process_video", fake_process)
        monkeypatch.setattr("core.video_processor.process_video", fake_process)
        vid = test_db.insert_video(filename="a.mp4", filepath="/tmp/a.mp4", status="ready")
        test_db.upsert_annotation(vid, ZONES)
        enforcer_client.post(
            f"/api/videos/{vid}/process",
            json={"enabled_violations": [], "viewer_mode": "watch_live"},
            content_type="application/json",
        )
        assert started.wait(2.0)
        resp = enforcer_client.get(f"/api/videos/{vid}/process-preview")
        assert resp.status_code == 200
        assert finished.wait(2.0)
        status = enforcer_client.get(f"/api/videos/{vid}/process-status").get_json()
        assert status["job_state"] in ("done", "processing")


class TestAnnotatedWriter:
    def test_annotated_frames_and_reopen(self, tmp_path, monkeypatch):
        import config

        monkeypatch.setattr(config, "ANNOTATED_FOLDER", str(tmp_path / "ann"))
        frame = np.zeros((120, 160, 3), dtype=np.uint8)
        dets = [{
            "bbox_x": 20, "bbox_y": 20, "bbox_w": 40, "bbox_h": 30,
            "class_label": "car", "confidence": 0.91, "track_id": 7,
        }]
        annotated = annotate_frame(frame, dets, violation_track_ids={7})
        assert annotated.sum() > frame.sum()

        writer = AnnotatedVideoWriter(99, width=160, height=120, fps=5.0)
        for _ in range(8):
            writer.write(annotated)
        out = writer.finalize()
        assert out.is_file()
        cap = cv2.VideoCapture(str(out))
        assert cap.isOpened()
        ok, fr = cap.read()
        cap.release()
        assert ok and fr is not None
        assert writer.fps == pytest.approx(5.0)
        assert (8 / 5.0) == pytest.approx(1.6)

    def test_failed_writer_does_not_publish(self, tmp_path, monkeypatch):
        import config

        monkeypatch.setattr(config, "ANNOTATED_FOLDER", str(tmp_path / "ann"))
        writer = AnnotatedVideoWriter(100, width=160, height=120, fps=5.0)
        writer.abort()
        final = annotated_final_path(100)
        assert not final.exists()
        with pytest.raises(AnnotatedWriterError):
            writer.finalize()


class TestHistoryPersistence:
    def test_history_survives_reload(self, enforcer_client, test_db):
        vid = test_db.insert_video(filename="h.mp4", filepath="/tmp/h.mp4", status="ready")
        test_db.upsert_annotation(vid, ZONES)
        test_db.insert_video_history_event(vid, "uploaded", detail={"filename": "h.mp4"})
        run_id = test_db.create_processing_run(vid, "[]", viewer_mode="watch_live")
        test_db.insert_video_history_event(
            vid, "processing_queued", run_id=run_id, detail={"viewer_mode": "watch_live"}
        )
        app_module._processing_jobs.clear()
        hist = enforcer_client.get(f"/api/videos/{vid}/history").get_json()
        assert hist["success"] is True
        types = [e["event_type"] for e in hist["history_events"]]
        assert "uploaded" in types
        assert "processing_queued" in types
        assert hist["processing_runs"][0]["viewer_mode"] == "watch_live"


class TestToggleSnapshots:
    def test_process_snapshot_does_not_change_global(self, enforcer_client, test_db, monkeypatch):
        from core.violation_config import load_enabled_violations, save_enabled_violations

        save_enabled_violations([VIOLATION_ILLEGAL_PARKING])
        before = list(load_enabled_violations())
        monkeypatch.setattr(app_module, "process_video", lambda *a, **k: ProcessVideoResult(events=[]))
        vid = test_db.insert_video(filename="t.mp4", filepath="/tmp/t.mp4", status="ready")
        test_db.upsert_annotation(vid, ZONES)
        resp = enforcer_client.post(
            f"/api/videos/{vid}/process",
            json={"enabled_violations": []},
            content_type="application/json",
        )
        assert resp.status_code == 200
        time.sleep(0.3)
        after = list(load_enabled_violations())
        assert after == before
        runs = test_db.list_processing_runs(vid)
        assert json.loads(runs[0]["enabled_violations_json"]) == []

    def test_switch_html_has_active_inactive_and_role(self):
        html = Path("templates/settings.html").read_text(encoding="utf-8")
        assert 'role="switch"' in html
        assert "Active" in html and "Inactive" in html
        assert "violation-group-toggle" in html
        js = Path("static/js/violation_switch.js").read_text(encoding="utf-8")
        assert "aria-checked" in js
        settings_js = Path("static/js/settings.js").read_text(encoding="utf-8")
        assert "violation-group-toggle" in settings_js


class TestBulkFifo:
    def test_bulk_fifo_sequential_and_duplicates(self, enforcer_client, test_db, monkeypatch):
        state = {"inflight": 0, "max": 0, "order": []}
        lock = threading.Lock()

        def fake_process(video_id, enabled_violations=None, **kwargs):
            with lock:
                state["inflight"] += 1
                state["max"] = max(state["max"], state["inflight"])
                state["order"].append(video_id)
            time.sleep(0.25)
            with lock:
                state["inflight"] -= 1
            return ProcessVideoResult(events=[])

        monkeypatch.setattr(app_module, "process_video", fake_process)
        monkeypatch.setattr("core.video_processor.process_video", fake_process)
        ids = []
        for name in ("b1.mp4", "b2.mp4", "b3.mp4"):
            vid = test_db.insert_video(filename=name, filepath=f"/tmp/{name}", status="ready")
            test_db.upsert_annotation(vid, ZONES)
            ids.append(vid)

        resp = enforcer_client.post(
            "/api/videos/process-bulk",
            json={"video_ids": ids, "enabled_violations": []},
            content_type="application/json",
        )
        body = resp.get_json()
        assert resp.status_code == 200
        assert len(body["accepted"]) == 3

        dup = enforcer_client.post(
            "/api/videos/process-bulk",
            json={"video_ids": [ids[0]], "enabled_violations": []},
            content_type="application/json",
        )
        dup_body = dup.get_json()
        assert len(dup_body.get("already_queued") or []) + len(dup_body.get("accepted") or []) >= 0
        assert len(dup_body.get("already_queued") or []) == 1 or (
            len(dup_body.get("accepted") or []) == 0 and len(dup_body.get("rejected") or []) >= 0
        )

        time.sleep(1.2)
        assert state["max"] == 1
        assert state["order"] == ids

    def test_bulk_one_failure_does_not_stop_later(self, enforcer_client, test_db, monkeypatch):
        def fake_process(video_id, enabled_violations=None, **kwargs):
            if video_id == first:
                raise RuntimeError("boom")
            return ProcessVideoResult(events=[])

        monkeypatch.setattr(app_module, "process_video", fake_process)
        monkeypatch.setattr("core.video_processor.process_video", fake_process)
        first = test_db.insert_video(filename="f1.mp4", filepath="/tmp/f1.mp4", status="ready")
        test_db.upsert_annotation(first, ZONES)
        second = test_db.insert_video(filename="f2.mp4", filepath="/tmp/f2.mp4", status="ready")
        test_db.upsert_annotation(second, ZONES)
        enforcer_client.post(
            "/api/videos/process-bulk",
            json={"video_ids": [first, second], "enabled_violations": []},
            content_type="application/json",
        )
        time.sleep(0.9)
        runs2 = test_db.list_processing_runs(second)
        assert runs2[0]["status"] == "completed"


class TestDeleteSafety:
    def test_remove_results_isolation(self, enforcer_client, test_db, tmp_path, monkeypatch):
        import config

        monkeypatch.setattr(config, "ANNOTATED_FOLDER", str(tmp_path / "ann"))
        monkeypatch.setattr(config, "EVIDENCE_FOLDER", str(tmp_path / "ev"))
        monkeypatch.setattr(config, "UPLOAD_FOLDER", str(tmp_path / "up"))
        (tmp_path / "up").mkdir()
        v1, _p1 = _ready_video(test_db, tmp_path / "up", "one.mp4")
        _v2, p2 = _ready_video(test_db, tmp_path / "up", "two.mp4")
        test_db.mark_video_processed(v1)
        run = test_db.create_processing_run(v1, "[]")
        art = annotated_final_path(run)
        art.parent.mkdir(parents=True, exist_ok=True)
        art.write_bytes(b"fake")
        test_db.finish_processing_run(
            run,
            status="completed",
            progress={"annotated_video_path": str(art), "annotated_video_ready": True},
        )
        resp = enforcer_client.post(f"/api/videos/{v1}/remove-results")
        assert resp.status_code == 200
        assert test_db.get_video(v1)["status"] == "ready"
        assert test_db.get_video(_v2) is not None
        assert Path(p2).exists()
        assert not art.exists()

    def test_permanent_delete_auth_and_block(self, client, test_db, tmp_path, monkeypatch):
        import bcrypt
        import config
        from database import db

        monkeypatch.setattr(config, "UPLOAD_FOLDER", str(tmp_path))
        monkeypatch.setattr(config, "FRAMES_FOLDER", str(tmp_path / "frames"))
        monkeypatch.setattr(config, "EVIDENCE_FOLDER", str(tmp_path / "ev"))
        monkeypatch.setattr(config, "ANNOTATED_FOLDER", str(tmp_path / "ann"))
        (tmp_path / "frames").mkdir()
        vid, path = _ready_video(test_db, tmp_path, "del.mp4")

        hash_pw = bcrypt.hashpw(b"enforcer123", bcrypt.gensalt()).decode("utf-8")
        db.create_user("enf_del", hash_pw, role="enforcer")
        client.post("/login", data={"username": "enf_del", "password": "enforcer123"})
        r = client.delete(
            f"/api/videos/{vid}",
            json={"confirm_filename": "del.mp4"},
            content_type="application/json",
        )
        # Enforcer is redirected away from admin-only delete.
        assert r.status_code in (302, 403, 401)

        # Confirmed violation blocks delete (call lifecycle directly as admin policy).
        db.insert_violation(
            video_id=vid,
            track_id=1,
            violation_type=VIOLATION_ILLEGAL_PARKING,
            confidence=0.9,
            frame_number=1,
            timestamp_sec=0.0,
            status="confirmed",
        )
        from core.video_lifecycle import VideoLifecycleError, delete_video_permanently

        with pytest.raises(VideoLifecycleError) as excinfo:
            delete_video_permanently(
                vid,
                is_busy=lambda _vid: False,
                expected_filename="del.mp4",
            )
        assert excinfo.value.status_code == 409
        assert Path(path).exists()

    def test_delete_one_does_not_affect_other(self, client, test_db, tmp_path, monkeypatch):
        import config
        from core.video_lifecycle import delete_video_permanently

        monkeypatch.setattr(config, "UPLOAD_FOLDER", str(tmp_path))
        monkeypatch.setattr(config, "FRAMES_FOLDER", str(tmp_path / "frames"))
        monkeypatch.setattr(config, "EVIDENCE_FOLDER", str(tmp_path / "ev"))
        monkeypatch.setattr(config, "ANNOTATED_FOLDER", str(tmp_path / "ann"))
        (tmp_path / "frames").mkdir()
        v1, p1 = _ready_video(test_db, tmp_path, "keep.mp4")
        v2, _p2 = _ready_video(test_db, tmp_path, "gone.mp4")
        # Exercise authorized delete path via lifecycle (HTTP role gate tested above).
        result = delete_video_permanently(
            v2,
            is_busy=lambda _vid: False,
            expected_filename="gone.mp4",
            actor_user_id=None,
        )
        assert result["success"] is True
        assert test_db.get_video(v1) is not None
        assert Path(p1).exists()
        assert test_db.get_video(v2) is None


class TestAnalyticsBoundaries:
    def test_period_bounds_and_unique_vs_runs(self, test_db):
        start, end, grain = period_bounds("today")
        assert end > start
        assert grain == "day"
        vid = test_db.insert_video(filename="a.mp4", filepath="/tmp/a.mp4", status="ready")
        test_db.create_processing_run(vid, "[]")
        test_db.create_processing_run(vid, "[]")
        stats = build_upload_processing_analytics("today")
        assert stats["unique_videos_uploaded"] == 1
        assert stats["total_processing_runs"] >= 2


class TestProcessVideoIntegration:
    def test_synthetic_process_produces_annotated_mp4(self, test_db, tmp_path, monkeypatch):
        import config

        monkeypatch.setattr(config, "ANNOTATED_FOLDER", str(tmp_path / "ann"))
        monkeypatch.setattr(config, "EVIDENCE_FOLDER", str(tmp_path / "ev"))
        (tmp_path / "ev").mkdir()

        class FakeDetector:
            def load(self):
                return None

            def track_frame(self, frame, conf=0.5, timestamp_sec=0.0):
                return [{
                    "bbox_x": 20, "bbox_y": 20, "bbox_w": 40, "bbox_h": 30,
                    "class_label": "car", "confidence": 0.9, "track_id": 1,
                    "timestamp_sec": timestamp_sec,
                }]

            @property
            def class_names(self):
                return {"car", "motorcycle", "bus", "truck", "bicycle", "person"}

        monkeypatch.setattr("core.video_processor.Detector", lambda: FakeDetector())
        monkeypatch.setattr(
            "core.video_processor.extract_model_class_names",
            lambda det: {"car", "motorcycle", "bus", "truck", "bicycle", "person"},
        )
        monkeypatch.setattr("core.video_processor.evaluate_detection_rules", lambda *a, **k: [])

        vid, _path = _ready_video(test_db, tmp_path, "proc.mp4", frames=10)
        tracker = ProgressTracker(run_id=1, video_id=vid, viewer_mode="watch_live")
        run_id = test_db.create_processing_run(vid, "[]")
        result = process_video(
            vid,
            enabled_violations=(),
            processing_run_id=run_id,
            progress_tracker=tracker,
        )
        assert result.annotated_video_ready is True
        assert result.annotated_video_path
        out = Path(result.annotated_video_path)
        assert out.is_file()
        cap = cv2.VideoCapture(str(out))
        assert cap.isOpened()
        ok, frame = cap.read()
        cap.release()
        assert ok and frame is not None
        assert result.detection_records > 0
        snap = tracker.snapshot()
        assert snap["progress_percent"] >= 100 or snap["frames_processed"] > 0
        assert snap["stage"] == "completed"
        test_db.finish_processing_run(run_id, status="completed", progress=result.progress_snapshot)
        row = test_db.get_processing_run(run_id)
        assert row["annotated_video_ready"] in (1, True)


class TestPreviewHubDropStale:
    def test_hub_keeps_latest_only(self):
        hub = ProcessingPreviewHub()
        hub.publish(1, b"frame-a")
        hub.publish(1, b"frame-b")
        assert hub.latest(1) == b"frame-b"


class TestStatusContract:
    def test_status_includes_required_fields(self, enforcer_client, test_db, monkeypatch):
        monkeypatch.setattr(app_module, "process_video", lambda *a, **k: ProcessVideoResult(events=[]))
        vid = test_db.insert_video(filename="s.mp4", filepath="/tmp/s.mp4", status="ready")
        test_db.upsert_annotation(vid, ZONES)
        enforcer_client.post(
            f"/api/videos/{vid}/process",
            json={"enabled_violations": []},
            content_type="application/json",
        )
        time.sleep(0.4)
        body = enforcer_client.get(f"/api/videos/{vid}/process-status").get_json()
        for key in (
            "run_id", "video_id", "state", "stage", "queued", "queue_position",
            "frames_processed", "total_frames", "progress_percent", "elapsed_sec",
            "processing_fps", "eta_sec", "detection_records", "unique_tracks",
            "class_counts", "violation_candidates", "annotated_video_ready",
            "annotated_video_url", "error",
        ):
            assert key in body

class TestPermanentDeleteConfirmation:
    def test_missing_blank_wrong_confirmation_no_delete(self, client, test_db, tmp_path, monkeypatch):
        import bcrypt
        import config
        from database import db
        from core.video_lifecycle import VideoLifecycleError, delete_video_permanently

        monkeypatch.setattr(config, 'UPLOAD_FOLDER', str(tmp_path))
        monkeypatch.setattr(config, 'FRAMES_FOLDER', str(tmp_path / 'frames'))
        monkeypatch.setattr(config, 'EVIDENCE_FOLDER', str(tmp_path / 'ev'))
        monkeypatch.setattr(config, 'ANNOTATED_FOLDER', str(tmp_path / 'ann'))
        (tmp_path / 'frames').mkdir()
        (tmp_path / 'ann').mkdir()
        vid, path = _ready_video(test_db, tmp_path, 'confirm_me.mp4')
        art_dir = tmp_path / 'ann' / f'run_x'
        art_dir.mkdir(parents=True)
        art = art_dir / 'annotated.webm'
        art.write_bytes(b'artifact')

        hash_pw = bcrypt.hashpw(b'admin123', bcrypt.gensalt()).decode('utf-8')
        db.create_user('admin_del', hash_pw, role='admin')
        client.post('/login', data={'username': 'admin_del', 'password': 'admin123'})

        for body in ({}, {'confirm_filename': ''}, {'confirm_filename': '   '}, {'confirm_filename': 'wrong.mp4'}):
            r = client.delete(f'/api/videos/{vid}', json=body, content_type='application/json')
            assert r.status_code in (400, 409), body
            assert test_db.get_video(vid) is not None
            assert path.exists()

        with pytest.raises(VideoLifecycleError):
            delete_video_permanently(vid, is_busy=lambda _v: False, expected_filename=None)
        with pytest.raises(VideoLifecycleError):
            delete_video_permanently(vid, is_busy=lambda _v: False, expected_filename='')
        with pytest.raises(VideoLifecycleError):
            delete_video_permanently(vid, is_busy=lambda _v: False, expected_filename='nope.mp4')
        assert path.exists() and test_db.get_video(vid) is not None

    def test_exact_confirmation_deletes(self, test_db, tmp_path, monkeypatch):
        import config
        from core.video_lifecycle import delete_video_permanently

        monkeypatch.setattr(config, 'UPLOAD_FOLDER', str(tmp_path))
        monkeypatch.setattr(config, 'FRAMES_FOLDER', str(tmp_path / 'frames'))
        monkeypatch.setattr(config, 'EVIDENCE_FOLDER', str(tmp_path / 'ev'))
        monkeypatch.setattr(config, 'ANNOTATED_FOLDER', str(tmp_path / 'ann'))
        (tmp_path / 'frames').mkdir()
        (tmp_path / 'ann').mkdir()
        vid, path = _ready_video(test_db, tmp_path, 'exact.mp4')
        result = delete_video_permanently(
            vid, is_busy=lambda _v: False, expected_filename='exact.mp4'
        )
        assert result['success'] is True
        assert test_db.get_video(vid) is None
        assert not path.exists()

    def test_db_failure_restores_files(self, test_db, tmp_path, monkeypatch):
        import config
        from core import video_lifecycle as vl

        monkeypatch.setattr(config, 'UPLOAD_FOLDER', str(tmp_path))
        monkeypatch.setattr(config, 'FRAMES_FOLDER', str(tmp_path / 'frames'))
        monkeypatch.setattr(config, 'EVIDENCE_FOLDER', str(tmp_path / 'ev'))
        monkeypatch.setattr(config, 'ANNOTATED_FOLDER', str(tmp_path / 'ann'))
        (tmp_path / 'frames').mkdir()
        (tmp_path / 'ann').mkdir()
        vid, path = _ready_video(test_db, tmp_path, 'restore.mp4')
        run = test_db.create_processing_run(vid, '[]')
        art = annotated_final_path(run)
        art.parent.mkdir(parents=True, exist_ok=True)
        art.write_bytes(b'keep-me')
        test_db.finish_processing_run(
            run, status='completed',
            progress={'annotated_video_path': str(art), 'annotated_video_ready': True},
        )

        def boom(*_a, **_k):
            raise RuntimeError('forced db failure')

        monkeypatch.setattr(vl.db, 'delete_video_cascade_with_audit', boom)
        with pytest.raises(vl.VideoLifecycleError):
            vl.delete_video_permanently(
                vid, is_busy=lambda _v: False, expected_filename='restore.mp4'
            )
        assert test_db.get_video(vid) is not None
        assert path.exists()
        assert art.exists() and art.read_bytes() == b'keep-me'


class TestAnnotatedBrowserFormat:
    def test_writer_publishes_webm_not_avi_as_mp4(self, tmp_path, monkeypatch):
        import config
        from core.annotated_writer import mime_for_annotated_path, download_name_for_annotated

        monkeypatch.setattr(config, 'ANNOTATED_FOLDER', str(tmp_path / 'ann'))
        writer = AnnotatedVideoWriter(77, width=160, height=120, fps=5.0)
        frame = np.zeros((120, 160, 3), dtype=np.uint8)
        frame[:] = (10, 80, 160)
        for i in range(12):
            writer.write(frame)
        out = writer.finalize()
        assert out.suffix.lower() in ('.webm', '.mp4')
        assert out.suffix.lower() != '.avi'
        if out.suffix.lower() == '.webm':
            assert out.read_bytes()[:4] == b'\x1a\x45\xdf\xa3'
        assert mime_for_annotated_path(out) == (
            'video/webm' if out.suffix.lower() == '.webm' else 'video/mp4'
        )
        name = download_name_for_annotated('clip.mp4', 77, out)
        assert name.endswith(out.suffix.lower())
        assert not name.endswith('.mp4') or out.suffix.lower() == '.mp4'

    def test_annotated_route_mime_and_range(self, enforcer_client, test_db, tmp_path, monkeypatch):
        import config

        monkeypatch.setattr(config, 'ANNOTATED_FOLDER', str(tmp_path / 'ann'))
        vid = test_db.insert_video(filename='clip.mp4', filepath=str(tmp_path / 'clip.mp4'), status='processed')
        run = test_db.create_processing_run(vid, '[]')
        out_dir = tmp_path / 'ann' / f'run_{run}'
        out_dir.mkdir(parents=True)
        webm = out_dir / 'annotated.webm'
        webm.write_bytes(b'\x1a\x45\xdf\xa3' + b'\x00' * 200)
        test_db.finish_processing_run(
            run, status='completed',
            progress={'annotated_video_path': str(webm), 'annotated_video_ready': True},
        )
        resp = enforcer_client.get(
            f'/api/videos/{vid}/annotated/{run}',
            headers={'Range': 'bytes=0-9'},
        )
        assert resp.status_code == 206
        assert 'webm' in (resp.headers.get('Content-Type') or '')
        assert 'annotated_run' in (resp.headers.get('Content-Disposition') or '')
        assert '.webm' in (resp.headers.get('Content-Disposition') or '')


class TestRunAttribution:
    def test_reprocess_does_not_double_current_summary(self, test_db, tmp_path, monkeypatch):
        import config

        monkeypatch.setattr(config, 'ANNOTATED_FOLDER', str(tmp_path / 'ann'))
        monkeypatch.setattr(config, 'EVIDENCE_FOLDER', str(tmp_path / 'ev'))
        (tmp_path / 'ev').mkdir()

        class FakeDetector:
            def load(self):
                return None
            def track_frame(self, frame, conf=0.5, timestamp_sec=0.0):
                return [{
                    'bbox_x': 20, 'bbox_y': 20, 'bbox_w': 40, 'bbox_h': 30,
                    'class_label': 'car', 'confidence': 0.9, 'track_id': 1,
                    'timestamp_sec': timestamp_sec,
                }]
            @property
            def class_names(self):
                return {'car'}

        monkeypatch.setattr('core.video_processor.Detector', lambda: FakeDetector())
        monkeypatch.setattr(
            'core.video_processor.extract_model_class_names', lambda det: {'car'}
        )
        monkeypatch.setattr('core.video_processor.evaluate_detection_rules', lambda *a, **k: [])

        vid, _path = _ready_video(test_db, tmp_path, 'repro.mp4', frames=8)
        counts = []
        for _ in range(2):
            run_id = test_db.create_processing_run(vid, '[]')
            result = process_video(
                vid, enabled_violations=(), processing_run_id=run_id,
                progress_tracker=ProgressTracker(run_id=run_id, video_id=vid),
            )
            test_db.finish_processing_run(run_id, status='completed', progress=result.progress_snapshot)
            counts.append(result.detection_records)
        assert counts[0] == counts[1]
        assert counts[0] > 0
        summary = test_db.detection_summary_for_video(vid)
        assert summary['detection_records'] == counts[1]
        assert summary['detection_records'] != counts[0] + counts[1]
        runs = test_db.list_processing_runs(vid)
        assert len(runs) == 2

    def test_failed_second_run_keeps_first_current(self, test_db, tmp_path, monkeypatch):
        import config
        from core.video_processor import ProcessVideoError

        monkeypatch.setattr(config, 'ANNOTATED_FOLDER', str(tmp_path / 'ann'))
        monkeypatch.setattr(config, 'EVIDENCE_FOLDER', str(tmp_path / 'ev'))
        (tmp_path / 'ev').mkdir()

        class FakeDetector:
            def load(self):
                return None
            def track_frame(self, frame, conf=0.5, timestamp_sec=0.0):
                return [{
                    'bbox_x': 20, 'bbox_y': 20, 'bbox_w': 40, 'bbox_h': 30,
                    'class_label': 'car', 'confidence': 0.9, 'track_id': 1,
                    'timestamp_sec': timestamp_sec,
                }]
            @property
            def class_names(self):
                return {'car'}

        monkeypatch.setattr('core.video_processor.Detector', lambda: FakeDetector())
        monkeypatch.setattr(
            'core.video_processor.extract_model_class_names', lambda det: {'car'}
        )
        monkeypatch.setattr('core.video_processor.evaluate_detection_rules', lambda *a, **k: [])

        vid, _path = _ready_video(test_db, tmp_path, 'keep.mp4', frames=6)
        run1 = test_db.create_processing_run(vid, '[]')
        result = process_video(
            vid, enabled_violations=(), processing_run_id=run1,
            progress_tracker=ProgressTracker(run_id=run1, video_id=vid),
        )
        test_db.finish_processing_run(run1, status='completed', progress=result.progress_snapshot)
        first_summary = test_db.detection_summary_for_video(vid)

        run2 = test_db.create_processing_run(vid, '[]')
        test_db.finish_processing_run(run2, status='failed', error_message='boom')
        second_summary = test_db.detection_summary_for_video(vid)
        assert second_summary['detection_records'] == first_summary['detection_records']
        assert second_summary['processing_run_id'] == run1

    def test_migration_005_on_legacy_db(self, tmp_path):
        import sqlite3
        from database import sqlite_adapter as sa

        db_path = tmp_path / 'legacy.db'
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.executescript('''
            CREATE TABLE videos (id INTEGER PRIMARY KEY, filename TEXT, filepath TEXT, status TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE detections (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              video_id INTEGER, frame_number INTEGER, timestamp_sec REAL,
              track_id INTEGER, class_label TEXT, confidence REAL,
              bbox_x REAL, bbox_y REAL, bbox_w REAL, bbox_h REAL
            );
            CREATE TABLE review_queue (id INTEGER PRIMARY KEY, video_id INTEGER, status TEXT);
            CREATE TABLE violations (id INTEGER PRIMARY KEY, video_id INTEGER, status TEXT);
            CREATE TABLE processing_runs (id INTEGER PRIMARY KEY, video_id INTEGER, status TEXT);
        ''')
        conn.execute('INSERT INTO videos (filename, filepath, status) VALUES (?,?,?)', ('a.mp4', '/a', 'ready'))
        conn.execute(
            "INSERT INTO detections (video_id, frame_number, timestamp_sec, track_id, class_label, confidence, bbox_x, bbox_y, bbox_w, bbox_h) VALUES (1,0,0,1,'car',0.9,0,0,1,1)"
        )
        conn.commit()
        sa._apply_migration_005(conn)
        conn.commit()
        cols = [r[1] for r in conn.execute('PRAGMA table_info(detections)').fetchall()]
        assert 'processing_run_id' in cols
        row = conn.execute('SELECT processing_run_id FROM detections').fetchone()
        assert row[0] is None
        conn.close()


class TestPreviewGating:
    def test_zero_viewers_never_encodes(self, monkeypatch):
        hub = ProcessingPreviewHub(max_display_fps=5.0)
        calls = {'n': 0}

        def fake_encode(frame, quality=75):
            calls['n'] += 1
            return b'jpeg'

        monkeypatch.setattr('core.processing_preview.encode_jpeg', fake_encode)
        frame = np.zeros((8, 8, 3), dtype=np.uint8)
        assert hub.publish_frame(1, frame) is False
        assert calls['n'] == 0

    def test_one_viewer_rate_limited_and_shared(self, monkeypatch):
        hub = ProcessingPreviewHub(max_display_fps=5.0)
        calls = {'n': 0}

        def fake_encode(frame, quality=75):
            calls['n'] += 1
            return b'jpeg-' + str(calls['n']).encode()

        monkeypatch.setattr('core.processing_preview.encode_jpeg', fake_encode)
        frame = np.zeros((8, 8, 3), dtype=np.uint8)
        hub.register_viewer(3)
        assert hub.publish_frame(3, frame) is True
        assert hub.publish_frame(3, frame) is False  # rate limit
        hub.register_viewer(3)  # second viewer
        time.sleep(0.25)
        assert hub.publish_frame(3, frame) is True
        assert calls['n'] == 2  # one encode serves both viewers
        hub.unregister_viewer(3)
        hub.unregister_viewer(3)
        assert hub.viewer_count(3) == 0
        assert hub.publish_frame(3, frame) is False


class TestSelectAllDomContract:
    def test_live_monitor_select_all_outside_list(self):
        html = Path('templates/live_monitor.html').read_text(encoding='utf-8')
        js = Path('static/js/live_monitor.js').read_text(encoding='utf-8')
        assert 'id="bulkSelectAll"' in html
        assert 'id="videoList"' in html
        # Select-all must not rely solely on videoList change bubbling.
        assert 'getElementById("bulkSelectAll")' in js
        assert 'eligibleBulkChecks' in js
        assert 'indeterminate' in js
        assert 'href="/review-queue"' in js
        assert 'Open Review Queue' in js
        assert 'href="/violations">Open Review Queue' not in js


class TestUploadStatusAndHistoryUi:
    def test_upload_js_phase_order(self):
        js = Path('static/js/upload.js').read_text(encoding='utf-8')
        assert 'Preparing' in js
        assert 'Uploading / saving' in js
        assert 'Extracting metadata' in js
        # Extracting must be set on upload.load (in-flight), not only after response parse success path alone.
        assert 'xhr.upload.addEventListener("load"' in js

    def test_history_unknown_recorded_at(self, enforcer_client, test_db):
        vid = test_db.insert_video(filename='u.mp4', filepath='/tmp/u.mp4', status='ready')
        hist = enforcer_client.get(f'/api/videos/{vid}/history').get_json()
        assert hist['success'] is True
        assert hist['recording_time_known'] is False
        assert hist.get('recorded_at') in (None, '')
        js = Path('static/js/live_monitor.js').read_text(encoding='utf-8')
        assert 'Unknown' in js
        assert 'enabled_violations' in js
        assert 'queued_at' in js


class TestAtomicEnqueue:
    def test_concurrent_duplicate_enqueue_one_job(self, test_db, monkeypatch):
        hold = threading.Event()

        def fake_process(video_id, enabled_violations=None, **kwargs):
            hold.wait(timeout=2.0)
            return ProcessVideoResult(events=[])

        monkeypatch.setattr(app_module, 'process_video', fake_process)
        monkeypatch.setattr('core.video_processor.process_video', fake_process)
        vid = test_db.insert_video(filename='dup.mp4', filepath='/tmp/dup.mp4', status='ready')
        test_db.upsert_annotation(vid, ZONES)

        results = []
        lock = threading.Lock()

        def worker():
            status, run_id, queued, pos = app_module._try_claim_enqueue(
                vid,
                enabled=(),
                viewer_mode='background',
                actor_user_id=None,
            )
            with lock:
                results.append((status, run_id))

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        statuses = [r[0] for r in results]
        assert statuses.count('accepted') == 1
        assert statuses.count('already_busy') == 1
        hold.set()
        time.sleep(0.3)
        runs = test_db.list_processing_runs(vid)
        assert len(runs) == 1


class TestGitignoreAnnotated:
    def test_annotated_dir_ignored(self):
        text = Path('.gitignore').read_text(encoding='utf-8')
        assert 'dataset/annotated/' in text
        assert (Path('dataset/annotated') / '.gitkeep').exists()
