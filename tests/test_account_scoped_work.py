"""Behavioral regression coverage for per-account TAVIDM work ownership."""

from __future__ import annotations


def _users(test_db):
    import bcrypt

    password_hash = bcrypt.hashpw(b"test-only", bcrypt.gensalt()).decode("utf-8")
    test_db.create_user("second_enforcer", password_hash, role="enforcer")
    return (
        test_db.get_user_by_username("enforcer")["id"],
        test_db.get_user_by_username("second_enforcer")["id"],
    )


def _video(test_db, name, owner):
    return test_db.insert_video(
        filename=name,
        filepath=f"/tmp/{name}",
        status="processed",
        uploaded_by=owner,
    )


def _seed_candidate(test_db, video_id, suffix):
    return test_db.upsert_motorcycle_detail_candidate(
        video_id=video_id,
        run_key=f"run-{suffix}",
        track_id=7,
        occurrence_index=1,
        occurrence_key=f"occ-{suffix}",
        selector_version="selector-test-v1",
        frame_number=10,
        timestamp_sec=1.0,
    )


def test_database_lists_scope_uploaded_and_video_derived_rows(auth_db):
    """Unscoped adapter reads currently expose other accounts' uploaded work."""
    owner_a, owner_b = _users(auth_db)
    video_a = _video(auth_db, "owner-a.mp4", owner_a)
    video_b = _video(auth_db, "owner-b.mp4", owner_b)
    video_unowned = _video(auth_db, "legacy-unowned.mp4", None)

    violation_a = auth_db.insert_violation(video_a, 1, "Illegal Parking", 0.8, 10, 1.0)
    violation_b = auth_db.insert_violation(video_b, 2, "Illegal Parking", 0.8, 11, 2.0)
    violation_unowned = auth_db.insert_violation(video_unowned, 3, "Illegal Parking", 0.8, 12, 3.0)
    violation_shared = auth_db.insert_violation(None, 4, "Illegal Parking", 0.8, 13, 4.0)

    review_a = auth_db.insert_review_queue(video_a, 1, "Illegal Parking", 0.7, 10)
    review_b = auth_db.insert_review_queue(video_b, 2, "Illegal Parking", 0.7, 11)
    review_unowned = auth_db.insert_review_queue(video_unowned, 3, "Illegal Parking", 0.7, 12)
    review_shared = auth_db.insert_review_queue(None, 4, "Illegal Parking", 0.7, 13)

    detail_a = _seed_candidate(auth_db, video_a, "a")
    _seed_candidate(auth_db, video_b, "b")
    detail_unowned = _seed_candidate(auth_db, video_unowned, "legacy")

    videos_a = auth_db.list_videos(owner_user_id=owner_a)
    videos_b = auth_db.list_videos(owner_user_id=owner_b)
    assert [row["id"] for row in videos_a] == [video_a]
    assert [row["id"] for row in videos_b] == [video_b]
    assert {row["id"] for row in auth_db.list_videos()} == {video_a, video_b, video_unowned}

    rows_a, total_a = auth_db.list_violations(owner_user_id=owner_a, page=1, per_page=20)
    assert {row["id"] for row in rows_a} == {violation_a, violation_shared}
    assert total_a == 2
    rows_a_page, total_a_page = auth_db.list_violations(
        owner_user_id=owner_a, page=1, per_page=1
    )
    assert len(rows_a_page) == 1
    assert total_a_page == total_a
    rows_b, total_b = auth_db.list_violations(owner_user_id=owner_b, page=1, per_page=20)
    assert {row["id"] for row in rows_b} == {violation_b, violation_shared}
    assert total_b == 2

    reviews_a, review_total_a = auth_db.list_review_queue(
        status="pending", owner_user_id=owner_a, page=1, per_page=20
    )
    assert {row["id"] for row in reviews_a} == {review_a, review_shared}
    assert review_total_a == 2
    details_a, detail_total_a = auth_db.list_motorcycle_detail_candidates(
        owner_user_id=owner_a, page=1, per_page=20
    )
    assert {row["id"] for row in details_a} == {detail_a}
    assert detail_total_a == 1

    all_rows, all_total = auth_db.list_violations(page=1, per_page=20)
    assert all_total == 4
    assert {row["id"] for row in all_rows} == {
        violation_a, violation_b, violation_unowned, violation_shared
    }
    assert auth_db.count_all_violations(owner_user_id=owner_a) == 2
    assert sum(row["count"] for row in auth_db.count_violations_by_type(owner_user_id=owner_a)) == 2
    assert auth_db.count_all_violations(uploader_id=owner_a) == 1
    upload_metrics = auth_db.upload_processing_analytics(
        "2000-01-01 00:00:00", "2100-01-01 00:00:00", uploader_id=owner_a
    )
    assert upload_metrics["unique_videos_uploaded"] == 1


def test_report_row_selection_forces_video_owner_scope(auth_db):
    """A report builder must not be able to include another account's rows."""
    from core import reports

    owner_a, owner_b = _users(auth_db)
    video_a = _video(auth_db, "report-owner-a.mp4", owner_a)
    video_b = _video(auth_db, "report-owner-b.mp4", owner_b)
    violation_a = auth_db.insert_violation(video_a, 1, "Illegal Parking", 0.8, 10, 1.0)
    auth_db.insert_violation(video_b, 2, "Illegal Parking", 0.8, 11, 2.0)
    auth_db.insert_violation(None, 3, "Illegal Parking", 0.8, 12, 3.0)

    rows = reports._fetch_rows({}, owner_user_id=owner_a)

    assert [row["id"] for row in rows] == [violation_a]


def _logged_in_client(username, password):
    import app as flask_app

    flask_app.app.config["TESTING"] = True
    flask_app.app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.app.test_client()
    response = client.post("/login", data={"username": username, "password": password})
    assert response.status_code in (302, 303)
    return client


def _set_test_admin_bootstrap(monkeypatch):
    import uuid

    monkeypatch.setenv(
        "TAVIDM_BOOTSTRAP_ADMIN_PASSWORD",
        "TestOnly-" + uuid.uuid4().hex + "-AccountScope",
    )
    return __import__("os").environ["TAVIDM_BOOTSTRAP_ADMIN_PASSWORD"]


def test_video_lists_and_direct_routes_enforce_uploader_scope(
    auth_db, tmp_path, monkeypatch
):
    """ID-based video routes currently let one enforcer read another's work."""
    import config

    _set_test_admin_bootstrap(monkeypatch)
    owner_a, owner_b = _users(auth_db)
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    monkeypatch.setattr(config, "UPLOAD_FOLDER", str(upload_root))
    video_a_path = upload_root / "owner-a.mp4"
    video_b_path = upload_root / "owner-b.mp4"
    video_a_path.write_bytes(b"video-a")
    video_b_path.write_bytes(b"video-b")
    video_a = auth_db.insert_video("owner-a.mp4", str(video_a_path), uploaded_by=owner_a)
    video_b = auth_db.insert_video("owner-b.mp4", str(video_b_path), uploaded_by=owner_b)
    import bcrypt
    admin_password = "scope-admin-pass"
    auth_db.create_user(
        "scope_admin", bcrypt.hashpw(admin_password.encode(), bcrypt.gensalt()).decode(),
        role="admin", full_name="Scope Admin",
    )

    client_a = _logged_in_client("enforcer", "enforcer123")
    client_b = _logged_in_client("second_enforcer", "test-only")
    admin = _logged_in_client("scope_admin", admin_password)

    assert [item["db_id"] for item in client_a.get("/api/videos").get_json()["videos"]] == [video_a]
    assert [item["db_id"] for item in client_b.get("/api/videos").get_json()["videos"]] == [video_b]
    assert {item["db_id"] for item in admin.get("/api/videos").get_json()["videos"]} == {
        video_a, video_b
    }
    assert [item["db_id"] for item in admin.get(f"/api/videos?uploader_id={owner_a}").get_json()["videos"]] == [video_a]
    assert client_a.get("/live-monitor").get_data(as_text=True).count("owner-a.mp4") >= 1
    filtered_admin_page = admin.get(f"/live-monitor?uploader_id={owner_a}").get_data(as_text=True)
    assert "owner-a.mp4" in filtered_admin_page
    assert "owner-b.mp4" not in filtered_admin_page
    assert client_a.get(f"/api/videos?uploader_id={owner_b}").status_code == 403

    for path, method, payload in (
        (f"/api/videos/{video_b}/frame-status", "get", None),
        (f"/api/videos/{video_b}/frame", "get", None),
        (f"/api/videos/{video_b}/annotation", "get", None),
        (f"/api/videos/{video_b}/annotation", "post", {"zones": {}}),
        (f"/api/videos/{video_b}/process", "post", {}),
        (f"/api/videos/{video_b}/process-cancel", "post", {"run_id": 1}),
        (f"/api/videos/{video_b}/process-status", "get", None),
        (f"/api/videos/{video_b}/process-preview", "get", None),
        (f"/api/videos/{video_b}/media", "get", None),
        (f"/api/videos/{video_b}/annotated/1", "get", None),
        (f"/api/videos/{video_b}/history", "get", None),
        (f"/api/videos/{video_b}/remove-results", "post", {}),
    ):
        response = client_a.open(path, method=method, json=payload)
        assert response.status_code == 404, (path, response.status_code, response.get_data(as_text=True))


def test_bulk_processing_rejects_other_account_video_before_enqueue(
    auth_db, monkeypatch
):
    """Bulk processing currently queues any known video ID supplied by an enforcer."""
    _set_test_admin_bootstrap(monkeypatch)
    import app as flask_app

    owner_a, owner_b = _users(auth_db)
    video_a = _video(auth_db, "bulk-a.mp4", owner_a)
    video_b = _video(auth_db, "bulk-b.mp4", owner_b)
    for video_id in (video_a, video_b):
        annotation_id = auth_db.upsert_annotation(video_id, "{}")
        auth_db.update_video(video_id, annotation_id=annotation_id, status="ready")
    calls = []

    def record_enqueue(video_id, **kwargs):
        calls.append(video_id)
        return "accepted", 100 + video_id, True, 1

    monkeypatch.setattr(flask_app, "_try_claim_enqueue", record_enqueue)
    client_a = _logged_in_client("enforcer", "enforcer123")
    response = client_a.post(
        "/api/videos/process-bulk", json={"video_ids": [video_a, video_b]}
    )

    assert response.status_code == 404
    assert calls == []


def test_processing_job_drawer_hides_other_account_jobs(auth_db, monkeypatch):
    """The shared processing-job endpoint currently exposes every account's runs."""
    _set_test_admin_bootstrap(monkeypatch)
    import app as flask_app

    owner_a, owner_b = _users(auth_db)
    video_a = _video(auth_db, "jobs-a.mp4", owner_a)
    video_b = _video(auth_db, "jobs-b.mp4", owner_b)

    monkeypatch.setattr(
        flask_app,
        "build_processing_jobs_snapshot",
        lambda **_kwargs: {
            "success": True,
            "server_time": "test",
            "poll_after_ms": 1000,
            "active": [{"video_id": video_a}, {"video_id": video_b}],
            "recent": [{"video_id": video_b}],
        },
    )
    client_a = _logged_in_client("enforcer", "enforcer123")
    response = client_a.get("/api/processing-jobs")

    assert [job["video_id"] for job in response.get_json()["active"]] == [video_a]
    assert response.get_json()["recent"] == []
    assert client_a.get(f"/api/processing-jobs?video_id={video_b}").status_code == 404


def test_result_and_review_lists_are_scoped_and_direct_mutations_are_blocked(
    auth_db, monkeypatch
):
    _set_test_admin_bootstrap(monkeypatch)
    owner_a, owner_b = _users(auth_db)
    video_a = _video(auth_db, "result-a.mp4", owner_a)
    video_b = _video(auth_db, "result-b.mp4", owner_b)
    violation_a = auth_db.insert_violation(video_a, 1, "Illegal Parking", 0.8, 10, 1.0)
    violation_b = auth_db.insert_violation(video_b, 2, "Illegal Parking", 0.8, 11, 2.0)
    review_a = auth_db.insert_review_queue(video_a, 1, "Illegal Parking", 0.7, 10)
    review_b = auth_db.insert_review_queue(video_b, 2, "Illegal Parking", 0.7, 11)
    candidate_a = _seed_candidate(auth_db, video_a, "route-a")
    candidate_b = _seed_candidate(auth_db, video_b, "route-b")

    client_a = _logged_in_client("enforcer", "enforcer123")
    violations = client_a.get("/api/violations").get_json()
    assert [row["id"] for row in violations["items"]] == [f"VIO-{violation_a:06d}"]
    queue = client_a.get("/api/review-queue?status=pending").get_json()
    assert [row["id"] for row in queue["items"]] == [review_a]
    details = client_a.get("/api/motorcycle-detail-review?outcome=all").get_json()
    assert [row["id"] for row in details["items"]] == [candidate_a]

    assert client_a.get(f"/api/cases/{violation_b}").status_code == 404
    assert client_a.get(f"/api/review-queue/{review_b}/evidence/scene").status_code == 404
    assert client_a.post(f"/api/review-queue/{review_b}/dismiss").status_code == 404
    response = client_a.post(
        f"/api/motorcycle-detail-review/{candidate_b}/outcome",
        json={"outcome": "reviewed"},
    )
    assert response.status_code == 404
    assert auth_db.get_motorcycle_detail_candidate(candidate_b)["human_outcome"] == "pending"


def test_reports_are_shared_but_enforcer_generation_scope_is_server_forced(
    auth_db, monkeypatch, tmp_path
):
    _set_test_admin_bootstrap(monkeypatch)
    import config
    monkeypatch.setattr(config, "REPORTS_FOLDER", tmp_path)
    owner_a, owner_b = _users(auth_db)
    video_a = _video(auth_db, "report-a.mp4", owner_a)
    video_b = _video(auth_db, "report-b.mp4", owner_b)
    auth_db.insert_violation(video_a, 1, "Illegal Parking", 0.8, 10, 1.0)
    auth_db.insert_violation(video_b, 2, "Illegal Parking", 0.8, 11, 2.0)
    from core import reports as reports_core

    seen = {}
    def fake_generate_report(**kwargs):
        seen.update(kwargs)
        return {
            "id": 12, "title": "Test report", "report_format": "pdf",
            "generated_by": kwargs["generated_by"], "generated_at": "test",
            "filters_json": "{}",
        }
    monkeypatch.setattr(reports_core, "generate_report", fake_generate_report)
    client_a = _logged_in_client("enforcer", "enforcer123")
    generated = client_a.post(
        "/api/reports",
        json={"format": "pdf", "video_id": video_b, "uploader_id": owner_b},
    )
    assert generated.status_code == 200
    assert seen["owner_user_id"] == owner_a
    assert seen["uploader_id"] is None
    assert seen["filters"]["video_id"] == video_b  # the forced owner scope still intersects client filters

    report_path = tmp_path / "shared.pdf"
    report_path.write_bytes(b"%PDF-test")
    report_id = auth_db.insert_report("Shared", "pdf", str(report_path), generated_by=owner_a)
    client_b = _logged_in_client("second_enforcer", "test-only")
    assert [row["id"] for row in client_b.get("/api/reports?visibility=all").get_json()["reports"]] == [report_id]
    assert client_b.get(f"/api/reports/{report_id}/download").status_code == 200
    assert [row["id"] for row in client_b.get("/api/reports?visibility=mine").get_json()["reports"]] == []
