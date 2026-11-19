"""Behavioral coverage for the per-video Reports browser."""

from __future__ import annotations

import uuid

import pytest


@pytest.fixture(autouse=True)
def _test_admin_bootstrap(monkeypatch):
    monkeypatch.setenv("TAVIDM_BOOTSTRAP_ADMIN_PASSWORD", "ReportTest-" + uuid.uuid4().hex)


def _login(client, username, password):
    response = client.post("/login", data={"username": username, "password": password})
    assert response.status_code in (302, 303)


def _video(db, filename, owner_id, *, status="processed"):
    return db.insert_video(
        filename=filename,
        filepath=f"/tmp/{filename}",
        status=status,
        uploaded_by=owner_id,
    )


def test_preview_paginates_cases_but_returns_complete_type_totals(auth_db):
    """A preview must not calculate totals from only its visible page."""
    import app as flask_app

    owner = auth_db.get_user_by_username("enforcer")["id"]
    video_id = _video(auth_db, "same-name.mp4", owner)
    auth_db.insert_violation(video_id, 1, "Illegal Parking", 0.8, 10, 1.0)
    auth_db.insert_violation(video_id, 2, "Illegal Parking", 0.7, 20, 2.0)
    auth_db.insert_violation(video_id, 3, "Illegal U-turn", 0.9, 30, 3.0)

    client = flask_app.app.test_client()
    _login(client, "enforcer", "enforcer123")
    response = client.get(f"/api/reports/preview?video_id={video_id}&page=1&per_page=2")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["total_cases"] == 3
    assert payload["pagination"] == {"page": 1, "per_page": 2, "pages": 2}
    assert len(payload["items"]) == 2
    assert payload["category_counts"] == [
        {"violation_type": "Illegal Parking", "count": 2},
        {"violation_type": "Illegal U-turn", "count": 1},
    ]
    assert payload["video"]["id"] == video_id
    assert payload["result_state"] == "legacy_results_available"


def test_video_groups_automatically_list_case_sources_with_owner_scope(auth_db):
    import app as flask_app

    owner_a = auth_db.get_user_by_username("enforcer")["id"]
    auth_db.create_user("second_enforcer", "not-used", role="enforcer")
    owner_b = auth_db.get_user_by_username("second_enforcer")["id"]
    own_video = _video(auth_db, "my-traffic.mp4", owner_a)
    other_video = _video(auth_db, "other-traffic.mp4", owner_b)
    no_case_video = _video(auth_db, "no-cases.mp4", owner_a)
    auth_db.insert_violation(own_video, 1, "Illegal Parking", 0.8, 10, 1.0)
    auth_db.insert_violation(own_video, 2, "Obstruction", 0.7, 20, 2.0)
    auth_db.insert_violation(other_video, 3, "Illegal Parking", 0.9, 30, 3.0)
    auth_db.insert_violation(None, 4, "Obstruction", 0.8, 40, 4.0, reason_log="[camera:school-gate]")

    client = flask_app.app.test_client()
    _login(client, "enforcer", "enforcer123")
    response = client.get("/api/reports/video-groups")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["total"] == 2
    live_group = next(group for group in payload["groups"] if group.get("source_type") == "live")
    assert live_group["video_id"] is None
    assert live_group["filename"] == "Live CCTV"
    assert live_group["case_count"] == 1
    group = next(group for group in payload["groups"] if group.get("source_type") == "video")
    assert group["video_id"] == own_video
    assert group["filename"] == "my-traffic.mp4"
    assert group["case_count"] == 2
    assert group["latest_case_at"]
    assert set(group["violation_types"]) == {"Illegal Parking", "Obstruction"}
    assert other_video != own_video and no_case_video != own_video


def test_enforcer_can_export_shared_live_cctv_cases_without_uploads(auth_db):
    from core import reports

    owner = auth_db.get_user_by_username("enforcer")["id"]
    own_video = _video(auth_db, "my-report.mp4", owner)
    auth_db.insert_violation(own_video, 1, "Illegal Parking", 0.8, 10, 1.0)
    auth_db.insert_violation(None, 2, "Obstruction", 0.8, 20, 2.0, reason_log="[camera:school-gate]")

    rows = reports._fetch_rows({"source_type": "live"}, owner_user_id=owner)

    assert {row["violation_type"] for row in rows} == {"Obstruction"}


def test_live_cctv_preview_returns_only_source_less_cases_for_owner(auth_db):
    import app as flask_app

    owner = auth_db.get_user_by_username("enforcer")["id"]
    own_video = _video(auth_db, "my-preview.mp4", owner)
    auth_db.insert_violation(own_video, 1, "Illegal Parking", 0.8, 10, 1.0)
    auth_db.insert_violation(None, 2, "Obstruction", 0.8, 20, 2.0, reason_log="[camera:school-gate]")

    client = flask_app.app.test_client()
    _login(client, "enforcer", "enforcer123")
    response = client.get("/api/reports/preview?source_type=live")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["video"]["filename"] == "Live CCTV"
    assert payload["total_cases"] == 1
    assert payload["items"][0]["violation_type"] == "Obstruction"


def test_preview_hides_another_enforcers_video_and_rejects_account_override(auth_db):
    """Preview scope must come from the signed-in account, not query arguments."""
    import app as flask_app

    owner_a = auth_db.get_user_by_username("enforcer")["id"]
    auth_db.create_user("second_enforcer", "not-used", role="enforcer")
    owner_b = auth_db.get_user_by_username("second_enforcer")["id"]
    video_b = _video(auth_db, "private.mp4", owner_b)

    client = flask_app.app.test_client()
    _login(client, "enforcer", "enforcer123")
    assert client.get(f"/api/reports/preview?video_id={video_b}").status_code == 404
    assert client.get(
        f"/api/reports/preview?video_id={video_b}&uploader_id={owner_b}"
    ).status_code == 403
    assert owner_a != owner_b


def test_preview_distinguishes_completed_empty_from_unprocessed(auth_db):
    """An empty page is not evidence that an unprocessed video completed cleanly."""
    import app as flask_app

    owner = auth_db.get_user_by_username("enforcer")["id"]
    completed_id = _video(auth_db, "finished.mp4", owner)
    run_id = auth_db.create_processing_run(completed_id, "[]")
    auth_db.start_processing_run(run_id)
    auth_db.finish_processing_run(run_id, status="completed")
    pending_id = _video(auth_db, "not-started.mp4", owner, status="uploaded")

    client = flask_app.app.test_client()
    _login(client, "enforcer", "enforcer123")
    completed = client.get(f"/api/reports/preview?video_id={completed_id}").get_json()
    pending = client.get(f"/api/reports/preview?video_id={pending_id}").get_json()

    assert completed["total_cases"] == 0
    assert completed["result_state"] == "completed_empty"
    assert pending["total_cases"] == 0
    assert pending["result_state"] == "not_processed"


def test_report_export_rejects_over_limit_before_creating_artifacts(
    auth_db, monkeypatch, tmp_path
):
    """An over-limit export must fail before creating a file or history row."""
    import app as flask_app
    import config
    from core import reports

    owner = auth_db.get_user_by_username("enforcer")["id"]
    video_id = _video(auth_db, "large.mp4", owner)
    auth_db.insert_violation(video_id, 1, "Illegal Parking", 0.8, 10, 1.0)
    monkeypatch.setattr(config, "REPORTS_FOLDER", tmp_path)
    real_snapshot = auth_db.report_export_snapshot

    def over_limit(*args, **kwargs):
        result = real_snapshot(*args, **kwargs)
        return {"rows": result["rows"], "total": reports._MAX_ROWS + 1}

    monkeypatch.setattr(reports.db, "report_export_snapshot", over_limit)
    client = flask_app.app.test_client()
    _login(client, "enforcer", "enforcer123")
    response = client.post("/api/reports", json={"format": "pdf"})

    assert response.status_code == 413
    assert response.get_json()["total_cases"] == reports._MAX_ROWS + 1
    assert list(tmp_path.iterdir()) == []
    assert auth_db.list_reports() == []
