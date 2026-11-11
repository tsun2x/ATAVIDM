"""Live annotation snapshots and per-camera scene persistence."""

import json

from tests.test_scene_annotation_gate2 import _v2_doc


def test_live_snapshot_requires_login_and_a_processed_frame(client, test_db, monkeypatch):
    import app as application
    camera_id = test_db.create_camera("test", "rtsp://example/stream")
    url = f"/api/cameras/{camera_id}/annotation-frame"
    assert client.get(url).status_code == 401
    monkeypatch.setattr(application.auth, "current_user", lambda: {"id": 1, "role": "admin"})
    monkeypatch.setattr(application.stream_manager, "get", lambda _: None)
    assert client.get(url).status_code == 409
    class Worker:
        def latest_raw_jpeg(self):
            return b"raw-frame"
    monkeypatch.setattr(application.stream_manager, "get", lambda _: Worker())
    response = client.get(url)
    assert response.data == b"raw-frame"
    assert response.mimetype == "image/jpeg"
    assert response.headers["Cache-Control"] == "no-store"
    assert client.get("/api/cameras/999999/annotation-frame").status_code == 404


def test_camera_annotation_save_preserves_scene_and_restarts_worker(client, test_db, monkeypatch):
    import app as application
    camera_id = test_db.create_camera("test", "rtsp://example/stream")
    stopped = []
    monkeypatch.setattr(application.stream_manager, "stop", stopped.append)
    monkeypatch.setattr(application.auth, "current_user", lambda: {"id": 1, "role": "enforcer"})
    url = f"/api/cameras/{camera_id}"
    assert client.put(url, json={"zones_json": _v2_doc()}).status_code == 403
    monkeypatch.setattr(application.auth, "current_user", lambda: {"id": 1, "role": "admin"})
    response = client.put(url, json={"zones_json": _v2_doc()})
    assert response.status_code == 200
    assert json.loads(test_db.get_camera(camera_id)["zones_json"]) == _v2_doc()
    assert stopped == [camera_id]


def test_camera_annotation_save_rejects_a_newer_server_scene(client, test_db, monkeypatch):
    import app as application

    camera_id = test_db.create_camera("test", "rtsp://example/stream")
    current = _v2_doc()
    test_db.update_camera(camera_id, zones_json=application.dumps_zones(current))
    stopped = []
    monkeypatch.setattr(application.stream_manager, "stop", stopped.append)
    monkeypatch.setattr(application.auth, "current_user", lambda: {"id": 1, "role": "admin"})

    response = client.put(
        f"/api/cameras/{camera_id}",
        json={"expected_zones_json": {}, "zones_json": {"zones": {"no_parking": [[1, 2], [3, 4], [5, 6]]}}},
    )

    assert response.status_code == 409
    assert "changed since this editor opened" in response.get_json()["error"]
    assert json.loads(test_db.get_camera(camera_id)["zones_json"]) == current
    assert stopped == []
