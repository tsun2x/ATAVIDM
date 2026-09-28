"""Motorcycle detail review API: access control, no confirm route, UI page."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from core.motorcycle_detail import DETAIL_SELECTOR_VERSION


@pytest.fixture
def detail_env(tmp_path, monkeypatch):
    """Patched evidence root + Flask app bound to a temporary database."""
    import config

    root = tmp_path / "evidence"
    monkeypatch.setattr("core.evidence.EVIDENCE_FOLDER", str(root))
    monkeypatch.setattr(config, "EVIDENCE_FOLDER", str(root))
    monkeypatch.setattr(
        "core.motorcycle_detail_scan.resolve_detail_evidence_path",
        _resolve_with(config.EVIDENCE_FOLDER),
    )
    return root


def _resolve_with(evidence_root):
    base = Path(evidence_root).resolve()

    def resolve(stored_path):
        if not stored_path:
            return None
        candidate = Path(str(stored_path))
        if not candidate.is_absolute():
            import config as cfg

            candidate = Path(cfg.BASE_DIR) / candidate
        try:
            resolved = candidate.resolve()
        except OSError:
            return None
        if resolved == base or base not in resolved.parents:
            return None
        return resolved if resolved.is_file() else None

    return resolve


def _seed(test_db, video_id, *, run_key="run_9", occurrence_key="t7g1"):
    root = Path(__import__("config").EVIDENCE_FOLDER) / "detail" / run_key / occurrence_key
    root.mkdir(parents=True, exist_ok=True)
    scene = root / "scene_f1.jpg"
    crop = root / "crop_f1.jpg"
    assert cv2.imwrite(str(scene), np.full((480, 640, 3), 40, dtype=np.uint8))
    assert cv2.imwrite(str(crop), np.full((120, 90, 3), 80, dtype=np.uint8))
    frames = [
        {
            "index": 1,
            "frame_number": 10,
            "timestamp_sec": 1.0,
            "scene_path": str(scene),
            "crop_path": str(crop),
            "crop": {"x": 100, "y": 50, "w": 90, "h": 120},
            "scan_scale_x": 1.0,
            "scan_scale_y": 1.0,
            "detection_bbox": {"x": 110.0, "y": 80.0, "w": 40.0, "h": 60.0},
            "rider_bbox": None,
            "score": {"total": 0.66, "components": {"sharpness": 0.5}, "reasons": []},
            "size_bytes": 2048,
            "overlay_path": None,
        }
    ]
    return test_db.upsert_motorcycle_detail_candidate(
        video_id=video_id,
        run_key=run_key,
        track_id=7,
        occurrence_index=1,
        occurrence_key=occurrence_key,
        selector_version=DETAIL_SELECTOR_VERSION,
        processing_run_id=9,
        frame_number=10,
        timestamp_sec=1.0,
        frame_score=0.66,
        score_breakdown_json=json.dumps({"sharpness": 0.5}),
        frame_count=1,
        frames_json=json.dumps(frames),
        source_width=640,
        source_height=480,
    )


@pytest.fixture
def seeded(test_db, detail_env):
    video_id = test_db.insert_video("api.mp4", "/tmp/api.mp4", status="processed")
    return video_id, _seed(test_db, video_id)


def _logged_in_client(username, password):
    """Fresh Flask test client logged in through the real /login route."""
    from core import auth
    from database import db

    import app as flask_app

    flask_app.app.config["TESTING"] = True
    flask_app.app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.app.test_client()
    client.post("/login", data={"username": username, "password": password})
    return client


def _create_user(username, password, role):
    import bcrypt
    from database import db

    hash_pw = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    db.create_user(username, hash_pw, role=role)
    return _logged_in_client(username, password)


@pytest.fixture
def detail_enforcer(seeded):
    """Enforcer client created *after* the schema exists (no forced re-init)."""
    return _create_user("detail_enforcer", "enforcer-pass-1", "enforcer")


@pytest.fixture
def detail_viewer(seeded):
    """Viewer client used to prove the role gate on the detail routes."""
    return _create_user("detail_viewer", "viewer-pass-1", "viewer")


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------


class TestAccessControl:
    def test_page_requires_login(self, client, seeded):
        assert client.get("/motorcycle-detail-review").status_code in (302, 401)

    def test_page_requires_enforcer(self, detail_viewer, seeded):
        assert detail_viewer.get("/motorcycle-detail-review").status_code in (302, 403)

    def test_enforcer_sees_the_page_and_the_pending_candidate(self, detail_enforcer, seeded):
        response = detail_enforcer.get("/motorcycle-detail-review")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "Motorcycle Detail Review" in body
        assert "no confirm action" in body
        assert "t7g1" in body

    def test_list_api_requires_authentication(self, client, seeded):
        assert client.get("/api/motorcycle-detail-review").status_code in (302, 401)

    def test_list_api_returns_candidates_with_evidence_urls(self, detail_enforcer, seeded):
        payload = detail_enforcer.get("/api/motorcycle-detail-review").get_json()
        assert payload["success"] is True
        assert payload["total"] == 1
        item = payload["items"][0]
        assert item["occurrence_key"] == "t7g1"
        assert item["scan_state"] == "queued"
        assert item["can_confirm"] is False
        assert item["frames"][0]["scene_url"].startswith("/api/motorcycle-detail-review/")
        assert item["frames"][0]["crop_url"]

    def test_list_api_rejects_unknown_filters(self, detail_enforcer, seeded):
        assert detail_enforcer.get(
            "/api/motorcycle-detail-review?scan_state=bogus"
        ).status_code == 400
        assert detail_enforcer.get(
            "/api/motorcycle-detail-review?outcome=confirmed"
        ).status_code == 400

    def test_evidence_route_requires_enforcer(self, detail_viewer, seeded):
        _, candidate_id = seeded
        assert detail_viewer.get(
            f"/api/motorcycle-detail-review/{candidate_id}/evidence/scene?index=1"
        ).status_code in (302, 403)

    def test_enforcer_can_read_scene_and_crop_evidence(self, detail_enforcer, seeded):
        _, candidate_id = seeded
        for kind in ("scene", "crop"):
            response = detail_enforcer.get(
                f"/api/motorcycle-detail-review/{candidate_id}/evidence/{kind}?index=1"
            )
            assert response.status_code == 200
            assert response.data[:2] == b"\xff\xd8"  # JPEG magic

    def test_evidence_route_ignores_an_unknown_index(self, detail_enforcer, seeded):
        _, candidate_id = seeded
        response = detail_enforcer.get(
            f"/api/motorcycle-detail-review/{candidate_id}/evidence/scene?index=2"
        )
        assert response.status_code == 404

    def test_evidence_route_rejects_an_unknown_kind(self, detail_enforcer, seeded):
        _, candidate_id = seeded
        response = detail_enforcer.get(
            f"/api/motorcycle-detail-review/{candidate_id}/evidence/clip"
        )
        assert response.status_code == 404

    def test_evidence_outside_the_private_root_is_refused(self, detail_enforcer, test_db, detail_env):
        video_id = test_db.insert_video("leak.mp4", "/tmp/leak.mp4", status="processed")
        outside = detail_env.parent / "outside.jpg"
        assert cv2.imwrite(str(outside), np.full((10, 10, 3), 1, dtype=np.uint8))
        candidate_id = _seed(test_db, video_id, run_key="run_leak", occurrence_key="t1g1")
        row = test_db.get_motorcycle_detail_candidate(candidate_id)
        frames = json.loads(row["frames_json"])
        frames[0]["scene_path"] = str(outside)
        with test_db.db_session() as conn:
            conn.execute(
                "UPDATE motorcycle_detail_candidates SET frames_json = ? WHERE id = ?",
                (json.dumps(frames), candidate_id),
            )
        response = detail_enforcer.get(
            f"/api/motorcycle-detail-review/{candidate_id}/evidence/scene?index=1"
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Outcome recording and the deliberate absence of a confirm action
# ---------------------------------------------------------------------------


class TestOutcomeEndpoint:
    def test_outcome_requires_enforcer(self, detail_viewer, seeded):
        _, candidate_id = seeded
        response = detail_viewer.post(
            f"/api/motorcycle-detail-review/{candidate_id}/outcome",
            json={"outcome": "reviewed"},
        )
        assert response.status_code in (302, 403)

    @pytest.mark.parametrize("outcome", ["reviewed", "dismissed", "uncertain"])
    def test_allowed_outcomes_are_accepted(self, detail_enforcer, seeded, outcome):
        _, candidate_id = seeded
        response = detail_enforcer.post(
            f"/api/motorcycle-detail-review/{candidate_id}/outcome",
            json={"outcome": outcome, "notes": "checked crop"},
        )
        assert response.status_code == 200
        assert response.get_json()["outcome"] == outcome

    def test_confirm_outcome_is_refused(self, detail_enforcer, seeded, test_db):
        _, candidate_id = seeded
        response = detail_enforcer.post(
            f"/api/motorcycle-detail-review/{candidate_id}/outcome",
            json={"outcome": "confirmed"},
        )
        assert response.status_code == 400
        assert "must be reviewed" in response.get_json()["error"]
        assert test_db.get_motorcycle_detail_candidate(candidate_id)["human_outcome"] == "pending"

    def test_unknown_candidate_returns_404(self, detail_enforcer, seeded):
        response = detail_enforcer.post(
            "/api/motorcycle-detail-review/98765/outcome", json={"outcome": "reviewed"}
        )
        assert response.status_code == 404

    def test_outcome_creates_no_violation_or_review_row(self, detail_enforcer, seeded, test_db):
        _, candidate_id = seeded
        detail_enforcer.post(
            f"/api/motorcycle-detail-review/{candidate_id}/outcome",
            json={"outcome": "reviewed"},
        )
        with test_db.db_session() as conn:
            assert conn.execute("SELECT COUNT(*) AS n FROM violations").fetchone()["n"] == 0
            assert conn.execute("SELECT COUNT(*) AS n FROM review_queue").fetchone()["n"] == 0

    def test_no_confirm_route_exists_on_the_detail_queue(self, detail_enforcer, seeded):
        _, candidate_id = seeded
        for path in (
            f"/api/motorcycle-detail-review/{candidate_id}/confirm",
            f"/api/motorcycle-detail-review/{candidate_id}/case",
            f"/api/motorcycle-detail-review/{candidate_id}/violation",
        ):
            assert detail_enforcer.post(path, json={}).status_code == 404

    def test_existing_review_queue_confirm_api_is_unchanged(self, detail_enforcer, test_db):
        video_id = test_db.insert_video("legacy.mp4", "/tmp/legacy.mp4", status="processed")
        review_id = test_db.insert_review_queue(
            video_id=video_id,
            track_id=1,
            violation_type="Illegal Parking",
            confidence=0.96,
            frame_number=5,
            vehicle_class="car",
        )
        response = detail_enforcer.get("/api/review-queue?status=pending").get_json()
        assert any(item["id"] == review_id for item in response["items"])
        # The legacy confirm route still exists and is unchanged.
        assert any(
            rule.rule == "/api/review-queue/<int:review_id>/confirm"
            for rule in detail_enforcer.application.url_map.iter_rules()
            if rule.rule.startswith("/api/review-queue")
        )


class TestScanNowEndpoint:
    def test_scan_now_is_refused_while_the_gpu_slot_is_busy(self, detail_enforcer, seeded, monkeypatch):
        import app as flask_app

        monkeypatch.setattr(flask_app, "_detail_gpu_idle", lambda: False)
        response = detail_enforcer.post("/api/motorcycle-detail-review/scan-now")
        assert response.status_code == 409
        assert "GPU slot" in response.get_json()["error"]

    def test_scan_now_runs_one_bounded_batch(self, detail_enforcer, seeded, monkeypatch):
        import app as flask_app

        monkeypatch.setattr(flask_app, "_detail_gpu_idle", lambda: True)
        calls = []

        class _OneShot:
            def run_once(self):
                calls.append(1)
                return {"claimed": 1, "ready": 1}

        monkeypatch.setattr(
            flask_app, "MotorcycleDetailScanner", lambda **kwargs: _OneShot()
        )
        response = detail_enforcer.post("/api/motorcycle-detail-review/scan-now")
        assert response.status_code == 200
        assert response.get_json()["report"]["ready"] == 1
        assert len(calls) == 1
