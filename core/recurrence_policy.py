"""Recurrence matching and eligibility evaluation (suggestion only).

Approved defaults:
  - lookback 365 days from the current event date/time
  - same verified plate
  - same official violation category
  - officer-confirmed prior cases only
  - each distinct case counted once

Does not invent offense-level labels when policy edges are unresolved.
Does not treat legacy ``violations.status='confirmed'`` as officer confirmation.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from core.violation_policy import (
    LegalStatus,
)

DEFAULT_LOOKBACK_DAYS = 365


class RecurrenceBoundaryStatus(str, enum.Enum):
    """Unresolved policy edges — history may show, no offense suggestion."""

    RESOLVED = "resolved"
    LOWER_BOUND_INCLUSION_UNRESOLVED = "lower_bound_inclusion_unresolved"
    SAME_INSTANT_ORDERING_UNRESOLVED = "same_instant_ordering_unresolved"
    CATEGORY_VERSION_EQUIVALENCE_UNRESOLVED = "category_version_equivalence_unresolved"
    HISTORICAL_AUDIT_EVIDENCE_MISSING = "historical_audit_evidence_missing"
    POLICY_LOOKBACK_INACTIVE = "policy_lookback_inactive"
    LEGAL_CATEGORY_NOT_VERIFIED = "legal_category_not_verified"
    CURRENT_CASE_NOT_READY = "current_case_not_ready"


@dataclass(frozen=True)
class RecurrenceMatch:
    violation_id: int
    event_time: str
    official_category: str | None
    plate_text: str
    case_confirmed: bool
    notice_printed: bool
    policy_version_id: int | None = None


@dataclass(frozen=True)
class RecurrenceEvaluation:
    """Separated matching history, eligibility, suggestion, and decision slot."""

    lookback_days: int
    policy_version_id: int | None
    matched: tuple[RecurrenceMatch, ...]
    eligible: tuple[RecurrenceMatch, ...]
    suggested_prior_offense_count: int | None
    boundary_status: RecurrenceBoundaryStatus
    boundary_notes: tuple[str, ...] = ()
    offense_suggestion_enabled: bool = False
    reviewer_decision: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "lookback_days": self.lookback_days,
            "policy_version_id": self.policy_version_id,
            "matched_violation_ids": [m.violation_id for m in self.matched],
            "eligible_match_ids": [m.violation_id for m in self.eligible],
            "suggested_prior_offense_count": self.suggested_prior_offense_count,
            "boundary_status": self.boundary_status.value,
            "boundary_notes": list(self.boundary_notes),
            "offense_suggestion_enabled": self.offense_suggestion_enabled,
            "reviewer_decision": self.reviewer_decision,
            "detail": dict(self.detail),
        }


def _parse_event_instant(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace(" ", "T")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        # Unscoped naive values are not usable for lookback math.
        return None
    return dt


def _plate_is_verified(plate_row: dict[str, Any] | None) -> tuple[bool, str | None]:
    if plate_row is None:
        return False, None
    status = plate_row.get("plate_status")
    text = (plate_row.get("accepted_plate_text") or "").strip() or None
    if status != "verified_readable" or not text:
        return False, None
    return True, text.upper()


def _applicable_case_mapping(
    adapter: Any,
    violation_id: int,
    canonical_rule: str,
) -> tuple[dict[str, Any] | None, RecurrenceBoundaryStatus | None, str | None]:
    """Resolve the case-level mapping applicable to ``canonical_rule``.

    Live configuration never verifies a stored case. Only the snapshot row for
    the evaluated canonical rule counts — an unrelated verified contributing
    behavior cannot authorize another category.
    """
    rows = adapter.get_case_policy_records(violation_id)
    if not rows:
        return (
            None,
            RecurrenceBoundaryStatus.LEGAL_CATEGORY_NOT_VERIFIED,
            "Case lacks policy snapshot provenance",
        )

    applicable = [
        r for r in rows if (r.get("canonical_rule") or "") == canonical_rule
    ]
    if not applicable:
        return (
            None,
            RecurrenceBoundaryStatus.LEGAL_CATEGORY_NOT_VERIFIED,
            f"No case snapshot for applicable rule {canonical_rule!r}",
        )

    # Ambiguous duplicate rows for the same rule → unresolved.
    if len(applicable) > 1:
        statuses = {r.get("legal_status") for r in applicable}
        categories = {r.get("official_category") for r in applicable}
        if len(statuses) > 1 or len(categories) > 1:
            return (
                applicable[0],
                RecurrenceBoundaryStatus.LEGAL_CATEGORY_NOT_VERIFIED,
                f"Ambiguous case snapshots for rule {canonical_rule!r}",
            )

    row = applicable[0]
    status = row.get("legal_status")
    category = row.get("official_category")
    if status == LegalStatus.VERIFIED.value and category:
        return row, None, None
    if status == LegalStatus.FLAG_ONLY.value:
        return (
            row,
            RecurrenceBoundaryStatus.LEGAL_CATEGORY_NOT_VERIFIED,
            f"Case snapshot for {canonical_rule!r} is flag_only",
        )
    if status == LegalStatus.PARTIALLY_VERIFIED.value:
        return (
            row,
            RecurrenceBoundaryStatus.LEGAL_CATEGORY_NOT_VERIFIED,
            f"Case snapshot for {canonical_rule!r} is partially_verified",
        )
    if not category:
        return (
            row,
            RecurrenceBoundaryStatus.LEGAL_CATEGORY_NOT_VERIFIED,
            f"Case snapshot for {canonical_rule!r} lacks official category",
        )
    return (
        row,
        RecurrenceBoundaryStatus.LEGAL_CATEGORY_NOT_VERIFIED,
        f"Case snapshot for {canonical_rule!r} is {status or 'unverified'}",
    )


def _official_category_for_case(
    adapter: Any,
    violation_id: int,
    canonical_rule: str,
) -> tuple[str | None, RecurrenceBoundaryStatus | None]:
    """Category from the applicable case snapshot only — never live promotion."""
    row, boundary, _note = _applicable_case_mapping(
        adapter, violation_id, canonical_rule
    )
    if row is None:
        return None, boundary or RecurrenceBoundaryStatus.LEGAL_CATEGORY_NOT_VERIFIED
    return row.get("official_category"), boundary


def _historical_case_eligible_for_category(
    adapter: Any,
    violation_id: int,
    official_category: str,
) -> tuple[bool, str | None]:
    """True only when a historical case has a verified snapshot for the category."""
    rows = adapter.get_case_policy_records(violation_id)
    if not rows:
        return False, "missing historical policy snapshot"
    matching = [
        r for r in rows if (r.get("official_category") or "") == official_category
    ]
    if not matching:
        return False, "no historical snapshot for evaluated category"
    verified = [
        r
        for r in matching
        if r.get("legal_status") == LegalStatus.VERIFIED.value
        and r.get("official_category")
    ]
    if verified:
        return True, None
    statuses = sorted({str(r.get("legal_status") or "unverified") for r in matching})
    return False, f"historical snapshot status {','.join(statuses)}"


def find_recurrence_matches(
    adapter: Any,
    *,
    violation_id: int,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    event_time: str | None = None,
    policy_version_id: int | None = None,
) -> list[RecurrenceMatch]:
    """Search backward for candidate prior cases (pre-eligibility filter)."""
    current = adapter.get_violation(violation_id)
    if current is None:
        return []
    if current.get("status") in ("dismissed", "rejected"):
        return []

    plate_row = adapter.get_plate_verification(violation_id)
    ok, plate_text = _plate_is_verified(plate_row)
    if not ok or plate_text is None:
        return []

    current_event = event_time or adapter.get_confirmed_event_time(violation_id)
    current_dt = _parse_event_instant(current_event)
    if current_dt is None:
        return []

    category, _boundary = _official_category_for_case(
        adapter, violation_id, current.get("violation_type") or ""
    )
    if not category:
        return []

    window_start = current_dt - timedelta(days=int(lookback_days))
    candidates = adapter.list_recurrence_candidate_violations(
        plate_text=plate_text,
        official_category=category,
        exclude_violation_id=violation_id,
    )

    matches: list[RecurrenceMatch] = []
    seen_ids: set[int] = set()
    for cand in candidates:
        cid = int(cand["id"])
        if cid in seen_ids or cid == violation_id:
            continue
        cand_event = cand.get("event_time") or adapter.get_confirmed_event_time(cid)
        cand_dt = _parse_event_instant(cand_event)
        if cand_dt is None:
            continue
        # Exclude future events and current instant duplicates here; same-
        # instant ordering is handled as an unresolved boundary later.
        if cand_dt > current_dt:
            continue
        if cand_dt < window_start:
            continue
        if not adapter.is_case_confirmed(cid):
            continue
        # Dismissed / rejected cases never match.
        if cand.get("status") in ("dismissed", "rejected"):
            continue
        plate = adapter.get_plate_verification(cid)
        pok, ptext = _plate_is_verified(plate)
        if not pok or ptext != plate_text:
            continue
        # Preserve each historical case's actual mapping/policy version.
        # Never substitute the current evaluation version for missing provenance.
        hist_version = _case_policy_version_id(adapter, cid)
        matches.append(
            RecurrenceMatch(
                violation_id=cid,
                event_time=cand_event,
                official_category=category,
                plate_text=plate_text,
                case_confirmed=True,
                notice_printed=adapter.is_notice_printed(cid),
                policy_version_id=hist_version,
            )
        )
        seen_ids.add(cid)
    matches.sort(key=lambda m: m.event_time)
    return matches


def _case_policy_version_id(adapter: Any, violation_id: int) -> int | None:
    """Return the stored case policy version, or None when provenance is missing."""
    rows = adapter.get_case_policy_records(violation_id)
    if not rows:
        return None
    versions = {
        int(r["policy_version_id"])
        for r in rows
        if r.get("policy_version_id") is not None
    }
    if not versions:
        return None
    # Multiple versions on one case remain unresolved equivalence territory;
    # surface the first recorded id without inventing a live substitution.
    return int(rows[0]["policy_version_id"])


def load_active_recurrence_policy_context(adapter: Any) -> dict[str, Any]:
    """Load the active approved policy once for an evaluation pass."""
    active = adapter.get_active_legal_policy_version()
    if active is None:
        return {
            "policy_version_id": None,
            "lookback_days": DEFAULT_LOOKBACK_DAYS,
            "lookback_policy_active": False,
            "offense_suggestions_enabled": False,
        }
    lookback = int(active.get("lookback_days") or DEFAULT_LOOKBACK_DAYS)
    enabled = False
    if hasattr(adapter, "offense_suggestions_enabled_for_active_policy"):
        enabled = bool(adapter.offense_suggestions_enabled_for_active_policy())
    else:
        try:
            import json

            detail = json.loads(active.get("detail_json") or "{}")
            enabled = bool(detail.get("offense_suggestions_enabled"))
        except Exception:
            enabled = False
    return {
        "policy_version_id": int(active["id"]),
        "lookback_days": lookback,
        "lookback_policy_active": enabled,
        "offense_suggestions_enabled": enabled,
    }


def evaluate_recurrence_eligibility(
    adapter: Any,
    *,
    violation_id: int,
    lookback_days: int | None = None,
    policy_version_id: int | None = None,
    lookback_policy_active: bool | None = None,
    event_time: str | None = None,
    allow_lower_bound_inclusive: bool | None = None,
) -> RecurrenceEvaluation:
    """Filter matches and decide whether an offense-level suggestion is allowed.

    ``lookback_policy_active`` must be True only when an approved policy version
    explicitly activates lookback use. The mere presence of the 365-day default
    does not enable first/second/third labels.

    When lookback/policy arguments are omitted, the active approved policy is
    loaded once and its actual lookback + version are used.
    """
    notes: list[str] = []
    if lookback_days is None or policy_version_id is None or lookback_policy_active is None:
        ctx = load_active_recurrence_policy_context(adapter)
        if lookback_days is None:
            lookback_days = int(ctx["lookback_days"])
        if policy_version_id is None:
            policy_version_id = ctx["policy_version_id"]
        if lookback_policy_active is None:
            lookback_policy_active = bool(ctx["lookback_policy_active"])
    lookback_days = int(lookback_days if lookback_days is not None else DEFAULT_LOOKBACK_DAYS)

    current = adapter.get_violation(violation_id)
    if current is None:
        return RecurrenceEvaluation(
            lookback_days=lookback_days,
            policy_version_id=policy_version_id,
            matched=(),
            eligible=(),
            suggested_prior_offense_count=None,
            boundary_status=RecurrenceBoundaryStatus.CURRENT_CASE_NOT_READY,
            boundary_notes=("Violation not found",),
            offense_suggestion_enabled=False,
        )

    if current.get("status") in ("dismissed", "rejected"):
        return RecurrenceEvaluation(
            lookback_days=lookback_days,
            policy_version_id=policy_version_id,
            matched=(),
            eligible=(),
            suggested_prior_offense_count=None,
            boundary_status=RecurrenceBoundaryStatus.CURRENT_CASE_NOT_READY,
            boundary_notes=("Current case is dismissed/rejected; no offense suggestion",),
            offense_suggestion_enabled=False,
            detail={"history_visible": False},
        )

    if not lookback_policy_active:
        notes.append(
            "Lookback default exists but offense-level suggestions remain disabled "
            "until CTEU-approved policy activation."
        )
        matched = tuple(
            find_recurrence_matches(
                adapter,
                violation_id=violation_id,
                lookback_days=lookback_days,
                event_time=event_time,
                policy_version_id=policy_version_id,
            )
        )
        return RecurrenceEvaluation(
            lookback_days=lookback_days,
            policy_version_id=policy_version_id,
            matched=matched,
            eligible=(),
            suggested_prior_offense_count=None,
            boundary_status=RecurrenceBoundaryStatus.POLICY_LOOKBACK_INACTIVE,
            boundary_notes=tuple(notes),
            offense_suggestion_enabled=False,
            detail={"history_visible": True},
        )

    canonical = current.get("violation_type") or ""
    current_mapping, mapping_boundary, mapping_note = _applicable_case_mapping(
        adapter, violation_id, canonical
    )
    if mapping_boundary is not None:
        notes.append(mapping_note or "Case-level legal mapping is not verified")
        # History may still be visible for review, but never suggest.
        matched = tuple(
            find_recurrence_matches(
                adapter,
                violation_id=violation_id,
                lookback_days=lookback_days,
                event_time=event_time,
                policy_version_id=policy_version_id,
            )
        )
        return RecurrenceEvaluation(
            lookback_days=lookback_days,
            policy_version_id=policy_version_id,
            matched=matched,
            eligible=(),
            suggested_prior_offense_count=None,
            boundary_status=mapping_boundary,
            boundary_notes=tuple(notes),
            offense_suggestion_enabled=False,
            detail={
                "history_visible": True,
                "lookback_days_used": lookback_days,
                "policy_version_id_used": policy_version_id,
                "case_snapshot_verified": False,
            },
        )

    # Case-level snapshot already verified above. Live mapping helpers are not
    # used to authorize suggestions.

    plate_row = adapter.get_plate_verification(violation_id)
    ok, _plate = _plate_is_verified(plate_row)
    current_event = event_time or adapter.get_confirmed_event_time(violation_id)
    current_dt = _parse_event_instant(current_event)
    if not ok or current_dt is None:
        return RecurrenceEvaluation(
            lookback_days=lookback_days,
            policy_version_id=policy_version_id,
            matched=(),
            eligible=(),
            suggested_prior_offense_count=None,
            boundary_status=RecurrenceBoundaryStatus.CURRENT_CASE_NOT_READY,
            boundary_notes=(
                "Current case lacks verified plate and/or usable event-time provenance",
            ),
            offense_suggestion_enabled=False,
            detail={"case_snapshot_verified": True},
        )

    matched = find_recurrence_matches(
        adapter,
        violation_id=violation_id,
        lookback_days=lookback_days,
        event_time=current_event,
        policy_version_id=policy_version_id,
    )

    # Unresolved edges inspection.
    window_start = current_dt - timedelta(days=int(lookback_days))
    boundary = RecurrenceBoundaryStatus.RESOLVED
    eligible: list[RecurrenceMatch] = []
    current_case_version = _case_policy_version_id(adapter, violation_id)
    current_category, _cat_boundary = _official_category_for_case(
        adapter, violation_id, canonical
    )

    for m in matched:
        m_dt = _parse_event_instant(m.event_time)
        if m_dt is None:
            boundary = RecurrenceBoundaryStatus.HISTORICAL_AUDIT_EVIDENCE_MISSING
            notes.append(
                f"Match {m.violation_id} lacks usable event-time provenance"
            )
            continue
        if m_dt == current_dt:
            boundary = RecurrenceBoundaryStatus.SAME_INSTANT_ORDERING_UNRESOLVED
            notes.append(
                f"Match {m.violation_id} shares the same instant as the current case"
            )
            continue
        if m_dt == window_start:
            if allow_lower_bound_inclusive is None:
                boundary = RecurrenceBoundaryStatus.LOWER_BOUND_INCLUSION_UNRESOLVED
                notes.append(
                    f"Match {m.violation_id} falls on the exact lookback lower bound"
                )
                continue
            if allow_lower_bound_inclusive is False:
                continue
        # Historical case must itself carry a verified applicable snapshot.
        if current_category:
            hist_ok, hist_note = _historical_case_eligible_for_category(
                adapter, m.violation_id, current_category
            )
            if not hist_ok:
                boundary = RecurrenceBoundaryStatus.LEGAL_CATEGORY_NOT_VERIFIED
                notes.append(
                    f"Match {m.violation_id} ineligible: {hist_note}"
                )
                continue
        if m.policy_version_id is None or current_case_version is None:
            boundary = RecurrenceBoundaryStatus.CATEGORY_VERSION_EQUIVALENCE_UNRESOLVED
            notes.append(
                f"Match {m.violation_id} lacks comparable policy-version provenance; "
                "equivalence not decided"
            )
            continue
        if m.policy_version_id != current_case_version:
            boundary = RecurrenceBoundaryStatus.CATEGORY_VERSION_EQUIVALENCE_UNRESOLVED
            notes.append(
                f"Match {m.violation_id} uses a different policy version; "
                "equivalence not decided"
            )
            continue
        eligible.append(m)

    suggestion_enabled = (
        boundary == RecurrenceBoundaryStatus.RESOLVED and lookback_policy_active
    )
    suggested_count = len(eligible) if suggestion_enabled else None

    return RecurrenceEvaluation(
        lookback_days=lookback_days,
        policy_version_id=policy_version_id,
        matched=tuple(matched),
        eligible=tuple(eligible),
        suggested_prior_offense_count=suggested_count,
        boundary_status=boundary,
        boundary_notes=tuple(notes),
        offense_suggestion_enabled=suggestion_enabled,
        detail={
            "history_visible": True,
            "current_event_time": current_event,
            "lookback_days_used": lookback_days,
            "policy_version_id_used": policy_version_id,
            "current_case_policy_version_id": current_case_version,
            "case_snapshot_verified": True,
            "official_category": current_category,
        },
    )


def summarize_recurrence(
    evaluation: RecurrenceEvaluation,
    *,
    reviewer_decision: str | None = None,
) -> dict[str, Any]:
    """Serialize evaluation for UI / persistence (suggestion only)."""
    payload = evaluation.to_dict()
    payload["reviewer_decision"] = reviewer_decision
    if not evaluation.offense_suggestion_enabled:
        payload["suggested_label"] = None
        payload["message"] = (
            "Historical matches may be shown for review, but no offense-level "
            "suggestion is active."
        )
    else:
        count = evaluation.suggested_prior_offense_count or 0
        # Suggestion only — never an autonomous legal determination.
        if count <= 0:
            payload["suggested_label"] = "suggested_first_offense"
        elif count == 1:
            payload["suggested_label"] = "suggested_second_offense"
        else:
            payload["suggested_label"] = "suggested_third_or_succeeding"
        payload["message"] = (
            "Suggested recurrence information for authorized reviewer decision only."
        )
    return payload


def persist_recurrence_evaluation(
    adapter: Any,
    violation_id: int,
    evaluation: RecurrenceEvaluation,
    *,
    evaluated_by: int | None = None,
    reviewer_decision: str | None = None,
) -> int:
    """Store a review-time recurrence snapshot without rewriting history."""
    policy_version_id = evaluation.policy_version_id
    if policy_version_id is None:
        active = adapter.get_active_legal_policy_version()
        if active is not None:
            policy_version_id = int(active["id"])
        else:
            policy_version_id = int(adapter.ensure_config_mapping_policy_version())
    detail = summarize_recurrence(evaluation, reviewer_decision=reviewer_decision)
    record_id = adapter.record_recurrence_review(
        violation_id,
        policy_version_id,
        lookback_days=evaluation.lookback_days,
        matched_violation_ids=[m.violation_id for m in evaluation.matched],
        eligible_match_ids=[m.violation_id for m in evaluation.eligible],
        suggested_recurrence_count=(
            evaluation.suggested_prior_offense_count
            if evaluation.offense_suggestion_enabled
            else 0
        ),
        evaluated_by=evaluated_by,
        detail=detail,
    )
    adapter.record_case_action(
        violation_id,
        adapter.ACTION_RECURRENCE_EVALUATED,
        detail=detail,
        actor_user_id=evaluated_by,
    )
    return record_id
