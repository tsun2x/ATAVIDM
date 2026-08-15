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

from pathlib import Path

# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

MODEL_FAMILY = "YOLOv8m"
MODELS_DIR = Path(__file__).resolve().parent.parent / "models"

# Custom fine-tuned weights (13-class traffic dataset) are looked up first;
# the pretrained COCO YOLOv8m checkpoint is the development fallback.
CUSTOM_WEIGHTS_CANDIDATES = ("tavidm_yolov8m.pt", "best.pt", "yolov8m_custom.pt")
PRETRAINED_WEIGHTS = "yolov8m.pt"

# ---------------------------------------------------------------------------
# Detection classes
# ---------------------------------------------------------------------------
# NOTE: The manuscript states thirteen (13) detection classes but never
# enumerates the full list. The registry below is the union of the concrete
# class lists that appear in Chapter 3 (Phase 3 annotation list, Functional
# Requirements, and Software Requirements sections).
YOLO_CLASS_CAR = "car"
YOLO_CLASS_SUV = "suv"
YOLO_CLASS_JEEPNEY = "jeepney"
YOLO_CLASS_BUS = "bus"
YOLO_CLASS_TRUCK = "truck"
YOLO_CLASS_MOTORCYCLE = "motorcycle"
YOLO_CLASS_BICYCLE = "bicycle"
YOLO_CLASS_RIDER = "rider"
YOLO_CLASS_PERSON = "person"
YOLO_CLASS_HELMET = "helmet"

DETECTION_CLASSES = (
    YOLO_CLASS_CAR,
    YOLO_CLASS_SUV,
    YOLO_CLASS_JEEPNEY,
    YOLO_CLASS_BUS,
    YOLO_CLASS_TRUCK,
    YOLO_CLASS_MOTORCYCLE,
    YOLO_CLASS_BICYCLE,
    YOLO_CLASS_RIDER,
    YOLO_CLASS_PERSON,
    YOLO_CLASS_HELMET,
)

# Manuscript vehicle classifications (Ch1 Scope): four categories.
VEHICLE_CATEGORIES: dict[str, tuple[str, ...]] = {
    "Private Vehicle": (YOLO_CLASS_CAR, YOLO_CLASS_SUV),
    "Public Utility Vehicle": (YOLO_CLASS_JEEPNEY, YOLO_CLASS_BUS),
    "Commercial Vehicle": (YOLO_CLASS_TRUCK,),
    "Two-Wheeled Vehicle": (YOLO_CLASS_MOTORCYCLE, YOLO_CLASS_BICYCLE),
}

VEHICLE_CLASSES = tuple(
    cls for classes in VEHICLE_CATEGORIES.values() for cls in classes
)


def vehicle_category(class_label: str) -> str | None:
    for category, classes in VEHICLE_CATEGORIES.items():
        if class_label in classes:
            return category
    return None


# Classes required before helmet-based rules may run (custom weights only).
REQUIRED_MODEL_CLASSES = (
    YOLO_CLASS_MOTORCYCLE,
    YOLO_CLASS_PERSON,
    YOLO_CLASS_HELMET,
)

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
    "lane_flow_degrees": 90.0,        # allowed travel direction in active lane
    "flow_tolerance_degrees": 60.0,   # deviation from opposite dir = counterflow
    "min_direction_px": 40.0,         # min displacement before direction is valid
}

# ---------------------------------------------------------------------------
# Violation registry (Canonical 11)
# ---------------------------------------------------------------------------
# These are the FINAL canonical violation types for TAVIDM.
# IMPLEMENTED violations are those with working detection logic.
# FUTURE violations are stubs requiring new model classes or researcher input.

# ---- IMPLEMENTED (currently executable) ----
VIOLATION_OBSTRUCTION = "Obstruction"
VIOLATION_NO_HELMET = "No Helmet"
VIOLATION_COUNTERFLOW = "Counterflow"
VIOLATION_TRUCK_BAN = "Truck-Ban Violation"
VIOLATION_MOTORCYCLE_OVERLOADING = "Motorcycle Overloading"

# ---- FUTURE/STUB (do not appear in analytics/reports until implemented) ----
VIOLATION_SUBSTANDARD_HELMET = "Substandard Helmet"
VIOLATION_DISREGARDING_SIGN = "Disregarding Traffic Sign"
VIOLATION_NO_SIDE_MIRROR = "No Side Mirror"
VIOLATION_ILLEGAL_PARKING_TERMINAL = "Illegal Parking / Illegal Terminal"
VIOLATION_PAVEMENT_MARKINGS = "Failure to Follow Road/Pavement Markings"
VIOLATION_CARGO_PASSENGERS = "Unauthorized Passengers in Pickup/Truck Cargo Area"


# Legacy names for backward compatibility with existing data
# These DO NOT map to canonical names - they preserve existing DB values
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


# All canonical violation types (must be exactly 11)
CANONICAL_VIOLATIONS = (
    VIOLATION_OBSTRUCTION,
    VIOLATION_SUBSTANDARD_HELMET,
    VIOLATION_DISREGARDING_SIGN,
    VIOLATION_NO_HELMET,
    VIOLATION_NO_SIDE_MIRROR,
    VIOLATION_ILLEGAL_PARKING_TERMINAL,
    VIOLATION_COUNTERFLOW,
    VIOLATION_TRUCK_BAN,
    VIOLATION_PAVEMENT_MARKINGS,
    VIOLATION_MOTORCYCLE_OVERLOADING,
    VIOLATION_CARGO_PASSENGERS,
)

# Currently implemented/active violation types (have working detection)
IMPLEMENTED_VIOLATIONS = (
    VIOLATION_OBSTRUCTION,
    VIOLATION_NO_HELMET,
    VIOLATION_COUNTERFLOW,
    VIOLATION_TRUCK_BAN,
    VIOLATION_MOTORCYCLE_OVERLOADING,
)

# All known violation types (canonical + legacy)
ALL_VIOLATION_TYPES = CANONICAL_VIOLATIONS + (
    LEGACY_ILLEGAL_PARKING,
    LEGACY_ILLEGAL_STOPPING,
    LEGACY_BLOCKING_PEDESTRIAN,
    LEGACY_LOADING_UNLOADING,
    LEGACY_RESTRICTED_LANE,
    LEGACY_NO_HELMET_VIOLATION,
    LEGACY_OVERLOADING,
)


def is_implemented_violation(viol_type: str) -> bool:
    """Check if a violation type is active/can be detected."""
    return viol_type in IMPLEMENTED_VIOLATIONS


def is_canonical_violation(viol_type: str) -> bool:
    """Check if a violation type is in the canonical set."""
    return viol_type in CANONICAL_VIOLATIONS


def legacy_to_canonical(viol_type: str) -> str | None:
    """Convert legacy violation names to canonical names.
    Returns None if no mapping exists (e.g., already canonical or unmapped).
    """
    mapping = {
        # Legacy maps to canonical
        LEGACY_OBSTRUCTION: VIOLATION_OBSTRUCTION,
        LEGACY_COUNTERFLOW: VIOLATION_COUNTERFLOW,
        LEGACY_TRUCK_BAN: VIOLATION_TRUCK_BAN,
        LEGACY_NO_HELMET_VIOLATION: VIOLATION_NO_HELMET,
        LEGACY_OVERLOADING: VIOLATION_MOTORCYCLE_OVERLOADING,
        # Legacy parking variants -> fused terminal/parking
        LEGACY_ILLEGAL_PARKING: VIOLATION_ILLEGAL_PARKING_TERMINAL,
        LEGACY_ILLEGAL_STOPPING: VIOLATION_ILLEGAL_PARKING_TERMINAL,
        # Legacy pedestrian crossing -> Obstruction (blocking)
        LEGACY_BLOCKING_PEDESTRIAN: VIOLATION_OBSTRUCTION,
        # Legacy restricted lane -> pavement markings
        LEGACY_RESTRICTED_LANE: VIOLATION_PAVEMENT_MARKINGS,
        LEGACY_LOADING_UNLOADING: VIOLATION_ILLEGAL_PARKING_TERMINAL,
    }
    return mapping.get(viol_type)


def canonicalize_violation(viol_type: str) -> str:
    """Convert any violation name to its canonical form.
    For unknown names, returns as-is.
    """
    result = legacy_to_canonical(viol_type)
    if result is None:
        # Already canonical or unknown
        return viol_type
    return result