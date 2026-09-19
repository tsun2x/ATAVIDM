"""Cross-video processing jobs API and presentation contract tests."""

from __future__ import annotations

import json
from pathlib import Path


def _video(db, name: str) -> int:
    return db.insert_video(
        filename=name,
        filepath=f"/tmp/{name}",
        duration_sec=30.0,
        condition="morning",
        status="ready",
    )


def _set_run(db, run_id: int, **values) -> None:
    assignments = ", ".join(f"{key} = ?" for key in values)
    with db.db_session() as conn:
        conn.execute(
            f"UPDATE processing_runs SET {assignments} WHERE id = ?",
            (*values.values(), run_id),
        )


def test_list_processing_jobs_groups_orders_and_marks_current_zero_detection(test_db):
    """Catches grouping by video status or detection count instead of run truth."""
    first_video = _video(test_db, "first.mp4")
    second_video = _video(test_db, "second.mp4")
    old = test_db.create_processing_run(first_video, "[]")
    test_db.finish_processing_run(old, progress={"detection_records": 8})
    current = test_db.create_processing_run(first_video, "[]")
    test_db.finish_processing_run(current, progress={"detection_records": 0})
    failed = test_db.create_processing_run(second_video, "[]")
    test_db.finish_processing_run(failed, status="failed", error_message="decoder stopped")
    active = test_db.create_processing_run(second_video, "[]")
    _set_run(test_db, active, status="running", stage="detecting", progress_percent=42.5)

    rows = test_db.list_processing_jobs(recent_limit=10)

    assert [row["id"] for row in rows["active"]] == [active]
    assert [row["id"] for row in rows["recent"]] == [failed, current, old]
    by_id = {row["id"]: row for row in rows["recent"]}
    assert by_id[current]["is_current_result"] is True
    assert by_id[current]["detection_records"] == 0
    assert by_id[old]["is_current_result"] is False
    assert by_id[old]["is_superseded"] is True
    assert by_id[failed]["error_message"] == "decoder stopped"


def test_list_processing_jobs_filters_and_removed_result_disables_preview(test_db):
    """Catches removed or unrelated runs being offered as previewable results."""
    keep_video = _video(test_db, "keep.mp4")
    other_video = _video(test_db, "other.mp4")
    removed = test_db.create_processing_run(keep_video, "[]")
    test_db.finish_processing_run(
        removed,
        progress={
            "annotated_video_ready": True,
            "annotated_video_path": "/private/path/result.webm",
        },
    )
    test_db.clear_processing_run_artifact(removed)
    other = test_db.create_processing_run(other_video, "[]")
    test_db.finish_processing_run(other)

    rows = test_db.list_processing_jobs(recent_limit=1, video_id=keep_video)

    assert [row["id"] for row in rows["recent"]] == [removed]
    assert rows["recent"][0]["results_removed"] is True
    assert "annotated_video_path" not in rows["recent"][0]


def test_processing_jobs_api_requires_auth_and_validates_bounds(client, enforcer_client):
    """Catches an unauthenticated or unbounded cross-video history endpoint."""
    client.get("/logout")
    unauthenticated = client.get("/api/processing-jobs")
    assert unauthenticated.status_code in (302, 401)

    enforcer_client.post(
        "/login", data={"username": "enforcer_test", "password": "enforcer123"}
    )
    bad_limit = enforcer_client.get("/api/processing-jobs?recent_limit=nope")
    assert bad_limit.status_code == 400
    bad_video = enforcer_client.get("/api/processing-jobs?video_id=not-an-id")
    assert bad_video.status_code == 400


def test_processing_jobs_api_returns_safe_urls_and_poll_interval(
    enforcer_client, test_db, monkeypatch, tmp_path
):
    """Catches path disclosure and incorrect idle/active polling advice."""
    import core.processing_jobs as processing_jobs

    video_id = _video(test_db, "safe.mp4")
    result_dir = tmp_path / "annotated"
    result_dir.mkdir()
    result_path = result_dir / "run.webm"
    result_path.write_bytes(b"webm")
    monkeypatch.setattr(processing_jobs.config, "ANNOTATED_FOLDER", result_dir)
    completed = test_db.create_processing_run(video_id, json.dumps(["Obstruction"]))
    test_db.finish_processing_run(
        completed,
        progress={
            "annotated_video_ready": True,
            "annotated_video_path": str(result_path),
            "class_counts": {"car": 3},
            "detection_records": 3,
        },
    )

    idle = enforcer_client.get("/api/processing-jobs?recent_limit=999").get_json()
    assert idle["success"] is True
    assert idle["poll_after_ms"] == 10000
    assert idle["recent"][0]["annotated_video_url"].endswith(
        f"/api/videos/{video_id}/annotated/{completed}"
    )
    assert idle["recent"][0]["download_url"].endswith("?download=1")
    assert str(result_path) not in json.dumps(idle)

    active = test_db.create_processing_run(video_id, "[]")
    live = enforcer_client.get("/api/processing-jobs").get_json()
    assert live["poll_after_ms"] == 1500
    assert live["active"][0]["id"] == active
    assert len(live["recent"]) <= 100


def test_authenticated_base_renders_global_jobs_component(enforcer_client):
    """Catches the global controller being absent or loaded more than once."""
    response = enforcer_client.get("/")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert body.count('id="processingJobsToggle"') == 1
    assert body.count('id="processingJobsDrawer"') == 1
    assert body.count('js/processing_jobs.js') == 1


def test_live_monitor_renders_active_and_completed_jobs_workspace(enforcer_client):
    """Catches cross-video processing history disappearing with video selection."""
    response = enforcer_client.get("/live-monitor")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'id="processingJobsWorkspace"' in body
    assert 'id="liveActiveJobs"' in body
    assert 'id="liveCompletedJobs"' in body
    assert 'data-jobs-tab="active"' in body
    assert 'data-jobs-tab="completed"' in body


def test_processing_jobs_api_marks_stop_authorization(enforcer_client, client):
    """Catches the Live Monitor exposing a stop control without server role truth."""
    from database import db

    video_id = _video(db, "stop-role.mp4")
    db.create_processing_run(video_id, "[]")
    enforcer_payload = enforcer_client.get("/api/processing-jobs").get_json()
    assert enforcer_payload["active"][0]["can_stop"] is True

    client.get("/logout")
    import bcrypt
    db.create_user(
        "viewer_jobs",
        bcrypt.hashpw(b"viewer123", bcrypt.gensalt()).decode("utf-8"),
        role="viewer",
        full_name="Viewer",
    )
    client.post("/login", data={"username": "viewer_jobs", "password": "viewer123"})
    viewer_payload = client.get("/api/processing-jobs").get_json()
    assert viewer_payload["active"][0]["can_stop"] is False


def test_processing_jobs_snapshot_reconstructs_from_sqlite_after_memory_is_cleared(
    enforcer_client
):
    """Catches browser/server memory becoming the authority after navigation or reload."""
    import app as flask_app
    from database import db

    video_id = _video(db, "durable-history.mp4")
    run_id = db.create_processing_run(video_id, "[]")
    db.start_processing_run(run_id)
    flask_app._processing_jobs[video_id] = {"state": "processing", "run_id": run_id}
    assert enforcer_client.get("/api/processing-jobs").get_json()["active"][0]["id"] == run_id

    flask_app._processing_jobs.clear()
    db.finish_processing_run(
        run_id,
        progress={"detection_records": 0, "violation_candidates": 0},
    )
    refreshed = enforcer_client.get("/api/processing-jobs").get_json()
    assert refreshed["active"] == []
    assert refreshed["recent"][0]["id"] == run_id
    assert refreshed["recent"][0]["status"] == "completed"
    assert refreshed["recent"][0]["is_current_result"] is True
