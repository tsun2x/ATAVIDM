"""Shared GPU reservation wiring in the Flask app and the live-stream worker.

Catches a second YOLOv8 pass overlapping an uploaded-video job, a motorcycle
crop batch, or a live camera frame, and catches a background worker thread being
started twice (Flask development reloader) or never started after a restart.
``process_video`` and the camera capture are mocked: no model is loaded here.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import numpy as np
import pytest

import app as app_module
import config
from core.gpu_inference_slot import (
    PRIORITY_DETAIL_SCAN,
    PRIORITY_LIVE_FRAME,
    PRIORITY_VIDEO_JOB,
    gpu_inference_slot,
)

ZONES = json.dumps({"z": [[0, 0], [1, 0], [1, 1], [0, 1]]})


def _wait_until(predicate, timeout: float = 10.0) -> bool:
    """Poll until ``predicate`` is true; no fixed sleeps in a test body."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


@pytest.fixture(autouse=True)
def _reset_workers(test_db_path):
    """Stop background threads and free the reservation between tests.

    Depends on ``test_db_path`` so teardown happens before the temporary SQLite
    directory is deleted: a live worker would keep a Windows file lock on it.
    """
    def _cleanup():
        app_module.stop_processing_worker()
        app_module.stop_detail_scanner()
        app_module._process_queue.clear()
        app_module._processing_jobs.clear()
        app_module._running_video_id = None

    _cleanup()
    yield
    _cleanup()


class _StubScanner:
    def __init__(self) -> None:
        self.start_calls = 0
        self.recovered: list[bool] = []
        self.stopped = 0

    def is_alive(self):
        return self.start_calls > 0

    def start(self):
        self.start_calls += 1

    def stop(self, timeout=None):
        self.stopped += 1

    def recover_abandoned(self, include_recent=False):
        self.recovered.append(bool(include_recent))
        return 0


class TestVideoJobHoldsTheReservation:
    def test_job_runs_inside_the_reservation_and_hands_it_back(
        self, test_db, enforcer_client, monkeypatch
    ):
        slot = gpu_inference_slot()
        observed: list[str | None] = []
        inside = threading.Event()
        release = threading.Event()

        def fake_process(video_id, enabled_violations=None, **kwargs):
            observed.append(slot.holder())
            inside.set()
            assert release.wait(10.0)
            return None

        monkeypatch.setattr(app_module, "process_video", fake_process)
        vid = test_db.insert_video(filename="a.mp4", filepath="/tmp/a.mp4", status="ready")
        test_db.upsert_annotation(vid, ZONES)
        response = enforcer_client.post(
            f"/api/videos/{vid}/process",
            json={"enabled_violations": []},
            content_type="application/json",
        )
        assert response.status_code == 200, response.get_json()
        try:
            assert inside.wait(10.0)
            # Nothing else may start a pass while the job holds the device.
            assert observed[0].startswith(f"video-job:{vid}:")
            assert slot.acquire("detail-scan", timeout=0.0, priority=PRIORITY_DETAIL_SCAN) is None
            assert slot.acquire("live-frame", timeout=0.0, priority=PRIORITY_LIVE_FRAME) is None
        finally:
            release.set()
        assert _wait_until(slot.is_free)
        assert slot.waiter_count() == 0

    def test_failed_job_still_releases_the_reservation(
        self, test_db, enforcer_client, monkeypatch
    ):
        slot = gpu_inference_slot()

        def fake_process(video_id, enabled_violations=None, **kwargs):
            raise RuntimeError("model exploded")

        monkeypatch.setattr(app_module, "process_video", fake_process)
        vid = test_db.insert_video(filename="b.mp4", filepath="/tmp/b.mp4", status="ready")
        test_db.upsert_annotation(vid, ZONES)
        enforcer_client.post(
            f"/api/videos/{vid}/process",
            json={"enabled_violations": []},
            content_type="application/json",
        )
        assert _wait_until(slot.is_free)
        # A failed job must not poison the device for the next one.
        next_holder = slot.acquire("detail-scan", timeout=0.0, priority=PRIORITY_DETAIL_SCAN)
        slot.release(next_holder)
        assert next_holder is not None

    def test_job_is_requeued_when_the_reservation_never_arrives(self, monkeypatch):
        """A bounded wait re-queues the job instead of inferring unreserved."""
        monkeypatch.setattr(app_module, "_VIDEO_JOB_GPU_WAIT_SEC", 0.05)
        monkeypatch.setattr(
            app_module,
            "_call_process_video",
            lambda *a, **k: pytest.fail("inference must not start unreserved"),
        )
        blocker = gpu_inference_slot().acquire(
            "video-job:blocker", timeout=0.0, priority=PRIORITY_VIDEO_JOB
        )
        assert blocker is not None
        with app_module._queue_cv:
            app_module._process_queue.append((42, (), 4242))
        thread = threading.Thread(target=app_module._processing_worker, daemon=True)
        thread.start()
        try:
            # The popped job is put back at the head of the queue.
            assert _wait_until(
                lambda: [e[0] for e in app_module._process_queue] == [42]
            )
            assert app_module._process_queue[0][2] == 4242  # the same run, not a new one
        finally:
            gpu_inference_slot().release(blocker)
            app_module._worker_shutdown.set()
            with app_module._queue_cv:
                app_module._queue_cv.notify_all()
            thread.join(10.0)
        assert not thread.is_alive()
        app_module._worker_shutdown.clear()


class TestDetailScannerLifecycle:
    def test_bootstrap_retries_recovery_failure_before_starting(self, test_db, monkeypatch):
        class _RecoveringScanner(_StubScanner):
            def recover_abandoned(self, include_recent=False):
                self.recovered.append(bool(include_recent))
                return None if len(self.recovered) == 1 else 0

        scanner = _RecoveringScanner()
        monkeypatch.setattr(app_module, "MotorcycleDetailScanner", lambda **kwargs: scanner)
        monkeypatch.setattr(app_module, "_detail_workers_allowed", True)
        app_module._detail_scanner = None
        app_module._detail_bootstrap_done = False
        try:
            assert app_module.bootstrap_detail_queue() is None
            assert scanner.start_calls == 0
            assert app_module.bootstrap_detail_queue() is scanner
            assert scanner.recovered == [True, True]
            assert scanner.start_calls == 1
        finally:
            app_module._detail_scanner = None
            app_module._detail_bootstrap_done = False

    def test_bootstrap_starts_exactly_one_scanner(self, test_db, monkeypatch):
        """Idempotent across repeated start-up calls and the reloader."""
        scanner = _StubScanner()
        monkeypatch.setattr(app_module, "MotorcycleDetailScanner", lambda **kwargs: scanner)
        monkeypatch.setattr(app_module, "_detail_workers_allowed", True)
        app_module._detail_scanner = None
        app_module._detail_bootstrap_done = False
        try:
            assert app_module.bootstrap_detail_queue() is scanner
            assert app_module.bootstrap_detail_queue() is scanner
            assert app_module.bootstrap_detail_queue() is scanner
        finally:
            app_module._detail_scanner = None
            app_module._detail_bootstrap_done = False
        assert scanner.start_calls == 1
        # Start-up recovery is requested exactly once, for abandoned rows.
        assert scanner.recovered == [True]

    def test_reloader_parent_starts_no_background_worker(self, monkeypatch):
        monkeypatch.setenv("FLASK_DEBUG", "1")
        monkeypatch.delenv("WERKZEUG_RUN_MAIN", raising=False)
        assert app_module._background_workers_allowed() is False
        monkeypatch.setenv("WERKZEUG_RUN_MAIN", "true")
        assert app_module._background_workers_allowed() is True

    def test_operator_opt_out_disables_background_workers(self, monkeypatch):
        monkeypatch.setenv("TAVIDM_DISABLE_BACKGROUND_WORKERS", "1")
        assert app_module._background_workers_allowed() is False

    def test_bootstrap_does_nothing_when_workers_are_disabled(self, test_db, monkeypatch):
        monkeypatch.setattr(app_module, "_detail_workers_allowed", False)
        app_module._detail_scanner = None
        app_module._detail_bootstrap_done = False
        try:
            assert app_module.bootstrap_detail_queue() is None
        finally:
            app_module._detail_bootstrap_done = False

    def test_startup_recovery_runs_before_the_thread_starts(self, test_db, monkeypatch):
        """Recovery must precede ``start()``.

        The loop's first ``run_once`` performs the stale-only recovery, which
        skips rows still inside the stale window. Starting the thread first would
        therefore let it consume the one-shot start-up pass and strand the rows
        a just-dead process abandoned.
        """
        order: list[str] = []

        class _OrderedScanner(_StubScanner):
            def recover_abandoned(self, include_recent=False):
                order.append("recover")
                return super().recover_abandoned(include_recent=include_recent)

            def start(self):
                order.append("start")
                return super().start()

        scanner = _OrderedScanner()
        monkeypatch.setattr(app_module, "MotorcycleDetailScanner", lambda **kwargs: scanner)
        monkeypatch.setattr(app_module, "_detail_workers_allowed", True)
        app_module._detail_scanner = None
        app_module._detail_bootstrap_done = False
        try:
            assert app_module.bootstrap_detail_queue() is scanner
        finally:
            app_module._detail_scanner = None
            app_module._detail_bootstrap_done = False
        assert order == ["recover", "start"]

    def test_enqueue_path_honours_the_operator_opt_out(self, test_db, monkeypatch):
        """Queueing a video must not bypass the background-worker opt-out."""
        started: list[bool] = []
        monkeypatch.setattr(app_module, "_detail_workers_allowed", False)
        monkeypatch.setattr(
            app_module, "_ensure_worker", lambda: started.append(True)
        )
        app_module._detail_scanner = None
        app_module._detail_bootstrap_done = False
        try:
            app_module._enqueue_processing(999, ("wrong_side_parking",), 1)
        finally:
            app_module._process_queue.clear()
            app_module._processing_jobs.clear()
            app_module._detail_scanner = None
            app_module._detail_bootstrap_done = False
        assert started == [True]
        assert app_module._detail_scanner is None

    def test_first_request_bootstraps_the_queue_once(self, test_db, client, monkeypatch):
        """A restart resumes the scanner on the first served request."""
        scanner = _StubScanner()
        monkeypatch.setattr(app_module, "MotorcycleDetailScanner", lambda **kwargs: scanner)
        monkeypatch.setattr(app_module, "_detail_workers_allowed", True)
        app_module._detail_scanner = None
        app_module._detail_bootstrap_done = False
        try:
            client.get("/login")
            client.get("/login")
        finally:
            app_module._detail_scanner = None
            app_module._detail_bootstrap_done = False
        assert scanner.start_calls == 1
        assert scanner.recovered == [True]

    def test_ensure_detail_scanner_does_not_duplicate_a_live_thread(self, test_db, monkeypatch):
        """start() is only called while the thread is not already alive."""
        scanner = _StubScanner()
        monkeypatch.setattr(app_module, "MotorcycleDetailScanner", lambda **kwargs: scanner)
        app_module._detail_scanner = None
        try:
            first = app_module._ensure_detail_scanner()
            second = app_module._ensure_detail_scanner()
        finally:
            app_module._detail_scanner = None
        assert first is second is scanner
        assert scanner.start_calls == 1


EXPECTED_15 = (
    "car", "van", "jeepney", "tricycle", "autorickshaw", "bus", "truck",
    "pickup_truck", "motorcycle", "bicycle", "person", "rider",
    "helmet_acceptable", "helmet_nut_shell", "side_mirror",
)


def _designated_checkpoint(monkeypatch):
    """Force the one-shot manual scan past the checkpoint gate.

    Only the gate is stubbed: the reservation, the queue peek, the claim, and
    the crop scan all run for real, so the endpoint's busy contract is what is
    under test.
    """
    from core.motorcycle_detail_scan import DetailCheckpoint, MotorcycleDetailScanner as _Real

    checkpoint = DetailCheckpoint(
        path="mock", class_names=EXPECTED_15, ok=True, identity="YOLOv8m:mock"
    )

    def factory(**kwargs):
        kwargs.setdefault("checkpoint_loader", lambda: checkpoint)
        kwargs.setdefault("predictor_factory", lambda cp: (lambda image: []))
        kwargs.setdefault("gpu_wait_sec", app_module.DETAIL_SCAN_MANUAL_GPU_WAIT_SEC)
        return _Real(**kwargs)

    monkeypatch.setattr(app_module, "MotorcycleDetailScanner", factory)


def _queue_one_candidate(test_db):
    """Queue a candidate whose crop file is missing (scan then fails)."""
    video_id = test_db.insert_video("busy.mp4", "/tmp/busy.mp4", status="processed")
    run_id = test_db.create_processing_run(video_id, "[]")
    return test_db.upsert_motorcycle_detail_candidate(
        video_id=video_id, run_key=run_id, track_id=7, occurrence_index=1,
        occurrence_key="t7g1", selector_version="md-1.0", processing_run_id=1,
        frame_number=3, timestamp_sec=1.0, frame_score=0.7,
        score_breakdown_json="{}", frame_count=1,
        frames_json=json.dumps(
            [
                {
                    "index": 1,
                    "frame_number": 10,
                    "crop_path": str(Path(config.EVIDENCE_FOLDER) / "missing.jpg"),
                    "crop": {"x": 0, "y": 0, "w": 30, "h": 30},
                    "scan_scale_x": 1.0,
                    "scan_scale_y": 1.0,
                    "detection_bbox": {"x": 5.0, "y": 5.0, "w": 20.0, "h": 20.0},
                }
            ]
        ),
        source_width=640, source_height=480,
    )


class TestManualScanBusyResponse:
    def test_busy_gpu_returns_409_and_leaves_the_candidate_queued(
        self, test_db, enforcer_client, monkeypatch
    ):
        _designated_checkpoint(monkeypatch)
        candidate_id = _queue_one_candidate(test_db)
        token = gpu_inference_slot().acquire(
            "video-job:held", timeout=0.0, priority=PRIORITY_VIDEO_JOB
        )
        assert token is not None
        try:
            response = enforcer_client.post("/api/motorcycle-detail-review/scan-now")
        finally:
            gpu_inference_slot().release(token)
        assert response.status_code == 409, response.get_json()
        body = response.get_json()
        assert body["success"] is False
        assert "GPU" in body["error"]
        assert body["report"]["skipped"] == "gpu_slot_busy"
        # A refused batch must not consume the candidate or spend an attempt.
        row = test_db.get_motorcycle_detail_candidate(candidate_id)
        assert row["scan_state"] == "queued"
        assert row["scan_attempts"] == 0

    def test_reservation_is_authoritative_when_the_queue_hint_is_wrong(
        self, test_db, enforcer_client, monkeypatch
    ):
        """A live frame holding the device yields 409, not a false success."""
        _designated_checkpoint(monkeypatch)
        _queue_one_candidate(test_db)
        monkeypatch.setattr(app_module, "_detail_gpu_idle", lambda: True)
        token = gpu_inference_slot().acquire(
            "live-frame:camera-1", timeout=0.0, priority=PRIORITY_LIVE_FRAME
        )
        assert token is not None
        try:
            response = enforcer_client.post("/api/motorcycle-detail-review/scan-now")
        finally:
            gpu_inference_slot().release(token)
        assert response.status_code == 409
        body = response.get_json()
        assert body["report"]["skipped"] == "gpu_slot_busy"

    def test_free_device_lets_the_one_shot_batch_run(
        self, test_db, enforcer_client, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(config, "EVIDENCE_FOLDER", str(tmp_path / "evidence"))
        _designated_checkpoint(monkeypatch)
        candidate_id = _queue_one_candidate(test_db)
        response = enforcer_client.post("/api/motorcycle-detail-review/scan-now")
        assert response.status_code == 200, response.get_json()
        body = response.get_json()
        assert body["success"] is True
        assert body["report"]["claimed"] == 1
        # The batch really ran (the crop is missing, so the attempt fails), the
        # failure is retried within the attempt budget, and the device is free.
        assert body["report"]["deferred"] == 1
        assert gpu_inference_slot().is_free()
        row = test_db.get_motorcycle_detail_candidate(candidate_id)
        assert row["scan_state"] == "queued"
        assert row["scan_attempts"] == 1
        assert "crop_unreadable" in (row["scan_error"] or "")


class _FakeCapture:
    """Camera stub that yields ``frames`` synthetic frames, then stops."""

    def __init__(self, worker, frames: int = 2) -> None:
        self._worker = worker
        self._remaining = frames
        self.released = False

    def isOpened(self):
        return True

    def read(self):
        if self._remaining <= 0:
            self._worker._stop_event.set()
            return False, None
        self._remaining -= 1
        return True, np.zeros((8, 8, 3), dtype=np.uint8)

    def release(self):
        self.released = True


class _TrackStateStub:
    def __init__(self, **kwargs):
        pass

    def update(self, raw, now):
        return raw

    def history_view(self, now):
        return []


def _stub_live_pipeline(monkeypatch, live, detector_cls, capture):
    monkeypatch.setattr(live, "Detector", detector_cls)
    monkeypatch.setattr(
        live, "load_rule_parameters",
        lambda: {"frame_skip": 1, "confidence_threshold": 0.3, "stationary_px": 5},
    )
    monkeypatch.setattr(live, "load_enabled_violations", lambda: ())
    monkeypatch.setattr(live, "enforce_object_class_contract", lambda detector: None)
    monkeypatch.setattr(live, "extract_model_class_names", lambda detector: {})
    monkeypatch.setattr(live, "TrackState", _TrackStateStub)
    monkeypatch.setattr(live, "evaluate_detection_rules", lambda *a, **k: [])
    monkeypatch.setattr(live, "save_evidence_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(live, "save_vehicle_crop", lambda *a, **k: None)
    monkeypatch.setattr(live.cv2, "VideoCapture", lambda url: capture)


class TestLiveStreamFrameReservation:
    def test_frame_is_dropped_instead_of_inferring_twice(self, monkeypatch):
        from core import live_stream as live

        worker = live.LiveStreamWorker({"id": 3, "name": "Gate", "rtsp_url": "rtsp://cam-3"})
        slot = gpu_inference_slot()
        token = slot.acquire("video-job:1", timeout=0.0, priority=PRIORITY_VIDEO_JOB)
        assert token is not None
        calls: list[int] = []

        class _Detector:
            def load(self):
                return None

            def track_frame(self, frame, **kwargs):
                calls.append(1)
                raise AssertionError("must not infer while the GPU is reserved")

        capture = _FakeCapture(worker, frames=2)
        _stub_live_pipeline(monkeypatch, live, _Detector, capture)
        worker._annotate = lambda frame, tracked: frame
        worker._publish = lambda frame: None
        try:
            worker.run()
        finally:
            slot.release(token)
        assert calls == []
        assert capture.released is True
        assert worker.status == "stopped"
        assert slot.is_free()
        assert slot.waiter_count() == 0

    def test_frame_is_scanned_inside_its_own_reservation(self, monkeypatch):
        from core import live_stream as live

        worker = live.LiveStreamWorker({"id": 4, "name": "Gate", "rtsp_url": "rtsp://cam-4"})
        slot = gpu_inference_slot()
        inside: list[str | None] = []

        class _Detector:
            def load(self):
                return None

            def track_frame(self, frame, **kwargs):
                inside.append(slot.holder())
                return []

        capture = _FakeCapture(worker, frames=1)
        _stub_live_pipeline(monkeypatch, live, _Detector, capture)
        worker._annotate = lambda frame, tracked: frame
        worker._publish = lambda frame: None
        worker.run()
        assert inside == ["live-frame:camera-4"]
        assert slot.is_free()
        assert slot.waiter_count() == 0
