"""Shared GPU reservation wiring in the Flask app and the live-stream worker.

Catches a second YOLOv8 pass overlapping an uploaded-video job, a motorcycle
crop batch, or a live camera frame, and catches a background worker thread being
started twice (Flask development reloader) or never started after a restart.
``process_video`` and the camera capture are mocked: no model is loaded here.
"""

from __future__ import annotations

import gc
import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

import numpy as np
import pytest

import app as app_module
import config
from core.gpu_inference_slot import (
    GpuInferenceSlot,
    PRIORITY_DETAIL_SCAN,
    PRIORITY_LIVE_FRAME,
    PRIORITY_VIDEO_JOB,
    gpu_inference_slot,
)
from core.processing_preview import preview_hub
from core.processing_progress import ProgressTracker

ZONES = json.dumps({"z": [[0, 0], [1, 0], [1, 1], [0, 1]]})


def _wait_until(predicate, timeout: float = 10.0) -> bool:
    """Poll until ``predicate`` is true; no fixed sleeps in a test body."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def test_gpu_slot_admits_two_inference_jobs_but_not_a_third():
    """Catches a hard-coded one-holder lock after the two-job benchmark passes."""
    slot = GpuInferenceSlot(name="test-gpu", capacity=2)
    first = slot.acquire("video-a", timeout=0.0, priority=PRIORITY_VIDEO_JOB)
    second = slot.acquire("video-b", timeout=0.0, priority=PRIORITY_VIDEO_JOB)
    third = slot.acquire("video-c", timeout=0.0, priority=PRIORITY_VIDEO_JOB)

    assert first is not None
    assert second is not None
    assert third is None
    assert slot.snapshot()["holders"] == ["video-a", "video-b"]

    slot.release(first)
    third = slot.acquire("video-c", timeout=0.0, priority=PRIORITY_VIDEO_JOB)
    assert third is not None
    slot.release(second)
    slot.release(third)
    assert slot.is_free()


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
        app_module._processing_cancel_events.clear()
        app_module._running_video_ids.clear()
        app_module._local_attempt_tokens.clear()
        app_module._running_video_id = None
        # Windows keeps a just-closed SQLite handle until the connection is
        # collected. Do that before the temporary database directory is removed.
        gc.collect()

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
        owner = test_db.get_user_by_username("enforcer_test")
        vid = test_db.insert_video(
            filename="a.mp4", filepath="/tmp/a.mp4", status="ready", uploaded_by=owner["id"]
        )
        test_db.upsert_annotation(vid, ZONES)
        response = enforcer_client.post(
            f"/api/videos/{vid}/process",
            json={"enabled_violations": []},
            content_type="application/json",
        )
        assert response.status_code == 200, response.get_json()
        try:
            assert inside.wait(10.0)
            # The second reservation is intentionally available at capacity two.
            assert observed[0].startswith(f"video-job:{vid}:")
            detail_token = slot.acquire("detail-scan", timeout=0.0, priority=PRIORITY_DETAIL_SCAN)
            assert detail_token is not None
            assert slot.acquire("live-frame", timeout=0.0, priority=PRIORITY_LIVE_FRAME) is None
            slot.release(detail_token)
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

    def test_job_is_requeued_when_the_reservation_never_arrives(self, test_db, monkeypatch):
        """A bounded wait releases its durable claim instead of inferring unreserved."""
        monkeypatch.setattr(app_module, "_VIDEO_JOB_GPU_WAIT_SEC", 0.05)
        monkeypatch.setattr(
            app_module,
            "_call_process_video",
            lambda *a, **k: pytest.fail("inference must not start unreserved"),
        )
        blockers = [gpu_inference_slot().acquire(
            f"video-job:blocker-{index}", timeout=0.0, priority=PRIORITY_VIDEO_JOB
        ) for index in (1, 2)]
        assert all(blocker is not None for blocker in blockers)
        owner = test_db.create_user("slot-owner", "test-hash", role="enforcer")
        video_id = test_db.insert_video(
            filename="slot.mp4", filepath="/tmp/slot.mp4", status="ready", uploaded_by=owner
        )
        run_id = test_db.enqueue_processing_run(video_id, "[]")
        with app_module._queue_cv:
            app_module._process_queue.append((video_id, (), run_id))
        app_module._worker_shutdown.clear()
        thread = threading.Thread(target=app_module._processing_worker, daemon=True)
        thread.start()
        try:
            # The run remains queued and can be claimed again.
            assert _wait_until(lambda: (
                (job := app_module._processing_jobs.get(video_id)) is not None
                and job.get("run_id") == run_id
                and job.get("tracker") is not None
                and job["tracker"].snapshot()["queued"] is True
                and test_db.get_processing_run(run_id)["status"] == "queued"
            ))
            assert app_module._processing_jobs[video_id]["run_id"] == run_id
            assert app_module._processing_jobs[video_id]["tracker"].snapshot()["queued"] is True
            assert run_id not in app_module._processing_cancel_events
        finally:
            for blocker in blockers:
                gpu_inference_slot().release(blocker)
            app_module._worker_shutdown.set()
            with app_module._queue_cv:
                app_module._queue_cv.notify_all()
            thread.join(10.0)
        assert not thread.is_alive()
        app_module._worker_shutdown.clear()

    def test_cancel_during_gpu_timeout_finishes_durable_and_cached_state(self, test_db, monkeypatch):
        monkeypatch.setattr(app_module, "_VIDEO_JOB_GPU_WAIT_SEC", 5.0)
        monkeypatch.setattr(
            app_module,
            "_call_process_video",
            lambda *a, **k: pytest.fail("cancelled job must not infer"),
        )
        blockers = [gpu_inference_slot().acquire(
            f"video-job:cancel-blocker-{index}", timeout=0.0, priority=PRIORITY_VIDEO_JOB
        ) for index in (1, 2)]
        assert all(blocker is not None for blocker in blockers)
        owner = test_db.create_user("cancel-slot-owner", "test-hash", role="enforcer")
        video_id = test_db.insert_video(
            filename="cancel-slot.mp4", filepath="/tmp/cancel-slot.mp4", status="ready", uploaded_by=owner
        )
        run_id = test_db.enqueue_processing_run(video_id, "[]")
        with app_module._queue_cv:
            app_module._process_queue.append((video_id, (), run_id))
        app_module._worker_shutdown.clear()
        thread = threading.Thread(target=app_module._processing_worker, daemon=True)
        thread.start()
        try:
            assert _wait_until(lambda: gpu_inference_slot().waiter_count() > 0, timeout=10.0)
            assert test_db.request_processing_cancel(run_id) == "stopping"
            app_module._processing_cancel_events.setdefault(run_id, threading.Event()).set()
            assert _wait_until(lambda: test_db.get_processing_run(run_id)["status"] == "cancelled")
            assert app_module._processing_jobs[video_id]["state"] == "cancelled"
            assert run_id not in app_module._processing_cancel_events
        finally:
            for blocker in blockers:
                gpu_inference_slot().release(blocker)
            app_module._worker_shutdown.set()
            with app_module._queue_cv:
                app_module._queue_cv.notify_all()
            thread.join(10.0)
        assert not thread.is_alive()
        app_module._worker_shutdown.clear()

    def test_release_failure_stops_attempt_and_other_job_can_run(self, test_db, monkeypatch):
        monkeypatch.setattr(app_module, "_VIDEO_JOB_GPU_WAIT_SEC", 0.05)
        blockers = [gpu_inference_slot().acquire(
            f"video-job:release-failure-blocker-{index}", timeout=0.0,
            priority=PRIORITY_VIDEO_JOB,
        ) for index in (1, 2)]
        assert all(blocker is not None for blocker in blockers)
        owner_a = test_db.create_user("release-owner-a", "test-hash", role="enforcer")
        owner_b = test_db.create_user("release-owner-b", "test-hash", role="enforcer")
        first = test_db.insert_video(
            filename="release-failure.mp4", filepath="/tmp/release-failure.mp4",
            status="ready", uploaded_by=owner_a,
        )
        second = test_db.insert_video(
            filename="after-release-failure.mp4", filepath="/tmp/after-release-failure.mp4",
            status="ready", uploaded_by=owner_b,
        )
        first_run = test_db.enqueue_processing_run(first, "[]")
        second_run = test_db.enqueue_processing_run(second, "[]")
        original_release = test_db.release_processing_claim
        failed_release = threading.Event()
        lease_threads = []
        real_thread = threading.Thread

        def track_lease_thread(*args, **kwargs):
            thread = real_thread(*args, **kwargs)
            if str(kwargs.get("name") or "").startswith("lease-"):
                lease_threads.append(thread)
            return thread

        def release(run_id, worker_id):
            if run_id == first_run:
                failed_release.set()
                raise OSError("synthetic release outage")
            return original_release(run_id, worker_id)

        monkeypatch.setattr(app_module.db, "release_processing_claim", release)
        monkeypatch.setattr(app_module, "_call_process_video", lambda *_a, **_k: app_module.ProcessVideoResult())
        monkeypatch.setattr(app_module.threading, "Thread", track_lease_thread)
        app_module._worker_shutdown.clear()
        worker = threading.Thread(target=app_module._processing_worker, daemon=True)
        worker.start()
        try:
            assert failed_release.wait(5)
            assert test_db.get_processing_run(first_run)["status"] == "running"
            assert _wait_until(lambda: bool(lease_threads) and not lease_threads[0].is_alive())
            for blocker in blockers:
                gpu_inference_slot().release(blocker)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and test_db.get_processing_run(second_run)["status"] != "completed":
                time.sleep(0.01)
            assert test_db.get_processing_run(second_run)["status"] == "completed"
            assert second_run not in app_module._processing_cancel_events
        finally:
            for blocker in blockers:
                gpu_inference_slot().release(blocker)
            app_module._worker_shutdown.set()
            with app_module._queue_cv:
                app_module._queue_cv.notify_all()
            worker.join(10.0)
            app_module._worker_shutdown.clear()
        assert not worker.is_alive()


def _expire_lease(db_path: str, run_id: int) -> None:
    """Backdate a live claim so the real expiry path can recover it immediately."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE processing_runs SET lease_expires_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00.000000+00:00", int(run_id)),
        )


def _release_gpu_blockers(blockers) -> None:
    slot = gpu_inference_slot()
    for blocker in blockers:
        slot.release(blocker)


class TestSameVideoRetryAfterWorkerCleanup:
    """Same-video retry must survive a failed GPU acquire and a rejected claim release."""

    def test_gpu_acquisition_exception_allows_retry_after_lease_recovery(
        self, test_db, test_db_path, enforcer_client, monkeypatch
    ):
        raised = {"done": False}
        slot = gpu_inference_slot()
        real_acquire = slot.acquire

        def acquire(owner, *, timeout, priority=PRIORITY_VIDEO_JOB):
            if str(owner).startswith("video-job:") and not raised["done"]:
                raised["done"] = True
                raise RuntimeError("synthetic GPU acquisition failure")
            return real_acquire(owner, timeout=timeout, priority=priority)

        monkeypatch.setattr(slot, "acquire", acquire)
        monkeypatch.setattr(
            app_module, "process_video", lambda *_a, **_k: app_module.ProcessVideoResult()
        )
        owner = test_db.get_user_by_username("enforcer_test")
        video_id = test_db.insert_video(
            filename="retry-after-gpu-exception.mp4",
            filepath="/tmp/retry-after-gpu-exception.mp4",
            status="ready",
            uploaded_by=owner["id"],
        )
        test_db.upsert_annotation(video_id, ZONES)
        started = enforcer_client.post(
            f"/api/videos/{video_id}/process",
            json={"enabled_violations": []},
            content_type="application/json",
        )
        assert started.status_code == 200, started.get_json()
        run_id = int(started.get_json()["run_id"])

        def attempt_cleared() -> bool:
            run = test_db.get_processing_run(run_id)
            return bool(
                raised["done"]
                and run
                and run["status"] == "running"
                and video_id not in app_module._running_video_ids
                and app_module._processing_jobs.get(video_id) is None
            )

        assert _wait_until(attempt_cleared)
        # The shared marker must follow the running set. SQLite, not a stale
        # marker, is what still blocks a duplicate while the lease is live.
        assert app_module._running_video_id != video_id
        assert test_db.processing_queue_position(video_id) == 0
        assert app_module._is_processing_or_queued(video_id) is True
        duplicate = enforcer_client.post(
            f"/api/videos/{video_id}/process",
            json={"enabled_violations": []},
            content_type="application/json",
        )
        assert duplicate.status_code == 409, duplicate.get_json()
        assert test_db.get_processing_run(run_id)["status"] == "running"

        _expire_lease(test_db_path, run_id)
        test_db.recover_orphaned_processing()
        assert _wait_until(
            lambda: (
                (recovered := test_db.get_processing_run(run_id)) is not None
                and recovered["status"] == "failed"
                and recovered["error_message"] == "Worker lease expired."
            )
        )
        assert test_db.processing_queue_position(video_id) is None
        assert app_module._processing_jobs.get(video_id) is None
        assert app_module._is_processing_or_queued(video_id) is False

        retried = enforcer_client.post(
            f"/api/videos/{video_id}/process",
            json={"enabled_violations": []},
            content_type="application/json",
        )
        assert retried.status_code == 200, retried.get_json()
        retry_run = int(retried.get_json()["run_id"])
        assert retry_run != run_id
        assert _wait_until(
            lambda: test_db.get_processing_run(retry_run)["status"] == "completed"
        )
        app_module.stop_processing_worker(timeout=5)

    def test_rejected_claim_release_cannot_block_resubmission_after_durable_clear(
        self, test_db, test_db_path, enforcer_client, monkeypatch
    ):
        monkeypatch.setattr(app_module, "_VIDEO_JOB_GPU_WAIT_SEC", 0.05)
        monkeypatch.setattr(
            app_module, "process_video", lambda *_a, **_k: app_module.ProcessVideoResult()
        )
        blockers = [
            gpu_inference_slot().acquire(
                f"video-job:rejected-release-blocker-{index}",
                timeout=0.0,
                priority=PRIORITY_VIDEO_JOB,
            )
            for index in (1, 2)
        ]
        assert all(blocker is not None for blocker in blockers)
        original_release = test_db.release_processing_claim
        released = {"count": 0}

        def release(run_id, worker_id):
            released["count"] += 1
            if released["count"] == 1:
                return False
            return original_release(run_id, worker_id)

        monkeypatch.setattr(app_module.db, "release_processing_claim", release)
        owner = test_db.get_user_by_username("enforcer_test")
        video_id = test_db.insert_video(
            filename="rejected-release.mp4",
            filepath="/tmp/rejected-release.mp4",
            status="ready",
            uploaded_by=owner["id"],
        )
        test_db.upsert_annotation(video_id, ZONES)
        try:
            started = enforcer_client.post(
                f"/api/videos/{video_id}/process",
                json={"enabled_violations": []},
                content_type="application/json",
            )
            assert started.status_code == 200, started.get_json()
            run_id = int(started.get_json()["run_id"])
            assert _wait_until(lambda: released["count"] >= 1)
            assert _wait_until(
                lambda: app_module._processing_jobs.get(video_id, {}).get("state") != "processing"
            )
            assert test_db.get_processing_run(run_id)["status"] == "running"
            blocked = enforcer_client.post(
                f"/api/videos/{video_id}/process",
                json={"enabled_violations": []},
                content_type="application/json",
            )
            assert blocked.status_code == 409, blocked.get_json()

            _expire_lease(test_db_path, run_id)
            test_db.recover_orphaned_processing()
            assert _wait_until(
                lambda: test_db.get_processing_run(run_id)["status"] == "failed"
                and test_db.get_processing_run(run_id)["error_message"] == "Worker lease expired."
            )
            assert app_module._processing_jobs.get(video_id, {}).get("state") != "processing"
            assert app_module._is_processing_or_queued(video_id) is False

            _release_gpu_blockers(blockers)
            blockers.clear()
            retried = enforcer_client.post(
                f"/api/videos/{video_id}/process",
                json={"enabled_violations": []},
                content_type="application/json",
            )
            assert retried.status_code == 200, retried.get_json()
            retry_run = int(retried.get_json()["run_id"])
            assert _wait_until(
                lambda: test_db.get_processing_run(retry_run)["status"] == "completed"
            )
            app_module.stop_processing_worker(timeout=5)
        finally:
            _release_gpu_blockers(blockers)
            app_module.stop_processing_worker(timeout=5)

    def test_replacement_attempt_survives_rejected_release_cleanup(
        self, test_db, monkeypatch
    ):
        monkeypatch.setattr(app_module, "_VIDEO_JOB_GPU_WAIT_SEC", 0.05)
        monkeypatch.setattr(
            app_module,
            "_call_process_video",
            lambda *_a, **_k: pytest.fail("replacement cleanup must not infer"),
        )
        blockers = [
            gpu_inference_slot().acquire(
                f"video-job:replacement-blocker-{index}",
                timeout=0.0,
                priority=PRIORITY_VIDEO_JOB,
            )
            for index in (1, 2)
        ]
        assert all(blocker is not None for blocker in blockers)
        owner = test_db.create_user("replacement-owner", "test-hash", role="enforcer")
        video_id = test_db.insert_video(
            filename="replacement.mp4",
            filepath="/tmp/replacement.mp4",
            status="ready",
            uploaded_by=owner,
        )
        other_id = test_db.insert_video(
            filename="other-active.mp4",
            filepath="/tmp/other-active.mp4",
            status="ready",
            uploaded_by=owner,
        )
        run_id = test_db.enqueue_processing_run(video_id, "[]")
        with app_module._queue_cv:
            app_module._process_queue.append((video_id, (), run_id))
        original_release = test_db.release_processing_claim
        original_claim = test_db.claim_next_processing_run
        reached = threading.Event()
        gate = threading.Event()
        claims = {"count": 0}

        def claim(worker_id, *args, **kwargs):
            claims["count"] += 1
            return original_claim(worker_id, *args, **kwargs)

        def release(run_id_arg, worker_id):
            if int(run_id_arg) == int(run_id):
                reached.set()
                assert gate.wait(5.0)
                return False
            return original_release(run_id_arg, worker_id)

        monkeypatch.setattr(app_module.db, "claim_next_processing_run", claim)
        monkeypatch.setattr(app_module.db, "release_processing_claim", release)
        app_module._worker_shutdown.clear()
        worker = threading.Thread(target=app_module._processing_worker, daemon=True)
        worker.start()
        try:
            assert reached.wait(10.0)
            shared_event = app_module._processing_cancel_events[run_id]
            replacement_tracker = ProgressTracker(
                run_id=run_id, video_id=video_id, viewer_mode="watch_live"
            )
            replacement_tracker.set_stage("loading_model")
            app_module._processing_jobs[video_id] = {
                "state": "processing",
                "error": None,
                "run_id": run_id,
                "tracker": replacement_tracker,
                "viewer_mode": "watch_live",
            }
            app_module._processing_cancel_events[run_id] = shared_event
            preview_hub.publish(video_id, b"replacement-preview")
            app_module._adopt_running_attempt(video_id, object())
            with app_module._queue_cv:
                app_module._running_video_ids.add(other_id)
                app_module._running_video_id = other_id
            claims_before = claims["count"]
            gate.set()
            assert _wait_until(lambda: claims["count"] > claims_before)
            assert app_module._processing_jobs[video_id]["tracker"] is replacement_tracker
            replacement_tracker.set_stage("processing")
            assert replacement_tracker.snapshot()["stage"] == "processing"
            assert app_module._processing_cancel_events[run_id] is shared_event
            shared_event.set()
            assert shared_event.is_set() is True
            assert preview_hub.latest(video_id) == b"replacement-preview"
            assert video_id in app_module._running_video_ids
            assert other_id in app_module._running_video_ids
            assert app_module._running_video_id == other_id
            assert app_module._running_video_id in app_module._running_video_ids
        finally:
            gate.set()
            _release_gpu_blockers(blockers)
            preview_hub.clear(video_id)
            app_module._worker_shutdown.set()
            with app_module._queue_cv:
                app_module._queue_cv.notify_all()
            worker.join(10.0)
            app_module._worker_shutdown.clear()
        assert not worker.is_alive()


class TestPostReleaseReadCleanup:
    """Local cleanup after a successful GPU-timeout release must survive a bad read."""

    def test_status_read_failure_after_release_still_clears_local_attempt(
        self, test_db, enforcer_client, monkeypatch, caplog
    ):
        _assert_post_release_read_is_best_effort(
            test_db,
            enforcer_client,
            monkeypatch,
            caplog,
            failing_read="get_processing_run",
        )

    def test_queue_position_read_failure_after_release_still_clears_local_attempt(
        self, test_db, enforcer_client, monkeypatch, caplog
    ):
        _assert_post_release_read_is_best_effort(
            test_db,
            enforcer_client,
            monkeypatch,
            caplog,
            failing_read="processing_queue_position",
        )


def _assert_post_release_read_is_best_effort(
    test_db,
    enforcer_client,
    monkeypatch,
    caplog,
    *,
    failing_read: str,
) -> None:
    """One failed post-release read must not pin the video or kill the worker.

    The injected failure is one-shot and happens only on the worker thread after
    ``release_processing_claim`` has already returned true. Later status reads
    use the real SQLite functions.
    """
    monkeypatch.setattr(app_module, "_VIDEO_JOB_GPU_WAIT_SEC", 0.05)
    monkeypatch.setattr(app_module, "_ensure_worker", lambda: None)
    monkeypatch.setattr(app_module, "bootstrap_detail_queue", lambda: None)
    monkeypatch.setattr(
        app_module, "process_video", lambda *_a, **_k: app_module.ProcessVideoResult()
    )
    blockers = [
        gpu_inference_slot().acquire(
            f"video-job:post-release-{failing_read}-{index}",
            timeout=0.0,
            priority=PRIORITY_VIDEO_JOB,
        )
        for index in (1, 2)
    ]
    assert all(blocker is not None for blocker in blockers)
    owner = test_db.get_user_by_username("enforcer_test")
    video_id = test_db.insert_video(
        filename=f"{failing_read}.mp4",
        filepath=f"/tmp/{failing_read}.mp4",
        status="ready",
        uploaded_by=owner["id"],
    )
    other_id = test_db.insert_video(
        filename=f"other-{failing_read}.mp4",
        filepath=f"/tmp/other-{failing_read}.mp4",
        status="ready",
        uploaded_by=owner["id"],
    )
    next_id = test_db.insert_video(
        filename=f"next-{failing_read}.mp4",
        filepath=f"/tmp/next-{failing_read}.mp4",
        status="ready",
        uploaded_by=owner["id"],
    )
    test_db.upsert_annotation(video_id, ZONES)
    run_id = test_db.enqueue_processing_run(video_id, "[]")
    next_run = test_db.enqueue_processing_run(next_id, "[]")
    with app_module._queue_cv:
        app_module._running_video_ids.add(other_id)
        app_module._running_video_id = other_id

    original_release = app_module.db.release_processing_claim
    original_get = app_module.db.get_processing_run
    original_position = app_module.db.processing_queue_position
    original_claim = app_module.db.claim_next_processing_run
    original_finish = app_module._finish_local_attempt
    released = threading.Event()
    read_failed = threading.Event()
    claim_gate = threading.Event()
    claims = {"count": 0}
    finishes = {"count": 0}
    consumed = {"done": False}

    def release(released_run_id, worker_id):
        result = original_release(released_run_id, worker_id)
        if (
            threading.current_thread() is worker
            and int(released_run_id) == int(run_id)
            and result is True
        ):
            released.set()
        return result

    def fail_once():
        if (
            threading.current_thread() is worker
            and released.is_set()
            and not consumed["done"]
        ):
            consumed["done"] = True
            read_failed.set()
            raise sqlite3.OperationalError(f"synthetic {failing_read} outage")

    def get_processing_run(read_run_id):
        if failing_read == "get_processing_run" and int(read_run_id) == int(run_id):
            fail_once()
        return original_get(read_run_id)

    def processing_queue_position(read_video_id):
        if (
            failing_read == "processing_queue_position"
            and int(read_video_id) == int(video_id)
        ):
            fail_once()
        return original_position(read_video_id)

    def claim(worker_id, *args, **kwargs):
        if threading.current_thread() is worker:
            claims["count"] += 1
            if claims["count"] == 2 and not claim_gate.wait(10):
                raise TimeoutError("second claim was not released")
        return original_claim(worker_id, *args, **kwargs)

    def finish(*args, **kwargs):
        if threading.current_thread() is worker:
            finishes["count"] += 1
        return original_finish(*args, **kwargs)

    monkeypatch.setattr(app_module.db, "release_processing_claim", release)
    monkeypatch.setattr(app_module.db, "get_processing_run", get_processing_run)
    monkeypatch.setattr(app_module.db, "processing_queue_position", processing_queue_position)
    monkeypatch.setattr(app_module.db, "claim_next_processing_run", claim)
    monkeypatch.setattr(app_module, "_finish_local_attempt", finish)
    caplog.set_level(logging.ERROR, logger="app")
    app_module._worker_shutdown.clear()
    worker = threading.Thread(
        target=app_module._processing_worker,
        args=("post-release-read-worker",),
        daemon=True,
        name="post-release-read-worker",
    )
    worker.start()
    try:
        assert released.wait(10)
        assert read_failed.wait(10)
        assert _wait_until(
            lambda: (
                app_module._processing_jobs.get(video_id) is None
                and video_id not in app_module._running_video_ids
                and other_id in app_module._running_video_ids
                and app_module._running_video_id == other_id
                and finishes["count"] == 1
                and worker.is_alive()
            ),
            timeout=2.0,
        )
        assert str(run_id) in caplog.text
        assert test_db.get_processing_run(run_id)["status"] == "queued"
        assert test_db.get_processing_run(run_id)["worker_id"] in (None, "")
        blocked = enforcer_client.post(
            f"/api/videos/{video_id}/process",
            json={"enabled_violations": []},
            content_type="application/json",
        )
        assert blocked.status_code == 409, blocked.get_json()
        assert test_db.get_processing_run(run_id)["status"] == "queued"
        assert finishes["count"] == 1

        assert test_db.request_processing_cancel(run_id) == "cancelled"
        assert test_db.get_processing_run(run_id)["status"] == "cancelled"
        assert test_db.processing_queue_position(video_id) is None
        assert app_module._is_processing_or_queued(video_id) is False
        retried = enforcer_client.post(
            f"/api/videos/{video_id}/process",
            json={"enabled_violations": []},
            content_type="application/json",
        )
        assert retried.status_code == 200, retried.get_json()
        retry_run = int(retried.get_json()["run_id"])
        assert retry_run != run_id

        _release_gpu_blockers(blockers)
        blockers.clear()
        claim_gate.set()
        assert _wait_until(
            lambda: test_db.get_processing_run(next_run)["status"] == "completed"
            and test_db.get_processing_run(retry_run)["status"] == "completed"
        )
        assert worker.is_alive()
        assert worker.ident is not None
        assert claims["count"] >= 2
        assert not app_module._worker_threads
        assert other_id in app_module._running_video_ids
    finally:
        claim_gate.set()
        _release_gpu_blockers(blockers)
        app_module._worker_shutdown.set()
        with app_module._queue_cv:
            app_module._queue_cv.notify_all()
        worker.join(10.0)
        app_module._worker_shutdown.clear()
    assert not worker.is_alive()


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


def _designated_checkpoint(monkeypatch):
    """Force the one-shot manual scan past the checkpoint gate.

    Only the gate is stubbed: the reservation, the queue peek, the claim, and
    the crop scan all run for real, so the endpoint's busy contract is what is
    under test.
    """
    from core.motorcycle_detail_scan import DetailCheckpoint, MotorcycleDetailScanner as _Real

    checkpoint = DetailCheckpoint(
        path="mock",
        class_names=("helmet_nut_shell", "helmet_acceptable", "side_mirror"),
        ok=True,
        identity="md3c:mock",
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
        tokens = [gpu_inference_slot().acquire(
            f"video-job:held-{index}", timeout=0.0, priority=PRIORITY_VIDEO_JOB
        ) for index in (1, 2)]
        assert all(token is not None for token in tokens)
        try:
            response = enforcer_client.post("/api/motorcycle-detail-review/scan-now")
        finally:
            for token in tokens:
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
        tokens = [gpu_inference_slot().acquire(
            f"live-frame:camera-{index}", timeout=0.0, priority=PRIORITY_LIVE_FRAME
        ) for index in (1, 2)]
        assert all(token is not None for token in tokens)
        try:
            response = enforcer_client.post("/api/motorcycle-detail-review/scan-now")
        finally:
            for token in tokens:
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
    def test_camera_open_error_omits_connection_details(self, monkeypatch):
        from unittest.mock import Mock
        from core import live_stream as live

        worker = live.LiveStreamWorker({"id": 3, "rtsp_url": "rtsp://user:synthetic-secret@camera/stream"})
        capture = Mock()
        capture.isOpened.return_value = False
        _stub_live_pipeline(monkeypatch, live, Mock, capture)
        worker.run()
        manager = live.StreamManager()
        manager._workers[3] = worker
        status = manager.status(3)
        assert status["status"] == "error"
        assert status["error"] == "Cannot open stream for camera 3."

    def test_frame_is_dropped_instead_of_inferring_twice(self, monkeypatch):
        from core import live_stream as live

        worker = live.LiveStreamWorker({"id": 3, "name": "Gate", "rtsp_url": "rtsp://cam-3"})
        slot = gpu_inference_slot()
        tokens = [
            slot.acquire(f"video-job:{index}", timeout=0.0, priority=PRIORITY_VIDEO_JOB)
            for index in (1, 2)
        ]
        assert all(token is not None for token in tokens)
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
            for token in tokens:
                slot.release(token)
        assert calls == []
        activity = worker.frame_activity()
        assert activity["gpu_skipped_frames"] == 2
        assert activity["last_decoded_age_sec"] is not None
        assert activity["last_inferred_age_sec"] is None
        assert activity["last_published_age_sec"] is None
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
