"""Review Queue sidebar item and completed-video vehicle crossings by class.

Both are display changes: rendering pages or reading process status must not
create, confirm, or change violations, cases, or review-queue rows.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import app as app_module

REPO = Path(__file__).resolve().parent.parent
PROTECTED_TABLES = ("violations", "review_queue", "case_policy_records", "case_action_events")


@pytest.fixture(autouse=True)
def _bootstrap_password(monkeypatch):
    import os

    if len(os.environ.get("TAVIDM_BOOTSTRAP_ADMIN_PASSWORD", "").strip()) < 12:
        monkeypatch.setenv("TAVIDM_BOOTSTRAP_ADMIN_PASSWORD", "nav-crossing-test-only-pass")


def _login_as(client, role: str):
    import bcrypt
    from database import db

    username = f"nav_{role}"
    password = f"{role}-nav-pass-123"
    db.create_user(username, bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(), role=role)
    response = client.post("/login", data={"username": username, "password": password})
    assert response.status_code in (302, 303)
    return client


def _sidebar(html: str) -> str:
    match = re.search(r'<nav class="sidebar-nav">(.*?)</nav>', html, re.S)
    assert match, "sidebar navigation not rendered"
    return match.group(1)


def _sidebar_link(html: str, href: str) -> str | None:
    for anchor in re.findall(r"<a\b[^>]*>.*?</a>", _sidebar(html), re.S):
        if f'href="{href}"' in anchor:
            return anchor
    return None


def _pending(video_id: int, n: int, *, status: str = "pending"):
    from database import db

    for i in range(n):
        db.insert_review_queue(
            video_id=video_id, track_id=100 + i, violation_type="Illegal Parking",
            confidence=0.8, frame_number=10 + i, status=status, vehicle_class="car",
        )


def _protected_counts():
    from database import db

    with db.db_session() as conn:
        counts = {t: conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"] for t in PROTECTED_TABLES}
        outcomes = [tuple(r) for r in conn.execute("SELECT id, status FROM review_queue ORDER BY id").fetchall()]
    return counts, outcomes


def _video(name="nav.mp4", status="processed"):
    from database import db

    vid = db.insert_video(filename=name, filepath=f"/tmp/{name}", status=status)
    app_module._processing_jobs.pop(vid, None)
    return vid


# ---------------------------------------------------------------------------
# Sidebar navigation
# ---------------------------------------------------------------------------


class TestReviewQueueSidebar:
    @pytest.mark.parametrize("role", ["admin", "enforcer"])
    def test_admin_and_enforcer_get_the_item_right_after_violations(self, client, role):
        _login_as(client, role)
        html = client.get("/").get_data(as_text=True)
        sidebar = _sidebar(html)
        link = _sidebar_link(html, "/review-queue")
        assert link is not None
        assert ">Review Queue<" in link
        hrefs = re.findall(r'href="([^"]+)"', sidebar)
        assert hrefs[hrefs.index("/violations") + 1] == "/review-queue"

    def test_viewer_does_not_see_the_item_and_route_stays_protected(self, client):
        _login_as(client, "viewer")
        html = client.get("/").get_data(as_text=True)
        assert _sidebar_link(html, "/review-queue") is None
        assert 'href="/review-queue"' not in html
        response = client.get("/review-queue")
        assert response.status_code in (302, 303)
        assert "/review-queue" not in response.headers.get("Location", "")

    def test_route_decorator_is_unchanged(self):
        source = (REPO / "app.py").read_text(encoding="utf-8")
        assert '@app.route("/review-queue")\n@auth.role_required("enforcer")\ndef review_queue():' in source

    def test_badge_shows_ordinary_pending_count_with_accessible_name(self, client):
        vid = _video()
        _pending(vid, 3)
        _pending(vid, 2, status="confirmed")
        _pending(vid, 1, status="dismissed")
        _login_as(client, "enforcer")
        link = _sidebar_link(client.get("/").get_data(as_text=True), "/review-queue")
        assert '<span class="nav-badge nav-badge-count" aria-hidden="true">3</span>' in link
        assert 'aria-label="Review Queue, 3 pending review items"' in link

    def test_badge_is_hidden_at_zero(self, client):
        _login_as(client, "admin")
        link = _sidebar_link(client.get("/").get_data(as_text=True), "/review-queue")
        assert "nav-badge" not in link
        assert "aria-label=" not in link
        assert "pending review item" not in link

    @pytest.mark.parametrize("role", ["admin", "enforcer"])
    def test_badge_wording_is_singular_for_one_item_and_plural_otherwise(self, client, role):
        vid = _video(f"wording-{role}.mp4")
        _login_as(client, role)
        hidden = _sidebar_link(client.get("/").get_data(as_text=True), "/review-queue")
        assert "nav-badge" not in hidden
        assert "pending review" not in hidden
        _pending(vid, 1)
        one = _sidebar_link(client.get("/").get_data(as_text=True), "/review-queue")
        assert 'aria-label="Review Queue, 1 pending review item"' in one
        assert "pending review items" not in one
        assert '<span class="nav-badge nav-badge-count" aria-hidden="true">1</span>' in one
        _pending(vid, 2)
        many = _sidebar_link(client.get("/").get_data(as_text=True), "/review-queue")
        assert 'aria-label="Review Queue, 3 pending review items"' in many
        assert '<span class="nav-badge nav-badge-count" aria-hidden="true">3</span>' in many

    def test_motorcycle_detail_pending_never_counts_as_review_queue(self, client):
        from database import db

        vid = _video()
        for k in range(4):
            db.upsert_motorcycle_detail_candidate(
                video_id=vid, run_key="run_nav", track_id=k, occurrence_index=1,
                occurrence_key=f"t{k}g1", selector_version="nav-test", frames_json="[]",
            )
        _pending(vid, 1)
        assert db.count_motorcycle_detail_pending() == 4
        _login_as(client, "enforcer")
        html = client.get("/").get_data(as_text=True)
        review = _sidebar_link(html, "/review-queue")
        assert 'aria-label="Review Queue, 1 pending review item"' in review
        assert ">1</span>" in review
        detail = _sidebar_link(html, "/motorcycle-detail-review")
        assert ">4</span>" in detail
        assert "pending review items" not in detail

    def test_current_page_is_marked_for_assistive_technology(self, client):
        _login_as(client, "enforcer")
        on_queue = client.get("/review-queue").get_data(as_text=True)
        link = _sidebar_link(on_queue, "/review-queue")
        assert 'aria-current="page"' in link and "active" in link
        assert _sidebar(on_queue).count('aria-current="page"') == 1
        on_dashboard = client.get("/").get_data(as_text=True)
        assert 'aria-current="page"' not in _sidebar_link(on_dashboard, "/review-queue")
        assert 'aria-current="page"' in _sidebar_link(on_dashboard, "/")

    def test_existing_review_queue_links_are_preserved(self, client):
        vid = _video()
        _pending(vid, 2)
        _login_as(client, "admin")
        html = client.get("/").get_data(as_text=True)
        assert 'class="btn btn-icon position-relative" aria-label="Review Queue" title="Review Queue"' in html
        assert '<span class="notification-badge">2</span>' in html
        assert 'class="dropdown-item" href="/review-queue"' in html
        assert 'class="dropdown-item" href="/settings"' in html
        settings = client.get("/settings").get_data(as_text=True)
        assert "Open Review Queue" in settings

    def test_pending_count_is_queried_once_per_page_render(self, client, monkeypatch):
        _login_as(client, "enforcer")
        calls = []
        original = app_module.db.count_review_pending

        def counting():
            calls.append(1)
            return original()

        monkeypatch.setattr(app_module.db, "count_review_pending", counting)
        for path in ("/live-monitor", "/review-queue", "/motorcycle-detail-review", "/analytics"):
            calls.clear()
            assert client.get(path).status_code == 200
            assert len(calls) == 1, path
        # The dashboard's own statistics card (core.analytics) is the only other query.
        calls.clear()
        assert client.get("/").status_code == 200
        assert len(calls) == 2

    def test_opening_pages_writes_nothing_and_changes_no_outcome(self, client):
        vid = _video()
        _pending(vid, 2)
        _pending(vid, 1, status="confirmed")
        before = _protected_counts()
        _login_as(client, "enforcer")
        for path in (
            "/", "/review-queue", "/live-monitor", "/analytics",
            f"/api/videos/{vid}/process-status", f"/api/videos/{vid}/history",
        ):
            assert client.get(path).status_code == 200
        assert _protected_counts() == before


# ---------------------------------------------------------------------------
# Completed-video vehicle crossings by class
# ---------------------------------------------------------------------------


def _completed_run(vid, *, crossing_counts=None, vehicles_crossed=None, class_counts=None):
    from database import db

    run_id = db.create_processing_run(vid, "[]")
    progress = {
        "detection_records": 57,
        "unique_tracks": 9,
        "violation_candidates": 2,
        "class_counts": class_counts if class_counts is not None else {"car": 40, "person": 12, "rider": 5},
    }
    if crossing_counts is not None:
        progress["crossing_counts"] = crossing_counts
    if vehicles_crossed is not None:
        progress["vehicles_crossed"] = vehicles_crossed
    db.finish_processing_run(run_id, status="completed", progress=progress)
    return run_id


class TestCompletedCrossingStatus:
    def test_completed_result_exposes_crossing_counts_not_class_counts(self, client):
        vid = _video("cross.mp4")
        run_id = _completed_run(vid, crossing_counts={"car": 2, "motorcycle": 1}, vehicles_crossed=3)
        _login_as(client, "viewer")
        body = client.get(f"/api/videos/{vid}/process-status").get_json()
        assert body["run_id"] == body["current_result_run_id"] == run_id
        assert body["latest_attempt_status"] == "completed"
        assert body["vehicles_crossed"] == 3
        assert body["crossing_counts"] == {"car": 2, "motorcycle": 1}
        assert body["class_counts"] == {"car": 40, "person": 12, "rider": 5}

    def test_reload_shows_summary_section_and_reads_the_same_saved_result(self, client):
        vid = _video("reload.mp4")
        run_id = _completed_run(vid, crossing_counts={"jeepney": 4}, vehicles_crossed=4)
        _login_as(client, "enforcer")
        first = client.get(f"/api/videos/{vid}/process-status").get_json()
        page = client.get("/live-monitor").get_data(as_text=True)
        second = client.get(f"/api/videos/{vid}/process-status").get_json()
        assert first["crossing_counts"] == second["crossing_counts"] == {"jeepney": 4}
        assert second["current_result_run_id"] == run_id
        assert 'id="crossingSummaryPanel"' in page
        assert "Vehicle line crossings by class" in page
        assert "js/crossing_summary.js" in page
        classes = json.loads(re.search(r"window.TAVIDM_VEHICLE_CROSSING_CLASSES = (\[.*?\]);", page).group(1))
        assert classes == [
            "car", "van", "jeepney", "tricycle", "autorickshaw",
            "bus", "truck", "pickup_truck", "motorcycle", "bicycle",
        ]

    def test_failed_rerun_keeps_earlier_result_separate(self, client):
        from database import db

        vid = _video("rerun.mp4")
        first = _completed_run(vid, crossing_counts={"bus": 1}, vehicles_crossed=1)
        second = db.create_processing_run(vid, "[]")
        db.finish_processing_run(second, status="failed", error_message="boom")
        _login_as(client, "enforcer")
        body = client.get(f"/api/videos/{vid}/process-status").get_json()
        assert body["run_id"] == second
        assert body["current_result_run_id"] == first
        assert body["latest_attempt_status"] == "failed"
        assert body["state"] == "error"

    def test_legacy_row_without_crossing_data_is_null_not_zero(self, client):
        vid = _video("legacy.mp4")
        _completed_run(vid)
        _login_as(client, "viewer")
        body = client.get(f"/api/videos/{vid}/process-status").get_json()
        assert body["vehicles_crossed"] is None
        assert body["crossing_counts"] == {}

    def test_valid_zero_is_preserved(self, client):
        vid = _video("zero.mp4")
        _completed_run(vid, crossing_counts={}, vehicles_crossed=0)
        _login_as(client, "viewer")
        body = client.get(f"/api/videos/{vid}/process-status").get_json()
        assert body["vehicles_crossed"] == 0
        assert body["crossing_counts"] == {}

    @pytest.mark.parametrize("raw", ["not json", "[1, 2]", '{"car": "x", "person": 3}'])
    def test_malformed_saved_counts_do_not_break_status(self, client, raw):
        from database import db

        vid = _video("bad.mp4")
        run_id = _completed_run(vid, crossing_counts={"car": 1}, vehicles_crossed=1)
        with db.db_session() as conn:
            conn.execute("UPDATE processing_runs SET crossing_counts_json = ? WHERE id = ?", (raw, run_id))
        _login_as(client, "viewer")
        response = client.get(f"/api/videos/{vid}/process-status")
        assert response.status_code == 200
        body = response.get_json()
        assert body["vehicles_crossed"] == 1
        assert body["current_result_run_id"] == run_id

    @pytest.mark.parametrize("later", ["queued", "running", "failed", "cancelled"])
    def test_history_identifies_completed_crossings_after_a_later_attempt(self, client, later):
        from database import db

        vid = _video(f"later-{later}.mp4")
        first = _completed_run(
            vid,
            crossing_counts={"car": 2, "motorcycle": 1, "person": 9},
            vehicles_crossed=3,
        )
        second = db.create_processing_run(vid, "[]")
        if later == "running":
            with db.db_session() as conn:
                conn.execute(
                    "UPDATE processing_runs SET status = 'running', stage = 'processing' WHERE id = ?",
                    (second,),
                )
        elif later in ("failed", "cancelled"):
            db.finish_processing_run(
                second,
                status=later,
                error_message="later attempt",
                progress={"crossing_counts": {"bus": 50}, "vehicles_crossed": 50, "class_counts": {"bus": 900}},
            )
        before = _protected_counts()
        _login_as(client, "viewer")
        status = client.get(f"/api/videos/{vid}/process-status").get_json()
        history = client.get(f"/api/videos/{vid}/history").get_json()
        assert status["success"] is True and history["success"] is True
        assert status["run_id"] == second
        assert status["current_result_run_id"] == history["current_result_run_id"] == first
        assert status["latest_attempt_status"] == history["latest_attempt_status"] == later
        matched = [
            run for run in history["processing_runs"]
            if run["id"] == first and run["is_current_result"] is True and run["status"] == "completed"
        ]
        assert len(matched) == 1
        assert matched[0]["vehicles_crossed"] == 3
        assert json.loads(matched[0]["crossing_counts_json"]) == {"car": 2, "motorcycle": 1, "person": 9}
        later_run = next(run for run in history["processing_runs"] if run["id"] == second)
        assert later_run["is_current_result"] is False
        assert later_run["status"] == later
        if later in ("failed", "cancelled"):
            assert json.loads(later_run["crossing_counts_json"]) == {"bus": 50}
            assert later_run["vehicles_crossed"] == 50
        assert _protected_counts() == before

    def test_removed_completed_run_is_not_the_current_history_result(self, client):
        from database import db

        vid = _video("removed-result.mp4")
        run_id = _completed_run(vid, crossing_counts={"truck": 6}, vehicles_crossed=6)
        with db.db_session() as conn:
            conn.execute(
                "UPDATE processing_runs SET results_removed_at = ? WHERE id = ?",
                ("2026-01-01 00:00:00", run_id),
            )
        before = _protected_counts()
        _login_as(client, "enforcer")
        history = client.get(f"/api/videos/{vid}/history").get_json()
        status = client.get(f"/api/videos/{vid}/process-status").get_json()
        assert history["current_result_run_id"] is None
        assert status["current_result_run_id"] is None
        assert all(run["is_current_result"] is False for run in history["processing_runs"])
        saved = next(run for run in history["processing_runs"] if run["id"] == run_id)
        assert saved["results_removed_at"] == "2026-01-01 00:00:00"
        assert json.loads(saved["crossing_counts_json"]) == {"truck": 6}
        assert _protected_counts() == before

    def test_history_keeps_malformed_crossing_json_for_the_client_to_reject(self, client):
        from database import db

        vid = _video("bad-history.mp4")
        run_id = _completed_run(vid, crossing_counts={"car": 1}, vehicles_crossed=1)
        with db.db_session() as conn:
            conn.execute(
                "UPDATE processing_runs SET crossing_counts_json = ? WHERE id = ?",
                ("not json", run_id),
            )
        _login_as(client, "viewer")
        history = client.get(f"/api/videos/{vid}/history").get_json()
        matched = next(run for run in history["processing_runs"] if run["is_current_result"])
        assert matched["id"] == run_id
        assert matched["crossing_counts_json"] == "not json"
        assert matched["vehicles_crossed"] == 1


class TestCrossingSummaryReloadPath:
    def test_page_reads_history_when_the_latest_attempt_is_not_the_result(self):
        js = (REPO / "static" / "js" / "live_monitor.js").read_text(encoding="utf-8")
        helper = (REPO / "static" / "js" / "crossing_summary.js").read_text(encoding="utf-8")
        wiring = js[js.index("CrossingSummary.createCrossingLoader"):js.index("function loadCompletedSummary")]
        assert '"/history"' in wiring
        assert "crossingLoader.showStatus(videoId, token, payload)" in wiring
        load = js[js.index("function loadCompletedSummary"):js.index("function formatSec")]
        assert "showCrossingForStatus(videoId, token, payload)" in load
        poll = js[js.index("function pollProcessing"):js.index("const processConfirmModal")]
        assert "showCrossingForStatus(videoId, token, payload)" in poll
        assert "CrossingSummary.attemptCompleted(payload)" in poll
        loader = helper[helper.index("function createCrossingLoader"):helper.index("function laterAttemptText")]
        assert "inflight" in loader
        assert "flight.epoch !== epoch" in loader
        assert "inflight.currentId === currentId" in loader
        assert "opts.guard.isCurrent(flight.token, flight.videoId)" in loader
        assert "resultFromHistory" in helper
        assert "crossing_counts_json" in helper
        assert "class_counts" not in helper[helper.index("function resultFromHistory"):helper.index("function parseSavedCrossingCounts")]


# ---------------------------------------------------------------------------
# Labels: frame-level records, diagnostic track IDs, and the Analytics chart
# ---------------------------------------------------------------------------


class TestMetricLabels:
    def test_live_monitor_labels_keep_metrics_distinct(self):
        html = (REPO / "templates" / "live_monitor.html").read_text(encoding="utf-8")
        js = (REPO / "static" / "js" / "live_monitor.js").read_text(encoding="utf-8")
        helper = (REPO / "static" / "js" / "crossing_summary.js").read_text(encoding="utf-8")
        assert "frame-level detection records" in html
        assert "Frame-level detection records" in js
        assert "ByteTrack IDs observed (diagnostic)" in js
        assert "not confirmed violations" in js
        for text in (html, js, helper):
            assert "unique vehicles" not in text.lower()

    def test_completion_summary_escapes_api_text(self):
        js = (REPO / "static" / "js" / "live_monitor.js").read_text(encoding="utf-8")
        body = js[js.index("function showCompletionSummary"):js.index("function pollProcessing")]
        assert "esc(k)" in body
        assert "esc(payload.model_identifier" in body
        assert "TavidmCrossingSummary" in js

    def test_live_monitor_guards_stale_responses_and_completion(self):
        js = (REPO / "static" / "js" / "live_monitor.js").read_text(encoding="utf-8")
        poll = js[js.index("function pollProcessing"):js.index("const processConfirmModal")]
        assert "selectionGuard.isCurrent(token, videoId)" in poll
        assert 'payload.video_status === "processed"' not in poll
        assert "CrossingSummary.attemptCompleted(payload)" in poll
        select = js[js.index("function selectVideo"):js.index("videoList?.addEventListener")]
        assert "loadCompletedSummary(video.db_id)" in select

    def test_analytics_chart_names_its_violation_population(self):
        html = (REPO / "templates" / "analytics.html").read_text(encoding="utf-8")
        assert "By Vehicle Classification</h5>" not in html
        assert "Violation records by vehicle class" in html
        assert "not a traffic count" in html
        source = (REPO / "core" / "analytics.py").read_text(encoding="utf-8")
        assert "db.violations_by_vehicle_class()" in source

    def test_analytics_page_renders_new_label(self, client):
        _login_as(client, "viewer")
        html = client.get("/analytics").get_data(as_text=True)
        assert "Violation records by vehicle class" in html
        assert "Non-dismissed violation records" in html
