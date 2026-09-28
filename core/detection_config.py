"""Detection model, class, and violation-rule configuration for TAVIDM.

Project decision (team): object detector is YOLOv8m (Ultralytics), fine-tuned
on a custom traffic dataset. (Manuscript text still mixes YOLOv8s/YOLOv8m;
implementation follows the team's chosen variant.)
- Tracker: ByteTrack
- Rule engine features: ROI analysis, direction analysis, trajectory analysis,
  object counting, dwell-time analysis, and time-based rules.
- Manual review policy (Ch3 flowchart, Step 7):
    >= 95%  -> automatically queued for operator validation
    80-94%  -> highlighted for careful manual review
    < 80%   -> logged for monitoring (routed to review queue, never auto-confirmed)
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

MODEL_FAMILY = "YOLOv8m"
MODELS_DIR = Path(__file__).resolve().parent.parent / "models"

# Custom fine-tuned weights under ``models/`` are looked up first when present
# (candidate filenames only — none of these are assumed to exist on disk).
# The pretrained COCO YOLOv8m checkpoint is the development fallback.
CUSTOM_WEIGHTS_CANDIDATES = ("tavidm_yolov8m.pt", "best.pt", "yolov8m_custom.pt")
PRETRAINED_WEIGHTS = "yolov8m.pt"

# ---------------------------------------------------------------------------
# Detection classes
# ---------------------------------------------------------------------------
# Frozen Phase 2 vehicle detector roster (owner-approved 2026-08-22 / implemented
# 2026-08-27): exactly **10** vehicle labels. Machine-readable contract:
# config/training/class_schema.json (schema_version 1.3.0).
# Attribute / person labels remain separate from the vehicle roster.
# Broad hierarchy keys (passenger_vehicle, etc.) are NOT detector labels.

YOLO_CLASS_CAR = "car"
YOLO_CLASS_SUV_CROSSOVER = "suv_crossover"  # legacy alias consolidated to car
YOLO_CLASS_VAN = "van"
YOLO_CLASS_JEEPNEY = "jeepney"
YOLO_CLASS_TRICYCLE = "tricycle"
YOLO_CLASS_AUTORICKSHAW = "autorickshaw"
YOLO_CLASS_BUS = "bus"
YOLO_CLASS_TRUCK = "truck"
YOLO_CLASS_PICKUP_TRUCK = "pickup_truck"
YOLO_CLASS_MOTORCYCLE = "motorcycle"
YOLO_CLASS_BICYCLE = "bicycle"
YOLO_CLASS_RIDER = "rider"
YOLO_CLASS_PERSON = "person"
YOLO_CLASS_HELMET_ACCEPTABLE = "helmet_acceptable"
YOLO_CLASS_HELMET_NUT_SHELL = "helmet_nut_shell"
YOLO_CLASS_SIDE_MIRROR = "side_mirror"

# Legacy / non-canonical labels (compatibility only — not in the frozen roster).
YOLO_CLASS_SUV = "suv"  # legacy alias consolidated to car
YOLO_CLASS_HELMET = "helmet"  # legacy generic helmet class
LEGACY_CLASS_UV_EXPRESS_VAN = "uv_express_van"  # consolidates to van
LEGACY_CLASS_PIAGGIO = "piaggio"  # brand; requires body-form review

# Back-compat names kept so older imports do not crash; values are legacy-only.
YOLO_CLASS_UV_EXPRESS_VAN = LEGACY_CLASS_UV_EXPRESS_VAN
YOLO_CLASS_PIAGGIO = LEGACY_CLASS_PIAGGIO

FROZEN_VEHICLE_DETECTOR_CLASSES = (
    YOLO_CLASS_CAR,
    YOLO_CLASS_VAN,
    YOLO_CLASS_JEEPNEY,
    YOLO_CLASS_TRICYCLE,
    YOLO_CLASS_AUTORICKSHAW,
    YOLO_CLASS_BUS,
    YOLO_CLASS_TRUCK,
    YOLO_CLASS_PICKUP_TRUCK,
    YOLO_CLASS_MOTORCYCLE,
    YOLO_CLASS_BICYCLE,
)

# ---------------------------------------------------------------------------
# Object detector roster (ordered) — future 15-class checkpoints
# ---------------------------------------------------------------------------
# ``config/training/class_schema.json`` (schema_version 1.3.0) declares 15
# ``object_classes`` **in detector-ID order**. A future 15-class checkpoint
# (V9.1 family) must reproduce this exact ID->name order or the application
# refuses to process video with it. See ``validate_object_class_map`` and
# ``core.detector.enforce_object_class_contract``.
#
# The first ten entries are the *vehicle* subset (``FROZEN_VEHICLE_DETECTOR_CLASSES``).
# This is NOT a 15-class vehicle roster: people, riders, helmets, and mirrors are
# observable object/attribute labels, never vehicles.
OBJECT_DETECTOR_CLASSES = (
    YOLO_CLASS_CAR,
    YOLO_CLASS_VAN,
    YOLO_CLASS_JEEPNEY,
    YOLO_CLASS_TRICYCLE,
    YOLO_CLASS_AUTORICKSHAW,
    YOLO_CLASS_BUS,
    YOLO_CLASS_TRUCK,
    YOLO_CLASS_PICKUP_TRUCK,
    YOLO_CLASS_MOTORCYCLE,
    YOLO_CLASS_BICYCLE,
    YOLO_CLASS_PERSON,
    YOLO_CLASS_RIDER,
    YOLO_CLASS_HELMET_ACCEPTABLE,
    YOLO_CLASS_HELMET_NUT_SHELL,
    YOLO_CLASS_SIDE_MIRROR,
)
OBJECT_DETECTOR_CLASS_COUNT = len(OBJECT_DETECTOR_CLASSES)

# The non-vehicle tail of the object roster. Attribute / person labels.
NON_VEHICLE_OBJECT_CLASSES = OBJECT_DETECTOR_CLASSES[
    len(FROZEN_VEHICLE_DETECTOR_CLASSES):
]
NON_VEHICLE_OBJECT_CLASS_SET = frozenset(NON_VEHICLE_OBJECT_CLASSES)

# The object roster is the vehicle subset in schema ID order plus the non-vehicle
# tail. Keep this assertion close to the definition so a future roster edit that
# breaks the prefix relationship fails loudly at import time.
assert OBJECT_DETECTOR_CLASSES[: len(FROZEN_VEHICLE_DETECTOR_CLASSES)] == (
    FROZEN_VEHICLE_DETECTOR_CLASSES
)
_OBJECT_CLASS_NAME_SET = frozenset(OBJECT_DETECTOR_CLASSES)

# Seven-class baseline roster (label set only). A trained seven-class ``best.pt``
# exists in an *external* training output directory and has **not** been
# integrated into ``D:\tavidm\models``. Do not claim ``models/best.pt`` exists.
# When such a model is loaded elsewhere, it is a valid subset of the 10-class roster.
SEVEN_CLASS_BASELINE_VEHICLES = (
    YOLO_CLASS_BICYCLE,
    YOLO_CLASS_BUS,
    YOLO_CLASS_CAR,
    YOLO_CLASS_JEEPNEY,
    YOLO_CLASS_MOTORCYCLE,
    YOLO_CLASS_TRICYCLE,
    YOLO_CLASS_TRUCK,
)
SEVEN_CLASS_BASELINE_MISSING = (
    YOLO_CLASS_VAN,
    YOLO_CLASS_AUTORICKSHAW,
    YOLO_CLASS_PICKUP_TRUCK,
)

# Legacy rosters that are *intentionally* smaller than the 15-class object roster
# and must keep working without the caller declaring anything. Compared as a set
# of names; ID order is not part of the legacy contract.
LEGACY_13_CLASS_ATTRIBUTES = (
    YOLO_CLASS_PERSON,
    YOLO_CLASS_RIDER,
    YOLO_CLASS_HELMET,
)

KNOWN_LEGACY_DETECTOR_ROSTERS: tuple[frozenset[str], ...] = tuple(
    frozenset(_names)
    for _names in (
        SEVEN_CLASS_BASELINE_VEHICLES,
        FROZEN_VEHICLE_DETECTOR_CLASSES,
        FROZEN_VEHICLE_DETECTOR_CLASSES + LEGACY_13_CLASS_ATTRIBUTES,
    )
)

# Names that mark a checkpoint as belonging to the object-roster family.
#
# Stock COCO overlaps the object roster on exactly the six generic classes below
# and declares none of the rest, so a COCO checkpoint declares no marker and stays
# legacy. Any mis-sized map that does declare a marker is a broken object-roster
# export (truncated or renamed) and is rejected rather than silently demoted.
COCO_OVERLAP_WITH_OBJECT_ROSTER = frozenset(
    {
        YOLO_CLASS_CAR,
        YOLO_CLASS_BUS,
        YOLO_CLASS_TRUCK,
        YOLO_CLASS_MOTORCYCLE,
        YOLO_CLASS_BICYCLE,
        YOLO_CLASS_PERSON,
    }
)
OBJECT_ROSTER_MARKER_CLASSES = frozenset(OBJECT_DETECTOR_CLASSES) - (
    COCO_OVERLAP_WITH_OBJECT_ROSTER
)
assert OBJECT_ROSTER_MARKER_CLASSES.isdisjoint(COCO_OVERLAP_WITH_OBJECT_ROSTER)
assert "jeepney" in OBJECT_ROSTER_MARKER_CLASSES
assert "side_mirror" in OBJECT_ROSTER_MARKER_CLASSES
assert "car" not in OBJECT_ROSTER_MARKER_CLASSES
assert "person" not in OBJECT_ROSTER_MARKER_CLASSES

# Derived hierarchy (not detector labels). Keys match class_schema.json.
VEHICLE_HIERARCHY: dict[str, tuple[str, ...]] = {
    "passenger_vehicle": (
        YOLO_CLASS_CAR,
        YOLO_CLASS_VAN,
    ),
    # van may also be treated as PUV when contextual for-hire evidence exists;
    # that is rule/context logic, not a hierarchy detector membership.
    "public_utility_vehicle": (
        YOLO_CLASS_JEEPNEY,
        YOLO_CLASS_TRICYCLE,
        YOLO_CLASS_AUTORICKSHAW,
        YOLO_CLASS_BUS,
    ),
    "commercial_vehicle": (
        YOLO_CLASS_TRUCK,
        YOLO_CLASS_PICKUP_TRUCK,
    ),
    "two_or_three_wheeled": (
        YOLO_CLASS_MOTORCYCLE,
        YOLO_CLASS_TRICYCLE,
        YOLO_CLASS_AUTORICKSHAW,
        YOLO_CLASS_BICYCLE,
    ),
}

# Display categories used by analytics UI (derived; not detector labels).
VEHICLE_CATEGORIES: dict[str, tuple[str, ...]] = {
    "Passenger Vehicle": VEHICLE_HIERARCHY["passenger_vehicle"],
    "Public Utility Vehicle": VEHICLE_HIERARCHY["public_utility_vehicle"],
    "Commercial Vehicle": VEHICLE_HIERARCHY["commercial_vehicle"],
    "Two- or Three-Wheeled Vehicle": VEHICLE_HIERARCHY["two_or_three_wheeled"],
}

# Runtime vehicle set used by the rule engine (frozen + safe legacy SUV aliases).
VEHICLE_CLASSES = FROZEN_VEHICLE_DETECTOR_CLASSES + (
    YOLO_CLASS_SUV,
    YOLO_CLASS_SUV_CROSSOVER,
)

# Labels a loaded model may still emit that we accept for compatibility processing.
LEGACY_ACCEPTED_MODEL_LABELS = (
    LEGACY_CLASS_UV_EXPRESS_VAN,
    LEGACY_CLASS_PIAGGIO,
)

# Full detector allow-list: frozen vehicles + attributes + legacy aliases.
DETECTION_CLASSES = VEHICLE_CLASSES + (
    YOLO_CLASS_RIDER,
    YOLO_CLASS_PERSON,
    YOLO_CLASS_HELMET_ACCEPTABLE,
    YOLO_CLASS_HELMET_NUT_SHELL,
    YOLO_CLASS_SIDE_MIRROR,
    YOLO_CLASS_HELMET,
) + LEGACY_ACCEPTED_MODEL_LABELS

# Truck-ban applicability is explicit and configurable. Default: truck only.
# pickup_truck is NOT automatically covered by truck-ban rules.
DEFAULT_TRUCK_BAN_CLASSES = (YOLO_CLASS_TRUCK,)

# Cargo-area passenger rule applicability.
CARGO_PASSENGER_APPLICABLE_CLASSES = (
    YOLO_CLASS_TRUCK,
    YOLO_CLASS_PICKUP_TRUCK,
)

# Safe legacy → canonical consolidations only (never piaggio).
LEGACY_VEHICLE_CLASS_ALIASES = {
    YOLO_CLASS_SUV: YOLO_CLASS_CAR,
    YOLO_CLASS_SUV_CROSSOVER: YOLO_CLASS_CAR,
    LEGACY_CLASS_UV_EXPRESS_VAN: YOLO_CLASS_VAN,
}

# Body-form hints for optional piaggio resolution (never invent when absent).
PIAGGIO_BODY_FORM_TRICYCLE = "sidecar"
PIAGGIO_BODY_FORM_AUTORICKSHAW = "integrated"
REVIEW_STATE_UNCERTAIN = "UNCERTAIN"
REVIEW_STATE_UNKNOWN = "UNKNOWN"


def normalize_vehicle_class(class_label: str) -> str:
    """Map *safe* legacy vehicle labels onto the frozen detector roster.

    ``uv_express_van`` consolidates to ``van``. ``piaggio`` is intentionally
    **not** auto-mapped — use ``resolve_vehicle_label`` with a body-form hint
    or accept an UNCERTAIN review state.
    """
    return LEGACY_VEHICLE_CLASS_ALIASES.get(class_label, class_label)


@dataclass(frozen=True)
class VehicleLabelResolution:
    """Result of resolving a model/annotation vehicle label to the 10-class roster."""

    raw_class: str
    canonical_class: str | None
    review_state: str | None = None
    notes: str = ""

    @property
    def is_canonical(self) -> bool:
        return self.canonical_class in FROZEN_VEHICLE_DETECTOR_CLASSES

    @property
    def fail_closed(self) -> bool:
        return self.canonical_class is None or self.review_state in (
            REVIEW_STATE_UNCERTAIN,
            REVIEW_STATE_UNKNOWN,
        )


def resolve_vehicle_label(
    class_label: str,
    *,
    body_form: str | None = None,
) -> VehicleLabelResolution:
    """Resolve a vehicle label with raw/canonical separation.

    ``body_form`` is only consulted for legacy ``piaggio``:
    - ``sidecar`` → tricycle
    - ``integrated`` → autorickshaw
    - anything else / missing → UNCERTAIN (fail closed; do not invent)
    """
    raw = str(class_label or "").strip().lower()
    if not raw:
        return VehicleLabelResolution(
            raw_class="",
            canonical_class=None,
            review_state=REVIEW_STATE_UNKNOWN,
            notes="Empty class label.",
        )

    if raw == LEGACY_CLASS_PIAGGIO:
        form = (body_form or "").strip().lower()
        if form in (PIAGGIO_BODY_FORM_TRICYCLE, "tricycle", "sidecar_tricycle"):
            return VehicleLabelResolution(
                raw_class=raw,
                canonical_class=YOLO_CLASS_TRICYCLE,
                notes="Legacy piaggio resolved to tricycle via sidecar/conventional body form.",
            )
        if form in (PIAGGIO_BODY_FORM_AUTORICKSHAW, "autorickshaw", "integrated_body"):
            return VehicleLabelResolution(
                raw_class=raw,
                canonical_class=YOLO_CLASS_AUTORICKSHAW,
                notes="Legacy piaggio resolved to autorickshaw via integrated body form.",
            )
        return VehicleLabelResolution(
            raw_class=raw,
            canonical_class=None,
            review_state=REVIEW_STATE_UNCERTAIN,
            notes=(
                "Legacy piaggio brand label cannot be auto-mapped without visible "
                "body-form review (tricycle vs autorickshaw)."
            ),
        )

    if raw in LEGACY_VEHICLE_CLASS_ALIASES:
        canon = LEGACY_VEHICLE_CLASS_ALIASES[raw]
        return VehicleLabelResolution(
            raw_class=raw,
            canonical_class=canon,
            notes=f"Legacy label '{raw}' consolidated to canonical '{canon}'.",
        )

    if raw in FROZEN_VEHICLE_DETECTOR_CLASSES:
        return VehicleLabelResolution(raw_class=raw, canonical_class=raw)

    # Non-vehicle attribute / unknown string — leave as-is without claiming vehicle canon.
    return VehicleLabelResolution(
        raw_class=raw,
        canonical_class=None if raw not in DETECTION_CLASSES else raw,
        notes="Non-vehicle or unrecognized vehicle label.",
    )


def is_canonical_vehicle_class(class_label: str) -> bool:
    return normalize_vehicle_class(class_label) in FROZEN_VEHICLE_DETECTOR_CLASSES


def seven_class_baseline_coverage(available_classes: set[str] | tuple[str, ...] | list[str]) -> dict:
    """Report how a loaded model covers the 10-class roster vs the 7-class baseline."""
    available = {str(c).lower() for c in available_classes}
    present = [c for c in FROZEN_VEHICLE_DETECTOR_CLASSES if c in available]
    missing = [c for c in FROZEN_VEHICLE_DETECTOR_CLASSES if c not in available]
    baseline_ok = all(c in available for c in SEVEN_CLASS_BASELINE_VEHICLES)
    return {
        "roster_size": len(FROZEN_VEHICLE_DETECTOR_CLASSES),
        "present_canonical": present,
        "missing_canonical": missing,
        "seven_class_baseline_complete": baseline_ok,
        "seven_class_baseline_missing_expected": list(SEVEN_CLASS_BASELINE_MISSING),
        "is_valid_seven_class_subset": baseline_ok and set(missing) == set(SEVEN_CLASS_BASELINE_MISSING),
    }


def vehicle_hierarchy_key(class_label: str) -> str | None:
    """Return the derived hierarchy key for a detector class, if any."""
    resolved = resolve_vehicle_label(class_label)
    canon = resolved.canonical_class
    if canon is None:
        return None
    for key, members in VEHICLE_HIERARCHY.items():
        if canon in members:
            return key
    return None


def vehicle_category(class_label: str) -> str | None:
    """Return the display category for analytics (derived; not a detector label)."""
    resolved = resolve_vehicle_label(class_label)
    canon = resolved.canonical_class
    if canon is None:
        return None
    for category, classes in VEHICLE_CATEGORIES.items():
        if canon in classes:
            return category
    return None


def is_cargo_passenger_applicable(class_label: str) -> bool:
    """True when the vehicle type is in scope for the cargo-passenger rule."""
    resolved = resolve_vehicle_label(class_label)
    return resolved.canonical_class in CARGO_PASSENGER_APPLICABLE_CLASSES


def is_truck_ban_applicable(
    class_label: str,
    ban_classes: tuple[str, ...] | list[str] | None = None,
) -> bool:
    """True when the vehicle type is covered by the configured truck-ban set."""
    allowed = tuple(ban_classes) if ban_classes is not None else DEFAULT_TRUCK_BAN_CLASSES
    resolved = resolve_vehicle_label(class_label)
    if resolved.canonical_class is None:
        return False
    allowed_canon = {
        resolve_vehicle_label(name).canonical_class or normalize_vehicle_class(name)
        for name in allowed
    }
    return resolved.canonical_class in allowed_canon


# Classes required before helmet/overloading rules may run (custom weights only).
# Prefer the frozen helmet taxonomy; legacy generic ``helmet`` remains an
# alternate accepted by ``core.model_capability.RULE_ALTERNATE_CLASS_SETS``.
#
# These mirror ``RULE_REQUIRED_CLASSES[No Helmet]`` in core/model_capability.py.
# The rider association logic in core/violation_engine.py only ever consumes
# ``rider`` detections, so this helper must name ``rider`` too. ``person`` is a
# distinct object class and is never a substitute for ``rider``.
REQUIRED_MODEL_CLASSES = (
    YOLO_CLASS_MOTORCYCLE,
    YOLO_CLASS_RIDER,
    YOLO_CLASS_HELMET_ACCEPTABLE,
    YOLO_CLASS_HELMET_NUT_SHELL,
)

# Cargo-passenger evidence is a ``person`` box in a truck/pickup cargo region
# (core/cargo_passenger.py). This set is intentionally separate from
# ``REQUIRED_MODEL_CLASSES``: a rider is not cargo-passenger evidence.
REQUIRED_MODEL_CLASSES_CARGO_PASSENGER = (
    YOLO_CLASS_PERSON,
    YOLO_CLASS_TRUCK,
    YOLO_CLASS_PICKUP_TRUCK,
)

# Classes whose absence must disable side-mirror automatic evaluation.
REQUIRED_MODEL_CLASSES_SIDE_MIRROR = (YOLO_CLASS_SIDE_MIRROR,)


# ---------------------------------------------------------------------------
# 15-class object roster contract (strict, reusable, never raises)
# ---------------------------------------------------------------------------

# Ordered-roster violation codes. Reused by callers, logs, and tests so a
# rejection reason is machine-readable without re-deriving it.
CLASS_MAP_MALFORMED = "malformed"
CLASS_MAP_WRONG_COUNT = "wrong_class_count"
CLASS_MAP_NON_CONTIGUOUS_IDS = "non_contiguous_class_ids"
CLASS_MAP_DUPLICATE = "duplicate_names"
CLASS_MAP_MISSING = "missing_names"
CLASS_MAP_UNKNOWN = "unknown_names"
CLASS_MAP_REORDERED = "reordered_names"


@dataclass(frozen=True)
class ObjectClassMapReport:
    """Outcome of checking a checkpoint's ID->name map against the object roster.

    ``is_object_roster`` is True when the checkpoint declares exactly
    ``len(expected)`` classes, or when a smaller map is a broken attempt at the
    object roster. Only then is the mapping checked for exact order. A genuine
    legacy roster (the frozen 10-class vehicle roster, the seven-class baseline,
    the 13-class attribute checkpoint, COCO) is reported as legacy with no errors
    so the currently selected checkpoint keeps working.

    ``malformed`` records why a map could not be read at all. An unreadable map
    is rejected in both modes: it cannot be proven legacy, so it is not trusted.
    """

    class_count: int
    is_object_roster: bool
    expected: tuple[str, ...]
    actual: tuple[str, ...]
    errors: tuple[str, ...] = ()
    codes: tuple[str, ...] = ()
    malformed: str | None = None
    order_known: bool = True

    @property
    def ok(self) -> bool:
        """True when the checkpoint may be used (roster matches, or is legacy)."""
        return not self.errors

    @property
    def is_legacy_roster(self) -> bool:
        """True when the checkpoint is not an object-roster checkpoint."""
        return not self.is_object_roster

    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(n for n in self.expected if n not in self.actual)

    @property
    def unknown(self) -> tuple[str, ...]:
        known = set(self.expected)
        return tuple(n for n in self.actual if n not in known)

    @property
    def duplicate(self) -> tuple[str, ...]:
        return tuple(
            sorted(n for n, count in Counter(self.actual).items() if count > 1)
        )

    @property
    def summary(self) -> str:
        if self.ok:
            if self.is_object_roster:
                if self.order_known:
                    return (
                        f"{len(self.expected)}-class object roster verified in exact "
                        f"ID order ({self.class_count} classes)."
                    )
                return (
                    f"{len(self.expected)}-class object roster membership verified "
                    f"({self.class_count} classes); ID order not observable from this "
                    "checkpoint's roster."
                )
            return (
                f"Legacy {self.class_count}-class checkpoint accepted "
                f"(not a {len(self.expected)}-class object-roster checkpoint)."
            )
        return "Class map rejected: " + ", ".join(self.errors)


def _ordered_names_from_map(names: Any) -> tuple[tuple[str, ...] | None, str | None]:
    """Normalize an ID->name mapping or ordered name sequence to a name tuple.

    Returns ``(names, malformed_reason)``. ``names`` is None when the input is
    malformed. Accepts ``{id: name}`` (numeric or numeric-string keys) and
    ordered ``list``/``tuple`` name sequences.
    """
    if isinstance(names, Mapping):
        if not names:
            return None, "empty_class_map"
        pairs: list[tuple[int, Any]] = []
        for key, value in names.items():
            if isinstance(key, bool):
                return None, f"non_numeric_class_id:{key!r}"
            if isinstance(key, int):
                class_id = key
            elif isinstance(key, str):
                text = key.strip()
                negative = text.startswith("-")
                if negative:
                    text = text[1:]
                if not text.isdigit():
                    return None, f"non_numeric_class_id:{key!r}"
                class_id = -int(text) if negative else int(text)
            else:
                return None, f"non_numeric_class_id:{key!r}"
            pairs.append((class_id, value))
        if any(class_id < 0 for class_id, _ in pairs):
            return None, "negative_class_id"
        if sorted(class_id for class_id, _ in pairs) != list(range(len(pairs))):
            return None, "non_contiguous_class_ids"
        pairs.sort(key=lambda item: item[0])
        values: list[Any] = [value for _class_id, value in pairs]
    elif isinstance(names, (list, tuple)):
        values = list(names)
    else:
        return None, f"unsupported_class_map_type:{type(names).__name__}"

    out: list[str] = []
    for value in values:
        if not isinstance(value, str):
            return None, f"non_string_class_name:{value!r}"
        text = value.strip()
        if not text:
            return None, "empty_class_name"
        out.append(text.lower())
    if not out:
        return None, "empty_class_map"
    return tuple(out), None


def ordered_class_names(names: Any) -> tuple[str, ...]:
    """ID-ordered, lowercased class names from a checkpoint ``names`` value.

    Reusable normalizer shared by ``Detector`` and the strict contract validator
    so both see the same ordering and the same malformed-input policy. Returns
    ``()`` for an unreadable map.
    """
    ordered, _reason = _ordered_names_from_map(names)
    return ordered or ()


def validate_object_class_map(
    names: Any,
    *,
    expected: Sequence[str] | None = None,
    require_roster: bool = False,
    order_known: bool = True,
) -> ObjectClassMapReport:
    """Strictly validate a checkpoint's ID->name map against the object roster.

    ``names`` is a model ``names`` mapping (``{class_id: class_name}``) or an
    ordered name sequence. The check is pure, never raises, and fails closed.

    ``require_roster=False`` (default) auto-detects: a checkpoint exposing exactly
    ``len(expected)`` classes is treated as an object-roster checkpoint and must
    match the declared order exactly. A mis-sized map is only demoted to "legacy"
    when it declares none of ``OBJECT_ROSTER_MARKER_CLASSES`` (stock COCO) or is one
    of the frozen rosters in ``KNOWN_LEGACY_DETECTOR_ROSTERS``; a truncated or
    renamed object-roster export is rejected, so the currently selected checkpoint
    keeps working while a broken 15-class checkpoint cannot slip through.

    ``require_roster=True`` is for a checkpoint the caller has already declared to
    be an object-roster checkpoint. Then a mis-sized, incomplete, duplicated,
    unknown, or reordered map is a hard rejection.

    ``order_known=False`` marks a roster the caller could only observe as an
    unordered collection. Membership is still enforced exactly; the reordering
    check is skipped because ID order is genuinely not observable.
    """
    roster = tuple(expected) if expected is not None else OBJECT_DETECTOR_CLASSES
    ordered, malformed_reason = _ordered_names_from_map(names)
    if ordered is None:
        # Non-contiguous IDs are a distinct, common export defect, so they get
        # their own machine-readable code instead of the generic one.
        codes = (
            (CLASS_MAP_NON_CONTIGUOUS_IDS,)
            if malformed_reason == "non_contiguous_class_ids"
            else (CLASS_MAP_MALFORMED,)
        )
        if require_roster:
            errors = (
                f"class map is malformed ({malformed_reason}); a {len(roster)}-class "
                "object-roster checkpoint must expose a readable {id: name} map",
            )
        else:
            # An unreadable map cannot be proven legacy, so it cannot be trusted:
            # fail closed rather than let a broken 15-class checkpoint through.
            errors = (
                f"class map is unreadable ({malformed_reason}); refusing to assume it "
                "is a legacy checkpoint",
            )
        return ObjectClassMapReport(
            class_count=0,
            is_object_roster=require_roster,
            expected=roster,
            actual=(),
            errors=errors,
            codes=codes,
            malformed=malformed_reason,
            order_known=order_known,
        )

    class_count = len(ordered)
    if class_count != len(roster):
        roster_markers = OBJECT_ROSTER_MARKER_CLASSES.intersection(ordered)
        known_legacy = frozenset(ordered) in KNOWN_LEGACY_DETECTOR_ROSTERS
        if not require_roster and (not roster_markers or known_legacy):
            # Unrelated to the object roster (COCO) or an intentionally frozen
            # legacy roster: keep the currently selected checkpoint working.
            return ObjectClassMapReport(
                class_count=class_count,
                is_object_roster=False,
                expected=roster,
                actual=ordered,
                order_known=order_known,
            )
        absent = tuple(n for n in roster if n not in set(ordered))
        if roster_markers and not known_legacy:
            errors = (
                f"class map reports {class_count} classes using object-roster names "
                f"{sorted(roster_markers)} but is not a known legacy roster; missing "
                f"{list(absent)}",
            )
        else:
            errors = (
                f"class map reports {class_count} classes but the declared object "
                f"roster has {len(roster)}",
            )
        return ObjectClassMapReport(
            class_count=class_count,
            is_object_roster=True,
            expected=roster,
            actual=ordered,
            errors=errors,
            codes=(CLASS_MAP_WRONG_COUNT,),
            order_known=order_known,
        )

    report = ObjectClassMapReport(
        class_count=class_count,
        is_object_roster=True,
        expected=roster,
        actual=ordered,
        order_known=order_known,
    )
    errors_list: list[str] = []
    codes_list: list[str] = []

    missing = report.missing
    if missing:
        codes_list.append(CLASS_MAP_MISSING)
        errors_list.append("missing class name(s): " + ", ".join(missing))
    unknown = report.unknown
    if unknown:
        codes_list.append(CLASS_MAP_UNKNOWN)
        errors_list.append("unknown class name(s): " + ", ".join(unknown))
    duplicate = report.duplicate
    if duplicate:
        codes_list.append(CLASS_MAP_DUPLICATE)
        errors_list.append("duplicate class name(s): " + ", ".join(duplicate))
    if (
        order_known
        and not (missing or unknown or duplicate)
        and ordered != roster
    ):
        codes_list.append(CLASS_MAP_REORDERED)
        errors_list.append(
            "class IDs are reordered; expected "
            f"{list(roster)} but the checkpoint reports {list(ordered)}"
        )

    if not errors_list:
        return report
    return ObjectClassMapReport(
        class_count=class_count,
        is_object_roster=True,
        expected=roster,
        actual=ordered,
        errors=tuple(errors_list),
        codes=tuple(codes_list),
        order_known=order_known,
    )


def object_class_map_report_text(
    names: Any,
    *,
    expected: Sequence[str] | None = None,
    require_roster: bool = False,
) -> str:
    """Reusable single-line description of a checkpoint class-map verdict."""
    ordered, malformed_reason = _ordered_names_from_map(names)
    if ordered is None:
        return (
            f"Class map unreadable ({malformed_reason}); the "
            f"{len(expected) if expected is not None else len(OBJECT_DETECTOR_CLASSES)}"
            "-class object roster contract could not be verified."
        )
    return validate_object_class_map(
        names, expected=expected, require_roster=require_roster
    ).summary


def is_object_class_name(class_label: str) -> bool:
    """True for any declared object-class name (vehicle or non-vehicle)."""
    return str(class_label or "").strip().lower() in _OBJECT_CLASS_NAME_SET


def is_vehicle_object_class(class_label: str) -> bool:
    """True only for the ten-class vehicle subset of the object roster."""
    return is_canonical_vehicle_class(class_label)


# ---------------------------------------------------------------------------
# Confidence policy (manuscript flowchart Step 7)
# ---------------------------------------------------------------------------

CONF_AUTO_QUEUE = 0.95   # >= : auto-queued for operator validation
CONF_CAREFUL_REVIEW = 0.80  # 80-94%: highlighted for careful manual review
# < CONF_CAREFUL_REVIEW: logged for monitoring (still routed to review queue —
# the manuscript's Data Source section requires below-threshold detections to
# be "routed to the manual review queue rather than being discarded)."


def confidence_band(confidence: float) -> str:
    if confidence >= CONF_AUTO_QUEUE:
        return "auto-queued"
    if confidence >= CONF_CAREFUL_REVIEW:
        return "careful-review"
    return "low-confidence"

# ---------------------------------------------------------------------------
# Rule-engine tuning (System Configuration Module: "Traffic Rule Parameters")
# ---------------------------------------------------------------------------
# Defaults; runtime values come from the system_settings table.

VIOLATION_PERSISTENCE_SEC = 1.5     # condition must persist before firing
RIDER_ASSOCIATION_PADDING = 0.35    # motorcycle bbox expansion for rider match

DEFAULT_RULE_PARAMETERS = {
    "confidence_threshold": 0.60,     # minimum detection confidence
    "frame_skip": 2,                  # process every Nth frame
    "stationary_px": 8.0,             # max centroid movement (px/s) = stationary
    "stopping_dwell_sec": 10.0,       # stationary in no-parking zone -> Illegal Stopping
    "parking_dwell_sec": 30.0,        # stationary in no-parking zone -> Illegal Parking
    "obstruction_dwell_sec": 10.0,    # stationary in active lane -> Obstruction
    "crossing_block_sec": 3.0,        # stationary on pedestrian crossing
    "loading_dwell_sec": 8.0,         # PUV stationary in no-loading zone
    "truck_ban_start": "06:00",
    "truck_ban_end": "09:00",
    "truck_ban_classes": list(DEFAULT_TRUCK_BAN_CLASSES),
    "lane_flow_degrees": 90.0,        # allowed travel direction in active lane
    "flow_tolerance_degrees": 60.0,   # deviation from opposite dir = counterflow
    "min_direction_px": 40.0,         # min displacement before direction is valid
}

# ---------------------------------------------------------------------------
# Violation registry (Canonical 12 — frozen project scope)
# ---------------------------------------------------------------------------
# CANONICAL_VIOLATIONS = intended project coverage (12 types).
# IMPLEMENTED_VIOLATIONS = rules with complete executable logic in this codebase.
# PARTIAL_VIOLATIONS = zone-dwell proxy rules; not full spec behavior.
# PLANNED_VIOLATIONS = canonical scope without executable rule functions yet.
# TOGGLEABLE_VIOLATIONS = may be enabled/disabled (implemented + partial + model-dependent).

# ---- Canonical identifiers (exact owner-approved roster strings) ----------
VIOLATION_ILLEGAL_PARKING = "Illegal Parking"
VIOLATION_OBSTRUCTION = "Obstruction"
VIOLATION_COUNTERFLOW = "Counterflow"
VIOLATION_TRUCK_BAN = "Truck-Ban Violation"
VIOLATION_NO_HELMET = "No Helmet"
VIOLATION_NO_SIDE_MIRROR = "No Side Mirror"
VIOLATION_MOTORCYCLE_OVERLOADING = "Motorcycle Overloading"
VIOLATION_DISREGARDING_SIGN = "Disregarding Traffic Sign"
VIOLATION_PAVEMENT_MARKINGS = "Failure to Follow Road/Pavement Markings"
VIOLATION_ILLEGAL_TERMINAL = "Illegal Terminal"
VIOLATION_CARGO_PASSENGERS = (
    "Unauthorized Passenger in Applicable Truck/Pickup Cargo Area"
)
VIOLATION_SUBSTANDARD_HELMET = "Substandard / Nut-Shell Helmet"

# Legacy fused label (not canonical — compatibility only)
LEGACY_FUSED_PARKING_TERMINAL = "Illegal Parking / Illegal Terminal"
VIOLATION_ILLEGAL_PARKING_TERMINAL = LEGACY_FUSED_PARKING_TERMINAL

# ---- Legacy names (DB / historical records; not canonical) -----------------
LEGACY_ILLEGAL_PARKING = "Illegal Parking"
LEGACY_ILLEGAL_STOPPING = "Illegal Stopping"
LEGACY_BLOCKING_PEDESTRIAN = "Blocking Pedestrian Crossing"
LEGACY_LOADING_UNLOADING = "Illegal Loading/Unloading"
LEGACY_RESTRICTED_LANE = "Restricted Lane Violation"
LEGACY_NO_HELMET_VIOLATION = "No Helmet Violation"
LEGACY_OVERLOADING = "Motorcycle Overloading"
LEGACY_TRUCK_BAN = "Truck Ban Violation"
LEGACY_OBSTRUCTION = "Obstruction"
LEGACY_COUNTERFLOW = "Counterflow Driving"
LEGACY_SUBSTANDARD_HELMET = "Substandard Helmet"
LEGACY_CARGO_PASSENGERS = "Unauthorized Passengers in Pickup/Truck Cargo Area"

# Order matches docs/VIOLATION_ENGINE_SPECIFICATION.md §4.
CANONICAL_VIOLATIONS = (
    VIOLATION_ILLEGAL_PARKING,
    VIOLATION_OBSTRUCTION,
    VIOLATION_COUNTERFLOW,
    VIOLATION_TRUCK_BAN,
    VIOLATION_NO_HELMET,
    VIOLATION_NO_SIDE_MIRROR,
    VIOLATION_MOTORCYCLE_OVERLOADING,
    VIOLATION_DISREGARDING_SIGN,
    VIOLATION_PAVEMENT_MARKINGS,
    VIOLATION_ILLEGAL_TERMINAL,
    VIOLATION_CARGO_PASSENGERS,
    VIOLATION_SUBSTANDARD_HELMET,
)

# Core rules with working detection logic in the current engine
IMPLEMENTED_VIOLATIONS = (
    VIOLATION_OBSTRUCTION,
    VIOLATION_NO_HELMET,
    VIOLATION_COUNTERFLOW,
    VIOLATION_TRUCK_BAN,
    VIOLATION_MOTORCYCLE_OVERLOADING,
)

# Partial / fail-closed evaluators exist but are not production-complete.
# Do not label these "implemented" solely because a stub or fail-closed
# evaluator is present.
PARTIAL_VIOLATIONS = (
    VIOLATION_ILLEGAL_PARKING,
    VIOLATION_ILLEGAL_TERMINAL,
    VIOLATION_PAVEMENT_MARKINGS,
    VIOLATION_NO_SIDE_MIRROR,
    VIOLATION_CARGO_PASSENGERS,
    VIOLATION_SUBSTANDARD_HELMET,
    VIOLATION_DISREGARDING_SIGN,
)

# Rules that remain blocked on an explicit owner decision (still PARTIAL status).
NEEDS_DECISION_VIOLATIONS = (
    VIOLATION_NO_SIDE_MIRROR,
)

# Requires custom model classes (e.g. helmet / side_mirror) to trigger
MODEL_DEPENDENT_VIOLATIONS = (
    VIOLATION_NO_HELMET,
    VIOLATION_SUBSTANDARD_HELMET,
    VIOLATION_NO_SIDE_MIRROR,
)

# No canonical rule remains without an explicit evaluator. Rules that still
# lack frozen legal thresholds or annotations stay in PARTIAL (fail-closed /
# review), not PLANNED.
PLANNED_VIOLATIONS: tuple[str, ...] = ()

TOGGLEABLE_VIOLATIONS = IMPLEMENTED_VIOLATIONS + PARTIAL_VIOLATIONS

ENABLED_VIOLATIONS_SETTING_KEY = "enabled_violations"

# Default runtime enablement preserves prior behavior (5 core rules)
DEFAULT_ENABLED_VIOLATIONS = IMPLEMENTED_VIOLATIONS

ALL_VIOLATION_TYPES = CANONICAL_VIOLATIONS + (
    LEGACY_FUSED_PARKING_TERMINAL,
    LEGACY_ILLEGAL_STOPPING,
    LEGACY_BLOCKING_PEDESTRIAN,
    LEGACY_LOADING_UNLOADING,
    LEGACY_RESTRICTED_LANE,
    LEGACY_NO_HELMET_VIOLATION,
    LEGACY_OVERLOADING,
)


def violation_execution_status(viol_type: str) -> str:
    """Return UI/engine status: implemented | model_dependent | partial | planned."""
    if viol_type in PLANNED_VIOLATIONS:
        return "planned"
    if viol_type in PARTIAL_VIOLATIONS:
        return "partial"
    if viol_type in MODEL_DEPENDENT_VIOLATIONS:
        return "model_dependent"
    if viol_type in IMPLEMENTED_VIOLATIONS:
        return "implemented"
    return "unknown"


def is_implemented_violation(viol_type: str) -> bool:
    """True when the violation has complete executable rule logic."""
    return viol_type in IMPLEMENTED_VIOLATIONS


def is_toggleable_violation(viol_type: str) -> bool:
    return viol_type in TOGGLEABLE_VIOLATIONS


def is_canonical_violation(viol_type: str) -> bool:
    return viol_type in CANONICAL_VIOLATIONS


def legacy_to_canonical(viol_type: str) -> str | None:
    """Map legacy DB/display names to canonical identifiers.

    The fused parking/terminal label is ambiguous. For analytics aggregation it
    maps to Illegal Parking (the more general rule). Historical row text is
    never rewritten by this helper.
    """
    mapping = {
        LEGACY_OBSTRUCTION: VIOLATION_OBSTRUCTION,
        LEGACY_COUNTERFLOW: VIOLATION_COUNTERFLOW,
        LEGACY_TRUCK_BAN: VIOLATION_TRUCK_BAN,
        LEGACY_NO_HELMET_VIOLATION: VIOLATION_NO_HELMET,
        LEGACY_OVERLOADING: VIOLATION_MOTORCYCLE_OVERLOADING,
        LEGACY_ILLEGAL_PARKING: VIOLATION_ILLEGAL_PARKING,
        LEGACY_ILLEGAL_STOPPING: VIOLATION_ILLEGAL_PARKING,
        LEGACY_LOADING_UNLOADING: VIOLATION_ILLEGAL_TERMINAL,
        LEGACY_BLOCKING_PEDESTRIAN: VIOLATION_OBSTRUCTION,
        LEGACY_RESTRICTED_LANE: VIOLATION_PAVEMENT_MARKINGS,
        LEGACY_FUSED_PARKING_TERMINAL: VIOLATION_ILLEGAL_PARKING,
        LEGACY_SUBSTANDARD_HELMET: VIOLATION_SUBSTANDARD_HELMET,
        LEGACY_CARGO_PASSENGERS: VIOLATION_CARGO_PASSENGERS,
    }
    return mapping.get(viol_type)


def canonicalize_violation(viol_type: str) -> str:
    """Convert any known violation name to its canonical form."""
    if viol_type in CANONICAL_VIOLATIONS:
        return viol_type
    mapped = legacy_to_canonical(viol_type)
    return mapped if mapped is not None else viol_type


def violation_type_query_names(viol_type: str) -> tuple[str, ...]:
    """Return DB values that should match a filter for ``viol_type``.

    Includes the requested name, its canonical form, and every known legacy
    alias that maps to the same canonical type. Historical row text is not
    rewritten; this only expands read-side filters.
    """
    canon = canonicalize_violation(viol_type)
    names = {viol_type, canon}
    # Rebuild the legacy map locally so this stays the single expansion point.
    for legacy_name in (
        LEGACY_OBSTRUCTION,
        LEGACY_COUNTERFLOW,
        LEGACY_TRUCK_BAN,
        LEGACY_NO_HELMET_VIOLATION,
        LEGACY_OVERLOADING,
        LEGACY_ILLEGAL_PARKING,
        LEGACY_ILLEGAL_STOPPING,
        LEGACY_LOADING_UNLOADING,
        LEGACY_BLOCKING_PEDESTRIAN,
        LEGACY_RESTRICTED_LANE,
        LEGACY_FUSED_PARKING_TERMINAL,
        LEGACY_SUBSTANDARD_HELMET,
        LEGACY_CARGO_PASSENGERS,
    ):
        mapped = legacy_to_canonical(legacy_name)
        if mapped == canon:
            names.add(legacy_name)
    return tuple(sorted(names))
