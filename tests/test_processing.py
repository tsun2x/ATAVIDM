"""Tests for per-run processing: validation, sequential queue, recovery."""

from __future__ import annotations

import json
import threading
import time

import pytest

import app as app_module
from core.detection_config import (
    PLANNED_VIOLATIONS,
    VIOLATION_ILLEGAL_PARKING,
    VIOLATION_OBSTRUCTION,
)
from core.violation_config import validate_enabled_violations


ZONES = json.dumps({"z": [[0, 0], [1, 0], [1, 1], [0, 1]]})


@pytest.fixture(autouse=True)
def _reset_processing_state(test_db_path):
    """Stop the worker and clear in-memory queue/jobs between tests.

    Depends on ``test_db_path`` so this fixture tears down *before* the
    temporary SQLite directory is deleted. Leaving the worker alive keeps a
    Windows file lock on the DB and raises PermissionError at tmpdir cleanup.
    """
    app_module.stop_processing_worker()
    app_module._process_queue.clear()
    app_module._processing_jobs.clear()
    app_module._running_video_id = None
    yield
    app_module.stop_processing_worker()
    app_module._process_queue.clear()
    app_module._processing_jobs.clear()
    app_module._running_video_id = None


class TestProcessApiValidation:
    def test_valid_subset_accepted(self):
        enabled = validate_enabled_violations([VIOLATION_ILLEGAL_PARKING, VIOLATION_OBSTRUCTION])
        assert set(enabled) == {VIOLATION_ILLEGAL_PARKING, VIOLATION_OBSTRUCTION}

    def test_unknown_type_rejected(self):
        from core.violation_config import ViolationConfigError

        with pytest.raises(ViolationConfigError):
            validate_enabled_violations(["Definitely Not A Violation"])

    def test_planned_type_rejected(self):
        from core.violation_config import ViolationConfigError

        if PLANNED_VIOLATIONS:
            with pytest.raises(ViolationConfigError):
                validate_enabled_violations([PLANNED_VIOLATIONS[0]])

    def test_endpoint_rejects_bad_payload(self, enforcer_client, test_db, monkeypatch):
        monkeypatch.setattr(app_module, "process_video", lambda *a, **k: None)
        vid = test_db.insert_video(filename="c.mp4", filepath="/tmp/c.mp4", status="ready")
        test_db.upsert_annotation(vid, ZONES)
        resp = enforcer_client.post(
            f"/api/videos/{vid}/process",
            json={"enabled_violations": ["Not Real"]},
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert resp.get_json()["success"] is False

    def test_endpoint_accepts_valid_payload_and_creates_run(self, enforcer_client, test_db, monkeypatch):
        monkeypatch.setattr(app_module, "process_video", lambda *a, **k: None)
        vid = test_db.insert_video(filename="c.mp4", filepath="/tmp/c.mp4", status="ready")
        test_db.upsert_annotation(vid, ZONES)
        resp = enforcer_client.post(
            f"/api/videos/{vid}/process",
            json={"enabled_violations": [VIOLATION_ILLEGAL_PARKING]},
            content_type="application/json",
        )
        body = resp.get_json()
        assert resp.status_code == 200, body
        assert body["success"] is True
        assert body["queued"] is False
        runs = test_db.list_processing_runs(vid)
        assert runs
        assert json.loads(runs[0]["enabled_violations_json"]) == [VIOLATION_ILLEGAL_PARKING]

    def test_empty_list_is_honored_as_zero_violations(self, enforcer_client, test_db, monkeypatch):
        monkeypatch.setattr(app_module, "process_video", lambda *a, **k: None)
        vid = test_db.insert_video(filename="c.mp4", filepath="/tmp/c.mp4", status="ready")
        test_db.upsert_annotation(vid, ZONES)
        resp = enforcer_client.post(
            f"/api/videos/{vid}/process",
            json={"enabled_violations": []},
            content_type="application/json",
        )
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()["enabled_violations"] == []


class TestSequentialQueue:
    """All process_video calls are mocked; the queue must serialize them."""

    def test_only_one_job_in_flight(self, enforcer_client, test_db, monkeypatch):
        # Track in-flight count; block while "running" so a second job can only
        # start after the first fully exits.
        state = {"inflight": 0, "max_inflight": 0}
        _lock = threading.Lock()
        _started = threading.Event()

        def fake_process(video_id, enabled_violations=None, **kwargs):
            with _lock:
                state["inflight"] += 1
                state["max_inflight"] = max(state["max_inflight"], state["inflight"])
            _started.set()  # signal the first job is now running
            # Hold the slot long enough for the second request to be enqueued.
            threading.Event().wait(0.4)
            with _lock:
                state["inflight"] -= 1

        monkeypatch.setattr(app_module, "process_video", fake_process)

        v1 = test_db.insert_video(filename="a.mp4", filepath="/tmp/a.mp4", status="ready")
        test_db.upsert_annotation(v1, ZONES)
        v2 = test_db.insert_video(filename="b.mp4", filepath="/tmp/b.mp4", status="ready")
        test_db.upsert_annotation(v2, ZONES)

        r1 = enforcer_client.post(f"/api/videos/{v1}/process", json={"enabled_violations": []}, content_type="application/json")
        assert r1.get_json()["success"] is True
        r2 = enforcer_client.post(f"/api/videos/{v2}/process", json={"enabled_violations": []}, content_type="application/json")
        # Second video is auto-queued (single inference slot) — not rejected.
        assert r2.status_code == 200
        assert r2.get_json()["queued"] is True

        _started.wait(2.0)
        # Give the worker time to drain both jobs sequentially.
        import time
        time.sleep(1.2)
        # The queue must never have run two jobs at once.
        assert state["max_inflight"] == 1

    def test_same_video_rejected_while_queued(self, enforcer_client, test_db, monkeypatch):
        started = threading.Event()

        def fake_process(video_id, enabled_violations=None, **kwargs):
            started.set()
            threading.Event().wait(0.4)

        monkeypatch.setattr(app_module, "process_video", fake_process)
        v1 = test_db.insert_video(filename="a.mp4", filepath="/tmp/a.mp4", status="ready")
        test_db.upsert_annotation(v1, ZONES)
        enforcer_client.post(f"/api/videos/{v1}/process", json={"enabled_violations": []}, content_type="application/json")
        # A duplicate request for the *same* video must be rejected (409).
        dup = enforcer_client.post(f"/api/videos/{v1}/process", json={"enabled_violations": []}, content_type="application/json")
        assert dup.status_code == 409


class TestProcessStatusReflectsError:
    def test_failed_run_reports_error(self, enforcer_client, test_db, monkeypatch):
        def fake_process(video_id, enabled_violations=None, **kwargs):
            raise RuntimeError("model exploded")

        monkeypatch.setattr(app_module, "process_video", fake_process)
        vid = test_db.insert_video(filename="c.mp4", filepath="/tmp/c.mp4", status="ready")
        test_db.upsert_annotation(vid, ZONES)
        enforcer_client.post(f"/api/videos/{vid}/process", json={"enabled_violations": []}, content_type="application/json")

        # Poll until the worker finishes (job_state should be 'error').
        import time
        status = None
        for _ in range(40):
            resp = enforcer_client.get(f"/api/videos/{vid}/process-status")
            status = resp.get_json()
            if status.get("job_state") == "error":
                break
            time.sleep(0.1)

        assert status["job_state"] == "error", status
        assert "model exploded" in (status.get("job_error") or "")


class TestOrphanRecovery:
    def test_recovers_processing_status_on_startup(self, test_db):
        vid = test_db.insert_video(filename="c.mp4", filepath="/tmp/c.mp4", status="processing")
        test_db.create_processing_run(vid, json.dumps([]))
        recovered = test_db.recover_orphaned_processing()
        assert recovered == 1
        row = test_db.get_video(vid)
        assert row["status"] == "ready"
        runs = test_db.list_processing_runs(vid)
        assert runs[0]["status"] == "failed"

    def test_recovers_orphaned_queued_run(self, test_db):
        # Real orphan scenario: a job was queued (FIFO wait) but the app
        # crashed before the worker popped it. The video is 'ready' (not
        # 'processing'), so the video-level pass never sees it.
        vid = test_db.insert_video(filename="c.mp4", filepath="/tmp/c.mp4", status="ready")
        rid = test_db.create_processing_run(vid, json.dumps(["Illegal Parking"]))
        run = test_db.get_processing_run(rid)
        assert run["status"] == "queued"
        recovered = test_db.recover_orphaned_processing()
        # Video was already 'ready': not counted as a recovered video.
        assert recovered == 0
        assert test_db.get_processing_run(rid)["status"] == "failed"
        assert test_db.get_video(vid)["status"] == "ready"

    def test_recovers_orphaned_running_run_without_processing_video(self, test_db):
        # Same gap but the run was already flipped to 'running' (worker popped
        # it) and then the process died; the video was reset to 'ready' before
        # the crash or never marked 'processing'. Second pass must clean it.
        vid = test_db.insert_video(filename="c.mp4", filepath="/tmp/c.mp4", status="ready")
        rid = test_db.create_processing_run(vid, json.dumps(["Illegal Parking"]))
        test_db.start_processing_run(rid)
        assert test_db.get_processing_run(rid)["status"] == "running"
        recovered = test_db.recover_orphaned_processing()
        assert recovered == 0
        assert test_db.get_processing_run(rid)["status"] == "failed"
        assert test_db.get_video(vid)["status"] == "ready"

    def test_run_snapshot_preserved_across_runs(self, test_db):
        vid = test_db.insert_video(filename="c.mp4", filepath="/tmp/c.mp4", status="ready")
        r1 = test_db.create_processing_run(vid, json.dumps(["Illegal Parking"]))
        r2 = test_db.create_processing_run(vid, json.dumps(["Obstruction"]))
        runs = test_db.list_processing_runs(vid)
        assert [json.loads(r["enabled_violations_json"]) for r in runs] == [
            ["Obstruction"],
            ["Illegal Parking"],
        ]
        assert r1 != r2

    def test_no_recovery_when_not_processing(self, test_db):
        vid = test_db.insert_video(filename="c.mp4", filepath="/tmp/c.mp4", status="ready")
        assert test_db.recover_orphaned_processing() == 0


class TestProcessingRunLifecycle:
    def test_create_persists_queued_status(self, test_db):
        vid = test_db.insert_video(filename="c.mp4", filepath="/tmp/c.mp4", status="ready")
        rid = test_db.create_processing_run(vid, json.dumps(["Illegal Parking"]))
        run = test_db.get_processing_run(rid)
        assert run["status"] == "queued"
        assert run["started_at"] is None

    def test_worker_transitions_queued_to_running(self, enforcer_client, test_db, monkeypatch):
        got: list[str] = []
        started = threading.Event()

        def fake_process(video_id, enabled_violations=None, **kwargs):
            runs = test_db.list_processing_runs(video_id)
            got.append(runs[0]["status"])
            started.set()
            threading.Event().wait(0.4)

        monkeypatch.setattr(app_module, "process_video", fake_process)
        vid = test_db.insert_video(filename="c.mp4", filepath="/tmp/c.mp4", status="ready")
        test_db.upsert_annotation(vid, ZONES)
        enforcer_client.post(
            f"/api/videos/{vid}/process",
            json={"enabled_violations": []},
            content_type="application/json",
        )
        started.wait(2.0)
        import time
        time.sleep(0.9)
        assert "running" in got
