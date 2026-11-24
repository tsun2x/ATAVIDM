"""Process-scoped holder for the experimental plate OCR runtime.

A single :class:`PlateRuntime` owns the settings, the lazily-initialised
:class:`~core.plate_onnx_backend.PlateOnnxBackend`, and the single bounded
inference worker. Creating this module imports nothing heavy and constructs
nothing: :func:`get_runtime` returns ``None`` unless plate OCR is explicitly
enabled, and even when it is enabled the ONNX sessions are only created on the
first inference call.

``app.stop_processing_worker()`` and the demo launcher both call
:func:`shutdown_runtime`, which stops the worker thread and releases every
retained-crop reservation.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from core import plate_manifest as manifests
from core.plate_jobs import (
    CropMemoryBudget,
    PlateInferenceWorker,
    PlateObservationCollector,
)
from core.plate_onnx_backend import PlateBackendError, PlateBackendUnavailable, PlateOnnxBackend
from core.plate_settings import (
    PlateOcrConfigError,
    PlateOcrSettings,
    load_plate_ocr_settings,
    verify_artifact_hashes,
)

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_DETAIL_SUBMISSION_LOCK = threading.Lock()
_RUNTIME: "PlateRuntime | None" = None
_RUNTIME_BUILT = False
_LAST_ERROR: str | None = None
LEGACY_DETAIL_FRAME_SKIP = "collection_ended:no_attributable_detail_frame"


def _validated_manifest_root(
    protected_paths: tuple[str, ...] = (), *, mode: str | None = None
) -> tuple[Path | None, str | None]:
    """Validate the plate tree against the isolated or regular-app evidence root."""
    try:
        from config import EVIDENCE_FOLDER, SQLITE_PATH

        if mode == "app":
            evidence = Path(os.environ.get("EVIDENCE_FOLDER", EVIDENCE_FOLDER)).resolve()
            database_text = os.environ.get(
                "DATABASE_URL", os.environ.get("SQLITE_PATH", SQLITE_PATH)
            )
            database = Path(database_text).resolve()
            expected = (evidence / manifests.PLATE_SUBDIR).resolve()
        else:
            isolated = Path(os.environ["TAVIDM_PLATE_OCR_ISOLATED_ROOT"]).resolve()
            evidence = Path(os.environ["EVIDENCE_FOLDER"]).resolve()
            database = Path(os.environ["SQLITE_PATH"]).resolve()
            expected = (evidence / manifests.PLATE_SUBDIR).resolve()

        manifest_root = manifests.plate_root().resolve()
        repo = Path(__file__).resolve().parents[1]
        if manifest_root != expected:
            if mode == "app":
                return None, "plate_evidence_outside_authorized_evidence_root"
            return None, "plate_evidence_outside_isolated_evidence_root"
        if mode != "app" and (evidence == isolated or isolated not in evidence.parents):
            return None, "plate_evidence_outside_isolated_evidence_root"
        if manifest_root == database or manifest_root in database.parents or database in manifest_root.parents:
            return None, "plate_evidence_overlaps_database"
        if mode != "app" and (manifest_root == repo or manifest_root in repo.parents or repo in manifest_root.parents):
            return None, "plate_evidence_overlaps_repository"
        for text in protected_paths:
            protected = Path(text).resolve()
            if manifest_root == protected or manifest_root in protected.parents or protected in manifest_root.parents:
                return None, "plate_evidence_overlaps_protected_input"
        return manifest_root, None
    except (KeyError, OSError, RuntimeError, ValueError):
        return None, "plate_evidence_paths_invalid"


def _authorized_mode() -> tuple[str | None, str | None]:
    """Return an explicitly enabled app, demo, or evaluation mode."""
    demo_value = os.environ.get("TAVIDM_PLATE_OCR_DEMO", "").strip()
    evaluation_value = os.environ.get("TAVIDM_PLATE_OCR_EVALUATION", "").strip()
    if demo_value not in ("", "0", "1") or evaluation_value not in ("", "0", "1"):
        return None, "execution_mode_invalid"
    app_value = os.environ.get("TAVIDM_PLATE_OCR_APP", "").strip()
    if app_value not in ("", "0", "1"):
        return None, "execution_mode_invalid"
    demo = demo_value == "1"
    evaluation = evaluation_value == "1"
    app_mode = app_value == "1"
    if sum((demo, evaluation, app_mode)) != 1:
        return None, "explicit_ocr_mode_required"
    if app_mode:
        manifest_root, manifest_error = _validated_manifest_root(mode="app")
        if manifest_root is None:
            return None, manifest_error
        return "app", None
    root_text = os.environ.get("TAVIDM_PLATE_OCR_ISOLATED_ROOT", "").strip()
    db_text = os.environ.get("SQLITE_PATH", "").strip()
    database_url_text = os.environ.get("DATABASE_URL", "").strip()
    evidence_text = os.environ.get("EVIDENCE_FOLDER", "").strip()
    output_names = ("UPLOAD_FOLDER", "FRAMES_FOLDER", "ANNOTATED_FOLDER", "REPORTS_FOLDER")
    output_texts = {name: os.environ.get(name, "").strip() for name in output_names}
    if not root_text or not db_text or not evidence_text or any(not value for value in output_texts.values()):
        return None, "isolated_paths_required"
    try:
        root = Path(root_text).resolve()
        database = Path(db_text).resolve()
        database_url = Path(database_url_text).resolve() if database_url_text else database
        evidence = Path(evidence_text).resolve()
        output_paths = {name: Path(value).resolve() for name, value in output_texts.items()}
        repo = Path(__file__).resolve().parents[1]
        if root == repo or root in repo.parents or repo in root.parents:
            return None, "isolated_root_overlaps_repository"
        if database == root or root not in database.parents:
            return None, "database_outside_isolated_root"
        if database_url != database or database_url == root or root not in database_url.parents:
            return None, "database_url_outside_isolated_root"
        if evidence == root or root not in evidence.parents:
            return None, "evidence_outside_isolated_root"
        manifest_root, manifest_error = _validated_manifest_root()
        if manifest_root is None:
            return None, manifest_error
        if database == evidence or database in evidence.parents or evidence in database.parents:
            return None, "database_evidence_paths_overlap"
        directories = {"EVIDENCE_FOLDER": evidence, **output_paths}
        directory_values = list(directories.items())
        for index, (left_name, left) in enumerate(directory_values):
            if left == root or root not in left.parents:
                return None, f"{left_name.lower()}_outside_isolated_root"
            for right_name, right in directory_values[index + 1 :]:
                if left == right or left in right.parents or right in left.parents:
                    return None, "isolated_output_paths_overlap"
    except (OSError, RuntimeError, ValueError):
        return None, "isolated_paths_invalid"
    return ("demo" if demo else "evaluation"), None


class PlateRuntime:
    """Shared, process-scoped plate OCR runtime."""

    def __init__(self, settings: PlateOcrSettings) -> None:
        self.settings = settings
        self.backend = PlateOnnxBackend(settings)
        self.memory = CropMemoryBudget(int(settings.bounds["retained_crop_memory_budget_bytes"]))
        self.worker = PlateInferenceWorker(settings, self.backend, memory=self.memory)

    # -- lifecycle ----------------------------------------------------
    def start(self) -> None:
        self.worker.start()

    def shutdown(self) -> None:
        self.worker.stop()
        self.memory.release(self.memory.used_bytes)

    def new_collector(
        self,
        *,
        source: str,
        run_key: str,
        live_session_id: str | None = None,
    ) -> PlateObservationCollector:
        return PlateObservationCollector(
            self.settings,
            self.backend,
            self.worker,
            source=source,
            run_key=run_key,
            live_session_id=live_session_id,
        )

    # -- introspection ------------------------------------------------
    def status(self) -> dict[str, Any]:
        artifacts = verify_artifact_hashes(self.settings)
        return {
            "experimental": True,
            "enabled": True,
            "provider_requested": self.settings.provider,
            "backend_state": "ready" if self.backend.runtime_ready else "not_initialized",
            "artifacts": artifacts,
            "artifacts_ok": all(row["ok"] for row in artifacts),
            "bounds": dict(self.settings.bounds),
            "quality": dict(self.settings.quality),
            "worker": self.worker.snapshot(),
            "detector_calls_process": self.backend.detector_calls,
            "ocr_calls_process": self.backend.ocr_calls,
            "manifests": manifests.orphan_report(),
            "evaluation_record": self.settings.evaluation_record_path,
        }


def get_runtime() -> PlateRuntime | None:
    """Return the shared runtime, or ``None`` when plate OCR is disabled."""
    global _RUNTIME, _RUNTIME_BUILT, _LAST_ERROR
    with _LOCK:
        if _RUNTIME_BUILT:
            return _RUNTIME
        _RUNTIME_BUILT = True
        mode, mode_error = _authorized_mode()
        if mode is None:
            _LAST_ERROR = f"plate_ocr_disabled:{mode_error}"
            _RUNTIME = None
            return None
        try:
            settings = load_plate_ocr_settings()
        except PlateOcrConfigError as exc:
            _LAST_ERROR = f"plate_ocr_config_invalid:{exc}"
            logger.error("%s", _LAST_ERROR)
            _RUNTIME = None
            return None
        if not settings.is_enabled:
            _RUNTIME = None
            return None
        isolated_root = (
            Path(os.environ["TAVIDM_PLATE_OCR_ISOLATED_ROOT"]).resolve()
            if mode != "app"
            else None
        )
        protected = [*settings.artifact_paths()]
        protected.extend(path for path in (settings.config_path, settings.evaluation_record_path) if path)
        manifest_root, manifest_error = _validated_manifest_root(tuple(protected), mode=mode)
        if manifest_root is None:
            _LAST_ERROR = f"plate_ocr_{manifest_error}"
            _RUNTIME = None
            return None
        for protected_text in protected:
            try:
                protected_path = Path(protected_text).resolve()
            except (OSError, RuntimeError, ValueError):
                _LAST_ERROR = "plate_ocr_protected_path_invalid"
                return None
            if isolated_root is not None and (
                protected_path == isolated_root
                or isolated_root in protected_path.parents
                or protected_path in isolated_root.parents
            ):
                _LAST_ERROR = "plate_ocr_output_overlaps_input_artifact"
                return None
        # A thesis demo does not require a passing evaluation record. Keep
        # model/config integrity checks for both isolated execution modes.
        artifact_rows = verify_artifact_hashes(settings)
        if not artifact_rows or any(row.get("ok") is not True for row in artifact_rows):
            _LAST_ERROR = "plate_ocr_artifacts_unavailable"
            _RUNTIME = None
            return None
        try:
            runtime = PlateRuntime(settings)
            runtime.start()
        except Exception as exc:  # noqa: BLE001 - never break app startup
            _LAST_ERROR = f"plate_ocr_runtime_unavailable:{type(exc).__name__}"
            logger.error("%s", _LAST_ERROR)
            _RUNTIME = None
            return None
        _RUNTIME = runtime
        return _RUNTIME


def reset_runtime() -> None:
    """Drop the cached runtime (tests and the demo launcher)."""
    global _RUNTIME, _RUNTIME_BUILT, _LAST_ERROR
    with _LOCK:
        if _RUNTIME is not None:
            _RUNTIME.shutdown()
        _RUNTIME = None
        _RUNTIME_BUILT = False
        _LAST_ERROR = None


def shutdown_runtime() -> None:
    """Stop the worker and release retained crops. Safe to call repeatedly."""
    reset_runtime()


def status_report() -> dict[str, Any]:
    """Machine-readable feature status for the UI and API.

    Always safe to call: when the feature is disabled this returns the disabled
    state without touching a model, a socket, or the filesystem.
    """
    runtime = get_runtime()
    if runtime is None:
        return {
            "experimental": True,
            "enabled": False,
            "state": "disabled",
            "reason": _LAST_ERROR or "plate_ocr_disabled_by_default",
            "worker": None,
        }
    # ONNX Runtime sessions are intentionally lazy, but the Settings/status
    # surface must still distinguish a usable configured feature from one that
    # cannot possibly run in this interpreter. Otherwise the UI can report
    # "enabled" until the first plate attempt fails.
    try:
        runtime_available = find_spec("onnxruntime") is not None
    except (ImportError, ValueError):
        runtime_available = False
    if not runtime_available:
        return {
            "experimental": True,
            "enabled": True,
            "state": "unavailable",
            "reason": "onnxruntime_not_installed",
            "worker": runtime.worker.snapshot(),
        }
    try:
        report = runtime.status()
    except (PlateBackendError, PlateBackendUnavailable) as exc:
        return {
            "experimental": True,
            "enabled": True,
            "state": "unavailable",
            "reason": f"{type(exc).__name__}",
            "worker": runtime.worker.snapshot(),
        }
    report["state"] = "enabled" if report.get("artifacts_ok") else "unavailable"
    if not report.get("artifacts_ok"):
        report["reason"] = "artifact_rejected"
    return report


def worker_state_for(review_id: int | None) -> str | None:
    runtime = get_runtime()
    if runtime is None or review_id is None:
        return None
    return runtime.worker.state_for(review_id)


def _review_attempt(runtime: PlateRuntime, review_id: int, rescan_from: str | None) -> dict | None:
    """Check the attempt token while holding the review submission lock."""
    if runtime.worker.state_for(review_id):
        return None
    attempts = manifests.attempts_for_review(review_id)
    latest = attempts[0] if attempts else None
    if rescan_from != (latest.attempt_id if latest else None):
        return None
    return (latest.data or {}) if latest else {}


def _stored_review_image(stored_path: str | None) -> Path | None:
    import config
    from core.media_serve import resolve_under_roots

    if not stored_path:
        return None
    base = Path(config.BASE_DIR)
    try:
        return resolve_under_roots(base / stored_path, [base / config.EVIDENCE_FOLDER, base / "static" / "evidence"])
    except (ValueError, OSError):
        return None


def saved_review_vehicle_crop(review: dict[str, Any]) -> Path | None:
    return _stored_review_image(review.get("vehicle_evidence_path"))


def queue_saved_review_ocr(adapter: Any, review_id: int, *, rescan_from: str | None) -> str | None:
    """Rescan evidence attributed to an ordinary uploaded or live review."""
    import cv2
    from core.detection_config import VEHICLE_CLASSES
    from core.evidence import raw_scene_sidecar_path

    runtime = get_runtime()
    if runtime is None:
        return None
    with _DETAIL_SUBMISSION_LOCK:
        latest = _review_attempt(runtime, review_id, rescan_from)
        review = adapter.get_review_item(review_id)
        if latest is None or not review or review["status"] != "pending":
            return None
        crop = saved_review_vehicle_crop(review)
        if crop is None:
            return "No saved vehicle crop is available for this review."

        track_id = review["track_id"]
        frame_number = review["frame_number"]
        timestamp = review["timestamp_sec"]
        video_id = review.get("video_id")
        run_id = review.get("processing_run_id")
        frame = None
        vehicles = []
        scene = _stored_review_image(review.get("evidence_path")) if video_id and run_id else None
        raw_scene = raw_scene_sidecar_path(scene) if scene else None
        if raw_scene:
            vehicles = [det for det in adapter.get_detections_by_video(
                video_id, frame_number, frame_number, processing_run_id=run_id,
            ) if det.get("class_label") in VEHICLE_CLASSES]
            if sum(det["track_id"] == track_id for det in vehicles) == 1:
                frame = cv2.imread(str(raw_scene))
        if frame is None:
            # A tight saved crop is attributable even on older and live rows.
            # A previously ambiguous observation needs the tracked scene instead.
            if latest.get("association_uncertain"):
                return "Rescan needs a saved frame with tracking data to check which vehicle owns the plate."
            frame = cv2.imread(str(crop))
            if frame is not None:
                height, width = frame.shape[:2]
                vehicles = [dict(track_id=track_id, bbox_x=0, bbox_y=0, bbox_w=width, bbox_h=height)]

        collector = runtime.new_collector(
            source="live" if video_id is None else "video",
            run_key=f"run_{run_id}" if run_id else crop.parent.name,
        )
        collector.register(
            review_id=review_id, track_id=track_id, identity_epoch=None,
            violation_type=review["violation_type"], frame_number=frame_number, timestamp_sec=timestamp,
        )
        try:
            if frame is not None:
                collector.observe(frame, frame_number, timestamp, vehicles)
        finally:
            collector.flush("stored_review_evidence" if frame is not None else "review_evidence_unavailable")
    return None


def queue_detail_review_ocr(
    adapter: Any, review_id: int, lead: dict[str, Any], *,
    kind: str, rescan_from: str | None = None,
) -> None:
    """Offer stored vehicle evidence or rescan the latest completed attempt."""
    try:
        runtime = get_runtime()
        if runtime is None:
            return
        with _DETAIL_SUBMISSION_LOCK:
            if runtime.worker.state_for(review_id):
                return
            attempts = manifests.attempts_for_review(review_id)
            latest_attempt = attempts[0] if attempts else None
            latest = (latest_attempt.data or {}) if latest_attempt else {}
            legacy_skip = latest and (
                latest.get("outcome") == "no_candidate_detected"
                and LEGACY_DETAIL_FRAME_SKIP in (latest.get("quality_rejections") or [])
            )
            if rescan_from is not None:
                if latest_attempt is None or latest_attempt.attempt_id != rescan_from:
                    return
            elif latest and not legacy_skip:
                return
            _submit_detail_evidence(runtime, adapter, review_id, lead, kind)
    except Exception:
        logger.exception("Plate OCR could not be prepared for detail review %s", review_id)


def _submit_detail_evidence(
    runtime: PlateRuntime, adapter: Any, review_id: int, lead: dict[str, Any], kind: str,
) -> None:
    import cv2
    import config
    from core.detection_config import VEHICLE_CLASSES
    from core.evidence import detail_occurrence_dir, pickup_detail_occurrence_dir
    from core.media_serve import resolve_under_roots

    pickup = kind == "pickup"
    track_id = lead["pickup_track_id" if pickup else "track_id"]
    review = adapter.get_review_item(review_id)
    if not review or not lead.get("processing_run_id") or (
        review.get("video_id"), review.get("processing_run_id"), review.get("track_id")
    ) != (lead.get("video_id"), lead["processing_run_id"], track_id):
        return

    occurrence_dir = pickup_detail_occurrence_dir if pickup else detail_occurrence_dir
    evidence_dir = Path(config.BASE_DIR) / config.EVIDENCE_FOLDER / ("pickup_detail" if pickup else "detail")
    occurrence_root = resolve_under_roots(
        Path(config.BASE_DIR) / occurrence_dir(lead["run_key"], lead["occurrence_key"]),
        [evidence_dir], must_exist=False,
    )
    frames = json.loads(lead.get("frames_json") or "[]")
    if not isinstance(frames, list):
        frames = []
    ordered = sorted(
        (frame for frame in frames if isinstance(frame, dict) and type(frame.get("frame_number")) is int),
        key=lambda frame: frame["frame_number"],
    )
    collector = runtime.new_collector(source="video", run_key=f"run_{lead['processing_run_id']}")
    epoch = lead.get("pickup_identity_epoch") if pickup else None
    collector.register(
        review_id=review_id, track_id=track_id, identity_epoch=epoch,
        violation_type=review["violation_type"], frame_number=review["frame_number"],
        timestamp_sec=review["timestamp_sec"],
    )
    offered = False
    try:
        for frame in ordered[:int(runtime.settings.bounds["max_crops_per_observation"])]:
            try:
                stored_box = frame["pickup_bbox" if pickup else "detection_bbox"]
                box = {f"bbox_{axis}": float(stored_box[axis]) for axis in ("x", "y", "w", "h")}
                timestamp = float(frame["timestamp_sec"])
                if not all(math.isfinite(number) for number in (*box.values(), timestamp)):
                    continue
                if box["bbox_w"] <= 0 or box["bbox_h"] <= 0:
                    continue
                path = resolve_under_roots(Path(config.BASE_DIR) / frame["scene_path"], [occurrence_root])
            except (KeyError, TypeError, ValueError, OSError):
                continue
            image = cv2.imread(str(path))
            if image is None or image.shape[:2] != (lead.get("source_height"), lead.get("source_width")):
                continue
            frame_number = frame["frame_number"]
            neighbors = adapter.get_detections_by_video(
                lead["video_id"], frame_number, frame_number, processing_run_id=lead["processing_run_id"],
            )
            target = {"track_id": track_id, "track_identity_epoch": epoch, **box}
            vehicles = [row for row in neighbors if row.get("class_label") in VEHICLE_CLASSES
                        and row.get("track_id") != track_id]
            collector.observe(image, frame_number, timestamp, [target, *vehicles])
            offered = True
    finally:
        collector.flush("stored_detail_evidence" if offered else "detail_evidence_unavailable")
