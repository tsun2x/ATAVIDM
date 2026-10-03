"""Authorized case review operations: plate, event-time, confirmation wiring.

Thin application/service layer between Flask routes and persistence helpers.
Actors are always derived from authenticated user ids passed by callers —
never from client-supplied reviewer flags.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from collections.abc import Mapping

from core.case_identity import (
    ObservationRef,
    build_case_group,
    find_existing_fused_case_violation_id,
    merge_evidence_from_group,
    observation_from_review_row,
    policy_snapshots_for_group,
)
from core.event_time import (
    EventTimeClaim,
    EventTimeResult,
    EventTimeSource,
    confirm_event_time as helper_confirm_event_time,
    from_user_entry,
    resolve_event_time,
    usable_event_instant,
)
from core.plate_processing import (
    HUMAN_PLATE_STATUS_CANDIDATE_AWAITING_VERIFICATION,
    HUMAN_PLATE_STATUS_NOT_VISIBLE,
    HUMAN_PLATE_STATUS_UNCLEAR,
    HUMAN_PLATE_STATUS_VERIFIED_READABLE,
    PlateProcessingOutcome,
    PlateProcessor,
)


_NO_CASE_DECISIONS = frozenset({"no_violation", "insufficient_evidence"})

VERIFIED_PLATE_STATUSES = frozenset({HUMAN_PLATE_STATUS_VERIFIED_READABLE})
NON_IDENTITY_PLATE_STATUSES = frozenset(
    {
        HUMAN_PLATE_STATUS_UNCLEAR,
        HUMAN_PLATE_STATUS_NOT_VISIBLE,
        HUMAN_PLATE_STATUS_CANDIDATE_AWAITING_VERIFICATION,
    }
)


class CaseReviewError(ValueError):
    """Domain error for case review operations."""


class ReviewDecisionStateConflict(CaseReviewError):
    """Another request already closed the review. Nothing was written."""

    def __init__(self, message: str, existing: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.existing = existing


def _review_write_transaction(adapter: Any):
    tx = getattr(adapter, "review_write_transaction", None)
    if tx is None:
        raise CaseReviewError(
            "Adapter lacks review_write_transaction; serialized decision writes required"
        )
    return tx()


def _stored_review_decision(adapter: Any, review_id: int) -> dict[str, Any] | None:
    getter = getattr(adapter, "get_review_decision", None)
    if getter is None:
        return None
    return getter(int(review_id))


def _effective_violation_type(
    row: dict[str, Any],
    decision: dict[str, Any] | None,
) -> str | None:
    """Canonical rule used for grouping.

    A saved correction or confirmation wins over the original queue suggestion.
    No-violation and insufficient-evidence decisions return None so the
    observation cannot re-enter fusion or materialization. Rows with no
    decision keep the stored suggestion.
    """
    from core.detection_config import canonicalize_violation

    if decision is not None and str(decision.get("decision") or "") in _NO_CASE_DECISIONS:
        return None
    if decision is not None and decision.get("selected_canonical_rule"):
        selected = canonicalize_violation(str(decision["selected_canonical_rule"]))
        if selected:
            return selected
    suggested = canonicalize_violation(row.get("violation_type") or "")
    return suggested or str(row.get("violation_type") or "")


def _observation_from_queue_row(
    adapter: Any,
    row: dict[str, Any],
) -> ObservationRef | None:
    """Rebuild one observation from the queue row and its saved decision."""
    effective = _effective_violation_type(row, _stored_review_decision(adapter, int(row["id"])))
    if effective is None:
        return None
    observed = observation_from_review_row(row)
    if observed.violation_type == effective:
        return observed
    return replace(observed, violation_type=effective)


def _observation_for_materialization(
    adapter: Any,
    row: dict[str, Any],
    canonical_override: str | None,
) -> ObservationRef:
    """Use the saved human rule when one exists. Do not rewrite the queue row.

    ``canonical_override`` applies only while the review has no decision yet.
    A later recovery or sibling load cannot replace that saved rule with the
    original suggestion.
    """
    decision = _stored_review_decision(adapter, int(row["id"]))
    if decision is not None and str(decision.get("decision") or "") in _NO_CASE_DECISIONS:
        raise CaseReviewError(
            f"Review item {row['id']} is closed without a case and cannot be materialized"
        )
    if decision is not None and decision.get("selected_canonical_rule"):
        effective = _effective_violation_type(row, decision)
        observed = observation_from_review_row(row)
        if effective and observed.violation_type != effective:
            return replace(observed, violation_type=effective)
        return observed
    observed = observation_from_review_row(row)
    if not canonical_override:
        return observed
    return replace(observed, violation_type=canonical_override)


def _decision_payload_with_policy(
    decision_insert: dict[str, Any] | None,
    group,
    policy_version_id: int,
) -> dict[str, Any] | None:
    """Attach the group policy snapshot computed before case materialization."""
    if not decision_insert:
        return None
    payload = dict(decision_insert)
    payload["policy_refs"] = {
        "policy_version_id": int(policy_version_id),
        "snapshots": policy_snapshots_for_group(group),
        "contributing_rules": list(group.contributing_rules),
        "primary_canonical_rule": group.primary_canonical_rule,
        "computed_before_materialization": True,
    }
    return payload


def materialize_case_from_review(
    adapter: Any,
    review_id: int,
    reviewed_by: int,
    *,
    canonical_override: str | None = None,
    decision_insert: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Confirm a review item into a stable logical case.

    For overlapping parking/obstruction observations, creates or reuses one
    violation identity, retains contributing behaviors in case_policy_records,
    and links every contributing review to that case. Idempotent retries of the
    same review do not create a second recurrence-countable identity.

    A linked violation alone is not success — retries finish missing links,
    evidence, and required per-behavior policy snapshots without duplication.
    The policy version selected for the operation is preserved across retries.

    Authorization: active account with case-review capability
    (``can_confirm_case``) — Traffic Enforcer, System Administrator, or
    explicit grant. Legal-policy approval is a separate capability.

    Concurrency: decision state is read only while the adapter's review write
    lock is held. The first locked transaction re-reads the review, its saved
    decision, and the sibling group, then commits the case or attachment, the
    human decision, and every group member's link together. When
    ``decision_insert`` is given and any decision already exists, it raises
    ``ReviewDecisionStateConflict`` before writing, so the caller can tell a
    matching retry from a conflict. The second locked transaction rebuilds the
    group from current state and finishes evidence and policy snapshots. A
    failure there leaves the first transaction in place for recovery.
    """
    if not adapter.can_confirm_case(reviewed_by):
        raise PermissionError(
            f"User {reviewed_by} lacks confirm_case / case-review authority"
        )

    with _review_write_transaction(adapter):
        reused = _materialize_initial_locked(
            adapter,
            int(review_id),
            int(reviewed_by),
            canonical_override=canonical_override,
            decision_insert=decision_insert,
        )
    with _review_write_transaction(adapter):
        return _recover_or_return_materialization(
            adapter,
            int(review_id),
            int(reviewed_by),
            canonical_override=canonical_override,
            reused=reused,
        )


def _materialize_initial_locked(
    adapter: Any,
    review_id: int,
    reviewed_by: int,
    *,
    canonical_override: str | None,
    decision_insert: dict[str, Any] | None,
) -> bool:
    """Atomic first stage. Returns True when an existing case was used.

    Runs inside the review write lock, so every read below is authoritative
    until the transaction ends.
    """
    row = adapter.get_review_item(review_id)
    if row is None:
        raise CaseReviewError(f"Review item {review_id} not found")

    stored_decision = _stored_review_decision(adapter, review_id)
    if decision_insert is not None and stored_decision is not None:
        raise ReviewDecisionStateConflict(
            f"Review item {review_id} already has a human decision",
            existing=stored_decision,
        )
    if stored_decision is not None and str(stored_decision.get("decision") or "") in _NO_CASE_DECISIONS:
        raise CaseReviewError(
            f"Review item {review_id} is closed without a case and cannot be materialized"
        )

    if row.get("status") != "pending":
        if decision_insert is not None:
            raise ReviewDecisionStateConflict(
                f"Review item {review_id} is not pending (status: {row.get('status')})"
            )
        return True

    primary = _observation_for_materialization(adapter, row, canonical_override)
    siblings = _load_sibling_observations(adapter, primary)
    group = build_case_group(primary, siblings)
    policy_version_id = _resolve_policy_version_id(adapter)
    decision_payload = _decision_payload_with_policy(
        decision_insert, group, policy_version_id
    )

    existing_vid = find_existing_fused_case_violation_id(adapter, group)
    if existing_vid is not None:
        viol = adapter.get_violation(int(existing_vid))
        if viol is not None and viol.get("status") == "dismissed":
            existing_vid = None
    if existing_vid is not None:
        reuse_version = _resolve_original_policy_version(adapter, int(existing_vid))
        decision_payload = _decision_payload_with_policy(
            decision_insert, group, reuse_version
        )
        if decision_payload is not None:
            adapter.attach_review_decision_to_existing_case(
                int(review_id),
                int(existing_vid),
                int(reviewed_by),
                policy_version_id=reuse_version,
                contributing_rules=group.contributing_rules,
                review_ids=group.review_ids,
                decision_insert=decision_payload,
            )
        _claim_group_members(adapter, group, int(existing_vid), reviewed_by)
        return True

    primary_for_insert = _choose_primary_observation(group)
    create_initial = getattr(adapter, "create_case_with_materialization_intent", None)
    if create_initial is None:
        raise CaseReviewError(
            "Adapter lacks create_case_with_materialization_intent; "
            "atomic initial materialization write required"
        )
    extra_links = (
        [review_id]
        if primary_for_insert.review_id != review_id
        else []
    )
    stored_primary = adapter.get_review_item(primary_for_insert.review_id) or {}
    insert_override = None
    if primary_for_insert.violation_type != stored_primary.get("violation_type"):
        insert_override = primary_for_insert.violation_type
    violation_id = create_initial(
        primary_for_insert.review_id,
        reviewed_by,
        policy_version_id=policy_version_id,
        contributing_rules=group.contributing_rules,
        review_ids=group.review_ids,
        additional_link_review_ids=extra_links,
        violation_type_override=insert_override,
        decision_insert=decision_payload,
    )
    if group.is_fused_parking_obstruction:
        adapter.update_violation_canonical_type(
            violation_id, group.primary_canonical_rule
        )
    _claim_group_members(adapter, group, int(violation_id), reviewed_by)
    return False


def _claim_group_members(
    adapter: Any,
    group,
    violation_id: int,
    reviewed_by: int,
) -> None:
    """Confirm and link every group member in the first-stage transaction.

    A member closed without a case must never be linked. Under the write lock
    the group excludes such rows, so reaching one means the group is stale.
    """
    for obs in group.observations:
        row = adapter.get_review_item(obs.review_id)
        if row is None:
            continue
        _assert_member_can_join(adapter, row)
        if row.get("status") == "pending":
            adapter.mark_review_confirmed_linked(obs.review_id, violation_id, reviewed_by)
        else:
            adapter.link_review_to_case(obs.review_id, violation_id)


def _assert_member_can_join(adapter: Any, row: dict[str, Any]) -> None:
    decision = _stored_review_decision(adapter, int(row["id"]))
    closed_without_case = (
        decision is not None
        and str(decision.get("decision") or "") in _NO_CASE_DECISIONS
    )
    if closed_without_case or row.get("status") == "dismissed":
        raise ReviewDecisionStateConflict(
            f"Review item {row['id']} was closed without a case; the case group is stale",
            existing=decision,
        )


def _recover_or_return_materialization(
    adapter: Any,
    review_id: int,
    reviewed_by: int,
    row: dict[str, Any] | None = None,
    *,
    canonical_override: str | None = None,
    reused: bool = True,
) -> dict[str, Any]:
    """Finish incomplete materialization or return a verified-complete result.

    Callers hold the review write lock. The review row and group are re-read
    here so a stale snapshot from before the lock is never used.
    """
    row = adapter.get_review_item(review_id)
    if row is None:
        raise CaseReviewError(f"Review item {review_id} not found")
    existing = adapter.find_violation_linked_to_review(review_id)
    if existing is None and hasattr(adapter, "find_orphan_violation_for_review"):
        existing = adapter.find_orphan_violation_for_review(review_id)
        if existing is not None:
            adapter.link_review_to_case(review_id, int(existing))

    if existing is None:
        raise CaseReviewError(
            f"Review item {review_id} is not pending (status: {row.get('status')})"
        )

    viol = adapter.get_violation(int(existing))
    if viol is None or viol.get("status") == "dismissed":
        raise CaseReviewError(
            f"Review item {review_id} is not pending (status: {row.get('status')})"
        )

    primary = _observation_for_materialization(adapter, row, canonical_override)
    siblings = _load_sibling_observations(adapter, primary)
    # Include already-linked observations for this violation so fused recovery
    # knows every required contributing behavior.
    linked_extras = _load_linked_observations_for_violation(
        adapter, int(existing), exclude_review_id=review_id
    )
    by_id = {o.review_id: o for o in siblings}
    for extra in linked_extras:
        by_id.setdefault(extra.review_id, extra)
    group = build_case_group(primary, list(by_id.values()))

    # Recover only from durable intent or usable snapshot provenance —
    # never invent from the currently active policy.
    policy_version_id = _resolve_original_policy_version(adapter, int(existing))
    _complete_materialization(
        adapter,
        group,
        int(existing),
        reviewed_by,
        policy_version_id=policy_version_id,
    )
    return _materialization_result(
        adapter, int(existing), group, reused=reused
    )


def _load_linked_observations_for_violation(
    adapter: Any,
    violation_id: int,
    *,
    exclude_review_id: int,
) -> list[ObservationRef]:
    out: list[ObservationRef] = []
    actions = adapter.get_case_actions(violation_id)
    seen: set[int] = set()
    for action in actions:
        if action.get("action_type") != getattr(
            adapter, "ACTION_REVIEW_CONFIRMED", "review_confirmed"
        ):
            continue
        rid = action.get("review_id")
        if rid is None or int(rid) == exclude_review_id or int(rid) in seen:
            continue
        seen.add(int(rid))
        row = adapter.get_review_item(int(rid))
        if row is None or str(row.get("status") or "") == "dismissed":
            continue
        observed = _observation_from_queue_row(adapter, row)
        if observed is None:
            continue
        out.append(observed)
    return out


def _resolve_policy_version_id(adapter: Any) -> int:
    version = adapter.get_active_legal_policy_version()
    if version is not None:
        return int(version["id"])
    return int(adapter.ensure_config_mapping_policy_version())


def _record_materialization_intent(
    adapter: Any,
    violation_id: int,
    *,
    policy_version_id: int,
    contributing_rules: tuple[str, ...] | list[str],
    review_ids: tuple[int, ...] | list[int],
    actor_user_id: int,
) -> None:
    """Persist the policy context selected for this materialization (idempotent)."""
    if _get_materialization_intent(adapter, violation_id) is not None:
        return
    adapter.record_case_action(
        violation_id,
        getattr(adapter, "ACTION_REVIEW_CONFIRMED", "review_confirmed"),
        detail={
            "materialization_intent": True,
            "policy_version_id": int(policy_version_id),
            "contributing_rules": list(contributing_rules),
            "review_ids": [int(r) for r in review_ids],
        },
        actor_user_id=actor_user_id,
    )


def _get_materialization_intent(
    adapter: Any, violation_id: int
) -> dict[str, Any] | None:
    import json

    for action in adapter.get_case_actions(violation_id):
        try:
            detail = json.loads(action.get("detail_json") or "{}")
        except Exception:
            continue
        if detail.get("materialization_intent"):
            return detail
    return None


def _policy_version_for_case(
    adapter: Any,
    violation_id: int,
    fallback_version_id: int,
) -> int:
    """Compatibility wrapper — prefer original durable context; never invent.

    ``fallback_version_id`` is ignored for incomplete historical recovery.
    New materializations select the active policy before case creation.
    """
    del fallback_version_id  # intentional: active policy is not historical context
    return _resolve_original_policy_version(adapter, violation_id)


def _resolve_original_policy_version(adapter: Any, violation_id: int) -> int:
    """Return the attributable original policy version for an existing case.

    Sources: materialization_intent and existing case_policy_records.
    Missing context or conflicting versions raise CaseReviewError without writes.
    """
    versions: set[int] = set()
    intent = _get_materialization_intent(adapter, violation_id)
    if intent and intent.get("policy_version_id") is not None:
        versions.add(int(intent["policy_version_id"]))
    for row in adapter.get_case_policy_records(violation_id):
        if row.get("policy_version_id") is not None:
            versions.add(int(row["policy_version_id"]))
    if not versions:
        raise CaseReviewError(
            f"Materialization recovery unresolved for violation {violation_id}: "
            "missing original policy intent and usable snapshots"
        )
    if len(versions) > 1:
        raise CaseReviewError(
            f"Materialization recovery unresolved for violation {violation_id}: "
            f"conflicting policy versions {sorted(versions)}"
        )
    return next(iter(versions))


def _required_contributing_rules(group) -> set[str]:
    return set(group.contributing_rules)


def _materialization_gaps(
    adapter: Any,
    violation_id: int,
    group,
) -> dict[str, Any]:
    """Return missing required pieces; empty dict means complete."""
    records = adapter.get_case_policy_records(violation_id)
    stored_rules = {r.get("canonical_rule") for r in records if r.get("canonical_rule")}
    required = _required_contributing_rules(group)
    missing_rules = sorted(required - stored_rules)

    missing_links: list[int] = []
    for obs in group.observations:
        linked = adapter.find_violation_linked_to_review(obs.review_id)
        if linked is None or int(linked) != int(violation_id):
            missing_links.append(obs.review_id)

    return {
        "missing_rules": missing_rules,
        "missing_links": missing_links,
    }


def _materialization_is_complete(adapter: Any, violation_id: int, group) -> bool:
    gaps = _materialization_gaps(adapter, violation_id, group)
    return not gaps["missing_rules"] and not gaps["missing_links"]


def _complete_materialization(
    adapter: Any,
    group,
    violation_id: int,
    reviewed_by: int,
    *,
    policy_version_id: int,
) -> None:
    """Attach links/evidence/snapshots until the case is consistent (no duplicates)."""
    viol = adapter.get_violation(violation_id)
    if viol is None:
        raise CaseReviewError(f"Violation {violation_id} not found")
    if viol.get("status") == "dismissed":
        raise CaseReviewError(
            f"Violation {violation_id} is dismissed; cannot attach observations"
        )

    _record_materialization_intent(
        adapter,
        violation_id,
        policy_version_id=policy_version_id,
        contributing_rules=group.contributing_rules,
        review_ids=group.review_ids,
        actor_user_id=reviewed_by,
    )

    for obs in group.observations:
        row = adapter.get_review_item(obs.review_id)
        if row is None:
            continue
        _assert_member_can_join(adapter, row)
        if row.get("status") == "pending":
            adapter.mark_review_confirmed_linked(
                obs.review_id, violation_id, reviewed_by
            )
        adapter.link_review_to_case(obs.review_id, violation_id)

    if group.is_fused_parking_obstruction:
        adapter.update_violation_canonical_type(
            violation_id, group.primary_canonical_rule
        )

    evidence = merge_evidence_from_group(group)
    adapter.merge_violation_evidence(violation_id, evidence)
    _persist_policy_snapshots(
        adapter, violation_id, group, policy_version_id=policy_version_id
    )

    if not _materialization_is_complete(adapter, violation_id, group):
        gaps = _materialization_gaps(adapter, violation_id, group)
        raise CaseReviewError(
            f"Materialization incomplete for violation {violation_id}: {gaps}"
        )


def _materialization_result(
    adapter: Any,
    violation_id: int,
    group,
    *,
    reused: bool,
) -> dict[str, Any]:
    if not _materialization_is_complete(adapter, violation_id, group):
        gaps = _materialization_gaps(adapter, violation_id, group)
        raise CaseReviewError(
            f"Materialization incomplete for violation {violation_id}: {gaps}"
        )
    return {
        "violation_id": int(violation_id),
        "review_ids": list(group.review_ids),
        "reused_existing": reused,
        "fused": group.is_fused_parking_obstruction,
        "contributing_rules": list(group.contributing_rules),
        "proposed_official_category": group.proposed_official_category,
        "verified_official_category": group.verified_official_category,
        "episode_identity_uncertain": group.episode_identity_uncertain,
    }


def _choose_primary_observation(group):
    if group.is_fused_parking_obstruction:
        for obs in group.observations:
            if obs.violation_type == group.primary_canonical_rule:
                return obs
    return group.observations[0]


def _load_sibling_observations(adapter: Any, primary: ObservationRef) -> list[ObservationRef]:
    """Load pending and already-linked observations for the same source/run/track.

    Already-confirmed siblings must participate so a late fusion member can
    attach to the established episode case. Dismissed reviews are excluded.
    Observations already linked to a dismissed case are also excluded so a new
    case cannot be forced incomplete by a closed prior episode.
    """
    if hasattr(adapter, "list_reviews_for_event"):
        rows = adapter.list_reviews_for_event(
            video_id=primary.video_id,
            track_id=primary.track_id,
            processing_run_id=primary.processing_run_id,
        )
    else:
        rows = adapter.list_pending_reviews_for_event(
            video_id=primary.video_id,
            track_id=primary.track_id,
            processing_run_id=primary.processing_run_id,
        )
    out: list[ObservationRef] = []
    for r in rows:
        if int(r["id"]) == primary.review_id:
            continue
        if str(r.get("status") or "") == "dismissed":
            continue
        linked = adapter.find_violation_linked_to_review(int(r["id"]))
        if linked is not None:
            viol = adapter.get_violation(int(linked))
            if viol is not None and viol.get("status") == "dismissed":
                continue
        observed = _observation_from_queue_row(adapter, r)
        if observed is None:
            continue
        out.append(observed)
    return out


def _persist_policy_snapshots(
    adapter: Any,
    violation_id: int,
    group,
    *,
    policy_version_id: int | None = None,
) -> None:
    if policy_version_id is None:
        policy_version_id = _resolve_policy_version_id(adapter)
    for snap in policy_snapshots_for_group(group):
        adapter.create_case_policy_record(
            violation_id=violation_id,
            policy_version_id=int(policy_version_id),
            canonical_rule=snap["canonical_rule"],
            official_category=snap["official_category"],
            legal_status=snap["legal_status"],
            behavior_details=snap["behavior_details"],
        )


# ---------------------------------------------------------------------------
# Plate processing + human verification
# ---------------------------------------------------------------------------


def process_plate_for_evidence(
    processor: PlateProcessor | None,
    image: Any,
    *,
    evidence_ref: str | None = None,
) -> dict[str, Any]:
    """Run the injected plate adapter after a possible violation.

    Missing processor → explicit unavailable state (cases are not discarded).
    """
    if processor is None:
        return {
            "outcome": PlateProcessingOutcome.PROCESSING_UNAVAILABLE.value,
            "candidates": [],
            "human_plate_status_suggestion": None,
            "unavailable_reason": "No plate backend configured",
        }
    result = processor.read_plate(image, evidence_ref=evidence_ref)
    suggestion = None
    if result.outcome == PlateProcessingOutcome.CANDIDATE_FOUND:
        suggestion = HUMAN_PLATE_STATUS_CANDIDATE_AWAITING_VERIFICATION
    elif result.outcome == PlateProcessingOutcome.NO_CANDIDATE_DETECTED:
        # Machine empty ≠ human not_visible.
        suggestion = HUMAN_PLATE_STATUS_CANDIDATE_AWAITING_VERIFICATION
    return {
        "outcome": result.outcome.value,
        "candidates": [
            {
                "ocr_raw": c.ocr_raw,
                "ocr_confidence": c.ocr_confidence,
                "bounding_box": (
                    [c.bounding_box.x1, c.bounding_box.y1, c.bounding_box.x2, c.bounding_box.y2]
                    if c.bounding_box
                    else None
                ),
            }
            for c in result.candidates
        ],
        "human_plate_status_suggestion": suggestion,
        "provider": result.provider,
        "diagnostics": dict(result.diagnostics or {}),
    }


def verify_plate_identity(
    adapter: Any,
    violation_id: int,
    actor_user_id: int,
    *,
    plate_status: str,
    accepted_plate_text: str | None = None,
    candidate_ocr_raw: str | None = None,
    candidate_reference: str | None = None,
    evidence_crop_ref: str | None = None,
    ocr_confidence: float | None = None,
    review_id: int | None = None,
    machine_provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Authorized human plate acceptance/correction.

    ``verified_readable`` requires fully readable accepted text. Unclear and
    not_visible retain the vehicle/evidence record without establishing identity.

    Authority: **active System Administrator only**. This check lives in the
    service, not only in the route, so a direct service call cannot bypass it.
    An explicit ``verify_plate`` grant does not bypass it either. This is a
    deliberate, narrow restriction for the experimental plate demo; it does not
    change ``can_verify_plate``, ``ENFORCEMENT_ROLES``, case confirmation,
    event-time review, review decisions, or any other enforcer permission.

    Machine jobs must never call this function. Only an explicit human action
    may write accepted identity or ``verified_readable``.

    Plate row, audit history, and legacy field sync are one atomic persistence
    operation - any failure rolls all three back.
    """
    confirm = getattr(adapter, "can_confirm_plate_identity", None)
    if confirm is None:
        raise CaseReviewError(
            "Adapter lacks can_confirm_plate_identity; active-admin authority required"
        )
    if not confirm(actor_user_id):
        raise PermissionError(
            f"User {actor_user_id} lacks admin-only plate confirmation authority"
        )
    viol = adapter.get_violation(violation_id)
    if viol is None:
        raise CaseReviewError(f"Violation {violation_id} not found")
    if viol.get("status") == "dismissed":
        raise CaseReviewError(
            f"Violation {violation_id} is dismissed; cannot verify plate"
        )

    if candidate_reference is None and any(
        value is not None
        for value in (candidate_ocr_raw, evidence_crop_ref, ocr_confidence, machine_provenance)
    ):
        raise CaseReviewError("Machine candidate metadata requires a server-resolved candidate reference")
    if candidate_reference is not None:
        try:
            from core.plate_review import manifest_provenance_for_verification, resolve_candidate
            from core import plate_manifest as plate_manifests

            attempt_id, candidate_id = str(candidate_reference).split(":", 1)
            linked = [item for item in plate_manifests.attempts_for_review(review_id) if item.attempt_id == attempt_id]
            if len(linked) != 1:
                raise ValueError("attempt is not linked to the review")
            resolved = resolve_candidate(adapter, violation_id, candidate_id, review_id=review_id)
            if resolved.get("attempt_id") != attempt_id:
                raise ValueError("attempt identity mismatch")
            candidate_ocr_raw = resolved.get("ocr_raw")
            evidence_crop_ref = (resolved.get("plate_crop_ref") or {}).get("stored_path")
            machine_provenance = manifest_provenance_for_verification(resolved)
        except Exception as exc:
            raise CaseReviewError("Machine candidate could not be validated for this case") from exc

    status = str(plate_status).strip()
    accepted = (accepted_plate_text or "").strip() or None
    clear_accepted = False

    if status == HUMAN_PLATE_STATUS_VERIFIED_READABLE:
        if not accepted:
            raise CaseReviewError(
                "verified_readable requires accepted_plate_text with all "
                "characters visibly readable"
            )
        # Reject partial markers.
        if any(ch in accepted for ch in ("*", "?", "_", "…")):
            raise CaseReviewError(
                "Partial or predicted plate text cannot establish identity"
            )
    elif status in NON_IDENTITY_PLATE_STATUSES:
        # Identity must not be established from unclear/absent plates.
        if status in (HUMAN_PLATE_STATUS_UNCLEAR, HUMAN_PLATE_STATUS_NOT_VISIBLE):
            accepted = None
            clear_accepted = True
        elif status != HUMAN_PLATE_STATUS_CANDIDATE_AWAITING_VERIFICATION:
            accepted = None
    else:
        raise CaseReviewError(f"Unsupported plate_status: {status}")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    # Mirror onto legacy violations.plate_* columns using the historical CHECK
    # constraint vocabulary (not_attempted / unreadable / recognized).
    legacy_status = "unreadable"
    legacy_text = None
    if status == HUMAN_PLATE_STATUS_VERIFIED_READABLE:
        legacy_status = "recognized"
        legacy_text = accepted
    elif status == HUMAN_PLATE_STATUS_CANDIDATE_AWAITING_VERIFICATION and accepted:
        legacy_status = "unreadable"
        legacy_text = None

    prior = adapter.get_plate_verification(violation_id)
    prior_snapshot = None
    prior_machine_provenance: dict[str, Any] = {}
    if prior is not None:
        prior_snapshot = {
            "plate_status": prior.get("plate_status"),
            "accepted_plate_text": prior.get("accepted_plate_text"),
            "ocr_raw": prior.get("ocr_raw"),
            "verified_by": prior.get("verified_by"),
            "verified_at": prior.get("verified_at"),
            "evidence_crop_ref": (prior.get("processing_diagnostics_json") or ""),
        }
        # Retain machine provenance recorded by an earlier confirmation. A
        # correction that does not name a candidate must not erase it.
        prior_machine_provenance = _machine_provenance_from_diagnostics(
            prior.get("processing_diagnostics_json")
        )

    diagnostics: dict[str, Any] = {
        "candidate_reference": candidate_reference,
        "actor_user_id": actor_user_id,
    }
    diagnostics.update(prior_machine_provenance)
    diagnostics.update(dict(machine_provenance or {}))

    apply = getattr(adapter, "apply_plate_verification", None)
    if apply is None:
        raise CaseReviewError(
            "Adapter lacks apply_plate_verification; atomic plate write required"
        )
    record_id = apply(
        violation_id,
        review_id=review_id,
        ocr_raw=candidate_ocr_raw,
        accepted_plate_text=accepted,
        clear_accepted_identity=clear_accepted,
        plate_status=status,
        ocr_confidence=ocr_confidence,
        verified_by=actor_user_id,
        verified_at=now,
        evidence_crop_ref=evidence_crop_ref,
        processing_diagnostics=diagnostics,
        legacy_plate_text=legacy_text,
        legacy_plate_status=legacy_status,
        audit_detail={
            "plate_status": status,
            "accepted_plate_text": accepted,
            "candidate_reference": candidate_reference,
            "evidence_crop_ref": evidence_crop_ref,
            "previous": prior_snapshot,
        },
        actor_user_id=actor_user_id,
    )
    return {
        "plate_verification_id": record_id,
        "plate_status": status,
        "accepted_plate_text": accepted,
        "verified_by": actor_user_id,
        "verified_at": now,
    }


def _machine_provenance_from_diagnostics(raw: Any) -> dict[str, Any]:
    """Recover previously stored ``machine_*`` provenance from a diagnostics blob."""
    if not raw:
        return {}
    try:
        data = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: value for key, value in data.items() if key.startswith("machine_")}


# ---------------------------------------------------------------------------
# Event-time persistence
# ---------------------------------------------------------------------------


def persist_event_time_review(
    adapter: Any,
    violation_id: int,
    actor_user_id: int,
    *,
    claims: list[EventTimeClaim] | None = None,
    user_entry_raw: str | None = None,
    user_entry_timezone: str | None = None,
    correction_raw: str | None = None,
    video_relative_sec: float | None = None,
    prior_result: EventTimeResult | None = None,
) -> dict[str, Any]:
    """Resolve and persist event-time with provenance under authorization."""
    if not adapter.can_confirm_event_time(actor_user_id):
        raise PermissionError(
            f"User {actor_user_id} lacks confirm_event_time authority"
        )
    viol = adapter.get_violation(violation_id)
    if viol is None:
        raise CaseReviewError(f"Violation {violation_id} not found")
    if viol.get("status") == "dismissed":
        raise CaseReviewError(
            f"Violation {violation_id} is dismissed; cannot confirm event time"
        )

    confirmed_at = datetime.now(timezone.utc)
    loaded_prior_meta: dict[str, Any] | None = None
    applied_correction = bool(correction_raw)

    # Correction-only / reconfirm paths must load the latest persisted snapshot
    # before rejecting the request. Never invent prior confirmation using the
    # current actor/time.
    if prior_result is None and not claims and not user_entry_raw:
        loaded = _load_prior_event_time_result(adapter, violation_id)
        if loaded is not None:
            prior_result, loaded_prior_meta = loaded
        elif correction_raw:
            # No prior snapshot: treat correction as an explicit new user entry
            # with truthful provenance (not a fabricated prior confirmation).
            user_entry_raw = correction_raw
            correction_raw = None
            applied_correction = False

    resolved: EventTimeResult
    if claims:
        resolved = resolve_event_time(claims)
    elif user_entry_raw:
        resolved = from_user_entry(
            raw_text=user_entry_raw,
            source_timezone=user_entry_timezone,
            video_relative_sec=video_relative_sec,
        )
    elif prior_result is not None:
        resolved = prior_result
    else:
        raise CaseReviewError("No event-time claims or user entry supplied")

    if prior_result is not None and not claims and not user_entry_raw:
        resolved = prior_result

    if correction_raw:
        correction = from_user_entry(
            raw_text=correction_raw,
            source_timezone=user_entry_timezone,
            video_relative_sec=video_relative_sec,
        )
        if correction.candidate is None:
            raise CaseReviewError("Correction value is not a usable event time")
        result = helper_confirm_event_time(
            resolved,
            confirmed_by=str(actor_user_id),
            confirmed_at=confirmed_at,
            corrected_instant=correction.candidate,
        )
    else:
        result = helper_confirm_event_time(
            resolved,
            confirmed_by=str(actor_user_id),
            confirmed_at=confirmed_at,
        )

    def _instant_iso(inst) -> str | None:
        if inst is None:
            return None
        return inst.value.isoformat()

    instant = usable_event_instant(result)
    effective = None
    if result.confirmed_event_time is not None:
        effective = _instant_iso(result.confirmed_event_time)
    elif instant is not None:
        effective = instant.isoformat()

    source = (
        result.original_source.value
        if result.original_source is not None
        else EventTimeSource.USER_CONFIRMATION.value
    )
    if loaded_prior_meta and loaded_prior_meta.get("original_source"):
        source = str(loaded_prior_meta["original_source"])

    persist_source = source
    if persist_source not in (
        "cctv_timestamp",
        "video_metadata",
        "user_entry",
        "user_confirmation",
    ):
        persist_source = "user_confirmation"

    contributing = [
        {
            "source": c.source.value if hasattr(c.source, "value") else str(c.source),
            "raw_text": getattr(c, "raw_text", None),
            "usability": None,
        }
        for c in (result.contributing_claims or ())
    ]
    if not contributing and loaded_prior_meta:
        contributing = list(loaded_prior_meta.get("contributing_claims") or [])

    conflicts = [
        {
            "source": c.source.value if hasattr(c.source, "value") else str(c.source),
            "raw_text": getattr(c, "raw_text", None),
        }
        for c in (result.conflict_claims or ())
    ]
    if not conflicts and loaded_prior_meta:
        conflicts = list(loaded_prior_meta.get("conflict_claims") or [])

    previous = None
    if result.previous is not None and result.previous.confirmed_event_time is not None:
        previous = _instant_iso(result.previous.confirmed_event_time)
    elif (
        applied_correction
        and result.previous is not None
        and result.previous.candidate is not None
    ):
        # In-request correction against an unconfirmed candidate (e.g. user entry
        # + correction in one call) — retain the pre-correction candidate.
        previous = _instant_iso(result.previous.candidate)
    elif loaded_prior_meta and loaded_prior_meta.get("effective_confirmed"):
        previous = str(loaded_prior_meta["effective_confirmed"])

    original_confirmed_by = None
    original_confirmed_at = None
    if loaded_prior_meta:
        original_confirmed_by = loaded_prior_meta.get("original_confirmed_by")
        original_confirmed_at = loaded_prior_meta.get("original_confirmed_at")
    elif prior_result is not None and prior_result.confirmation is not None:
        original_confirmed_by = prior_result.confirmation.confirmed_by
        original_confirmed_at = prior_result.confirmation.confirmed_at.isoformat()

    # Preserve the first recorded candidate across every correction. Never
    # overwrite it with a later effective confirmed time.
    if loaded_prior_meta and loaded_prior_meta.get("original_candidate"):
        original_candidate_iso = str(loaded_prior_meta["original_candidate"])
    elif loaded_prior_meta and loaded_prior_meta.get("original_candidate_missing"):
        original_candidate_iso = None
    else:
        original_candidate_iso = _instant_iso(result.candidate)

    detail = {
        "candidate": original_candidate_iso,
        "original_candidate": original_candidate_iso,
        "original_source": source,
        "contributing_claims": contributing,
        "conflict_claims": conflicts,
        "effective_confirmed": effective,
        "usability": result.usability.value if result.usability else None,
        "video_relative_sec": (
            video_relative_sec
            if video_relative_sec is not None
            else (
                result.video_relative_sec
                if result.video_relative_sec is not None
                else (loaded_prior_meta or {}).get("video_relative_sec")
            )
        ),
        "previous_confirmed": previous,
        "timestamp_ocr_available": False,
        "original_confirmed_by": original_confirmed_by,
        "original_confirmed_at": original_confirmed_at,
        "correction_by": str(actor_user_id) if correction_raw else None,
        "correction_at": confirmed_at.isoformat() if correction_raw else None,
        "confirmed_by": str(actor_user_id),
        "confirmed_at": confirmed_at.isoformat(),
        "timezone_offset": (
            (result.confirmed_event_time.offset_label if result.confirmed_event_time else None)
            or (result.candidate.offset_label if result.candidate else None)
            or (loaded_prior_meta or {}).get("timezone_offset")
        ),
    }

    if effective is None:
        adapter.record_case_action(
            violation_id,
            adapter.ACTION_EVENT_TIME_CONFIRMED,
            detail={**detail, "persisted_effective": False},
            actor_user_id=actor_user_id,
        )
        return {
            "persisted": False,
            "usability": detail["usability"],
            "detail": detail,
        }

    offset_label = ""
    if result.confirmed_event_time is not None:
        offset_label = result.confirmed_event_time.offset_label or ""
    elif result.candidate is not None:
        offset_label = result.candidate.offset_label or ""

    adapter.record_event_time(
        violation_id,
        effective,
        persist_source,
        confirmed_by=actor_user_id,
        original_metadata=json_dumps_safe(detail),
        video_timestamp_sec=detail.get("video_relative_sec"),
        timezone_offset=offset_label,
    )
    return {
        "persisted": True,
        "effective_confirmed": effective,
        "usability": detail["usability"],
        "detail": detail,
        "confirmed_by": actor_user_id,
        "confirmed_at": confirmed_at.strftime("%Y-%m-%d %H:%M:%S"),
    }


def _load_prior_event_time_result(
    adapter: Any,
    violation_id: int,
) -> tuple[EventTimeResult, dict[str, Any]] | None:
    """Reconstruct the latest persisted event-time snapshot with provenance.

    Loads the original candidate separately from the latest effective confirmed
    time. Does not invent an original candidate or attach UTC to unknown
    historical timestamps.
    """
    from core.event_time import ReviewConfirmation

    snap = None
    if hasattr(adapter, "get_event_time_snapshot"):
        snap = adapter.get_event_time_snapshot(violation_id)
    if not snap:
        prior_iso = adapter.get_confirmed_event_time(violation_id)
        if not prior_iso:
            return None
        snap = {
            "event_time": prior_iso,
            "source": "user_confirmation",
            "original_metadata": {},
            "confirmed_by": None,
            "confirmed_at": None,
            "video_timestamp_sec": None,
        }

    meta = snap.get("original_metadata") or {}
    if isinstance(meta, str):
        try:
            import json

            meta = json.loads(meta)
        except Exception:
            meta = {}

    effective_iso = (
        snap.get("event_time")
        or meta.get("effective_confirmed")
        or meta.get("event_time")
    )
    if not effective_iso:
        return None

    effective_loaded = from_user_entry(raw_text=str(effective_iso))
    if effective_loaded.candidate is None:
        return None

    # Original candidate is a distinct field — never invent from effective time.
    original_iso = meta.get("original_candidate")
    if original_iso is None and meta.get("candidate") and not meta.get("previous_confirmed"):
        # Legacy first confirmation: candidate field was the original.
        original_iso = meta.get("candidate")
    original_candidate = None
    original_candidate_missing = False
    if original_iso:
        orig_loaded = from_user_entry(raw_text=str(original_iso))
        original_candidate = orig_loaded.candidate
        if original_candidate is None:
            original_candidate_missing = True
    else:
        original_candidate_missing = True

    source_raw = (
        meta.get("original_source")
        or snap.get("source")
        or EventTimeSource.USER_CONFIRMATION.value
    )
    try:
        original_source = EventTimeSource(str(source_raw))
    except ValueError:
        original_source = EventTimeSource.USER_CONFIRMATION

    contributing: list[EventTimeClaim] = []
    for c in meta.get("contributing_claims") or []:
        try:
            src = EventTimeSource(str(c.get("source")))
        except ValueError:
            continue
        contributing.append(
            EventTimeClaim(source=src, raw_text=c.get("raw_text"))
        )

    conflict: list[EventTimeClaim] = []
    for c in meta.get("conflict_claims") or []:
        try:
            src = EventTimeSource(str(c.get("source")))
        except ValueError:
            continue
        conflict.append(EventTimeClaim(source=src, raw_text=c.get("raw_text")))

    orig_by = meta.get("original_confirmed_by") or meta.get("confirmed_by") or snap.get(
        "confirmed_by"
    )
    orig_at_raw = (
        meta.get("original_confirmed_at")
        or meta.get("confirmed_at")
        or snap.get("confirmed_at")
    )
    confirmation = None
    if orig_by is not None and orig_at_raw:
        try:
            if isinstance(orig_at_raw, datetime):
                at = orig_at_raw
            else:
                text = str(orig_at_raw).replace(" ", "T")
                at = datetime.fromisoformat(text)
            if at.tzinfo is None:
                # Do not invent a timezone for unknown historical review times.
                confirmation = None
            else:
                confirmation = ReviewConfirmation(
                    confirmed_by=str(orig_by),
                    confirmed_at=at,
                )
        except (TypeError, ValueError):
            confirmation = None

    prior = EventTimeResult(
        usability=effective_loaded.usability,
        candidate=original_candidate,
        original_source=original_source,
        confirmed_event_time=effective_loaded.candidate,
        confirmation=confirmation,
        video_relative_sec=(
            meta.get("video_relative_sec")
            if meta.get("video_relative_sec") is not None
            else snap.get("video_timestamp_sec")
        ),
        contributing_claims=tuple(contributing),
        conflict_claims=tuple(conflict),
        reason=effective_loaded.reason,
        diagnostics={
            "loaded_from_persistence": True,
            "original_candidate_missing": original_candidate_missing,
        },
    )
    prior_meta = {
        "effective_confirmed": str(effective_iso),
        "original_candidate": (
            original_candidate.value.isoformat() if original_candidate else None
        ),
        "original_candidate_missing": original_candidate_missing,
        "original_source": original_source.value,
        "contributing_claims": list(meta.get("contributing_claims") or []),
        "conflict_claims": list(meta.get("conflict_claims") or []),
        "video_relative_sec": prior.video_relative_sec,
        "timezone_offset": meta.get("timezone_offset") or snap.get("timezone_offset"),
        "original_confirmed_by": confirmation.confirmed_by if confirmation else None,
        "original_confirmed_at": (
            confirmation.confirmed_at.isoformat() if confirmation else None
        ),
        "provenance_missing": confirmation is None,
    }
    return prior, prior_meta


def json_dumps_safe(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, default=str)
