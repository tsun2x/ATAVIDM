"""Gate 9 — Evaluation harness contract (preparation only).

Does not evaluate private footage or claim accuracy. Accepts human-reviewed
ground truth and separates outcome categories for later authorized evaluation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Literal


VALID_GROUND_TRUTH: frozenset[str] = frozenset(
    {
        "genuine_positive",
        "hard_negative",
        "unknown_suppressed",
    }
)

PREDICTION_ONLY_LABELS: frozenset[str] = frozenset({"candidate_emitted"})

GroundTruthKind = Literal[
    "genuine_positive",
    "hard_negative",
    "unknown_suppressed",
]


class EvaluationHarnessError(ValueError):
    """Invalid evaluation input (label, identity, or contradictory sets)."""


@dataclass
class EvaluationCase:
    case_id: str
    violation_type: str
    camera_viewpoint: str
    movement_direction: str
    ground_truth: GroundTruthKind
    notes: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvaluationReport:
    """Precision/recall over reviewed ground truth — never raw candidate counts alone."""

    cases: list[EvaluationCase]
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    unknowns: int = 0

    def candidate_precision(self) -> float | None:
        denom = self.true_positives + self.false_positives
        if denom == 0:
            return None
        return self.true_positives / denom

    def candidate_recall(self) -> float | None:
        denom = self.true_positives + self.false_negatives
        if denom == 0:
            return None
        return self.true_positives / denom

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_count": len(self.cases),
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "unknowns": self.unknowns,
            "candidate_precision": self.candidate_precision(),
            "candidate_recall": self.candidate_recall(),
            "note": (
                "Do not call raw candidate counts accuracy. "
                "Metrics require human-reviewed ground truth."
            ),
        }


def _normalize_id_set(values: Iterable[str] | None, *, label: str) -> set[str]:
    if values is None:
        return set()
    ids: set[str] = set()
    for raw in values:
        case_id = str(raw)
        if not case_id:
            raise EvaluationHarnessError(f"{label} contains an empty case id.")
        ids.add(case_id)
    return ids


def validate_evaluation_inputs(
    cases: list[EvaluationCase],
    emitted_case_ids: Iterable[str],
    unknown_case_ids: Iterable[str] | None = None,
) -> tuple[set[str], set[str]]:
    """Reject invalid ground-truth labels, duplicate IDs, and contradictions."""
    seen: set[str] = set()
    for case in cases:
        case_id = str(case.case_id)
        if not case_id:
            raise EvaluationHarnessError("Evaluation case_id must be a non-empty string.")
        if case_id in seen:
            raise EvaluationHarnessError(f"Duplicate case id: {case_id!r}.")
        seen.add(case_id)
        label = str(case.ground_truth)
        if label in PREDICTION_ONLY_LABELS:
            raise EvaluationHarnessError(
                "candidate_emitted is a prediction/outcome, not ground truth."
            )
        if label not in VALID_GROUND_TRUTH:
            raise EvaluationHarnessError(
                f"Invalid ground-truth label {label!r}. "
                f"Expected one of: {', '.join(sorted(VALID_GROUND_TRUTH))}."
            )

    emitted = _normalize_id_set(emitted_case_ids, label="emitted_case_ids")
    unknown = _normalize_id_set(unknown_case_ids, label="unknown_case_ids")
    overlap = emitted & unknown
    if overlap:
        raise EvaluationHarnessError(
            "Case ids cannot be both emitted and unknown: "
            + ", ".join(sorted(overlap))
            + "."
        )
    return emitted, unknown


def score_against_ground_truth(
    cases: list[EvaluationCase],
    emitted_case_ids: set[str],
    unknown_case_ids: set[str] | None = None,
) -> EvaluationReport:
    emitted, unknown = validate_evaluation_inputs(
        cases, emitted_case_ids, unknown_case_ids
    )
    report = EvaluationReport(cases=list(cases))
    for case in cases:
        was_emitted = case.case_id in emitted
        was_unknown = case.case_id in unknown
        if case.ground_truth == "genuine_positive":
            if was_emitted:
                report.true_positives += 1
            elif was_unknown:
                report.unknowns += 1
            else:
                report.false_negatives += 1
        elif case.ground_truth == "hard_negative":
            if was_emitted:
                report.false_positives += 1
        elif case.ground_truth == "unknown_suppressed":
            if was_unknown or not was_emitted:
                report.unknowns += 1
            else:
                report.false_positives += 1
    return report
