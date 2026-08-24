"""Shared rule-engine types: contextual logic, confidence, zone membership."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TriState(str, Enum):
    """Three-valued contextual logic. UNKNOWN must never silently become TRUE."""

    TRUE = "TRUE"
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"

    def is_true(self) -> bool:
        return self is TriState.TRUE

    def is_known(self) -> bool:
        return self is not TriState.UNKNOWN


class MembershipState(str, Enum):
    INSIDE = "INSIDE"
    OUTSIDE = "OUTSIDE"
    BOUNDARY_UNKNOWN = "BOUNDARY_UNKNOWN"


class GeometryMode(str, Enum):
    CALIBRATED = "calibrated"
    NORMALIZED = "normalized"
    LEGACY_FALLBACK = "legacy_fallback"


@dataclass(frozen=True)
class ViolationScore:
    """Rule-engine confidence — not a copy of detector confidence.

    ``violation_confidence`` is an internal evidence-sufficiency score for
    routing (auto-queue / careful review / low). It has no invented legal
    meaning as a probability of guilt.
    """

    violation_confidence: float
    evidence_sufficiency: float
    contributing_factors: dict[str, float] = field(default_factory=dict)
    unavailable_factors: tuple[str, ...] = ()
    detection_confidence: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "violation_confidence": self.violation_confidence,
            "evidence_sufficiency": self.evidence_sufficiency,
            "contributing_factors": dict(self.contributing_factors),
            "unavailable_factors": list(self.unavailable_factors),
            "detection_confidence": self.detection_confidence,
        }


@dataclass
class ZoneMembershipResult:
    state: MembershipState
    overlap_ratio: float
    footprint: list[list[float]]
    anchor_inside: bool
    stable_inside: bool = False
    hysteresis_note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "overlap_ratio": self.overlap_ratio,
            "footprint": self.footprint,
            "anchor_inside": self.anchor_inside,
            "stable_inside": self.stable_inside,
            "hysteresis_note": self.hysteresis_note,
        }


@dataclass
class RuleCapabilityStatus:
    rule_name: str
    automatic_evaluation: bool
    missing_prerequisites: tuple[str, ...] = ()
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_name": self.rule_name,
            "automatic_evaluation": self.automatic_evaluation,
            "missing_prerequisites": list(self.missing_prerequisites),
            "notes": self.notes,
        }


@dataclass
class ProcessingDiagnostics:
    """Surfaced at processing-run start for UI/logs."""

    model_classes: tuple[str, ...] = ()
    geometry_mode: GeometryMode = GeometryMode.LEGACY_FALLBACK
    geometry_snapshot: dict[str, Any] = field(default_factory=dict)
    rule_capabilities: list[RuleCapabilityStatus] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_classes": list(self.model_classes),
            "geometry_mode": self.geometry_mode.value,
            "geometry_snapshot": dict(self.geometry_snapshot),
            "rule_capabilities": [r.as_dict() for r in self.rule_capabilities],
            "notes": list(self.notes),
        }
