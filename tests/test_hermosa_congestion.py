"""Tests for the Hermosa Connect congestion/hotspot expansion.

Reuses the existing TAVIDM test fixtures (auth_db). Verifies:
- new tables exist after init_db
- DEMO analysis persists observations, events, and hotspots
- single stopped vehicle is NOT classified as congestion (persistence rule)
- routes render for an authenticated admin
"""

import os
import importlib

import pytest


@pytest.fixture
def hermes_db(auth_db):
    """DB with Hermosa tables + a couple of cameras."""
    auth_db.create_camera("CAM-A", "rtsp://demo/A", location="Junction A")
    auth_db.create_camera("CAM-B", "rtsp://demo/B", location="Junction B")
    return auth_db


def test_hermosa_tables_exist(hermes_db):
    with hermes_db.db_session() as conn:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "congestion_observations" in tables
    assert "congestion_events" in tables
    assert "hotspots" in tables


def test_single_stopped_vehicle_not_congestion(hermes_db):
    """A single low-movement observation must NOT open a congestion event."""
    import core.congestion as congestion_core

    from datetime import datetime, timedelta

    obs = [
        congestion_core.Observation(
            camera_id=1,
            location="X",
            obs_time=datetime(2026, 8, 18, 8, 0),
            vehicle_count=5,
            moving_count=1,
            stationary_count=4,
            density_score=0.9,  # high, but only ONE window
        )
    ]
    events = congestion_core.detect_events(obs)
    assert events == [], "single observation must not be a congestion event"


def test_persistent_congestion_detected(hermes_db):
    import core.congestion as congestion_core

    from datetime import datetime

    base = datetime(2026, 8, 18, 8, 0)
    obs = [
        congestion_core.Observation(
            camera_id=1, location="Junction A", obs_time=base,
            vehicle_count=120, moving_count=20, stationary_count=100,
            density_score=0.85,
        ),
        congestion_core.Observation(
            camera_id=1, location="Junction A",
            obs_time=__import__("datetime").timedelta(seconds=300) and base,
            vehicle_count=125, moving_count=15, stationary_count=110,
            density_score=0.88,
        ),
    ]
    events = congestion_core.detect_events(obs)
    assert len(events) == 1
    assert events[0].severity in ("moderate", "heavy", "severe")


def test_demo_analysis_persists(hermes_db):
    import core.congestion as congestion_core

    cameras = hermes_db.list_cameras()
    summary = congestion_core.persist_demo_analysis(cameras, steps=24, seed=1)
    assert summary["observations"] > 0
    assert summary["events"] >= 0
    assert summary["hotspots"] >= 0
    # verify persisted
    assert len(hermes_db.list_hotspots()) == summary["hotspots"]


def test_congestion_routes_render(auth_db, client):
    """New Hermosa pages render for an authenticated admin."""
    # auth_db fixture already created admin/admin123
    resp = client.post(
        "/login", data={"username": "admin", "password": "admin123"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    for endpoint in ("/traffic-map", "/congestion", "/hotspots", "/traffic-history"):
        r = client.get(endpoint)
        assert r.status_code == 200, f"{endpoint} failed: {r.status_code}"


@pytest.fixture
def client(hermes_db):
    # Build a Flask test client bound to the test DB.
    os.environ["SQLITE_PATH"] = os.environ.get("SQLITE_PATH", "")
    import app as flask_app_module

    flask_app_module.app.config["TESTING"] = True
    flask_app_module.app.config["WTF_CSRF_ENABLED"] = False
    with flask_app_module.app.test_client() as c:
        yield c
