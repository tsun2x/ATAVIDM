"""Footprint / overlap-based zone membership (replaces bottom-center-only)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

from core.rule_types import MembershipState, ZoneMembershipResult
from core.tracker import point_in_polygon


# Default: lower 30% of the bounding box as the road-contact footprint.
DEFAULT_FOOTPRINT_HEIGHT_FRACTION = 0.30
DEFAULT_ENTER_OVERLAP = 0.35
DEFAULT_EXIT_OVERLAP = 0.20


@dataclass
class MembershipPolicy:
    """Rule-specific enter/exit thresholds and footprint fraction."""

    enter_overlap: float = DEFAULT_ENTER_OVERLAP
    exit_overlap: float = DEFAULT_EXIT_OVERLAP
    footprint_height_fraction: float = DEFAULT_FOOTPRINT_HEIGHT_FRACTION
    require_stable: bool = False
    # When True, INSIDE also requires the bottom-center anchor inside the zone.
    require_anchor_inside: bool = False


# Named policies used by rules.
POLICY_STATIONARY = MembershipPolicy(
    enter_overlap=0.40,
    exit_overlap=0.25,
    require_stable=True,
    require_anchor_inside=False,
)
POLICY_LANE = MembershipPolicy(
    enter_overlap=0.30,
    exit_overlap=0.15,
    require_stable=False,
)
POLICY_STRICT = MembershipPolicy(
    enter_overlap=0.50,
    exit_overlap=0.30,
    require_stable=True,
    require_anchor_inside=True,
)


@dataclass
class MembershipHysteresis:
    """Per-(rule, track) last committed membership for jitter suppression."""

    last_inside: bool = False
    history: dict[tuple[str, int], bool] = field(default_factory=dict)

    def update(
        self,
        key: tuple[str, int],
        raw_inside: bool,
        overlap: float,
        policy: MembershipPolicy,
    ) -> bool:
        prev = self.history.get(key, False)
        if prev:
            inside = overlap >= policy.exit_overlap
        else:
            inside = overlap >= policy.enter_overlap and raw_inside
        # Boundary band: keep previous state when neither threshold is decisive.
        if policy.exit_overlap <= overlap < policy.enter_overlap:
            inside = prev
        self.history[key] = inside
        return inside

    def prune(self, active_keys: set[tuple[str, int]]) -> None:
        stale = [k for k in self.history if k not in active_keys]
        for k in stale:
            del self.history[k]


def road_contact_footprint(
    det: dict[str, Any],
    height_fraction: float = DEFAULT_FOOTPRINT_HEIGHT_FRACTION,
) -> list[list[float]]:
    """Lower bounding-box rectangle used as the road-contact footprint."""
    x = float(det["bbox_x"])
    y = float(det["bbox_y"])
    w = float(det["bbox_w"])
    h = float(det["bbox_h"])
    frac = max(0.05, min(1.0, float(height_fraction)))
    top = y + h * (1.0 - frac)
    return [
        [x, top],
        [x + w, top],
        [x + w, y + h],
        [x, y + h],
    ]


def bottom_center(det: dict[str, Any]) -> tuple[float, float]:
    x = float(det["bbox_x"]) + float(det["bbox_w"]) / 2
    y = float(det["bbox_y"]) + float(det["bbox_h"])
    return x, y


def _poly_mask(poly: list[list[float]], width: int, height: int) -> np.ndarray:
    mask = np.zeros((height, width), dtype=np.uint8)
    if len(poly) < 3 or width < 1 or height < 1:
        return mask
    pts = np.array([[int(round(p[0])), int(round(p[1]))] for p in poly], dtype=np.int32)
    cv2.fillPoly(mask, [pts], 1)
    return mask


def footprint_zone_overlap(
    footprint: list[list[float]],
    zone: list[list[float]],
    *,
    raster_max: int = 256,
) -> float:
    """Return intersection(footprint, zone) / area(footprint) in [0, 1]."""
    if len(footprint) < 3 or len(zone) < 3:
        return 0.0

    xs = [p[0] for p in footprint] + [p[0] for p in zone]
    ys = [p[1] for p in footprint] + [p[1] for p in zone]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = max(max_x - min_x, 1.0)
    span_y = max(max_y - min_y, 1.0)
    scale = min(raster_max / span_x, raster_max / span_y, 1.0)
    width = max(int(np.ceil(span_x * scale)) + 2, 2)
    height = max(int(np.ceil(span_y * scale)) + 2, 2)

    def _shift(poly: list[list[float]]) -> list[list[float]]:
        return [[(p[0] - min_x) * scale + 1, (p[1] - min_y) * scale + 1] for p in poly]

    fp_mask = _poly_mask(_shift(footprint), width, height)
    zone_mask = _poly_mask(_shift(zone), width, height)
    fp_area = int(fp_mask.sum())
    if fp_area <= 0:
        return 0.0
    inter = int(np.logical_and(fp_mask, zone_mask).sum())
    return inter / fp_area


def evaluate_zone_membership(
    det: dict[str, Any],
    zone: list[list[float]],
    *,
    policy: MembershipPolicy | None = None,
    hysteresis: MembershipHysteresis | None = None,
    hysteresis_key: tuple[str, int] | None = None,
    footprint_override: list[list[float]] | None = None,
) -> ZoneMembershipResult:
    """Compute footprint/overlap membership with optional hysteresis."""
    policy = policy or MembershipPolicy()
    footprint = footprint_override or road_contact_footprint(
        det, policy.footprint_height_fraction
    )
    overlap = footprint_zone_overlap(footprint, zone)
    ax, ay = bottom_center(det)
    anchor_inside = point_in_polygon(ax, ay, zone) if len(zone) >= 3 else False

    # Raw classification before hysteresis:
    # - meaningful overlap → candidate inside
    # - negligible overlap even if anchor inside → outside
    # - partial band without decisive overlap → boundary unknown
    if overlap >= policy.enter_overlap:
        raw_inside = True
        if policy.require_anchor_inside and not anchor_inside:
            raw_inside = False
        state = MembershipState.INSIDE
    elif overlap <= 0.02 and not anchor_inside:
        raw_inside = False
        state = MembershipState.OUTSIDE
    elif overlap < policy.exit_overlap and not anchor_inside:
        raw_inside = False
        state = MembershipState.OUTSIDE
    elif overlap < 0.05 and anchor_inside:
        # Anchor alone with negligible footprint overlap is not membership.
        raw_inside = False
        state = MembershipState.OUTSIDE
    elif policy.exit_overlap <= overlap < policy.enter_overlap:
        raw_inside = False
        state = MembershipState.BOUNDARY_UNKNOWN
    else:
        raw_inside = overlap >= policy.exit_overlap
        state = MembershipState.INSIDE if raw_inside else MembershipState.OUTSIDE

    stable = False
    note = ""
    if hysteresis is not None and hysteresis_key is not None:
        committed = hysteresis.update(hysteresis_key, raw_inside, overlap, policy)
        stable = committed
        if state is MembershipState.BOUNDARY_UNKNOWN:
            note = "boundary hysteresis retained prior state"
            state = MembershipState.INSIDE if committed else MembershipState.OUTSIDE
        elif committed != raw_inside:
            note = "hysteresis suppressed boundary jitter"
            state = MembershipState.INSIDE if committed else MembershipState.OUTSIDE
        else:
            stable = committed and state is MembershipState.INSIDE
    else:
        stable = state is MembershipState.INSIDE and overlap >= policy.enter_overlap

    if policy.require_stable and state is MembershipState.INSIDE and not stable:
        # Without hysteresis history, first-frame INSIDE is provisional.
        if hysteresis is None:
            note = note or "stable membership requires persistence across frames"

    return ZoneMembershipResult(
        state=state,
        overlap_ratio=float(overlap),
        footprint=footprint,
        anchor_inside=anchor_inside,
        stable_inside=stable and state is MembershipState.INSIDE,
        hysteresis_note=note,
    )


def is_inside_for_rule(
    result: ZoneMembershipResult,
    *,
    require_stable: bool = False,
) -> bool:
    if result.state is not MembershipState.INSIDE:
        return False
    if require_stable and not result.stable_inside:
        return False
    return True
