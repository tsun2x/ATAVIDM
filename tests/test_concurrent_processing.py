"""Account-fair, cross-process claims for concurrent video processing."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
import time

import app as app_module
from core.video_processor import ProcessVideoResult


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
