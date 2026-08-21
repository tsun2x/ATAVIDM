# TAVIDM Violation Engine Specification

**Project:** TAVIDM — Traffic Violation Detection and Monitoring System
**Specification:** Violation Engine
**Status:** DESIGN IN PROGRESS — CURRENT DECISIONS ARE AUTHORITATIVE; ADDITIONAL RULE DETAILS WILL BE ADDED LATER
**Authority:** Current project decisions approved by the project owner. Later explicit project decisions, including revisions based on professor/panel feedback, supersede earlier decisions.

---

# 1. Purpose

This document defines the current authoritative design for the TAVIDM violation-detection engine.

The Violation Engine is responsible for determining whether tracked vehicle behavior provides sufficient evidence to create a **violation candidate**.

It must not simply convert an object detector's prediction directly into a violation.

The engine must combine:

* object detection,
* vehicle classification,
* object tracking,
* track history,
* vehicle state,
* movement,
* scene context,
* temporal behavior,
* rule-specific conditions,
* confidence,
* exceptions,
* evidence quality,
* and manual review.

---

# 2. Core Violation-Detection Philosophy

TAVIDM must follow:

```text
Detection
    ↓
Tracking
    ↓
Vehicle Classification
    ↓
Track History
    ↓
Vehicle State / Motion
    ↓
Scene Context
    ↓
Rule Evaluation
    ↓
Violation Candidate?
    ↓
Confidence Evaluation
    ↓
Automatic Candidate / Manual Review / Ignore
    ↓
Evidence Capture
    ↓
License Plate Detection / OCR
    ↓
Violation Record
```

The fundamental principle is:

> **Detection alone does not equal violation.**

A violation candidate requires the rule-specific combination of observations, behavior, context, and temporal evidence.

---

# 3. Relationship With Vehicle Classification

The separate:

`docs/VEHICLE_CLASSIFICATION_SPECIFICATION.md`

defines what vehicle the system believes it is observing.

This document defines what behavior may constitute a violation.

The relationship is:

```text
Vehicle Classification
        ↓
Determine Applicable Rules
        ↓
Violation Engine
        ↓
Evaluate Behavior
```

Vehicle classification must not be redefined inside individual violation rules.

For example:

```text
vehicle_type = Truck
```

does not automatically mean:

```text
Truck Ban Violation = true
```

Likewise:

```text
vehicle_type = Jeepney
```

does not automatically mean:

```text
Illegal Terminal = true
```

---

# 4. Current Violation Roster

TAVIDM currently has **12** canonical violation categories.

**Illegal Parking** and **Illegal Terminal** are **separate** canonical violation types. They must not be merged into a single `Illegal Parking / Illegal Terminal` category.

They may share upstream vehicle-state and contextual analysis, but they remain **separate violation rules and separate records**.

### Illegal Parking (distinct)

General vehicle parking behavior. Applicability is based on parking-like behavior, location, duration, driver presence/absence, and contextual evidence. Do not invent numerical thresholds for this rule.

### Illegal Terminal (distinct)

Specifically concerns PUV terminal-like behavior. Applies to applicable PUV types such as Jeepney, UV Express / Van, Tricycle, and Piaggio. Consider prolonged stopping, passenger boarding/alighting, repeated passenger activity, location, traffic interference, and terminal-like behavior. Do not invent numerical thresholds for this rule.

### Canonical list (count = 12)

1. Illegal Parking
2. Obstruction
3. Counterflow
4. Truck-Ban Violation
5. No Helmet
6. No Side Mirror
7. Motorcycle Overloading
8. Disregarding Traffic Sign
9. Failure to Follow Road/Pavement Markings
10. Illegal Terminal
11. Unauthorized Passenger in Applicable Truck/Pickup Cargo Area
12. Substandard / Nut-Shell Helmet

This is the current canonical roster.

Do not add, remove, merge, or rename a violation without an explicit project decision.

---

# 5. Multiple Violations Per Vehicle

A vehicle is not exempt from other rules merely because it has already committed one violation.

Multiple violation candidates may be generated for the same track.

Example:

```text
Track ID: Motorcycle45

Possible candidates:
    No Helmet
    Substandard Helmet
    No Side Mirror
    Motorcycle Overloading
```

Each rule must be evaluated independently when applicable.

---

# 6. Three-Valued Context Logic

Context observations should support three states:

```text
TRUE
FALSE
UNKNOWN
```

Do not automatically interpret:

```text
UNKNOWN = FALSE
```

If the camera cannot determine whether a relevant contextual condition exists, the engine should preserve the uncertainty.

The final effect of `UNKNOWN` is determined by the applicable rule and confidence/review policy.

---

# 7. Vehicle State

Multiple violation rules require the system to understand vehicle behavior.

The conceptual vehicle-state vocabulary includes:

```text
MOVING
SLOW_MOVING
TEMPORARILY_STOPPED
STATIONARY
PARKED
UNKNOWN
```

The exact mathematical/state-transition implementation remains subject to further design discussion.

Do not invent final state thresholds if they are not specified.

In particular:

> Small tracking jitter must not automatically count as meaningful vehicle movement.

---

# 8. Track History

A tracked vehicle should maintain relevant history while the track remains active.

Example:

```text
Track ID: Car45

position history
velocity / movement history
direction history
vehicle classification
classification confidence
scene/context observations
rule observations
candidate violations
```

Track history should be used to distinguish momentary behavior from sustained behavior.

---

# 9. Track Loss and Reacquisition

When a vehicle temporarily disappears, TAVIDM should attempt to determine whether a newly detected object is the same vehicle.

Reacquisition should not rely on color alone.

Potential features include:

* vehicle class,
* approximate color/appearance,
* bounding-box size,
* spatial proximity,
* movement direction,
* expected position,
* temporal proximity,
* visual appearance similarity,
* and other available tracking/appearance features.

Conceptually:

```text
Existing Track
      ↓
Temporary disappearance
      ↓
Candidate detection
      ↓
Compare appearance + motion + position + class + timing
      ↓
Likely same vehicle?
 ┌──────────────┴──────────────┐
 YES                           NO
 ↓                             ↓
Reacquire existing track       New track
```

### Current status

The exact:

* loss timeout,
* reacquisition window,
* similarity thresholds,
* scoring formula,

are **NOT YET FROZEN**.

Do not invent final values.

---

# 10. Violation Confidence vs Detection Confidence

TAVIDM must distinguish two different confidence concepts.

## 10.1 Detection Confidence

This represents confidence that the detector has correctly detected an object or relevant feature.

Examples:

```text
vehicle detection confidence
helmet detection confidence
traffic sign detection confidence
license plate detection confidence
```

## 10.2 Violation Confidence

This represents confidence that the **complete rule evaluation** provides sufficient evidence for a violation candidate.

Violation confidence may incorporate:

* detector confidence,
* tracking confidence,
* classification confidence,
* temporal consistency,
* context completeness,
* rule-condition satisfaction,
* evidence quality,
* and other rule-specific factors.

Therefore:

```text
YOLO confidence =/= violation confidence
```

A highly confident truck detection does not automatically mean a highly confident truck-ban violation.

---

# 11. Per-Violation Confidence Configuration

Each violation must have its own configurable confidence thresholds.

Do not use a single universal violation-confidence threshold for every violation.

The system should support configuration conceptually like:

```text
Violation:
    Obstruction

Detection Confidence:
    configurable

Violation Confidence:
    configurable
```

and separately:

```text
Violation:
    Counterflow

Detection Confidence:
    configurable

Violation Confidence:
    configurable
```

Different violations have different levels of uncertainty and therefore may require different thresholds.

---

# 12. Operator Confidence Controls

The operator-facing UI should expose configurable confidence controls per violation.

At minimum, each violation should have:

```text
Detection Confidence Threshold
Violation Confidence Threshold
```

The UI should allow the operator to adjust these thresholds.

The exact final UI implementation may differ, but the semantic configuration must remain available.

Example:

```text
COUNTERFLOW

Detection Confidence
[────────●──────] 0.80

Violation Confidence
[──────────●────] 0.90
```

The UI should clearly distinguish:

> **Detection confidence** — confidence in the detected object/feature.

from:

> **Violation confidence** — confidence in the rule-level violation determination.

---

# 13. Automatic Candidate / Manual Review

Violation confidence should support a configurable decision structure.

Conceptually:

```text
High confidence
    ↓
Automatic violation candidate

Medium / uncertain confidence
    ↓
Manual review

Low confidence
    ↓
Ignore / insufficient evidence
```

The exact review thresholds remain configurable and may be revised following validation.

Do not treat arbitrary example values as final legal or scientific thresholds.

---

# 14. No Universal "Legal" Confidence Number

There is no universal confidence value that means:

> "This violation is legally proven."

Confidence thresholds are **engineering parameters** for TAVIDM.

They should eventually be tuned and evaluated using validation data.

The system must not claim that:

```text
0.85 confidence = legally proven violation
```

Instead:

```text
confidence threshold
    =
system decision parameter
```

---

# 15. Evidence Capture

When a violation candidate is generated, TAVIDM should preserve evidence surrounding the event.

The current evidence-window design is:

```text
6 seconds before violation
        +
entire observed violation duration
        +
3 seconds after violation
```

Therefore:

```text
Evidence Start
    = violation start - 6 seconds

Evidence End
    = violation end + 3 seconds
```

Example:

If the violation lasts 4 seconds:

```text
6 sec before
+
4 sec violation
+
3 sec after
=
13-second evidence clip
```

The evidence may be represented by:

* video clip,
* screenshot/frame,
* or both,

depending on the implementation.

---

# 16. Evidence Must Include Context

The evidence should not only show the violating object.

Where possible, the evidence should preserve enough scene context to understand:

* surrounding vehicles,
* road position,
* traffic flow,
* traffic signs,
* pavement markings,
* traffic enforcers,
* obstacles,
* intersections,
* passenger activity,
* and other relevant environmental context.

This is especially important for contextual violations such as:

* Obstruction,
* Illegal Parking,
* Illegal Terminal,
* Counterflow,
* Traffic Sign violations,
* Pavement-Mark violations.

---

# 17. License Plate Processing

License plate detection and OCR occur **after a violation candidate/evidence event is identified**.

Conceptually:

```text
Violation Candidate
        ↓
Evidence Capture
        ↓
Identify violating vehicle
        ↓
License Plate Detection
        ↓
OCR / Plate Reading
        ↓
Attach plate information to violation record
```

ALPR is an **identification component**.

It is not the component that determines whether the traffic violation occurred.

Poor or unreadable plate recognition must not invalidate an otherwise valid violation observation.

---

# 18. Illegal Parking

## Current rule philosophy

Illegal Parking is a **behavioral/contextual rule**, not a simple stationary-object detector.

A vehicle remaining stationary does not automatically constitute illegal parking.

The engine should consider:

* vehicle state,
* duration,
* location / road position,
* driver presence or absence,
* surrounding traffic,
* traffic queue,
* traffic-control circumstances,
* obstacles,
* traffic enforcer involvement,
* and other relevant context.

Illegal Parking remains a separate canonical type from Illegal Terminal. They may share upstream vehicle-state and context, but they produce separate rules and separate records.

Conceptually:

```text
Vehicle stationary
        ↓
Parking-like behavior?
        ↓
Context evaluation
        ↓
No obvious legitimate traffic-related reason
        ↓
Persistence
        ↓
Violation candidate
```

### Starting engineering heuristic

An initial value of approximately:

```text
10 seconds
```

may be used during development.

**This value is NOT frozen.**

It is a system heuristic for distinguishing sustained parking-like behavior from momentary stopping.

It must not be represented as a legal definition of parking.

---

# 19. Obstruction

Obstruction should not be reduced to:

```text
Vehicle stopped = obstruction
```

The engine should evaluate whether the vehicle is actually obstructing traffic and whether there is an apparent legitimate reason for its state.

Relevant context may include:

* other vehicles around it,
* whether it is within/near an intersection,
* whether vehicles are stopped behind it,
* whether there is an object or vehicle ahead,
* whether there is a road incident/collision,
* whether a traffic enforcer is controlling/stopping it,
* whether traffic conditions explain the stoppage,
* whether the vehicle is moving slowly,
* whether the vehicle is temporarily stopped,
* and other visible scene context.

Conceptually:

```text
Vehicle stopped / slow
        ↓
Potentially obstructive position?
        ↓
Context evaluation
        ↓
Legitimate explanation?
        ↓
Persistence
        ↓
Violation candidate
```

### Starting engineering heuristic

An initial value of approximately:

```text
3 seconds
```

may be used.

**This value is NOT frozen.**

It is a behavioral persistence heuristic, not a legal grace period.

---

# 20. Illegal Terminal

Illegal Terminal is specifically dependent on applicable **PUV classification**.

Relevant operational PUV types include:

* Jeepney,
* UV Express / Van,
* Tricycle,
* Piaggio,

subject to the final applicability matrix.

A PUV merely being stationary is not sufficient.

The system should look for terminal-like behavior, such as:

* sustained / prolonged stopping,
* passengers entering,
* passengers leaving,
* repeated boarding/alighting,
* prolonged passenger activity,
* location,
* traffic interference,
* and other contextual evidence consistent with terminal-like operation.

Illegal Terminal remains a separate canonical type from Illegal Parking. They may share upstream vehicle-state and context, but they produce separate rules and separate records.

Conceptually:

```text
Applicable PUV
      ↓
Sustained stop
      ↓
Passenger activity
      ↓
Terminal-like behavior
      ↓
Context evaluation
      ↓
Violation candidate
```

### Starting engineering heuristic

An initial value of approximately:

```text
15 seconds
```

may be used during development.

**This value is NOT frozen.**

Time alone must not trigger Illegal Terminal.

The intended logic is:

```text
PUV
+
sustained stopping
+
terminal-like passenger behavior
+
supporting context
=
candidate
```

---

# 21. Truck-Ban Violation

Truck Ban is fundamentally **time-based**.

The system should compare the applicable truck-ban schedule against the observed truck's timestamp.

The truck-ban rule must not be converted into a generic "truck is present = violation" rule.

The engine must also maintain vehicle-state awareness so that a truck that was already parked or otherwise legitimately stationary before the restricted period is not automatically flagged merely because it remains visible when the ban period begins.

Conceptually:

```text
Truck detected
      ↓
Is truck applicable?
      ↓
Check current time
      ↓
Is current time inside configured truck-ban period?
      ↓
Check vehicle state/history
      ↓
Was the truck already parked / stationary?
      ↓
Rule decision
```

The truck vehicle-state information exists specifically to prevent false positives such as:

```text
Parked truck visible in frame
+
ban period begins
=
FALSE TRUCK-BAN VIOLATION
```

The exact truck-ban schedule and final state-transition behavior are configuration/legal-input dependent and must not be invented.

---

# 22. Counterflow

TAVIDM identifies a potential counterflow violation when the tracked vehicle's movement is opposite to the established traffic direction for the roadway area it occupies.

Current rule:

```text
Vehicle movement direction
        ↓
Compare against established roadway traffic direction
        ↓
Opposing movement
        ↓
Movement persists
        ↓
Counterflow candidate
```

### Current temporal threshold

The initial threshold is:

```text
4 seconds
```

of persistent opposing movement.

This is an engineering heuristic and remains configurable.

---

# 23. Counterflow: Vehicle Enters Frame Already in Opposing Flow

A vehicle may enter the camera's field of view after it has already begun moving against traffic.

TAVIDM cannot assume that the vehicle's entire prior movement is known.

For such cases, the system may use an initially shorter observation threshold of:

```text
2 seconds
```

to identify a potential counterflow candidate.

However, this case should be treated as having less complete history and may require manual review depending on confidence/evidence.

The system must not pretend that it observed movement that occurred outside the camera's field of view.

---

# 24. Counterflow Exceptions / Ambiguous Maneuvers

Opposing movement is not automatically a violation in every circumstance.

Potential ambiguous maneuvers include:

* overtaking,
* U-turns,
* turning movements,
* entering or exiting a property,
* avoiding an obstruction,
* temporary maneuvering,
* traffic-enforcer direction,
* emergency situations,
* other legitimate roadway maneuvers.

Such cases may require:

```text
manual review
```

rather than automatic confirmation.

The exact handling of each maneuver remains subject to further specification.

---

# 25. No Helmet

No Helmet is primarily a visual detection rule.

For an applicable motorcycle/rider:

```text
Motorcycle/rider detected
        ↓
Helmet absent
        ↓
Sufficient detection confidence
        ↓
Violation candidate
```

This rule should not require detection of tiny certification stickers.

Helmet presence and helmet type are separate concepts.

---

# 26. Substandard / Nut-Shell Helmet

TAVIDM includes a dedicated:

> **Substandard / Nut-Shell Helmet**

violation.

The intended approach is to classify the **helmet form/type**, rather than attempting to inspect tiny certification stickers in the video.

The system should distinguish the relevant nut-shell/substandard helmet appearance from compliant helmet forms.

Potential helmet categories may include visual forms such as:

* nut-shell/substandard helmet,
* full-face helmet,
* full-face with visor,
* other supported helmet types.

The exact dataset/class taxonomy and final classification model remain subject to further specification and validation.

Do not invent certification rules or legal definitions based solely on visual helmet appearance.

---

# 27. No Side Mirror

No Side Mirror is primarily a visual detection/classification rule.

For an applicable vehicle:

```text
Applicable vehicle
        ↓
Required side mirror not visibly present
        ↓
Sufficient evidence
        ↓
Violation candidate
```

If the relevant side of the vehicle cannot be seen clearly, the system should be capable of returning:

```text
UNKNOWN / INSUFFICIENT EVIDENCE
```

rather than automatically declaring that the mirror is absent.

---

# 28. Motorcycle Overloading

Motorcycle Overloading should evaluate the number of occupants associated with the motorcycle.

Conceptually:

```text
Motorcycle
      ↓
Detect rider/passengers
      ↓
Determine occupant count
      ↓
Compare with applicable rule
      ↓
Violation candidate
```

The exact allowed occupant configuration and edge-case interpretation require the applicable project/legal specification and must not be invented.

---

# 29. Disregarding Traffic Sign

The current architecture is:

```text
Traffic sign detected
        ↓
Identify sign type
        ↓
Determine which traffic flow the sign serves
        ↓
Determine vehicle's traffic flow
        ↓
Does sign apply to this vehicle?
        ↓
Evaluate vehicle behavior
        ↓
Does behavior conflict with sign?
        ↓
Context / exception check
        ↓
Violation candidate
```

### Critical applicability rule

Do **not** use the simplistic rule:

```text
Sign on right side of image
=
sign applies to vehicle
```

Instead:

> **The sign must serve the traffic flow in which the tracked vehicle is traveling.**

This allows the system to handle:

* camera orientation,
* opposing traffic,
* divided roads,
* intersections,
* signs serving different traffic streams,
* and other scene arrangements.

### Speed-limit signs

Speed-limit violations are explicitly **excluded from this rule for the current TAVIDM scope**.

Do not implement speed-limit violation detection as part of this rule.

The reason is that reliable speed estimation would require additional scene/camera calibration and mathematical estimation that is outside the current scope.

### Exact supported sign types

The complete list of supported signs and their individual behavioral triggers is **NOT YET FROZEN**.

Do not invent the final list or individual sign logic.

---

# 30. Failure to Follow Road / Pavement Markings

The current architecture is:

```text
Pavement marking detected
        ↓
Identify marking
        ↓
Determine applicable roadway area
        ↓
Track vehicle relative to marking
        ↓
Did vehicle behavior conflict with marking?
        ↓
Check legitimate maneuver/context
        ↓
Violation candidate / review / ignore
```

The system must not implement:

```text
Vehicle crossed solid line
=
automatic violation
```

because legitimate maneuvers may require crossing or interacting with markings.

Potential contextual exceptions may include:

* U-turn,
* entering a building/property,
* exiting a building/property,
* avoiding an obstruction,
* traffic control,
* emergency circumstances,
* other legitimate roadway maneuvers.

The exact marking taxonomy and individual trigger conditions are **NOT YET FROZEN**.

Do not invent them.

---

# 31. Unauthorized Passenger in Applicable Truck/Pickup Cargo Area

The system should detect whether a person is occupying an applicable cargo area where such occupancy is prohibited by the project's defined rule.

Conceptually:

```text
Applicable truck/pickup
        ↓
Person detected
        ↓
Person spatially associated with cargo area
        ↓
Sustained / sufficient evidence
        ↓
Violation candidate
```

The exact vehicle applicability, cargo-area geometry, and exception handling remain subject to further specification.

---

# 32. Contextual Exception Philosophy

TAVIDM must avoid treating every visually similar movement as a violation.

Where a legitimate explanation is visually apparent, the engine should be capable of preventing an automatic candidate.

Examples:

```text
Vehicle stops
+
traffic queue
=
likely legitimate traffic condition
```

```text
Vehicle moves across marking
+
entering property
=
potential legitimate maneuver
```

```text
Vehicle moves against flow
+
U-turn maneuver
=
ambiguous / review
```

```text
Vehicle stopped
+
traffic enforcer directing it
=
likely legitimate controlled stop
```

Context is therefore part of the violation engine rather than an optional afterthought.

---

# 33. Candidate vs Confirmed Violation

The computer-vision system should distinguish between:

```text
Violation Candidate
```

and:

```text
Confirmed Violation
```

A high-confidence automated result may generate a candidate for downstream processing, but ambiguous cases may be routed to manual review.

The system should not represent a computer-vision prediction as unquestionable legal adjudication.

---

# 34. Manual Review

Manual review exists for cases where the system detects potentially violating behavior but the available evidence is insufficient for safe automatic classification.

Manual review may be triggered by:

* medium violation confidence,
* incomplete track history,
* ambiguous maneuver,
* insufficient scene visibility,
* uncertain vehicle classification,
* conflicting contextual evidence,
* poor evidence quality,
* uncertain sign applicability,
* uncertain pavement-marking applicability,
* track reacquisition uncertainty.

Manual review is an intentional part of the architecture.

---

# 35. Event Deduplication

The engine must prevent a single continuous violation from generating an uncontrolled stream of duplicate violation records.

Conceptually:

```text
Track 45
   ↓
Same rule continuously satisfied
   ↓
ONE violation event
   ↓
Evidence window
   ↓
Event ends
```

The exact deduplication window/state machine remains subject to further implementation specification.

Do not create one database violation record for every frame satisfying the rule.

---

# 36. Evidence and Violation Event Lifecycle

A violation event should conceptually follow:

```text
OBSERVATION
    ↓
RULE CONDITIONS BEGIN
    ↓
PERSISTENCE / CONTEXT SATISFIED
    ↓
VIOLATION CANDIDATE
    ↓
CONFIDENCE EVALUATION
    ↓
AUTOMATIC / MANUAL REVIEW
    ↓
EVIDENCE FINALIZATION
    ↓
PLATE PROCESSING
    ↓
VIOLATION RECORD
```

The evidence event should preserve the temporal context around the violation as specified above.

---

# 37. Important Non-Goals

The Violation Engine must not:

* invent traffic laws,
* redefine LTO requirements,
* claim that a confidence percentage proves legal guilt,
* assume every stationary vehicle is illegally parked,
* assume every PUV stop is an illegal terminal,
* assume every truck during a ban period committed a violation without state/context evaluation,
* assume every opposing movement is counterflow,
* treat every marking crossing as a violation,
* use one violation to exempt a vehicle from other rules,
* treat unknown context as automatically false,
* or invent unspecified rule behavior.

---

# 38. Design-In-Progress / Future Changes

This specification is **not permanently final**.

Additional details will be added as TAVIDM design discussions continue.

In particular, the following areas remain intentionally unfinished:

### TBD / NOT YET FROZEN

* Exact vehicle-state thresholds.
* Track-reacquisition similarity scoring formula.
* Exact Obstruction persistence value.
* Exact Illegal Parking persistence value.
* Exact Illegal Terminal persistence value.
* Closed list of “specific parking/stopping restrictions” and per-sign behavioral triggers (initial sign *scope* is in the 2026-08-16 design update).
* Pavement markings outside the double-solid / single-solid / solid+broken working rules in the 2026-08-16 design update.
* Detailed counterflow maneuver exceptions.
* Overloading edge cases beyond “maximum two occupants actually on the motorcycle.”
* Cargo-area geometry algorithm (exposed-cargo applicability is in the 2026-08-16 design update).
* Final confidence default values.
* Final manual-review numeric bands.
* Final event-deduplication behavior.
* Final applicability matrix for all vehicle types.

Do not invent these missing details.

When the project owner provides an explicit decision, update the specification accordingly.

**Design update:** `docs/VIOLATION_ENGINE_DESIGN_UPDATE_2026-08-16.md` records later owner direction. Open conflicts in that addendum are not silently resolved here.

---

# 39. Professor / Panel Feedback

The specification may be revised based on feedback, corrections, or requirements provided by the thesis professor, adviser, or panel.

When the project owner explicitly approves a change based on such feedback:

```text
New approved decision
        ↓
Supersedes previous specification
        ↓
Update this document
        ↓
Future implementation follows updated specification
```

Do not independently reinterpret professor/panel feedback.

The project owner will determine how feedback is incorporated into the specification.

---

# 40. Rules for AI Coding Agents

When working on TAVIDM:

1. Load this specification before performing relevant work.
2. Also load `docs/VEHICLE_CLASSIFICATION_SPECIFICATION.md`.
3. Treat both documents as complementary project authorities.
4. Follow frozen decisions exactly.
5. Do not invent unspecified violation rules.
6. Do not silently change violation definitions.
7. Do not treat detection confidence as violation confidence.
8. Do not use one global violation threshold when a per-violation threshold is specified.
9. Preserve `UNKNOWN` when evidence is insufficient.
10. Do not automatically convert contextual uncertainty into a violation.
11. Do not modify the specification merely to make existing code easier to implement.
12. If existing code conflicts with the specification, report the conflict.
13. If implementation requires an unspecified rule, identify the missing decision instead of inventing one.
14. Do not modify code merely because a specification was provided unless implementation is explicitly requested.
15. The specifications are living documents and may be revised following explicit project decisions and professor/panel feedback.
16. Later explicit approved project decisions supersede earlier decisions.

---

# 41. Required Pre-Implementation Behavior

Before implementing a TAVIDM violation-engine task, the AI coding agent must:

```text
Load Vehicle Classification Specification
        ↓
Load Violation Engine Specification
        ↓
Check relevant frozen decisions
        ↓
Check for TBD / unfinished dependencies
        ↓
Check existing implementation
        ↓
Identify conflicts
        ↓
Only then plan implementation
```

If a required behavior is marked:

```text
TBD
NOT YET FROZEN
UNSPECIFIED
```

the agent must not invent the missing behavior.

It should report the dependency and request/await the project's explicit decision.

---

# 42. Current Status

**Vehicle Classification Specification:** ACTIVE / DESIGN IN PROGRESS

**Violation Engine Specification:** ACTIVE / DESIGN IN PROGRESS

**Design update (2026-08-16):** `docs/VIOLATION_ENGINE_DESIGN_UPDATE_2026-08-16.md` — current design direction for filled TBD items.

**Roster (owner-confirmed 2026-08-16):** **12** canonical types. Illegal Parking and Illegal Terminal are **separate**. Do not merge them into `Illegal Parking / Illegal Terminal`. Production registry in `core/detection_config.py` must match this roster exactly; the fused label is legacy-only.

**Implementation status:** Specifications are being established before full violation-engine implementation. Production code must not be changed from a specification drop unless implementation is explicitly requested.

Additional specification updates are expected.

The current specification must therefore be treated as the **current source of truth**, not as an immutable final legal or academic definition.
