"""Tests for violation enable/disable configuration."""

from __future__ import annotations

import json

import pytest

from core.detection_config import (
    CANONICAL_VIOLATIONS,
    DEFAULT_ENABLED_VIOLATIONS,
    IMPLEMENTED_VIOLATIONS,
    PARTIAL_VIOLATIONS,
    PLANNED_VIOLATIONS,
    TOGGLEABLE_VIOLATIONS,
    VIOLATION_ILLEGAL_PARKING,
    VIOLATION_ILLEGAL_TERMINAL,
    VIOLATION_OBSTRUCTION,
    VIOLATION_SUBSTANDARD_HELMET,
    ENABLED_VIOLATIONS_SETTING_KEY,
    LEGACY_FUSED_PARKING_TERMINAL,
    canonicalize_violation,
)
from core.violation_config import (
    ViolationConfigError,
    load_enabled_violations,
    save_enabled_violations,
    validate_enabled_violations,
)


class TestCanonicalRegistry:
    def test_exactly_twelve_canonical_violations(self):
        assert len(CANONICAL_VIOLATIONS) == 12

    def test_parking_and_terminal_are_separate(self):
        assert VIOLATION_ILLEGAL_PARKING in CANONICAL_VIOLATIONS
        assert VIOLATION_ILLEGAL_TERMINAL in CANONICAL_VIOLATIONS
        assert LEGACY_FUSED_PARKING_TERMINAL not in CANONICAL_VIOLATIONS

    def test_implemented_is_subset_of_canonical(self):
        for name in IMPLEMENTED_VIOLATIONS:
            assert name in CANONICAL_VIOLATIONS

    def test_planned_not_toggleable(self):
        for name in PLANNED_VIOLATIONS:
            assert name not in TOGGLEABLE_VIOLATIONS

    def test_all_canonical_are_toggleable_when_no_planned(self):
        # After remediation every canonical rule has an evaluator and is toggleable.
        if not PLANNED_VIOLATIONS:
            assert set(TOGGLEABLE_VIOLATIONS) == set(CANONICAL_VIOLATIONS)

    def test_partial_is_toggleable_not_implemented(self):
        for name in PARTIAL_VIOLATIONS:
            assert name in TOGGLEABLE_VIOLATIONS
            assert name not in IMPLEMENTED_VIOLATIONS


class TestLegacyCanonicalization:
    def test_fused_legacy_maps_to_illegal_parking(self):
        assert canonicalize_violation(LEGACY_FUSED_PARKING_TERMINAL) == VIOLATION_ILLEGAL_PARKING

    def test_legacy_spellings_map_to_approved_names(self):
        from core.detection_config import (
            LEGACY_CARGO_PASSENGERS,
            LEGACY_SUBSTANDARD_HELMET,
            VIOLATION_CARGO_PASSENGERS,
            VIOLATION_SUBSTANDARD_HELMET,
        )

        assert canonicalize_violation(LEGACY_SUBSTANDARD_HELMET) == VIOLATION_SUBSTANDARD_HELMET
        assert canonicalize_violation(LEGACY_CARGO_PASSENGERS) == VIOLATION_CARGO_PASSENGERS
        assert VIOLATION_SUBSTANDARD_HELMET == "Substandard / Nut-Shell Helmet"
        assert "Nut-Shell" in VIOLATION_SUBSTANDARD_HELMET
        assert "Applicable" in VIOLATION_CARGO_PASSENGERS


class TestEnabledViolationsPersistence:
    def test_default_when_unset(self, monkeypatch):
        monkeypatch.setattr("core.violation_config.db.get_setting", lambda _key: None)
        assert load_enabled_violations() == DEFAULT_ENABLED_VIOLATIONS

    def test_save_and_load_round_trip(self, monkeypatch):
        store: dict[str, str] = {}

        def fake_get(key):
            return store.get(key)

        def fake_set(values):
            store.update(values)

        monkeypatch.setattr("core.violation_config.db.get_setting", fake_get)
        monkeypatch.setattr("core.violation_config.db.set_settings", fake_set)

        enabled = (VIOLATION_OBSTRUCTION, VIOLATION_ILLEGAL_PARKING)
        saved = save_enabled_violations(list(enabled))
        assert saved == enabled
        assert json.loads(store[ENABLED_VIOLATIONS_SETTING_KEY]) == list(enabled)
        assert load_enabled_violations() == enabled

    def test_rejects_unknown_violation(self):
        with pytest.raises(ViolationConfigError):
            validate_enabled_violations(["Not A Real Violation"])

    def test_accepts_partial_rules_that_were_formerly_planned(self):
        # Fail-closed / partial evaluators are toggleable; enabling them must
        # not invent automatic confirmation by itself.
        validated = validate_enabled_violations([VIOLATION_SUBSTANDARD_HELMET])
        assert validated == (VIOLATION_SUBSTANDARD_HELMET,)

    def test_explicit_empty_persists(self, monkeypatch):
        store: dict[str, str] = {}
        monkeypatch.setattr("core.violation_config.db.get_setting", lambda key: store.get(key))
        monkeypatch.setattr("core.violation_config.db.set_settings", lambda values: store.update(values))
        save_enabled_violations([])
        assert load_enabled_violations() == ()
