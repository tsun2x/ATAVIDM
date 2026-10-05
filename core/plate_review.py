"""Read-side helpers for experimental machine plate results.

Everything here is **additive**. It adds optional ``plate_machine`` fields to
existing review/case payloads and never replaces or renames an existing key,
and it never writes to the database.

Authority boundary
------------------
* Candidate text, crop references, and provenance are resolved from the
  server-owned manifests in :mod:`core.plate_manifest`. A client-supplied
  candidate id is only ever used as a *lookup key*; the returned OCR text,
  confidence, and crop reference always come from the manifest.
* This module never decides eligibility for human confirmation. It reports
  whether a candidate is *machine-eligible*, and the human decision remains an
  explicit admin action through the existing plate endpoint.
"""

from __future__ import annotations

import logging
from typing import Any

from core import plate_manifest as manifests
from core.plate_jobs import AMBIGUOUS_OUTCOMES, PlateAttemptOutcome

logger = logging.getLogger(__name__)


class PlateReviewError(ValueError):
    """A machine-result reference could not be resolved safely."""


# ----------------------------------------------------------------------
# Review -> attempt linkage
# ----------------------------------------------------------------------


def review_ids_for_violation(adapter: Any, violation_id: int) -> list[int]:
    """Server-derived review ids that produced a case.

    Uses the existing ``case_action_events.review_id`` column, so no new table,
    column, or migration is involved. Permitted-result removals and video
    deletion can drop the review row; this list is then simply shorter.
    """
    getter = getattr(adapter, "get_case_actions", None)
    if getter is None:
        return []
    try:
        rows = getter(int(violation_id)) or []
    except Exception:  # noqa: BLE001 - a missing audit trail is not fatal
        logger.exception("case action lookup failed for violation %s", violation_id)
        return []
    out: list[int] = []
    for row in rows:
        value = row.get("review_id") if isinstance(row, dict) else None
        if value is None:
            continue
        try:
            out.append(int(value))
        except (TypeError, ValueError):
            continue
    return out


def current_attempt_for_review(review_id: int | None) -> manifests.LoadedManifest | None:
    found = manifests.attempts_for_review(review_id)
    return found[0] if found else None


def review_ids_for_case(adapter: Any, violation_id: int, review_id: int | None = None) -> list[int]:
    ids = list(review_ids_for_violation(adapter, violation_id))
    if review_id is not None:
        try:
            requested = int(review_id)
        except (TypeError, ValueError):
            return []
        # A client filter can narrow server-owned authority, never extend it.
        if requested not in ids:
            return []
        ids = [requested]
    seen: set[int] = set()
    ordered: list[int] = []
    for value in ids:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


# ----------------------------------------------------------------------
# Serialization
# ----------------------------------------------------------------------


def _attempt_state(loaded: manifests.LoadedManifest | None, live_state: str | None) -> str:
    if live_state:
        return live_state
    if loaded is None:
        return "none"
    return str((loaded.data or {}).get("outcome") or PlateAttemptOutcome.NO_CANDIDATE_DETECTED.value)


def _machine_eligible(outcome: str, association_uncertain: bool, has_candidate: bool) -> bool:
    """Machine-side eligibility only. Never a human decision."""
    if association_uncertain:
        return False
    if not has_candidate:
        return False
    return outcome == PlateAttemptOutcome.CANDIDATE_FOUND.value


def machine_result_payload(
    loaded: manifests.LoadedManifest | None,
    *,
    live_state: str | None = None,
    case_id: int | None = None,
) -> dict[str, Any]:
    """Serialize one attempt into the additive ``plate_machine`` block."""
    base: dict[str, Any] = {
        "experimental": True,
        "human_review_required": True,
        "attempt_id": None,
        "review_id": None,
        "case_id": case_id,
        "manifest_status": "missing" if loaded is None else loaded.status,
        "state": _attempt_state(loaded, live_state),
        "outcome_is_ambiguous": True,
        "eligible_for_confirmation": False,
        "association_uncertain": False,
        "primary_candidate_id": None,
        "candidates": [],
        "quality_rejections": [],
        "calls": None,
        "truncated": None,
        "budget": None,
        "artifact": None,
        "evidence_missing": False,
        "notes": (
            "Experimental local OCR. Machine candidate only: not a verified plate, "
            "not an offender identity, and not a violation decision."
        ),
    }
    if loaded is None or not loaded.ok or loaded.data is None:
        if loaded is not None and loaded.status != manifests.MANIFEST_STATUS_OK:
            base["detail"] = loaded.detail or loaded.status
        return base

    data = loaded.data
    outcome = str(data.get("outcome") or "")
    candidates = [c for c in (data.get("candidates") or []) if isinstance(c, dict)]
    association_uncertain = bool(data.get("association_uncertain"))
    evidence_missing = False

    serialized_candidates: list[dict[str, Any]] = []
    for candidate in candidates:
        crop_ref = candidate.get("plate_crop_ref") or {}
        crop_name = crop_ref.get("name") if isinstance(crop_ref, dict) else None
        available = manifests.resolve_crop_ref(
            crop_ref.get("stored_path") if isinstance(crop_ref, dict) else None
        )
        if crop_name and available is None:
            evidence_missing = True
        sample = _sample_for(data, str(candidate.get("sample_id") or ""))
        serialized_candidates.append(
            {
                "candidate_id": candidate.get("candidate_id"),
                "ocr_raw": candidate.get("ocr_raw"),
                "ocr_display_normalized": candidate.get("ocr_display_normalized"),
                "detection_label": candidate.get("detection_label"),
                "detection_confidence": candidate.get("detection_confidence"),
                "ocr_scalar_score": candidate.get("ocr_scalar_score"),
                "ocr_char_scores": candidate.get("ocr_char_scores"),
                "ocr_score_semantics": candidate.get("ocr_score_semantics"),
                "plate_box_in_frame": candidate.get("plate_box_in_frame"),
                "plate_crop_name": crop_name,
                "plate_crop_available": bool(available),
                "vehicle_box_in_frame": sample.get("vehicle_box_frame_px"),
                "frame_number": sample.get("frame_number"),
                "timestamp_sec": sample.get("timestamp_sec"),
                "frame_width": sample.get("frame_width"),
                "frame_height": sample.get("frame_height"),
                "track_id": (data.get("source") or {}).get("track_id"),
                "track_identity_epoch": (data.get("source") or {}).get("track_identity_epoch"),
                "violation_type": (data.get("source") or {}).get("violation_type"),
                "run_key": (data.get("source") or {}).get("run_key"),
                "live_session_id": (data.get("source") or {}).get("live_session_id"),
            }
        )

    provenance = data.get("machine_provenance") or {}
    detector = provenance.get("detector_artifact") or {}
    ocr = provenance.get("ocr_artifact") or {}

    base.update(
        {
            "attempt_id": data.get("attempt_id"),
            "review_id": data.get("review_id"),
            "case_id": data.get("case_id") if data.get("case_id") is not None else case_id,
            "manifest_status": loaded.status,
            "state": outcome or "none",
            "outcome_is_ambiguous": bool(data.get("outcome_is_ambiguous"))
            or outcome in {o.value for o in AMBIGUOUS_OUTCOMES},
            "eligible_for_confirmation": _machine_eligible(outcome, association_uncertain, bool(candidates)),
            "association_uncertain": association_uncertain,
            "primary_candidate_id": data.get("primary_candidate_id"),
            "candidates": serialized_candidates,
            "quality_rejections": data.get("quality_rejections") or [],
            "calls": data.get("calls"),
            "truncated": data.get("truncated"),
            "budget": data.get("budget"),
            "evidence_missing": evidence_missing,
            "source": data.get("source"),
            "trigger": data.get("trigger"),
            "detections_seen": data.get("detections_seen"),
            "artifact": {
                "detector_sha256": detector.get("sha256"),
                "ocr_sha256": ocr.get("sha256"),
                "ocr_config_sha256": ocr.get("ocr_config_sha256"),
                "provider_requested": provenance.get("requested_provider"),
                "provider_active": provenance.get("active_providers"),
                "runtime": provenance.get("runtime"),
                "color_mode": ocr.get("color_mode"),
            },
        }
    )
    return base


def _sample_for(data: dict[str, Any], sample_id: str) -> dict[str, Any]:
    for sample in data.get("samples") or []:
        if isinstance(sample, dict) and str(sample.get("sample_id")) == sample_id:
            return sample
    return {}


def machine_result_for_review(review_id: int | None) -> dict[str, Any]:
    """Machine block for one review observation."""
    live_state = None
    try:
        from core.plate_runtime import worker_state_for

        live_state = worker_state_for(review_id)
    except Exception:  # noqa: BLE001 - status must never break a page render
        logger.debug("plate worker state unavailable", exc_info=True)
    loaded = current_attempt_for_review(review_id)
    return machine_result_payload(loaded, live_state=live_state)


def machine_result_for_violation(adapter: Any, violation_id: int) -> dict[str, Any]:
    """Machine block for a materialized case, resolved through existing links."""
    loaded = None
    for review_id in review_ids_for_violation(adapter, violation_id):
        loaded = current_attempt_for_review(review_id)
        if loaded is not None:
            break
    live_state = None
    try:
        from core.plate_runtime import worker_state_for

        live_state = worker_state_for(review_ids_for_violation(adapter, violation_id) or [None])[0] \
            if loaded is None else None
    except Exception:  # noqa: BLE001
        logger.debug("plate worker state unavailable", exc_info=True)
    return machine_result_payload(loaded, live_state=live_state, case_id=int(violation_id))


# ----------------------------------------------------------------------
# Candidate resolution (server-owned authority)
# ----------------------------------------------------------------------


def resolve_candidate(
    adapter: Any,
    violation_id: int,
    candidate_id: str,
    *,
    review_id: int | None = None,
) -> dict[str, Any]:
    """Resolve a machine candidate for a case, rejecting cross-case references.

    A ``candidate_id`` is only a lookup key. The returned OCR text, crop
    reference, and provenance always come from the server-owned manifest that
    belongs to *this* case.
    """
    text = str(candidate_id or "").strip()
    if not text:
        raise PlateReviewError("candidate_id is required")

    allowed_review_ids = set(review_ids_for_case(adapter, violation_id, review_id))
    if not allowed_review_ids:
        raise PlateReviewError("No machine attempt is linked to this case")

    found_in_case = False
    for rid in sorted(allowed_review_ids):
        for loaded in manifests.attempts_for_review(rid):
            data = loaded.data or {}
            # Validate both sides of the review -> manifest link. Manifest
            # contents and an index pointer are not themselves case authority.
            if loaded.status != manifests.MANIFEST_STATUS_OK:
                continue
            if data.get("review_id") is None or int(data["review_id"]) != rid:
                continue
            manifest_case_id = data.get("case_id")
            if manifest_case_id not in (None, int(violation_id)):
                continue
            # A null manifest case id is valid for an attempt created while
            # its review row was still pending; the trusted action link above
            # must still connect that review to this case.
            for candidate in data.get("candidates") or []:
                if not isinstance(candidate, dict):
                    continue
                if str(candidate.get("candidate_id")) != text:
                    continue
                found_in_case = True
                outcome = str(data.get("outcome") or "")
                uncertain = data.get("association_uncertain") is True or outcome == PlateAttemptOutcome.ASSOCIATION_UNCERTAIN.value
                if uncertain or outcome != PlateAttemptOutcome.CANDIDATE_FOUND.value:
                    raise PlateReviewError("machine candidate is not eligible for confirmation")
                sample = _sample_for(data, str(candidate.get("sample_id") or ""))
                crop_ref = candidate.get("plate_crop_ref")
                if not sample or not isinstance(crop_ref, dict) or manifests.resolve_crop_ref(crop_ref.get("stored_path")) is None:
                    raise PlateReviewError("required machine evidence is unavailable")
                return {
                    "attempt_id": loaded.attempt_id,
                    "review_id": data.get("review_id"),
                    "candidate_id": text,
                    "ocr_raw": candidate.get("ocr_raw"),
                    "ocr_display_normalized": candidate.get("ocr_display_normalized"),
                    "detection_confidence": candidate.get("detection_confidence"),
                    "ocr_scalar_score": candidate.get("ocr_scalar_score"),
                    "ocr_char_scores": candidate.get("ocr_char_scores"),
                    "ocr_score_semantics": candidate.get("ocr_score_semantics"),
                    "plate_crop_ref": candidate.get("plate_crop_ref"),
                    "association_uncertain": uncertain,
                    "outcome": data.get("outcome"),
                    "machine_provenance": data.get("machine_provenance"),
                    "sample": sample,
                }

    if not found_in_case:
        # Distinguish "unknown id" from "belongs to another case" only as a
        # generic refusal: the response never reveals another case's data.
        raise PlateReviewError("candidate_id is not a machine candidate for this case")
    raise PlateReviewError("candidate_id is not a machine candidate for this case")


def resolve_review_candidate(review_id: int, candidate_id: str) -> dict[str, Any]:
    """Resolve a candidate while its observation is still pending.

    The review id must be present in the manifest itself and in the server
    generated review index. Candidate metadata is always sourced from that
    manifest; the caller's candidate id is only a lookup key.
    """
    text = str(candidate_id or "").strip()
    if not text:
        raise PlateReviewError("candidate_id is required")
    for loaded in manifests.attempts_for_review(int(review_id)):
        data = loaded.data or {}
        if loaded.status != manifests.MANIFEST_STATUS_OK:
            continue
        if data.get("review_id") is None or int(data["review_id"]) != int(review_id):
            continue
        for candidate in data.get("candidates") or []:
            if not isinstance(candidate, dict) or str(candidate.get("candidate_id")) != text:
                continue
            outcome = str(data.get("outcome") or "")
            uncertain = data.get("association_uncertain") is True or outcome == PlateAttemptOutcome.ASSOCIATION_UNCERTAIN.value
            if uncertain or outcome != PlateAttemptOutcome.CANDIDATE_FOUND.value:
                raise PlateReviewError("machine candidate is not eligible for confirmation")
            sample = _sample_for(data, str(candidate.get("sample_id") or ""))
            crop_ref = candidate.get("plate_crop_ref")
            if not sample or not isinstance(crop_ref, dict) or manifests.resolve_crop_ref(crop_ref.get("stored_path")) is None:
                raise PlateReviewError("required machine evidence is unavailable")
            resolved = {
                "attempt_id": loaded.attempt_id,
                "review_id": int(review_id),
                "candidate_id": text,
                "ocr_raw": candidate.get("ocr_raw"),
                "ocr_scalar_score": candidate.get("ocr_scalar_score"),
                "detection_confidence": candidate.get("detection_confidence"),
                "plate_crop_ref": crop_ref,
                "association_uncertain": uncertain,
                "outcome": outcome,
                "machine_provenance": data.get("machine_provenance"),
                "sample": sample,
            }
            return resolved
    raise PlateReviewError("candidate_id is not a machine candidate for this review")


def manifest_provenance_for_verification(resolved: dict[str, Any]) -> dict[str, Any]:
    """Compact machine provenance stored alongside a human verification."""
    provenance = resolved.get("machine_provenance") or {}
    detector = provenance.get("detector_artifact") or {}
    ocr = provenance.get("ocr_artifact") or {}
    return {
        "machine_attempt_id": resolved.get("attempt_id"),
        "machine_review_id": resolved.get("review_id"),
        "machine_candidate_id": resolved.get("candidate_id"),
        "machine_outcome": resolved.get("outcome"),
        "machine_ocr_raw": resolved.get("ocr_raw"),
        "machine_detection_confidence": resolved.get("detection_confidence"),
        "machine_ocr_scalar_score": resolved.get("ocr_scalar_score"),
        "machine_ocr_char_scores": resolved.get("ocr_char_scores"),
        "machine_ocr_score_semantics": resolved.get("ocr_score_semantics"),
        "machine_plate_crop_ref": (resolved.get("plate_crop_ref") or {}).get("stored_path"),
        "machine_plate_crop_sha256": (resolved.get("plate_crop_ref") or {}).get("sha256"),
        "machine_detector_sha256": detector.get("sha256"),
        "machine_ocr_sha256": ocr.get("sha256"),
        "machine_ocr_config_sha256": ocr.get("ocr_config_sha256"),
        "machine_provider_requested": provenance.get("requested_provider"),
        "machine_provider_active": provenance.get("active_providers"),
        "machine_runtime": provenance.get("runtime"),
        "machine_association_uncertain": resolved.get("association_uncertain"),
        "machine_evidence": {
            "frame_number": (resolved.get("sample") or {}).get("frame_number"),
            "timestamp_sec": (resolved.get("sample") or {}).get("timestamp_sec"),
            "vehicle_box_frame_px": (resolved.get("sample") or {}).get("vehicle_box_frame_px"),
            "track_id": (resolved.get("machine_provenance") or {}).get("track_id"),
        },
    }
