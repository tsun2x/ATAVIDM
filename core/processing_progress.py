"""Thread-safe processing progress state for uploaded-video jobs."""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any

STAGES = (
    "queued",
    "loading_model",
    "opening_video",
    "processing",
    "finalizing",
    "completed",
    "failed",
)


@dataclass
class ProcessingStatus:
    run_id: int | None = None
    video_id: int | None = None
    state: str | None = None  # processing|done|error (legacy job_state)
    stage: str = "queued"
    queued: bool = False
    queue_position: int | None = None
    frames_processed: int = 0
    total_frames: int | None = None
    progress_percent: float = 0.0
    elapsed_sec: float | None = None
    processing_fps: float | None = None
    eta_sec: float | None = None
    detection_records: int = 0
    unique_tracks: int | None = None
    class_counts: dict[str, int] = field(default_factory=dict)
    violation_candidates: int = 0
    annotated_video_ready: bool = False
    annotated_video_url: str | None = None
    error: str | None = None
    model_identifier: str | None = None
    viewer_mode: str | None = None
    source_duration_sec: float | None = None
    started_at: str | None = None
    finished_at: str | None = None
    diagnostics: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        # Stable contract: omit invented ETA
        if data.get("eta_sec") is None:
            data["eta_sec"] = None
        if data.get("unique_tracks") is None:
            data["unique_tracks"] = None
        return data


class ProgressTracker:
    """Monotonic frame/percent progress for a single processing run."""

    def __init__(
        self,
        *,
        run_id: int,
        video_id: int,
        viewer_mode: str = "background",
    ) -> None:
        self._lock = threading.Lock()
        self._started_mono: float | None = None
        self._track_ids: set[int] = set()
        self._status = ProcessingStatus(
            run_id=run_id,
            video_id=video_id,
            state="processing",
            stage="queued",
            viewer_mode=viewer_mode,
        )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._status.to_dict()

    def set_stage(self, stage: str) -> None:
        if stage not in STAGES:
            raise ValueError(f"Unknown stage: {stage}")
        with self._lock:
            self._status.stage = stage
            if stage in ("loading_model", "opening_video", "processing") and self._started_mono is None:
                self._started_mono = time.monotonic()
            self._refresh_timing_locked()

    def set_total_frames(self, total: int) -> None:
        with self._lock:
            if total >= 0:
                self._status.total_frames = int(total)
            self._refresh_timing_locked()

    def set_model_identifier(self, identifier: str | None) -> None:
        with self._lock:
            self._status.model_identifier = identifier

    def set_source_duration(self, duration_sec: float | None) -> None:
        with self._lock:
            self._status.source_duration_sec = duration_sec

    def set_queued(self, queued: bool, position: int | None = None) -> None:
        with self._lock:
            self._status.queued = bool(queued)
            self._status.queue_position = position

    def set_error(self, message: str) -> None:
        with self._lock:
            self._status.error = message
            self._status.stage = "failed"
            self._status.state = "error"
            self._refresh_timing_locked()

    def mark_completed(self, *, annotated_ready: bool, annotated_url: str | None) -> None:
        with self._lock:
            self._status.stage = "completed"
            self._status.state = "done"
            self._status.annotated_video_ready = bool(annotated_ready)
            self._status.annotated_video_url = annotated_url
            if self._status.total_frames and self._status.frames_processed < self._status.total_frames:
                self._status.frames_processed = self._status.total_frames
                self._status.progress_percent = 100.0
            elif self._status.total_frames:
                self._status.progress_percent = 100.0
            self._refresh_timing_locked()
            self._status.eta_sec = 0.0 if self._status.progress_percent >= 100 else None

    def update_frame(
        self,
        frames_processed: int,
        *,
        detection_delta: int = 0,
        class_counts_delta: dict[str, int] | None = None,
        track_ids: set[int] | None = None,
        violation_delta: int = 0,
    ) -> None:
        with self._lock:
            # Monotonic: never decrease frames or percent for a run.
            frames_processed = max(int(frames_processed), self._status.frames_processed)
            self._status.frames_processed = frames_processed
            self._status.detection_records += max(0, int(detection_delta))
            self._status.violation_candidates += max(0, int(violation_delta))
            if class_counts_delta:
                for label, count in class_counts_delta.items():
                    self._status.class_counts[label] = self._status.class_counts.get(label, 0) + max(0, int(count))
            if track_ids:
                self._track_ids.update(int(t) for t in track_ids)
                self._status.unique_tracks = len(self._track_ids)
            total = self._status.total_frames
            if total and total > 0:
                pct = min(100.0, (frames_processed / total) * 100.0)
                self._status.progress_percent = max(self._status.progress_percent, pct)
            self._status.stage = "processing"
            self._refresh_timing_locked()

    def add_diagnostics(self, notes: list[str] | None) -> None:
        if not notes:
            return
        with self._lock:
            for note in notes:
                if note and note not in self._status.diagnostics:
                    self._status.diagnostics.append(note)

    def _refresh_timing_locked(self) -> None:
        if self._started_mono is None:
            return
        elapsed = max(0.0, time.monotonic() - self._started_mono)
        self._status.elapsed_sec = round(elapsed, 3)
        processed = self._status.frames_processed
        if elapsed > 0 and processed > 0:
            fps = processed / elapsed
            self._status.processing_fps = round(fps, 3)
            total = self._status.total_frames
            if total and total > processed and fps > 0:
                remaining = (total - processed) / fps
                self._status.eta_sec = round(remaining, 1)
            elif total and processed >= total:
                self._status.eta_sec = 0.0
            else:
                self._status.eta_sec = None
        else:
            self._status.processing_fps = None
            self._status.eta_sec = None
