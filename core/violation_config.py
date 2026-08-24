"""Violation enable/disable configuration and catalog metadata.

Global violation toggles are persisted in ``system_settings`` under
``enabled_violations`` as a JSON list of canonical violation names.

Per-video and per-camera violation profiles are not part of the current
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


def violation_catalog_for_ui() -> list[dict[str, Any]]:
    """Catalog entries for Settings UI (all 12 canonical violations)."""
    enabled = set(load_enabled_violations())
    catalog: list[dict[str, Any]] = []
    for name in CANONICAL_VIOLATIONS:
        status = violation_execution_status(name)
        catalog.append(
            {
                "name": name,
                "status": status,
                "toggleable": name in TOGGLEABLE_VIOLATIONS,
                "enabled": name in enabled,
                "implemented": name in IMPLEMENTED_VIOLATIONS,
                "model_dependent": name in MODEL_DEPENDENT_VIOLATIONS,
                "partial": name in PARTIAL_VIOLATIONS,
                "needs_decision": name in NEEDS_DECISION_VIOLATIONS,
                "planned": name in PLANNED_VIOLATIONS,
            }
        )
    return catalog
