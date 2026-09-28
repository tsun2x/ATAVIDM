"""One shared reservation for the single GPU inference device.

TAVIDM runs YOLOv8m + ByteTrack on one consumer GPU (RTX 3050 6GB). Several
callers need that device at the same time:

- the sequential uploaded-video worker (:func:`app._processing_worker`),
- background motorcycle-detail crop batches
  (:meth:`core.motorcycle_detail_scan.MotorcycleDetailScanner.run_once`),
- the manual ``POST /api/motorcycle-detail-review/scan-now`` batch,
- every live RTSP camera frame (:class:`core.live_stream.LiveStreamWorker`).

Checking a queue flag *once* before inference is not mutual exclusion: the flag
can still be clear when a second caller starts. This module is the authoritative
single-slot reservation. A caller holds it from immediately before its GPU
inference begins until its whole job/batch is finished, and releases it in a
``finally`` on success, error, cancellation, and shutdown.

Scheduling policy (documented, deterministic):

* Strict priority, FIFO within one priority level
  (:data:`PRIORITY_VIDEO_JOB` > :data:`PRIORITY_DETAIL_SCAN` >
  :data:`PRIORITY_LIVE_FRAME`). A higher-priority waiter is admitted before a
  lower-priority one even when the slot is momentarily free, so a queued video
  job is never kept out by crop batches or live frames.
* An in-flight holder is never preempted: inference cannot be interrupted, so
  a video job that arrives while a crop batch runs waits for that batch (one
  bounded batch, at most ``DETAIL_SCAN_BATCH_LIMIT`` crops).
* Waiting is always bounded (``timeout``). A caller that cannot be admitted
  reports "busy" and retries on its next pass instead of blocking a thread
  forever; the video worker re-queues its job at the head of the queue.
* Live camera frames never wait: they use :meth:`try_acquire` only and drop the
  frame when the device is busy, so a stream can never starve offline work.
* No database or filesystem operation is ever performed while this module's
  lock is held, and the video queue mutex is never held across inference.

Nested reservations are allowed for one thread re-entering with the same owner
label (an endpoint that reserves, then delegates to a component that reserves
again), so a holder can never deadlock against itself.
"""

from __future__ import annotations

import contextlib
import itertools
import logging
import threading
import time
from collections.abc import Iterator

logger = logging.getLogger(__name__)

# Lower number == higher priority. See the module docstring for the policy.
PRIORITY_VIDEO_JOB = 0
PRIORITY_DETAIL_SCAN = 10
PRIORITY_LIVE_FRAME = 20


class GpuInferenceSlot:
    """A fair, priority-ordered, single-holder reservation for one GPU."""

    def __init__(self, *, name: str = "gpu") -> None:
        self.name = str(name)
        self._cond = threading.Condition(threading.Lock())
        self._owner: str | None = None
        self._token: int | None = None
        self._tokens = itertools.count(1)
        self._waiters: list[dict[str, object]] = []
        self._reentrant: dict[int, tuple[str, int]] = {}
        self.stats: dict[str, int] = {
            "acquired": 0,
            "timeouts": 0,
            "refused": 0,
            "max_waiters": 0,
        }

    # -- introspection ---------------------------------------------------
    def holder(self) -> str | None:
        """Owner label currently holding the slot (None when free)."""
        with self._cond:
            return self._owner

    def is_free(self) -> bool:
        with self._cond:
            return self._owner is None

    def waiter_count(self) -> int:
        with self._cond:
            return len(self._waiters)

    def snapshot(self) -> dict[str, object]:
        with self._cond:
            return {
                "name": self.name,
                "holder": self._owner,
                "waiters": len(self._waiters),
                "stats": dict(self.stats),
            }

    # -- acquisition -----------------------------------------------------
    def try_acquire(
        self, owner: str, *, priority: int = PRIORITY_DETAIL_SCAN
    ) -> int | None:
        """Take the slot if it is free and no higher-priority waiter is queued."""
        owner = str(owner)
        with self._cond:
            nested = self._reentrant.get(threading.get_ident())
            if nested is not None and nested[0] == owner:
                self._reentrant[threading.get_ident()] = (owner, nested[1] + 1)
                return self._token
            if not self._may_admit(priority, seq=None):
                self.stats["refused"] += 1
                return None
            return self._take(owner)

    def acquire(
        self,
        owner: str,
        *,
        timeout: float,
        priority: int = PRIORITY_DETAIL_SCAN,
    ) -> int | None:
        """Wait up to ``timeout`` seconds for the slot; None when it times out."""
        owner = str(owner)
        ident = threading.get_ident()
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._cond:
            nested = self._reentrant.get(ident)
            if nested is not None and nested[0] == owner:
                self._reentrant[ident] = (owner, nested[1] + 1)
                return self._token
            entry: dict[str, object] = {
                "owner": owner,
                "priority": int(priority),
                "seq": next(self._tokens),
                "granted": False,
            }
            self._waiters.append(entry)
            self.stats["max_waiters"] = max(
                self.stats["max_waiters"], len(self._waiters)
            )
            try:
                while True:
                    if self._may_admit(int(priority), seq=int(entry["seq"])):  # type: ignore[arg-type]
                        entry["granted"] = True
                        token = self._take(owner)
                        self._reentrant[ident] = (owner, 1)
                        return token
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self.stats["timeouts"] += 1
                        return None
                    self._cond.wait(timeout=remaining)
            finally:
                # The entry only expresses queue position, so it leaves the
                # waiter list on every exit path (granted or timed out).
                try:
                    self._waiters.remove(entry)
                except ValueError:  # pragma: no cover - defensive
                    pass
                self._cond.notify_all()

    def release(self, token: int | None) -> None:
        """Release a reservation. Safe to call in a ``finally`` on any path."""
        if token is None:
            return
        with self._cond:
            ident = threading.get_ident()
            nested = self._reentrant.get(ident)
            if nested is not None and token == self._token:
                if nested[1] > 1:
                    self._reentrant[ident] = (nested[0], nested[1] - 1)
                    return
                self._reentrant.pop(ident, None)
            if token != self._token or self._owner is None:
                # Mismatched or double release: never free someone else's slot.
                logger.warning(
                    "ignoring stale GPU reservation release for '%s' (token=%s holder=%s)",
                    nested[0] if nested else "unknown",
                    token,
                    self._owner,
                )
                return
            self._owner = None
            self._token = None
            self._cond.notify_all()

    @contextlib.contextmanager
    def reservation(
        self,
        owner: str,
        *,
        timeout: float,
        priority: int = PRIORITY_DETAIL_SCAN,
    ) -> Iterator[bool]:
        """Hold the slot for a whole job. Yields False when it is not acquired.

        The release always happens in this ``finally``, so success, error,
        cancellation, and shutdown all free the device. The body must not be
        entered at all when the result is False, and must never hold another
        lock (a queue mutex, a database handle) while it runs.
        """
        token = self.acquire(owner, timeout=timeout, priority=priority)
        if token is None:
            yield False
            return
        try:
            yield True
        finally:
            self.release(token)

    # -- internals -------------------------------------------------------
    def _may_admit(self, priority: int, *, seq: int | None) -> bool:
        """Free slot, and no better waiter queued ahead of the caller.

        ``seq is None`` marks a non-queued arrival (a live camera frame): it
        must queue behind every waiter that is already present, and it never
        displaces one, so a waiting video job is admitted first.
        """
        if self._owner is not None:
            return False
        for other in self._waiters:
            other_priority = int(other["priority"])  # type: ignore[arg-type]
            if other_priority < priority:
                return False
            if other_priority == priority and seq is not None:
                if int(other["seq"]) < seq:  # type: ignore[arg-type]
                    return False
        return True

    def _take(self, owner: str) -> int:
        token = next(self._tokens)
        self._owner = owner
        self._token = token
        self.stats["acquired"] += 1
        return token


_GLOBAL_SLOT = GpuInferenceSlot(name="tavidm-gpu")


def gpu_inference_slot() -> GpuInferenceSlot:
    """The process-wide reservation shared by every GPU inference caller."""
    return _GLOBAL_SLOT
