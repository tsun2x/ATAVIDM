"""Rendered HTML contracts for the shared TAVIDM interface."""

from __future__ import annotations


def _login(client, username: str, password: str):
    response = client.post("/login", data={"username": username, "password": password})
    assert response.status_code in (302, 303)


def test_shared_shell_uses_truthful_status_and_named_search(client):
    """Catches a fabricated green readiness claim and placeholder-only search."""
    _login(client, "admin", "admin123")
    html = client.get("/").get_data(as_text=True)

    assert "Pipeline Ready" not in html
    assert 'class="status-dot neutral"' in html
    assert 'for="globalSearch"' in html
    assert 'aria-label="Search violation records"' in html
    assert 'id="globalSearchSubmit"' in html
    assert 'aria-expanded="false"' in html
    assert "CV Pipeline" in html
    assert ">Configured<" in html
    assert ">Ready<" not in html


def test_review_queue_exposes_filter_and_action_state(client):
    """Catches unnamed consequential controls and filters with hidden state."""
    _login(client, "admin", "admin123")
    from database import db

    video_id = db.insert_video(
        filename="review-accessibility.mp4",
        filepath="/tmp/review-accessibility.mp4",
        status="processed",
    )
    db.insert_review_queue(
        video_id=video_id,
        track_id=4,
        violation_type="Illegal Parking",
        confidence=0.87,
        frame_number=12,
        status="pending",
        vehicle_class="car",
    )
    html = client.get("/review-queue").get_data(as_text=True)

    assert 'role="group" aria-label="Filter review queue"' in html
    assert 'id="filterAll"' in html and 'aria-pressed="true"' in html
    assert 'id="filterLowConf"' in html and 'aria-pressed="false"' in html
    assert 'id="reviewFilterStatus"' in html and 'aria-live="polite"' in html
    assert 'aria-label="View evidence for' in html
    assert 'aria-label="Confirm violation' in html
    assert 'aria-label="Dismiss detection' in html
    assert 'data-confirm-message=' in html
    assert 'data-loading-label="Confirming…"' in html


def test_icon_actions_and_dialog_closers_have_names(client):
    """Catches icon-only actions and close buttons that have no accessible name."""
    _login(client, "admin", "admin123")
    from database import db

    video_id = db.insert_video(
        filename="violation-accessibility.mp4",
        filepath="/tmp/violation-accessibility.mp4",
        status="processed",
    )
    db.insert_violation(
        video_id=video_id,
        track_id=8,
        violation_type="Counterflow",
        confidence=0.91,
        frame_number=20,
        timestamp_sec=2.0,
    )
    db.create_zone_template(
        template_name="Accessible template",
        zones_json="{}",
    )
    violations = client.get("/violations").get_data(as_text=True)
    assert 'aria-label="Reset violation filters"' in violations
    assert 'aria-label="View details for' in violations
    assert 'aria-label="View evidence for' in violations
    assert violations.count('class="btn-close" data-bs-dismiss="modal" aria-label="Close"') >= 2

    settings = client.get("/settings").get_data(as_text=True)
    assert 'aria-label="Preview zone template' in settings
    assert 'aria-label="Edit zone template' in settings
    assert 'aria-label="Duplicate zone template' in settings
    assert 'aria-label="Delete zone template' in settings
    assert settings.count('class="btn-close" data-bs-dismiss="modal" aria-label="Close"') >= 4


def test_styles_define_reduced_motion_and_mobile_workspace_rules():
    """Catches motion and dense controls remaining unsafe at the declared viewports."""
    css = open("static/css/style.css", encoding="utf-8").read()

    assert "@media (prefers-reduced-motion: reduce)" in css
    assert ".live-indicator .pulse" in css
    assert "animation: none" in css
    assert "@media (max-width: 575.98px)" in css
    assert "@media (max-width: 768px)" in css
    assert "@media (max-width: 991.98px)" in css
    assert ".main-content { margin-left: 0; min-width: 0; width: 100%; }" in css
    assert ".top-navbar-actions { width: 100%; justify-content: flex-end; flex-wrap: wrap; }" in css
    assert ".search-box { display: flex !important; flex: 1 1 320px; min-width: 0; }" in css
    assert ".main-content { min-width: 0; width: 100%; }" in css
    assert ".top-navbar" in css
    assert ".live-jobs-list" in css
