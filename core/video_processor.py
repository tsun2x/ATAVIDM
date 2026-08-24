"""Detection pipeline orchestrator (manuscript Ch3 workflow).

Video Acquisition -> Preprocessing (frame extraction/skip) -> YOLOv8m Detection
-> ByteTrack Tracking -> Rule-Based Violation Detection -> Evidence Recording
-> Review Queue (manual validation) -> Centralized Database.

Every violation event is routed to the manual review queue — the manuscript's
human-in-the-loop policy means violations are only confirmed by an operator.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from typing import Any, Callable

import cv2

from core.annotated_writer import AnnotatedVideoWriter, AnnotatedWriterError
from core.detection_config import DEFAULT_RULE_PARAMETERS, confidence_band
from core.detector import Detector, DetectorError, resolve_weights_path
from core.evidence import save_evidence_snapshot, save_vehicle_crop
from core.frame_annotate import annotate_frame
from core.geometry_profile import build_geometry_profile
from core.model_capability import extract_model_class_names, baseline_capability_notes
from core.processing_preview import preview_hub
from core.processing_progress import ProgressTracker
from core.temporal_evidence import (
    EVIDENCE_POST_SEC,
    EVIDENCE_PRE_SEC,
    EvidenceEpisode,
    TemporalEvidenceBuffer,
    evidence_timing_dict,
)
from core.tracker import TrackState
from core.violation_config import load_enabled_violations
from core.violation_engine import (
    RuleEngineState,
    ViolationEvent,
    build_processing_diagnostics,
)
from core.violation_engine import evaluate_detection_rules
from core.zone_config import parse_zones_json
from database import db

_DETECTION_BATCH_SIZE = 500
logger = logging.getLogger(__name__)


@dataclass
class ProcessVideoResult:
    """Return value of ``process_video`` including diagnostics for the run row.

    List-compatible: ``list(result)``, iteration, and indexing expose ``events``
    so former list-return callers keep working.
    """

    events: list[ViolationEvent] = field(default_factory=list)
    diagnostics_json: str | None = None
    geometry_snapshot_json: str | None = None
    failed: bool = False
    error_message: str | None = None
    detection_records: int = 0
    unique_tracks: int | None = None
    class_counts: dict[str, int] = field(default_factory=dict)
    violation_candidates: int = 0
    annotated_video_path: str | None = None
    annotated_video_ready: bool = False
    model_identifier: str | None = None
    frames_processed: int = 0
    total_frames: int | None = None
    source_duration_sec: float | None = None
    effective_output_fps: float | None = None
    progress_snapshot: dict[str, Any] | None = None

    def __iter__(self):
        return iter(self.events)

    def __len__(self) -> int:
        return len(self.events)

    def __getitem__(self, index):
        return self.events[index]


class ProcessVideoError(RuntimeError):
    """Pipeline failure that still carries partial diagnostics for the run row."""

    def __init__(
        self,
        message: str,
        *,
        diagnostics_json: str | None = None,
        geometry_snapshot_json: str | None = None,
        progress_snapshot: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.diagnostics_json = diagnostics_json
        self.geometry_snapshot_json = geometry_snapshot_json
        self.progress_snapshot = progress_snapshot


def load_rule_parameters() -> dict[str, Any]:
    """Merge persisted system settings over the manuscript defaults."""
    params: dict[str, Any] = dict(DEFAULT_RULE_PARAMETERS)
    stored = db.get_all_settings()
    for key, default in DEFAULT_RULE_PARAMETERS.items():
        if key not in stored:
            continue
        raw = stored[key]
        try:
            if isinstance(default, bool):
                params[key] = raw.lower() in ("1", "true", "yes")
            elif isinstance(default, int):
                params[key] = int(float(raw))
            elif isinstance(default, float):
                params[key] = float(raw)
            else:
                params[key] = raw
        except (TypeError, ValueError):
            pass  # keep the default when a stored value is malformed
    return params


def get_processing_context(video_id: int) -> dict[str, Any]:
    """
    Load everything the detection pipeline needs for a video.
    Zones always come from the video annotation — never hardcoded.
    """
    video = db.get_video(video_id)
    if video is None:
        raise ValueError(f"Video {video_id} not found.")

    annotation = db.get_annotation_by_video(video_id)
    zones = parse_zones_json(annotation["zones_json"]) if annotation else {}

    template = None
    if video.get("template_id"):
        template = db.get_zone_template(video["template_id"])

    return {
        "video_id": video_id,
        "video_path": video["filepath"],
        "status": video.get("status"),
        "recorded_at": video.get("recorded_at"),
        "duration_sec": video.get("duration_sec"),
        "zones": zones,
        "annotation_id": annotation["id"] if annotation else None,
        "template_id": video.get("template_id"),
        "template_name": template["template_name"] if template else None,
    }


def _base_datetime(recorded_at: str | None) -> datetime | None:
    """Parse the video's recording start so time-based rules use scene time."""
    if not recorded_at:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(recorded_at, fmt)
        except ValueError:
            continue
    return None


def _scene_time(base: datetime | None, timestamp_sec: float) -> dtime | None:
    """Return recording-scene clock, or None when recording datetime is unknown.

    Upload/processing wall-clock must never substitute for recording time.
    """
    if base is None:
        return None
    return (base + timedelta(seconds=timestamp_sec)).time()


def _safe_model_identifier(weights_path: str | None) -> str:
    """Public model label without exposing internal filesystem paths."""
    if not weights_path:
        return "YOLOv8m"
    name = Path(weights_path).name
    return f"YOLOv8m ({name})"


def _apply_finalized_episode(ep: EvidenceEpisode) -> None:
    """Write clip/sequence/timing back onto the associated review row."""
    if ep.review_id is None:
        return
    db.update_review_temporal_evidence(
        ep.review_id,
        evidence_clip_path=ep.clip_path,
        evidence_sequence_dir=ep.sequence_dir,
        episode_start_sec=ep.episode_start_sec,
        episode_end_sec=ep.episode_end_sec,
        evidence_pre_sec=EVIDENCE_PRE_SEC,
        evidence_post_sec=EVIDENCE_POST_SEC,
    )


def _persist_event(
    event: ViolationEvent,
    frame: Any,
    detection: dict[str, Any] | None,
    video_id: int,
    evidence_buf: TemporalEvidenceBuffer,
    *,
    processing_run_id: int | None = None,
) -> int:
    """Save still/crop, insert review row, begin temporal episode linked to that row."""
    evidence_path = None
    vehicle_evidence_path = None
    if detection is not None:
        evidence_path = save_evidence_snapshot(
            frame,
            detection,
            event.violation_type,
            source_key=f"video_{video_id}",
            frame_number=event.frame_number,
            violation_confidence=event.violation_confidence,
            detection_confidence=event.detection_confidence,
        )
        vehicle_evidence_path = save_vehicle_crop(
            frame,
            detection,
            source_key=f"video_{video_id}",
            frame_number=event.frame_number,
        )

    band = confidence_band(event.violation_confidence)
    factors_json = json.dumps(event.contributing_factors)
    unavailable_json = json.dumps(list(event.unavailable_factors))
    review_id = db.insert_review_queue(
        video_id=video_id,
        track_id=event.track_id,
        violation_type=event.violation_type,
        confidence=event.confidence,
        detection_confidence=event.detection_confidence,
        violation_confidence=event.violation_confidence,
        evidence_sufficiency=event.evidence_sufficiency,
        frame_number=event.frame_number,
        evidence_path=evidence_path,
        reason_log=f"[{band}] {event.reason_log}",
        vehicle_class=event.vehicle_class,
        timestamp_sec=event.timestamp_sec,
        vehicle_evidence_path=vehicle_evidence_path,
        plate_status="not_attempted",
        evidence_pre_sec=EVIDENCE_PRE_SEC,
        evidence_post_sec=EVIDENCE_POST_SEC,
        episode_start_sec=float(event.timestamp_sec),
        episode_end_sec=None,
        contributing_factors_json=factors_json,
        unavailable_factors_json=unavailable_json,
        processing_run_id=processing_run_id,
    )

    episode = evidence_buf.begin_episode(
        violation_type=event.violation_type,
        track_id=event.track_id,
        confirmed_at_sec=event.timestamp_sec,
        episode_start_sec=float(event.timestamp_sec),
        still_path=evidence_path,
        vehicle_crop_path=vehicle_evidence_path,
        review_id=review_id,
    )
    event.evidence_timing = evidence_timing_dict(episode)
    return int(review_id)


def process_video(
    video_id: int,
    progress_callback: Callable[[int, int], None] | None = None,
    enabled_violations: tuple[str, ...] | None = None,
    processing_run_id: int | str | None = None,
    progress_tracker: ProgressTracker | None = None,
) -> ProcessVideoResult:
    """
    Run the full YOLOv8m -> ByteTrack -> rule engine pipeline on one video.

    Detections are persisted per processed frame; every violation event is
    snapshotted and queued for manual review. Returns ``ProcessVideoResult``
    with events plus diagnostics/geometry JSON for the processing_runs row.

    ``enabled_violations`` is the per-run snapshot. ``None`` (the default)
    means "use the current global enabled set"; an explicit (possibly empty)
    tuple is honored as-is so a run can disable every violation type.

    Live preview is published through ``preview_hub`` for the same run — it
    never starts a second inference job.
    """
    ctx = get_processing_context(video_id)
    params = load_rule_parameters()
    enabled_violations = load_enabled_violations() if enabled_violations is None else enabled_violations
    base_dt = _base_datetime(ctx["recorded_at"])
    recording_time_known = base_dt is not None

    diagnostics_json: str | None = None
    geometry_snapshot_json: str | None = None
    writer: AnnotatedVideoWriter | None = None
    cap: cv2.VideoCapture | None = None
    model_identifier: str | None = None
    effective_fps: float | None = None
    source_duration: float | None = ctx.get("duration_sec")
    detection_total = 0
    class_counts: dict[str, int] = {}
    track_ids: set[int] = set()
    frames_done = 0
    total_frames = 0

    def _snap() -> dict[str, Any] | None:
        return progress_tracker.snapshot() if progress_tracker else None

    def _legacy_progress(cur: int, tot: int) -> None:
        if progress_callback:
            try:
                progress_callback(cur, tot)
            except Exception:
                logger.exception("progress_callback failed")

    try:
        if progress_tracker:
            progress_tracker.set_stage("opening_video")

        cap = cv2.VideoCapture(ctx["video_path"])
        if not cap.isOpened():
            raise ProcessVideoError(
                f"Cannot open video file: {Path(ctx['video_path']).name}",
                progress_snapshot=_snap(),
            )

        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0) or 30.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        frame_skip = max(int(params["frame_skip"]), 1)
        conf_threshold = float(params["confidence_threshold"])
        effective_fps = float(fps) / float(frame_skip)
        if effective_fps <= 0:
            effective_fps = float(fps)
        if total_frames > 0 and fps > 0:
            source_duration = total_frames / fps
        if progress_tracker:
            progress_tracker.set_total_frames(total_frames)
            progress_tracker.set_source_duration(source_duration)

        geometry = build_geometry_profile(
            frame_w or 1920,
            frame_h or 1080,
            rule_params=params,
            zone_polygons={k: v for k, v in ctx["zones"].items() if v},
        )
        geometry_snapshot_json = json.dumps(geometry.snapshot())

        if progress_tracker:
            progress_tracker.set_stage("loading_model")

        weights_path, _is_custom = resolve_weights_path()
        model_identifier = _safe_model_identifier(weights_path)
        if progress_tracker:
            progress_tracker.set_model_identifier(model_identifier)

        detector = Detector()
        try:
            detector.load()
        except DetectorError as exc:
            raise ProcessVideoError(
                str(exc),
                geometry_snapshot_json=geometry_snapshot_json,
                progress_snapshot=_snap(),
            ) from exc
        except Exception as exc:
            # Preserve geometry when model load fails for any reason.
            raise ProcessVideoError(
                str(exc),
                geometry_snapshot_json=geometry_snapshot_json,
                progress_snapshot=_snap(),
            ) from exc

        track_state = TrackState(stationary_px=geometry.stationary_px_per_sec())
        rule_state = RuleEngineState()

        model_classes = extract_model_class_names(detector)
        diagnostics = build_processing_diagnostics(
            model_classes=model_classes,
            enabled_violations=tuple(enabled_violations),
            geometry=geometry,
            state=rule_state,
        )
        for note in baseline_capability_notes(model_classes):
            diagnostics.notes.append(note)
            rule_state.diagnostics.append(note)
            logger.warning("%s", note)
        for note in diagnostics.notes:
            logger.info("processing diagnostic: %s", note)
        for rule_cap in diagnostics.rule_capabilities:
            if not rule_cap.automatic_evaluation:
                logger.warning("%s", rule_cap.notes)
                rule_state.diagnostics.append(rule_cap.notes)
            rule_state.capability[rule_cap.rule_name] = rule_cap

        diagnostics_json = json.dumps(diagnostics.as_dict())
        if progress_tracker:
            progress_tracker.add_diagnostics(list(diagnostics.notes))

        evidence_buf = TemporalEvidenceBuffer(
            source_key=f"video_{video_id}",
            fps=effective_fps,
            max_frames=max(int(max(effective_fps, 0.1) * (EVIDENCE_PRE_SEC + EVIDENCE_POST_SEC + 30)), 90),
            run_id=processing_run_id,
        )

        run_id_for_writer = processing_run_id if processing_run_id is not None else f"video_{video_id}"
        try:
            writer = AnnotatedVideoWriter(
                run_id_for_writer,
                width=frame_w or 640,
                height=frame_h or 360,
                fps=effective_fps,
            )
        except AnnotatedWriterError as exc:
            raise ProcessVideoError(str(exc), progress_snapshot=_snap()) from exc

        db.update_video(video_id, status="processing")
        if progress_tracker:
            progress_tracker.set_stage("processing")

        events: list[ViolationEvent] = []
        detection_batch: list[dict[str, Any]] = []
        frame_number = -1
        last_ts = 0.0
        run_id_int = int(processing_run_id) if processing_run_id is not None else None

        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frame_number += 1
            if frame_number % frame_skip != 0:
                # Still advance monotonic source-frame progress for ETA.
                if progress_tracker and total_frames:
                    progress_tracker.update_frame(frame_number + 1)
                _legacy_progress(frame_number + 1, total_frames or frame_number + 1)
                continue

            timestamp_sec = frame_number / fps
            last_ts = timestamp_sec
            for ep in evidence_buf.push(frame, frame_number, timestamp_sec):
                _apply_finalized_episode(ep)

            raw = detector.track_frame(frame, conf=conf_threshold, timestamp_sec=timestamp_sec)
            tracked = track_state.update(raw, now=timestamp_sec)

            frame_class_delta: dict[str, int] = {}
            frame_track_ids: set[int] = set()
            for det in tracked:
                row = {
                    "video_id": video_id,
                    "frame_number": frame_number,
                    "timestamp_sec": timestamp_sec,
                    "track_id": det["track_id"],
                    "class_label": det["class_label"],
                    "confidence": det["confidence"],
                    "bbox_x": det["bbox_x"],
                    "bbox_y": det["bbox_y"],
                    "bbox_w": det["bbox_w"],
                    "bbox_h": det["bbox_h"],
                    "processing_run_id": run_id_int,
                }
                detection_batch.append(row)
                label = str(det.get("class_label") or "unknown")
                frame_class_delta[label] = frame_class_delta.get(label, 0) + 1
                class_counts[label] = class_counts.get(label, 0) + 1
                if det.get("track_id") is not None:
                    tid = int(det["track_id"])
                    frame_track_ids.add(tid)
                    track_ids.add(tid)
            detection_total += len(tracked)

            if len(detection_batch) >= _DETECTION_BATCH_SIZE:
                db.bulk_insert_detections(detection_batch)
                detection_batch = []

            scene_t = _scene_time(base_dt, timestamp_sec)
            frame_events = evaluate_detection_rules(
                tracked,
                rule_state,
                frame_number,
                zones=ctx["zones"],
                params=params,
                now_time=scene_t,
                enabled_violations=enabled_violations,
                geometry=geometry,
                model_classes=model_classes,
                recording_time_known=recording_time_known,
                frame_size=(frame_w, frame_h),
                now_sec=timestamp_sec,
            )
            by_track = {int(d["track_id"]): d for d in tracked}
            viol_ids = {int(e.track_id) for e in frame_events if e.track_id is not None}
            for event in frame_events:
                _persist_event(
                    event,
                    frame,
                    by_track.get(event.track_id),
                    video_id,
                    evidence_buf,
                    processing_run_id=run_id_int,
                )
            events.extend(frame_events)

            annotated = annotate_frame(
                frame,
                tracked,
                zones=ctx["zones"],
                violation_track_ids=viol_ids,
            )
            if writer is not None:
                writer.write(annotated)

            # Gate JPEG encode on active viewers + wall-clock display rate.
            # Inference and annotated writer are not throttled.
            try:
                preview_hub.publish_frame(video_id, annotated)
            except Exception:
                logger.exception("preview publish failed for video %s", video_id)

            for vtype, tid, cleared_at in rule_state.consume_cleared():
                evidence_buf.end_episode(vtype, tid, cleared_at)
            for ep in evidence_buf.finalize_due(timestamp_sec):
                _apply_finalized_episode(ep)

            frames_done = frame_number + 1
            if progress_tracker:
                progress_tracker.update_frame(
                    frames_done,
                    detection_delta=len(tracked),
                    class_counts_delta=frame_class_delta,
                    track_ids=frame_track_ids,
                    violation_delta=len(frame_events),
                )
            _legacy_progress(frames_done, total_frames or frames_done)

        if detection_batch:
            db.bulk_insert_detections(detection_batch)

        if progress_tracker:
            progress_tracker.set_stage("finalizing")

        for ep in evidence_buf.finalize_all(last_ts):
            _apply_finalized_episode(ep)
            logger.info(
                "temporal evidence finalized: %s track=%s review=%s clip=%s seq=%s",
                ep.violation_type,
                ep.track_id,
                ep.review_id,
                ep.clip_path,
                ep.sequence_dir,
            )

        annotated_path: str | None = None
        annotated_ready = False
        if writer is not None:
            try:
                final = writer.finalize()
                annotated_path = str(final)
                annotated_ready = True
            except AnnotatedWriterError as exc:
                writer.abort()
                raise ProcessVideoError(
                    str(exc),
                    diagnostics_json=diagnostics_json,
                    geometry_snapshot_json=geometry_snapshot_json,
                    progress_snapshot=_snap(),
                ) from exc

        db.mark_video_processed(video_id)
        result = ProcessVideoResult(
            events=events,
            diagnostics_json=diagnostics_json,
            geometry_snapshot_json=geometry_snapshot_json,
            detection_records=detection_total,
            unique_tracks=len(track_ids) if track_ids else 0,
            class_counts=dict(class_counts),
            violation_candidates=len(events),
            annotated_video_path=annotated_path,
            annotated_video_ready=annotated_ready,
            model_identifier=model_identifier,
            frames_processed=frames_done or total_frames,
            total_frames=total_frames or None,
            source_duration_sec=source_duration,
            effective_output_fps=effective_fps,
        )
        if progress_tracker:
            url = (
                f"/api/videos/{video_id}/annotated/{processing_run_id}"
                if annotated_ready and processing_run_id is not None
                else None
            )
            progress_tracker.mark_completed(annotated_ready=annotated_ready, annotated_url=url)
            result.progress_snapshot = progress_tracker.snapshot()
        return result
    except ProcessVideoError:
        if writer is not None:
            writer.abort()
        raise
    except Exception as exc:
        try:
            if "evidence_buf" in locals():
                evidence_buf.abort()
        except Exception:
            pass
        if writer is not None:
            writer.abort()
        raise ProcessVideoError(
            str(exc),
            diagnostics_json=diagnostics_json,
            geometry_snapshot_json=geometry_snapshot_json,
            progress_snapshot=_snap(),
        ) from exc
    finally:
        if cap is not None:
            cap.release()
        if writer is not None:
            writer.close()
