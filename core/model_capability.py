"""Model capability gate: compare loaded class names to rule requirements."""

from __future__ import annotations

from typing import Any, Iterable

from core.detection_config import (
    CANONICAL_VIOLATIONS,
    ObjectClassMapReport,
    SEVEN_CLASS_BASELINE_MISSING,
    VIOLATION_CARGO_PASSENGERS,
    VIOLATION_COUNTERFLOW,
    VIOLATION_DISREGARDING_SIGN,
    VIOLATION_ILLEGAL_PARKING,
    VIOLATION_ILLEGAL_TERMINAL,
    VIOLATION_MOTORCYCLE_OVERLOADING,
    VIOLATION_NO_HELMET,
    VIOLATION_NO_SIDE_MIRROR,
    VIOLATION_OBSTRUCTION,
    VIOLATION_PAVEMENT_MARKINGS,
    VIOLATION_SUBSTANDARD_HELMET,
    VIOLATION_TRUCK_BAN,
    YOLO_CLASS_HELMET,
    YOLO_CLASS_HELMET_ACCEPTABLE,
    YOLO_CLASS_HELMET_NUT_SHELL,
    YOLO_CLASS_MOTORCYCLE,
    YOLO_CLASS_PERSON,
    YOLO_CLASS_PICKUP_TRUCK,
    YOLO_CLASS_RIDER,
    YOLO_CLASS_SIDE_MIRROR,
    YOLO_CLASS_TRUCK,
    seven_class_baseline_coverage,
)
from core.rule_types import RuleCapabilityStatus


# Classes that must be present for automatic evaluation of each rule.
# Empty required_classes means the rule is geometry/behavior based (no custom
# attribute classes), but may still need zones/context (checked separately).
RULE_REQUIRED_CLASSES: dict[str, tuple[str, ...]] = {
    VIOLATION_ILLEGAL_PARKING: (),
    VIOLATION_OBSTRUCTION: (),
    VIOLATION_COUNTERFLOW: (),
    VIOLATION_TRUCK_BAN: (YOLO_CLASS_TRUCK,),
    VIOLATION_NO_HELMET: (
        YOLO_CLASS_MOTORCYCLE,
        YOLO_CLASS_RIDER,
        YOLO_CLASS_HELMET_ACCEPTABLE,
        YOLO_CLASS_HELMET_NUT_SHELL,
    ),
    VIOLATION_NO_SIDE_MIRROR: (YOLO_CLASS_SIDE_MIRROR,),
    VIOLATION_MOTORCYCLE_OVERLOADING: (YOLO_CLASS_MOTORCYCLE, YOLO_CLASS_RIDER),
    VIOLATION_DISREGARDING_SIGN: (),  # needs configured sign annotations, not YOLO alone
    VIOLATION_PAVEMENT_MARKINGS: (),  # needs operator-saved marking geometry
    VIOLATION_ILLEGAL_TERMINAL: (),
    VIOLATION_CARGO_PASSENGERS: (
        YOLO_CLASS_PERSON,
        YOLO_CLASS_TRUCK,
    ),
    VIOLATION_SUBSTANDARD_HELMET: (
        YOLO_CLASS_MOTORCYCLE,
        YOLO_CLASS_RIDER,
        YOLO_CLASS_HELMET_NUT_SHELL,
    ),
}

# Alternate acceptable class sets (any one complete set satisfies the gate).
RULE_ALTERNATE_CLASS_SETS: dict[str, tuple[tuple[str, ...], ...]] = {
    VIOLATION_NO_HELMET: (
        (YOLO_CLASS_MOTORCYCLE, YOLO_CLASS_RIDER, YOLO_CLASS_HELMET),
        (YOLO_CLASS_MOTORCYCLE, YOLO_CLASS_RIDER, YOLO_CLASS_HELMET_ACCEPTABLE),
    ),
    VIOLATION_SUBSTANDARD_HELMET: (
        (YOLO_CLASS_MOTORCYCLE, YOLO_CLASS_RIDER, YOLO_CLASS_HELMET_ACCEPTABLE, YOLO_CLASS_HELMET_NUT_SHELL),
    ),
    VIOLATION_CARGO_PASSENGERS: (
        (YOLO_CLASS_PERSON, YOLO_CLASS_PICKUP_TRUCK),
    ),
}

# The frozen main detector roster can observe a helmet. It cannot observe an
# uncovered head. Motorcycle-detail ``no_helmet`` is a review-only label and
# is not an approved source for this rule. Until an owner/adviser defines a
# positive uncovered-head contract, automatic No Helmet evaluation stays off.
POSITIVE_UNCOVERED_HEAD_OBSERVATION_APPROVED = False
NO_HELMET_BLOCKED_PREREQUISITE = "evidence:positive_uncovered_head_observation"


# Annotation / context prerequisites (not detector classes).
RULE_REQUIRED_CONTEXT: dict[str, tuple[str, ...]] = {
    VIOLATION_ILLEGAL_PARKING: ("zone:no_parking",),
    VIOLATION_OBSTRUCTION: ("zone:active_lane_or_crossing",),
    VIOLATION_COUNTERFLOW: ("zone:active_lane", "lane_flow_degrees"),
    VIOLATION_TRUCK_BAN: ("zone:truck_ban_zone", "recording_datetime"),
    # The evaluator runs only when a loading_unloading zone is present.
    # Activity regions do not satisfy this prerequisite.
    VIOLATION_ILLEGAL_TERMINAL: ("zone:loading_unloading", "puv_context"),
    VIOLATION_PAVEMENT_MARKINGS: ("marking_geometry",),
    VIOLATION_DISREGARDING_SIGN: ("supported_sign_annotations",),
    # Mirror/cargo visibility is produced per-episode by observation helpers —
    # not unconditional context flags.
}


def normalize_class_set(names: Iterable[str]) -> set[str]:
    return {str(n).strip().lower() for n in names if str(n).strip()}


def _set_satisfied(required: tuple[str, ...], available: set[str]) -> bool:
    if not required:
        return True
    return all(r.lower() in available for r in required)


def classes_satisfy_rule(rule_name: str, available_classes: Iterable[str]) -> bool:
    available = normalize_class_set(available_classes)
    primary = RULE_REQUIRED_CLASSES.get(rule_name, ())
    if _set_satisfied(primary, available):
        return True
    for alt in RULE_ALTERNATE_CLASS_SETS.get(rule_name, ()):
        if _set_satisfied(alt, available):
            return True
    return False


def missing_classes_for_rule(rule_name: str, available_classes: Iterable[str]) -> tuple[str, ...]:
    available = normalize_class_set(available_classes)
    primary = RULE_REQUIRED_CLASSES.get(rule_name, ())
    if _set_satisfied(primary, available):
        return ()
    for alt in RULE_ALTERNATE_CLASS_SETS.get(rule_name, ()):
        if _set_satisfied(alt, available):
            return ()
    # Report missing against the primary (preferred) set.
    return tuple(r for r in primary if r.lower() not in available)


def assess_rule_capability(
    rule_name: str,
    available_classes: Iterable[str],
    *,
    context_flags: dict[str, bool] | None = None,
) -> RuleCapabilityStatus:
    """Return whether automatic evaluation is allowed for ``rule_name``."""
    missing: list[str] = []
    class_missing = missing_classes_for_rule(rule_name, available_classes)
    if class_missing:
        missing.extend(f"class:{c}" for c in class_missing)

    flags = context_flags or {}
    for req in RULE_REQUIRED_CONTEXT.get(rule_name, ()):
        # Only fail when the flag is explicitly False; absent means unchecked.
        if req in flags and not flags[req]:
            missing.append(f"context:{req}")

    if (
        rule_name == VIOLATION_NO_HELMET
        and not POSITIVE_UNCOVERED_HEAD_OBSERVATION_APPROVED
    ):
        missing.append(NO_HELMET_BLOCKED_PREREQUISITE)

    auto = len(missing) == 0
    notes = ""
    if not auto:
        notes = (
            f"Automatic evaluation disabled for '{rule_name}'. "
            f"Missing: {', '.join(missing)}."
        )
    if NO_HELMET_BLOCKED_PREREQUISITE in missing:
        notes = (
            f"{notes} A missing helmet detection is UNKNOWN, not an uncovered head. "
            "Automatic No Helmet candidate generation is blocked pending an "
            "owner/adviser decision on a positive uncovered-head source. "
            "The motorcycle detail model remains review-only and cannot create "
            "or confirm this violation."
        ).strip()
    return RuleCapabilityStatus(
        rule_name=rule_name,
        automatic_evaluation=auto,
        missing_prerequisites=tuple(missing),
        notes=notes,
    )


def assess_enabled_rules(
    enabled_violations: Iterable[str],
    available_classes: Iterable[str],
    *,
    context_flags: dict[str, bool] | None = None,
) -> list[RuleCapabilityStatus]:
    results: list[RuleCapabilityStatus] = []
    for name in enabled_violations:
        if name not in CANONICAL_VIOLATIONS:
            continue
        results.append(
            assess_rule_capability(name, available_classes, context_flags=context_flags)
        )
    return results


def extract_model_class_names(detector: Any) -> tuple[str, ...]:
    """Read class names via the public Detector.class_names contract.

    Malformed mappings (including non-numeric dict keys) fail closed as empty
    and never raise.
    """
    try:
        raw = getattr(detector, "class_names", None)
        if raw is None:
            return ()
        if isinstance(raw, dict):
            from core.detector import _is_numeric_class_key

            if any(not _is_numeric_class_key(k) for k in raw):
                return ()
            values = raw.values()
        else:
            values = raw
        names = [str(n).strip().lower() for n in values if str(n).strip()]
        return tuple(sorted(set(names)))
    except Exception:
        return ()


def baseline_capability_notes(available_classes: Iterable[str]) -> list[str]:
    """Human-readable notes when a seven-class baseline model is loaded."""
    coverage = seven_class_baseline_coverage(available_classes)
    notes: list[str] = []
    if coverage["is_valid_seven_class_subset"]:
        missing = ", ".join(SEVEN_CLASS_BASELINE_MISSING)
        notes.append(
            "Loaded model matches the seven-class baseline subset of the 10-class "
            f"roster. Unavailable canonical vehicle classes: {missing}. "
            "Rules requiring those classes must fail closed."
        )
    elif coverage["missing_canonical"]:
        missing = ", ".join(coverage["missing_canonical"])
        notes.append(
            f"Loaded model is missing canonical vehicle classes: {missing}."
        )
    return notes


def object_roster_capability_notes(
    report: ObjectClassMapReport | None,
) -> list[str]:
    """Notes for a checkpoint verified against the 15-class object roster.

    Additive only: a legacy roster contributes no note, so existing diagnostics
    and the stored ``diagnostics_json`` shape are unchanged for the currently
    selected checkpoint. The ten vehicle classes remain the vehicle subset; the
    five non-vehicle object classes (person, rider, helmets, side_mirror) are
    observable object/attribute labels and are never counted as vehicles.
    """
    if report is None:
        return []
    if getattr(report, "is_legacy_roster", True):
        return []
    notes = [f"Class roster: {report.summary}"]
    notes.append(
        "Object roster carries 10 vehicle classes and 5 non-vehicle object "
        "classes (person, rider, helmet_acceptable, helmet_nut_shell, "
        "side_mirror). Line-crossing vehicle counts use the vehicle subset only; "
        "detection records cover all object classes."
    )
    return notes
