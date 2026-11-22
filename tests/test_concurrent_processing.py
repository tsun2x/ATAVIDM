"""Account-fair, cross-process claims for concurrent video processing."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

import app as app_module
from core.video_processor import ProcessVideoError, ProcessVideoResult


def _user(db, username: str) -> int:
    return db.create_user(username, "test-hash", role="enforcer")


def _video(db, owner_id: int, filename: str) -> int:
    return db.insert_video(
        filename=filename,
        filepath=f"/tmp/{filename}",
        status="ready",
        uploaded_by=owner_id,
    )


def test_scheduler_defaults_to_three_jobs_and_one_active_job_per_account(test_db):
    """Catches local queues dispatching two videos from one owner's backlog."""
    owner_a = _user(test_db, "owner-a")
    owner_b = _user(test_db, "owner-b")
    owner_c = _user(test_db, "owner-c")
    video_a1 = _video(test_db, owner_a, "a1.mp4")
    video_a2 = _video(test_db, owner_a, "a2.mp4")
    video_b1 = _video(test_db, owner_b, "b1.mp4")
    video_c1 = _video(test_db, owner_c, "c1.mp4")

    run_a1 = test_db.enqueue_processing_run(video_a1, "[]")
    run_a2 = test_db.enqueue_processing_run(video_a2, "[]")
    run_b1 = test_db.enqueue_processing_run(video_b1, "[]")
    run_c1 = test_db.enqueue_processing_run(video_c1, "[]")

    first = test_db.claim_next_processing_run("worker-1")
    second = test_db.claim_next_processing_run("worker-2")
    third = test_db.claim_next_processing_run("worker-3")

    assert first["id"] == run_a1
    assert second["id"] == run_b1
    assert third["id"] == run_c1

    test_db.finish_processing_run(run_a1)
    next_job = test_db.claim_next_processing_run("worker-1")
    assert next_job["id"] == run_a2


def test_scheduler_obeys_admin_configured_global_and_account_limits(test_db):
    owner_a = _user(test_db, "limit-owner-a")
    owner_b = _user(test_db, "limit-owner-b")
    videos = [
        _video(test_db, owner_a, "limit-a1.mp4"),
        _video(test_db, owner_a, "limit-a2.mp4"),
        _video(test_db, owner_a, "limit-a3.mp4"),
        _video(test_db, owner_b, "limit-b1.mp4"),
        _video(test_db, owner_b, "limit-b2.mp4"),
    ]
    test_db.set_settings({
        "max_concurrent_video_jobs": 3,
        "max_concurrent_jobs_per_account": 2,
    })
    for video_id in videos:
        test_db.enqueue_processing_run(video_id, "[]")

    claims = [test_db.claim_next_processing_run(f"limit-worker-{index}") for index in range(4)]
    claimed = [claim for claim in claims if claim is not None]

    assert len(claimed) == 3
    assert sum(claim["uploaded_by"] == owner_a for claim in claimed) == 2
    assert sum(claim["uploaded_by"] == owner_b for claim in claimed) == 1


def test_worker_lease_migration_is_safe_to_reapply(test_db):
    with test_db.db_session() as conn:
        test_db._apply_migration_014(conn)
        test_db._apply_migration_014(conn)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(processing_runs)")}
        index = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_processing_runs_queue_claim'"
        ).fetchone()
        dispatch_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='processing_account_dispatch'"
        ).fetchone()

    assert {"worker_id", "heartbeat_at", "lease_expires_at", "cancel_requested_at"} <= columns
    assert index is not None
    assert dispatch_table is not None


def test_simultaneous_claims_never_exceed_configured_global_limit(test_db):
    """Catches separate SQLite connections both admitting beyond the GPU cap."""
    owners = [_user(test_db, f"owner-{i}") for i in range(6)]
    for i, owner_id in enumerate(owners):
        video_id = _video(test_db, owner_id, f"video-{i}.mp4")
        test_db.enqueue_processing_run(video_id, "[]")

    with ThreadPoolExecutor(max_workers=6) as pool:
        claimed = list(pool.map(
            lambda i: test_db.claim_next_processing_run(f"worker-{i}"),
            range(6),
        ))

    claimed = [row for row in claimed if row is not None]
    assert len(claimed) == 3
    assert len({row["uploaded_by"] for row in claimed}) == 3


def test_startup_recovery_leaves_fresh_worker_claims_running(test_db):
    """Catches one app process failing jobs owned by a still-live peer process."""
    owner_id = _user(test_db, "recovery-owner")
    video_id = _video(test_db, owner_id, "recovery.mp4")
    run_id = test_db.enqueue_processing_run(video_id, "[]")
    claimed = test_db.claim_next_processing_run("worker-live")
    assert claimed["id"] == run_id

    recovered = test_db.recover_orphaned_processing()

    assert recovered == 0
    assert test_db.get_processing_run(run_id)["status"] == "running"


def test_duplicate_video_enqueue_is_rejected_atomically(test_db):
    """Catches concurrent API processes creating duplicate queued runs."""
    owner_id = _user(test_db, "duplicate-owner")
    video_id = _video(test_db, owner_id, "duplicate.mp4")

    first = test_db.enqueue_processing_run(video_id, "[]")
    second = test_db.enqueue_processing_run(video_id, "[]")

    assert first is not None
    assert second is None
    assert len(test_db.list_processing_runs(video_id)) == 1


def test_worker_can_release_claim_back_to_durable_queue(test_db):
    owner_id = _user(test_db, "release-owner")
    video_id = _video(test_db, owner_id, "release.mp4")
    run_id = test_db.enqueue_processing_run(video_id, "[]")
    claim = test_db.claim_next_processing_run("worker-release")

    assert claim["id"] == run_id
    assert test_db.release_processing_claim(run_id, "worker-release") is True
    assert test_db.get_processing_run(run_id)["status"] == "queued"
    assert test_db.claim_next_processing_run("worker-retry")["id"] == run_id


def test_release_and_cancel_restore_previous_processed_video(test_db):
    owner_id = _user(test_db, "restore-owner")
    video_id = _video(test_db, owner_id, "restore.mp4")
    first = test_db.enqueue_processing_run(video_id, "[]")
    assert test_db.claim_next_processing_run("worker-first")
    assert test_db.finish_processing_run(first, worker_id="worker-first") is True
    second = test_db.enqueue_processing_run(video_id, "[]")
    assert test_db.claim_next_processing_run("worker-second")

    assert test_db.release_processing_claim(second, "worker-second") is True
    assert test_db.get_video(video_id)["status"] == "processed"

    assert test_db.claim_next_processing_run("worker-third")
    assert test_db.finish_processing_run(
        second, status="cancelled", worker_id="worker-third", progress={"stage": "cancelled"}
    ) is True
    assert test_db.get_processing_run(second)["status"] == "cancelled"
    assert test_db.get_video(video_id)["status"] == "processed"


def test_stale_worker_cannot_finish_or_write_progress(test_db):
    owner_id = _user(test_db, "fence-owner")
    video_id = _video(test_db, owner_id, "fence.mp4")
    run_id = test_db.enqueue_processing_run(video_id, "[]")
    assert test_db.claim_next_processing_run("current-worker")

    assert test_db.finish_processing_run(
        run_id, worker_id="stale-worker", status="completed",
        progress={"stage": "completed", "frames_processed": 999},
    ) is False
    test_db.update_processing_run_progress(
        run_id, {"stage": "stale", "frames_processed": 999}, worker_id="stale-worker"
    )
    row = test_db.get_processing_run(run_id)
    assert row["status"] == "running"
    assert row["stage"] == "loading_model"
    assert row["frames_processed"] != 999
    assert test_db.get_video(video_id)["status"] == "processing"


def test_cancel_requested_claim_is_not_requeued_on_release(test_db):
    owner_id = _user(test_db, "cancel-release-owner")
    video_id = _video(test_db, owner_id, "cancel-release.mp4")
    run_id = test_db.enqueue_processing_run(video_id, "[]")
    claim = test_db.claim_next_processing_run("cancel-release-worker")
    assert claim["id"] == run_id

    assert test_db.request_processing_cancel(run_id) == "stopping"
    assert test_db.release_processing_claim(run_id, "cancel-release-worker") is True
    assert test_db.get_processing_run(run_id)["status"] == "cancelled"
    assert test_db.claim_next_processing_run("next-worker") is None
    assert test_db.get_video(video_id)["status"] == "ready"


def test_worker_reports_failed_terminal_write_without_completed_tracker(test_db, monkeypatch):
    owner_id = _user(test_db, "terminal-write-owner")
    video_id = _video(test_db, owner_id, "terminal-write.mp4")
    run_id = test_db.enqueue_processing_run(video_id, "[]")
    original_finish = test_db.finish_processing_run
    completed_attempt = threading.Event()

    def fail_completed(run_id_arg, *, status="completed", **kwargs):
        if status == "completed":
            completed_attempt.set()
            raise OSError("terminal write unavailable")
        return original_finish(run_id_arg, status=status, **kwargs)

    monkeypatch.setattr(app_module, "process_video", lambda _video_id, **_kwargs: ProcessVideoResult())
    monkeypatch.setattr(app_module.db, "finish_processing_run", fail_completed)
    try:
        app_module._ensure_worker()
        with app_module._queue_cv:
            app_module._queue_cv.notify_all()
        assert completed_attempt.wait(5)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and test_db.get_processing_run(run_id)["status"] not in {"completed", "failed", "cancelled"}:
            time.sleep(0.01)
        assert test_db.get_processing_run(run_id)["status"] == "failed"
        assert app_module._processing_jobs[video_id]["state"] == "error"
        assert app_module._processing_jobs[video_id]["tracker"].snapshot()["stage"] == "failed"
        assert test_db.get_video(video_id)["status"] == "ready"
    finally:
        app_module.stop_processing_worker(timeout=5)


def test_completion_history_failure_does_not_reverse_success(test_db, monkeypatch):
    owner_id = _user(test_db, "history-write-owner")
    video_id = _video(test_db, owner_id, "history-write.mp4")
    run_id = test_db.enqueue_processing_run(video_id, "[]")

    def fail_history(*_args, **_kwargs):
        raise OSError("history storage unavailable")

    monkeypatch.setattr(app_module, "process_video", lambda _video_id, **_kwargs: ProcessVideoResult())
    monkeypatch.setattr(app_module.db, "insert_video_history_event", fail_history)
    try:
        app_module._ensure_worker()
        with app_module._queue_cv:
            app_module._queue_cv.notify_all()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and test_db.get_processing_run(run_id)["status"] == "running":
            time.sleep(0.01)
        assert test_db.get_processing_run(run_id)["status"] == "completed"
        assert test_db.get_video(video_id)["status"] == "processed"
        assert app_module._processing_jobs[video_id]["state"] == "done"
    finally:
        app_module.stop_processing_worker(timeout=5)


def test_failure_history_error_does_not_mask_pipeline_failure(test_db, monkeypatch):
    owner_id = _user(test_db, "failure-history-owner")
    video_id = _video(test_db, owner_id, "failure-history.mp4")
    run_id = test_db.enqueue_processing_run(video_id, "[]")
    pipeline_started = threading.Event()

    def fail_pipeline(_video_id, **_kwargs):
        pipeline_started.set()
        raise ProcessVideoError("synthetic pipeline failure")

    def fail_history(*_args, **_kwargs):
        raise OSError("history storage unavailable")

    monkeypatch.setattr(app_module, "process_video", fail_pipeline)
    monkeypatch.setattr(app_module.db, "insert_video_history_event", fail_history)
    try:
        app_module._ensure_worker()
        with app_module._queue_cv:
            app_module._queue_cv.notify_all()
        assert pipeline_started.wait(15)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and test_db.get_processing_run(run_id)["status"] == "running":
            time.sleep(0.01)
        assert test_db.get_processing_run(run_id)["status"] == "failed"
        assert test_db.get_video(video_id)["status"] == "ready"
        assert app_module._processing_jobs[video_id]["state"] == "error"
        assert app_module._processing_jobs[video_id]["error"] == "synthetic pipeline failure"
    finally:
        app_module.stop_processing_worker(timeout=5)


@pytest.mark.parametrize("failure_type", [ProcessVideoError, RuntimeError])
def test_unavailable_failure_write_does_not_publish_failed_state(test_db, monkeypatch, failure_type):
    owner_id = _user(test_db, "failure-write-owner")
    video_id = _video(test_db, owner_id, "failure-write.mp4")
    run_id = test_db.enqueue_processing_run(video_id, "[]")
    original_finish = test_db.finish_processing_run

    pipeline_started = threading.Event()
    terminal_attempted = threading.Event()

    def fail_pipeline(_video_id, **_kwargs):
        pipeline_started.set()
        raise failure_type("synthetic failure")

    def fail_terminal(*_args, **_kwargs):
        terminal_attempted.set()
        raise OSError("synthetic database outage")

    monkeypatch.setattr(app_module, "process_video", fail_pipeline)
    monkeypatch.setattr(app_module.db, "finish_processing_run", fail_terminal)
    try:
        app_module._worker_shutdown.clear()
        worker = threading.Thread(target=app_module._processing_worker, args=("failure-write-test-worker",), daemon=True)
        worker.start()
        assert pipeline_started.wait(5)
        assert terminal_attempted.wait(5)
        cached = app_module._processing_jobs[video_id]
        old_tracker = cached["tracker"]
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and app_module._processing_jobs.get(video_id) is cached:
            time.sleep(0.01)
        assert app_module._processing_jobs.get(video_id) is not cached
        assert old_tracker.snapshot()["stage"] != "failed"
        assert test_db.get_processing_run(run_id)["status"] == "running"
        assert not any(e["event_type"] == "processing_failed" for e in test_db.list_video_history_events(video_id))
    finally:
        app_module._worker_shutdown.set()
        with app_module._queue_cv:
            app_module._queue_cv.notify_all()
        worker.join(5)
        assert not worker.is_alive()
        app_module._worker_shutdown.clear()

    # Expire the abandoned durable lease through SQLite, then exercise the
    # ordinary enqueue helper and worker path with the same video.
    monkeypatch.setattr(app_module.db, "finish_processing_run", original_finish)
    with test_db.db_session() as conn:
        conn.execute(
            "UPDATE processing_runs SET lease_expires_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00+00:00", run_id),
        )
    assert test_db.recover_orphaned_processing() == 1
    assert not app_module._is_processing_or_queued(video_id)
    monkeypatch.setattr(app_module, "process_video", lambda *_a, **_k: ProcessVideoResult())
    retry_run, _queued = app_module._enqueue_video_job(video_id, enabled=(), actor_user_id=owner_id)
    assert retry_run != run_id
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and test_db.get_processing_run(retry_run)["status"] != "completed":
        time.sleep(0.01)
    assert test_db.get_processing_run(retry_run)["status"] == "completed"
    app_module.stop_processing_worker(timeout=5)


@pytest.mark.parametrize("write_mode", ["committed", "rejected", "raised"])
def test_cancel_is_published_only_after_durable_write_and_worker_continues(
    test_db, monkeypatch, write_mode
):
    owner_a = _user(test_db, f"cancel-fence-a-{write_mode}")
    owner_b = _user(test_db, f"cancel-fence-b-{write_mode}")
    cancelled_video = _video(test_db, owner_a, f"cancel-fence-{write_mode}.mp4")
    next_video = _video(test_db, owner_b, f"after-cancel-{write_mode}.mp4")
    cancelled_run = test_db.enqueue_processing_run(cancelled_video, "[]")
    next_run = test_db.enqueue_processing_run(next_video, "[]")
    app_module._processing_jobs.pop(cancelled_video, None)
    app_module._processing_jobs.pop(next_video, None)
    cancel_write_started = threading.Event()
    release_cancel_write = threading.Event()
    other_job_started = threading.Event()
    observed_tracker = {}
    real_finish = test_db.finish_processing_run

    def process(video_id, **kwargs):
        if video_id == cancelled_video:
            observed_tracker["tracker"] = kwargs["progress_tracker"]
            raise app_module.ProcessVideoCancelled("cancel requested")
        other_job_started.set()
        return ProcessVideoResult()

    def finish(run_id, *, status="completed", **kwargs):
        if run_id == cancelled_run and status == "cancelled":
            cancel_write_started.set()
            assert release_cancel_write.wait(5)
            if write_mode == "raised":
                raise OSError("synthetic cancellation storage outage")
            if write_mode == "committed":
                return real_finish(run_id, status=status, **kwargs)
            return False
        return real_finish(run_id, status=status, **kwargs)

    monkeypatch.setattr(app_module, "process_video", process)
    monkeypatch.setattr(app_module.db, "finish_processing_run", finish)
    app_module._worker_shutdown.clear()
    worker = threading.Thread(target=app_module._processing_worker, args=(f"cancel-fence-{write_mode}",), daemon=True)
    worker.start()
    try:
        assert cancel_write_started.wait(5)
        tracker = observed_tracker["tracker"]
        before = tracker.snapshot()
        assert before["state"] == "processing"
        assert before["stage"] != "cancelled"
        assert test_db.get_processing_run(cancelled_run)["status"] == "running"
        assert not any(e["event_type"] == "processing_cancelled" for e in test_db.list_video_history_events(cancelled_video))
        release_cancel_write.set()
        assert other_job_started.wait(5)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and test_db.get_processing_run(next_run)["status"] != "completed":
            time.sleep(0.01)
        assert test_db.get_processing_run(next_run)["status"] == "completed"
        if write_mode == "committed":
            assert tracker.snapshot()["state"] == "cancelled"
            assert test_db.get_processing_run(cancelled_run)["status"] == "cancelled"
            assert any(e["event_type"] == "processing_cancelled" for e in test_db.list_video_history_events(cancelled_video))
        else:
            assert tracker.snapshot()["state"] != "cancelled"
            assert app_module._processing_jobs.get(cancelled_video, {}).get("state") != "cancelled"
            assert test_db.get_processing_run(cancelled_run)["status"] == "running"
            assert not any(e["event_type"] == "processing_cancelled" for e in test_db.list_video_history_events(cancelled_video))
    finally:
        release_cancel_write.set()
        app_module._worker_shutdown.set()
        with app_module._queue_cv:
            app_module._queue_cv.notify_all()
        worker.join(5)
        app_module._worker_shutdown.clear()
    assert not worker.is_alive()


def test_terminal_ownership_loss_preserves_replacement_tracker(test_db, monkeypatch):
    owner_id = _user(test_db, "replacement-tracker-owner")
    video_id = _video(test_db, owner_id, "replacement-tracker.mp4")
    run_id = test_db.enqueue_processing_run(video_id, "[]")
    entered = threading.Event()
    release = threading.Event()
    original_finish = test_db.finish_processing_run
    replacement = app_module.ProgressTracker(run_id=run_id, video_id=video_id)

    def process(_video_id, **_kwargs):
        entered.set()
        assert release.wait(5)
        return ProcessVideoResult()

    def finish(run_id_arg, *, status="completed", **kwargs):
        if status == "completed":
            return False
        return original_finish(run_id_arg, status=status, **kwargs)

    monkeypatch.setattr(app_module, "process_video", process)
    monkeypatch.setattr(app_module.db, "finish_processing_run", finish)
    app_module._processing_jobs.pop(video_id, None)
    app_module._worker_shutdown.clear()
    worker = threading.Thread(target=app_module._processing_worker, args=("replacement-tracker-worker",), daemon=True)
    worker.start()
    try:
        assert entered.wait(5)
        app_module._processing_jobs[video_id] = {
            "state": "processing", "error": None, "run_id": run_id,
            "tracker": replacement, "viewer_mode": "background",
        }
        release.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and test_db.get_processing_run(run_id)["status"] == "running":
            time.sleep(0.01)
        assert test_db.get_processing_run(run_id)["status"] == "running"
        assert app_module._processing_jobs[video_id]["tracker"] is replacement
    finally:
        release.set()
        app_module._worker_shutdown.set()
        with app_module._queue_cv:
            app_module._queue_cv.notify_all()
        worker.join(5)
        app_module._worker_shutdown.clear()
    assert not worker.is_alive()


def test_workers_run_two_accounts_and_hold_same_account_backlog(test_db, monkeypatch):
    """Catches a local worker pool ignoring account-aware SQLite claims."""
    owner_a = _user(test_db, "worker-owner-a")
    owner_b = _user(test_db, "worker-owner-b")
    videos = [
        _video(test_db, owner_a, "worker-a1.mp4"),
        _video(test_db, owner_a, "worker-a2.mp4"),
        _video(test_db, owner_b, "worker-b1.mp4"),
    ]
    run_ids = [test_db.enqueue_processing_run(video, "[]") for video in videos]
    started: dict[int, threading.Event] = {video: threading.Event() for video in videos}
    release = threading.Event()

    def fake_process(video_id, **kwargs):
        started[video_id].set()
        assert release.wait(5)
        return ProcessVideoResult(events=[])

    monkeypatch.setattr(app_module, "process_video", fake_process)
    try:
        app_module._ensure_worker()
        with app_module._queue_cv:
            app_module._queue_cv.notify_all()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not (started[videos[0]].is_set() and started[videos[2]].is_set()):
            time.sleep(0.01)

        assert started[videos[0]].is_set()
        assert started[videos[2]].is_set()
        assert not started[videos[1]].is_set()
        rows = [test_db.get_processing_run(run_id) for run_id in run_ids]
        active = [row for row in rows if row["status"] == "running"]
        assert len(active) == 2
        assert {test_db.get_video(row["video_id"])["uploaded_by"] for row in active} == {owner_a, owner_b}

        release.set()
        assert started[videos[1]].wait(3)
    finally:
        release.set()
        app_module.stop_processing_worker(timeout=5)
