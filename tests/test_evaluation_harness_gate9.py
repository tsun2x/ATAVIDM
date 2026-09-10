"""Gate 9 — evaluation harness fixture contract tests."""

from __future__ import annotations

import pytest

from core.evaluation_harness import (
    EvaluationCase,
    EvaluationHarnessError,
    score_against_ground_truth,
    validate_evaluation_inputs,
)


def _case(case_id: str, ground_truth: str, **kwargs) -> EvaluationCase:
    return EvaluationCase(
        case_id,
        kwargs.get("violation_type", "Counterflow"),
        kwargs.get("camera_viewpoint", "overhead"),
        kwargs.get("movement_direction", "left_to_right"),
        ground_truth,
        notes=kwargs.get("notes", ""),
    )


def test_harness_separates_outcomes_and_avoids_raw_accuracy_claim():
    cases = [
        _case("gp1", "genuine_positive"),
        _case("hn1", "hard_negative", camera_viewpoint="side", movement_direction="right_to_left"),
        _case(
            "uk1",
            "unknown_suppressed",
            violation_type="No Side Mirror",
            camera_viewpoint="distant",
            movement_direction="unknown",
        ),
    ]
    report = score_against_ground_truth(
        cases,
        emitted_case_ids={"gp1"},
        unknown_case_ids={"uk1"},
    )
    payload = report.as_dict()
    assert payload["true_positives"] == 1
    assert payload["false_positives"] == 0
    assert payload["candidate_precision"] == 1.0
    assert "raw candidate counts" in payload["note"].lower()


def test_true_positive_false_positive_false_negative_and_unknown():
    cases = [
        _case("tp", "genuine_positive"),
        _case("fn", "genuine_positive"),
        _case("fp", "hard_negative"),
        _case("tn", "hard_negative"),
        _case("uk", "unknown_suppressed"),
        _case("uk_emitted", "unknown_suppressed"),
    ]
    report = score_against_ground_truth(
        cases,
        emitted_case_ids={"tp", "fp", "uk_emitted"},
        unknown_case_ids={"uk"},
    )
    assert report.true_positives == 1
    assert report.false_positives == 2  # hard-negative emission + unknown_suppressed emission
    assert report.false_negatives == 1
    assert report.unknowns == 1
    assert report.candidate_precision() == pytest.approx(1 / 3)
    assert report.candidate_recall() == pytest.approx(1 / 2)


def test_unknown_suppression_without_emission_counts_unknown():
    cases = [_case("uk1", "unknown_suppressed"), _case("uk2", "unknown_suppressed")]
    report = score_against_ground_truth(cases, emitted_case_ids=set())
    assert report.unknowns == 2
    assert report.false_positives == 0


def test_zero_denominator_metrics_are_none():
    cases = [_case("uk1", "unknown_suppressed")]
    report = score_against_ground_truth(
        cases,
        emitted_case_ids=set(),
        unknown_case_ids={"uk1"},
    )
    payload = report.as_dict()
    assert payload["true_positives"] == 0
    assert payload["false_positives"] == 0
    assert payload["false_negatives"] == 0
    assert payload["candidate_precision"] is None
    assert payload["candidate_recall"] is None


def test_empty_cases_zero_denominator():
    report = score_against_ground_truth([], emitted_case_ids=set())
    assert report.as_dict()["candidate_precision"] is None
    assert report.as_dict()["candidate_recall"] is None
    assert report.as_dict()["case_count"] == 0


def test_invalid_ground_truth_label_rejected():
    cases = [_case("bad", "maybe_positive")]
    with pytest.raises(EvaluationHarnessError, match="Invalid ground-truth label"):
        score_against_ground_truth(cases, emitted_case_ids=set())


def test_candidate_emitted_is_not_ground_truth():
    cases = [_case("c1", "candidate_emitted")]
    with pytest.raises(EvaluationHarnessError, match="prediction/outcome"):
        score_against_ground_truth(cases, emitted_case_ids={"c1"})


def test_duplicate_case_ids_rejected():
    cases = [_case("dup", "genuine_positive"), _case("dup", "hard_negative")]
    with pytest.raises(EvaluationHarnessError, match="Duplicate case id"):
        score_against_ground_truth(cases, emitted_case_ids=set())


def test_contradictory_emitted_and_unknown_rejected():
    cases = [_case("x1", "genuine_positive")]
    with pytest.raises(EvaluationHarnessError, match="both emitted and unknown"):
        score_against_ground_truth(
            cases,
            emitted_case_ids={"x1"},
            unknown_case_ids={"x1"},
        )


def test_validate_evaluation_inputs_accepts_valid_sets():
    cases = [_case("a", "genuine_positive"), _case("b", "hard_negative")]
    emitted, unknown = validate_evaluation_inputs(
        cases, {"a"}, {"not_in_cases_is_ok"}
    )
    assert emitted == {"a"}
    assert "not_in_cases_is_ok" in unknown
