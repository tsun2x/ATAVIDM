import json

from core import support_diagnostics


def test_support_export_is_admin_only_and_excludes_private_data(client, monkeypatch):
    import app as application

    route = "/api/support/diagnostics"
    assert client.get(route).status_code == 401
    monkeypatch.setattr(application.auth, "current_user", lambda: {"id": 1, "role": "enforcer"})
    assert client.get(route).status_code == 403
    monkeypatch.setattr(application.auth, "current_user", lambda: {"id": 1, "role": "admin", "username": "test", "full_name": "Test"})
    settings = client.get("/settings")
    assert settings.status_code == 200
    assert b"Download diagnostics" in settings.data
    monkeypatch.setattr(application.db, "support_processing_summary", lambda: [{
        "id": 7, "video_id": 4, "status": "completed", "stage": "password-secret",
        "class_counts_json": json.dumps({"truck": 2, "rtsp://password-secret": 1}),
        "diagnostics_json": json.dumps({"model_classes": ["person", "motorcycle", "password-secret"],
                                        "notes": ["password-secret"]}),
        "has_error": 1, "motorcycle_detail_count": 0, "pickup_detail_count": 0,
    }])
    support_diagnostics.record_request("api_process_video", 200)
    support_diagnostics.record_request("api_confirm_review", 409, {"review_id": 23})
    support_diagnostics.record_request("rtsp://password-secret", 500)
    response = client.get(route)
    assert response.status_code == 200
    assert "attachment" in response.headers["Content-Disposition"]
    assert response.headers["Cache-Control"] == "no-store"
    assert b"password-secret" not in response.data
    data = response.get_json()
    assert data["recent_processing_runs"][0]["class_counts"] == {"truck": 2}
    assert data["recent_processing_runs"][0]["model_classes"] == ["person", "motorcycle"]
    assert any(event["action"] == "api_process_video" for event in data["recent_activity"])
    confirm = next(
        event for event in data["recent_activity"]
        if event["action"] == "api_confirm_review" and event.get("review_id") == 23
    )
    assert confirm["http_status"] == 409
    assert confirm["review_id"] == 23
    assert len(response.data) < 200_000


def test_summary_reads_recent_run_counts(test_db):
    video_id = test_db.insert_video(filename="private.mp4", filepath="private-path", status="ready")
    run_id = test_db.create_processing_run(video_id, "[]")
    rows = test_db.support_processing_summary()
    assert rows[0]["id"] == run_id
    assert rows[0]["video_id"] == video_id
    assert rows[0]["motorcycle_detail_count"] == 0
    assert rows[0]["pickup_detail_count"] == 0
    assert "private" not in json.dumps(rows)


def test_activity_is_bounded():
    for _ in range(600):
        support_diagnostics.record_request("api_process_video", 200)
    assert len(support_diagnostics.recent_activity()) == 500
