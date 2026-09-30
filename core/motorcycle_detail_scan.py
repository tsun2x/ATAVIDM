"""Queued second YOLOv8 pass over motorcycle detail crops.

The detail pass scans the bounded crops selected by
:mod:`core.motorcycle_detail` with a **separate three-class detail checkpoint**
and records helmet / side-mirror observations for a **separate human-review
queue**. Hard boundaries:

- The scan can supply evidence or raise a review flag. It never declares,
  confirms, or creates a violation, and it never writes to ``violations`` or
  ``review_queue``. Its output never reaches the main tracker or
  ``core.violation_engine``.
- The designated checkpoint's **exact three-class ID order**
  (:data:`core.motorcycle_detail_contract.DETAIL_MODEL_CLASSES`) is validated
  before the first crop is scanned. A missing, unreadable, reordered, renamed,
  duplicated, or mis-sized class map fails closed: no crop is scanned and no
  observation is produced. The main 15-class object checkpoint is not a detail
  checkpoint and is rejected here.
- The detail model outputs only ``helmet_nut_shell``, ``helmet_acceptable``,
  and ``side_mirror``. Rider and motorcycle attribution uses the main
  detector's context stored with each selected frame
  (:class:`core.motorcycle_detail.MainDetectorContext`).
- Crop inference holds the **shared GPU reservation**
  (:mod:`core.gpu_inference_slot`) for the whole batch, so it cannot overlap
  an uploaded-video job, a manual scan, or a live camera frame. The cheap
  ``idle_check`` is only a pre-filter; the reservation is the authority.
- Scan state is persisted, so work is bounded, resumable after a crash, and
  retries are capped. An unavailable checkpoint never consumes an attempt.
- The cached checkpoint result is keyed by a *designation fingerprint*, so a
  checkpoint designated after start-up (or a corrected file) is re-checked
  instead of staying cached as unavailable.

No checkpoint is activated, promoted, or moved by this module. The designated
detail checkpoint is configured explicitly (``TAVIDM_MOTORCYCLE_DETAIL_WEIGHTS``
or the ``motorcycle_detail_weights`` system setting). Nothing defaults to a
training-run candidate.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2

from core.detection_config import (
    YOLO_CLASS_HELMET,
    YOLO_CLASS_HELMET_ACCEPTABLE,
    YOLO_CLASS_HELMET_NUT_SHELL,
    YOLO_CLASS_MOTORCYCLE,
    YOLO_CLASS_RIDER,
    YOLO_CLASS_SIDE_MIRROR,
    ordered_class_names,
)
from core.gpu_inference_slot import PRIORITY_DETAIL_SCAN
from core.motorcycle_detail import (
    MAX_FRAMES_PER_OCCURRENCE,
    AttributeAssociation,
    CropRect,
    MainDetectorContext,
    RiderAssociation,
    associate_helmet,
    associate_mirrors,
    associate_rider,
    box_from_dict,
    map_box_from_crop,
    padded_crop_rect,
)
from core.motorcycle_detail_contract import (
    DETAIL_CONTRACT_VERSION,
    DETAIL_IDENTITY_PREFIX,
    DETAIL_MODEL_CLASSES,
    DETAIL_MODEL_KIND,
    check_detail_class_map,
    check_detail_task,
    detail_architecture_hint,
)

logger = logging.getLogger(__name__)

# --- Scan policy ------------------------------------------------------------
DETAIL_SCAN_CONF = 0.25
DETAIL_SCAN_MAX_ATTEMPTS = 2
DETAIL_SCAN_STALE_SEC = 900.0
DETAIL_SCAN_BATCH_LIMIT = 4
DETAIL_SCAN_POLL_SEC = 5.0
DETAIL_SCAN_MIN_SIDE = 320
# Background batches never block on the GPU: they skip and retry next poll.
DETAIL_SCAN_GPU_WAIT_SEC = 0.0
# A manual "scan now" click may wait briefly for the reservation instead of
# failing instantly, but the wait stays bounded.
DETAIL_SCAN_MANUAL_GPU_WAIT_SEC = 5.0

SCAN_STATES = ("queued", "scanning", "ready", "failed")
HUMAN_OUTCOMES = ("pending", "reviewed", "dismissed", "uncertain")


class DetailScanError(RuntimeError):
    """One candidate could not be scanned (crop read, inference, or decode)."""


@dataclass(frozen=True)
class DetailCheckpoint:
    """A validated designative checkpoint for the crop pass."""

    path: str | None
    class_names: tuple[str, ...] = ()
    ok: bool = False
    reason: str | None = None
    identity: str | None = None
    architecture: str | None = None

    def class_map_ids(self) -> list[str]:
        return list(self.class_names)

    def describe(self) -> dict[str, Any]:
        """Model identity block stored with every scan's observations."""
        return {
            "model": self.identity,
            "model_kind": DETAIL_MODEL_KIND,
            "architecture": self.architecture or "unverified",
            "contract": DETAIL_CONTRACT_VERSION,
            "class_names": list(self.class_names),
            "class_map": {str(i): name for i, name in enumerate(self.class_names)},
        }


def designated_weights_path() -> str | None:
    """Configured detail checkpoint path, or None when none is designated.

    Order: ``config.MOTORCYCLE_DETAIL_WEIGHTS`` (env/file), then the persisted
    ``motorcycle_detail_weights`` system setting. A value that is not an existing
    file is reported as designated-but-unusable so the scan fails closed with a
    visible reason instead of silently using something else.
    """
    try:
        import config

        configured = str(getattr(config, "MOTORCYCLE_DETAIL_WEIGHTS", "") or "").strip()
    except Exception:  # pragma: no cover - config import failure is not expected
        configured = ""
    if configured:
        return configured
    try:
        from database import db

        stored = db.get_setting("motorcycle_detail_weights", None)
    except Exception:
        stored = None
    stored = str(stored or "").strip()
    return stored or None


def _file_identity(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()[:12]


def _file_stamp(path: str | None) -> str:
    """Cheap change marker for a designated file (mtime + size, never content).

    A full sha256 of a 50 MB checkpoint on every poll would be wasteful; the
    ``st_mtime_ns``/``st_size`` pair changes whenever the operator replaces the
    file, which is exactly the invalidation this module needs.
    """
    if not path:
        return "unset"
    try:
        stat = Path(str(path)).stat()
    except OSError:
        return "absent"
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def designated_weights_fingerprint() -> str:
    """Identity of the current detail-checkpoint designation.

    Combines the configured path with the file's change stamp, so the scanner
    re-validates when the operator designates a checkpoint after start-up, or
    replaces/corrects the designated file. Both parts are cheap (one attribute
    read, one ``stat``, one settings row) and the result is stable between
    polls, so an unchanged designation is never re-validated.
    """
    try:
        import config

        configured = str(getattr(config, "MOTORCYCLE_DETAIL_WEIGHTS", "") or "").strip()
    except Exception:  # pragma: no cover - config import failure is not expected
        configured = ""
    try:
        from database import db

        stored = str(db.get_setting("motorcycle_detail_weights", None) or "").strip()
    except Exception:
        stored = ""
    path = configured or stored or None
    material = "|".join(
        (configured, stored, str(path or ""), _file_stamp(path))
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def validate_detail_class_map(
    raw_map: Any, *, expected: Sequence[str] = DETAIL_MODEL_CLASSES
) -> tuple[tuple[str, ...], str | None]:
    """Validate a checkpoint's ID->name map against the three-class detail contract.

    Returns ``(class_names, error)``. Any error is fatal for the scan: the caller
    must not scan a single crop with an unverified class map.
    """
    report = check_detail_class_map(raw_map, expected=expected)
    if not report.ok:
        return (), f"{report.code}: {report.reason}"
    return report.class_names, None


# ---------------------------------------------------------------------------
# Checkpoint loading + crop prediction
# ---------------------------------------------------------------------------

_MODEL_CACHE: dict[str, Any] = {}
_MODEL_CACHE_LOCK = threading.Lock()


def _default_model_loader(path: str) -> Any:
    from ultralytics import YOLO

    return YOLO(path)


def cached_model(path: str, model_loader: Callable[[str], Any] | None = None) -> Any:
    """Load (once per process) and cache a checkpoint by path *and* content stamp.

    Keying on the file stamp as well as the path means a corrected checkpoint
    written to the same path is re-loaded instead of serving the rejected model
    from cache.
    """
    key = f"{Path(path).resolve()}|{_file_stamp(path)}"
    with _MODEL_CACHE_LOCK:
        if key in _MODEL_CACHE:
            return _MODEL_CACHE[key]
    loader = model_loader or _default_model_loader
    model = loader(str(Path(path).resolve()))
    with _MODEL_CACHE_LOCK:
        _MODEL_CACHE[key] = model
    return model


def clear_model_cache() -> None:
    """Drop cached checkpoints (tests and explicit operator resets)."""
    with _MODEL_CACHE_LOCK:
        _MODEL_CACHE.clear()


def load_detail_checkpoint(
    path: str | None = None,
    *,
    model_loader: Callable[[str], Any] | None = None,
) -> DetailCheckpoint:
    """Resolve and strictly validate the designated detail checkpoint.

    Fails closed on: no designation, missing file, unreadable checkpoint, task
    metadata that is missing, malformed, or not ``detect``, or a class map that
    does not reproduce the exact three-class detail order. No crop is touched
    by this function.
    """
    resolved = path if path is not None else designated_weights_path()
    if not resolved:
        return DetailCheckpoint(None, ok=False, reason="no_designated_detail_checkpoint")
    candidate = Path(str(resolved))
    if not candidate.is_file():
        return DetailCheckpoint(str(candidate), ok=False, reason="detail_checkpoint_missing")
    try:
        model = cached_model(str(candidate), model_loader)
        raw_map = getattr(model, "names", None)
        task = getattr(model, "task", None)
    except Exception as exc:  # unreadable / corrupt checkpoint
        logger.exception("detail checkpoint load failed: %s", candidate)
        return DetailCheckpoint(str(candidate), ok=False, reason=f"detail_checkpoint_unreadable:{exc}")
    task_error = check_detail_task(task)
    if task_error:
        return DetailCheckpoint(str(candidate), ok=False, reason=task_error)
    class_names, error = validate_detail_class_map(raw_map)
    if error:
        return DetailCheckpoint(str(candidate), ok=False, reason=f"detail_class_map_rejected:{error}")
    try:
        identity = f"{DETAIL_IDENTITY_PREFIX}:{candidate.name}:{_file_identity(candidate)}"
    except OSError as exc:
        return DetailCheckpoint(str(candidate), ok=False, reason=f"detail_checkpoint_unreadable:{exc}")
    return DetailCheckpoint(
        path=str(candidate),
        class_names=class_names,
        ok=True,
        reason=None,
        identity=identity,
        architecture=detail_architecture_hint(model),
    )


class UltralyticsCropPredictor:
    """One YOLOv8 ``predict`` pass over a crop image (attribute pass, no tracking).

    Tracking is deliberately not used: ByteTrack IDs are not meaningful on an
    isolated crop, and the crop pass must not perturb the video pipeline's
    tracker state.
    """

    def __init__(self, model: Any, class_names: Sequence[str], *, conf: float) -> None:
        self._model = model
        self._class_names = tuple(str(n).strip().lower() for n in class_names)
        self._conf = float(conf)

    def predict(self, image: Any) -> list[dict[str, Any]]:
        results = self._model.predict(source=image, conf=self._conf, verbose=False)
        if not results:
            return []
        result = results[0]
        names = getattr(result, "names", None)
        if names is not None and ordered_class_names(names) != self._class_names:
            # The loaded model no longer matches the validated contract.
            raise DetailScanError("detail_result_class_map_mismatch")
        boxes = getattr(result, "boxes", None)
        if boxes is None or len(boxes) == 0:
            return []
        out: list[dict[str, Any]] = []
        for box in boxes:
            cls_id = int(box.cls[0]) if getattr(box, "cls", None) is not None else -1
            if not 0 <= cls_id < len(self._class_names):
                raise DetailScanError("detail_result_class_id_out_of_range")
            label = self._class_names[cls_id]
            xyxy = box.xyxy[0].tolist() if getattr(box, "xyxy", None) is not None else [0, 0, 0, 0]
            x1, y1, x2, y2 = (float(v) for v in xyxy)
            confidence = float(box.conf[0]) if getattr(box, "conf", None) is not None else 0.0
            out.append(
                {
                    "class_label": label,
                    "confidence": confidence,
                    "bbox": (x1, y1, x2, y2),
                }
            )
        return out


def default_predictor_factory(
    checkpoint: DetailCheckpoint, *, conf: float, model_loader: Callable[[str], Any] | None = None
) -> Callable[[Any], list[dict[str, Any]]]:
    """Return a callable that scans one crop image with the designated model."""
    assert checkpoint.path is not None
    model = cached_model(checkpoint.path, model_loader)
    predictor = UltralyticsCropPredictor(model, checkpoint.class_names, conf=conf)
    return predictor.predict



# ---------------------------------------------------------------------------
# Coordinate mapping, overlays, and association
# ---------------------------------------------------------------------------

_OVERLAY_COLORS = {
    "motorcycle": (246, 130, 59),
    "rider": (60, 200, 60),
    "helmet_acceptable": (200, 200, 60),
    "helmet_nut_shell": (40, 40, 220),
    "helmet": (140, 140, 200),
    "side_mirror": (220, 90, 180),
    "main:motorcycle": (246, 130, 59),
    "main:rider": (60, 200, 60),
    "main:other_motorcycle": (0, 165, 255),
}
_OVERLAY_DEFAULT_COLOR = (200, 200, 200)


def to_pipeline_detection(label: str, confidence: float, box: Sequence[float]) -> dict[str, Any]:
    """Normalize a scan box into the pipeline detection dict shape."""
    x1, y1, x2, y2 = (float(v) for v in box)
    return {
        "class_label": str(label).strip().lower(),
        "confidence": float(confidence),
        "bbox_x": x1,
        "bbox_y": y1,
        "bbox_w": max(0.0, x2 - x1),
        "bbox_h": max(0.0, y2 - y1),
        "track_id": None,
    }


def draw_mapped_boxes(
    image: Any,
    boxes: Sequence[Sequence[float]],
    *,
    labels: Sequence[str] | None = None,
    confidences: Sequence[float] | None = None,
) -> Any:
    """Draw mapped boxes (image-local coordinates) and return a new image."""
    annotated = image.copy()
    height, width = annotated.shape[:2]
    for index, box in enumerate(boxes):
        x1 = max(0, min(width - 1, int(round(float(box[0])))))
        y1 = max(0, min(height - 1, int(round(float(box[1])))))
        x2 = max(0, min(width - 1, int(round(float(box[2])))))
        y2 = max(0, min(height - 1, int(round(float(box[3])))))
        label = str(labels[index]) if labels and index < len(labels) else "object"
        color = _OVERLAY_COLORS.get(label, _OVERLAY_DEFAULT_COLOR)
        cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
        text = label
        if confidences and index < len(confidences):
            text = f"{label} {float(confidences[index]) * 100:.0f}%"
        ty = max(12, y1 - 4)
        (tw, th), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
        cv2.rectangle(
            annotated,
            (x1, max(0, ty - th - baseline)),
            (min(width - 1, x1 + tw + 4), min(height - 1, ty + baseline)),
            (20, 20, 20),
            -1,
        )
        cv2.putText(
            annotated,
            text,
            (x1 + 2, ty),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
    return annotated


@dataclass
class ScanAssociations:
    rider: RiderAssociation
    helmet: AttributeAssociation
    mirror: AttributeAssociation
    uncertainty: list[str] = field(default_factory=list)
    motorcycle_box: tuple[float, float, float, float] | None = None
    context: MainDetectorContext | None = None

    def as_dict(self) -> dict[str, Any]:
        out = {
            "rider": self.rider.as_dict(),
            "helmet": self.helmet.as_dict(),
            "mirror": self.mirror.as_dict(),
        }
        if self.context is not None:
            out["motorcycle_box"] = (
                [round(float(v), 2) for v in self.motorcycle_box] if self.motorcycle_box else None
            )
            out["context"] = self.context.summary()
        return out


def associate_scan_detections(
    detections: Sequence[Mapping[str, Any]],
    *,
    motorcycle_box: Sequence[float],
    frame_w: int,
    frame_h: int,
    main_context: MainDetectorContext | None = None,
) -> ScanAssociations:
    """Associate scan detections from one frame into review observations.

    ``detections`` are already mapped into *source frame* coordinates. The
    motorcycle reference box comes from the selection stage, because a padded
    crop can cut the motorcycle itself. Rider/helmet/mirror attribution uses the
    spatial rules in :mod:`core.motorcycle_detail` and reports ambiguity instead
    of guessing.

    With ``main_context`` (the three-class detail path) the rider comes only
    from the main detector's stored association, and the main detector's nearby
    motorcycles/riders are treated as known *different* objects. Scan output is
    only ever used for helmet and mirror labels.
    """
    del frame_w, frame_h  # reserved for future frame-edge reasoning
    if main_context is not None:
        return _associate_with_main_context(
            detections, motorcycle_box=motorcycle_box, context=main_context
        )
    primary = _primary_detection(_as_box(motorcycle_box))
    riders = [d for d in detections if d.get("class_label") == YOLO_CLASS_RIDER]
    helmets = [
        d
        for d in detections
        if d.get("class_label")
        in (YOLO_CLASS_HELMET_ACCEPTABLE, YOLO_CLASS_HELMET_NUT_SHELL, YOLO_CLASS_HELMET)
    ]
    mirrors = [d for d in detections if d.get("class_label") == YOLO_CLASS_SIDE_MIRROR]
    motorcycles = [d for d in detections if d.get("class_label") == YOLO_CLASS_MOTORCYCLE]

    rider = associate_rider(primary, riders)
    helmet = associate_helmet(primary, rider, helmets)
    mirror = associate_mirrors(primary, mirrors, motorcycles_in_frame=motorcycles)
    return ScanAssociations(
        rider=rider,
        helmet=helmet,
        mirror=mirror,
        uncertainty=_association_uncertainty(rider, helmet, mirror),
        motorcycle_box=_as_box(motorcycle_box),
    )


def _as_box(values: Sequence[float]) -> tuple[float, float, float, float]:
    return (float(values[0]), float(values[1]), float(values[2]), float(values[3]))


def _primary_detection(box: tuple[float, float, float, float]) -> dict[str, Any]:
    return {
        "class_label": YOLO_CLASS_MOTORCYCLE,
        "confidence": 1.0,
        "bbox_x": box[0],
        "bbox_y": box[1],
        "bbox_w": max(0.0, box[2] - box[0]),
        "bbox_h": max(0.0, box[3] - box[1]),
        "track_id": None,
    }


def _association_uncertainty(
    rider: RiderAssociation, helmet: AttributeAssociation, mirror: AttributeAssociation
) -> list[str]:
    uncertainty: list[str] = []
    for state, association in (("rider", rider), ("helmet", helmet), ("mirror", mirror)):
        for reason in association.reasons:
            uncertainty.append(f"{state}:{reason}")
    if helmet.state == "unknown" and not helmet.observations:
        uncertainty.append("helmet:no_observation_does_not_prove_absence")
    if mirror.state == "none_visible":
        uncertainty.append("mirror:absence_not_proven_unknown")
    return uncertainty


def _associate_with_main_context(
    detections: Sequence[Mapping[str, Any]],
    *,
    motorcycle_box: Sequence[float],
    context: MainDetectorContext,
) -> ScanAssociations:
    box = _as_box(motorcycle_box)
    primary = _primary_detection(box)
    helmets = [
        d
        for d in detections
        if d.get("class_label")
        in (YOLO_CLASS_HELMET_ACCEPTABLE, YOLO_CLASS_HELMET_NUT_SHELL, YOLO_CLASS_HELMET)
    ]
    mirrors = [d for d in detections if d.get("class_label") == YOLO_CLASS_SIDE_MIRROR]
    scan_motorcycles = [d for d in detections if d.get("class_label") == YOLO_CLASS_MOTORCYCLE]

    rider = context.rider_association()
    helmet = associate_helmet(
        primary,
        rider,
        helmets,
        other_riders=[o.box for o in context.riders],
        other_riders_complete=context.riders_complete,
    )
    mirror = associate_mirrors(
        primary,
        mirrors,
        motorcycles_in_frame=scan_motorcycles,
        known_other_motorcycles=[o.box for o in context.motorcycles],
        other_motorcycles_complete=context.motorcycles_complete,
    )
    uncertainty = _association_uncertainty(rider, helmet, mirror)
    if not context.recorded:
        uncertainty.append("context:nearby_motorcycles_not_recorded_for_this_row")
        uncertainty.append("context:nearby_riders_not_recorded_for_this_row")
    else:
        if context.motorcycles_truncated:
            uncertainty.append("context:nearby_motorcycles_truncated")
        if context.riders_truncated:
            uncertainty.append("context:nearby_riders_truncated")
    return ScanAssociations(
        rider=rider,
        helmet=helmet,
        mirror=mirror,
        uncertainty=uncertainty,
        motorcycle_box=box,
        context=context,
    )



# ---------------------------------------------------------------------------
# Scanner (queued, GPU-slot aware, resumable)
# ---------------------------------------------------------------------------


def resolve_detail_evidence_path(stored_path: str | None) -> Path | None:
    """Resolve a stored detail-evidence path inside the private evidence root."""
    import config
    from core.media_serve import MediaPathError, resolve_under_roots

    if not stored_path:
        return None
    raw = Path(str(stored_path))
    candidate = raw if raw.is_absolute() else Path(config.BASE_DIR) / raw
    root = Path(config.EVIDENCE_FOLDER)
    root = root if root.is_absolute() else Path(config.BASE_DIR) / root
    try:
        return resolve_under_roots(candidate, [root], must_exist=True)
    except MediaPathError:
        return None


class MotorcycleDetailScanner:
    """Scan queued detail candidates on crops, one bounded batch at a time.

    Scheduling: the batch holds the **shared GPU reservation**
    (:class:`core.gpu_inference_slot.GpuInferenceSlot`) from immediately before
    the first crop inference until the whole batch is finished, so a crop batch
    never overlaps an uploaded-video job, a manual scan, or a live camera
    frame. ``idle_check`` is only a cheap pre-filter (the video queue hint);
    waiting for the reservation is bounded and a busy device skips the batch
    instead of blocking the worker thread.

    State lives in the database, so a crash or restart resumes the remaining
    work, and attempts are capped per candidate. Abandoned ``scanning`` rows are
    recovered on the first pass, and a designated-but-unavailable checkpoint
    keeps candidates ``queued`` with a visible reason and no attempt spent.
    """

    def __init__(
        self,
        *,
        adapter: Any | None = None,
        idle_check: Callable[[], bool] | None = None,
        gpu_slot: Any | None = None,
        slot_owner: str | None = None,
        gpu_wait_sec: float = DETAIL_SCAN_GPU_WAIT_SEC,
        poll_sec: float = DETAIL_SCAN_POLL_SEC,
        batch_limit: int = DETAIL_SCAN_BATCH_LIMIT,
        max_attempts: int = DETAIL_SCAN_MAX_ATTEMPTS,
        stale_sec: float = DETAIL_SCAN_STALE_SEC,
        conf: float = DETAIL_SCAN_CONF,
        min_side: int = DETAIL_SCAN_MIN_SIDE,
        checkpoint_loader: Callable[..., DetailCheckpoint] | None = None,
        checkpoint_fingerprint: Callable[[], str] | None = None,
        predictor_factory: Callable[[DetailCheckpoint], Callable[[Any], list[dict[str, Any]]]] | None = None,
    ) -> None:
        self._adapter = adapter
        self._idle_check = idle_check
        self._gpu_slot = gpu_slot
        self._slot_owner = str(slot_owner or f"detail-scan:{id(self):x}")
        self.gpu_wait_sec = max(0.0, float(gpu_wait_sec))
        self.poll_sec = float(poll_sec)
        self.batch_limit = int(batch_limit)
        self.max_attempts = int(max_attempts)
        self.stale_sec = float(stale_sec)
        self.conf = float(conf)
        self.min_side = int(min_side)
        self._checkpoint_loader = checkpoint_loader or load_detail_checkpoint
        self._checkpoint_fingerprint = checkpoint_fingerprint or designated_weights_fingerprint
        self._predictor_factory = predictor_factory or (
            lambda checkpoint: default_predictor_factory(checkpoint, conf=self.conf)
        )
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._recovered = False
        self._recovered_include_recent = False
        self.last_checkpoint: DetailCheckpoint | None = None
        self.last_checkpoint_fingerprint: str | None = None
        self.stats: dict[str, int] = {
            "batches": 0,
            "claimed": 0,
            "ready": 0,
            "deferred": 0,
            "failed": 0,
            "gate_blocked": 0,
            "gpu_busy": 0,
            "recovered": 0,
        }

    # -- dependencies ----------------------------------------------------
    @property
    def adapter(self) -> Any:
        if self._adapter is None:
            from database import db as adapter_module

            self._adapter = adapter_module
        return self._adapter

    @property
    def gpu_slot(self) -> Any:
        if self._gpu_slot is None:
            from core.gpu_inference_slot import gpu_inference_slot

            self._gpu_slot = gpu_inference_slot()
        return self._gpu_slot

    def checkpoint(self) -> DetailCheckpoint:
        """The designated checkpoint, re-validated when its designation changes.

        Caching a *failed* gate forever would strand candidates that became
        scannable after an operator designated (or corrected) a checkpoint, so
        the cached result is keyed by a configuration fingerprint.
        """
        fingerprint = self._checkpoint_fingerprint()
        if self.last_checkpoint is None or fingerprint != self.last_checkpoint_fingerprint:
            self.last_checkpoint = self._checkpoint_loader()
            self.last_checkpoint_fingerprint = fingerprint
        return self.last_checkpoint

    def invalidate_checkpoint(self) -> None:
        """Explicit invalidation: forget the cached result and re-validate."""
        self.last_checkpoint = None
        self.last_checkpoint_fingerprint = None

    def _predictor(self, checkpoint: DetailCheckpoint) -> Callable[[Any], list[dict[str, Any]]]:
        return self._predictor_factory(checkpoint)

    # -- recovery --------------------------------------------------------
    def recover_abandoned(self, *, include_recent: bool = False) -> int | None:
        """Re-queue scans abandoned by a crash/restart (idempotent, once per pass).

        ``include_recent=True`` is for process start-up, where any ``scanning``
        row is by definition left by a previous process. No attempt is consumed
        by a recovery; rows that already spent their attempts become ``failed``.
        """
        if self._recovered and (not include_recent or self._recovered_include_recent):
            return 0
        broad_pass_requested = include_recent
        try:
            recovered = self.adapter.recover_stale_motorcycle_detail_scans(
                stale_sec=self.stale_sec,
                max_attempts=self.max_attempts,
                include_recent=include_recent,
            )
        except TypeError:
            # Adapter without the newer keyword: fall back to the stale-only call.
            # The broader pass is then still pending, so a later start-up call
            # retries instead of being silently skipped.
            try:
                recovered = self.adapter.recover_stale_motorcycle_detail_scans(
                    stale_sec=self.stale_sec, max_attempts=self.max_attempts
                )
            except Exception:
                logger.exception("failed to recover stale detail scans with legacy adapter")
                return None
            include_recent = False
        except Exception:
            logger.exception("failed to recover stale detail scans")
            return None
        self._recovered = True
        self._recovered_include_recent = bool(include_recent)
        if recovered:
            self.stats["recovered"] += int(recovered)
            logger.info("recovered %d abandoned motorcycle detail scans", int(recovered))
        # A stale-only fallback cannot certify start-up recovery of recent rows.
        if broad_pass_requested and not include_recent:
            return None
        return int(recovered or 0)

    def _queued_count(self) -> int:
        counter = getattr(self.adapter, "count_queued_motorcycle_detail_scans", None)
        if counter is None:
            return -1
        try:
            return int(counter())
        except Exception:
            logger.exception("failed to count queued motorcycle detail scans")
            return -1

    # -- scheduling ------------------------------------------------------
    def run_once(self) -> dict[str, Any]:
        """Claim and scan at most one bounded batch. Safe to call repeatedly."""
        report: dict[str, Any] = {
            "claimed": 0,
            "ready": 0,
            "deferred": 0,
            "failed": 0,
            "skipped": None,
            "gate_reason": None,
        }
        self.stats["batches"] += 1

        # Resume abandoned work first, before any gate: a crash must not need a
        # newly enqueued video (nor a designated checkpoint) to unblock.
        self.recover_abandoned()

        if self._idle_check is not None and not self._idle_check():
            report["skipped"] = "gpu_slot_busy"
            return report

        checkpoint = self.checkpoint()
        if not checkpoint.ok:
            # Fail closed: no crop is scanned and no observation is produced.
            report["skipped"] = "detail_checkpoint_unavailable"
            report["gate_reason"] = checkpoint.reason
            self.stats["gate_blocked"] += 1
            try:
                self.adapter.note_motorcycle_detail_scan_gate(
                    reason=checkpoint.reason or "unavailable"
                )
            except Exception:
                logger.exception("failed to record detail scan gate reason")
            return report

        # Cheap read-only peek: never claim (and never spend an attempt) for a
        # batch that cannot start because the GPU reservation is busy.
        queued = self._queued_count()
        if queued == 0:
            return report

        owner = self._slot_owner
        with self.gpu_slot.reservation(
            owner,
            timeout=self.gpu_wait_sec,
            priority=PRIORITY_DETAIL_SCAN,
        ) as acquired:
            if not acquired:
                # Do not hold a queue mutex here: the video worker may be
                # waiting for the same device and must not be blocked.
                report["skipped"] = "gpu_slot_busy"
                self.stats["gpu_busy"] += 1
                return report
            # Claims are atomic, so a second scanner that waited for the
            # reservation simply finds nothing left to claim.
            rows = self.adapter.claim_motorcycle_detail_scans(limit=self.batch_limit)
            report["claimed"] = len(rows)
            self.stats["claimed"] += len(rows)
            predictor = self._predictor(checkpoint) if rows else None
            for row in rows:
                candidate_id = int(row["id"])
                try:
                    result = self._scan_candidate(row, checkpoint, predictor)
                    self.adapter.finish_motorcycle_detail_scan(
                        candidate_id=candidate_id,
                        observations_json=result["observations_json"],
                        association_json=result["association_json"],
                        uncertainty_json=result["uncertainty_json"],
                        scan_model=checkpoint.identity,
                        scan_class_map_json=json.dumps(list(checkpoint.class_names)),
                        scan_seconds=result["scan_seconds"],
                    )
                    report["ready"] += 1
                    self.stats["ready"] += 1
                except Exception as exc:
                    logger.warning("detail scan failed for candidate %s: %s", candidate_id, exc)
                    try:
                        state = self.adapter.record_motorcycle_detail_scan_failure(
                            candidate_id=candidate_id, error=str(exc), max_attempts=self.max_attempts
                        )
                    except Exception:
                        logger.exception(
                            "failed to record detail scan failure for %s", candidate_id
                        )
                        state = "failed"
                    if state == "failed":
                        report["failed"] += 1
                        self.stats["failed"] += 1
                    else:
                        report["deferred"] += 1
                        self.stats["deferred"] += 1
        return report

    # -- worker thread ---------------------------------------------------
    def start(self) -> bool:
        if self._thread is not None and self._thread.is_alive():
            return False
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="tavidm-detail-scanner"
        )
        self._thread.start()
        return True

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and threading.current_thread() is not thread:
            thread.join(timeout=timeout)
        self._thread = None

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                logger.exception("detail scanner batch failed")
            self._stop.wait(self.poll_sec)


    # -- one candidate ---------------------------------------------------
    def _scan_candidate(
        self,
        row: Mapping[str, Any],
        checkpoint: DetailCheckpoint,
        predictor: Callable[[Any], list[dict[str, Any]]] | None,
    ) -> dict[str, Any]:
        if predictor is None:  # pragma: no cover - defensive
            raise DetailScanError("detail_predictor_unavailable")
        started = time.monotonic()
        frames = self._parse_frames(row)
        if not frames:
            raise DetailScanError("candidate_has_no_selected_frames")
        source_w = int(row.get("source_width") or 0)
        source_h = int(row.get("source_height") or 0)
        if source_w <= 0 or source_h <= 0:
            raise DetailScanError("candidate_source_dimensions_missing")
        primary_number = row.get("frame_number")
        run_key = str(row.get("run_key") or "")
        occurrence_key = str(row.get("occurrence_key") or "")

        frame_records: list[dict[str, Any]] = []
        uncertainty: list[str] = []
        primary_assoc: ScanAssociations | None = None
        for frame in frames:
            crop = self._frame_crop(frame, source_w, source_h)
            if crop is None:
                raise DetailScanError("candidate_frame_crop_missing")
            image = self._read_crop_image(frame, crop)
            motorcycle_box = self._reference_motorcycle_box(frame)
            context = MainDetectorContext.from_frame(frame)
            scale_x = float(frame.get("scan_scale_x") or 1.0)
            scale_y = float(frame.get("scan_scale_y") or 1.0)
            if scale_x <= 0 or scale_y <= 0:
                raise DetailScanError("candidate_scan_scale_invalid")
            scan_image = self._apply_scan_scale(image, scale_x=scale_x, scale_y=scale_y)
            raw = predictor(scan_image)
            mapped: list[tuple[dict[str, Any], tuple[float, float, float, float]]] = []
            for det in raw:
                source_box = map_box_from_crop(
                    tuple(det["bbox"]),
                    crop,
                    scale_x=scale_x,
                    scale_y=scale_y,
                    source_w=source_w,
                    source_h=source_h,
                )
                mapped.append((det, source_box))
            detections = [
                to_pipeline_detection(det["class_label"], det["confidence"], box)
                for det, box in mapped
            ]
            association = associate_scan_detections(
                detections,
                motorcycle_box=motorcycle_box,
                frame_w=source_w,
                frame_h=source_h,
                main_context=context,
            )
            frame_uncertainty = list(association.uncertainty)
            if scale_x != 1.0 or scale_y != 1.0:
                frame_uncertainty.append("scan_resize_applied")
            if (
                crop.x <= 0
                or crop.y <= 0
                or (crop.x + crop.w) >= source_w
                or (crop.y + crop.h) >= source_h
            ):
                frame_uncertainty.append("crop_clamped_at_frame_edge")
            uncertainty.extend(frame_uncertainty)

            def _local(box: Sequence[float]) -> tuple[float, float, float, float]:
                return (box[0] - crop.x, box[1] - crop.y, box[2] - crop.x, box[3] - crop.y)

            main_boxes = [_local(motorcycle_box)]
            main_labels = ["main:motorcycle"]
            if context.rider_box is not None:
                main_boxes.append(_local(context.rider_box))
                main_labels.append("main:rider")
            for other in context.motorcycles:
                main_boxes.append(_local(other.box))
                main_labels.append("main:other_motorcycle")
            overlay = draw_mapped_boxes(image, main_boxes, labels=main_labels)
            overlay = draw_mapped_boxes(
                overlay,
                [_local(box) for _, box in mapped],
                labels=[str(det["class_label"]) for det, _ in mapped],
                confidences=[float(det["confidence"]) for det, _ in mapped],
            )
            from core.evidence import save_detail_overlay

            overlay_path = save_detail_overlay(
                overlay,
                run_key=run_key,
                occurrence_key=occurrence_key,
                frame_index=int(frame.get("index") or 1),
            )
            frame_record = {
                "index": int(frame.get("index") or 1),
                "frame_number": frame.get("frame_number"),
                "scan_scale_x": scale_x,
                "scan_scale_y": scale_y,
                "crop": crop.as_dict(),
                "overlay_path": overlay_path,
                "detections": [
                    {
                        "class_label": det["class_label"],
                        "confidence": round(float(det["confidence"]), 4),
                        "source_bbox": [round(float(v), 2) for v in box],
                    }
                    for det, box in mapped
                ],
                "association": association.as_dict(),
                "uncertainty": frame_uncertainty,
            }
            frame_records.append(frame_record)
            if primary_assoc is None or frame.get("frame_number") == primary_number:
                primary_assoc = association

        if primary_assoc is None:  # pragma: no cover - frames is non-empty
            raise DetailScanError("candidate_association_failed")
        uncertainty = list(dict.fromkeys(uncertainty))
        observations = {
            "scan": {
                **checkpoint.describe(),
                "conf": self.conf,
                "frames_scanned": len(frame_records),
                "attribution_source": "main_detector",
                "review_only": True,
            },
            "frames": frame_records,
            "association": primary_assoc.as_dict(),
            "uncertainty": uncertainty,
        }
        scan_seconds = max(0.0, time.monotonic() - started)
        return {
            "observations_json": json.dumps(observations),
            "association_json": json.dumps(primary_assoc.as_dict()),
            "uncertainty_json": json.dumps(uncertainty),
            "scan_seconds": round(scan_seconds, 4),
        }


    # -- small helpers ---------------------------------------------------
    @staticmethod
    def _parse_frames(row: Mapping[str, Any]) -> list[dict[str, Any]]:
        raw = row.get("frames_json")
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = []
        elif isinstance(raw, list):
            parsed = raw
        else:
            parsed = []
        return [f for f in parsed if isinstance(f, dict)][:MAX_FRAMES_PER_OCCURRENCE]

    @staticmethod
    def _frame_crop(frame: Mapping[str, Any], source_w: int, source_h: int) -> CropRect | None:
        raw = frame.get("crop")
        if isinstance(raw, dict):
            try:
                rect = CropRect(int(raw["x"]), int(raw["y"]), int(raw["w"]), int(raw["h"]))
            except (KeyError, TypeError, ValueError):
                rect = None
            if rect is not None and rect.w > 0 and rect.h > 0:
                return rect
        box = frame.get("detection_bbox")
        if isinstance(box, dict):
            try:
                x = float(box["x"])
                y = float(box["y"])
                w = float(box["w"])
                h = float(box["h"])
            except (KeyError, TypeError, ValueError):
                return None
            return padded_crop_rect((x, y, x + w, y + h), source_w, source_h)
        return None

    @staticmethod
    def _read_crop_image(frame: Mapping[str, Any], crop: CropRect) -> Any:
        path = resolve_detail_evidence_path(frame.get("crop_path"))
        if path is None:
            raise DetailScanError("candidate_frame_crop_unreadable")
        image = cv2.imread(str(path))
        if image is None:
            raise DetailScanError("candidate_frame_crop_unreadable")
        height, width = image.shape[:2]
        if width != crop.w or height != crop.h:
            # Refuse to map boxes from a crop whose size does not match the
            # recorded rectangle: the coordinate mapping would be wrong.
            raise DetailScanError("candidate_frame_crop_size_mismatch")
        return image

    @staticmethod
    def _apply_scan_scale(image: Any, *, scale_x: float, scale_y: float) -> Any:
        if scale_x == 1.0 and scale_y == 1.0:
            return image
        height, width = image.shape[:2]
        target = (max(1, int(round(width * scale_x))), max(1, int(round(height * scale_y))))
        return cv2.resize(image, target, interpolation=cv2.INTER_LINEAR)

    @staticmethod
    def _reference_motorcycle_box(frame: Mapping[str, Any]) -> tuple[float, float, float, float]:
        """The main detector's stored target motorcycle box (never a scan box)."""
        stored_box = box_from_dict(frame.get("detection_bbox"))
        if stored_box is None:
            raise DetailScanError("candidate_motorcycle_reference_missing")
        return stored_box
