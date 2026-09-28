"""Shared GPU inference reservation contract.

Catches a second YOLOv8 pass running at the same time as another one: an
uploaded-video job, a background motorcycle crop batch, a manual detail scan, and
a live camera frame must never overlap. Concurrency is proven with barriers and
events, never with sleeps.
"""

from __future__ import annotations

import threading

import pytest

from core.gpu_inference_slot import (
    PRIORITY_DETAIL_SCAN,
    PRIORITY_LIVE_FRAME,
    PRIORITY_VIDEO_JOB,
    GpuInferenceSlot,
)


class TestGpuInferenceSlot:
    def test_only_one_holder_at_a_time(self):
        slot = GpuInferenceSlot()
        first = slot.acquire("video-job:1", timeout=0.0, priority=PRIORITY_VIDEO_JOB)
        assert first is not None
        # A second, lower-priority request must not be granted while held.
        assert slot.acquire("live:1", timeout=0.0, priority=PRIORITY_LIVE_FRAME) is None
        slot.release(first)
        assert slot.try_acquire("live:1", priority=PRIORITY_LIVE_FRAME) is not None

    def test_video_job_preempts_queued_detail_scan_waiter(self):
        """A queued crop batch must not delay a waiting uploaded-video job."""
        slot = GpuInferenceSlot()
        order: list[str] = []
        record_lock = threading.Lock()
        holder_ready = threading.Event()
        release_holder = threading.Event()
        video_done = threading.Event()
        detail_done = threading.Event()

        def record(name: str) -> None:
            with record_lock:
                order.append(name)

        def live_holder() -> None:
            # An in-flight holder is never preempted; it just finishes.
            token = slot.acquire("live-frame:1", timeout=0.0, priority=PRIORITY_LIVE_FRAME)
            holder_ready.set()
            release_holder.wait(10.0)
            slot.release(token)

        def video_job() -> None:
            token = slot.acquire("video-job:1", timeout=10.0, priority=PRIORITY_VIDEO_JOB)
            if token is None:
                record("video-timeout")
            else:
                record("video-acquired")
                slot.release(token)
            video_done.set()

        def detail_batch() -> None:
            with slot.reservation(
                "detail-scan", timeout=10.0, priority=PRIORITY_DETAIL_SCAN
            ) as acquired:
                record("detail-acquired" if acquired else "detail-timeout")
                detail_done.set()

        holder = threading.Thread(target=live_holder, name="holder")
        video = threading.Thread(target=video_job, name="video")
        detail = threading.Thread(target=detail_batch, name="detail")
        holder.start()
        assert holder_ready.wait(5.0)
        detail.start()
        # Deterministic: the crop batch must be queued before the video job asks,
        # otherwise the ordering proves nothing.
        assert _await_condition(lambda: slot.waiter_count() == 1, 5.0)
        video.start()
        assert _await_condition(lambda: slot.waiter_count() == 2, 5.0)
        release_holder.set()
        try:
            assert video_done.wait(10.0)
            assert detail_done.wait(10.0)
        finally:
            release_holder.set()
            for thread in (holder, video, detail):
                thread.join(10.0)
        assert all(not t.is_alive() for t in (holder, video, detail))
        # The video job jumped the already-queued crop batch, which ran after it.
        assert order.index("video-acquired") < order.index("detail-acquired")
        assert slot.is_free()
        assert slot.waiter_count() == 0

    def test_live_frame_never_blocks_and_never_queues(self):
        slot = GpuInferenceSlot()
        slot.acquire("video-job:1", timeout=0.0, priority=PRIORITY_VIDEO_JOB)
        with slot.reservation(
            "live-frame", timeout=0.0, priority=PRIORITY_LIVE_FRAME
        ) as acquired:
            assert acquired is False
        # A dropped frame must not leave a waiter behind for later to satisfy.
        assert slot.waiter_count() == 0

    def test_timeout_reports_failure_and_leaves_no_waiter(self):
        slot = GpuInferenceSlot()
        slot.acquire("video-job:1", timeout=0.0, priority=PRIORITY_VIDEO_JOB)
        with slot.reservation("detail-scan", timeout=0.05, priority=PRIORITY_DETAIL_SCAN) as acquired:
            assert acquired is False
        assert slot.waiter_count() == 0
        assert slot.holder() == "video-job:1"
        assert slot.snapshot()["stats"]["timeouts"] == 1

    def test_reservation_releases_on_exception(self):
        slot = GpuInferenceSlot()
        with pytest.raises(RuntimeError):
            with slot.reservation("video-job:1", timeout=0.0, priority=PRIORITY_VIDEO_JOB):
                raise RuntimeError("pipeline blew up")
        assert slot.is_free()
        assert slot.try_acquire("detail-scan", priority=PRIORITY_DETAIL_SCAN) is not None

    def test_release_is_idempotent_for_the_same_token(self):
        slot = GpuInferenceSlot()
        token = slot.acquire("video-job:1", timeout=0.0, priority=PRIORITY_VIDEO_JOB)
        slot.release(token)
        slot.release(token)  # must not free someone else's later reservation
        other = slot.acquire("video-job:2", timeout=0.0, priority=PRIORITY_VIDEO_JOB)
        assert slot.holder() == "video-job:2"
        slot.release(other)
        assert slot.is_free()

    def test_bounded_timeout_returns_none_instead_of_blocking_forever(self):
        slot = GpuInferenceSlot()
        slot.acquire("video-job:1", timeout=0.0, priority=PRIORITY_VIDEO_JOB)
        result: list[object] = []
        thread = threading.Thread(
            target=lambda: result.append(
                slot.acquire("detail-scan", timeout=0.2, priority=PRIORITY_DETAIL_SCAN)
            )
        )
        thread.start()
        thread.join(5.0)
        assert not thread.is_alive()
        assert result == [None]

    def test_concurrent_holders_never_overlap(self):
        """10 threads x 20 acquisitions: exactly one in flight at any moment."""
        slot = GpuInferenceSlot()
        inside = 0
        max_inside = 0
        guard = threading.Lock()
        errors: list[str] = []
        barrier = threading.Barrier(10, timeout=30.0)

        def worker(name: str) -> None:
            nonlocal inside, max_inside
            barrier.wait()  # maximize contention: all 10 threads start together
            for i in range(20):
                with slot.reservation(name, timeout=5.0, priority=PRIORITY_DETAIL_SCAN) as acquired:
                    if not acquired:
                        errors.append(f"{name} not acquired")
                        continue
                    with guard:
                        inside += 1
                        max_inside = max(max_inside, inside)
                        if inside != 1:
                            errors.append(f"{name}#{i} overlapped")
                    with guard:
                        inside -= 1

        threads = [threading.Thread(target=worker, args=(f"t{n}",)) for n in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30.0)
        assert all(not t.is_alive() for t in threads)
        assert errors == []
        assert max_inside == 1

    def test_module_default_slot_is_a_process_wide_singleton(self):
        from core.gpu_inference_slot import gpu_inference_slot

        assert gpu_inference_slot() is gpu_inference_slot()


def _await_condition(predicate, timeout: float) -> bool:
    """Poll ``predicate`` until true or ``timeout`` elapses (no fixed sleeps)."""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()
