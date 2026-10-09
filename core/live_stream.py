"""Live CCTV (RTSP) stream processing (manuscript Ch1 Scope, Ch3 Data Source).

Each active camera runs one background worker: RTSP capture -> YOLOv8m +
ByteTrack -> rule engine -> evidence + review queue, and keeps the latest
annotated frame available as JPEG for the Live Monitor MJPEG feed.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from typing import Any

import cv2

logger = logging.getLogger(__name__)

from core.detection_config import (
    VIOLATION_NO_SIDE_MIRROR,
    VIOLATION_TRUCK_BAN,
    confidence_band,
)
from core.detector import Detector, DetectorError, enforce_object_class_contract
from core.evidence import save_evidence_snapshot, save_vehicle_crop
from core.frame_annotate import TrajectoryOverlay, annotate_frame
from core.gpu_inference_slot import PRIORITY_LIVE_FRAME, gpu_inference_slot
from core.model_capability import extract_model_class_names
from core.scene_annotation import load_scene_annotation
from core.tracker import TrackState
from core.video_processor import load_rule_parameters
from core.violation_config import load_enabled_violations
from core.violation_engine import RuleDiagnostics, RuleEngineState, evaluate_detection_rules
from database import db


def live_capability_diagnostics(capabilities: dict[str, Any]) -> list[str]:
    """Visible fail-closed prerequisites for live-only time/evidence gates."""
    notes = []
    for rule in (VIOLATION_TRUCK_BAN, VIOLATION_NO_SIDE_MIRROR):
        capability = capabilities.get(rule)
        if capability is not None and not capability.automatic_evaluation and capability.notes:
            notes.append(capability.notes)
    return notes


def live_review_evidence_fields(event: Any) -> dict[str, Any]:
    """Keep live review confidence fields aligned with uploaded-video events."""
    return {
        "detection_confidence": event.detection_confidence,
        "violation_confidence": event.violation_confidence,
        "evidence_sufficiency": event.evidence_sufficiency,
        "contributing_factors_json": json.dumps(event.contributing_factors),
        "unavailable_factors_json": json.dumps(list(event.unavailable_factors)),
    }


def _consume_live_diagnostics(
    target: list[str], seen: set[str], state: RuleEngineState, revision: int
) -> int:
    new_notes, revision = state.diagnostics.since(revision)
    for note in live_capability_diagnostics(state.capability) + new_notes:
        if note in seen:
            continue
        seen.add(note)
        target.append(note)
        if len(target) > RuleDiagnostics.MAX_ITEMS:
            seen.discard(target.pop(0))
    return revision


class LiveStreamWorker(threading.Thread):
    def __init__(self, camera: dict[str, Any]) -> None:
        super().__init__(daemon=True, name=f"camera-{camera['id']}")
        self.camera = camera
        self.scene_document = load_scene_annotation(camera.get("zones_json"))
        self.rule_scene = self.scene_document.to_rule_context()
        self.zones = dict(self.rule_scene.legacy_zones)
        try:
            self.trajectory_overlay = TrajectoryOverlay(trace_length=30)
        except Exception:
            logger.exception(
                "camera %s trajectory overlay unavailable; continuing without track trails",
                camera.get("id"),
            )
            self.trajectory_overlay = None
        self._stop_event = threading.Event()
        self._frame_lock = threading.Lock()
        self._latest_jpeg: bytes | None = None
        self.status = "starting"
        self.error: str | None = None
        self.diagnostics: list[str] = []
        self._diagnostic_seen: set[str] = set()

    def stop(self) -> None:
        self._stop_event.set()

    def latest_jpeg(self) -> bytes | None:
        with self._frame_lock:
            return self._latest_jpeg

    def _annotate(self, frame: Any, tracked: list[dict[str, Any]]) -> Any:
        try:
            return annotate_frame(
                frame,
                tracked,
                scene=self.scene_document,
                trajectory_overlay=self.trajectory_overlay,
            )
        except Exception:
            if self.trajectory_overlay is None:
                raise
            logger.exception(
                "camera %s trajectory overlay failed; retrying without track trails",
                self.camera.get("id"),
            )
            self.trajectory_overlay = None
            return annotate_frame(frame, tracked, scene=self.scene_document)

    def _publish(self, frame: Any) -> None:
        ok, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ok:
            with self._frame_lock:
                self._latest_jpeg = buffer.tobytes()

    def run(self) -> None:  # noqa: C901 — single sequential pipeline loop
        params = load_rule_parameters()
        enabled_violations = load_enabled_violations()
        try:
            detector = Detector()
            detector.load()
        except DetectorError as exc:
            self.status = "error"
            self.error = str(exc)
            return

        # Class-roster contract before the capture loop: a 15-class
        # object-roster checkpoint must expose the exact declared ID->name order.
        try:
            enforce_object_class_contract(detector)
        except DetectorError as exc:
            self.status = "error"
            self.error = str(exc)
            return

        model_classes = extract_model_class_names(detector)
        gpu_slot = gpu_inference_slot()
        started = time.monotonic()

        # Experimental local plate OCR (disabled by default). The collector
        # samples at most two frames per second for at most two seconds after a
        # review observation exists, and all detector/OCR calls run on a
        # background worker: never on the live frame path and never inside the
        # vehicle GPU reservation above.
        plate_collector = None
        plate_runtime = None
        live_session_id = f"{uuid.uuid4().hex}_{self.camera['id']}"
        try:
            from core.plate_runtime import get_runtime

            plate_runtime = get_runtime()
            if plate_runtime is not None:
                plate_collector = plate_runtime.new_collector(
                    source="camera",
                    run_key=f"camera_{self.camera['id']}",
                    live_session_id=live_session_id,
                )
        except Exception:  # noqa: BLE001 - the experiment must never break a stream
            logger.exception("plate OCR collector unavailable; continuing without plate OCR")
            plate_collector = None

        cap = cv2.VideoCapture(self.camera["rtsp_url"])
        if not cap.isOpened():
            if plate_collector is not None:
                plate_collector.flush("stream_start_failed")
            cap.release()
            self.status = "error"
            self.error = f"Cannot open stream: {self.camera['rtsp_url']}"
            return

        self.status = "live"
        track_state = TrackState(stationary_px=float(params["stationary_px"]))
        rule_state = RuleEngineState()
        rule_diagnostic_revision = 0
        frame_skip = max(int(params["frame_skip"]), 1)
        conf_threshold = float(params["confidence_threshold"])
        frame_number = -1
        skipped_frames = 0

        try:
            while not self._stop_event.is_set():
                ok, frame = cap.read()
                if not ok:
                    self.status = "reconnecting"
                    cap.release()
                    if plate_collector is not None:
                        plate_collector.flush("stream_reconnected")
                    time.sleep(2)
                    cap = cv2.VideoCapture(self.camera["rtsp_url"])
                    if cap.isOpened():
                        # New session identity isolates all post-reconnect
                        # observations from pending crops in the old session.
                        live_session_id = f"{uuid.uuid4().hex}_{self.camera['id']}"
                        if plate_runtime is not None:
                            try:
                                plate_collector = plate_runtime.new_collector(
                                    source="camera",
                                    run_key=f"camera_{self.camera['id']}",
                                    live_session_id=live_session_id,
                                )
                            except Exception:  # noqa: BLE001 - OCR cannot stop live tracking
                                logger.exception("plate collector unavailable after camera reconnect")
                                plate_collector = None
                        self.status = "live"
                    continue

                frame_number += 1
                if frame_number % frame_skip != 0:
                    continue

                timestamp_sec = time.monotonic() - started
                # Lowest-priority user of the single GPU: never wait and never
                # queue. If an uploaded-video job, a detail crop batch, or a
                # manual scan holds the device, this frame is skipped instead of
                # starting a second YOLOv8 pass.
                with gpu_slot.reservation(
                    f"live-frame:camera-{self.camera['id']}",
                    timeout=0.0,
                    priority=PRIORITY_LIVE_FRAME,
                ) as acquired:
                    if not acquired:
                        skipped_frames += 1
                        continue
                    raw = detector.track_frame(frame, conf=conf_threshold, timestamp_sec=timestamp_sec)
                tracked = track_state.update(raw, now=timestamp_sec)
                self._publish(self._annotate(frame, tracked))

                events = evaluate_detection_rules(
                    tracked, rule_state, frame_number,
                    zones=self.zones, params={**params, "_live_mode": True},
                    enabled_violations=enabled_violations,
                    now_sec=timestamp_sec,
                    scene=self.rule_scene,
                    model_classes=model_classes,
                    history=track_state.history_view(now=timestamp_sec),
                )
                rule_diagnostic_revision = _consume_live_diagnostics(
                    self.diagnostics, self._diagnostic_seen, rule_state,
                    rule_diagnostic_revision,
                )
                by_track = {int(d["track_id"]): d for d in tracked}
                for event in events:
                    det = by_track.get(event.track_id)
                    evidence_path = None
                    vehicle_evidence_path = None
                    if det is not None:
                        evidence_path = save_evidence_snapshot(
                            frame, det, event.violation_type,
                            source_key=f"camera_{self.camera['id']}",
                            frame_number=event.frame_number,
                            violation_confidence=event.violation_confidence,
                            detection_confidence=event.detection_confidence,
                        )
                        vehicle_evidence_path = save_vehicle_crop(
                            frame, det,
                            source_key=f"camera_{self.camera['id']}",
                            frame_number=event.frame_number,
                        )
                    review_id = db.insert_review_queue(
                        video_id=None,
                        track_id=event.track_id,
                        violation_type=event.violation_type,
                        confidence=event.confidence,
                        **live_review_evidence_fields(event),
                        frame_number=event.frame_number,
                        evidence_path=evidence_path,
                        vehicle_evidence_path=vehicle_evidence_path,
                        plate_status="not_attempted",
                        reason_log=(
                            f"[{confidence_band(event.confidence)}] "
                            f"[camera:{self.camera['name']}] {event.reason_log}"
                        ),
                        vehicle_class=event.vehicle_class,
                        timestamp_sec=event.timestamp_sec,
                    )
                    if plate_collector is not None and det is not None:
                        plate_collector.register(
                            review_id=int(review_id),
                            track_id=event.track_id,
                            identity_epoch=det.get("track_identity_epoch"),
                            violation_type=event.violation_type,
                            frame_number=event.frame_number,
                            timestamp_sec=event.timestamp_sec,
                        )
                if plate_collector is not None:
                    plate_collector.observe(frame, frame_number, timestamp_sec, tracked)
        finally:
            if plate_collector is not None:
                plate_collector.flush("stream_stopped")
            cap.release()
            if skipped_frames:
                logger.info(
                    "camera %s skipped %d frame(s) while the GPU was reserved",
                    self.camera["id"],
                    skipped_frames,
                )
            self.status = "stopped"


class StreamManager:
    """Singleton registry of running camera workers."""

    def __init__(self) -> None:
        self._workers: dict[int, LiveStreamWorker] = {}
        self._lock = threading.Lock()

    def start(self, camera_id: int) -> LiveStreamWorker:
        with self._lock:
            worker = self._workers.get(camera_id)
            if worker is not None and worker.is_alive():
                return worker
            camera = db.get_camera(camera_id)
            if camera is None:
                raise ValueError(f"Camera {camera_id} not found.")
            worker = LiveStreamWorker(camera)
            self._workers[camera_id] = worker
            worker.start()
            return worker

    def stop(self, camera_id: int) -> None:
        with self._lock:
            worker = self._workers.pop(camera_id, None)
        if worker is not None:
            worker.stop()

    def get(self, camera_id: int) -> LiveStreamWorker | None:
        with self._lock:
            return self._workers.get(camera_id)

    def status(self, camera_id: int) -> dict[str, Any]:
        worker = self.get(camera_id)
        if worker is None:
            return {
                "running": False,
                "status": "stopped",
                "error": None,
                "diagnostics": [],
            }
        return {
            "running": worker.is_alive(),
            "status": worker.status,
            "error": worker.error,
            "diagnostics": list(worker.diagnostics),
        }


stream_manager = StreamManager()


def mjpeg_generator(worker: LiveStreamWorker):
    """Yield multipart JPEG frames for the Live Monitor feed."""
    while worker.is_alive():
        jpeg = worker.latest_jpeg()
        if jpeg is not None:
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
            )
        time.sleep(0.05)
