"""Person-only second-stage inference on detected pickup-truck crops.

This auxiliary model supplies review and annotated-video evidence only. Its
synthetic person tracks are never sent to the rule engine or persisted as main
detector detections.
"""

from __future__ import annotations

import logging
import math
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from core.detection_config import (
    PRETRAINED_WEIGHTS,
    YOLO_CLASS_CAR,
    YOLO_CLASS_PERSON,
    YOLO_CLASS_PICKUP_TRUCK,
)
from core.tracker import TRACK_EXPIRY_SEC

logger = logging.getLogger(__name__)

SCANNER_VERSION = "pickup-person-scan-4"
# Raw person boxes down to this score are logged for missed-rider diagnosis;
# only boxes at or above the scanner confidence can become review evidence.
DIAGNOSTIC_CONFIDENCE = 0.10
MAX_SCAN_LOG_ROWS = 5000
MAX_PICKUP_COVERAGE = 500
# Rider geometry, as fractions of the vehicle box height. A bed rider's head
# projects above the cab roofline and the bed hides the lower body, so the
# visible box ends near the top of the vehicle box. A person standing on the
# ground behind or beside the vehicle shows legs down to its lower edge.
ELEVATED_MAX_TOP_REL = 0.05
ELEVATED_MIN_BOTTOM_REL = -0.25
ELEVATED_MAX_BOTTOM_REL = 0.55
ELEVATED_MAX_HEIGHT_RATIO = 0.75
GROUND_MIN_BOTTOM_REL = 0.80
GROUND_MIN_HEIGHT_RATIO = 0.80
# Overlapping pickup crops can see the same person; it is linked only to the
# best-matching pickup at or above this frame-space IoU.
EXCLUSIVE_ASSIGNMENT_IOU = 0.50
EXCLUSIVE_ASSIGNMENT_MIN_COVERAGE = 0.75


def _box(det: Mapping[str, Any]) -> tuple[float, float, float, float]:
    return (
        float(det["bbox_x"]), float(det["bbox_y"]),
        float(det["bbox_w"]), float(det["bbox_h"]),
    )


def _intersection_area(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax1, ay1, aw, ah = a
    bx1, by1, bw, bh = b
    iw = max(0.0, min(ax1 + aw, bx1 + bw) - max(ax1, bx1))
    ih = max(0.0, min(ay1 + ah, by1 + bh) - max(ay1, by1))
    return iw * ih


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    intersection = _intersection_area(a, b)
    union = a[2] * a[3] + b[2] * b[3] - intersection
    return intersection / union if union > 0 else 0.0


def _same_person_across_crops(
    first: Mapping[str, Any], second: Mapping[str, Any],
) -> bool:
    a, b = _box(first), _box(second)
    intersection = _intersection_area(a, b)
    area_a, area_b = a[2] * a[3], b[2] * b[3]
    # A crop can truncate the detection, lowering IoU even when the smaller
    # box is mostly contained in the larger one.
    return (
        intersection >= EXCLUSIVE_ASSIGNMENT_IOU * (area_a + area_b - intersection)
        or intersection >= EXCLUSIVE_ASSIGNMENT_MIN_COVERAGE * min(area_a, area_b)
    )


def _center_distance(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> float:
    ax, ay = a[0] + a[2] / 2.0, a[1] + a[3] / 2.0
    bx, by = b[0] + b[2] / 2.0, b[1] + b[3] / 2.0
    return math.hypot(ax - bx, ay - by)


def rider_geometry(
    person_box: tuple[float, float, float, float],
    vehicle_box: tuple[float, float, float, float],
) -> dict[str, Any]:
    """Classify a person box by its position and size relative to a vehicle box."""
    px, py, pw, ph = person_box
    vx, vy, vw, vh = vehicle_box
    center_x_rel = (px + pw / 2.0 - vx) / vw
    top_rel = (py - vy) / vh
    bottom_rel = (py + ph - vy) / vh
    height_ratio = ph / vh
    if bottom_rel >= GROUND_MIN_BOTTOM_REL or height_ratio >= GROUND_MIN_HEIGHT_RATIO:
        label = "ground_level"
    elif bottom_rel < ELEVATED_MIN_BOTTOM_REL:
        label = "detached_above"
    elif (
        top_rel <= ELEVATED_MAX_TOP_REL
        and bottom_rel <= ELEVATED_MAX_BOTTOM_REL
        and height_ratio <= ELEVATED_MAX_HEIGHT_RATIO
    ):
        label = "elevated"
    else:
        label = "ambiguous"
    return {
        "label": label,
        "center_x_rel": round(center_x_rel, 3),
        "top_rel": round(top_rel, 3),
        "bottom_rel": round(bottom_rel, 3),
        "height_ratio": round(height_ratio, 3),
    }


def _read_source_revision() -> str | None:
    git_dir = Path(__file__).resolve().parent.parent / ".git"
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref: "):
            return head[:12] or None
        ref = head[5:].strip()
        ref_file = git_dir / ref
        if ref_file.is_file():
            return ref_file.read_text(encoding="utf-8").strip()[:12] or None
        packed = git_dir / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                if line.endswith(" " + ref):
                    return line.split()[0][:12]
    except OSError:
        return None
    return None


# Read at import so a long-running server reports the revision it loaded.
SOURCE_REVISION = _read_source_revision()


def _weights_path() -> str:
    try:
        import config

        configured = str(getattr(config, "PICKUP_PERSON_WEIGHTS", "") or "").strip()
    except Exception:  # pragma: no cover - configuration import is expected
        configured = ""
    return configured or PRETRAINED_WEIGHTS


class PickupPersonCropScanner:
    """Scan pickup crops and bounded car fallbacks for frame-coordinate people."""

    CAR_FALLBACK_INTERVAL_SEC = 0.5
    MAX_CAR_FALLBACK_CROPS = 3
    # The tracker settles car/pickup identity only after one label holds for
    # its motion window, so an approaching pickup whose label alternates with
    # car stays "car" (or unresolved) for its whole pass. Raw pickup frames
    # within one track identity epoch promote it to a full per-frame scan.
    # Sensitivity: lower values scan more car tracks with spurious pickup
    # frames; higher values delay or miss short pickup passes.
    PICKUP_LABEL_MIN_FRAMES = 3

    def __init__(
        self,
        *,
        enabled: bool = True,
        model_loader: Callable[[str], Any] | None = None,
        confidence: float = 0.15,
        minimum_iou: float = 0.20,
        track_gap_sec: float = 1.5,
    ) -> None:
        self.enabled = bool(enabled)
        self.confidence = min(1.0, max(0.01, float(confidence)))
        self.minimum_iou = min(1.0, max(0.0, float(minimum_iou)))
        self.track_gap_sec = max(0.1, float(track_gap_sec))
        self._model_loader = model_loader or self._load_yolo
        self._model: Any | None = None
        self._person_class_ids: set[int] = set()
        self._disabled_reason: str | None = None
        self._next_track_id = -1
        self._previous: dict[tuple[int, int], list[dict[str, Any]]] = {}
        self._last_car_fallback_scan_sec: float | None = None
        # (track_id, identity_epoch) -> (raw pickup frame count, last seen sec)
        self._pickup_label_votes: dict[tuple[int, int], tuple[int, float]] = {}
        self._counters: Counter[str] = Counter()
        self._scan_seconds = 0.0
        self._log_rows: list[dict[str, Any]] = []
        self._coverage: dict[tuple[int, int], dict[str, Any]] = {}

    def _scan_class(self, det: Mapping[str, Any], timestamp_sec: float) -> tuple[str, str]:
        """Return the vehicle class used to route this track and why."""
        raw = str(det.get("class_label") or "")
        resolved_value = det.get("resolved_track_class")
        resolved = str(resolved_value or raw)
        reason = "resolved_track_class" if resolved_value else "raw_label"
        if det.get("track_id") is None:
            return resolved, reason
        key = (int(det["track_id"]), int(det.get("track_identity_epoch") or 0))
        votes, _ = self._pickup_label_votes.get(key, (0, timestamp_sec))
        if raw == YOLO_CLASS_PICKUP_TRUCK:
            votes += 1
        self._pickup_label_votes[key] = (votes, timestamp_sec)
        if votes >= self.PICKUP_LABEL_MIN_FRAMES and resolved != YOLO_CLASS_PICKUP_TRUCK:
            return YOLO_CLASS_PICKUP_TRUCK, "pickup_label_votes"
        return resolved, reason

    def _prune_label_votes(self, timestamp_sec: float) -> None:
        stale = [
            key for key, (_, last_seen) in self._pickup_label_votes.items()
            if timestamp_sec - last_seen > TRACK_EXPIRY_SEC
        ]
        for key in stale:
            self._pickup_label_votes.pop(key, None)

    @staticmethod
    def _load_yolo(path: str) -> Any:
        try:
            from ultralytics import YOLO
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise RuntimeError("ultralytics is not installed") from exc
        return YOLO(path)

    def _load(self) -> bool:
        if self._model is not None:
            return True
        if self._disabled_reason is not None:
            return False
        path = _weights_path()
        if path != PRETRAINED_WEIGHTS and not Path(path).is_file():
            self._disabled_reason = "configured checkpoint is missing"
        else:
            try:
                model = self._model_loader(path)
                names = getattr(model, "names", None)
                items = names.items() if isinstance(names, Mapping) else enumerate(names or ())
                ids = {
                    int(class_id)
                    for class_id, name in items
                    if str(name).strip().lower() == YOLO_CLASS_PERSON
                    and str(class_id).lstrip("+-").isdigit()
                }
                if not ids:
                    raise RuntimeError("checkpoint has no person class")
                self._model = model
                self._person_class_ids = ids
                return True
            except Exception as exc:  # noqa: BLE001 - auxiliary stage fails open
                self._disabled_reason = str(exc)
        logger.error("pickup person crop submodel unavailable: %s", self._disabled_reason)
        return False

    def _filter_decision(
        self,
        mapped: Mapping[str, Any],
        vehicle_box: tuple[float, float, float, float],
        vehicle_class: str,
        geometry: Mapping[str, Any],
    ) -> str:
        if float(mapped["confidence"]) < self.confidence:
            return "below_confidence"
        center_x = mapped["bbox_x"] + mapped["bbox_w"] / 2.0
        center_y = mapped["bbox_y"] + mapped["bbox_h"] / 2.0
        vx, vy, vw, vh = vehicle_box
        # A rider in the bed is horizontally over the vehicle; a pedestrian
        # beside a parked vehicle is centred past its side.
        if not (vx <= center_x <= vx + vw):
            return "outside_vehicle_width"
        if vehicle_class == YOLO_CLASS_CAR and center_y > vy + vh * 0.15:
            return "below_car_roofline"
        if geometry["label"] in {"ground_level", "detached_above"}:
            return str(geometry["label"])
        return "kept"

    def _record_coverage(
        self, key: tuple[int, int], timestamp_sec: float, reason: str, raw: int, kept: int
    ) -> None:
        entry = self._coverage.get(key)
        if entry is None:
            if len(self._coverage) >= MAX_PICKUP_COVERAGE:
                self._counters["coverage_entries_dropped"] += 1
                return
            entry = {
                "pickup_track_id": key[0],
                "pickup_identity_epoch": key[1],
                "first_scan_sec": round(timestamp_sec, 3),
                "crops": 0,
                "raw_person_boxes": 0,
                "kept_person_boxes": 0,
                "scan_reasons": [],
            }
            self._coverage[key] = entry
        entry["last_scan_sec"] = round(timestamp_sec, 3)
        entry["crops"] += 1
        entry["raw_person_boxes"] += raw
        entry["kept_person_boxes"] += kept
        if reason not in entry["scan_reasons"]:
            entry["scan_reasons"].append(reason)

    def scan(
        self,
        frame: Any,
        tracked: Sequence[Mapping[str, Any]],
        *,
        timestamp_sec: float,
        frame_number: int | None = None,
    ) -> list[dict[str, Any]]:
        """Detect people on pickup crops and selected car fallbacks.

        The crop extends above the vehicle detection to retain riders whose
        upper bodies project beyond its roofline. Car fallbacks are throttled
        and accepted only above the vehicle body. If the auxiliary checkpoint
        cannot load or infer, the primary video pipeline continues unchanged.
        """
        if not self.enabled or not self._load():
            return []
        ts = float(timestamp_sec)
        try:
            frame_h, frame_w = frame.shape[:2]
            pickups: list[dict[str, Any]] = []
            car_fallbacks: list[dict[str, Any]] = []
            self._prune_label_votes(ts)
            for det in tracked:
                label, reason = self._scan_class(det, ts)
                is_pickup = label == YOLO_CLASS_PICKUP_TRUCK
                is_car = label == YOLO_CLASS_CAR
                if not (is_pickup or is_car) or det.get("track_id") is None:
                    continue
                x, y, w, h = _box(det)
                if w <= 0 or h <= 0:
                    continue
                # Modest side/bottom padding and generous top padding help keep
                # visible riders in-frame while retaining the detected pickup.
                left = max(0, int(math.floor(x - w * 0.08)))
                top = max(0, int(math.floor(y - h * 0.55)))
                right = min(frame_w, int(math.ceil(x + w * 1.08)))
                bottom = min(frame_h, int(math.ceil(y + h * 1.05)))
                if right <= left or bottom <= top:
                    continue
                candidate = {
                    "key": (int(det["track_id"]), int(det.get("track_identity_epoch") or 0)),
                    "rect": (left, top, right, bottom),
                    "crop": frame[top:bottom, left:right],
                    "vehicle_box": (x, y, w, h),
                    "vehicle_class": label,
                    "reason": reason,
                }
                if is_pickup:
                    pickups.append(candidate)
                else:
                    car_fallbacks.append({**candidate, "reason": "car_fallback"})

            # Some pickups are classified as cars. Inspect only the three
            # largest car crops twice per second; a person hit is accepted only
            # above the vehicle's roofline and horizontally over that vehicle.
            # This bounded fallback catches missed pickup labels without
            # scanning every car crop on every frame.
            if (
                car_fallbacks
                and (
                    self._last_car_fallback_scan_sec is None
                    or ts - self._last_car_fallback_scan_sec
                    >= self.CAR_FALLBACK_INTERVAL_SEC
                )
            ):
                car_fallbacks.sort(
                    key=lambda item: item["vehicle_box"][2] * item["vehicle_box"][3],
                    reverse=True,
                )
                pickups.extend(car_fallbacks[:self.MAX_CAR_FALLBACK_CROPS])
                self._last_car_fallback_scan_sec = ts
            if not pickups:
                return []

            started = time.perf_counter()
            results = self._model.predict(
                source=[item["crop"] for item in pickups],
                classes=sorted(self._person_class_ids),
                conf=min(self.confidence, DIAGNOSTIC_CONFIDENCE),
                verbose=False,
            )
            self._scan_seconds += time.perf_counter() - started
            if len(results) != len(pickups):
                raise RuntimeError("person submodel returned an incomplete crop batch")
            self._counters["frames_scanned"] += 1
            self._counters["crops_scanned"] += len(pickups)

            primary_persons = [
                primary for primary in tracked
                if str(primary.get("class_label") or "") == YOLO_CLASS_PERSON
                and primary.get("track_id") is not None
            ]
            kept: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
            log_rows: list[dict[str, Any]] = []
            for candidate, result in zip(pickups, results):
                left, top = candidate["rect"][:2]
                vehicle_box = candidate["vehicle_box"]
                vehicle_class = candidate["vehicle_class"]
                persons_log: list[dict[str, Any]] = []
                boxes = getattr(result, "boxes", None) or ()
                for box in boxes:
                    class_id = int(box.cls[0]) if getattr(box, "cls", None) is not None else -1
                    if class_id not in self._person_class_ids:
                        continue
                    confidence = float(box.conf[0]) if getattr(box, "conf", None) is not None else 0.0
                    coords = box.xyxy[0].tolist() if getattr(box, "xyxy", None) is not None else ()
                    if len(coords) != 4:
                        continue
                    x1, y1, x2, y2 = (float(value) for value in coords)
                    if x2 <= x1 or y2 <= y1:
                        continue
                    mapped = {
                        "class_label": YOLO_CLASS_PERSON,
                        "confidence": confidence,
                        "bbox_x": x1 + left,
                        "bbox_y": y1 + top,
                        "bbox_w": x2 - x1,
                        "bbox_h": y2 - y1,
                        "source": "pickup_person_crop_submodel",
                        "source_vehicle_class": vehicle_class,
                    }
                    geometry = rider_geometry(_box(mapped), vehicle_box)
                    mapped["rider_geometry"] = geometry
                    entry = {
                        "box": [round(value, 1) for value in _box(mapped)],
                        "score": round(confidence, 3),
                        "geometry": geometry,
                        "decision": self._filter_decision(
                            mapped, vehicle_box, vehicle_class, geometry
                        ),
                    }
                    persons_log.append(entry)
                    if entry["decision"] != "kept":
                        continue
                    # The main detector may also see this person; the crop
                    # detection stays linked to its pickup and records the
                    # overlap instead of being dropped.
                    overlap = next(
                        (
                            primary for primary in primary_persons
                            if _iou(_box(mapped), _box(primary)) >= 0.30
                        ),
                        None,
                    )
                    if overlap is not None:
                        mapped["overlaps_primary_track_id"] = int(overlap["track_id"])
                        entry["overlaps_primary_track_id"] = int(overlap["track_id"])
                    kept.append((candidate, mapped, entry))
                candidate["raw_persons"] = len(persons_log)
                if persons_log:
                    log_rows.append({
                        "t": round(ts, 3),
                        "frame": frame_number,
                        "pickup_track_id": candidate["key"][0],
                        "pickup_identity_epoch": candidate["key"][1],
                        "vehicle_class": vehicle_class,
                        "scan_reason": candidate["reason"],
                        "vehicle_box": [round(value, 1) for value in vehicle_box],
                        "crop": list(candidate["rect"]),
                        "persons": persons_log,
                    })

            # Retain one box for annotation, but withhold review credit when
            # another pickup's crop also contains that person.
            kept.sort(
                key=lambda item: (
                    item[1]["rider_geometry"]["label"] == "elevated",
                    -abs(float(item[1]["rider_geometry"]["center_x_rel"]) - 0.5),
                    float(item[1]["confidence"]),
                ),
                reverse=True,
            )
            detections_by_pickup: dict[tuple[int, int], list[dict[str, Any]]] = {
                candidate["key"]: [] for candidate in pickups
            }
            accepted: list[tuple[tuple[int, int], dict[str, Any], dict[str, Any]]] = []
            for candidate, mapped, entry in kept:
                duplicate = next(
                    (
                        (other, other_entry)
                        for other_key, other, other_entry in accepted
                        if other_key != candidate["key"] and _same_person_across_crops(mapped, other)
                    ),
                    None,
                )
                if duplicate is not None:
                    other, other_entry = duplicate
                    other["pickup_ownership_contested"] = True
                    other_entry["pickup_ownership_contested"] = True
                    if "overlaps_primary_track_id" in mapped:
                        other.setdefault("overlaps_primary_track_id", mapped["overlaps_primary_track_id"])
                    entry["decision"] = "assigned_to_other_pickup"
                    continue
                accepted.append((candidate["key"], mapped, entry))
                detections_by_pickup[candidate["key"]].append(mapped)
            output = self._assign_tracks(detections_by_pickup, ts)
            for _candidate, mapped, entry in kept:
                if "track_id" in mapped and entry["decision"] == "kept":
                    entry["track_id"] = int(mapped["track_id"])

            for candidate in pickups:
                self._record_coverage(
                    candidate["key"], ts, candidate["reason"],
                    int(candidate.get("raw_persons", 0)),
                    len(detections_by_pickup[candidate["key"]]),
                )
            for row in log_rows:
                for entry in row["persons"]:
                    self._counters[f"decision_{entry['decision']}"] += 1
                if len(self._log_rows) < MAX_SCAN_LOG_ROWS:
                    self._log_rows.append(row)
                else:
                    self._counters["log_rows_dropped"] += 1
            return output
        except Exception as exc:  # noqa: BLE001 - optional stage cannot stop video
            self._disable(f"inference failed: {exc}")
            return []

    def _assign_tracks(
        self,
        current: Mapping[tuple[int, int], list[dict[str, Any]]],
        timestamp_sec: float,
    ) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        next_previous: dict[tuple[int, int], list[dict[str, Any]]] = {}
        for pickup_key, detections in current.items():
            prior = self._previous.get(pickup_key, [])
            available = set(range(len(prior)))
            for detection in detections:
                detection_box = _box(detection)
                best_index = min(
                    available,
                    key=lambda index: (
                        0 if _iou(detection_box, _box(prior[index])) >= self.minimum_iou else 1,
                        -_iou(detection_box, _box(prior[index]))
                        if _iou(detection_box, _box(prior[index])) >= self.minimum_iou
                        else _center_distance(detection_box, _box(prior[index])),
                    ),
                    default=None,
                )
                matched = False
                if (
                    best_index is not None
                    and timestamp_sec - float(prior[best_index]["timestamp_sec"]) <= self.track_gap_sec
                ):
                    prior_box = _box(prior[best_index])
                    matched = (
                        _iou(detection_box, prior_box) >= self.minimum_iou
                        or _center_distance(detection_box, prior_box)
                        <= max(8.0, min(detection_box[3], prior_box[3]) * 0.45)
                    )
                if matched and best_index is not None:
                    track_id = int(prior[best_index]["track_id"])
                    available.remove(best_index)
                else:
                    track_id = self._next_track_id
                    self._next_track_id -= 1
                detection.update({
                    "track_id": track_id,
                    "track_identity_epoch": 1,
                    "resolved_track_class": YOLO_CLASS_PERSON,
                    "source_pickup_track_id": pickup_key[0],
                    "source_pickup_identity_epoch": pickup_key[1],
                })
                output.append(detection)
                next_previous.setdefault(pickup_key, []).append({
                    **detection,
                    "timestamp_sec": timestamp_sec,
                })
            if pickup_key not in next_previous:
                recent = [
                    item for item in prior
                    if timestamp_sec - float(item["timestamp_sec"]) <= self.track_gap_sec
                ]
                if recent:
                    next_previous[pickup_key] = recent
        self._previous = next_previous
        return output

    def _disable(self, reason: str) -> None:
        if self._disabled_reason is None:
            self._disabled_reason = reason
            self._model = None
            logger.exception("pickup person crop submodel disabled: %s", reason)

    def scan_log_rows(self) -> list[dict[str, Any]]:
        """Bounded per-crop rows for frames where any person score reached the log floor."""
        return list(self._log_rows)

    def summary(self) -> dict[str, Any]:
        frames = int(self._counters.get("frames_scanned", 0))
        return {
            "enabled": self.enabled,
            "loaded": self._model is not None,
            "weights": _weights_path(),
            "person_class_ids": sorted(self._person_class_ids),
            "disabled_reason": self._disabled_reason,
            "version": SCANNER_VERSION,
            "source_revision": SOURCE_REVISION,
            "pid": os.getpid(),
            "confidence": self.confidence,
            "diagnostic_confidence": DIAGNOSTIC_CONFIDENCE,
            "counters": dict(self._counters),
            "scan_seconds": round(self._scan_seconds, 3),
            "mean_scan_ms_per_scanned_frame": (
                round(self._scan_seconds * 1000.0 / frames, 2) if frames else None
            ),
            "log_rows": len(self._log_rows),
            "pickups": sorted(
                self._coverage.values(), key=lambda item: item["first_scan_sec"]
            ),
        }
