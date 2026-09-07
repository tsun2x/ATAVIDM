"""Legal-policy mapping layer for TAVIDM.

Separates observed canonical behavior from official legal classification.

This module provides:
  - LegalStatus: verified | partially_verified | unverified | flag_only
  - Behavior-to-official-category mapping (proposed/verified).
  - Fused parking/obstruction grouping (one grouped case, retained behaviors).
  - Recurrence-eligibility gating based on verified plates, verified legal
    mappings, and confirmed cases.
  - Safe fail-closed behavior for invalid or unestablished mappings.

Legal source material and mapping provenance are stored in
``config/violation_legal_mappings.json``. Per contract, authoritative legal
sources were inaccessible during this implementation phase, so most mappings
remain ``unverified`` or ``flag_only`` with explicit documentation of what is
unresolved.

Design rules (NEVER VIOLATE):
  - Canonical identifiers are never altered by legal mapping.
  - Legal status never implies detector-readiness.
  - Invalid/unverified mappings fail safely without invented citations/penalties.
  - Recurrence eligibility requires verified plate + verified legal mapping +
    confirmed eligible case outcome.
"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.detection_config import CANONICAL_VIOLATIONS

# ---------------------------------------------------------------------------
# Configuration loading
# ---------------------------------------------------------------------------

LEGAL_MAPPINGS_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "violation_legal_mappings.json"
)


@dataclass(frozen=True)
class BehaviorMapping:
    """A single canonical-rule → legal-mapping entry.

    ``official_category_proposed`` is the proposed grouping; it is only an
    official legal category when ``legal_status`` is ``verified`` or
    ``partially_verified`` with the category element among verified elements.
    """

    canonical_rule: str
    official_category_proposed: str | None
    legal_status: str
    verified_elements: tuple[str, ...]
    unresolved_elements: tuple[str, ...]
    behavior_details: tuple[str, ...]
    provision_reference: str | None
    penalty_schedule: dict[str, Any] | None
    source_url: str | None
    mapping_version: str
    is_grouped_with: tuple[str, ...]
    notes: str | None


class LegalStatus(str, enum.Enum):
    """Legal mapping status for a canonical violation.

    Operational meanings:
      - ``verified``: behavior-to-category mapping, applicable provision, and
        reference penalty checked against authoritative sources; approved for
        active use.
      - ``partially_verified``: some elements established, others unresolved.
        ``verified_elements`` / ``unresolved_elements`` identify which.
      - ``unverified``: legal applicability not established; collected for
        review only.
      - ``flag_only``: evidence collection and authorized review only; no
        official legal classification or reference penalty enabled.
    """

    VERIFIED = "verified"
    PARTIALLY_VERIFIED = "partially_verified"
    UNVERIFIED = "unverified"
    FLAG_ONLY = "flag_only"

    @classmethod
    def values(cls) -> frozenset[str]:
        return frozenset(member.value for member in cls)


# ---------------------------------------------------------------------------
# Mapping registry (loaded once, cached)
# ---------------------------------------------------------------------------

_mappings_cache: dict[str, BehaviorMapping] | None = None
_policy_version_cache: str | None = None


def _load_mappings() -> tuple[str, dict[str, BehaviorMapping]]:
    """Load and parse ``violation_legal_mappings.json``.

    Returns ``(version, mappings_dict)``. Failures fall back to an empty
    mapping set — never raises, because legal config absence must not crash
    the application. Callers receive ``LegalStatus.UNVERIFIED`` for any rule
    not present in the config.
    """
    global _mappings_cache, _policy_version_cache
    if _mappings_cache is not None:
        return _policy_version_cache or "unknown", _mappings_cache  # type: ignore[return-value]

    mappings: dict[str, BehaviorMapping] = {}
    version = "unknown"
    if LEGAL_MAPPINGS_PATH.exists():
        try:
            raw = json.loads(LEGAL_MAPPINGS_PATH.read_text(encoding="utf-8"))
            version = raw.get("version", "unknown")
            for rule_name, entry in raw.get("mappings", {}).items():
                mappings[rule_name] = BehaviorMapping(
                    canonical_rule=rule_name,
                    official_category_proposed=entry.get("official_category_proposed"),
                    legal_status=entry.get("legal_status", LegalStatus.UNVERIFIED.value),
                    verified_elements=tuple(entry.get("verified_elements", [])),
                    unresolved_elements=tuple(entry.get("unresolved_elements", [])),
                    behavior_details=tuple(entry.get("behavior_details", [])),
                    provision_reference=entry.get("provision_reference"),
                    penalty_schedule=entry.get("penalty_schedule"),
                    source_url=entry.get("source_url"),
                    mapping_version=entry.get("mapping_version", version),
                    is_grouped_with=tuple(entry.get("is_grouped_with", [])),
                    notes=entry.get("notes"),
                )
        except (json.JSONDecodeError, OSError):
            # Config unreadable — fail safe to empty mappings (all unverified).
            mappings = {}
            version = "unknown"

    _mappings_cache = mappings
    _policy_version_cache = version
    return version, mappings


def get_policy_version() -> str:
    """Return the version of the active legal-policy configuration."""
    version, _ = _load_mappings()
    return version


def reset_policy_cache() -> None:
    """Clear the in-memory config cache (for tests / reload)."""
    global _mappings_cache, _policy_version_cache
    _mappings_cache = None
    _policy_version_cache = None


# ---------------------------------------------------------------------------
# Legal status lookups
# ---------------------------------------------------------------------------

def legal_status_for(canonical_rule: str) -> LegalStatus:
    """Return the legal status for a canonical violation.

    If the rule is unknown or not present in the legal config, returns
    ``unverified`` (fail-safe: never guess).
    """
    _, mappings = _load_mappings()
    entry = mappings.get(canonical_rule)
    if entry is None:
        return LegalStatus.UNVERIFIED
    try:
        return LegalStatus(entry.legal_status)
    except ValueError:
        return LegalStatus.UNVERIFIED


def legal_mapping_for(canonical_rule: str) -> BehaviorMapping | None:
    """Return the full behavior mapping for a canonical rule, or None."""
    _, mappings = _load_mappings()
    return mappings.get(canonical_rule)


def official_category_for(canonical_rule: str) -> str | None:
    """Return a *verified* official legal classification, or None.

    Unverified, partially_verified, and flag_only mappings never return an
    apparently approved classification through this interface. Use
    ``proposed_official_category_for`` for research/review wording.
    """
    return verified_official_category_for(canonical_rule)


def proposed_official_category_for(canonical_rule: str) -> str | None:
    """Return proposed official wording for research/review only.

    Returns ``None`` when no wording is proposed, the rule is unknown, or
    the mapping is ``flag_only`` (no active category wording).
    Callers must not treat the result as a verified legal classification.
    """
    mapping = legal_mapping_for(canonical_rule)
    if mapping is None:
        return None
    status = legal_status_for(canonical_rule)
    if status == LegalStatus.FLAG_ONLY:
        return None
    return mapping.official_category_proposed


def verified_official_category_for(canonical_rule: str) -> str | None:
    """Return the official category only when legal_status is ``verified``.

    Partially verified mappings are not treated as fully verified.
    """
    mapping = legal_mapping_for(canonical_rule)
    if mapping is None:
        return None
    if legal_status_for(canonical_rule) != LegalStatus.VERIFIED:
        return None
    return mapping.official_category_proposed


def reference_penalty_schedule_for(canonical_rule: str) -> dict[str, Any] | None:
    """Return a reference penalty schedule only for verified mappings.

    Unverified / partially_verified / flag_only never expose penalties.
    """
    mapping = legal_mapping_for(canonical_rule)
    if mapping is None:
        return None
    if legal_status_for(canonical_rule) != LegalStatus.VERIFIED:
        return None
    return mapping.penalty_schedule


def behavior_details_for(canonical_rule: str) -> tuple[str, ...]:
    """Return observed behavior detail strings for traceability."""
    mapping = legal_mapping_for(canonical_rule)
    if mapping is None:
        return ()
    return mapping.behavior_details


def is_recurrence_eligible_canonical(canonical_rule: str) -> bool:
    """Gate: can this canonical rule contribute to recurrence eligibility?

    Requires a fully verified official category. ``partially_verified``,
    ``flag_only``, and ``unverified`` mappings never qualify.
    """
    mapping = legal_mapping_for(canonical_rule)
    if mapping is None:
        return False
    if legal_status_for(canonical_rule) != LegalStatus.VERIFIED:
        return False
    return mapping.official_category_proposed is not None


# ---------------------------------------------------------------------------
# Fused parking/obstruction grouping
# ---------------------------------------------------------------------------

def fused_case_category(
    contributing_rules: tuple[str, ...],
) -> tuple[str | None, tuple[str, ...]]:
    """Determine the *proposed* official category for a fused case.

    When the same detected event qualifies as overlapping parking and
    obstruction, the proposed wording is ``Obstruction of Traffic Flow``.
    This helper does not assert verified legal status — callers must use
    ``verified_official_category_for`` / ``official_category_for`` before
    treating the wording as an approved classification.

    Returns ``(proposed_official_category, contributing_rules)``. Grouping
    uses existing run, source, track, and episode lifecycle to determine
    event identity — unrelated events sharing a plate or same video are
    never grouped here; that logic lives in the engine/persistence layer.
    """
    rule_set = set(contributing_rules)
    from core.detection_config import (
        VIOLATION_ILLEGAL_PARKING,
        VIOLATION_OBSTRUCTION,
    )

    if VIOLATION_ILLEGAL_PARKING in rule_set and VIOLATION_OBSTRUCTION in rule_set:
        category = proposed_official_category_for(VIOLATION_OBSTRUCTION)
        return category, tuple(sorted(rule_set))

    # No fusion case — return None and the contributing rules as-is.
    return None, tuple(contributing_rules)


def is_parking_obstruction_fusion(
    contributing_rules: tuple[str, ...] | list[str],
) -> bool:
    """True when the contributing rules are exactly Illegal Parking + Obstruction."""
    from core.detection_config import (
        VIOLATION_ILLEGAL_PARKING,
        VIOLATION_OBSTRUCTION,
    )
    rule_set = set(contributing_rules)
    return {VIOLATION_ILLEGAL_PARKING, VIOLATION_OBSTRUCTION} == rule_set


# ---------------------------------------------------------------------------
# Detector-readiness separation
# ---------------------------------------------------------------------------

def detector_readiness_for(canonical_rule: str) -> str:
    """Return the engine execution status, independent of legal status.

    Uses ``core.detection_config.violation_execution_status`` so that legal
    verification status never implies detector-readiness.
    """
    from core.detection_config import violation_execution_status

    return violation_execution_status(canonical_rule)


def legal_status_does_not_imply_detection(canonical_rule: str) -> bool:
    """Return True if legal verification is absent despite detector readiness.

    This documents the separation: a rule may be implemented (detector works)
    but legally unverified or flag-only.
    """
    legality = legal_status_for(canonical_rule)
    engine_status = detector_readiness_for(canonical_rule)
    if engine_status in ("implemented", "partial", "model_dependent"):
        # Even when detector-capable, legal status may be unverified/flag_only.
        return legality in (LegalStatus.UNVERIFIED, LegalStatus.FLAG_ONLY)
    return True
