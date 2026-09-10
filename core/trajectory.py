"""Shared trajectory and line-crossing primitives for rule evaluation.

Rules must not duplicate this math. All finite-line crossing, signed-side,
prohibited-direction, and zone-transition helpers live here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

Point = tuple[float, float]
Segment = tuple[Point, Point]


@dataclass(frozen=True)
class DisplacementResult:
    dx: float
    dy: float
    distance: float
    degrees: float | None
    sufficient: bool


def displacement(
    points: Sequence[Point],
    *,
    min_distance: float,
) -> DisplacementResult:
    """Stable A→B displacement from the first/last observation."""
    if len(points) < 2:
        return DisplacementResult(0.0, 0.0, 0.0, None, False)
    x0, y0 = points[0]
    x1, y1 = points[-1]
    dx, dy = x1 - x0, y1 - y0
    distance = math.hypot(dx, dy)
    if not math.isfinite(distance) or distance < float(min_distance):
        return DisplacementResult(dx, dy, distance, None, False)
    degrees = math.degrees(math.atan2(dy, dx)) % 360.0
    return DisplacementResult(dx, dy, distance, degrees, True)


def heading_degrees(
    points: Sequence[Point],
    *,
    min_distance: float,
) -> float | None:
    result = displacement(points, min_distance=min_distance)
    return result.degrees if result.sufficient else None


def signed_side(point: Point, line_a: Point, line_b: Point) -> float:
    """Cross-product side of ``point`` relative to oriented segment A→B.

    Positive = left of A→B, negative = right, zero = on the line.
    """
    ax, ay = line_a
    bx, by = line_b
    px, py = point
    return (bx - ax) * (py - ay) - (by - ay) * (px - ax)


def segments_intersect(a: Segment, b: Segment, *, endpoint_eps: float = 1e-6) -> bool:
    """Finite segment intersection (includes proper crossings; rejects parallel miss)."""
    (x1, y1), (x2, y2) = a
    (x3, y3), (x4, y4) = b
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(den) <= endpoint_eps:
        return False
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / den
    u = ((x1 - x3) * (y1 - y2) - (y1 - y3) * (x1 - x2)) / den
    return (endpoint_eps < t < 1.0 - endpoint_eps) and (
        endpoint_eps < u < 1.0 - endpoint_eps
    )


def crossed_oriented_line(
    prev: Point,
    curr: Point,
    line_a: Point,
    line_b: Point,
    *,
    prohibited_from: str,
) -> bool:
    """True when motion crosses A→B from a prohibited side.

    ``prohibited_from`` is ``left``, ``right``, or ``both`` relative to A→B.
    """
    if prohibited_from not in ("left", "right", "both"):
        return False
    if not segments_intersect((prev, curr), (line_a, line_b)):
        return False
    side_prev = signed_side(prev, line_a, line_b)
    side_curr = signed_side(curr, line_a, line_b)
    if side_prev == 0 or side_curr == 0:
        return False
    if side_prev * side_curr > 0:
        return False
    if prohibited_from == "both":
        return True
    # Approach side is the prior signed side.
    if prohibited_from == "left":
        return side_prev > 0
    return side_prev < 0


def motion_projects_into_direction(
    motion: Point,
    direction: Point,
    *,
    min_projection: float,
) -> bool:
    """Require motion · direction above a minimum (rejects reverse/parallel)."""
    mx, my = motion
    dx, dy = direction
    mag = math.hypot(dx, dy)
    if mag <= 0 or not math.isfinite(mag):
        return False
    ux, uy = dx / mag, dy / mag
    projection = mx * ux + my * uy
    return math.isfinite(projection) and projection >= float(min_projection)


def is_parallel_motion(
    motion: Point,
    line_a: Point,
    line_b: Point,
    *,
    max_sin: float = 0.25,
) -> bool:
    mx, my = motion
    lx, ly = line_b[0] - line_a[0], line_b[1] - line_a[1]
    m_mag = math.hypot(mx, my)
    l_mag = math.hypot(lx, ly)
    if m_mag <= 0 or l_mag <= 0:
        return True
    cross = abs(mx * ly - my * lx) / (m_mag * l_mag)
    return cross <= max_sin


def endpoint_jitter(
    points: Sequence[Point],
    *,
    max_jitter: float,
) -> bool:
    """True when consecutive samples only jitter near an endpoint."""
    if len(points) < 2:
        return True
    span = math.hypot(points[-1][0] - points[0][0], points[-1][1] - points[0][1])
    return span < float(max_jitter)


def point_in_polygon(point: Point, polygon: Sequence[Sequence[float]]) -> bool:
    if len(polygon) < 3:
        return False
    x, y = point
    inside = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = float(polygon[i][0]), float(polygon[i][1])
        xj, yj = float(polygon[j][0]), float(polygon[j][1])
        intersects = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


@dataclass
class ZoneMembershipHysteresis:
    """Outside→inside transition with enter/exit persistence counts."""

    enter_frames: int = 2
    exit_frames: int = 2
    _inside: bool = False
    _enter_streak: int = 0
    _exit_streak: int = 0

    def update(self, currently_inside: bool) -> str:
        """Return ``entered``, ``exited``, ``inside``, or ``outside``."""
        if currently_inside:
            self._enter_streak += 1
            self._exit_streak = 0
            if not self._inside and self._enter_streak >= self.enter_frames:
                self._inside = True
                return "entered"
            return "inside" if self._inside else "outside"
        self._exit_streak += 1
        self._enter_streak = 0
        if self._inside and self._exit_streak >= self.exit_frames:
            self._inside = False
            return "exited"
        return "inside" if self._inside else "outside"


def outside_to_inside_transition(
    prev_inside: bool,
    curr_inside: bool,
) -> bool:
    return (not prev_inside) and curr_inside


def angle_difference_degrees(a: float, b: float) -> float:
    """Smallest absolute difference between two headings in degrees."""
    return abs((a - b + 180.0) % 360.0 - 180.0)
