"""Pytest configuration and fixtures for TAVIDM test suite."""

from __future__ import annotations

import os
import json
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Generator

import pytest


@pytest.fixture
def test_db_path() -> Generator[str, None, None]:
    """Create a temporary SQLite database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_tavidm.db")
        os.environ["SQLITE_PATH"] = db_path
        os.environ["DATABASE_URL"] = db_path
        yield db_path


@pytest.fixture
def test_db(test_db_path: str) -> Any:
    """Initialize a test database with schema."""
    # Import after path is set
    from database import sqlite_adapter

    sqlite_adapter.init_db(force=True)
    return sqlite_adapter


@pytest.fixture
def auth_db(test_db: Any) -> Any:
    """Database with test users (admin, enforcer, viewer)."""
    import bcrypt

    # Create test users
    admin_hash = bcrypt.hashpw(b"admin123", bcrypt.gensalt()).decode("utf-8")
    enforcer_hash = bcrypt.hashpw(b"enforcer123", bcrypt.gensalt()).decode("utf-8")
    viewer_hash = bcrypt.hashpw(b"viewer123", bcrypt.gensalt()).decode("utf-8")

    test_db.create_user("admin", admin_hash, role="admin", full_name="Test Admin")
    test_db.create_user("enforcer", enforcer_hash, role="enforcer", full_name="Test Enforcer")
    test_db.create_user("viewer", viewer_hash, role="viewer", full_name="Test Viewer")

    return test_db


@pytest.fixture
def sample_video(auth_db: Any) -> dict[str, Any]:
    """Create a sample video entry with annotation."""
    video_id = auth_db.insert_video(
        filename="test_video.mp4",
        filepath="/tmp/test_video.mp4",
        duration_sec=60.0,
        recorded_at="2024-01-15 08:30:00",
        condition="peak",
        status="ready",
    )
    # Add minimal annotation with zones
    zones_json = {
        "no_parking": [[100, 100], [200, 100], [200, 200], [100, 200]],
        "active_lane": [[300, 100], [400, 100], [400, 200], [300, 200]],
    }
    from database import db

    db.upsert_annotation(video_id, json.dumps(zones_json))
    return db.get_video(video_id)


@pytest.fixture
def sample_zones() -> dict[str, list[list[float]]]:
    """Sample zone polygons for testing."""
    return {
        "no_parking": [[100, 100], [200, 100], [200, 200], [100, 200]],
        "active_lane": [[300, 100], [400, 100], [400, 200], [300, 200]],
        "pedestrian_crossing": [[500, 300], [600, 300], [600, 400], [500, 400]],
        "truck_ban_zone": [[100, 400], [200, 400], [200, 500], [100, 500]],
        "no_loading": [[300, 400], [400, 400], [400, 500], [300, 500]],
        "restricted_lane": [[500, 100], [600, 100], [600, 200], [500, 200]],
    }


@pytest.fixture
def sample_detection() -> dict[str, Any]:
    """Baseline detection dict with all required fields."""
    return {
        "track_id": 1,
        "class_label": "car",
        "confidence": 0.85,
        "bbox_x": 100.0,
        "bbox_y": 150.0,
        "bbox_w": 50.0,
        "bbox_h": 30.0,
        "timestamp_sec": 5.0,
    }


@pytest.fixture
def sample_large_detection() -> dict[str, Any]:
    """Large detection for zone boundary testing."""
    return {
        "track_id": 2,
        "class_label": "truck",
        "confidence": 0.92,
        "bbox_x": 150.0,
        "bbox_y": 450.0,
        "bbox_w": 80.0,
        "bbox_h": 40.0,
        "timestamp_sec": 10.0,
    }