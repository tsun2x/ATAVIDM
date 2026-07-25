"""Detection model, class, and violation-rule configuration for TAVIDM.

Project decision (team): object detector is YOLOv8m (Ultralytics), fine-tuned
on a custom traffic dataset. (Manuscript text still mixes YOLOv8s/YOLOv8m;
implementation follows the team's chosen variant.)
- Tracker: ByteTrack.
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
# be "routed to the manual review queue rather than being discarded").


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
    "loading_dwell_sec": 8.0,         # PUV stationary in no-loading zone
    "crossing_block_sec": 3.0,        # stationary on pedestrian crossing
    "truck_ban_start": "06:00",
    "truck_ban_end": "09:00",
    "lane_flow_degrees": 90.0,        # allowed travel direction in active lane
    "flow_tolerance_degrees": 60.0,   # deviation from opposite dir = counterflow
    "min_direction_px": 40.0,         # min displacement before direction is valid
}

# ---------------------------------------------------------------------------
# Violation registry
# ---------------------------------------------------------------------------
# NOTE: The manuscript defines twenty (20) finalized action-based violations
# but never enumerates the canonical list. The names below are the violations
# explicitly cited in Chapters 1-3 (Ordinances 248/576/946 mapping, Functional
# Requirements examples, and RA 10054/10666 rules). Per the manuscript:
# "the implementation of specific violations depends on their technical
# feasibility using the proposed detection pipeline."

ILLEGAL_PARKING = "Illegal Parking"
ILLEGAL_STOPPING = "Illegal Stopping"
OBSTRUCTION = "Obstruction"
COUNTERFLOW = "Counterflow Driving"
BLOCKING_PEDESTRIAN_CROSSING = "Blocking Pedestrian Crossing"
TRUCK_BAN = "Truck Ban Violation"
ILLEGAL_LOADING_UNLOADING = "Illegal Loading/Unloading"
RESTRICTED_LANE = "Restricted Lane Violation"
NO_HELMET_VIOLATION = "No Helmet Violation"
MOTORCYCLE_OVERLOADING = "Motorcycle Overloading"

# Named in the manuscript but not implementable with the described
# single-camera pipeline and available detection classes.
RECKLESS_DRIVING = "Reckless Driving"
OVERTAKING_NO_PASSING = "Overtaking in No-Passing Zone"
ILLEGAL_U_TURN = "Illegal U-Turn"
CHILD_ON_MOTORCYCLE = "Child on Motorcycle"
CARGO_BED_PASSENGERS = "Passengers in Cargo Bed"
ONE_WAY_SCHEME = "Time-Based One-Way Scheme Violation"

VIOLATION_REGISTRY: dict[str, dict] = {
    ILLEGAL_PARKING: {
        "implemented": True,
        "basis": "RA 4136; Zamboanga City Ord. 248 / 601",
        "technique": "ROI + dwell-time analysis (No Parking Zone)",
    },
    ILLEGAL_STOPPING: {
        "implemented": True,
        "basis": "Zamboanga City Ord. 248",
        "technique": "ROI + dwell-time analysis (No Parking Zone, short dwell)",
    },
    OBSTRUCTION: {
        "implemented": True,
        "basis": "Zamboanga City Ord. 248",
        "technique": "ROI + dwell-time analysis (Active Lane)",
    },
    COUNTERFLOW: {
        "implemented": True,
        "basis": "RA 4136; Zamboanga City Ord. 248",
        "technique": "Direction + trajectory analysis (Active Lane)",
    },
    BLOCKING_PEDESTRIAN_CROSSING: {
        "implemented": True,
        "basis": "Zamboanga City Ord. 248",
        "technique": "ROI + dwell-time analysis (Pedestrian Crossing)",
    },
    TRUCK_BAN: {
        "implemented": True,
        "basis": "Zamboanga City ordinances (time-based truck ban)",
        "technique": "ROI + vehicle class + time-based rule (Truck Ban Zone)",
    },
    ILLEGAL_LOADING_UNLOADING: {
        "implemented": True,
        "basis": "Zamboanga City Ord. 248 (PUV loading/unloading restrictions)",
        "technique": "ROI + vehicle class + dwell-time (No Loading/Unloading Zone)",
    },
    RESTRICTED_LANE: {
        "implemented": True,
        "basis": "Zamboanga City Ord. 576",
        "technique": "ROI + vehicle class (Restricted Lane)",
    },
    NO_HELMET_VIOLATION: {
        "implemented": True,
        "basis": "RA 10054 (Motorcycle Helmet Act)",
        "technique": "Helmet detection + vehicle-person association",
        "requires_custom_model": True,
    },
    MOTORCYCLE_OVERLOADING: {
        "implemented": True,
        "basis": "RA 4136; Zamboanga City Ord. 248 (passenger limit)",
        "technique": "Passenger counting + vehicle-person association",
    },
    RECKLESS_DRIVING: {
        "implemented": False,
        "basis": "RA 4136; Zamboanga City Ord. 248",
        "reason": "Requires behavior classification beyond the described rule features.",
    },
    OVERTAKING_NO_PASSING: {
        "implemented": False,
        "basis": "RA 4136; Zamboanga City Ord. 248",
        "reason": "Requires lane estimation; flagged as constrained in the manuscript scope.",
    },
    ILLEGAL_U_TURN: {
        "implemented": False,
        "basis": "Zamboanga City Ord. 248",
        "reason": "Requires trajectory curvature analysis not specified in Ch3.",
    },
    CHILD_ON_MOTORCYCLE: {
        "implemented": False,
        "basis": "RA 10666",
        "reason": "No child detection class in any manuscript class list.",
    },
    CARGO_BED_PASSENGERS: {
        "implemented": False,
        "basis": "RA 4136",
        "reason": "No cargo-bed detection class in any manuscript class list.",
    },
    ONE_WAY_SCHEME: {
        "implemented": False,
        "basis": "Zamboanga City Ord. 946",
        "reason": "Requires per-lane schedule direction configuration not specified in Ch3.",
    },
}

IMPLEMENTED_VIOLATIONS = tuple(
    name for name, meta in VIOLATION_REGISTRY.items() if meta["implemented"]
)

ALL_VIOLATION_TYPES = tuple(VIOLATION_REGISTRY.keys())
