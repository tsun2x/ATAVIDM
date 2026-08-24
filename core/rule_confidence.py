"""Violation confidence scoring — separate from detector confidence."""

from __future__ import annotations

from typing import Any

from core.rule_types import ViolationScore


def score_violation(
    *,
    detection_confidence: float | None = None,
    persistence_ratio: float | None = None,
    geometry_stability: float | None = None,
    association_quality: float | None = None,
    contextual_availability: float | None = None,
    detection_reliability: float | None = None,
    extra_factors: dict[str, float] | None = None,
    weights: dict[str, float] | None = None,
) -> ViolationScore:
    """Compose a rule-engine confidence from named contributing factors.

    Detector confidence may contribute as one named factor but cannot be the
    entire violation score. Missing factors are listed in ``unavailable_factors``
    and do not invent evidence.
    """
    default_weights = {
        "persistence": 0.30,
        "geometry_stability": 0.20,
        "association_quality": 0.20,
        "contextual_availability": 0.15,
        "detection_reliability": 0.15,
    }
    w = dict(default_weights)
    if weights:
        w.update(weights)

    factors: dict[str, float] = {}
    unavailable: list[str] = []

    def _add(name: str, value: float | None) -> None:
        if value is None:
            unavailable.append(name)
            return
        factors[name] = float(max(0.0, min(1.0, value)))

    _add("persistence", persistence_ratio)
    _add("geometry_stability", geometry_stability)
    _add("association_quality", association_quality)
    _add("contextual_availability", contextual_availability)

    # Detection reliability defaults from detector confidence when provided,
    # but is still only one weighted factor.
    if detection_reliability is not None:
        _add("detection_reliability", detection_reliability)
    elif detection_confidence is not None:
        _add("detection_reliability", float(detection_confidence))
    else:
        unavailable.append("detection_reliability")

    if extra_factors:
        for name, value in extra_factors.items():
            factors[name] = float(max(0.0, min(1.0, value)))
            w.setdefault(name, 0.10)

    # Renormalize weights over available factors only.
    available_weight = sum(w.get(name, 0.0) for name in factors)
    if available_weight <= 0:
        return ViolationScore(
            violation_confidence=0.0,
            evidence_sufficiency=0.0,
            contributing_factors={},
            unavailable_factors=tuple(unavailable),
            detection_confidence=detection_confidence,
        )

    violation_conf = sum(factors[name] * w.get(name, 0.0) for name in factors) / available_weight
    # Sufficiency: fraction of core factors that were available, scaled by score.
    core = (
        "persistence",
        "geometry_stability",
        "association_quality",
        "contextual_availability",
        "detection_reliability",
    )
    present = sum(1 for name in core if name in factors)
    sufficiency = (present / len(core)) * violation_conf

    return ViolationScore(
        violation_confidence=float(max(0.0, min(1.0, violation_conf))),
        evidence_sufficiency=float(max(0.0, min(1.0, sufficiency))),
        contributing_factors=factors,
        unavailable_factors=tuple(unavailable),
        detection_confidence=(
            float(detection_confidence) if detection_confidence is not None else None
        ),
    )


def legacy_confidence_mapping(score: ViolationScore) -> float:
    """Compatibility value for consumers of the historical ``confidence`` field.

    Mapping (documented): ``confidence`` == ``violation_confidence``.
    Detection confidence is stored separately and is never copied here.
    """
    return float(score.violation_confidence)


def score_from_persistence(
    *,
    detection_confidence: float | None,
    elapsed_sec: float,
    required_sec: float,
    geometry_stability: float = 1.0,
    association_quality: float | None = None,
    contextual_availability: float = 1.0,
) -> ViolationScore:
    """Convenience scorer for dwell/persistence rules."""
    ratio = 0.0 if required_sec <= 0 else min(1.0, max(0.0, elapsed_sec / required_sec))
    return score_violation(
        detection_confidence=detection_confidence,
        persistence_ratio=ratio,
        geometry_stability=geometry_stability,
        association_quality=association_quality,
        contextual_availability=contextual_availability,
    )
