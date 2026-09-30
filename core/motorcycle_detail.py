"""Motorcycle detail review: candidate frame selection and observation association.

Scope (uploaded-video processing only; live stream integration is deferred):

- group ``motorcycle`` detections by processing run and *track occurrence*
- score only frames the main pipeline already processed (never rescan every frame)
- keep at most two useful, temporally separated frames per occurrence
- build bounded, run-scoped evidence (source-resolution scene + padded
  motorcycle/rider crop) through :mod:`core.evidence`
- associate ``rider`` (never generic ``person``), helmet labels, and
  ``side_mirror`` observations for a *separate human-review queue*
- store the main detector's rider-association state and nearby *other*
  motorcycles/riders with each selected frame (``main_context`` inside
  ``frames_json``), because the three-class detail model cannot output
  ``rider`` or ``motorcycle`` itself

Hard boundaries:

- Nothing in this module declares, confirms, or creates a violation.
- A missing mirror detection means *unknown*, never proof of absence.
- ``core.violation_engine`` never imports this module; crop-scan observations
  are stored in their own table and cannot reach the rule engine.

Every threshold here is an engineering heuristic for review routing. None of
them is a validated precision/recall claim (see
``docs/specs/2026-09-28-motorcycle-detail-review-design.md``).
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import cv2

from core.detection_config import (
    YOLO_CLASS_HELMET,
    YOLO_CLASS_HELMET_ACCEPTABLE,
    YOLO_CLASS_HELMET_NUT_SHELL,
    YOLO_CLASS_MOTORCYCLE,
    YOLO_CLASS_RIDER,
    YOLO_CLASS_SIDE_MIRROR,
)
from core.tracker import TRACK_EXPIRY_SEC

logger = logging.getLogger(__name__)

# Selector identity: part of the candidate dedup key. Bump when the selection
# policy changes in a way that should produce distinct candidates.
DETAIL_SELECTOR_VERSION = "md-1.0"

# --- Bounds (memory, queue, disk, candidate count) -------------------------
MAX_FRAMES_PER_OCCURRENCE = 2
MAX_ACTIVE_OCCURRENCES = 24
MAX_CANDIDATES_PER_RUN = 120
MAX_SCORED_FRAMES_PER_OCCURRENCE = 60
MAX_EVIDENCE_WRITES_PER_OCCURRENCE = 6
# Non-motorcycle tracks (cars, people, helmets, ...) are tracked only so a
# class change on a motorcycle ID is still detected. They get their own, larger
# budget and can therefore never evict a motorcycle occurrence.
MAX_FOREIGN_OCCURRENCES = 96
# Generation counters remember ID *reuse* for every motorcycle ID ever seen.
# They are bounded and trimmed oldest-first, never below the active set.
MAX_TRACKED_GENERATIONS = 256

# --- Selection heuristics ---------------------------------------------------
MIN_FRAME_SEPARATION_SEC = 0.4
SCORE_REPLACEMENT_MARGIN = 0.02
MIN_CROP_SIDE_PX = 64
PAD_LEFT_RIGHT_FRAC = 0.20
PAD_TOP_FRAC = 0.35  # helmets and mirrors sit above a tight vehicle box
PAD_BOTTOM_FRAC = 0.12
MAX_SCAN_SCALE = 3.0

SCORE_WEIGHTS: dict[str, float] = {
    "visibility": 0.20,
    "sharpness": 0.22,
    "size": 0.16,
    "clipping": 0.14,
    "occlusion": 0.14,
    "viewpoint": 0.14,
}

# --- Association heuristics -------------------------------------------------
RIDER_MIN_AREA_OVERLAP = 0.05
RIDER_AMBIGUOUS_IOU = 0.25
RIDER_HEAD_TOP_FRAC = 0.45
MIRROR_MOUNT_BAND_FRAC = 0.60
MIRROR_MAX_WIDTH_RATIO = 0.75
MOTORCYCLE_AMBIGUOUS_IOU = 0.20
# A scan detection that matches the *stored* target motorcycle this closely is
# the same motorcycle re-detected inside the padded crop, not a second one.
MOTORCYCLE_TARGET_MATCH_IOU = 0.30

# Association states (explicit, never silently guessed).
STATE_ASSOCIATED = "associated"
STATE_AMBIGUOUS = "ambiguous"
STATE_UNASSOCIATED = "unassociated"
# Helmet *states* as reported to reviewers (not detector label names).
HELMET_ACCEPTABLE = "acceptable"
HELMET_NUT_SHELL = "nut_shell"
HELMET_UNKNOWN = "unknown"
MIRROR_UNKNOWN = "unknown"
MIRROR_BOTH = "both_visible"
MIRROR_ONE_LEFT = "one_left"
MIRROR_ONE_RIGHT = "one_right"
MIRROR_NONE_VISIBLE = "none_visible"


class DetailSelectionError(ValueError):
    """Invalid detail-selection input or unsafe evidence identifier."""


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


Box = tuple[float, float, float, float]


def box_of(det: Mapping[str, Any]) -> Box:
    """Return ``(x1, y1, x2, y2)`` for a pipeline detection dict."""
    x = float(det.get("bbox_x") or 0.0)
    y = float(det.get("bbox_y") or 0.0)
    w = float(det.get("bbox_w") or 0.0)
    h = float(det.get("bbox_h") or 0.0)
    return (x, y, x + w, y + h)


def box_area(box: Box) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def intersection_area(a: Box, b: Box) -> float:
    dx = min(a[2], b[2]) - max(a[0], b[0])
    dy = min(a[3], b[3]) - max(a[1], b[1])
    if dx <= 0 or dy <= 0:
        return 0.0
    return float(dx) * float(dy)


def box_iou(a: Box, b: Box) -> float:
    inter = intersection_area(a, b)
    if inter <= 0:
        return 0.0
    union = box_area(a) + box_area(b) - inter
    return float(inter / union) if union > 0 else 0.0


def clamp_box(box: Box, width: int, height: int) -> Box:
    """Clamp a box to ``[0, width] x [0, height]`` preserving non-inversion."""
    x1 = min(max(float(box[0]), 0.0), float(width))
    y1 = min(max(float(box[1]), 0.0), float(height))
    x2 = min(max(float(box[2]), x1), float(width))
    y2 = min(max(float(box[3]), y1), float(height))
    return (x1, y1, x2, y2)


def is_motorcycle_detection(det: Mapping[str, Any]) -> bool:
    """True for a ``motorcycle`` detection carrying a usable ByteTrack ID.

    ``bicycle`` is a separate canonical class and is therefore excluded. A
    detection without a ByteTrack ID cannot be grouped by occurrence and is
    skipped rather than guessed.
    """
    if str(det.get("class_label") or "").strip().lower() != YOLO_CLASS_MOTORCYCLE:
        return False
    track_id = det.get("track_id")
    if track_id is None or isinstance(track_id, bool):
        return False
    try:
        return int(track_id) >= 0
    except (TypeError, ValueError):
        return False


def is_rider_detection(det: Mapping[str, Any]) -> bool:
    """True only for an explicit ``rider`` label.

    Generic ``person`` is deliberately excluded: rider association drives
    helmet-adjacent review, and ``person`` may be a pedestrian.
    """
    return str(det.get("class_label") or "").strip().lower() == YOLO_CLASS_RIDER



@dataclass(frozen=True)
class CropRect:
    """Integer crop rectangle in *source frame* pixel coordinates."""

    x: int
    y: int
    w: int
    h: int

    def as_box(self) -> Box:
        return (float(self.x), float(self.y), float(self.x + self.w), float(self.y + self.h))

    def as_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}


def padded_crop_rect(
    box: Box,
    frame_w: int,
    frame_h: int,
    *,
    rider_box: Box | None = None,
    pad_left_right: float = PAD_LEFT_RIGHT_FRAC,
    pad_top: float = PAD_TOP_FRAC,
    pad_bottom: float = PAD_BOTTOM_FRAC,
    min_side: int = MIN_CROP_SIDE_PX,
) -> CropRect | None:
    """Padded motorcycle/rider crop rectangle clamped to the frame.

    The existing tight vehicle crop in ``core.evidence`` clips a helmet or a
    mirror above the vehicle box, so the detail crop pads the top band, includes
    the associated rider box when one exists, and grows tiny crops to a usable
    minimum side. Returns ``None`` when nothing usable remains in frame.
    """
    if frame_w <= 0 or frame_h <= 0:
        return None
    base = clamp_box(box, frame_w, frame_h)
    if rider_box is not None:
        rider = clamp_box(rider_box, frame_w, frame_h)
        base = (
            min(base[0], rider[0]),
            min(base[1], rider[1]),
            max(base[2], rider[2]),
            max(base[3], rider[3]),
        )
    bw = base[2] - base[0]
    bh = base[3] - base[1]
    if bw <= 0 and bh <= 0:
        return None
    x1 = base[0] - bw * pad_left_right
    x2 = base[2] + bw * pad_left_right
    y1 = base[1] - bh * pad_top
    y2 = base[3] + bh * pad_bottom

    # Grow to the minimum usable side around the padded centre, then clamp.
    width = x2 - x1
    height = y2 - y1
    if width < min_side:
        extra = (min_side - width) / 2.0
        x1 -= extra
        x2 += extra
    if height < min_side:
        extra = (min_side - height) / 2.0
        y1 -= extra
        y2 += extra

    x1 = max(0.0, x1)
    y1 = max(0.0, y1)
    x2 = min(float(frame_w), x2)
    y2 = min(float(frame_h), y2)
    ix1, iy1, ix2, iy2 = int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2))
    ix2 = min(max(ix2, ix1 + 1), frame_w)
    iy2 = min(max(iy2, iy1 + 1), frame_h)
    ix1 = min(ix1, ix2 - 1)
    iy1 = min(iy1, iy2 - 1)
    if ix2 - ix1 <= 0 or iy2 - iy1 <= 0:
        return None
    return CropRect(x=ix1, y=iy1, w=ix2 - ix1, h=iy2 - iy1)


def scan_scale_for(crop: CropRect, *, min_side: int = 320) -> tuple[float, float]:
    """Uniform upscale factor applied to a crop before the detail pass.

    Small distant motorcycles are the common failure case, so crops below
    ``min_side`` are enlarged (capped at ``MAX_SCAN_SCALE``). Scale 1.0 means the
    crop is scanned at its native size.
    """
    side = min(crop.w, crop.h)
    if side <= 0 or side >= min_side:
        return (1.0, 1.0)
    scale = min(MAX_SCAN_SCALE, float(min_side) / float(side))
    if scale <= 1.0:
        return (1.0, 1.0)
    return (scale, scale)


def map_box_from_crop(
    box: Box,
    crop: CropRect,
    *,
    scale_x: float,
    scale_y: float,
    source_w: int,
    source_h: int,
) -> Box:
    """Map a box measured on the (possibly resized) crop back to source space.

    ``scale_x``/``scale_y`` are the factors applied to the crop *before*
    inference, so dividing by them returns crop-space coordinates. The result is
    clamped to the source frame: an edge-clamped crop still yields in-frame boxes.
    """
    sx = 1.0 if scale_x is None else float(scale_x)
    sy = 1.0 if scale_y is None else float(scale_y)
    if sx <= 0 or sy <= 0:
        raise DetailSelectionError("Scan scale must be positive.")
    x1 = crop.x + float(box[0]) / sx
    y1 = crop.y + float(box[1]) / sy
    x2 = crop.x + float(box[2]) / sx
    y2 = crop.y + float(box[3]) / sy
    return clamp_box((x1, y1, x2, y2), source_w, source_h)


def map_boxes_from_crop(
    boxes: Iterable[Box],
    crop: CropRect,
    *,
    scale_x: float,
    scale_y: float,
    source_w: int,
    source_h: int,
) -> list[Box]:
    return [
        map_box_from_crop(b, crop, scale_x=scale_x, scale_y=scale_y, source_w=source_w, source_h=source_h)
        for b in boxes
    ]


def crop_view(frame: Any, crop: CropRect) -> Any | None:
    """Return the crop region of ``frame`` or None when unusable."""
    if frame is None:
        return None
    h, w = frame.shape[:2]
    restricted = clamp_box(crop.as_box(), w, h)
    x1, y1, x2, y2 = (int(round(v)) for v in restricted)
    if x2 - x1 <= 0 or y2 - y1 <= 0:
        return None
    return frame[y1:y2, x1:x2]


# ---------------------------------------------------------------------------
# Frame scoring (only for frames the pipeline already processed)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FrameScore:
    total: float
    breakdown: dict[str, float] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": round(float(self.total), 4),
            "components": {k: round(float(v), 4) for k, v in self.breakdown.items()},
            "reasons": list(self.reasons),
        }


def sharpness_score(image: Any) -> float:
    """Normalized Laplacian variance of a small image (0..1)."""
    if image is None or getattr(image, "size", 0) == 0:
        return 0.0
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    if variance <= 0:
        return 0.0
    return float(min(1.0, math.log1p(variance) / math.log1p(300.0)))


def score_frame(
    frame: Any,
    detection: Mapping[str, Any],
    *,
    others: Sequence[Mapping[str, Any]] = (),
    rider_box: Box | None = None,
    frame_w: int | None = None,
    frame_h: int | None = None,
    crop: CropRect | None = None,
    conf_floor: float = 0.25,
) -> FrameScore:
    """Score one already-processed frame for detail selection.

    Components: visibility (detector confidence), sharpness (Laplacian variance
    of the crop), size, clipping (how much of the box is inside the frame),
    occlusion (overlap with other detections), and viewpoint (box aspect ratio
    plausibility plus rider visibility). Each component is 0..1 and the weighted
    sum is the selection score. Reasons explain low components to reviewers.
    """
    height, width = (frame.shape[:2] if frame is not None else (frame_h or 0, frame_w or 0))
    width = int(frame_w or width or 0)
    height = int(frame_h or height or 0)
    box = box_of(detection)
    reasons: list[str] = []

    conf = float(detection.get("confidence") or 0.0)
    span = max(1e-6, 1.0 - float(conf_floor))
    visibility = min(1.0, max(0.0, (conf - float(conf_floor)) / span))
    if visibility < 0.35:
        reasons.append("low_detection_confidence")

    region = crop_view(frame, crop) if (frame is not None and crop is not None) else None
    if region is None and frame is not None:
        fallback = padded_crop_rect(box, width, height)
        region = crop_view(frame, fallback) if fallback is not None else None
    sharpness = sharpness_score(region)
    if sharpness < 0.35:
        reasons.append("low_sharpness")

    area_frac = (box_area(clamp_box(box, width, height)) / float(width * height)) if width and height else 0.0
    size = float(min(1.0, math.sqrt(max(0.0, area_frac) / 0.02)))
    if size < 0.35:
        reasons.append("small_object")

    raw_area = box_area(box)
    inside_ratio = (box_area(clamp_box(box, width, height)) / raw_area) if raw_area > 0 else 0.0
    clipping = float(min(1.0, max(0.0, inside_ratio)))
    if clipping < 0.99:
        reasons.append("clipped_by_frame_edge")

    occluded_area = 0.0
    for other in others:
        if other is detection:
            continue
        occluded_area += intersection_area(box, box_of(other))
    occlusion_frac = min(1.0, occluded_area / raw_area) if raw_area > 0 else 0.0
    occlusion = float(1.0 - occlusion_frac)
    if occlusion < 0.65:
        reasons.append("occluded_by_other_object")

    bike_h = max(1.0, box[3] - box[1])
    bike_w = max(1.0, box[2] - box[0])
    aspect = bike_h / bike_w
    viewpoint = float(max(0.0, 1.0 - abs(aspect - 1.2) / 1.2))
    if rider_box is None:
        reasons.append("rider_not_associated")
    else:
        viewpoint = min(1.0, viewpoint + 0.15)
    if viewpoint < 0.35:
        reasons.append("unusual_viewpoint")

    breakdown = {
        "visibility": visibility,
        "sharpness": sharpness,
        "size": size,
        "clipping": clipping,
        "occlusion": occlusion,
        "viewpoint": viewpoint,
    }
    total = sum(SCORE_WEIGHTS.get(k, 0.0) * v for k, v in breakdown.items())
    return FrameScore(total=round(float(total), 4), breakdown=breakdown, reasons=tuple(reasons))


# ---------------------------------------------------------------------------
# Association (rider, helmet, side mirror)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RiderAssociation:
    state: str
    rider_track_id: int | None = None
    rider_box: Box | None = None
    head_region: Box | None = None
    confidence: float = 0.0
    reasons: tuple[str, ...] = ()
    source: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "rider_track_id": self.rider_track_id,
            "rider_box": list(self.rider_box) if self.rider_box else None,
            "head_region": list(self.head_region) if self.head_region else None,
            "confidence": round(float(self.confidence), 4),
            "reasons": list(self.reasons),
            "source": self.source,
        }


@dataclass(frozen=True)
class AttributeObservation:
    """One associated attribute observation (helmet or mirror)."""

    label: str
    confidence: float
    box: Box
    side: str | None = None
    reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "confidence": round(float(self.confidence), 4),
            "box": [round(float(v), 2) for v in self.box],
            "side": self.side,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class AttributeAssociation:
    state: str
    observations: tuple[AttributeObservation, ...] = ()
    reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "observations": [o.as_dict() for o in self.observations],
            "reasons": list(self.reasons),
        }


def rider_head_region(rider_box: Box, *, top_frac: float = RIDER_HEAD_TOP_FRAC) -> Box:
    """Head region of a rider box: top band, slightly widened."""
    x1, y1, x2, y2 = rider_box
    width = x2 - x1
    height = y2 - y1
    widen = width * 0.08
    return (x1 - widen, y1 - height * 0.05, x2 + widen, y1 + height * top_frac)


def associate_rider(
    motorcycle: Mapping[str, Any],
    riders: Sequence[Mapping[str, Any]],
    *,
    min_area_overlap: float = RIDER_MIN_AREA_OVERLAP,
    ambiguous_iou: float = RIDER_AMBIGUOUS_IOU,
) -> RiderAssociation:
    """Associate a ``rider`` (never ``person``) with a motorcycle.

    Candidates must overlap the *upper* body of the motorcycle box, which is
    where a seated rider sits. More than one plausible rider is reported as
    ``ambiguous`` instead of guessing.
    """
    bike = box_of(motorcycle)
    bike_w = max(1.0, bike[2] - bike[0])
    bike_h = max(1.0, bike[3] - bike[1])
    upper = (bike[0] - bike_w * 0.15, bike[1] - bike_h * 0.55, bike[2] + bike_w * 0.15, bike[3])
    candidates: list[tuple[float, Mapping[str, Any], Box]] = []
    for det in riders:
        if not is_rider_detection(det):
            continue
        rbox = box_of(det)
        inter = intersection_area(bike, rbox)
        r_area = box_area(rbox)
        if r_area <= 0 or inter <= 0:
            continue
        if (inter / r_area) < min_area_overlap:
            continue
        if not _boxes_overlap(upper, rbox):
            continue
        candidates.append((inter / r_area, det, rbox))

    if not candidates:
        return RiderAssociation(state=STATE_UNASSOCIATED, reasons=("no_rider_overlapping_motorcycle",))

    candidates.sort(key=lambda item: item[0], reverse=True)
    best_overlap, best_det, best_box = candidates[0]
    ambiguous = [c for c in candidates[1:] if box_iou(best_box, c[2]) >= ambiguous_iou]
    if ambiguous:
        return RiderAssociation(
            state=STATE_AMBIGUOUS,
            confidence=round(float(best_overlap), 4),
            reasons=("multiple_riders_overlap_motorcycle",),
        )

    head = rider_head_region(best_box)
    return RiderAssociation(
        state=STATE_ASSOCIATED,
        rider_track_id=_track_id_of(best_det),
        rider_box=best_box,
        head_region=head,
        confidence=round(float(best_overlap), 4),
        reasons=(),
    )



def associate_helmet(
    motorcycle: Mapping[str, Any],
    rider: RiderAssociation,
    helmets: Sequence[Mapping[str, Any]],
    *,
    other_riders: Sequence[Box] = (),
    other_riders_complete: bool = True,
) -> AttributeAssociation:
    """Associate helmet labels with the associated rider's head region.

    Without an associated rider the helmet state is ``unknown``: an unassociated
    helmet must not be attributed to a motorcycle. Legacy generic ``helmet`` is
    never upgraded to acceptable shape — only ``helmet_acceptable`` sets that.

    ``other_riders`` are *different* riders the main detector saw near the
    target. A helmet that also sits in one of their head regions is not
    attributed; when nothing else remains the state is ``ambiguous``.

    ``other_riders_complete=False`` means the neighbour list was truncated or
    never recorded, so an omitted rider could own any helmet in the head region:
    such helmets are left unattributed (``ambiguous``). With no helmet in the
    head region the state stays ``unknown``.
    """
    if rider.state != STATE_ASSOCIATED or rider.head_region is None:
        reason = "rider_ambiguous" if rider.state == STATE_AMBIGUOUS else "rider_not_associated"
        return AttributeAssociation(state=HELMET_UNKNOWN, reasons=(reason,))
    head = rider.head_region
    other_heads = [rider_head_region(box) for box in other_riders]
    shared = 0
    head_height = max(1.0, head[3] - head[1])
    observations: list[AttributeObservation] = []
    for det in helmets:
        label = str(det.get("class_label") or "").strip().lower()
        if label not in (
            YOLO_CLASS_HELMET_ACCEPTABLE,
            YOLO_CLASS_HELMET_NUT_SHELL,
            YOLO_CLASS_HELMET,
        ):
            continue
        hbox = box_of(det)
        if box_area(hbox) <= 0:
            continue
        centre = ((hbox[0] + hbox[2]) / 2.0, (hbox[1] + hbox[3]) / 2.0)
        area_overlap = intersection_area(head, hbox) / box_area(hbox)
        sits_on_head = (
            head[0] <= centre[0] <= head[2]
            and hbox[3] >= head[1] - head_height * 0.6
            and hbox[3] <= head[1] + head_height * 0.35
        )
        if _point_in_box(centre, head) or area_overlap >= 0.25 or sits_on_head:
            if any(_point_in_box(centre, other) for other in other_heads):
                shared += 1
                continue
            observations.append(
                AttributeObservation(
                    label=label,
                    confidence=float(det.get("confidence") or 0.0),
                    box=hbox,
                )
            )
    shared_reasons: tuple[str, ...] = (
        ("helmet_in_other_rider_head_region_unattributed",) if shared else ()
    )
    if not other_riders_complete and (observations or shared):
        return AttributeAssociation(
            state=STATE_AMBIGUOUS,
            reasons=("nearby_rider_context_incomplete_helmet_unattributed",) + shared_reasons,
        )
    if not observations:
        if shared:
            return AttributeAssociation(state=STATE_AMBIGUOUS, reasons=shared_reasons)
        # No helmet label in the head region: UNKNOWN, never "no helmet".
        return AttributeAssociation(state=HELMET_UNKNOWN, reasons=("no_helmet_observation",))
    labels = {o.label for o in observations}
    if YOLO_CLASS_HELMET_NUT_SHELL in labels:
        return AttributeAssociation(
            state=HELMET_NUT_SHELL, observations=tuple(observations), reasons=shared_reasons
        )
    if YOLO_CLASS_HELMET_ACCEPTABLE in labels:
        return AttributeAssociation(
            state=HELMET_ACCEPTABLE, observations=tuple(observations), reasons=shared_reasons
        )
    return AttributeAssociation(
        state=HELMET_UNKNOWN,
        observations=tuple(observations),
        reasons=("legacy_generic_helmet_label",) + shared_reasons,
    )


def match_target_motorcycle(
    motorcycle: Mapping[str, Any],
    motorcycles_in_frame: Sequence[Mapping[str, Any]],
    *,
    target_match_iou: float = MOTORCYCLE_TARGET_MATCH_IOU,
) -> Mapping[str, Any] | None:
    """Return the scan detection that is the target motorcycle itself.

    A padded detail crop often re-detects the motorcycle that produced it. That
    detection is *the same object*, so it must never be counted as a second
    overlapping motorcycle. Matching is purely spatial (IoU against the target
    box, best match wins) — never Python object identity, because the target
    reference box is restored from storage and is a different object than any
    scan detection. At most one detection is consumed as the target.
    """
    bike = box_of(motorcycle)
    best: Mapping[str, Any] | None = None
    best_iou = 0.0
    for det in motorcycles_in_frame:
        iou = box_iou(bike, box_of(det))
        if iou > best_iou:
            best, best_iou = det, iou
    if best is not None and best_iou >= float(target_match_iou):
        return best
    return None


def other_motorcycles(
    motorcycle: Mapping[str, Any],
    motorcycles_in_frame: Sequence[Mapping[str, Any]],
    *,
    target_match_iou: float = MOTORCYCLE_TARGET_MATCH_IOU,
    ambiguous_iou: float = MOTORCYCLE_AMBIGUOUS_IOU,
) -> list[Mapping[str, Any]]:
    """Scan detections that are a *different* motorcycle overlapping the target.

    Everything except the single best spatial match for the target box counts as
    another motorcycle, so genuinely overlapping bikes keep the association
    ``ambiguous`` instead of being silently attributed.
    """
    target = match_target_motorcycle(
        motorcycle, motorcycles_in_frame, target_match_iou=target_match_iou
    )
    bike = box_of(motorcycle)
    others: list[Mapping[str, Any]] = []
    for det in motorcycles_in_frame:
        if target is not None and det is target:
            continue
        if box_iou(bike, box_of(det)) >= float(ambiguous_iou):
            others.append(det)
    return others


def associate_mirrors(
    motorcycle: Mapping[str, Any],
    mirrors: Sequence[Mapping[str, Any]],
    *,
    motorcycles_in_frame: Sequence[Mapping[str, Any]] = (),
    known_other_motorcycles: Sequence[Box] = (),
    other_motorcycles_complete: bool = True,
    mount_band_frac: float = MIRROR_MOUNT_BAND_FRAC,
    max_width_ratio: float = MIRROR_MAX_WIDTH_RATIO,
    ambiguous_iou: float = MOTORCYCLE_AMBIGUOUS_IOU,
    target_match_iou: float = MOTORCYCLE_TARGET_MATCH_IOU,
) -> AttributeAssociation:
    """Associate ``side_mirror`` observations with plausible mounting areas.

    Mounting areas are the upper band of the motorcycle box, split left/right.
    When another motorcycle overlaps this one, attribution is ambiguous and no
    side is assigned; the target's *own* re-detection inside the crop is matched
    spatially (see :func:`match_target_motorcycle`) and never creates that
    ambiguity. ``none_visible`` never means absence: a missed mirror detection
    is reported as ``unknown`` (see ``no_mirror_observation``).

    ``known_other_motorcycles`` are boxes the main detector tracked as
    *different* motorcycles. They skip spatial target matching (identity is
    already known), so even a heavily overlapping neighbour keeps attribution
    ambiguous, and a mirror inside a neighbour's mounting area is not attributed.

    ``other_motorcycles_complete=False`` means that list was truncated or never
    recorded: an omitted motorcycle could overlap the target or share its
    mounting area, so any mirror found there is left unattributed
    (``ambiguous``). With no mirror found the state stays ``none_visible``.
    """
    bike = box_of(motorcycle)
    bike_w = max(1.0, bike[2] - bike[0])
    mount_box = mirror_mount_box(bike, mount_band_frac=mount_band_frac)

    overlapping = other_motorcycles(
        motorcycle,
        motorcycles_in_frame,
        target_match_iou=target_match_iou,
        ambiguous_iou=ambiguous_iou,
    )
    known_others = [tuple(float(v) for v in box) for box in known_other_motorcycles]
    if overlapping or any(box_iou(bike, other) >= float(ambiguous_iou) for other in known_others):
        return AttributeAssociation(
            state=STATE_AMBIGUOUS,
            reasons=("overlapping_motorcycles_mounting_area_unresolved",),
        )
    other_mounts = [mirror_mount_box(other, mount_band_frac=mount_band_frac) for other in known_others]
    shared = 0

    observations: list[AttributeObservation] = []
    reasons: list[str] = []
    for det in mirrors:
        if str(det.get("class_label") or "").strip().lower() != YOLO_CLASS_SIDE_MIRROR:
            continue
        mbox = box_of(det)
        if box_area(mbox) <= 0:
            continue
        if (mbox[2] - mbox[0]) > bike_w * max_width_ratio:
            reasons.append("mirror_box_implausibly_wide_for_mounting_area")
            continue
        centre = ((mbox[0] + mbox[2]) / 2.0, (mbox[1] + mbox[3]) / 2.0)
        if not _point_in_box(centre, mount_box):
            continue
        if any(_point_in_box(centre, other) for other in other_mounts):
            shared += 1
            continue
        side = "left" if centre[0] < (bike[0] + bike[2]) / 2.0 else "right"
        observations.append(
            AttributeObservation(
                label="side_mirror",
                confidence=float(det.get("confidence") or 0.0),
                box=mbox,
                side=side,
            )
        )

    if shared:
        reasons.append("mirror_in_shared_mounting_area_unattributed")
    if not other_motorcycles_complete and (observations or shared):
        reasons.insert(0, "nearby_motorcycle_context_incomplete_mirror_unattributed")
        return AttributeAssociation(state=STATE_AMBIGUOUS, reasons=tuple(reasons))
    if shared and not observations:
        return AttributeAssociation(state=STATE_AMBIGUOUS, reasons=tuple(reasons))
    sides = {o.side for o in observations}
    if len(sides) >= 2:
        state = MIRROR_BOTH
    elif sides == {"left"}:
        state = MIRROR_ONE_LEFT
    elif sides == {"right"}:
        state = MIRROR_ONE_RIGHT
    else:
        # No associated mirror observation — review flag, never proof of absence.
        state = MIRROR_NONE_VISIBLE
        reasons.append("no_mirror_observation_absence_not_proven")
    return AttributeAssociation(state=state, observations=tuple(observations), reasons=tuple(reasons))


def _track_id_of(det: Mapping[str, Any]) -> int | None:
    value = det.get("track_id")
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _boxes_overlap(a: Box, b: Box) -> bool:
    return intersection_area(a, b) > 0


def _point_in_box(point: tuple[float, float], box: Box) -> bool:
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def _box_centre(box: Box) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def mirror_mount_box(bike: Box, *, mount_band_frac: float = MIRROR_MOUNT_BAND_FRAC) -> Box:
    """Upper mounting band of a motorcycle box, where side mirrors sit."""
    bike_w = max(1.0, bike[2] - bike[0])
    bike_h = max(1.0, bike[3] - bike[1])
    return (
        bike[0] - bike_w * 0.10,
        bike[1] - bike_h * 0.10,
        bike[2] + bike_w * 0.10,
        bike[1] + bike_h * mount_band_frac,
    )


def bbox_dict(box: Box) -> dict[str, float]:
    """Stored ``{"x","y","w","h"}`` form of an ``(x1, y1, x2, y2)`` box."""
    return {
        "x": round(float(box[0]), 2),
        "y": round(float(box[1]), 2),
        "w": round(float(box[2] - box[0]), 2),
        "h": round(float(box[3] - box[1]), 2),
    }


def box_from_dict(raw: Any) -> Box | None:
    """Parse a stored ``{"x","y","w","h"}`` box; None when missing or unusable."""
    if not isinstance(raw, Mapping):
        return None
    try:
        x, y, w, h = (float(raw[k]) for k in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (x, y, w, h)) or w <= 0 or h <= 0:
        return None
    return (x, y, x + w, y + h)


# ---------------------------------------------------------------------------
# Main-detector context stored with each selected frame
# ---------------------------------------------------------------------------

# v2 records which list was truncated (``truncated_lists``); v1 rows carry only
# the generic ``truncated`` flag, which is read as "both lists truncated".
MAIN_CONTEXT_VERSION = 2
MAX_CONTEXT_OBJECTS = 8
CONTEXT_SOURCE_MAIN = "main_detector"
CONTEXT_SOURCE_LEGACY = "legacy_row"
RIDER_STATE_UNRECORDED = "unrecorded"
_RIDER_STATES = (STATE_ASSOCIATED, STATE_AMBIGUOUS, STATE_UNASSOCIATED)


@dataclass(frozen=True)
class ContextObject:
    """A nearby main-detector object recorded next to the target motorcycle."""

    box: Box
    track_id: int | None = None
    confidence: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "track_id": self.track_id,
            "confidence": round(float(self.confidence), 4),
            "bbox": bbox_dict(self.box),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "ContextObject | None":
        if not isinstance(raw, Mapping):
            return None
        box = box_from_dict(raw.get("bbox"))
        if box is None:
            return None
        try:
            confidence = float(raw.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        return cls(box=box, track_id=_track_id_of(raw), confidence=confidence)


@dataclass(frozen=True)
class MainDetectorContext:
    """Main YOLOv8m/ByteTrack context for one selected frame.

    The three-class detail model cannot output ``rider`` or ``motorcycle``, so
    rider and motorcycle attribution uses what the main detector saw in the same
    frame: the associated ``rider`` (never ``person``), the rider-association
    state (so an ambiguous rider stays ambiguous), and nearby *other*
    motorcycles/riders intersecting the padded crop. Those are different tracks
    by construction, so a heavily overlapping neighbour is never mistaken for the
    target's own re-detection.

    ``recorded`` is False for rows written before this context existed; the scan
    then flags that nearby motorcycles were not checked instead of assuming none.

    A list is *complete* only when it was recorded and not truncated. An
    incomplete list may omit a neighbour that owns a helmet or mirror, so the
    scan never makes a definite attribution that depends on it.
    """

    source: str
    rider_state: str
    rider_box: Box | None = None
    rider_track_id: int | None = None
    rider_reasons: tuple[str, ...] = ()
    target_track_id: int | None = None
    motorcycles: tuple[ContextObject, ...] = ()
    riders: tuple[ContextObject, ...] = ()
    motorcycles_truncated: bool = False
    riders_truncated: bool = False
    recorded: bool = True

    @property
    def truncated(self) -> bool:
        return bool(self.motorcycles_truncated or self.riders_truncated)

    @property
    def motorcycles_complete(self) -> bool:
        return bool(self.recorded and not self.motorcycles_truncated)

    @property
    def riders_complete(self) -> bool:
        return bool(self.recorded and not self.riders_truncated)

    def truncated_lists(self) -> dict[str, bool]:
        return {
            "motorcycles": bool(self.motorcycles_truncated),
            "riders": bool(self.riders_truncated),
        }

    def as_dict(self) -> dict[str, Any]:
        """Stored form inside ``frames_json`` (``rider_bbox`` stays top-level)."""
        return {
            "version": MAIN_CONTEXT_VERSION,
            "source": self.source,
            "target_track_id": self.target_track_id,
            "rider": {
                "state": self.rider_state,
                "track_id": self.rider_track_id,
                "reasons": list(self.rider_reasons),
            },
            "motorcycles": [o.as_dict() for o in self.motorcycles],
            "riders": [o.as_dict() for o in self.riders],
            "truncated": self.truncated,
            "truncated_lists": self.truncated_lists(),
        }

    def summary(self) -> dict[str, Any]:
        """Reviewer-facing summary stored with scan observations."""
        return {
            "source": self.source,
            "recorded": bool(self.recorded),
            "rider_state": self.rider_state,
            "rider_track_id": self.rider_track_id,
            "rider_box": [round(float(v), 2) for v in self.rider_box] if self.rider_box else None,
            "nearby_motorcycles": [o.as_dict() for o in self.motorcycles],
            "nearby_riders": [o.as_dict() for o in self.riders],
            "truncated": self.truncated,
            "truncated_lists": self.truncated_lists(),
        }

    def rider_association(self) -> RiderAssociation:
        """Rider association taken from the main detector, never from the scan."""
        if self.rider_state == STATE_ASSOCIATED and self.rider_box is not None:
            return RiderAssociation(
                state=STATE_ASSOCIATED,
                rider_track_id=self.rider_track_id,
                rider_box=self.rider_box,
                head_region=rider_head_region(self.rider_box),
                confidence=1.0,
                source=self.source,
            )
        if self.rider_state == STATE_AMBIGUOUS:
            return RiderAssociation(
                state=STATE_AMBIGUOUS,
                reasons=self.rider_reasons or ("multiple_riders_overlap_motorcycle",),
                source=self.source,
            )
        if self.rider_state == RIDER_STATE_UNRECORDED:
            reasons: tuple[str, ...] = ("main_detector_rider_state_not_recorded",)
        elif self.rider_state == STATE_ASSOCIATED:
            reasons = ("main_detector_rider_box_missing",)
        else:
            reasons = self.rider_reasons or ("no_main_detector_rider_for_motorcycle",)
        return RiderAssociation(state=STATE_UNASSOCIATED, reasons=reasons, source=self.source)

    @classmethod
    def from_frame(cls, frame: Mapping[str, Any]) -> "MainDetectorContext":
        """Rebuild the context from a stored frame, including pre-context rows."""
        rider_box = box_from_dict(frame.get("rider_bbox"))
        raw = frame.get("main_context")
        if not isinstance(raw, Mapping):
            # Older rows stored ``rider_bbox`` only when the rider was
            # associated, so a box implies association; its absence is unknown.
            return cls(
                source=CONTEXT_SOURCE_LEGACY,
                rider_state=STATE_ASSOCIATED if rider_box else RIDER_STATE_UNRECORDED,
                rider_box=rider_box,
                recorded=False,
            )
        rider_raw = raw.get("rider") if isinstance(raw.get("rider"), Mapping) else {}
        state = str(rider_raw.get("state") or "").strip().lower()
        if state not in _RIDER_STATES:
            state = RIDER_STATE_UNRECORDED
        reasons = rider_raw.get("reasons")
        motorcycles_raw = raw.get("motorcycles")
        riders_raw = raw.get("riders")
        motorcycles = tuple(
            o for o in (ContextObject.from_dict(r) for r in (motorcycles_raw or [])) if o
        ) if isinstance(motorcycles_raw, list) else ()
        riders = tuple(
            o for o in (ContextObject.from_dict(r) for r in (riders_raw or [])) if o
        ) if isinstance(riders_raw, list) else ()
        motorcycles_truncated, riders_truncated = _stored_truncation(raw)
        return cls(
            source=str(raw.get("source") or CONTEXT_SOURCE_MAIN),
            rider_state=state,
            rider_box=rider_box if state == STATE_ASSOCIATED else None,
            rider_track_id=_track_id_of(rider_raw) if state == STATE_ASSOCIATED else None,
            rider_reasons=tuple(str(r) for r in reasons) if isinstance(reasons, list) else (),
            target_track_id=_track_id_of({"track_id": raw.get("target_track_id")}),
            motorcycles=motorcycles,
            riders=riders,
            motorcycles_truncated=motorcycles_truncated,
            riders_truncated=riders_truncated,
            # A malformed context list cannot certify that neighbours were checked.
            recorded=isinstance(motorcycles_raw, list) and isinstance(riders_raw, list),
        )


def _stored_truncation(raw: Mapping[str, Any]) -> tuple[bool, bool]:
    """Per-list truncation from stored context, failing closed.

    A valid boolean in ``truncated_lists`` wins for that list. Otherwise the
    generic ``truncated`` flag applies to both lists (v1 rows). When neither is
    a real boolean the list is treated as truncated: completeness is unproven.
    """
    per_list = raw.get("truncated_lists")
    per_list = per_list if isinstance(per_list, Mapping) else {}
    generic = raw.get("truncated")
    fallback = generic if isinstance(generic, bool) else True

    def flag(key: str) -> bool:
        value = per_list.get(key)
        return value if isinstance(value, bool) else fallback

    return flag("motorcycles"), flag("riders")


def build_main_context(
    target: Mapping[str, Any],
    rider: RiderAssociation,
    detections: Sequence[Mapping[str, Any]],
    crop: CropRect,
    *,
    max_objects: int = MAX_CONTEXT_OBJECTS,
) -> MainDetectorContext:
    """Capture nearby main-detector motorcycles/riders for one selected frame.

    Anything intersecting the padded crop can contribute a helmet or mirror to
    the crop, so it is recorded (bounded, largest overlap first). Generic
    ``person`` is never recorded as a rider.
    """
    crop_box = crop.as_box()
    target_id = _track_id_of(target)
    motorcycles: list[tuple[float, ContextObject]] = []
    riders: list[tuple[float, ContextObject]] = []
    for det in detections:
        if det is target:
            continue
        label = str(det.get("class_label") or "").strip().lower()
        if label not in (YOLO_CLASS_MOTORCYCLE, YOLO_CLASS_RIDER):
            continue
        box = box_of(det)
        overlap = intersection_area(box, crop_box)
        if box_area(box) <= 0 or overlap <= 0:
            continue
        tid = _track_id_of(det)
        obj = ContextObject(box=box, track_id=tid, confidence=float(det.get("confidence") or 0.0))
        if label == YOLO_CLASS_MOTORCYCLE:
            if target_id is not None and tid == target_id:
                continue
            motorcycles.append((overlap, obj))
        else:
            if (
                rider.state == STATE_ASSOCIATED
                and rider.rider_box == box
                and rider.rider_track_id == tid
            ):
                continue
            riders.append((overlap, obj))
    motorcycles.sort(key=lambda item: item[0], reverse=True)
    riders.sort(key=lambda item: item[0], reverse=True)
    limit = max(0, int(max_objects))
    return MainDetectorContext(
        source=CONTEXT_SOURCE_MAIN,
        rider_state=rider.state,
        rider_box=rider.rider_box if rider.state == STATE_ASSOCIATED else None,
        rider_track_id=rider.rider_track_id if rider.state == STATE_ASSOCIATED else None,
        rider_reasons=tuple(rider.reasons),
        target_track_id=target_id,
        motorcycles=tuple(o for _, o in motorcycles[:limit]),
        riders=tuple(o for _, o in riders[:limit]),
        motorcycles_truncated=len(motorcycles) > limit,
        riders_truncated=len(riders) > limit,
        recorded=True,
    )



# ---------------------------------------------------------------------------
# Track-occurrence grouping
# ---------------------------------------------------------------------------


@dataclass
class _OccurrenceState:
    track_id: int
    occurrence_index: int
    class_label: str
    first_seen: float
    last_seen: float

    @property
    def key(self) -> str:
        return f"t{self.track_id}g{self.occurrence_index}"

    @property
    def is_motorcycle(self) -> bool:
        return self.class_label == YOLO_CLASS_MOTORCYCLE


@dataclass(frozen=True)
class OccurrenceTransition:
    key: str
    track_id: int
    occurrence_index: int
    class_label: str
    reason: str


class TrackOccurrenceRegistry:
    """Group detections into motorcycle track occurrences.

    An occurrence ends on a class change for the same ByteTrack ID, a gap longer
    than the tracker expiry (the ID was dropped by ByteTrack, so a later
    appearance is a possible ID *reuse*), or an explicit close. Occurrence keys
    are ``t<track_id>g<generation>``, so a reused ID never merges with its
    earlier occurrence.

    Memory bound by class group, not by frame content: motorcycle occurrences
    hold the ``max_active`` budget (a genuinely busy frame of motorcycles is
    still bounded and still retires the least-recently-seen one), while
    non-motorcycle tracks are counted against a separate ``max_foreign`` budget.
    A frame of 24 cars therefore cannot evict a motorcycle, and that motorcycle
    keeps its occurrence key instead of being re-keyed on the next frame.
    Non-motorcycle occurrences are still tracked (bounded) because a later class
    change on an ID that was a motorcycle must close that occurrence and open a
    new generation.
    """

    def __init__(
        self,
        *,
        expiry_sec: float = TRACK_EXPIRY_SEC,
        max_active: int = MAX_ACTIVE_OCCURRENCES,
        max_foreign: int = MAX_FOREIGN_OCCURRENCES,
        max_generations: int = MAX_TRACKED_GENERATIONS,
    ) -> None:
        self.expiry_sec = float(expiry_sec)
        self.max_active = int(max_active)
        self.max_foreign = max(int(max_foreign), int(max_active))
        self.max_generations = max(int(max_generations), 4 * int(max_active))
        self._active: dict[int, _OccurrenceState] = {}
        self._generation: dict[int, int] = {}
        self.overflow_retired = 0
        self.foreign_retired = 0

    def observe(
        self, detections: Sequence[Mapping[str, Any]], *, now: float
    ) -> list[OccurrenceTransition]:
        """Update occurrence state for one processed frame."""
        retired = self.retire_expired(now)
        for det in detections:
            tid = _track_id_of(det)
            if tid is None:
                continue
            label = str(det.get("class_label") or "").strip().lower()
            state = self._active.get(tid)
            if state is not None and label and state.class_label and label != state.class_label:
                retired.append(
                    OccurrenceTransition(
                        state.key, tid, state.occurrence_index, state.class_label, "class_changed"
                    )
                )
                del self._active[tid]
                state = None
            if state is None:
                generation = int(self._generation.get(tid, 0)) + 1
                # Re-insert so the dict order stays "least recently started
                # first" and the trim below evicts the oldest generations.
                self._generation.pop(tid, None)
                self._generation[tid] = generation
                self._active[tid] = _OccurrenceState(tid, generation, label, float(now), float(now))
            else:
                state.last_seen = float(now)

        retired.extend(self._enforce_bounds())
        self._trim_generations()
        return retired

    def _enforce_bounds(self) -> list[OccurrenceTransition]:
        """Enforce each class group's bound, foreign tracks first.

        The foreign budget is applied first and only ever evicts non-motorcycle
        occurrences, so an unrelated track can never cost a motorcycle its
        occurrence key. The motorcycle budget is applied afterwards and applies
        only to genuine motorcycle overload.
        """
        retired: list[OccurrenceTransition] = []
        foreign = [s for s in self._active.values() if not s.is_motorcycle]
        while len(foreign) > self.max_foreign:
            oldest = min(foreign, key=lambda s: (s.last_seen, s.track_id))
            foreign.remove(oldest)
            retired.append(self._retire(oldest, "foreign_memory_bound"))
            self.foreign_retired += 1
        motorcycles = [s for s in self._active.values() if s.is_motorcycle]
        while len(motorcycles) > self.max_active:
            oldest = min(motorcycles, key=lambda s: (s.last_seen, s.track_id))
            motorcycles.remove(oldest)
            retired.append(self._retire(oldest, "active_memory_bound"))
            self.overflow_retired += 1
        return retired

    def _retire(self, state: _OccurrenceState, reason: str) -> OccurrenceTransition:
        self._active.pop(state.track_id, None)
        return OccurrenceTransition(
            state.key, state.track_id, state.occurrence_index, state.class_label, reason
        )

    def _trim_generations(self) -> None:
        """Keep the ID-reuse counters bounded, never evicting a live track."""
        if len(self._generation) <= self.max_generations:
            return
        for tid in list(self._generation):
            if len(self._generation) <= self.max_generations:
                break
            if tid in self._active:
                continue
            del self._generation[tid]

    def active_occurrence(self, track_id: int) -> _OccurrenceState | None:
        return self._active.get(int(track_id))

    def retire_expired(self, now: float) -> list[OccurrenceTransition]:
        now = float(now)
        stale = [tid for tid, st in self._active.items() if now - st.last_seen > self.expiry_sec]
        retired: list[OccurrenceTransition] = []
        for tid in stale:
            st = self._active.pop(tid)
            retired.append(
                OccurrenceTransition(st.key, tid, st.occurrence_index, st.class_label, "track_expired")
            )
        return retired

    def close_all(self) -> list[OccurrenceTransition]:
        retired = [
            OccurrenceTransition(
                st.key, st.track_id, st.occurrence_index, st.class_label, "end_of_stream"
            )
            for st in self._active.values()
        ]
        self._active.clear()
        return retired

    def active_keys(self) -> tuple[str, ...]:
        return tuple(st.key for st in self._active.values())




# ---------------------------------------------------------------------------
# Candidate collector (bounded evidence writing during video processing)
# ---------------------------------------------------------------------------


@dataclass
class _FrameSlot:
    frame_index: int
    frame_number: int
    timestamp_sec: float
    score: FrameScore
    crop: CropRect
    detection_box: Box
    rider_box: Box | None
    scale_x: float
    scale_y: float
    scene_path: str | None = None
    crop_path: str | None = None
    size_bytes: int = 0
    main_context: MainDetectorContext | None = None

    def as_dict(self) -> dict[str, Any]:
        rider = self.rider_box
        out = {
            "index": self.frame_index,
            "frame_number": self.frame_number,
            "timestamp_sec": round(float(self.timestamp_sec), 3),
            "scene_path": self.scene_path,
            "crop_path": self.crop_path,
            "crop": self.crop.as_dict(),
            "scan_scale_x": round(float(self.scale_x), 6),
            "scan_scale_y": round(float(self.scale_y), 6),
            "detection_bbox": bbox_dict(self.detection_box),
            "rider_bbox": bbox_dict(rider) if rider else None,
            "score": self.score.as_dict(),
            "size_bytes": int(self.size_bytes),
            "overlay_path": None,
        }
        if self.main_context is not None:
            out["main_context"] = self.main_context.as_dict()
        return out


@dataclass
class _OccurrenceSlots:
    key: str
    track_id: int
    occurrence_index: int
    slots: list[_FrameSlot] = field(default_factory=list)
    scored: int = 0
    writes: int = 0
    closed: bool = False

    def worst(self) -> _FrameSlot | None:
        if len(self.slots) < MAX_FRAMES_PER_OCCURRENCE:
            return None
        return min(self.slots, key=lambda s: s.score.total)

    def best(self) -> _FrameSlot | None:
        if not self.slots:
            return None
        return max(self.slots, key=lambda s: s.score.total)

    def ordered(self) -> list[_FrameSlot]:
        return sorted(self.slots, key=lambda s: s.timestamp_sec)

    def separated_from(
        self, timestamp_sec: float, *, min_gap: float, ignore: _FrameSlot | None = None
    ) -> bool:
        for slot in self.slots:
            if slot is ignore:
                continue
            if abs(float(timestamp_sec) - float(slot.timestamp_sec)) < min_gap:
                return False
        return True


class MotorcycleDetailCollector:
    """Bounded motorcycle detail-candidate collector for one processing run.

    Only already-processed frames are scored, and only frames carrying a
    ``motorcycle`` detection with a ByteTrack ID are considered. Evidence is
    written the moment a frame wins a slot (so memory holds paths, not frames),
    at most ``MAX_FRAMES_PER_OCCURRENCE`` frames per occurrence, at most
    ``max_candidates`` occurrences per run, and at most ``max_writes`` evidence
    writes per occurrence. Nothing here can create or confirm a violation.
    """

    def __init__(
        self,
        *,
        video_id: int,
        run_key: str,
        frame_w: int,
        frame_h: int,
        processing_run_id: int | None = None,
        enabled: bool = True,
        writer: Callable[..., Mapping[str, Any] | None] | None = None,
        max_candidates: int = MAX_CANDIDATES_PER_RUN,
        max_active: int = MAX_ACTIVE_OCCURRENCES,
        max_foreign: int = MAX_FOREIGN_OCCURRENCES,
        max_scored: int = MAX_SCORED_FRAMES_PER_OCCURRENCE,
        max_writes: int = MAX_EVIDENCE_WRITES_PER_OCCURRENCE,
        expiry_sec: float = TRACK_EXPIRY_SEC,
        min_separation_sec: float = MIN_FRAME_SEPARATION_SEC,
        conf_floor: float = 0.25,
        scan_min_side: int = 320,
    ) -> None:
        self.video_id = int(video_id)
        self.run_key = str(run_key)
        # Run identity is part of the dedup key: reprocessing the same video
        # under a new run must not overwrite the previous run's candidate.
        self.processing_run_id = int(processing_run_id) if processing_run_id is not None else None
        self.frame_w = int(frame_w)
        self.frame_h = int(frame_h)
        self.enabled = bool(enabled)
        self.max_candidates = int(max_candidates)
        self.max_scored = int(max_scored)
        self.max_writes = int(max_writes)
        self.min_separation_sec = float(min_separation_sec)
        self.conf_floor = float(conf_floor)
        self.scan_min_side = int(scan_min_side)
        self._writer = writer
        self.registry = TrackOccurrenceRegistry(
            expiry_sec=expiry_sec, max_active=max_active, max_foreign=max_foreign
        )
        self._occurrences: dict[str, _OccurrenceSlots] = {}
        self._candidates: list[dict[str, Any]] = []
        self._budget_exhausted = False
        self.stats: dict[str, int] = {
            "frames_observed": 0,
            "frames_scored": 0,
            "frames_selected": 0,
            "frames_skipped_temporal_overlap": 0,
            "frames_skipped_score": 0,
            "scored_frames_capped": 0,
            "evidence_write_failures": 0,
            "evidence_write_budget_exhausted": 0,
            "occurrences_seen": 0,
            "occurrences_dropped": 0,
        }

    # -- collection ------------------------------------------------------
    def observe(
        self,
        frame: Any,
        detections: Sequence[Mapping[str, Any]],
        *,
        frame_number: int,
        timestamp_sec: float,
    ) -> None:
        """Update occurrence state and consider this frame for slotting."""
        if not self.enabled or frame is None:
            return
        detections = list(detections or [])
        during = self.registry.observe(detections, now=float(timestamp_sec))
        if during:
            self._close(during)
        if self._budget_exhausted:
            return
        motorcycles = [d for d in detections if is_motorcycle_detection(d)]
        if not motorcycles:
            return
        riders = [d for d in detections if is_rider_detection(d)]
        self.stats["frames_observed"] += 1

        for det in motorcycles:
            state = self.registry.active_occurrence(int(det["track_id"]))
            if state is None:
                continue
            occ = self._occurrences.get(state.key)
            if occ is None:
                occ = _OccurrenceSlots(
                    key=state.key,
                    track_id=state.track_id,
                    occurrence_index=state.occurrence_index,
                )
                self._occurrences[state.key] = occ
                self.stats["occurrences_seen"] += 1
            if occ.closed:
                continue
            if occ.scored >= self.max_scored and len(occ.slots) >= MAX_FRAMES_PER_OCCURRENCE:
                # Bounded scoring work: the slots are already full.
                self.stats["scored_frames_capped"] += 1
                continue
            occ.scored += 1
            self.stats["frames_scored"] += 1

            rider = associate_rider(det, riders)
            crop = padded_crop_rect(
                box_of(det), self.frame_w, self.frame_h, rider_box=rider.rider_box
            )
            if crop is None:
                continue
            score = score_frame(
                frame,
                det,
                others=detections,
                rider_box=rider.rider_box,
                frame_w=self.frame_w,
                frame_h=self.frame_h,
                crop=crop,
                conf_floor=self.conf_floor,
            )
            self._consider(
                occ,
                frame,
                det,
                crop,
                rider,
                score,
                int(frame_number),
                float(timestamp_sec),
                detections=detections,
            )

    def _consider(
        self,
        occ: _OccurrenceSlots,
        frame: Any,
        det: Mapping[str, Any],
        crop: CropRect,
        rider: RiderAssociation,
        score: FrameScore,
        frame_number: int,
        timestamp_sec: float,
        *,
        detections: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        target: _FrameSlot | None = None
        replace = False
        if len(occ.slots) < MAX_FRAMES_PER_OCCURRENCE:
            if occ.separated_from(timestamp_sec, min_gap=self.min_separation_sec):
                target = None
            else:
                nearest = min(occ.slots, key=lambda s: abs(s.timestamp_sec - timestamp_sec))
                if score.total <= nearest.score.total + SCORE_REPLACEMENT_MARGIN:
                    self.stats["frames_skipped_temporal_overlap"] += 1
                    return
                target, replace = nearest, True
        else:
            worst = occ.worst()
            if worst is None or score.total <= worst.score.total + SCORE_REPLACEMENT_MARGIN:
                self.stats["frames_skipped_score"] += 1
                return
            if not occ.separated_from(
                timestamp_sec, min_gap=self.min_separation_sec, ignore=worst
            ):
                self.stats["frames_skipped_temporal_overlap"] += 1
                return
            target, replace = worst, True

        scale_x, scale_y = scan_scale_for(crop, min_side=self.scan_min_side)
        slot = _FrameSlot(
            frame_index=target.frame_index if (replace and target is not None) else self._free_index(occ),
            frame_number=frame_number,
            timestamp_sec=timestamp_sec,
            score=score,
            crop=crop,
            detection_box=box_of(det),
            rider_box=rider.rider_box,
            scale_x=scale_x,
            scale_y=scale_y,
            # Only built for frames that win a slot, so context work stays bounded.
            main_context=build_main_context(det, rider, detections, crop),
        )
        if not self._write_evidence(occ, slot, frame):
            self.stats["evidence_write_failures"] += 1
            return
        if replace and target is not None:
            occ.slots[occ.slots.index(target)] = slot
        else:
            occ.slots.append(slot)
        self.stats["frames_selected"] += 1

    def _free_index(self, occ: _OccurrenceSlots) -> int:
        used = {s.frame_index for s in occ.slots}
        for index in range(1, MAX_FRAMES_PER_OCCURRENCE + 1):
            if index not in used:
                return index
        return MAX_FRAMES_PER_OCCURRENCE

    def _write_evidence(self, occ: _OccurrenceSlots, slot: _FrameSlot, frame: Any) -> bool:
        if occ.writes >= self.max_writes:
            self.stats["evidence_write_budget_exhausted"] += 1
            return False
        occ.writes += 1
        writer = self._writer or _default_detail_writer
        try:
            result = writer(
                frame,
                slot.crop,
                run_key=self.run_key,
                occurrence_key=occ.key,
                frame_index=slot.frame_index,
            )
        except Exception:
            logger.exception("detail evidence write failed for %s", occ.key)
            return False
        if not result or not result.get("scene_path") or not result.get("crop_path"):
            return False
        slot.scene_path = str(result.get("scene_path"))
        slot.crop_path = str(result.get("crop_path"))
        slot.size_bytes = int(result.get("size_bytes") or 0)
        return True

    def _close(self, transitions: Sequence[OccurrenceTransition]) -> None:
        for tr in transitions:
            if tr.class_label != YOLO_CLASS_MOTORCYCLE:
                continue
            occ = self._occurrences.pop(tr.key, None)
            if occ is None:
                continue
            occ.closed = True
            self._emit(occ)

    def _emit(self, occ: _OccurrenceSlots) -> None:
        if not occ.slots:
            return
        if self._budget_exhausted:
            self.stats["occurrences_dropped"] += 1
            return
        payload = build_detail_candidate_payload(
            video_id=self.video_id,
            run_key=self.run_key,
            occ=occ,
            source_width=self.frame_w,
            source_height=self.frame_h,
            processing_run_id=self.processing_run_id,
        )
        self._candidates.append(payload)
        if len(self._candidates) >= self.max_candidates:
            self._budget_exhausted = True

    def wrap_up(self) -> None:
        """Close open occurrences and finalize the bounded candidate list."""
        self._close(self.registry.close_all())
        if len(self._candidates) > self.max_candidates:
            # Keep the strongest occurrences when the budget is exceeded.
            dropped = len(self._candidates) - self.max_candidates
            self._candidates.sort(key=lambda p: float(p.get("frame_score") or 0.0), reverse=True)
            self._candidates = self._candidates[: self.max_candidates]
            self.stats["occurrences_dropped"] += dropped
        else:
            self._candidates.sort(key=lambda p: float(p.get("frame_score") or 0.0), reverse=True)

    # -- output ----------------------------------------------------------
    def candidates(self) -> list[dict[str, Any]]:
        """Bounded candidate payloads (one per motorcycle track occurrence)."""
        return list(self._candidates)

    def persist(self, adapter: Any | None = None) -> int:
        """Upsert candidates; re-running the same run updates the same rows."""
        if adapter is None:
            from database import db as adapter_module

            adapter = adapter_module
        written = 0
        for payload in self._candidates:
            try:
                adapter.upsert_motorcycle_detail_candidate(**payload)
                written += 1
            except Exception:
                logger.exception("failed to persist motorcycle detail candidate %s", payload.get("occurrence_key"))
        return written

    def discard(self) -> None:
        """Remove this run's detail evidence directory (failure path)."""
        try:
            from core.evidence import remove_detail_run

            remove_detail_run(self.run_key)
        except Exception:
            logger.exception("failed to remove detail evidence for run %s", self.run_key)

    def summary(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "candidates": len(self._candidates),
            "budget_exhausted": self._budget_exhausted,
            **self.stats,
        }


def build_detail_candidate_payload(
    *,
    video_id: int,
    run_key: str,
    occ: _OccurrenceSlots,
    source_width: int = 0,
    source_height: int = 0,
    processing_run_id: int | None = None,
) -> dict[str, Any]:
    """Build the persistence payload for one occurrence's two best frames."""
    best = occ.best()
    assert best is not None  # callers guard on empty slots
    frames = [slot.as_dict() for slot in occ.ordered()]
    return {
        "video_id": int(video_id),
        "run_key": str(run_key),
        "processing_run_id": (
            int(processing_run_id) if processing_run_id is not None else None
        ),
        "track_id": int(occ.track_id),
        "occurrence_index": int(occ.occurrence_index),
        "occurrence_key": occ.key,
        "selector_version": DETAIL_SELECTOR_VERSION,
        "frame_number": int(best.frame_number),
        "timestamp_sec": float(best.timestamp_sec),
        "frame_score": float(best.score.total),
        "score_breakdown_json": json.dumps(best.score.breakdown),
        "frame_count": len(frames),
        "frames_json": json.dumps(frames),
        "size_bytes": int(sum(slot.size_bytes for slot in occ.slots)),
        # Source-frame dimensions are required to map crop detections back.
        "source_width": int(source_width),
        "source_height": int(source_height),
    }


def _default_detail_writer(
    frame: Any,
    crop: CropRect,
    *,
    run_key: str,
    occurrence_key: str,
    frame_index: int,
) -> Mapping[str, Any] | None:
    from core.evidence import save_detail_evidence

    return save_detail_evidence(
        frame,
        crop,
        run_key=run_key,
        occurrence_key=occurrence_key,
        frame_index=frame_index,
    )
