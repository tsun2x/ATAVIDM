"""Regression tests for bounded live-camera worker replacement."""

from __future__ import annotations

import threading

import pytest

from core.live_stream import StreamManager, mjpeg_generator


class WorkerFactory:
    def __init__(self):
        self.instances = []
        self.old_join_entered = threading.Event()
        self.release_old = threading.Event()
        self.waiters = []

    def worker_class(self):
        factory = self

        class Worker:
            def __init__(self, camera):
                self.camera = dict(camera)
                self.alive = False
                self.stop_requested = False
                self.status = "starting"
                self.error = None
                self.diagnostics = []
                self.block_join = not factory.instances
                self.overlap = any(worker.alive for worker in factory.instances)
                factory.instances.append(self)

            def start(self):
                self.alive = True
                self.status = "running"

            def stop(self):
                self.stop_requested = True

            def join(self, timeout=None):
                if self.block_join:
                    factory.old_join_entered.set()
                    released = factory.release_old.wait(timeout)
                    if not released:
                        return
                self.alive = False
                self.status = "stopped"

            def is_alive(self):
                return self.alive

            def latest_jpeg(self):
                return b"test-frame"

        return Worker

    def thread_factory(self, *, target, args, name, daemon):
        completed = threading.Event()

        def run():
            try:
                target(*args)
            finally:
                completed.set()

        thread = threading.Thread(target=run, name=name, daemon=daemon)
        thread.completed = completed
        self.waiters.append(thread)
        return thread


def test_delayed_restart_replaces_worker_after_old_mjpeg_attachment(monkeypatch):
    import core.live_stream as live_stream

    factory = WorkerFactory()
    camera = {"id": 41, "zones_json": "old-scene"}
    monkeypatch.setattr(live_stream, "LiveStreamWorker", factory.worker_class())
    monkeypatch.setattr(live_stream.db, "get_camera", lambda camera_id: dict(camera))
    manager = StreamManager(restart_timeout_seconds=2, restart_thread_factory=factory.thread_factory)

    old_worker = manager.start(41)
    assert manager.restart(41) is True
    assert factory.old_join_entered.wait(1)

    # This is the original save→MJPEG race: a reconnect request still attaches
    # to the old worker while it is unwinding. Repeated viewer requests do not
    # create overlapping workers.
    old_mjpeg_worker = manager.start(41)
    assert old_mjpeg_worker is old_worker
    assert manager.start(41) is old_worker
    assert len(factory.instances) == 1
    old_response = mjpeg_generator(old_mjpeg_worker)
    assert b"test-frame" in next(old_response)

    camera["zones_json"] = "new-scene"
    factory.release_old.set()
    assert factory.waiters[0].completed.wait(1)

    replacement = manager.get(41)
    assert replacement is not old_worker
    assert replacement.camera["zones_json"] == "new-scene"
    assert replacement.is_alive()
    assert all(not worker.overlap for worker in factory.instances)
    with pytest.raises(StopIteration):
        next(old_response)


def test_explicit_stop_cancels_a_pending_save_restart(monkeypatch):
    import core.live_stream as live_stream

    factory = WorkerFactory()
    monkeypatch.setattr(live_stream, "LiveStreamWorker", factory.worker_class())
    monkeypatch.setattr(live_stream.db, "get_camera", lambda camera_id: {"id": camera_id, "zones_json": "scene"})
    manager = StreamManager(restart_timeout_seconds=2, restart_thread_factory=factory.thread_factory)

    old_worker = manager.start(42)
    assert manager.restart(42) is True
    assert factory.old_join_entered.wait(1)
    manager.stop(42)
    factory.release_old.set()
    assert factory.waiters[0].completed.wait(1)

    assert len(factory.instances) == 1
    assert not old_worker.is_alive()
    assert manager.status(42)["status"] == "stopped"


def test_restart_timeout_is_reported_and_does_not_overlap_workers(monkeypatch):
    import core.live_stream as live_stream

    factory = WorkerFactory()
    monkeypatch.setattr(live_stream, "LiveStreamWorker", factory.worker_class())
    monkeypatch.setattr(live_stream.db, "get_camera", lambda camera_id: {"id": camera_id, "zones_json": "scene"})
    manager = StreamManager(restart_timeout_seconds=0.1, restart_thread_factory=factory.thread_factory)

    old_worker = manager.start(43)
    assert manager.restart(43) is True
    assert factory.waiters[0].completed.wait(1)

    status = manager.status(43)
    assert status["status"] == "error"
    assert "did not stop within" in status["error"]
    assert old_worker.is_alive()
    assert len(factory.instances) == 1

    # A repeated save can retry the bounded shutdown wait after the first
    # timeout. Replacement still starts only once the old worker exits.
    factory.release_old.set()
    assert manager.restart(43) is True
    assert factory.waiters[1].completed.wait(1)
    replacement = manager.get(43)
    assert replacement is not old_worker
    assert not replacement.overlap
