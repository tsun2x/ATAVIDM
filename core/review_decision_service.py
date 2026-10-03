"""Human review-queue decisions.

Four dispositions are recorded in the append-only ``review_decisions`` table.
The review row keeps the original system suggestion. Confirm and correction
materialize a case only after evidence and policy snapshots are evaluated
with the reviewer-selected canonical rule. No-violation and insufficient
evidence close the item and do not create a case.

Insufficient evidence is its own disposition. It is not no-violation and it
does not confirm a violation.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from core.case_review_service import (
    CaseReviewError,
    ReviewDecisionStateConflict,
    materialize_case_from_review,
)
from core.detection_config import CANONICAL_VIOLATIONS, canonicalize_violation
from database.sqlite_adapter import ReviewDecisionAlreadyRecorded, TemporalEvidenceNotReady

DECISION_CONFIRM = "confirm_proposed"
DECISION_CORRECT = "correct_canonical"
DECISION_NO_VIOLATION = "no_violation"
DECISION_INSUFFICIENT = "insufficient_evidence"

REVIEW_DECISIONS = (
    DECISION_CONFIRM,
    DECISION_CORRECT,
    DECISION_NO_VIOLATION,
    DECISION_INSUFFICIENT,
)

CASE_DECISIONS = frozenset({DECISION_CONFIRM, DECISION_CORRECT})

DECISION_LABELS = {
    DECISION_CONFIRM: "Confirmed proposed violation",
    DECISION_CORRECT: "Corrected canonical violation",
    DECISION_NO_VIOLATION: "No violation",
    DECISION_INSUFFICIENT: "Insufficient evidence",
}

LEGACY_CONFIRM_REASON = "Legacy confirm action. No reviewer reason was supplied."
LEGACY_DISMISS_REASON = "Legacy dismiss action. No reviewer reason was supplied."
LEGACY_CONFIRM_KEY = "legacy:confirm"
LEGACY_DISMISS_KEY = "legacy:dismiss"

_EVIDENCE_FIELDS = (
    "evidence_path",
    "vehicle_evidence_path",
    "plate_evidence_path",
    "evidence_clip_path",
    "evidence_sequence_dir",
    "frame_number",
    "timestamp_sec",
    "episode_start_sec",
    "episode_end_sec",
)


class ReviewDecisionError(ValueError):
    """The decision request cannot be applied."""


class ReviewDecisionConflict(Exception):
    """The item is already closed by a different decision."""


def disposition_label(decision: str) -> str:
    label = DECISION_LABELS.get(decision, "")
    if decision == DECISION_INSUFFICIENT and label == DECISION_LABELS[DECISION_NO_VIOLATION]:
        raise ReviewDecisionError("Insufficient evidence must not be labeled no violation")
    return label


def evidence_refs_for_row(row: dict[str, Any]) -> dict[str, Any]:
    """Copy evidence fields that are actually stored. Do not invent any."""
    refs: dict[str, Any] = {}
    for key in _EVIDENCE_FIELDS:
        value = row.get(key)
        if value is not None and value != "":
            refs[key] = value
    return refs


def assert_temporal_evidence_ready(row: dict[str, Any]) -> None:
    """Same gate as case confirmation: tagged post-roll must already be written."""
    temporal_tagged = row.get("evidence_pre_sec") is not None
    has_clip = bool(row.get("evidence_clip_path") or row.get("evidence_sequence_dir"))
    episode_closed = row.get("episode_end_sec") is not None
    if temporal_tagged and (not has_clip or not episode_closed):
        raise TemporalEvidenceNotReady(
            "Temporal evidence is still being finalized; confirmation is blocked "
            "until the post-roll clip/sequence is written."
        )


def apply_review_decision(
    adapter: Any,
    review_id: int,
    reviewer_user_id: int,
    *,
    decision: str,
    reason: str,
    selected_canonical_rule: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Apply one human decision. The reviewer id comes from the caller, not the client."""
    if decision not in REVIEW_DECISIONS:
        raise ReviewDecisionError("Unknown review decision.")
    cleaned_reason = str(reason or "").strip()
    if not cleaned_reason or len(cleaned_reason) > 2000:
        raise ReviewDecisionError("A decision reason is required.")
    key = (idempotency_key or f"auto:{decision}").strip()
    if not key or len(key) > 200 or any(ch.isspace() for ch in key):
        raise ReviewDecisionError("Invalid idempotency key.")

    _authorize(adapter, reviewer_user_id, decision)

    row = adapter.get_review_item(review_id)
    if row is None:
        raise CaseReviewError(f"Review item {review_id} not found")

    original = str(row.get("violation_type") or "")
    selected = _selected_rule(decision, original, selected_canonical_rule)
    existing = adapter.get_review_decision(review_id)
    if existing is not None:
        return _retry_or_conflict(
            adapter, existing, decision, selected, key, cleaned_reason, row
        )
    if row.get("status") != "pending":
        raise ReviewDecisionConflict(
            "Review item is already closed and has no review decision."
        )

    evidence_refs = evidence_refs_for_row(row)
    if decision in CASE_DECISIONS:
        assert_temporal_evidence_ready(row)
        payload = {
            "review_id": int(review_id),
            "decision": decision,
            "original_violation_type": original,
            "selected_canonical_rule": selected,
            "reason": cleaned_reason,
            "reviewer_user_id": int(reviewer_user_id),
            "idempotency_key": key,
            "evidence_refs": evidence_refs,
        }
        try:
            materialized = materialize_case_from_review(
                adapter,
                int(review_id),
                int(reviewer_user_id),
                canonical_override=selected if decision == DECISION_CORRECT else None,
                decision_insert=payload,
            )
        except (
            sqlite3.IntegrityError,
            ReviewDecisionStateConflict,
            ReviewDecisionAlreadyRecorded,
        ) as exc:
            # Raised under the write lock before any write: another request
            # closed this review first. Only a matching retry may continue.
            return _integrity_retry(
                adapter, review_id, decision, selected, key, cleaned_reason, exc
            )
        except CaseReviewError as exc:
            # A competing decision can commit after the initial read.
            if adapter.get_review_decision(review_id) is None and "not pending" not in str(exc):
                raise
            return _integrity_retry(
                adapter, review_id, decision, selected, key, cleaned_reason, exc
            )
        except ValueError as exc:
            if "not pending" not in str(exc):
                raise
            return _integrity_retry(
                adapter, review_id, decision, selected, key, cleaned_reason, exc
            )
        stored = adapter.get_review_decision(review_id)
        if stored is None or not _same_request(stored, decision, selected, key, cleaned_reason):
            raise ReviewDecisionConflict(
                "Review item already has a different human decision."
            )
        return _public_result(stored, materialized=materialized, idempotent=False)

    policy_version = adapter.get_active_legal_policy_version()
    policy_refs = {
        "materialized": False,
        "policy_version_id": int(policy_version["id"]) if policy_version else None,
        "snapshots": [],
    }
    try:
        adapter.close_review_with_decision(
            int(review_id),
            int(reviewer_user_id),
            decision=decision,
            original_violation_type=original,
            reason=cleaned_reason,
            idempotency_key=key,
            evidence_refs=evidence_refs,
            policy_refs=policy_refs,
        )
    except (sqlite3.IntegrityError, ReviewDecisionAlreadyRecorded) as exc:
        return _integrity_retry(
            adapter, review_id, decision, selected, key, cleaned_reason, exc
        )
    except ValueError as exc:
        if "not pending" not in str(exc):
            raise
        return _integrity_retry(
            adapter, review_id, decision, selected, key, cleaned_reason, exc
        )
    stored = adapter.get_review_decision(review_id)
    if stored is None or not _same_request(stored, decision, selected, key, cleaned_reason):
        raise ReviewDecisionConflict("Review item already has a different human decision.")
    return _public_result(stored, materialized=None, idempotent=False)


def _authorize(adapter: Any, reviewer_user_id: int, decision: str) -> None:
    user = adapter.get_user(int(reviewer_user_id))
    if user is None or not bool(user.get("is_active", 1)):
        raise PermissionError("Reviewer is not an active account.")
    if decision in CASE_DECISIONS:
        if not adapter.can_confirm_case(int(reviewer_user_id)):
            raise PermissionError("Reviewer lacks confirm_case authority.")
        return
    if user.get("role") not in ("admin", "enforcer"):
        raise PermissionError("Reviewer cannot close a review item.")


def _selected_rule(
    decision: str,
    original: str,
    requested: str | None,
) -> str | None:
    if decision == DECISION_CONFIRM:
        selected = canonicalize_violation(original)
        if selected not in CANONICAL_VIOLATIONS:
            raise ReviewDecisionError("Proposed violation is not a canonical rule.")
        return selected
    if decision == DECISION_CORRECT:
        selected = canonicalize_violation(str(requested or "").strip())
        if selected not in CANONICAL_VIOLATIONS:
            raise ReviewDecisionError("Select an existing canonical violation.")
        if selected == canonicalize_violation(original):
            raise ReviewDecisionError(
                "Correction must select a different canonical violation."
            )
        return selected
    if requested:
        raise ReviewDecisionError("This decision does not select a canonical rule.")
    return None


def _same_request(
    existing: dict[str, Any],
    decision: str,
    selected: str | None,
    key: str,
    reason: str,
) -> bool:
    return (
        existing.get("decision") == decision
        and existing.get("idempotency_key") == key
        and (existing.get("selected_canonical_rule") or None) == selected
        and existing.get("reason") == reason
    )


def _retry_or_conflict(
    adapter: Any,
    existing: dict[str, Any],
    decision: str,
    selected: str | None,
    key: str,
    reason: str,
    row: dict[str, Any],
) -> dict[str, Any]:
    if not _same_request(existing, decision, selected, key, reason):
        raise ReviewDecisionConflict(
            "Review item already has a different human decision."
        )
    if str(row.get("violation_type") or "") != str(existing.get("original_violation_type") or ""):
        raise ReviewDecisionConflict("Original system suggestion was not preserved.")
    materialized = None
    if decision in CASE_DECISIONS:
        materialized = materialize_case_from_review(
            adapter,
            int(existing["review_id"]),
            int(existing["reviewer_user_id"]),
            canonical_override=selected if decision == DECISION_CORRECT else None,
        )
    return _public_result(existing, materialized=materialized, idempotent=True)


def _integrity_retry(
    adapter: Any,
    review_id: int,
    decision: str,
    selected: str | None,
    key: str,
    reason: str,
    exc: BaseException,
) -> dict[str, Any]:
    existing = adapter.get_review_decision(review_id)
    row = adapter.get_review_item(review_id) or {}
    if existing is None:
        raise ReviewDecisionConflict(
            "Review item was closed before this decision could be stored."
        ) from exc
    return _retry_or_conflict(adapter, existing, decision, selected, key, reason, row)


def _public_result(
    stored: dict[str, Any] | None,
    *,
    materialized: dict[str, Any] | None,
    idempotent: bool,
) -> dict[str, Any]:
    if stored is None:
        raise CaseReviewError("Review decision was not stored.")
    decision = str(stored["decision"])
    label = disposition_label(decision)
    result: dict[str, Any] = {
        "decision": decision,
        "disposition_label": label,
        "review_id": int(stored["review_id"]),
        "review_decision_id": int(stored["id"]),
        "original_violation_type": stored.get("original_violation_type"),
        "selected_canonical_rule": stored.get("selected_canonical_rule"),
        "reason": stored.get("reason"),
        "reviewer_user_id": stored.get("reviewer_user_id"),
        "decided_at": stored.get("decided_at"),
        "idempotent_retry": idempotent,
        "creates_case": decision in CASE_DECISIONS,
        "violation_id": None,
    }
    if materialized:
        result.update(materialized)
        result["violation_id"] = materialized.get("violation_id")
        result["creates_case"] = True
    if decision == DECISION_INSUFFICIENT:
        result["violation_id"] = None
        result["creates_case"] = False
        if result["disposition_label"] == DECISION_LABELS[DECISION_NO_VIOLATION]:
            raise ReviewDecisionError("Insufficient evidence was labeled as no violation.")
    return result
