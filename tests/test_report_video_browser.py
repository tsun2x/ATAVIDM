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
