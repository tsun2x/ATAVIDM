# TAVIDM Vehicle Classification Specification

**Project:** TAVIDM — Traffic Violation Detection and Monitoring System
**Specification:** Vehicle Classification
**Status:** DESIGN IN PROGRESS — CURRENT DECISIONS ARE AUTHORITATIVE; ADDITIONAL RULE DETAILS WILL BE ADDED LATER
**IMPORTANT:** This specification is subject to revision based on feedback, corrections, or requirements provided by the thesis adviser/panel/professor. When such feedback is explicitly provided by the user, treat the newer approved decision as superseding the previous specification. Do not assume the current specification is permanently final.
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
 ├── Broad Class: Motorcycle
 │    └── Specific Type: Motorcycle
 │
 ├── Broad Class: PUV
 │    └── Specific Type: Jeepney
 │
 └── Broad Class: PUV
      └── Specific Type: UV Express / Van
```

The purpose is to allow the violation engine to ask questions such as:

> "Is this vehicle a motorcycle?"

or:

> "Is this vehicle a PUV?"

without requiring every rule to understand every possible detailed vehicle type.

---

# 3. Required Vehicle Classes

TAVIDM must support, at minimum, the following operational vehicle categories.

## 3.1 Motorcycle

Includes ordinary motorcycles relevant to traffic-violation detection.

Used by rules such as:

* No Helmet
* Substandard / Nut-Shell Helmet
* Motorcycle Overloading
* No Side Mirror

The exact applicable rules are defined by the Violation Engine specification.

---

## 3.2 Car / Passenger Vehicle

General passenger vehicles such as ordinary cars and sedans.

Example:

```text
Car
Sedan
Passenger vehicle
```

This category may be expanded later if needed.

---

## 3.3 SUV / Crossover

SUVs and similar passenger vehicles may be classified separately when useful to the system.

They should still belong to an appropriate broader passenger-vehicle category for rule applicability.

---

## 3.4 Van

General vans should be distinguishable from ordinary cars when practical.

A van may additionally be classified as:

```text
UV Express / Van
```

when the available visual/contextual evidence supports the PUV classification.

Do not automatically assume every van is a UV Express vehicle.

---

## 3.5 Truck

Truck is a dedicated vehicle class.

This classification is particularly important for:

* Truck-Ban Violation
* Unauthorized passengers in applicable cargo areas
* Obstruction
* Illegal Parking

The truck class must remain distinguishable from ordinary passenger vehicles.

---

## 3.6 Bus

Bus is a dedicated vehicle class.

It should remain distinguishable from trucks and ordinary passenger vehicles.

---

# 4. PUV Classification

TAVIDM must explicitly support **PUV classification** because some violation rules are applicable specifically to public utility vehicles.

At minimum, the system should recognize the following operational PUV categories:

```text
PUV
├── Jeepney
├── UV Express / Van
├── Tricycle
└── Piaggio
```

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

# 6. UV Express / Van

UV Express / Van should be represented as a distinct operational type when sufficient evidence exists.

Example:

```text
broad_class = PUV
vehicle_type = UV Express / Van
```

Do not classify every ordinary van as UV Express solely because it is a van.

PUV classification should rely on the available visual/contextual evidence and the confidence framework.

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

> Vehicle Classification Specification — ACTIVE / DESIGN IN PROGRESS
> Violation Engine Specification — ACTIVE / DESIGN IN PROGRESS
> Design update: `docs/VIOLATION_ENGINE_DESIGN_UPDATE_2026-08-16.md`
> Canonical roster: 12 types; Illegal Parking and Illegal Terminal are separate
> Additional specification updates are expected.
