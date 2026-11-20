"""Person-only second-stage inference on detected pickup-truck crops.

This auxiliary model supplies review and annotated-video evidence only. Its
synthetic person tracks are never sent to the rule engine or persisted as main
detector detections.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from core.detection_config import (
    PRETRAINED_WEIGHTS,
    YOLO_CLASS_CAR,
    YOLO_CLASS_PERSON,
    YOLO_CLASS_PICKUP_TRUCK,
)

logger = logging.getLogger(__name__)


def _box(det: Mapping[str, Any]) -> tuple[float, float, float, float]:
    return (
        float(det["bbox_x"]), float(det["bbox_y"]),
        float(det["bbox_w"]), float(det["bbox_h"]),
    )


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax1, ay1, aw, ah = a
    bx1, by1, bw, bh = b
    iw = max(0.0, min(ax1 + aw, bx1 + bw) - max(ax1, bx1))
    ih = max(0.0, min(ay1 + ah, by1 + bh) - max(ay1, by1))
    intersection = iw * ih
    union = aw * ah + bw * bh - intersection
    return intersection / union if union > 0 else 0.0


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

    def __init__(
        self,
        *,
        enabled: bool = True,
        model_loader: Callable[[str], Any] | None = None,
        confidence: float = 0.25,
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

    def scan(
        self,
        frame: Any,
        tracked: Sequence[Mapping[str, Any]],
        *,
        timestamp_sec: float,
    ) -> list[dict[str, Any]]:
        """Detect people on pickup crops and selected car fallbacks.

        The crop extends above the vehicle detection to retain riders whose
        upper bodies project beyond its roofline. Car fallbacks are throttled
        and accepted only above the vehicle body. If the auxiliary checkpoint
        cannot load or infer, the primary video pipeline continues unchanged.
        """
        if not self.enabled or not self._load():
            return []
        try:
            frame_h, frame_w = frame.shape[:2]
            pickups: list[
                tuple[
                    tuple[int, int], tuple[int, int, int, int], Any,
                    tuple[float, float, float, float], str,
                ]
            ] = []
            car_fallbacks = []
            for det in tracked:
                label = str(det.get("resolved_track_class", det.get("class_label", "")))
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
                key = (int(det["track_id"]), int(det.get("track_identity_epoch") or 0))
                candidate = (
                    key,
                    (left, top, right, bottom),
                    frame[top:bottom, left:right],
                    (x, y, w, h),
                    label,
                )
                if is_pickup:
                    pickups.append(candidate)
                else:
                    car_fallbacks.append(candidate)

            # Some pickups are classified as cars. Inspect only the three
            # largest car crops twice per second; a person hit is accepted only
            # above the vehicle's roofline and horizontally over that vehicle.
            # This bounded fallback catches missed pickup labels without
            # scanning every car crop on every frame.
            if (
                car_fallbacks
                and (
                    self._last_car_fallback_scan_sec is None
                    or float(timestamp_sec) - self._last_car_fallback_scan_sec
                    >= self.CAR_FALLBACK_INTERVAL_SEC
                )
            ):
                car_fallbacks.sort(
                    key=lambda item: item[3][2] * item[3][3], reverse=True
                )
                pickups.extend(car_fallbacks[:self.MAX_CAR_FALLBACK_CROPS])
                self._last_car_fallback_scan_sec = float(timestamp_sec)
            if not pickups:
                return []

            results = self._model.predict(
                source=[item[2] for item in pickups],
                classes=sorted(self._person_class_ids),
                conf=self.confidence,
                verbose=False,
            )
            if len(results) != len(pickups):
                raise RuntimeError("person submodel returned an incomplete crop batch")
            detections_by_pickup: dict[tuple[int, int], list[dict[str, Any]]] = {}
            for (
                (pickup_key, (left, top, _right, _bottom), _crop, vehicle_box, vehicle_class),
                result,
            ) in zip(pickups, results):
                detections: list[dict[str, Any]] = []
                boxes = getattr(result, "boxes", None)
                if boxes is None:
                    detections_by_pickup[pickup_key] = detections
                    continue
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
                    center_x = mapped["bbox_x"] + mapped["bbox_w"] / 2.0
                    center_y = mapped["bbox_y"] + mapped["bbox_h"] / 2.0
                    vx, vy, vw, vh = vehicle_box
                    horizontal_margin = vw * 0.05
                    if not (vx - horizontal_margin <= center_x <= vx + vw + horizontal_margin):
                        continue
                    if vehicle_class == YOLO_CLASS_CAR and center_y > vy + vh * 0.15:
                        continue
                    # Avoid drawing/queuing the same person twice when the
                    # primary detector already found that person in-frame.
                    if any(
                        str(primary.get("class_label") or "") == YOLO_CLASS_PERSON
                        and _iou(_box(mapped), _box(primary)) >= 0.30
                        for primary in tracked
                    ):
                        continue
                    detections.append(mapped)
                detections_by_pickup[pickup_key] = detections
            return self._assign_tracks(detections_by_pickup, float(timestamp_sec))
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
                best_index = max(
                    available,
                    key=lambda index: _iou(detection_box, _box(prior[index])),
                    default=None,
                )
                if (
                    best_index is not None
                    and timestamp_sec - float(prior[best_index]["timestamp_sec"]) <= self.track_gap_sec
                    and _iou(detection_box, _box(prior[best_index])) >= self.minimum_iou
                ):
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
        self._previous = next_previous
        return output

    def _disable(self, reason: str) -> None:
        if self._disabled_reason is None:
            self._disabled_reason = reason
            self._model = None
            logger.exception("pickup person crop submodel disabled: %s", reason)

    def summary(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "loaded": self._model is not None,
            "weights": _weights_path(),
            "person_class_ids": sorted(self._person_class_ids),
            "disabled_reason": self._disabled_reason,
        }
