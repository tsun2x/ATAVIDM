"""Authoritative 12-violation roster contract tests."""

from __future__ import annotations

from core.detection_config import (
    CANONICAL_VIOLATIONS,
    IMPLEMENTED_VIOLATIONS,
    LEGACY_CARGO_PASSENGERS,
    LEGACY_FUSED_PARKING_TERMINAL,
    LEGACY_SUBSTANDARD_HELMET,
    PARTIAL_VIOLATIONS,
    PLANNED_VIOLATIONS,
    VIOLATION_CARGO_PASSENGERS,
    VIOLATION_ILLEGAL_PARKING,
    VIOLATION_ILLEGAL_TERMINAL,
    VIOLATION_SUBSTANDARD_HELMET,
    canonicalize_violation,
    is_canonical_violation,
    is_implemented_violation,
    violation_type_query_names,
)
from core.violation_engine import RuleEngineState, evaluate_detection_rules


APPROVED_ROSTER = (
    "Illegal Parking",
    "Obstruction",
    "Counterflow",
    "Truck-Ban Violation",
    "No Helmet",
    "No Side Mirror",
    "Motorcycle Overloading",
    "Disregarding Traffic Sign",
    "Failure to Follow Road/Pavement Markings",
    "Illegal Terminal",
    "Unauthorized Passenger in Applicable Truck/Pickup Cargo Area",
    "Substandard / Nut-Shell Helmet",
)


class TestCanonicalRosterContract:
    def test_exactly_twelve_unique_canonical_violations(self):
        assert len(CANONICAL_VIOLATIONS) == 12
        assert len(set(CANONICAL_VIOLATIONS)) == 12

    def test_exact_membership_and_order(self):
        assert CANONICAL_VIOLATIONS == APPROVED_ROSTER

    def test_parking_and_terminal_are_distinct(self):
        assert VIOLATION_ILLEGAL_PARKING != VIOLATION_ILLEGAL_TERMINAL
        assert VIOLATION_ILLEGAL_PARKING in CANONICAL_VIOLATIONS
        assert VIOLATION_ILLEGAL_TERMINAL in CANONICAL_VIOLATIONS
        assert LEGACY_FUSED_PARKING_TERMINAL not in CANONICAL_VIOLATIONS
        assert not is_canonical_violation(LEGACY_FUSED_PARKING_TERMINAL)

    def test_obsolete_merged_name_is_not_canonical(self):
        assert canonicalize_violation(LEGACY_FUSED_PARKING_TERMINAL) == VIOLATION_ILLEGAL_PARKING
        assert canonicalize_violation(LEGACY_FUSED_PARKING_TERMINAL) != VIOLATION_ILLEGAL_TERMINAL

    def test_legacy_renames_map_without_corrupting_canonical_strings(self):
        assert canonicalize_violation(LEGACY_SUBSTANDARD_HELMET) == VIOLATION_SUBSTANDARD_HELMET
        assert canonicalize_violation(LEGACY_CARGO_PASSENGERS) == VIOLATION_CARGO_PASSENGERS
        assert VIOLATION_SUBSTANDARD_HELMET == "Substandard / Nut-Shell Helmet"
        assert VIOLATION_CARGO_PASSENGERS == (
            "Unauthorized Passenger in Applicable Truck/Pickup Cargo Area"
        )

    def test_query_names_include_legacy_aliases(self):
        parking_names = violation_type_query_names(VIOLATION_ILLEGAL_PARKING)
        assert VIOLATION_ILLEGAL_PARKING in parking_names
        assert LEGACY_FUSED_PARKING_TERMINAL in parking_names
        assert VIOLATION_ILLEGAL_TERMINAL not in parking_names

        helmet_names = violation_type_query_names(VIOLATION_SUBSTANDARD_HELMET)
        assert LEGACY_SUBSTANDARD_HELMET in helmet_names
        assert VIOLATION_SUBSTANDARD_HELMET in helmet_names

    def test_status_partitions_cover_all_canonical(self):
        covered = set(IMPLEMENTED_VIOLATIONS) | set(PARTIAL_VIOLATIONS) | set(PLANNED_VIOLATIONS)
        assert covered == set(CANONICAL_VIOLATIONS)
        assert set(IMPLEMENTED_VIOLATIONS).isdisjoint(PARTIAL_VIOLATIONS)
        assert set(IMPLEMENTED_VIOLATIONS).isdisjoint(PLANNED_VIOLATIONS)
        assert set(PARTIAL_VIOLATIONS).isdisjoint(PLANNED_VIOLATIONS)


class TestStubViolationsCannotEmit:
    def test_planned_violations_are_not_implemented(self):
        for name in PLANNED_VIOLATIONS:
            assert not is_implemented_violation(name)

    def test_partial_rules_do_not_claim_implemented(self):
        for name in PARTIAL_VIOLATIONS:
            assert not is_implemented_violation(name)

    def test_evaluate_without_prerequisites_emits_nothing_for_sign_rule(self):
        """Disregarding Traffic Sign fail-closes without supported sign annotations."""
        from core.detection_config import VIOLATION_DISREGARDING_SIGN

        tracked = [
            {
                "class_label": "motorcycle",
                "track_id": 1,
                "bbox_x": 100,
                "bbox_y": 100,
                "bbox_w": 50,
                "bbox_h": 25,
                "confidence": 0.95,
                "timestamp_sec": 5.0,
                "speed_px_per_sec": 0.0,
                "direction_degrees": 90,
            }
        ]
        state = RuleEngineState()
        events = evaluate_detection_rules(
            tracked,
            state,
            frame_number=1,
            zones={},
            params={"confidence_threshold": 0.5},
            enabled_violations=(VIOLATION_DISREGARDING_SIGN,),
            model_classes=("motorcycle", "person", "car"),
        )
        assert all(e.violation_type != VIOLATION_DISREGARDING_SIGN for e in events)
