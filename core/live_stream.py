"""Live CCTV (RTSP) stream processing (manuscript Ch1 Scope, Ch3 Data Source).

Each active camera runs one background worker: RTSP capture -> YOLOv8m +
ByteTrack -> rule engine -> evidence + review queue, and keeps the latest
annotated frame available as JPEG for the Live Monitor MJPEG feed.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import cv2

from core.detection_config import confidence_band
from core.detector import Detector, DetectorError
from core.evidence import save_evidence_snapshot
from core.tracker import TrackState
from core.video_processor import load_rule_parameters
from core.violation_engine import RuleEngineState, evaluate_detection_rules
from core.zone_config import parse_zones_json
from database import db

_BOX_COLOR = (246, 130, 59)   # BGR
_ZONE_COLOR = (57, 57, 230)


class LiveStreamWorker(threading.Thread):
    def __init__(self, camera: dict[str, Any]) -> None:
        super().__init__(daemon=True, name=f"camera-{camera['id']}")
        self.camera = camera
        self.zones = parse_zones_json(camera.get("zones_json"))
        self._stop_event = threading.Event()
        self._frame_lock = threading.Lock()
        self._latest_jpeg: bytes | None = None
        self.status = "starting"
        self.error: str | None = None

    def stop(self) -> None:
        self._stop_event.set()

    def latest_jpeg(self) -> bytes | None:
        with self._frame_lock:
            return self._latest_jpeg

    def _annotate(self, frame: Any, tracked: list[dict[str, Any]]) -> Any:
        annotated = frame.copy()
        for polygon in self.zones.values():
            if len(polygon) >= 3:
                pts = [(int(p[0]), int(p[1])) for p in polygon]
                for i in range(len(pts)):
                    cv2.line(annotated, pts[i], pts[(i + 1) % len(pts)], _ZONE_COLOR, 2)
        for det in tracked:
            x, y = int(det["bbox_x"]), int(det["bbox_y"])
            w, h = int(det["bbox_w"]), int(det["bbox_h"])
            cv2.rectangle(annotated, (x, y), (x + w, y + h), _BOX_COLOR, 2)
            label = f"{det['class_label']} #{det['track_id']} {det['confidence'] * 100:.0f}%"
            cv2.putText(annotated, label, (x, max(y - 6, 12)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, _BOX_COLOR, 2)
        return annotated

    def _publish(self, frame: Any) -> None:
        ok, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ok:
            with self._frame_lock:
                self._latest_jpeg = buffer.tobytes()

    def run(self) -> None:  # noqa: C901 — single sequential pipeline loop
        params = load_rule_parameters()
        try:
            detector = Detector()
            detector.load()
        except DetectorError as exc:
            self.status = "error"
            self.error = str(exc)
            return

        cap = cv2.VideoCapture(self.camera["rtsp_url"])
        if not cap.isOpened():
            self.status = "error"
            self.error = f"Cannot open stream: {self.camera['rtsp_url']}"
            return

        self.status = "live"
        track_state = TrackState(stationary_px=float(params["stationary_px"]))
        rule_state = RuleEngineState()
        frame_skip = max(int(params["frame_skip"]), 1)
        conf_threshold = float(params["confidence_threshold"])
        frame_number = -1
        started = time.monotonic()

        try:
            while not self._stop_event.is_set():
                ok, frame = cap.read()
                if not ok:
                    self.status = "reconnecting"
                    cap.release()
                    time.sleep(2)
                    cap = cv2.VideoCapture(self.camera["rtsp_url"])
                    if cap.isOpened():
                        self.status = "live"
                    continue

                frame_number += 1
                if frame_number % frame_skip != 0:
                    continue

                timestamp_sec = time.monotonic() - started
                raw = detector.track_frame(frame, conf=conf_threshold, timestamp_sec=timestamp_sec)
                tracked = track_state.update(raw)
                self._publish(self._annotate(frame, tracked))

                events = evaluate_detection_rules(
                    tracked, rule_state, frame_number,
                    zones=self.zones, params=params,
                )
                by_track = {int(d["track_id"]): d for d in tracked}
                for event in events:
                    det = by_track.get(event.track_id)
                    evidence_path = None
                    if det is not None:
                        evidence_path = save_evidence_snapshot(
                            frame, det, event.violation_type,
                            source_key=f"camera_{self.camera['id']}",
                            frame_number=event.frame_number,
                        )
                    db.insert_review_queue(
                        video_id=None,
                        track_id=event.track_id,
                        violation_type=event.violation_type,
                        confidence=event.confidence,
                        frame_number=event.frame_number,
                        evidence_path=evidence_path,
                        reason_log=(
                            f"[{confidence_band(event.confidence)}] "
                            f"[camera:{self.camera['name']}] {event.reason_log}"
                        ),
                        vehicle_class=event.vehicle_class,
                        timestamp_sec=event.timestamp_sec,
                    )
        finally:
            cap.release()
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
            return {"running": False, "status": "stopped", "error": None}
        return {
            "running": worker.is_alive(),
            "status": worker.status,
            "error": worker.error,
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
