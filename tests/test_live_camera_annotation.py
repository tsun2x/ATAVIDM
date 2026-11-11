"""Live annotation snapshots and per-camera scene persistence."""

import json
import threading

from tests.test_scene_annotation_gate2 import _v2_doc
from core.zone_config import dumps_zones


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
    restarted = []
    monkeypatch.setattr(application.stream_manager, "restart", restarted.append)
    monkeypatch.setattr(application.auth, "current_user", lambda: {"id": 1, "role": "enforcer"})
    url = f"/api/cameras/{camera_id}"
    assert client.put(url, json={"zones_json": _v2_doc()}).status_code == 403
    assert restarted == []
    monkeypatch.setattr(application.auth, "current_user", lambda: {"id": 1, "role": "admin"})
    response = client.put(url, json={"zones_json": _v2_doc()})
    assert response.status_code == 200
    assert json.loads(test_db.get_camera(camera_id)["zones_json"]) == _v2_doc()
    assert restarted == [camera_id]


def test_camera_annotation_save_rejects_a_newer_server_scene(client, test_db, monkeypatch):
    import app as application

    camera_id = test_db.create_camera("test", "rtsp://example/stream")
    current = _v2_doc()
    test_db.update_camera(camera_id, zones_json=application.dumps_zones(current))
    restarted = []
    monkeypatch.setattr(application.stream_manager, "restart", restarted.append)
    monkeypatch.setattr(application.auth, "current_user", lambda: {"id": 1, "role": "admin"})

    response = client.put(
        f"/api/cameras/{camera_id}",
        json={"expected_zones_json": {}, "zones_json": {"zones": {"no_parking": [[1, 2], [3, 4], [5, 6]]}}},
    )

    assert response.status_code == 409
    assert "changed since this editor opened" in response.get_json()["error"]
    assert json.loads(test_db.get_camera(camera_id)["zones_json"]) == current
    assert restarted == []


def test_invalid_camera_scene_does_not_update_or_restart(client, test_db, monkeypatch):
    import app as application

    camera_id = test_db.create_camera("test", "rtsp://example/stream")
    before = test_db.get_camera(camera_id)["zones_json"]
    restarted = []
    monkeypatch.setattr(application.stream_manager, "restart", restarted.append)
    monkeypatch.setattr(application.auth, "current_user", lambda: {"id": 1, "role": "admin"})

    response = client.put(
        f"/api/cameras/{camera_id}",
        json={"zones_json": {"schema_version": 2, "zones": "not-a-list"}},
    )

    assert response.status_code == 400
    assert test_db.get_camera(camera_id)["zones_json"] == before
    assert restarted == []


def test_atomic_camera_scene_updates_allow_only_one_concurrent_writer(test_db):
    camera_id = test_db.create_camera("test", "rtsp://example/stream")
    expected = dumps_zones(test_db.get_camera(camera_id)["zones_json"])
    scenes = [_v2_doc(), _v2_doc()]
    scenes[0]["zones"][0]["points"][0][0] = 11
    scenes[1]["zones"][0]["points"][0][0] = 12
    barrier = threading.Barrier(3)
    results = []

    def update(scene):
        barrier.wait()
        results.append((scene, test_db.update_camera(
            camera_id,
            zones_json=dumps_zones(scene),
            expected_zones_json=expected,
        )))

    writers = [threading.Thread(target=update, args=(scene,)) for scene in scenes]
    for writer in writers:
        writer.start()
    barrier.wait()
    for writer in writers:
        writer.join(timeout=5)
        assert not writer.is_alive()

    assert sorted(result for _, result in results) == [False, True]
    winning_scene = next(scene for scene, succeeded in results if succeeded)
    assert json.loads(test_db.get_camera(camera_id)["zones_json"]) == winning_scene
