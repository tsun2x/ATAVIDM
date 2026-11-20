"""Review-only pickup/person association leads from the main detector tracks.

This collector does not run another detector and never produces a violation.
Cargo-region and lower-body visibility are deliberately left unknown until an
explicit evidence producer supplies them.
"""

from __future__ import annotations

import json
import logging
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

import cv2

from core.detection_config import YOLO_CLASS_PERSON, YOLO_CLASS_PICKUP_TRUCK
from core.evidence import remove_pickup_detail_run, save_pickup_detail_evidence
from core.tracker import TRACK_EXPIRY_SEC

logger = logging.getLogger(__name__)

PICKUP_REVIEW_SELECTOR_VERSION = "pickup-review-2"
MIN_ASSOCIATION_SEC = 2.0
MIN_CROP_ASSOCIATION_SEC = 1.1
MIN_SHARED_DISPLACEMENT_PX = 40.0
MAX_RELATIVE_DRIFT = 0.22
MAX_ACTIVE_PAIRS = 96
MAX_CANDIDATES_PER_RUN = 120
MAX_SAMPLES_PER_PAIR = 48
_MOTION_TOLERANCE = 0.4


@dataclass(frozen=True)
class _Rect:
    x: int
    y: int
    w: int
    h: int


@dataclass
class _PairEpisode:
    pickup_id: int
    person_id: int
    pickup_epoch: int
    person_epoch: int
    started_at: float
    last_seen: float
    samples: deque[dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=MAX_SAMPLES_PER_PAIR)
    )
    best_frame_jpeg: Any | None = None
    best_frame_number: int | None = None
    best_timestamp_sec: float | None = None
    best_person_box: tuple[float, float, float, float] | None = None
    best_pickup_box: tuple[float, float, float, float] | None = None
    best_vehicle_area: float = 0.0
    best_vehicle_class: str | None = None

    @property
    def key(self) -> str:
        start_ms = int(round(self.started_at * 1000.0))
        return (
            f"pickup_{self.pickup_id}_g{self.pickup_epoch}_person_{self.person_id}"
            f"_g{self.person_epoch}_at{start_ms}ms"
        )


def _box(det: Mapping[str, Any]) -> tuple[float, float, float, float]:
    return (
        float(det["bbox_x"]), float(det["bbox_y"]),
        float(det["bbox_w"]), float(det["bbox_h"]),
    )


def _center(box: tuple[float, float, float, float]) -> tuple[float, float]:
    x, y, w, h = box
    return x + w / 2.0, y + h / 2.0


def _center_inside(
    point: tuple[float, float], box: tuple[float, float, float, float]
) -> bool:
    x, y, w, h = box
    return x <= point[0] <= x + w and y <= point[1] <= y + h


def _shared_motion(samples: Sequence[Mapping[str, Any]]) -> dict[str, float] | None:
    if len(samples) < 3:
        return None
    first, last = samples[0], samples[-1]
    duration = float(last["timestamp_sec"]) - float(first["timestamp_sec"])
    if duration <= 0:
        return None
    v0, v1 = first["pickup_center"], last["pickup_center"]
    p0, p1 = first["person_center"], last["person_center"]
    vdx, vdy = float(v1[0]) - float(v0[0]), float(v1[1]) - float(v0[1])
    pdx, pdy = float(p1[0]) - float(p0[0]), float(p1[1]) - float(p0[1])
    vdist, pdist = math.hypot(vdx, vdy), math.hypot(pdx, pdy)
    if vdist < MIN_SHARED_DISPLACEMENT_PX or pdist < MIN_SHARED_DISPLACEMENT_PX:
        return None
    direction_cosine = (vdx * pdx + vdy * pdy) / (vdist * pdist)
    vector_error = math.hypot(vdx - pdx, vdy - pdy)
    if direction_cosine < 0.8 or vector_error > max(16.0, vdist * _MOTION_TOLERANCE):
        return None

    rel_positions = []
    for sample in samples:
        vx, vy, vw, vh = sample["pickup_box"]
        px, py = sample["person_center"]
        if vw <= 0 or vh <= 0:
            return None
        rel_positions.append(((px - vx) / vw, (py - vy) / vh))
    drift = max(
        max(p[0] for p in rel_positions) - min(p[0] for p in rel_positions),
        max(p[1] for p in rel_positions) - min(p[1] for p in rel_positions),
    )
    if drift > MAX_RELATIVE_DRIFT:
        return None
    return {
        "duration_sec": duration,
        "pickup_displacement_px": vdist,
        "person_displacement_px": pdist,
        "direction_cosine": direction_cosine,
        "relative_position_drift": drift,
    }


def _crop_rect(
    pickup_box: tuple[float, float, float, float],
    person_box: tuple[float, float, float, float],
    frame_w: int,
    frame_h: int,
) -> _Rect:
    boxes = (pickup_box, person_box)
    x1 = min(box[0] for box in boxes)
    y1 = min(box[1] for box in boxes)
    x2 = max(box[0] + box[2] for box in boxes)
    y2 = max(box[1] + box[3] for box in boxes)
    pad_x, pad_y = (x2 - x1) * 0.15, (y2 - y1) * 0.15
    left = max(0, int(math.floor(x1 - pad_x)))
    top = max(0, int(math.floor(y1 - pad_y)))
    right = min(frame_w, int(math.ceil(x2 + pad_x)))
    bottom = min(frame_h, int(math.ceil(y2 + pad_y)))
    return _Rect(left, top, max(0, right - left), max(0, bottom - top))


class PickupDetailCollector:
    """Collect bounded evidence leads from existing pickup/person tracks."""

    def __init__(
        self,
        *,
        video_id: int | None,
        run_key: str,
        frame_w: int,
        frame_h: int,
        processing_run_id: int | None = None,
        enabled: bool = True,
        writer: Callable[..., Mapping[str, Any] | None] | None = None,
        max_candidates: int = MAX_CANDIDATES_PER_RUN,
        max_active_pairs: int = MAX_ACTIVE_PAIRS,
        expiry_sec: float = TRACK_EXPIRY_SEC,
    ) -> None:
        self.video_id = int(video_id) if video_id is not None else None
        self.run_key = str(run_key)
        self.frame_w, self.frame_h = int(frame_w), int(frame_h)
        self.processing_run_id = int(processing_run_id) if processing_run_id is not None else None
        self.enabled = bool(enabled) and self.frame_w > 0 and self.frame_h > 0
        self.max_candidates = max(1, int(max_candidates))
        self.max_active_pairs = max(1, int(max_active_pairs))
        self.expiry_sec = max(0.1, float(expiry_sec))
        self._writer = writer or save_pickup_detail_evidence
        self._pairs: dict[tuple[int, int, int, int], _PairEpisode] = {}
        self._candidates: list[dict[str, Any]] = []
        self._persisted_occurrences: set[str] = set()
        self._emitted: set[tuple[int, int, int, int]] = set()
        self._crop_reviewed_pickups: set[tuple[int, int]] = set()
        self.budget_exhausted = False

    def observe(
        self,
        frame: Any,
        tracked: Sequence[Mapping[str, Any]],
        *,
        frame_number: int,
        timestamp_sec: float,
    ) -> None:
        if not self.enabled or self.budget_exhausted:
            return
        ts = float(timestamp_sec)
        pickups = []
        persons = []
        for det in tracked:
            label = str(det.get("class_label") or "")
            if label == YOLO_CLASS_PERSON and det.get("track_id") is not None:
                persons.append(det)
            resolved = det.get("resolved_track_class", label)
            if (
                str(resolved or "") in {YOLO_CLASS_PICKUP_TRUCK, "car"}
                and det.get("track_id") is not None
            ):
                pickups.append(det)

        active: set[tuple[int, int, int, int]] = set()
        observed_pickups = {
            int(det["track_id"]): int(det.get("track_identity_epoch") or 0)
            for det in pickups
        }
        observed_persons = {
            int(det["track_id"]): int(det.get("track_identity_epoch") or 0)
            for det in persons
        }
        for pickup in pickups:
            pbox = _box(pickup)
            pc = _center(pbox)
            pickup_id = int(pickup["track_id"])
            pickup_epoch = int(pickup.get("track_identity_epoch") or 0)
            vehicle_class = str(
                pickup.get("resolved_track_class", pickup.get("class_label", ""))
            )
            for person in persons:
                person_box = _box(person)
                person_center = _center(person_box)
                person_id = int(person["track_id"])
                person_epoch = int(person.get("track_identity_epoch") or 0)
                is_crop_submodel_person = (
                    str(person.get("source") or "") == "pickup_person_crop_submodel"
                    and int(person.get("source_pickup_track_id") or -1) == pickup_id
                    and int(person.get("source_pickup_identity_epoch") or 0) == pickup_epoch
                )
                if vehicle_class != YOLO_CLASS_PICKUP_TRUCK and not is_crop_submodel_person:
                    continue
                if not is_crop_submodel_person and not _center_inside(person_center, pbox):
                    continue
                key = (pickup_id, pickup_epoch, person_id, person_epoch)
                active.add(key)
                episode = self._pairs.get(key)
                if episode is None or ts <= episode.last_seen or ts - episode.last_seen > self.expiry_sec:
                    episode = _PairEpisode(
                        pickup_id, person_id, pickup_epoch, person_epoch, ts, ts
                    )
                    self._pairs[key] = episode
                episode.last_seen = ts
                episode.samples.append({
                    "timestamp_sec": ts,
                    "frame_number": int(frame_number),
                    "pickup_box": pbox,
                    "person_box": person_box,
                    "pickup_center": pc,
                    "person_center": person_center,
                    "person_source": str(person.get("source") or "main_detector_and_bytetrack"),
                    "vehicle_class_at_scan": str(
                        person.get("source_vehicle_class") or vehicle_class
                    ),
                })
                if is_crop_submodel_person and pbox[2] * pbox[3] >= episode.best_vehicle_area:
                    encoded_ok, encoded = cv2.imencode(
                        ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 82]
                    )
                    if encoded_ok:
                        episode.best_frame_jpeg = encoded
                        episode.best_frame_number = int(frame_number)
                        episode.best_timestamp_sec = ts
                        episode.best_person_box = person_box
                        episode.best_pickup_box = pbox
                        episode.best_vehicle_area = pbox[2] * pbox[3]
                        episode.best_vehicle_class = str(
                            person.get("source_vehicle_class") or vehicle_class
                        )
                if key in self._emitted:
                    continue
                if is_crop_submodel_person:
                    pickup_identity = (pickup_id, pickup_epoch)
                    if pickup_identity in self._crop_reviewed_pickups:
                        continue
                    if ts - episode.started_at < MIN_CROP_ASSOCIATION_SEC:
                        continue
                    evidence_frame = frame
                    evidence_frame_number = int(frame_number)
                    evidence_timestamp = ts
                    evidence_person_box = person_box
                    evidence_pickup_box = pbox
                    if episode.best_frame_jpeg is not None:
                        best = cv2.imdecode(episode.best_frame_jpeg, cv2.IMREAD_COLOR)
                        if best is not None:
                            evidence_frame = best
                            evidence_frame_number = int(
                                episode.best_frame_number or frame_number
                            )
                            evidence_timestamp = float(
                                episode.best_timestamp_sec
                                if episode.best_timestamp_sec is not None else ts
                            )
                            evidence_person_box = episode.best_person_box or person_box
                            evidence_pickup_box = episode.best_pickup_box or pbox
                    if self._emit_candidate(
                        episode,
                        key,
                        evidence_frame,
                        evidence_frame_number,
                        evidence_timestamp,
                        evidence_person_box,
                        evidence_pickup_box,
                        {
                            "state": "crop_person_scan_positive",
                            "person_source": "pickup_person_crop_submodel",
                            "minimum_persistence_sec": MIN_CROP_ASSOCIATION_SEC,
                            "evidence_vehicle_area": episode.best_vehicle_area,
                        },
                    ):
                        self._crop_reviewed_pickups.add(pickup_identity)
                    continue
                if ts - episode.started_at < MIN_ASSOCIATION_SEC:
                    continue
                motion = _shared_motion(tuple(episode.samples))
                if motion is None:
                    continue
                self._emit_candidate(
                    episode,
                    key,
                    frame,
                    int(frame_number),
                    ts,
                    person_box,
                    pbox,
                    motion,
                )
        for key, episode in list(self._pairs.items()):
            pickup_seen = observed_pickups.get(episode.pickup_id) == episode.pickup_epoch
            person_seen = observed_persons.get(episode.person_id) == episode.person_epoch
            if key not in active and pickup_seen and person_seen:
                # Both tracks were present but the person left the pickup box;
                # do not bridge two separate overlap episodes.
                self._pairs.pop(key, None)
                self._emitted.discard(key)
                continue
            if key not in active and ts - episode.last_seen > self.expiry_sec:
                self._pairs.pop(key, None)
        if len(self._pairs) > self.max_active_pairs:
            oldest = sorted(self._pairs, key=lambda item: self._pairs[item].last_seen)
            for key in oldest[:len(self._pairs) - self.max_active_pairs]:
                self._pairs.pop(key, None)

    def _emit_candidate(
        self,
        episode: _PairEpisode,
        key: tuple[int, int, int, int],
        frame: Any,
        frame_number: int,
        timestamp_sec: float,
        person_box: tuple[float, float, float, float],
        pickup_box: tuple[float, float, float, float],
        motion: Mapping[str, Any],
    ) -> bool:
        occurrence_key = episode.key
        rect = _crop_rect(pickup_box, person_box, self.frame_w, self.frame_h)
        try:
            saved = self._writer(
                frame,
                rect,
                run_key=self.run_key,
                occurrence_key=occurrence_key,
                frame_index=1,
            )
        except Exception:
            logger.exception("pickup review evidence write failed for %s", occurrence_key)
            return False
        if not saved or not saved.get("scene_path") or not saved.get("crop_path"):
            return False
        samples = [
            {
                "timestamp_sec": float(item["timestamp_sec"]),
                "frame_number": int(item["frame_number"]),
                "pickup_bbox": dict(zip(("x", "y", "w", "h"), item["pickup_box"])),
                "person_bbox": dict(zip(("x", "y", "w", "h"), item["person_box"])),
            }
            for item in episode.samples
        ]
        frame = {
            "index": 1,
            "frame_number": frame_number,
            "timestamp_sec": timestamp_sec,
            "scene_path": str(saved["scene_path"]),
            "crop_path": str(saved["crop_path"]),
            "crop": {"x": rect.x, "y": rect.y, "w": rect.w, "h": rect.h},
            "pickup_bbox": dict(zip(("x", "y", "w", "h"), pickup_box)),
            "person_bbox": dict(zip(("x", "y", "w", "h"), person_box)),
            "size_bytes": int(saved.get("size_bytes") or 0),
        }
        self._candidates.append({
            "video_id": self.video_id,
            "run_key": self.run_key,
            "processing_run_id": self.processing_run_id,
            "pickup_track_id": episode.pickup_id,
            "person_track_id": episode.person_id,
            "pickup_identity_epoch": episode.pickup_epoch,
            "person_identity_epoch": episode.person_epoch,
            "occurrence_key": occurrence_key,
            "selector_version": PICKUP_REVIEW_SELECTOR_VERSION,
            "timestamp_sec": timestamp_sec,
            "episode_start_sec": episode.started_at,
            "frame_number": frame_number,
            "source_width": self.frame_w,
            "source_height": self.frame_h,
            "frame_count": len(samples),
            "frames_json": json.dumps([frame]),
            "association_json": json.dumps({
                "state": str(motion.get("state") or "shared_motion_candidate"),
                "evidence_source": (
                    "pickup_person_crop_submodel"
                    if any(item.get("person_source") == "pickup_person_crop_submodel" for item in episode.samples)
                    else "main_detector_and_bytetrack"
                ),
                "observations": samples,
                "vehicle_class_at_scan": str(
                    episode.best_vehicle_class
                    or episode.samples[-1].get("vehicle_class_at_scan")
                    or YOLO_CLASS_PICKUP_TRUCK
                ),
                "crop_scan": {
                    "minimum_persistence_sec": motion.get("minimum_persistence_sec"),
                    "evidence_vehicle_area": motion.get("evidence_vehicle_area"),
                } if motion.get("minimum_persistence_sec") is not None else {},
                "motion": {
                    key: value for key, value in motion.items()
                    if key not in {
                        "state", "person_source", "minimum_persistence_sec",
                        "evidence_vehicle_area",
                    }
                },
                "cargo_region_visible": None,
                "lower_body_visible": None,
            }),
            "size_bytes": int(saved.get("size_bytes") or 0),
        })
        self._emitted.add(key)
        if len(self._candidates) >= self.max_candidates:
            self.budget_exhausted = True
        return True

    def candidates(self) -> list[dict[str, Any]]:
        return list(self._candidates)

    def persist(self, adapter: Any | None = None) -> int:
        if adapter is None:
            from database import db as adapter
        count = 0
        for payload in self._candidates:
            try:
                adapter.upsert_pickup_detail_candidate(**payload)
                count += 1
            except Exception:
                logger.exception("failed to persist pickup review candidate %s", payload.get("occurrence_key"))
        return count

    def persist_new(self, adapter: Any | None = None) -> int:
        """Persist only leads not yet written by this live worker."""
        if adapter is None:
            from database import db as adapter
        count = 0
        for payload in self._candidates:
            key = str(payload.get("occurrence_key") or "")
            if not key or key in self._persisted_occurrences:
                continue
            try:
                adapter.upsert_pickup_detail_candidate(**payload)
                self._persisted_occurrences.add(key)
                count += 1
            except Exception:
                logger.exception("failed to persist pickup review candidate %s", key)
        return count

    def discard(self) -> None:
        remove_pickup_detail_run(self.run_key)

    def summary(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "candidates": len(self._candidates),
            "budget_exhausted": self.budget_exhausted,
            "active_pairs": len(self._pairs),
            "source": "main_detector_and_bytetrack",
        }
