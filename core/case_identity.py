"""Stable case identity and observation grouping for legal-policy review.

Groups overlapping Illegal Parking + Obstruction observations into one logical
case while retaining each canonical behavior as a contributing observation.
Does not invent legal classifications or recurrence decisions.

Event identity is never source/run/track alone — episode timing must agree.
Missing or ambiguous episode evidence keeps observations separate for review.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.detection_config import (
    VIOLATION_ILLEGAL_PARKING,
    VIOLATION_OBSTRUCTION,
    canonicalize_violation,
)
from core.violation_policy import (
    behavior_details_for,
    fused_case_category,
    is_parking_obstruction_fusion,
    legal_status_for,
    proposed_official_category_for,
    verified_official_category_for,
)

FUSION_PAIR = frozenset({VIOLATION_ILLEGAL_PARKING, VIOLATION_OBSTRUCTION})


@dataclass(frozen=True)
class ObservationRef:
    """One review-queue observation contributing to a logical case."""

    review_id: int
    video_id: int
    track_id: int
    processing_run_id: int | None
    violation_type: str
    timestamp_sec: float | None
    episode_start_sec: float | None
    episode_end_sec: float | None
    evidence_path: str | None = None
    vehicle_evidence_path: str | None = None
    plate_evidence_path: str | None = None
    evidence_clip_path: str | None = None
    reason_log: str | None = None
    episode_identity_uncertain: bool = False
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)


@dataclass(frozen=True)
class CaseGroup:
    """One logical case assembled from one or more observations."""

    observations: tuple[ObservationRef, ...]
    contributing_rules: tuple[str, ...]
    primary_canonical_rule: str
    proposed_official_category: str | None
    verified_official_category: str | None
    is_fused_parking_obstruction: bool
    event_key: tuple[Any, ...]
    episode_identity_uncertain: bool = False

    @property
    def review_ids(self) -> tuple[int, ...]:
        return tuple(o.review_id for o in self.observations)


def observation_from_review_row(row: dict[str, Any]) -> ObservationRef:
    """Build an ObservationRef from a review_queue row dict.

    Does not invent wall-clock timestamps from assumed FPS. Frame-only rows
    without ``timestamp_sec`` / episode bounds are marked uncertain so they
    cannot be auto-merged by proximity guesswork.
    """
    vtype = canonicalize_violation(row.get("violation_type") or "")
    ts = _float_or_none(row.get("timestamp_sec"))
    ep_start = _float_or_none(row.get("episode_start_sec"))
    ep_end = _float_or_none(row.get("episode_end_sec"))
    uncertain = ts is None and ep_start is None and ep_end is None
    return ObservationRef(
        review_id=int(row["id"]),
        video_id=int(row["video_id"]),
        track_id=int(row["track_id"]),
        processing_run_id=(
            int(row["processing_run_id"])
            if row.get("processing_run_id") is not None
            else None
        ),
        violation_type=vtype or str(row.get("violation_type") or ""),
        timestamp_sec=ts,
        episode_start_sec=ep_start,
        episode_end_sec=ep_end,
        evidence_path=row.get("evidence_path"),
        vehicle_evidence_path=row.get("vehicle_evidence_path"),
        plate_evidence_path=row.get("plate_evidence_path"),
        evidence_clip_path=row.get("evidence_clip_path"),
        reason_log=row.get("reason_log"),
        episode_identity_uncertain=uncertain,
        raw=dict(row),
    )


def observation_from_violation_row(
    row: dict[str, Any],
    *,
    review_id: int | None = None,
) -> ObservationRef:
    """Build an ObservationRef-like view from a violations row for episode checks."""
    vtype = canonicalize_violation(row.get("violation_type") or "")
    ts = _float_or_none(row.get("timestamp_sec"))
    ep_start = _float_or_none(row.get("episode_start_sec"))
    ep_end = _float_or_none(row.get("episode_end_sec"))
    uncertain = ts is None and ep_start is None and ep_end is None
    return ObservationRef(
        review_id=int(review_id) if review_id is not None else -int(row["id"]),
        video_id=int(row["video_id"]),
        track_id=int(row["track_id"]),
        processing_run_id=(
            int(row["processing_run_id"])
            if row.get("processing_run_id") is not None
            else None
        ),
        violation_type=vtype or str(row.get("violation_type") or ""),
        timestamp_sec=ts,
        episode_start_sec=ep_start,
        episode_end_sec=ep_end,
        evidence_path=row.get("evidence_path"),
        vehicle_evidence_path=row.get("vehicle_evidence_path"),
        plate_evidence_path=row.get("plate_evidence_path"),
        evidence_clip_path=row.get("evidence_clip_path"),
        reason_log=row.get("reason_log"),
        episode_identity_uncertain=uncertain,
        raw=dict(row),
    )


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def event_identity_key(obs: ObservationRef) -> tuple[Any, ...]:
    """Source + run + track key — necessary but not sufficient for a case."""
    return (obs.video_id, obs.processing_run_id, obs.track_id)


def episodes_overlap(a: ObservationRef, b: ObservationRef) -> bool:
    """True when two observations share an established episode lifecycle.

    Requires positive episode evidence. Missing/ambiguous timing does **not**
    auto-merge via proximity or assumed FPS conversion.
    """
    if a.episode_identity_uncertain or b.episode_identity_uncertain:
        return False

    a_start, a_end = _episode_bounds(a)
    b_start, b_end = _episode_bounds(b)

    if a_start is not None and a_end is not None and b_start is not None and b_end is not None:
        return a_start <= b_end and b_start <= a_end

    # Timestamp falls inside the other established episode window.
    if a.timestamp_sec is not None and b_start is not None and b_end is not None:
        if b_start <= a.timestamp_sec <= b_end:
            return True
    if b.timestamp_sec is not None and a_start is not None and a_end is not None:
        if a_start <= b.timestamp_sec <= a_end:
            return True

    # Insufficient evidence — keep separate for human review.
    return False


def episode_identity_is_uncertain(a: ObservationRef, b: ObservationRef) -> bool:
    """True when same event key but episode membership cannot be established."""
    if event_identity_key(a) != event_identity_key(b):
        return False
    if episodes_overlap(a, b):
        return False
    a_start, a_end = _episode_bounds(a)
    b_start, b_end = _episode_bounds(b)
    has_a = a_start is not None or a_end is not None or a.timestamp_sec is not None
    has_b = b_start is not None or b_end is not None or b.timestamp_sec is not None
    if not has_a or not has_b:
        return True
    # Explicit non-overlap is certain separation, not uncertainty.
    if (
        a_start is not None
        and a_end is not None
        and b_start is not None
        and b_end is not None
        and (a_end < b_start or b_end < a_start)
    ):
        return False
    return not episodes_overlap(a, b)


def _episode_bounds(obs: ObservationRef) -> tuple[float | None, float | None]:
    start = obs.episode_start_sec
    end = obs.episode_end_sec
    if start is not None and end is None and obs.timestamp_sec is not None:
        end = obs.timestamp_sec
    if end is not None and start is None and obs.timestamp_sec is not None:
        start = obs.timestamp_sec
    return start, end


def can_fuse_parking_obstruction(a: ObservationRef, b: ObservationRef) -> bool:
    """True when parking + obstruction share the same overlapping event."""
    if event_identity_key(a) != event_identity_key(b):
        return False
    types = {a.violation_type, b.violation_type}
    if types != FUSION_PAIR:
        return False
    return episodes_overlap(a, b)


def build_case_group(
    primary: ObservationRef,
    siblings: list[ObservationRef] | tuple[ObservationRef, ...] = (),
) -> CaseGroup:
    """Assemble a CaseGroup around ``primary``, fusing eligible siblings.

    Unrelated events (different run/track/video, non-overlapping episodes, or
    non-fusion types) remain separate groups. Ambiguous episode data never
    forces a merge.
    """
    members = [primary]
    uncertain = bool(primary.episode_identity_uncertain)
    for sib in siblings:
        if sib.review_id == primary.review_id:
            continue
        if can_fuse_parking_obstruction(primary, sib):
            members.append(sib)
        elif episode_identity_is_uncertain(primary, sib):
            uncertain = True

    # Deduplicate by review_id while preserving order.
    seen: set[int] = set()
    unique: list[ObservationRef] = []
    for m in members:
        if m.review_id in seen:
            continue
        seen.add(m.review_id)
        unique.append(m)

    rules = tuple(sorted({m.violation_type for m in unique}))
    fused = is_parking_obstruction_fusion(rules)
    proposed, contributing = fused_case_category(rules) if fused else (None, rules)

    if fused:
        primary_rule = VIOLATION_OBSTRUCTION
        proposed = proposed or proposed_official_category_for(VIOLATION_OBSTRUCTION)
        verified = verified_official_category_for(VIOLATION_OBSTRUCTION)
    else:
        primary_rule = primary.violation_type
        proposed = proposed_official_category_for(primary_rule)
        verified = verified_official_category_for(primary_rule)

    return CaseGroup(
        observations=tuple(unique),
        contributing_rules=contributing if fused else rules,
        primary_canonical_rule=primary_rule,
        proposed_official_category=proposed,
        verified_official_category=verified,
        is_fused_parking_obstruction=fused,
        event_key=event_identity_key(primary),
        episode_identity_uncertain=uncertain,
    )


def find_existing_fused_case_violation_id(
    adapter: Any,
    group: CaseGroup,
) -> int | None:
    """Return an existing non-dismissed violation for this episode group.

    Prevents a second confirmation from creating a second recurrence-countable
    identity for the same overlapping parking/obstruction event. Already-linked
    observations participate. Dismissed cases are never reused or reopened.
    Non-fusion violation types never attach via the parking/obstruction finder.
    """
    # Prefer explicit links from any observation already attached to a case.
    for obs in group.observations:
        linked = adapter.find_violation_linked_to_review(obs.review_id)
        if linked is None:
            continue
        viol = adapter.get_violation(int(linked))
        if viol is None:
            continue
        if viol.get("status") == "dismissed":
            continue
        return int(linked)

    primary = group.observations[0]
    if (
        not group.is_fused_parking_obstruction
        and primary.violation_type not in FUSION_PAIR
    ):
        return None

    video_id, run_id, track_id = group.event_key
    return adapter.find_fused_case_violation(
        video_id=video_id,
        processing_run_id=run_id,
        track_id=track_id,
        contributing_rules=group.contributing_rules
        if group.is_fused_parking_obstruction
        else tuple(FUSION_PAIR),
        episode_start_sec=primary.episode_start_sec,
        episode_end_sec=primary.episode_end_sec,
        timestamp_sec=primary.timestamp_sec,
        require_fusion_pair=group.is_fused_parking_obstruction,
    )


def policy_snapshots_for_group(group: CaseGroup) -> list[dict[str, Any]]:
    """Build per-behavior policy snapshot payloads for persistence."""
    snapshots: list[dict[str, Any]] = []
    for rule in group.contributing_rules:
        status = legal_status_for(rule)
        # For fused cases the proposed official wording is shared; verified
        # classification remains None until legal activation.
        if group.is_fused_parking_obstruction:
            official = group.verified_official_category or group.proposed_official_category
        else:
            official = (
                verified_official_category_for(rule)
                or proposed_official_category_for(rule)
            )
        snapshots.append(
            {
                "canonical_rule": rule,
                "official_category": official,
                "legal_status": status.value if status else None,
                "behavior_details": list(behavior_details_for(rule)),
            }
        )
    return snapshots


def merge_evidence_from_group(group: CaseGroup) -> dict[str, Any]:
    """Prefer non-empty evidence paths across contributing observations."""
    fields = (
        "evidence_path",
        "vehicle_evidence_path",
        "plate_evidence_path",
        "evidence_clip_path",
        "reason_log",
    )
    merged: dict[str, Any] = {}
    for field_name in fields:
        for obs in group.observations:
            value = getattr(obs, field_name, None)
            if value:
                merged[field_name] = value
                break
        else:
            merged[field_name] = None
    return merged
