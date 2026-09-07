"""Violation enable/disable configuration and catalog metadata.

Global violation toggles are persisted in ``system_settings`` under
``enabled_violations`` as a JSON list of canonical violation names.

Settings UI presents configuration-driven *grouped* categories (from legal
mappings) while persistence and runtime evaluation always use canonical
identifiers. Per-video and per-camera profiles are not part of the current
schema; toggles apply globally to uploaded-video processing and live streams.
"""

from __future__ import annotations

import json
from typing import Any

from core.detection_config import (
    CANONICAL_VIOLATIONS,
    DEFAULT_ENABLED_VIOLATIONS,
    ENABLED_VIOLATIONS_SETTING_KEY,
    IMPLEMENTED_VIOLATIONS,
    MODEL_DEPENDENT_VIOLATIONS,
    NEEDS_DECISION_VIOLATIONS,
    PARTIAL_VIOLATIONS,
    PLANNED_VIOLATIONS,
    TOGGLEABLE_VIOLATIONS,
    violation_execution_status,
)
from database import db


class ViolationConfigError(ValueError):
    """Raised when an enabled-violation payload is invalid."""


# Preferred display order for known proposed official groups.
_PREFERRED_GROUP_ORDER = (
    "Obstruction of Traffic Flow",
    "Disregarding Traffic Signals",
    "Violation of Truck Ban",
    "Unauthorized or Non-Use of Helmet by Motorcycle Riders",
    "Incomplete Accessories",
)


def validate_enabled_violations(names: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Return a tuple of canonical, toggleable violation names."""
    canonical = set(CANONICAL_VIOLATIONS)
    toggleable = set(TOGGLEABLE_VIOLATIONS)
    seen: set[str] = set()
    result: list[str] = []

    for name in names:
        if name not in canonical:
            raise ViolationConfigError(f"Unknown violation type: {name}")
        if name not in toggleable:
            raise ViolationConfigError(
                f"Violation '{name}' is planned and cannot be enabled yet."
            )
        if name in seen:
            continue
        seen.add(name)
        result.append(name)

    return tuple(result)


def load_enabled_violations() -> tuple[str, ...]:
    """Load the globally enabled violation set from ``system_settings``.

    Missing / unset configuration may use defaults. An explicitly persisted
    empty list ``[]`` means all rules are disabled and must not be replaced
    by defaults.
    """
    raw = db.get_setting(ENABLED_VIOLATIONS_SETTING_KEY)
    if raw is None or raw == "":
        return DEFAULT_ENABLED_VIOLATIONS
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return DEFAULT_ENABLED_VIOLATIONS
    if not isinstance(parsed, list):
        return DEFAULT_ENABLED_VIOLATIONS
    try:
        return validate_enabled_violations(parsed)
    except ViolationConfigError:
        return DEFAULT_ENABLED_VIOLATIONS


def save_enabled_violations(names: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Persist the globally enabled violation set."""
    validated = validate_enabled_violations(names)
    db.set_settings({ENABLED_VIOLATIONS_SETTING_KEY: json.dumps(list(validated))})
    return validated


def expand_group_selection_to_canonical(
    *,
    group_states: dict[str, str],
    standalone_enabled: list[str] | tuple[str, ...] | None = None,
    prior_enabled: list[str] | tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    """Expand UI group selections into a flat canonical enabled list.

    ``group_states`` maps group_id → ``\"on\"`` | ``\"off\"`` | ``\"mixed\"``.
    Mixed groups preserve each member's prior enabled state (legacy mixed
    settings are never silently normalized).
    """
    # Membership only — do not re-read live settings for expansion.
    membership = {
        g["group_id"]: list(g["toggleable_canonical_rules"])
        for g in violation_groups_for_ui(enabled=set())
    }
    enabled: list[str] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        if name in seen:
            return
        if name not in TOGGLEABLE_VIOLATIONS:
            return
        seen.add(name)
        enabled.append(name)

    prior = set(prior_enabled or ())
    for group_id, state in group_states.items():
        members = membership.get(group_id)
        if members is None:
            continue
        if state == "on":
            for name in members:
                _add(name)
        elif state == "off":
            continue
        elif state == "mixed":
            for name in members:
                if name in prior:
                    _add(name)
        else:
            raise ViolationConfigError(f"Unknown group state: {state}")

    for name in standalone_enabled or ():
        _add(name)

    return validate_enabled_violations(enabled)


def _member_entry(name: str, enabled: set[str]) -> dict[str, Any]:
    status = violation_execution_status(name)
    from core.violation_policy import (
        behavior_details_for,
        legal_mapping_for,
        legal_status_for,
    )

    mapping = legal_mapping_for(name)
    legal = legal_status_for(name)
    return {
        "canonical_rule": name,
        "name": name,
        "status": status,
        "toggleable": name in TOGGLEABLE_VIOLATIONS,
        "enabled": name in enabled,
        "implemented": name in IMPLEMENTED_VIOLATIONS,
        "model_dependent": name in MODEL_DEPENDENT_VIOLATIONS,
        "partial": name in PARTIAL_VIOLATIONS,
        "needs_decision": name in NEEDS_DECISION_VIOLATIONS,
        "planned": name in PLANNED_VIOLATIONS,
        "legal_status": legal.value if legal else None,
        "behavior_details": list(behavior_details_for(name)),
        "notes": mapping.notes if mapping else None,
    }


def _group_enabled_state(members: list[dict[str, Any]]) -> str:
    toggleable = [m for m in members if m.get("toggleable")]
    if not toggleable:
        return "off"
    enabled_flags = [bool(m.get("enabled")) for m in toggleable]
    if all(enabled_flags):
        return "on"
    if not any(enabled_flags):
        return "off"
    return "mixed"


def violation_groups_for_ui(
    *,
    enabled: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Grouped catalog for Settings UI.

    Groups by ``official_category_proposed`` from the legal-mapping config.
    Rules without proposed wording appear as single-rule groups keyed by the
    canonical identifier. Persistence always uses canonical rule names.
    """
    from core.violation_policy import legal_mapping_for

    if enabled is None:
        enabled = set(load_enabled_violations())

    buckets: dict[str, list[str]] = {}
    for name in CANONICAL_VIOLATIONS:
        mapping = legal_mapping_for(name)
        key = None
        if mapping is not None and mapping.official_category_proposed:
            key = mapping.official_category_proposed
        else:
            key = name
        buckets.setdefault(key, []).append(name)

    def _sort_key(group_name: str) -> tuple[int, str]:
        try:
            return (0, str(_PREFERRED_GROUP_ORDER.index(group_name)).zfill(2))
        except ValueError:
            return (1, group_name)

    groups: list[dict[str, Any]] = []
    for group_name in sorted(buckets.keys(), key=_sort_key):
        members = [_member_entry(n, enabled) for n in buckets[group_name]]
        legal_statuses = {m.get("legal_status") for m in members if m.get("legal_status")}
        # Group legal badge: verified only if all verified; else most restrictive.
        if legal_statuses == {"verified"}:
            group_legal = "verified"
        elif "flag_only" in legal_statuses:
            group_legal = "flag_only"
        elif "unverified" in legal_statuses or "partially_verified" in legal_statuses:
            group_legal = next(
                (
                    s
                    for s in ("partially_verified", "unverified")
                    if s in legal_statuses
                ),
                "unverified",
            )
        else:
            group_legal = next(iter(legal_statuses), None)

        is_shared_official = group_name not in CANONICAL_VIOLATIONS
        state = _group_enabled_state(members)
        toggleable_members = [m for m in members if m.get("toggleable")]
        groups.append(
            {
                "group_id": group_name,
                "title": group_name,
                "is_official_group": is_shared_official,
                "enabled_state": state,
                "enabled": state == "on",
                "mixed": state == "mixed",
                "toggleable": any(m.get("toggleable") for m in members),
                "legal_status": group_legal,
                "pending_legal_verification": group_legal
                in ("unverified", "partially_verified", "flag_only", None),
                "members": members,
                "canonical_rules": [m["canonical_rule"] for m in members],
                "toggleable_canonical_rules": [
                    m["canonical_rule"] for m in toggleable_members
                ],
                "member_enabled_map": {
                    m["canonical_rule"]: bool(m.get("enabled")) for m in members
                },
            }
        )
    return groups


def violation_catalog_for_ui() -> list[dict[str, Any]]:
    """Flat catalog entries (compatibility) with legal grouping metadata."""
    enabled = set(load_enabled_violations())
    from core.violation_policy import legal_mapping_for

    catalog: list[dict[str, Any]] = []
    for name in CANONICAL_VIOLATIONS:
        entry = _member_entry(name, enabled)
        mapping = legal_mapping_for(name)
        entry["official_category_proposed"] = (
            mapping.official_category_proposed if mapping else None
        )
        entry["group_title"] = (
            mapping.official_category_proposed
            if mapping and mapping.official_category_proposed
            else name
        )
        catalog.append(entry)
    return catalog
