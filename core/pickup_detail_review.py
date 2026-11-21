"""Review-only pickup/person association leads from the main detector tracks.

This collector does not run another detector and never produces a violation.
Cargo-region and lower-body visibility are deliberately left unknown until an
explicit evidence producer supplies them.
"""

from __future__ import annotations

import json
import logging
import math
from collections import Counter, deque
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

import cv2

from core.detection_config import YOLO_CLASS_PERSON, YOLO_CLASS_PICKUP_TRUCK
from core.evidence import remove_pickup_detail_run, save_pickup_detail_evidence
from core.tracker import TRACK_EXPIRY_SEC

logger = logging.getLogger(__name__)

PICKUP_REVIEW_SELECTOR_VERSION = "pickup-review-2"
MIN_ASSOCIATION_SEC = 2.0
MIN_CROP_ASSOCIATION_SEC = 1.1
# A parked pickup gives no shared-motion evidence, so a crop rider on it must
# persist longer before review.
MIN_STATIONARY_CROP_SEC = 2.0
MIN_ELEVATED_FRACTION = 0.5
MIN_AMBIGUOUS_SAMPLES = 4
MAX_AMBIGUOUS_GAP_SEC = 0.6
MAX_AMBIGUOUS_WINDOW_SEC = 2.5
MAX_CROP_DECISIONS = 200
MIN_SHARED_DISPLACEMENT_PX = 40.0
MAX_RELATIVE_DRIFT = 0.22
MAX_ACTIVE_PAIRS = 96
MAX_CANDIDATES_PER_RUN = 120
MAX_SAMPLES_PER_PAIR = 48
MAX_CROP_SAMPLES_PER_PAIR = 120
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
    samples: deque[dict[str, Any]]
    best_frame_jpeg: Any | None = None
    best_sample: dict[str, Any] | None = None
    best_frame_score: tuple[bool, float] = (False, 0.0)

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


def _crop_motion(
    samples: Sequence[Mapping[str, Any]],
    *,
    minimum_displacement: float = MIN_SHARED_DISPLACEMENT_PX,
) -> dict[str, Any]:
    """Classify how a crop-detected person moved relative to its pickup."""
    first, last = samples[0], samples[-1]
    v0, v1 = first["pickup_center"], last["pickup_center"]
    p0, p1 = first["person_center"], last["person_center"]
    vdx, vdy = float(v1[0]) - float(v0[0]), float(v1[1]) - float(v0[1])
    pdx, pdy = float(p1[0]) - float(p0[0]), float(p1[1]) - float(p0[1])
    vdist, pdist = math.hypot(vdx, vdy), math.hypot(pdx, pdy)
    rel_positions = []
    for sample in samples:
        vx, vy, vw, vh = sample["pickup_box"]
        px, py = sample["person_center"]
        if vw > 0 and vh > 0:
            rel_positions.append(((px - vx) / vw, (py - vy) / vh))
    drift = max(
        max(p[0] for p in rel_positions) - min(p[0] for p in rel_positions),
        max(p[1] for p in rel_positions) - min(p[1] for p in rel_positions),
    ) if rel_positions else 1.0
    if drift > MAX_RELATIVE_DRIFT:
        state = "moved_independently"
    elif vdist < minimum_displacement:
        state = "moved_independently" if pdist >= minimum_displacement else "stationary"
    else:
        cosine = (vdx * pdx + vdy * pdy) / (vdist * pdist) if pdist > 0 else -1.0
        vector_error = math.hypot(vdx - pdx, vdy - pdy)
        state = (
            "moving_with_pickup"
            if cosine >= 0.8 and vector_error <= max(16.0, vdist * _MOTION_TOLERANCE)
            else "moved_independently"
        )
    return {
        "motion_state": state,
        "duration_sec": float(last["timestamp_sec"]) - float(first["timestamp_sec"]),
        "pickup_displacement_px": vdist,
        "person_displacement_px": pdist,
        "relative_position_drift": drift,
    }


def _ambiguous_motion(samples: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    if len(samples) < MIN_AMBIGUOUS_SAMPLES:
        return None
    start, end = samples[0]["timestamp_sec"], samples[-1]["timestamp_sec"]
    if end - start < MIN_CROP_ASSOCIATION_SEC:
        return None
    if any(
        current["timestamp_sec"] - previous["timestamp_sec"] > MAX_AMBIGUOUS_GAP_SEC
        for previous, current in zip(samples, samples[1:])
    ):
        return None
    if any(sample["vehicle_class_at_scan"] != YOLO_CLASS_PICKUP_TRUCK for sample in samples):
        return None
    motion = _crop_motion(samples)
    if motion["motion_state"] != "moving_with_pickup":
        return None
    midpoint = (start + end) / 2.0
    split = min(
        range(1, len(samples) - 1),
        key=lambda i: abs(samples[i]["timestamp_sec"] - midpoint),
    )
    for segment in (samples[:split + 1], samples[split:]):
        segment_duration = segment[-1]["timestamp_sec"] - segment[0]["timestamp_sec"]
        minimum_displacement = MIN_SHARED_DISPLACEMENT_PX * segment_duration / (end - start)
        segment_motion = _crop_motion(segment, minimum_displacement=minimum_displacement)
        if segment_motion["motion_state"] != "moving_with_pickup":
            return None
    return motion


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
        self._reviewed_pickups: set[tuple[int, int]] = set()
        self._crop_decision_counts: Counter[str] = Counter()
        self._crop_decisions: list[dict[str, Any]] = []
        self._crop_decision_seen: set[tuple[int, int, str]] = set()
        self.budget_exhausted = False

    def _record_crop_decision(
        self, pickup_identity: tuple[int, int], timestamp_sec: float, decision: str
    ) -> None:
        self._crop_decision_counts[decision] += 1
        seen_key = (*pickup_identity, decision)
        if seen_key in self._crop_decision_seen or len(self._crop_decisions) >= MAX_CROP_DECISIONS:
            return
        self._crop_decision_seen.add(seen_key)
        self._crop_decisions.append({
            "pickup_track_id": pickup_identity[0],
            "pickup_identity_epoch": pickup_identity[1],
            "first_timestamp_sec": round(float(timestamp_sec), 3),
            "decision": decision,
        })

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
            resolved = det.get("resolved_track_class") or label
            if (
                (str(resolved) in {YOLO_CLASS_PICKUP_TRUCK, "car"} or label == YOLO_CLASS_PICKUP_TRUCK)
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
        # A main-detector person linked to a crop must not bypass the crop's
        # ownership checks or start another pair under a different pickup.
        crop_covered_primary = {
            int(det["overlaps_primary_track_id"])
            for det in persons
            if str(det.get("source") or "") == "pickup_person_crop_submodel"
            and det.get("overlaps_primary_track_id") is not None
        }
        pickup_boxes = [
            _box(det) for det in pickups
            if (det.get("resolved_track_class") or det.get("class_label")) == YOLO_CLASS_PICKUP_TRUCK
        ]
        for pickup in pickups:
            if self.budget_exhausted:
                break
            pbox = _box(pickup)
            pc = _center(pbox)
            pickup_id = int(pickup["track_id"])
            pickup_epoch = int(pickup.get("track_identity_epoch") or 0)
            pickup_identity = (pickup_id, pickup_epoch)
            if pickup_identity in self._reviewed_pickups:
                continue
            vehicle_class = str(
                pickup.get("resolved_track_class") or pickup.get("class_label") or ""
            )
            for person in persons:
                person_box = _box(person)
                person_center = _center(person_box)
                person_id = int(person["track_id"])
                person_epoch = int(person.get("track_identity_epoch") or 0)
                is_crop_submodel_person = person.get("source") == "pickup_person_crop_submodel"
                if is_crop_submodel_person:
                    source_pickup = (
                        person.get("source_pickup_track_id"),
                        person.get("source_pickup_identity_epoch"),
                    )
                    if source_pickup != pickup_identity:
                        continue
                    if person.get("pickup_ownership_contested"):
                        self._record_crop_decision(pickup_identity, ts, "pickup_ownership_contested")
                        continue
                elif (
                    vehicle_class != YOLO_CLASS_PICKUP_TRUCK
                    or person_id in crop_covered_primary
                    or not _center_inside(person_center, pbox)
                    or sum(_center_inside(person_center, box) for box in pickup_boxes) > 1
                ):
                    continue
                key = (pickup_id, pickup_epoch, person_id, person_epoch)
                active.add(key)
                episode = self._pairs.get(key)
                if episode is None or ts <= episode.last_seen or ts - episode.last_seen > self.expiry_sec:
                    episode = _PairEpisode(
                        pickup_id, person_id, pickup_epoch, person_epoch, ts, ts,
                        deque(maxlen=MAX_CROP_SAMPLES_PER_PAIR if is_crop_submodel_person
                              else MAX_SAMPLES_PER_PAIR),
                    )
                    self._pairs[key] = episode
                episode.last_seen = ts
                sample = {
                    "timestamp_sec": ts,
                    "frame_number": int(frame_number),
                    "pickup_box": pbox,
                    "person_box": person_box,
                    "pickup_detector_class": str(pickup.get("class_label") or vehicle_class),
                    "pickup_detector_score": pickup.get("confidence"),
                    "person_detector_score": person.get("confidence"),
                    "pickup_center": pc,
                    "person_center": person_center,
                    "person_source": str(person.get("source") or "main_detector_and_bytetrack"),
                    "vehicle_class_at_scan": str(
                        person.get("source_vehicle_class") or vehicle_class
                    ),
                    "rider_geometry": person.get("rider_geometry"),
                }
                episode.samples.append(sample)
                if is_crop_submodel_person:
                    elevated_now = (sample["rider_geometry"] or {}).get("label") == "elevated"
                    evidence_score = (
                        elevated_now,
                        pbox[2] * pbox[3] if elevated_now else
                        person_box[2] * person_box[3] * float(person.get("confidence") or 0.0),
                    )
                    if (
                        episode.best_sample is None
                        or episode.best_sample["timestamp_sec"] < episode.samples[0]["timestamp_sec"]
                        or evidence_score >= episode.best_frame_score
                    ):
                        encoded_ok, encoded = cv2.imencode(
                            ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 82]
                        )
                        if encoded_ok:
                            episode.best_frame_jpeg = encoded
                            episode.best_sample = sample
                            episode.best_frame_score = evidence_score
                    if ts - episode.started_at < MIN_CROP_ASSOCIATION_SEC:
                        continue
                    all_samples = tuple(episode.samples)
                    samples = all_samples[-MAX_SAMPLES_PER_PAIR:]
                    elevated = sum(
                        1 for item in samples
                        if (item.get("rider_geometry") or {}).get("label") == "elevated"
                    )
                    elevated_fraction = elevated / len(samples)
                    bed_uncertain = elevated_fraction < MIN_ELEVATED_FRACTION
                    if bed_uncertain:
                        samples = tuple(
                            sample for sample in all_samples
                            if ts - sample["timestamp_sec"] <= MAX_AMBIGUOUS_WINDOW_SEC
                        )
                        crop_motion = _ambiguous_motion(samples)
                        if crop_motion is None:
                            self._record_crop_decision(pickup_identity, ts, "ambiguous_motion_unproven")
                            continue
                    else:
                        crop_motion = _crop_motion(samples)
                    if crop_motion["motion_state"] == "moved_independently":
                        self._record_crop_decision(pickup_identity, ts, "moved_independently")
                        continue
                    if (
                        crop_motion["motion_state"] == "stationary"
                        and ts - episode.started_at < MIN_STATIONARY_CROP_SEC
                    ):
                        continue
                    evidence_frame = frame
                    evidence_sample = sample
                    if (
                        episode.best_sample is not None
                        and episode.best_sample["timestamp_sec"] >= samples[0]["timestamp_sec"]
                    ):
                        best = cv2.imdecode(episode.best_frame_jpeg, cv2.IMREAD_COLOR)
                        if best is not None:
                            evidence_frame = best
                            evidence_sample = episode.best_sample
                    evidence_box = evidence_sample["pickup_box"]
                    if self._emit_candidate(
                        episode,
                        evidence_frame,
                        evidence_sample,
                        {
                            "state": "moving_with_pickup_bed_uncertain" if bed_uncertain else "crop_person_scan_positive",
                            "person_source": "pickup_person_crop_submodel",
                            "minimum_persistence_sec": MIN_CROP_ASSOCIATION_SEC,
                            "evidence_vehicle_area": evidence_box[2] * evidence_box[3],
                            "rider_geometry": {
                                "label": "ambiguous" if bed_uncertain else "elevated",
                                "elevated_fraction": round(elevated_fraction, 3),
                                "last": samples[-1].get("rider_geometry"),
                            },
                            **crop_motion,
                        },
                        samples=samples,
                    ):
                        self._record_crop_decision(
                            pickup_identity, ts, f"lead_created_{crop_motion['motion_state']}"
                        )
                        break
                    continue
                if ts - episode.started_at < MIN_ASSOCIATION_SEC:
                    continue
                motion = _shared_motion(tuple(episode.samples))
                if motion is None:
                    continue
                if self._emit_candidate(
                    episode,
                    frame,
                    sample,
                    motion,
                    samples=tuple(episode.samples),
                ):
                    break
        for key, episode in list(self._pairs.items()):
            pickup_seen = observed_pickups.get(episode.pickup_id) == episode.pickup_epoch
            person_seen = observed_persons.get(episode.person_id) == episode.person_epoch
            if key not in active and pickup_seen and person_seen:
                # Both tracks were present but the person left the pickup box;
                # do not bridge two separate overlap episodes.
                self._pairs.pop(key, None)
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
        frame: Any,
        evidence_sample: Mapping[str, Any],
        motion: Mapping[str, Any],
        *,
        samples: Sequence[Mapping[str, Any]],
    ) -> bool:
        frame_number = evidence_sample["frame_number"]
        timestamp_sec = evidence_sample["timestamp_sec"]
        pickup_box = evidence_sample["pickup_box"]
        person_box = evidence_sample["person_box"]
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
            self._record_crop_decision((episode.pickup_id, episode.pickup_epoch), timestamp_sec, "evidence_write_failed")
            return False
        if not saved or not saved.get("scene_path") or not saved.get("crop_path"):
            self._record_crop_decision((episode.pickup_id, episode.pickup_epoch), timestamp_sec, "evidence_write_failed")
            return False
        observations = [
            {
                "timestamp_sec": float(item["timestamp_sec"]),
                "frame_number": int(item["frame_number"]),
                "pickup_bbox": dict(zip(("x", "y", "w", "h"), item["pickup_box"])),
                "person_bbox": dict(zip(("x", "y", "w", "h"), item["person_box"])),
                "geometry_label": (item.get("rider_geometry") or {}).get("label"),
            }
            for item in samples
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
            "pickup_detector_class": evidence_sample["pickup_detector_class"],
            "pickup_detector_score": evidence_sample["pickup_detector_score"],
            "person_detector_score": evidence_sample["person_detector_score"],
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
            "episode_start_sec": samples[0]["timestamp_sec"],
            "frame_number": frame_number,
            "source_width": self.frame_w,
            "source_height": self.frame_h,
            "frame_count": len(observations),
            "frames_json": json.dumps([frame]),
            "association_json": json.dumps({
                "state": str(motion.get("state") or "shared_motion_candidate"),
                "evidence_source": (
                    "pickup_person_crop_submodel"
                    if any(item.get("person_source") == "pickup_person_crop_submodel" for item in episode.samples)
                    else "main_detector_and_bytetrack"
                ),
                "observations": observations,
                "vehicle_class_at_scan": evidence_sample["vehicle_class_at_scan"],
                "crop_scan": {
                    "minimum_persistence_sec": motion.get("minimum_persistence_sec"),
                    "evidence_vehicle_area": motion.get("evidence_vehicle_area"),
                } if motion.get("minimum_persistence_sec") is not None else {},
                "rider_geometry": motion.get("rider_geometry"),
                "motion": {
                    key: value for key, value in motion.items()
                    if key not in {
                        "state", "person_source", "minimum_persistence_sec",
                        "evidence_vehicle_area", "rider_geometry",
                    }
                },
                "cargo_region_visible": None,
                "lower_body_visible": None,
            }),
            "size_bytes": int(saved.get("size_bytes") or 0),
        })
        self._reviewed_pickups.add((episode.pickup_id, episode.pickup_epoch))
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
            "crop_decision_counts": dict(self._crop_decision_counts),
            "crop_decisions": list(self._crop_decisions),
        }
