# TAVIDM Vehicle Classification Specification

**Project:** TAVIDM — Traffic Violation Detection and Monitoring System
**Specification:** Vehicle Classification
**Status:** ACTIVE — 12 vehicle detector classes FROZEN for Phase 2 (owner-approved); additional rule-applicability details may still be refined
**Machine-readable contract:** `config/training/class_schema.json` (schema_version 1.1.0)
**IMPORTANT:** This specification is subject to revision based on feedback, corrections, or requirements provided by the thesis adviser/panel/professor. When such feedback is explicitly provided by the user, treat the newer approved decision as superseding the previous specification. Frozen detector-class names must not be silently renamed.
**Purpose:** Define how TAVIDM classifies vehicles and how vehicle classification is consumed by the Violation Engine.

---

## 1. Authority and Scope

This document is the current authoritative specification for **vehicle classification within TAVIDM**.

It defines:

* The vehicle-classification hierarchy.
* The distinction between broad vehicle classes and more specific vehicle types.
* Vehicle categories relevant to the violation engine.
* PUV identification.
* How vehicle classification should be exposed to the rest of TAVIDM.
* How classification uncertainty should be handled.
* The relationship between vehicle classification and violation applicability.

This document does **not** define the complete violation logic.

The Violation Engine has its own specification.

Both specifications must be loaded before performing TAVIDM implementation, refactoring, debugging, or architecture work.

---

# 2. Core Classification Principle

TAVIDM should use a **hierarchical vehicle classification system** rather than treating every vehicle as a single flat class.

The system should maintain:

```text
Broad Vehicle Class
        ↓
Specific Vehicle Type
        ↓
Optional Detailed Attributes
```

Example:

```text
Vehicle
 ├── Broad Class: passenger_vehicle   (derived; not a detector label)
 │    └── Detector: car | suv_crossover | van
 │
 ├── Broad Class: public_utility_vehicle
 │    └── Detector: jeepney | uv_express_van | tricycle | piaggio | bus
 │
 ├── Broad Class: commercial_vehicle
 │    └── Detector: truck | pickup_truck
 │
 └── Broad Class: two_or_three_wheeled
      └── Detector: motorcycle | tricycle | piaggio | bicycle
```

The purpose is to allow the violation engine to ask questions such as:

> "Is this vehicle a motorcycle?"

or:

> "Is this vehicle a commercial vehicle?"

without treating broad groups themselves as YOLO detector labels.

---

# 3. Frozen Vehicle Detector Classes

TAVIDM freezes exactly **12** vehicle detector classes for Phase 2 annotation and training.

These strings are detector labels. They must not be merged, collapsed, or replaced by broad category names.

### Canonical vehicle detector roster (count = 12)

1. `car`
2. `suv_crossover`
3. `van`
4. `jeepney`
5. `uv_express_van`
6. `tricycle`
7. `piaggio`
8. `bus`
9. `truck`
10. `pickup_truck`
11. `motorcycle`
12. `bicycle`

### Required distinctions

* `pickup_truck` is a **separate detector class**, not merely a subtype attribute of `truck`.
* `van` and `uv_express_van` are separate.
* `tricycle` and `piaggio` are separate from each other and from `motorcycle`.
* `bus` and `jeepney` are separate.
* Broad keys such as `passenger_vehicle`, `public_utility_vehicle`, and `commercial_vehicle` are **derived hierarchy values**, not detector labels.
* `UNKNOWN` / `UNCERTAIN` are review states, not detector classes.
* `Private Vehicle` is a display/manuscript phrase only; it is not a detector label.
* Vehicle collision / `collision_vehicle` is out of scope for this roster and must not be added as an object class here.

### Derived hierarchy (from `class_schema.json`)

```text
passenger_vehicle        → car, suv_crossover, van
public_utility_vehicle   → jeepney, uv_express_van, tricycle, piaggio, bus
commercial_vehicle       → truck, pickup_truck
two_or_three_wheeled     → motorcycle, tricycle, piaggio, bicycle
```

---

## 3.1 Motorcycle (`motorcycle`)

Includes ordinary motorcycles relevant to traffic-violation detection.

Used by rules such as:

* No Helmet
* Substandard / Nut-Shell Helmet
* Motorcycle Overloading
* No Side Mirror

The exact applicable rules are defined by the Violation Engine specification.

---

## 3.2 Car (`car`)

General passenger vehicles such as ordinary cars and sedans.

Broad class: `passenger_vehicle`.

---

## 3.3 SUV / Crossover (`suv_crossover`)

SUVs and similar passenger vehicles are classified separately when visual evidence supports it.

Broad class: `passenger_vehicle`.

---

## 3.4 Van (`van`)

General vans are distinguishable from ordinary cars and from UV Express vehicles.

Do **not** automatically assume every van is `uv_express_van`.

Broad class: `passenger_vehicle`.

---

## 3.5 Truck (`truck`)

Dedicated large cargo / commercial truck class, distinct from `pickup_truck`.

Relevant to:

* Truck-Ban Violation (default truck-ban applicability includes `truck`)
* Unauthorized passengers in applicable cargo areas
* Obstruction
* Illegal Parking

Broad class: `commercial_vehicle`.

---

## 3.6 Pickup Truck (`pickup_truck`)

Dedicated detector class for conventional pickups with an open or covered cargo bed.

* Separate from `truck`.
* Broad class: `commercial_vehicle`.
* **Applicable** to Unauthorized Passenger in Applicable Truck/Pickup Cargo Area.
* **Not** automatically covered by Truck-Ban Violation; truck-ban class membership is explicit/configurable and defaults to `truck` only.
* Do not invent cargo-area geometry thresholds here; the cargo bed is contextual evidence for a future rule evaluation, not automatic proof of a violation.

---

## 3.7 Bus (`bus`)

Dedicated vehicle class, distinct from trucks, pickups, and jeepneys.

Broad class: `public_utility_vehicle`.

---

# 4. PUV Classification

TAVIDM must explicitly support **PUV classification** because some violation rules are applicable specifically to public utility vehicles.

At minimum, the system should recognize the following operational PUV categories:

```text
PUV (derived broad class: public_utility_vehicle)
├── jeepney
├── uv_express_van
├── tricycle
├── piaggio
└── bus
```

Operational display names such as “UV Express / Van” may appear in UI text, but the detector label is `uv_express_van`.

### Important terminology note

"Piaggio" is technically a manufacturer/brand name, but the term is commonly used operationally in the local context.

For TAVIDM:

> **Piaggio may be retained as an operational vehicle-type label.**

The system should not silently rename or remove the label simply because it is technically a brand name.

If a future legal classification is required, the system may maintain a separate legal/category field without destroying the operational label.

Example:

```text
operational_type = "Piaggio"
legal_category = <future classification>
```

---

# 5. Jeepney

Jeepney must be represented as a distinct specific vehicle type.

Example:

```text
broad_class = PUV
vehicle_type = Jeepney
```

This distinction is necessary because certain violation rules may apply specifically to jeepneys or PUV behavior.

---

# 6. UV Express Van (`uv_express_van`)

`uv_express_van` is a distinct detector class from ordinary `van`.

Example:

```text
broad_class = public_utility_vehicle
vehicle_type = uv_express_van
```

Do not classify every ordinary van as UV Express solely because it is a van.

---

# 7. Tricycle

Tricycle must be represented as a distinct specific vehicle type.

Example:

```text
broad_class = PUV
vehicle_type = Tricycle
```

This is important because tricycles have different applicability from motorcycles and ordinary passenger vehicles.

A tricycle must not simply be treated as:

```text
Motorcycle
```

for every violation rule.

The Violation Engine may still use a broader property such as:

```text
motorized_two_or_three_wheeled = true
```

if required, but the original vehicle type must remain available.

---

# 8. Piaggio

Piaggio must be represented as its own operational vehicle type because this terminology is relevant to the project's local operating context.

Example:

```text
broad_class = PUV
vehicle_type = Piaggio
```

Do not automatically collapse:

```text
Piaggio → Motorcycle
```

or:

```text
Piaggio → Tricycle
```

unless a future explicit classification decision establishes that mapping.

The system should preserve the operational classification.

---

# 9. Classification Hierarchy

The implementation should allow the system to retain multiple levels of information.

Example:

```text
Track ID: Car45

broad_class:
    Passenger Vehicle

vehicle_type:
    Sedan

is_puv:
    false

is_motorcycle:
    false

is_truck:
    false
```

Another example:

```text
Track ID: Vehicle82

broad_class:
    PUV

vehicle_type:
    Jeepney

is_puv:
    true
```

Another:

```text
Track ID: Moto31

broad_class:
    Motorcycle

vehicle_type:
    Motorcycle

is_puv:
    false
```

The exact database/schema implementation may differ, but the semantic information must remain available.

---

# 10. Vehicle Classification Is Not Violation Determination

This is critical.

TAVIDM must **not** treat vehicle classification itself as evidence of a violation.

For example:

```text
vehicle_type = Truck
```

does NOT automatically mean:

```text
Truck-Ban Violation = true
```

Likewise:

```text
vehicle_type = Jeepney
```

does NOT automatically mean:

```text
Illegal Terminal = true
```

Vehicle classification only determines **which rules may be applicable**.

The Violation Engine then evaluates behavior, context, time, tracking history, and other required conditions.

Conceptually:

```text
Vehicle Classification
        ↓
Determine Applicable Rules
        ↓
Evaluate Rule Conditions
        ↓
Violation Candidate
```

---

# 11. One Vehicle May Be Subject to Multiple Rules

Vehicle classification must not restrict a vehicle to a single violation.

Example:

```text
Motorcycle
 ├── No Helmet
 ├── Substandard Helmet
 ├── No Side Mirror
 └── Motorcycle Overloading
```

If multiple rule conditions are satisfied, multiple violation candidates may be generated for the same tracked vehicle.

One violation must not automatically exempt the vehicle from evaluation under other applicable rules.

---

# 12. Classification Confidence

Vehicle classification is an observation and therefore has uncertainty.

The system should retain classification confidence where available.

Example:

```text
vehicle_type = Motorcycle
detection_confidence = 0.94
classification_confidence = 0.91
```

Do not confuse:

```text
vehicle classification confidence
```

with:

```text
violation confidence
```

They are separate concepts.

The Violation Engine has its own confidence system.

---

# 13. Unknown / Uncertain Classification

The system must be capable of representing uncertainty.

Do not force an uncertain observation into a definitive vehicle type merely to satisfy the pipeline.

Conceptually:

```text
KNOWN
UNKNOWN
UNCERTAIN
```

or an equivalent implementation.

Example:

```text
vehicle_type = UNKNOWN
```

is preferable to incorrectly declaring:

```text
vehicle_type = Truck
```

when the camera view is insufficient.

An unknown classification may result in:

* postponing rule evaluation,
* manual review,
* reduced confidence,
* or no candidate,

depending on the applicable violation rule.

The Violation Engine specification determines the final behavior.

---

# 14. Classification Persistence With Tracking

Vehicle classification should be associated with the tracked vehicle rather than being treated as an isolated frame-level prediction.

Example:

```text
Track ID: Car45

Frame 1:
    Sedan, confidence 0.88

Frame 2:
    Sedan, confidence 0.92

Frame 3:
    Sedan, confidence 0.94

Frame 4:
    Sedan, confidence 0.91
```

The track-level classification should be able to use information accumulated across observations.

This helps prevent a single poor frame from changing:

```text
Car45 = Sedan
```

into an unrelated vehicle type.

Classification information should therefore participate in the Track Manager / Track History architecture.

---

# 15. Track Identity and Vehicle Classification

Vehicle classification is one of the features that may help with track continuity and reacquisition.

However:

> **Vehicle class alone must not be used as a unique identity.**

For example, two white sedans may both be:

```text
vehicle_type = Sedan
```

but they are not necessarily the same vehicle.

Track reacquisition may use multiple features, including:

* vehicle class,
* approximate appearance/color,
* bounding-box size,
* spatial proximity,
* movement direction,
* temporal proximity,
* visual appearance similarity,
* and other available tracking features.

Color is therefore **a supporting appearance feature**, not a unique vehicle identifier.

---

# 16. Relationship to ByteTrack

ByteTrack remains responsible for object tracking.

The classification subsystem should provide vehicle-class information to the Track Manager.

Conceptually:

```text
YOLOv8m
   ↓
Object Detection
   ↓
Vehicle Classification
   ↓
ByteTrack
   ↓
Track ID
   ↓
Track History
```

The exact implementation order may vary if required by the existing codebase, but the semantic responsibilities must remain separated.

---

# 17. Relationship to the Violation Engine

The Violation Engine consumes the classification information.

Example:

```text
Track 45
    vehicle_type = Motorcycle
    is_motorcycle = true
```

The engine can then evaluate motorcycle-specific rules.

Another:

```text
Track 82
    vehicle_type = Jeepney
    is_puv = true
```

The engine can evaluate PUV-specific behavior such as Illegal Terminal.

Another:

```text
Track 91
    vehicle_type = Truck
    is_truck = true
```

The engine can evaluate Truck-Ban Violation.

The classification subsystem must **not implement the violation itself**.

---

# 18. Do Not Hard-Code Legal Assumptions Into Classification

Vehicle classification should describe what TAVIDM observes.

It should not invent or redefine Philippine traffic-law classifications.

If a legal classification becomes necessary:

```text
operational classification
```

and:

```text
legal classification
```

should be kept conceptually separate.

The purpose of TAVIDM is to detect and monitor evidence relevant to defined violations, not to rewrite LTO or traffic laws.

---

# 19. Design-In-Progress Status

This specification is authoritative for the decisions already established in the project.

However:

> **The TAVIDM design is still in progress.**

Additional vehicle-classification details, vehicle applicability rules, and edge cases may be added later.

Do not assume that this document represents the final complete system specification.

When a later explicit project decision updates this document, the newer decision supersedes the earlier one.

---

# 20. Rules for AI Coding Agents

When working on TAVIDM:

1. Load this specification before beginning relevant work.
2. Treat the frozen decisions in this document as authoritative.
3. Do not silently reinterpret vehicle categories.
4. Do not collapse distinct operational types such as Jeepney, UV Express/Van, Tricycle, and Piaggio into generic categories without explicit authorization.
5. Do not use vehicle classification alone to determine a violation.
6. Do not silently change classification terminology.
7. Preserve vehicle classification information through tracking where practical.
8. Treat uncertain classification as uncertain rather than inventing certainty.
9. If existing code conflicts with this specification, report the conflict before changing the specification.
10. If an implementation requires a vehicle-classification rule that is marked unfinished or unspecified, do not invent the rule. Report the missing specification.
11. This specification will receive additional updates as TAVIDM design discussions continue.

---

# 21. Relationship With Violation Engine Specification

TAVIDM maintains a separate:

**VIOLATION_ENGINE_SPECIFICATION** (`docs/VIOLATION_ENGINE_SPECIFICATION.md`)

The two specifications are complementary.

```text
VEHICLE CLASSIFICATION SPEC
        ↓
"What is this vehicle?"
        ↓
VIOLATION ENGINE SPEC
        ↓
"What behavior constitutes a violation candidate?"
```

Both specifications should be loaded before implementing or modifying the TAVIDM violation-detection pipeline.

**Current status:**

> Vehicle Classification Specification — ACTIVE; **12 vehicle detector classes FROZEN** (includes separate `pickup_truck`)
> Machine-readable contract: `config/training/class_schema.json` schema_version **1.1.0** (17 object + 9 scene = 26 pilot labels)
> Violation Engine Specification — ACTIVE / DESIGN IN PROGRESS
> Design update: `docs/VIOLATION_ENGINE_DESIGN_UPDATE_2026-08-16.md`
> Canonical violation roster: 12 types; Illegal Parking and Illegal Terminal are separate
> System-truth index: `docs/SYSTEM_TRUTH_INDEX.md`
