"""Bounded orchestration for experimental local plate detection + OCR.

Responsibilities, in order:

1. **Collect** only attributable vehicle crops for a review observation that
   already exists. Nothing here creates or confirms a violation.
2. **Queue** the collected crops for a single background inference worker that
   never touches the live-frame path or the shared GPU reservation.
3. **Run** the two-stage pipeline (detector -> quality gate -> OCR) under hard,
   configured budgets.
4. **Record** one immutable machine attempt manifest per observation.

Attribution rules (all enforced here, none of them optional)
-------------------------------------------------------------
* Evidence is keyed by ``(source, run_key, live_session_id, track_id,
  identity_epoch, review_id)``. ByteTrack ids are reused, so the identity
  epoch is part of every key and of every manifest.
* Every collected crop comes from **its own frame** and that frame's own
  tracked box. An event-frame box is never reused on a later frame.
* A live reconnect produces a new ``live_session_id``, so post-reconnect crops
  can never be attributed to a pre-reconnect observation.
* A vehicle whose box overlaps another tracked vehicle above
  ``max_vehicle_overlap_iou`` is treated as ambiguously associated and is not
  sent for OCR.
* Full-frame plate detections without trustworthy vehicle matching are never
  used: the detector only ever runs on a selected, attributable vehicle crop.

Budget honesty
--------------
The scheduling budget bounds **when the next call may start**. It cannot
interrupt a synchronous ONNX Runtime call that is already running, and this
module never claims otherwise. A job that exhausts the budget reports partial
results plus ``budget_exhausted``.
"""

from __future__ import annotations

import enum
import logging
import queue
import re
import threading
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from core import plate_manifest as manifests
from core.plate_onnx_backend import (
    PlateBackendError,
    PlateBackendUnavailable,
    PlateOnnxBackend,
    check_plate_quality,
    crop_from_box,
    iou,
)
from core.plate_settings import CONTRACT_VERSION, PlateOcrSettings

logger = logging.getLogger(__name__)

#: Predeclared presentation normalization used only for *grouping* competing
#: reads. It never rewrites a stored raw string, and it never substitutes
#: characters (``O`` is not turned into ``0``, ``I`` is not turned into ``1``).
_DISPLAY_NORMALIZE = re.compile(r"[^A-Z0-9]+")


class PlateAttemptOutcome(str, enum.Enum):
    """Orchestration-layer outcome. Never a human plate status."""

    DISABLED = "disabled"
    QUEUED = "queued"
    PROCESSING = "processing"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    NO_CANDIDATE_DETECTED = "no_candidate_detected"
    DETECTED_UNREADABLE = "detected_unreadable"
    QUALITY_REJECTED = "quality_rejected"
    ASSOCIATION_UNCERTAIN = "association_uncertain"
    CANCELLED = "cancelled"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CANDIDATE_FOUND = "candidate_found"

    @property
    def is_terminal(self) -> bool:
        return self not in (
            PlateAttemptOutcome.QUEUED,
            PlateAttemptOutcome.PROCESSING,
        )


#: Outcomes that a human admin must be told about without any implication
#: that a plate does not exist or that a human judged it invisible.
AMBIGUOUS_OUTCOMES = frozenset(
    {
        PlateAttemptOutcome.NO_CANDIDATE_DETECTED,
        PlateAttemptOutcome.DETECTED_UNREADABLE,
        PlateAttemptOutcome.QUALITY_REJECTED,
        PlateAttemptOutcome.ASSOCIATION_UNCERTAIN,
        PlateAttemptOutcome.CANCELLED,
        PlateAttemptOutcome.BUDGET_EXHAUSTED,
        PlateAttemptOutcome.UNAVAILABLE,
        PlateAttemptOutcome.FAILED,
    }
)


class CropMemoryBudget:
    """Process-wide retained-crop budget shared by collectors and queued work.

    Reservations are made when a crop is retained and released when the job
    finishes (success, failure, cancellation, or budget rejection).
    """

    def __init__(self, limit_bytes: int) -> None:
        self.limit_bytes = max(0, int(limit_bytes))
        self._used = 0
        self._lock = threading.Lock()

    @property
    def used_bytes(self) -> int:
        with self._lock:
            return self._used

    def try_reserve(self, nbytes: int) -> bool:
        size = max(0, int(nbytes))
        with self._lock:
            if self._used + size > self.limit_bytes:
                return False
            self._used += size
            return True

    def release(self, nbytes: int) -> None:
        size = max(0, int(nbytes))
        with self._lock:
            self._used = max(0, self._used - size)


# ----------------------------------------------------------------------
# Samples
# ----------------------------------------------------------------------


@dataclass
class PlateCropSample:
    """One attributable vehicle crop selected for OCR."""

    sample_id: str
    frame_number: int
    timestamp_sec: float
    frame_width: int
    frame_height: int
    vehicle_box: tuple[int, int, int, int]
    crop_origin: tuple[int, int]
    crop_width: int
    crop_height: int
    image: np.ndarray = field(repr=False)
    nbytes: int = 0
    reserved: bool = False
    vehicle_crop_ref: dict[str, Any] | None = None

    def key(self) -> str:
        return self.sample_id


@dataclass
class PlateCandidate:
    """One machine plate candidate. Not an accepted offender identity."""

    candidate_id: str
    sample_id: str
    ocr_raw: str | None
    detection_label: str | None
    detection_confidence: float | None
    ocr_scalar_score: float | None
    ocr_char_scores: list[float] | None
    plate_box_in_crop: tuple[int, int, int, int]
    plate_box_in_frame: tuple[int, int, int, int]
    plate_crop_ref: dict[str, Any] | None = None
    quality: dict[str, Any] = field(default_factory=dict)

    def display_text(self) -> str:
        """Declared presentation normalization (never replaces the raw text)."""
        return _DISPLAY_NORMALIZE.sub("", (self.ocr_raw or "").upper())

    def to_json(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "sample_id": self.sample_id,
            "ocr_raw": self.ocr_raw,
            "ocr_display_normalized": self.display_text(),
            "detection_label": self.detection_label,
            "detection_confidence": self.detection_confidence,
            "ocr_scalar_score": self.ocr_scalar_score,
            "ocr_char_scores": list(self.ocr_char_scores) if self.ocr_char_scores else None,
            "ocr_score_semantics": (
                "raw per-slot maximum logit from the OCR plate head; uncalibrated, "
                "not a probability and not an accuracy estimate"
            ),
            "plate_box_in_crop": list(self.plate_box_in_crop),
            "plate_box_in_frame": list(self.plate_box_in_frame),
            "plate_crop_ref": self.plate_crop_ref,
            "quality": self.quality,
        }


@dataclass
class PlateObservation:
    """A pending machine observation for one already-created review row."""

    review_id: int
    source: str
    run_key: str
    live_session_id: str | None
    track_id: int
    identity_epoch: int | None
    violation_type: str
    trigger_frame_number: int
    trigger_timestamp_sec: float
    registered_at_monotonic: float
    state: PlateAttemptOutcome = PlateAttemptOutcome.QUEUED
    samples: list[PlateCropSample] = field(default_factory=list)
    association_uncertain: bool = False
    truncated: dict[str, bool] = field(default_factory=dict)
    rejection_reasons: list[str] = field(default_factory=list)
    case_id: int | None = None
    finished: bool = False

    def observation_key(self) -> tuple[Any, ...]:
        return (
            self.source,
            self.run_key,
            self.live_session_id or "",
            int(self.track_id),
            self.identity_epoch,
            int(self.review_id),
        )

    def deadline(self, window_sec: float) -> float:
        return self.registered_at_monotonic + float(window_sec)

    def allow_sample(self, timestamp_sec: float, now_monotonic: float, bounds: Mapping[str, float]) -> bool:
        if self.association_uncertain:
            return False
        if now_monotonic > self.deadline(float(bounds["collection_window_sec"])):
            return False
        if len(self.samples) >= int(bounds["max_crops_per_observation"]):
            return False
        rate = float(bounds["sample_fps"])
        if rate <= 0:
            return False
        if self.samples:
            last = self.samples[-1].timestamp_sec
            if (float(timestamp_sec) - last) < (1.0 / rate) - 1e-6:
                return False
        return True


# ----------------------------------------------------------------------
# Worker
# ----------------------------------------------------------------------


@dataclass
class _Job:
    attempt_id: str
    observation: PlateObservation
    samples: list[PlateCropSample]
    reserved_bytes: int
    enqueued_monotonic: float


class PlateInferenceWorker:
    """Single background worker. One thread, one job at a time, bounded queue."""

    def __init__(
        self,
        settings: PlateOcrSettings,
        backend: PlateOnnxBackend,
        *,
        memory: CropMemoryBudget,
    ) -> None:
        self.settings = settings
        self.backend = backend
        self.memory = memory
        self._queue: queue.Queue[_Job | None] = queue.Queue(
            maxsize=int(settings.bounds["max_queued_jobs"])
        )
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._states: dict[int, str] = {}
        self.processed = 0
        self.rejected_saturated = 0
        self.failed = 0

    # -- lifecycle ----------------------------------------------------
    def start(self) -> None:
        with self._lock:
            if self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._run, name="plate-ocr-worker", daemon=True
            )
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            thread = self._thread
            self._thread = None
        if thread is None:
            return
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        thread.join(timeout=max(0.0, float(timeout)))

    # -- status surface (process-local, never persisted) ---------------
    def state_for(self, review_id: int | None) -> str | None:
        if review_id is None:
            return None
        with self._lock:
            return self._states.get(int(review_id))

    def _set_state(self, review_id: int | None, state: str | None) -> None:
        if review_id is None:
            return
        with self._lock:
            if state is None:
                self._states.pop(int(review_id), None)
            else:
                self._states[int(review_id)] = state

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self._thread is not None,
                "queue_depth": self._queue.qsize(),
                "queue_capacity": int(self.settings.bounds["max_queued_jobs"]),
                "processed_jobs": self.processed,
                "rejected_saturated": self.rejected_saturated,
                "failed_jobs": self.failed,
                "retained_crop_bytes": self.memory.used_bytes,
                "retained_crop_budget_bytes": self.memory.limit_bytes,
            }

    # -- submission ---------------------------------------------------
    def submit(self, attempt_id: str, observation: PlateObservation) -> bool:
        """Enqueue one observation. ``False`` means queue saturation."""
        if not observation.samples:
            self._write_terminal(attempt_id, observation, PlateAttemptOutcome.NO_CANDIDATE_DETECTED)
            return True
        reserved = sum(max(0, s.nbytes) for s in observation.samples)
        job = _Job(
            attempt_id=attempt_id,
            observation=observation,
            samples=list(observation.samples),
            reserved_bytes=reserved,
            enqueued_monotonic=time.monotonic(),
        )
        try:
            self._queue.put_nowait(job)
        except queue.Full:
            with self._lock:
                self.rejected_saturated += 1
            self._release(job)
            self._write_terminal(
                attempt_id,
                observation,
                PlateAttemptOutcome.BUDGET_EXHAUSTED,
                extra={"reason": "queue_saturated", "queued": False},
            )
            return False
        self._set_state(observation.review_id, PlateAttemptOutcome.QUEUED.value)
        return True

    def _release(self, job: _Job) -> None:
        # Drop the in-memory crop pixels and give the budget back. The crops
        # themselves are already persisted under the private plate root.
        for sample in job.samples:
            sample.image = np.empty((0, 0, 0), dtype=np.uint8)
            sample.nbytes = 0
            sample.reserved = False
        self.memory.release(job.reserved_bytes)

    # -- worker loop --------------------------------------------------
    def _run(self) -> None:
        while True:
            job = self._queue.get()
            try:
                if job is None:
                    return
                self._process(job)
            except Exception:  # noqa: BLE001 - a worker must never die silently
                logger.exception("plate OCR worker job failed")
                if job is not None:
                    with self._lock:
                        self.failed += 1
                    self._release(job)
                    self._write_terminal(
                        job.attempt_id,
                        job.observation,
                        PlateAttemptOutcome.FAILED,
                        extra={"reason": "worker_exception"},
                    )
            finally:
                self._queue.task_done()

    def _process(self, job: _Job) -> None:
        observation = job.observation
        self._set_state(observation.review_id, PlateAttemptOutcome.PROCESSING.value)
        timings: dict[str, float] = {}
        calls = {"detector": 0, "ocr": 0}
        candidates: list[PlateCandidate] = []
        truncated = {"detector": False, "ocr": False, "collection": bool(observation.truncated.get("collection"))}
        rejection_reasons = list(observation.rejection_reasons)
        detections_seen = 0
        budget_exhausted = False

        deadline = time.monotonic() + float(self.settings.bounds["schedule_budget_sec"])

        try:
            for sample in job.samples:
                if time.monotonic() >= deadline:
                    budget_exhausted = True
                    break
                if calls["detector"] >= int(self.settings.bounds["max_detector_calls"]):
                    truncated["detector"] = True
                    break
                self._set_state(observation.review_id, PlateAttemptOutcome.PROCESSING.value)

                start = time.perf_counter()
                try:
                    boxes = self.backend.detect_plates(sample.image)
                except PlateBackendUnavailable as exc:
                    calls["detector"] += 1
                    self._release(job)
                    self._write_terminal(
                        job.attempt_id,
                        observation,
                        PlateAttemptOutcome.UNAVAILABLE,
                        extra={"reason": str(exc)},
                        calls=calls,
                    )
                    return
                except PlateBackendError as exc:
                    calls["detector"] += 1
                    self._release(job)
                    self._write_terminal(
                        job.attempt_id,
                        observation,
                        PlateAttemptOutcome.FAILED,
                        extra={"reason": str(exc)},
                        calls=calls,
                    )
                    return
                calls["detector"] += 1
                timings[f"detect_{sample.sample_id}_ms"] = (time.perf_counter() - start) * 1000.0
                # Synchronous inference may overrun this deadline, but cannot
                # be interrupted. Keep the original deadline fixed.

                if not boxes:
                    continue
                detections_seen += len(boxes)

                for box in boxes:
                    if calls["ocr"] >= int(self.settings.bounds["max_ocr_calls"]):
                        truncated["ocr"] = True
                        break
                    if time.monotonic() >= deadline:
                        budget_exhausted = True
                        break
                    plate_crop, rect = crop_from_box(
                        sample.image, (box.x1, box.y1, box.x2, box.y2)
                    )
                    quality = check_plate_quality(
                        plate_crop,
                        settings=self.settings,
                        requested_box=(box.x1, box.y1, box.x2, box.y2),
                        effective_rect=rect,
                    )
                    if not quality.accepted:
                        rejection_reasons.extend(quality.reasons)
                        continue
                    ocr_start = time.perf_counter()
                    try:
                        read = self.backend.recognize_plate(plate_crop)
                    except PlateBackendError as exc:
                        calls["ocr"] += 1
                        self._release(job)
                        self._write_terminal(
                            job.attempt_id,
                            observation,
                            PlateAttemptOutcome.FAILED,
                            extra={"reason": str(exc)},
                            calls=calls,
                        )
                        return
                    calls["ocr"] += 1
                    timings[f"ocr_{sample.sample_id}_ms"] = (time.perf_counter() - ocr_start) * 1000.0

                    candidate_id = f"cand_{uuid.uuid4().hex[:12]}"
                    plate_ref = None
                    try:
                        plate_ref = manifests.save_crop(
                            job.attempt_id, f"plate_{candidate_id}.png", plate_crop
                        )
                    except Exception:  # noqa: BLE001 - crop persistence is best effort
                        logger.warning("plate crop write failed for %s", candidate_id)

                    ox, oy = sample.crop_origin
                    candidates.append(
                        PlateCandidate(
                            candidate_id=candidate_id,
                            sample_id=sample.sample_id,
                            ocr_raw=read.text,
                            detection_label=box.label,
                            detection_confidence=float(box.score),
                            ocr_scalar_score=read.mean_score,
                            ocr_char_scores=list(read.char_scores) if read.char_scores else None,
                            plate_box_in_crop=rect,
                            plate_box_in_frame=(ox + rect[0], oy + rect[1], ox + rect[2], oy + rect[3]),
                            plate_crop_ref=plate_ref,
                            quality=dict(quality.metrics),
                        )
                    )
                if truncated["ocr"] or budget_exhausted:
                    break

            outcome = self._classify(
                candidates=candidates,
                detections_seen=detections_seen,
                observation=observation,
                budget_exhausted=budget_exhausted,
                rejection_reasons=rejection_reasons,
            )
            self._release(job)
            self._write(
                job.attempt_id,
                observation,
                outcome,
                candidates=candidates,
                calls=calls,
                truncated=truncated,
                timings=timings,
                rejection_reasons=rejection_reasons,
                detections_seen=detections_seen,
                queue_wait_ms=(time.monotonic() - job.enqueued_monotonic) * 1000.0,
                budget_exhausted=budget_exhausted,
                extra=None,
            )
            with self._lock:
                self.processed += 1
        finally:
            self._set_state(observation.review_id, None)

    # -- outcome classification --------------------------------------
    @staticmethod
    def _classify(
        *,
        candidates: Sequence[PlateCandidate],
        detections_seen: int,
        observation: PlateObservation,
        budget_exhausted: bool,
        rejection_reasons: Sequence[str],
    ) -> PlateAttemptOutcome:
        if observation.association_uncertain:
            # An ambiguous vehicle association invalidates every candidate it
            # would have produced, regardless of what the models returned.
            return PlateAttemptOutcome.ASSOCIATION_UNCERTAIN
        readable = [c for c in candidates if c.ocr_raw]
        if readable:
            return PlateAttemptOutcome.CANDIDATE_FOUND
        if budget_exhausted:
            return PlateAttemptOutcome.BUDGET_EXHAUSTED
        if candidates:
            # A plate box was found and passed quality, but no text came back.
            return PlateAttemptOutcome.DETECTED_UNREADABLE
        if detections_seen:
            return (
                PlateAttemptOutcome.QUALITY_REJECTED
                if rejection_reasons
                else PlateAttemptOutcome.DETECTED_UNREADABLE
            )
        return PlateAttemptOutcome.NO_CANDIDATE_DETECTED

    # -- manifest writing --------------------------------------------
    def _write_terminal(
        self,
        attempt_id: str,
        observation: PlateObservation,
        outcome: PlateAttemptOutcome,
        *,
        extra: dict[str, Any] | None = None,
        calls: dict[str, int] | None = None,
    ) -> None:
        # An ambiguous vehicle association outranks every other terminal state:
        # whatever the models would have returned is not attributable to a vehicle.
        outcome = (
            PlateAttemptOutcome.ASSOCIATION_UNCERTAIN
            if observation.association_uncertain
            else outcome
        )
        self._write(
            attempt_id,
            observation,
            outcome,
            candidates=(),
            calls=calls or {"detector": 0, "ocr": 0},
            truncated={
                "detector": False,
                "ocr": False,
                "collection": bool(observation.truncated.get("collection")),
            },
            timings={},
            rejection_reasons=list(observation.rejection_reasons),
            detections_seen=0,
            queue_wait_ms=0.0,
            budget_exhausted=outcome is PlateAttemptOutcome.BUDGET_EXHAUSTED,
            extra=extra,
        )

    def _write(
        self,
        attempt_id: str,
        observation: PlateObservation,
        outcome: PlateAttemptOutcome,
        *,
        candidates: Sequence[PlateCandidate],
        calls: Mapping[str, int],
        truncated: Mapping[str, bool],
        timings: Mapping[str, float],
        rejection_reasons: Sequence[str],
        detections_seen: int,
        queue_wait_ms: float,
        budget_exhausted: bool,
        extra: dict[str, Any] | None,
    ) -> None:
        primary = select_primary_candidate(candidates)
        payload: dict[str, Any] = {
            "contract_version": CONTRACT_VERSION,
            "attempt_id": attempt_id,
            "created_at_unix": time.time(),
            "review_id": observation.review_id,
            "case_id": observation.case_id,
            "source": {
                "kind": observation.source,
                "run_key": observation.run_key,
                "live_session_id": observation.live_session_id,
                "track_id": int(observation.track_id),
                "track_identity_epoch": observation.identity_epoch,
                "violation_type": observation.violation_type,
            },
            "trigger": {
                "frame_number": int(observation.trigger_frame_number),
                "timestamp_sec": float(observation.trigger_timestamp_sec),
            },
            "outcome": outcome.value,
            "outcome_is_ambiguous": outcome in AMBIGUOUS_OUTCOMES,
            "candidates": [c.to_json() for c in candidates],
            "primary_candidate_id": primary.candidate_id if primary else None,
            "association_uncertain": bool(observation.association_uncertain),
            "quality_rejections": sorted(set(rejection_reasons)),
            "detections_seen": int(detections_seen),
            "calls": {
                "detector": int(calls.get("detector", 0)),
                "ocr": int(calls.get("ocr", 0)),
                "detector_limit": int(self.settings.bounds["max_detector_calls"]),
                "ocr_limit": int(self.settings.bounds["max_ocr_calls"]),
            },
            "truncated": {k: bool(v) for k, v in truncated.items()},
            "timings": {
                "queue_wait_ms": round(float(queue_wait_ms), 3),
                **{k: round(float(v), 3) for k, v in timings.items()},
            },
            "budget": {
                "schedule_budget_sec": float(self.settings.bounds["schedule_budget_sec"]),
                "schedule_budget_exhausted": bool(budget_exhausted),
                "note": (
                    "The scheduling budget bounds when the next inference call may start. "
                    "It cannot interrupt a synchronous ONNX Runtime call already in progress; "
                    "a hard deadline would require a supervised local process."
                ),
            },
            "samples": [
                {
                    "sample_id": s.sample_id,
                    "frame_number": int(s.frame_number),
                    "timestamp_sec": float(s.timestamp_sec),
                    "frame_width": int(s.frame_width),
                    "frame_height": int(s.frame_height),
                    "vehicle_box_frame_px": list(s.vehicle_box),
                    "crop_origin_frame_px": list(s.crop_origin),
                    "crop_width": int(s.crop_width),
                    "crop_height": int(s.crop_height),
                    "vehicle_crop_ref": s.vehicle_crop_ref,
                }
                for s in observation.samples
            ],
            "machine_provenance": self.backend.metadata(),
            "notes": (
                "Machine observation only. No human verification, no accepted plate "
                "identity, no violation decision. 'no candidate' does not mean no plate "
                "exists and never means a human judged the plate not visible."
            ),
        }
        if extra:
            payload["detail"] = extra
        try:
            path = manifests.write_manifest(payload)
        except manifests.PlateManifestError as exc:
            logger.warning("plate manifest write failed for %s: %s", attempt_id, exc)
            return
        try:
            manifests.write_index(
                manifests.review_key(observation.review_id),
                {
                    "attempt_id": attempt_id,
                    "review_id": observation.review_id,
                    "case_id": observation.case_id,
                    "manifest_path": manifests.stored_plate_path(path),
                    "outcome": outcome.value,
                    "written_at_unix": time.time(),
                },
            )
        except manifests.PlateManifestError:  # pragma: no cover - best effort
            logger.warning("plate manifest index write failed for %s", attempt_id)


def select_primary_candidate(candidates: Sequence[PlateCandidate]) -> PlateCandidate | None:
    """Declared primary-candidate rule. No ground truth is consulted.

    Reads are grouped by their *declared* presentation normalization. The group
    supported by the most distinct sampled frames wins; ties break on the
    highest mean uncalibrated OCR score, then on the lower candidate id.
    Agreement only ranks candidates - it never edits or merges raw text.
    """
    readable = [c for c in candidates if c.ocr_raw]
    if not readable:
        return None
    groups: dict[str, list[PlateCandidate]] = {}
    for candidate in readable:
        groups.setdefault(candidate.display_text(), []).append(candidate)
    ranked = sorted(
        groups.items(),
        key=lambda item: (
            -len({c.sample_id for c in item[1]}),
            -max((c.ocr_scalar_score or 0.0) for c in item[1]),
            min(c.candidate_id for c in item[1]),
        ),
    )
    best = ranked[0][1]
    return sorted(best, key=lambda c: c.candidate_id)[0]


# ----------------------------------------------------------------------
# Collector
# ----------------------------------------------------------------------


class PlateObservationCollector:
    """Per-run (uploaded video) or per-session (live camera) collector."""

    def __init__(
        self,
        settings: PlateOcrSettings,
        backend: PlateOnnxBackend,
        worker: PlateInferenceWorker,
        *,
        source: str,
        run_key: str,
        live_session_id: str | None = None,
    ) -> None:
        self.settings = settings
        self.backend = backend
        self.worker = worker
        self.source = source
        self.run_key = run_key
        self.live_session_id = live_session_id
        self._pending: dict[int, PlateObservation] = {}
        self._keys: dict[tuple[Any, ...], int] = {}
        self._lock = threading.Lock()
        self.duplicates_skipped = 0
        self.observations_registered = 0
        self.samples_collected = 0
        self.memory_rejections = 0

    # -- registration -------------------------------------------------
    def register(
        self,
        *,
        review_id: int,
        track_id: int,
        identity_epoch: int | None,
        violation_type: str,
        frame_number: int,
        timestamp_sec: float,
    ) -> int | None:
        """Start collecting for an existing review row.

        Returns ``None`` when the observation was skipped (OCR disabled,
        deduplicated equivalent work, or memory budget exhausted).
        """
        if not self.settings.is_enabled or review_id is None:
            return None
        observation = PlateObservation(
            review_id=int(review_id),
            source=self.source,
            run_key=self.run_key,
            live_session_id=self.live_session_id,
            track_id=int(track_id),
            identity_epoch=None if identity_epoch is None else int(identity_epoch),
            violation_type=str(violation_type or ""),
            trigger_frame_number=int(frame_number),
            trigger_timestamp_sec=float(timestamp_sec),
            registered_at_monotonic=time.monotonic(),
        )
        key = observation.observation_key()
        with self._lock:
            existing_id = self._keys.get(key)
            if existing_id is not None and existing_id in self._pending:
                self.duplicates_skipped += 1
                return None
            if existing_id is not None:
                # Same key already finished; a repeat trigger is new work but the
                # index pointer will be replaced atomically by the new attempt.
                self._keys.pop(key, None)
            self._pending[int(review_id)] = observation
            self._keys[key] = int(review_id)
            self.observations_registered += 1
        return int(review_id)

    # -- collection ---------------------------------------------------
    def observe(
        self,
        frame: np.ndarray,
        frame_number: int,
        timestamp_sec: float,
        tracked: Sequence[Mapping[str, Any]],
    ) -> None:
        """Offer this frame's tracked boxes to every pending observation."""
        if not self._pending:
            return
        now = time.monotonic()
        frame_h, frame_w = frame.shape[:2]
        boxes = [(int(d.get("track_id", -1)), _epoch_of(d), d) for d in tracked]

        ready: list[PlateObservation] = []
        with self._lock:
            snapshot = list(self._pending.items())

        for review_id, observation in snapshot:
            if observation.finished:
                continue
            match = None
            for tid, epoch, det in boxes:
                if tid != int(observation.track_id):
                    continue
                if observation.identity_epoch is not None and epoch is not None:
                    if epoch != observation.identity_epoch:
                        # ByteTrack id reused for a different vehicle.
                        match = "epoch_mismatch"
                        break
                match = det
                break
            if match == "epoch_mismatch":
                continue
            if match is None:
                continue

            det = match
            vehicle_box = (
                int(round(float(det["bbox_x"]))),
                int(round(float(det["bbox_y"]))),
                int(round(float(det["bbox_x"]) + float(det["bbox_w"]))),
                int(round(float(det["bbox_y"]) + float(det["bbox_h"]))),
            )
            overlap = _max_overlap_iou(vehicle_box, [b for _t, _e, b in boxes], int(det["track_id"]))
            if overlap > float(self.settings.quality["max_vehicle_overlap_iou"]):
                observation.association_uncertain = True
                observation.rejection_reasons.append("vehicle_overlap_ambiguous")
                continue
            if not observation.allow_sample(float(timestamp_sec), now, self.settings.bounds):
                if now > observation.deadline(float(self.settings.bounds["collection_window_sec"])):
                    ready.append(observation)
                continue

            crop, rect = crop_from_box(frame, vehicle_box)
            if crop is None or crop.size == 0:
                observation.rejection_reasons.append("vehicle_crop_unavailable")
                ready.append(observation)
                continue
            nbytes = int(crop.nbytes)
            if not self.worker.memory.try_reserve(nbytes):
                self.memory_rejections += 1
                observation.rejection_reasons.append("retained_crop_memory_budget_exhausted")
                observation.truncated["collection"] = True
                ready.append(observation)
                continue

            sample_id = f"s_{int(observation.review_id)}_{int(frame_number)}"
            sample = PlateCropSample(
                sample_id=sample_id,
                frame_number=int(frame_number),
                timestamp_sec=float(timestamp_sec),
                frame_width=int(frame_w),
                frame_height=int(frame_h),
                vehicle_box=vehicle_box,
                crop_origin=(rect[0], rect[1]),
                crop_width=int(crop.shape[1]),
                crop_height=int(crop.shape[0]),
                image=crop,
                nbytes=nbytes,
                reserved=True,
            )
            observation.samples.append(sample)
            self.samples_collected += 1
            if len(observation.samples) >= int(self.settings.bounds["max_crops_per_observation"]):
                ready.append(observation)

        for observation in ready:
            self._finish(observation, PlateAttemptOutcome.QUEUED, None)

    def flush(self, reason: str = "end_of_source") -> None:
        """End every open observation. ``reason`` is recorded in the manifest."""
        with self._lock:
            snapshot = list(self._pending.items())
            self._pending.clear()
            self._keys.clear()
        for _review_id, observation in snapshot:
            if reason in ("cancelled", "cancelled_by_user", "cancelled_run"):
                observation.truncated["collection"] = True
                observation.rejection_reasons.append(f"collection_{reason}")
            self._finish(observation, PlateAttemptOutcome.QUEUED, reason)

    def _finish(self, observation: PlateObservation, _state: PlateAttemptOutcome, reason: str | None) -> None:
        if observation.finished:
            return
        observation.finished = True
        with self._lock:
            self._pending.pop(observation.review_id, None)
            for key, rid in list(self._keys.items()):
                if rid == observation.review_id:
                    self._keys.pop(key, None)
        attempt_id = manifests.new_attempt_id()
        if reason:
            observation.rejection_reasons.append(f"collection_ended:{reason}")
        # Persist the attributable vehicle crops before inference so an admin can
        # always see what the machine actually looked at, read or not. The
        # in-memory pixels stay reserved until the worker finishes the job, so
        # the retained-crop budget keeps covering queued work.
        for sample in observation.samples:
            try:
                sample.vehicle_crop_ref = manifests.save_crop(
                    attempt_id,
                    f"vehicle_{sample.sample_id}.jpg",
                    sample.image,
                    params=[int(_cv_imwrite_jpeg_quality()), 92],
                )
            except Exception:  # noqa: BLE001
                logger.warning("vehicle crop write failed for %s", sample.sample_id)
        self.worker.submit(attempt_id, observation)

    def stats(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "run_key": self.run_key,
            "live_session_id": self.live_session_id,
            "observations_registered": self.observations_registered,
            "duplicates_skipped": self.duplicates_skipped,
            "samples_collected": self.samples_collected,
            "memory_rejections": self.memory_rejections,
            "pending": len(self._pending),
        }


def _cv_imwrite_jpeg_quality() -> int:
    import cv2

    return int(cv2.IMWRITE_JPEG_QUALITY)


def _epoch_of(det: Mapping[str, Any]) -> int | None:
    value = det.get("track_identity_epoch")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _max_overlap_iou(
    target: Sequence[float],
    others: Sequence[Mapping[str, Any]],
    target_track_id: int,
) -> float:
    worst = 0.0
    for det in others:
        if int(det.get("track_id", -1)) == target_track_id:
            continue
        box = (
            int(round(float(det["bbox_x"]))),
            int(round(float(det["bbox_y"]))),
            int(round(float(det["bbox_x"]) + float(det["bbox_w"]))),
            int(round(float(det["bbox_y"]) + float(det["bbox_h"]))),
        )
        worst = max(worst, iou(target, box))
    return worst
