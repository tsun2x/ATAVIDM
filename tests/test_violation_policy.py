"""Tests for the legal-policy mapping layer (core/violation_policy.py).

These tests verify Stage A acceptance criteria 1–8 from the implementation
contract:

  1. Exact 12-member canonical roster and order remain unchanged.
  2. Existing aliases and saved enabled-violation values remain compatible.
  3. Legal verification status does not alter detector-readiness claims.
  4. Invalid/unverified mappings fail safely without guessed citations/penalties.
  5. Fused parking/obstruction creates one case with retained behaviors.
  6. Standalone parking is not indiscriminately converted.
  7. Historical parking/terminal aliases are not confused with the new fusion.
  8. Flag-only mirror, motorcycle-overloading, and cargo cases receive no
     automatic legal penalty.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from core.detection_config import (
    CANONICAL_VIOLATIONS,
    LEGACY_FUSED_PARKING_TERMINAL,
    VIOLATION_CARGO_PASSENGERS,
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
    VIOLATION_COUNTERFLOW,
    canonicalize_violation,
    violation_execution_status,
    violation_type_query_names,
)
from core.violation_policy import (
    LegalStatus,
    behavior_details_for,
    detector_readiness_for,
    official_category_for,
    proposed_official_category_for,
    verified_official_category_for,
    reference_penalty_schedule_for,
    fused_case_category,
    is_parking_obstruction_fusion,
    is_recurrence_eligible_canonical,
    legal_mapping_for,
    legal_status_does_not_imply_detection,
    legal_status_for,
    reset_policy_cache,
)


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


class TestCanonicalRosterPreserved:
    def test_exactly_twelve_canonical(self):
        assert len(CANONICAL_VIOLATIONS) == 12
        assert CANONICAL_VIOLATIONS == APPROVED_ROSTER

    def test_legal_mapping_registers_all_twelve(self):
        for rule in APPROVED_ROSTER:
            mapping = legal_mapping_for(rule)
            assert mapping is not None, f"Missing legal mapping for {rule}"
            assert mapping.canonical_rule == rule

    def test_legal_status_known_for_all_twelve(self):
        valid = LegalStatus.values()
        for rule in APPROVED_ROSTER:
            status = legal_status_for(rule)
            assert status.value in valid

    def test_no_verified_mappings_without_authority(self):
        """No mapping may be 'verified' — sources were inaccessible."""
        for rule in APPROVED_ROSTER:
            status = legal_status_for(rule)
            assert status != LegalStatus.VERIFIED, (
                f"{rule} marked verified without authoritative source"
            )


class TestLegalStatusSeparationFromDetection:
    def test_no_helmet_is_model_dependent_but_unverified(self):
        """No Helmet detector requires custom classes (model_dependent) but
        legal status is unverified."""
        assert violation_execution_status(VIOLATION_NO_HELMET) == "model_dependent"
        status = legal_status_for(VIOLATION_NO_HELMET)
        assert status.value in (
            LegalStatus.FLAG_ONLY.value,
            LegalStatus.UNVERIFIED.value,
        )

    def test_side_mirror_is_partial_but_flag_only(self):
        assert violation_execution_status(VIOLATION_NO_SIDE_MIRROR) in (
            "partial", "model_dependent",
        )
        assert legal_status_for(VIOLATION_NO_SIDE_MIRROR) == LegalStatus.FLAG_ONLY

    def test_motorcycle_overloading_is_implemented_but_flag_only(self):
        assert violation_execution_status(VIOLATION_MOTORCYCLE_OVERLOADING) == "implemented"
        assert legal_status_for(VIOLATION_MOTORCYCLE_OVERLOADING) == LegalStatus.FLAG_ONLY

    def test_cargo_passengers_flag_only(self):
        assert legal_status_for(VIOLATION_CARGO_PASSENGERS) == LegalStatus.FLAG_ONLY

    def test_illegal_terminal_flag_only(self):
        assert legal_status_for(VIOLATION_ILLEGAL_TERMINAL) == LegalStatus.FLAG_ONLY

    def test_substandard_helmet_unverified(self):
        assert legal_status_for(VIOLATION_SUBSTANDARD_HELMET) == LegalStatus.UNVERIFIED

    def test_detector_readiness_independent_of_legal(self):
        for rule in CANONICAL_VIOLATIONS:
            eng = detector_readiness_for(rule)
            leg = legal_status_for(rule)
            # Engine status may be implemented/partial while legal is unverified
            if eng in ("implemented", "partial", "model_dependent"):
                # Legal may or may not be verified — but the two are independent.
                assert leg in LegalStatus.values()
            else:
                assert eng == "unknown"


class TestFailSafeMappings:
    def test_unknown_rule_is_unverified(self):
        assert legal_status_for("Not A Real Violation") == LegalStatus.UNVERIFIED

    def test_unknown_rule_has_no_category(self):
        assert official_category_for("Not A Real Violation") is None
        assert proposed_official_category_for("Not A Real Violation") is None
        assert verified_official_category_for("Not A Real Violation") is None

    def test_unknown_rule_is_not_recurrence_eligible(self):
        assert is_recurrence_eligible_canonical("Not A Real Violation") is False

    def test_no_penalty_schedule_for_flag_only(self):
        mapping = legal_mapping_for(VIOLATION_NO_SIDE_MIRROR)
        assert mapping is not None
        assert mapping.legal_status == "flag_only"
        assert mapping.penalty_schedule is None
        assert reference_penalty_schedule_for(VIOLATION_NO_SIDE_MIRROR) is None

    def test_no_provision_reference_for_unverified(self):
        mapping = legal_mapping_for(VIOLATION_SUBSTANDARD_HELMET)
        assert mapping is not None
        assert mapping.legal_status == "unverified"
        assert mapping.provision_reference is None

    def test_official_category_none_when_flag_only(self):
        """Flag-only rules must not expose an official category for display."""
        for rule in (
            VIOLATION_NO_SIDE_MIRROR,
            VIOLATION_MOTORCYCLE_OVERLOADING,
            VIOLATION_CARGO_PASSENGERS,
            VIOLATION_ILLEGAL_TERMINAL,
        ):
            assert official_category_for(rule) is None, (
                f"{rule} exposed an official category despite flag_only"
            )
            assert proposed_official_category_for(rule) is None

    def test_unverified_not_confused_with_verified_classification(self):
        """Proposed wording remains available; verified gate returns None."""
        for rule in (VIOLATION_ILLEGAL_PARKING, VIOLATION_OBSTRUCTION):
            proposed = proposed_official_category_for(rule)
            assert proposed is not None
            assert official_category_for(rule) is None
            assert verified_official_category_for(rule) is None
            assert reference_penalty_schedule_for(rule) is None
        # Some unverified rules intentionally have no proposed wording yet.
        assert proposed_official_category_for(VIOLATION_SUBSTANDARD_HELMET) is None
        assert official_category_for(VIOLATION_SUBSTANDARD_HELMET) is None
        assert reference_penalty_schedule_for(VIOLATION_SUBSTANDARD_HELMET) is None

    def test_partially_verified_not_treated_as_verified(self, monkeypatch):
        """partially_verified must not pass the verified classification gate."""
        from core import violation_policy as vp

        reset_policy_cache()

        class FakeMapping:
            canonical_rule = VIOLATION_COUNTERFLOW
            official_category_proposed = "Disregarding Traffic Signals"
            legal_status = LegalStatus.PARTIALLY_VERIFIED.value
            verified_elements = ("official category",)
            unresolved_elements = ("provision",)
            behavior_details = ()
            provision_reference = None
            penalty_schedule = {"first_offense": "1500"}
            source_url = None
            mapping_version = "test"
            is_grouped_with = ()
            notes = None

        monkeypatch.setattr(
            vp,
            "_load_mappings",
            lambda: ("test", {VIOLATION_COUNTERFLOW: FakeMapping()}),
        )
        assert legal_status_for(VIOLATION_COUNTERFLOW) == LegalStatus.PARTIALLY_VERIFIED
        assert proposed_official_category_for(VIOLATION_COUNTERFLOW) == (
            "Disregarding Traffic Signals"
        )
        assert verified_official_category_for(VIOLATION_COUNTERFLOW) is None
        assert official_category_for(VIOLATION_COUNTERFLOW) is None
        assert reference_penalty_schedule_for(VIOLATION_COUNTERFLOW) is None
        assert is_recurrence_eligible_canonical(VIOLATION_COUNTERFLOW) is False
        reset_policy_cache()

    def test_behavior_details_preserved_for_flag_only(self):
        mapping = legal_mapping_for(VIOLATION_NO_SIDE_MIRROR)
        assert mapping is not None
        assert len(mapping.behavior_details) > 0


class TestParkingObstructionFusion:
    def test_fusion_returns_obstruction_category(self):
        category, contributors = fused_case_category(
            (VIOLATION_ILLEGAL_PARKING, VIOLATION_OBSTRUCTION)
        )
        # The proposed official category for Obstruction.
        obs_mapping = legal_mapping_for(VIOLATION_OBSTRUCTION)
        assert obs_mapping is not None
        assert category == obs_mapping.official_category_proposed
        assert VIOLATION_ILLEGAL_PARKING in contributors
        assert VIOLATION_OBSTRUCTION in contributors

    def test_is_parking_obstruction_fusion_true_for_pair(self):
        assert is_parking_obstruction_fusion(
            (VIOLATION_ILLEGAL_PARKING, VIOLATION_OBSTRUCTION)
        ) is True

    def test_is_parking_obstruction_fusion_false_for_singles(self):
        assert is_parking_obstruction_fusion((VIOLATION_ILLEGAL_PARKING,)) is False
        assert is_parking_obstruction_fusion((VIOLATION_OBSTRUCTION,)) is False

    def test_is_parking_obstruction_fusion_false_for_extra_rules(self):
        assert is_parking_obstruction_fusion(
            (VIOLATION_ILLEGAL_PARKING, VIOLATION_OBSTRUCTION, VIOLATION_COUNTERFLOW)
        ) is False

    def test_standalone_parking_not_converted(self):
        """A standalone Illegal Parking event has no fusion category."""
        category, contributors = fused_case_category((VIOLATION_ILLEGAL_PARKING,))
        assert category is None
        assert VIOLATION_ILLEGAL_PARKING in contributors
        assert VIOLATION_OBSTRUCTION not in contributors

    def test_legacy_fused_alias_not_confused_with_new_fusion(self):
        """The legacy 'Illegal Parking / Illegal Terminal' label maps to
        Illegal Parking for read filters, NOT to the new Obstruction fusion."""
        canonical = canonicalize_violation(LEGACY_FUSED_PARKING_TERMINAL)
        assert canonical == VIOLATION_ILLEGAL_PARKING
        assert canonical != VIOLATION_ILLEGAL_TERMINAL

        # The legacy alias should NOT trigger fusion when passed alone.
        category, contributors = fused_case_category(
            (LEGACY_FUSED_PARKING_TERMINAL,)
        )
        assert category is None
        assert VIOLATION_OBSTRUCTION not in contributors


class TestRecurrenceEligibility:
    def test_flag_only_not_eligible(self):
        for rule in (
            VIOLATION_NO_SIDE_MIRROR,
            VIOLATION_MOTORCYCLE_OVERLOADING,
            VIOLATION_CARGO_PASSENGERS,
            VIOLATION_ILLEGAL_TERMINAL,
        ):
            assert is_recurrence_eligible_canonical(rule) is False

    def test_unverified_not_eligible(self):
        for rule in (
            VIOLATION_SUBSTANDARD_HELMET,
            VIOLATION_ILLEGAL_PARKING,
            VIOLATION_OBSTRUCTION,
            VIOLATION_COUNTERFLOW,
            VIOLATION_TRUCK_BAN,
            VIOLATION_NO_HELMET,
            VIOLATION_DISREGARDING_SIGN,
            VIOLATION_PAVEMENT_MARKINGS,
        ):
            assert is_recurrence_eligible_canonical(rule) is False, (
                f"{rule} should not be recurrence-eligible (unverified)"
            )

    def test_unknown_not_eligible(self):
        assert is_recurrence_eligible_canonical("Bogus") is False


class TestLegalStatusDoesNotImplyDetection:
    def test_returns_true_for_implemented_but_unverified(self):
        # No Helmet: implemented detector, but legal is flag_only/unverified.
        result = legal_status_does_not_imply_detection(VIOLATION_NO_HELMET)
        assert result is True

    def test_returns_true_for_planned_rules(self):
        # All canonical rules have evaluators; unknown rules also return True.
        result = legal_status_does_not_imply_detection("Bogus Violation")
        assert result is True


class TestLegalStatusEnum:
    def test_values_are_strings(self):
        for member in LegalStatus:
            assert isinstance(member.value, str)

    def test_values_match_doc(self):
        vals = LegalStatus.values()
        assert LegalStatus.VERIFIED.value in vals
        assert LegalStatus.PARTIALLY_VERIFIED.value in vals
        assert LegalStatus.UNVERIFIED.value in vals
        assert LegalStatus.FLAG_ONLY.value in vals
