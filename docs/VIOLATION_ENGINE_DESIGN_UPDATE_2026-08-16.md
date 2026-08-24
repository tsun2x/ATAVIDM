# TAVIDM Violation Engine — Design Update 2026-08-16

**Project:** TAVIDM — Traffic Violation Detection and Monitoring System
**Type:** Design / specification addendum
**Date:** 2026-08-16
**Status:** ACTIVE / DESIGN IN PROGRESS — NOT PERMANENTLY FROZEN
**Authority:** Project-owner design update after the initial Vehicle Classification and Violation Engine specifications. Later explicit project decisions, including professor/panel feedback, supersede this addendum.
**Implementation:** Documentation only. Production code must not be changed from this addendum.

This addendum records newly stated design direction and **does not silently resolve conflicts** with `docs/VEHICLE_CLASSIFICATION_SPECIFICATION.md` or `docs/VIOLATION_ENGINE_SPECIFICATION.md`.

Where this update fills a prior TBD item without contradiction, treat the new decision as current design direction.

Where this update contradicts an earlier frozen decision, **the conflict is open** until the project owner explicitly chooses.

**Exception:** Conflict B.1 (roster fusion) was **resolved** by explicit project-owner decision on 2026-08-16: Illegal Parking and Illegal Terminal remain separate. See §B.1.

**Vehicle detector labels (supersession):** Any earlier wording that treated `uv_express_van` or `piaggio` as canonical YOLO vehicle detector classes is **superseded** by `config/training/class_schema.json` schema_version **1.2.0** (11-class roster: single `van`; `autorickshaw` by body form; Piaggio brand metadata only; UV Express / for-hire as contextual metadata). See `docs/VEHICLE_CLASSIFICATION_SPECIFICATION.md` and §A.3 Illegal Terminal below.

---

## A. Newly stated design direction (no roster rewrite)

These items refine or fill prior TBD language. They are current design direction unless a later explicit decision supersedes them.

### A.1 Pipeline and context

Intended pipeline:

```text
YOLOv8m
→ ByteTrack
→ Vehicle Classification
→ Track / Vehicle State
→ Scene / Context Analysis
→ Violation Engine
→ Confidence Evaluation
→ Candidate / Manual Review
→ Evidence Manager
→ ALPR / OCR when appropriate
→ Violation Record
```

The engine must consume upstream outputs rather than reinvent detection/tracking.

The system is context-aware. Context may include movement/state, trajectory, surrounding vehicles, pedestrians, traffic enforcers, intersections, road edges/shoulders, signs, pavement markings, obstacles, incidents, passenger activity, occlusion, classification, and temporal persistence.

**Hazard lights are contextual evidence only. They do not automatically make parking legal.**

### A.2 Vehicle state

Minimum vocabulary:

* MOVING
* SLOW_MOVING
* STOPPED / TEMPORARILY_STOPPED
* STATIONARY
* PARKED
* UNKNOWN

Movement comes from tracked coordinate displacement over time. Small jitter is not meaningful movement.

**PARKED is not merely “coordinates did not change.”** Parking requires contextual evidence such as position near the road shoulder/proper edge, prolonged inactivity, parking-like behavior, and surrounding road context.

Exact numeric state-transition thresholds remain unspecified.

### A.3 Rule philosophies (behavioral)

* **Obstruction:** contextual traffic-interference. Stopped/stationary is not automatically obstruction. Legitimate explanations (enforcer direction, pedestrians crossing, incident ahead, traffic queue, driver still inside, etc.) should prevent an automatic candidate.
* **Illegal Parking:** parking-like behavior using stationarity, location, duration, apparent driver absence, driver exiting, and whether the stop is a temporary loading/unloading maneuver. A visible driver exit is strong evidence; absence of a visible driver is not automatic proof.
* **Illegal Terminal:** PUV-applicable visual types under schema **1.2.0**: Jeepney, Van (with contextual public/for-hire evidence — not a separate `uv_express_van` detector class), Tricycle, and Autorickshaw. **Superseded:** earlier “UV Express / Van” and “Piaggio” detector wording. UV Express / for-hire status is contextual metadata only; Piaggio is brand metadata — classify three-wheel vehicles by body form (`tricycle` = motorcycle+sidecar; `autorickshaw` = integrated body). Stop alone is insufficient. Requires context + time + passenger activity (repeated/extended boarding/alighting, terminal-like behavior).
* **Truck Ban:** remains time-based and configurable. Not a general behavioral truck rule. Parked/stationary presence during ban hours must not automatically become a violation.
* **Counterflow:** opposing movement vs established roadway direction, persisting beyond a configurable threshold. Initial **4 seconds**. Defined shorter observation **2 seconds** for the already-opposing-on-entry edge case; that is not “every 2-second opposing movement is a violation.” Ambiguous maneuvers → manual review.
* **No Helmet / Substandard Helmet:** for each person actually on the motorcycle (rider and rear passenger): no helmet → No Helmet; helmet present → classify form; nut-shell/substandard → Substandard Helmet; other helmet → no helmet violation. Categories: NO HELMET, NUT-SHELL / SUBSTANDARD, ACCEPTABLE / OTHER HELMET. No approval-sticker recognition. Dataset/model selection is Pre-Phase 2.
* **No Side Mirror:** PRESENT vs ABSENT vs UNKNOWN (not observable). Do not assume an unseen mirror is absent or that an occluded mirror is present.
* **Motorcycle Overloading (now specified):** maximum **two occupants** actually ON / riding the motorcycle. Do not count pedestrians or people merely adjacent. Entering-frame with two occupants still counts those two. Association uses spatial relationship, shared movement, relative position, persistence, and tracks.
* **Cargo-area passenger:** primarily exposed cargo areas (pickup, dump, flatbed, other applicable exposed-cargo trucks). Person in the cabin is not a cargo-area passenger. Geometry details remain unspecified.
* **Multiple violations:** one candidate does not exempt other rules.

### A.4 Traffic signs (initial supported scope)

STOP signs and speed-limit signs are **excluded**.

Initial supported signs feed the single canonical violation **Disregarding Traffic Sign** (do not create a category per sign):

Movement restrictions: No Entry, No Overtaking, No Left Turn, No U-Turn.

Parking/stopping restrictions: No Parking, No Stopping Anytime, specific parking/stopping restrictions, location-specific restrictions such as fire-hydrant parking restrictions where visibly signed.

Sign applicability is by the traffic flow the sign serves (driver’s perspective), not “sign on the right of the image.”

Use Philippine traffic-sign standards. Do not invent new traffic laws.

Individual behavioral triggers beyond this architecture remain to be specified.

### A.5 Pavement markings (working rules)

Use applicable Philippine/LTO road-marking guidance. Do not invent laws.

* **Double solid yellow/white:** crossing prohibited; overtaking prohibited. Either → candidate (subject to contextual exceptions).
* **Single solid yellow/white:** crossing permitted; overtaking prohibited. Crossing alone is not automatically a violation; prohibited overtaking → candidate.
* **Solid + broken:** vehicle’s side of the marking matters. Broken-line side: passing/overtaking and crossing permitted where otherwise appropriate. Solid-line side: overtaking prohibited; crossing follows applicable Philippine/LTO rule.

Do not treat bounding-box line-touch as sufficient. Ambiguous marking/maneuver → manual review.

### A.6 Track loss, reacquisition, history

* Initial temporary lost-track window: **5 seconds**. Time alone is insufficient.
* Occlusion example (Car45 behind BusB) is design direction: occluded/lost state, reacquisition, predicted-exit vs uncertainty, then terminate after timeout. No indefinite ghost tracks.
* License plate is the strongest identity confirmation when readable. Do not blindly merge two similar vehicles. Prefer internal reacquisition hypotheses over permanent IDs such as `Car45.1`.
* Hybrid history: ordinary tracks keep short-term history then lightweight metadata; potential/confirmed violations retain richer trajectory/evidence. Do not keep unlimited raw coordinates for every ordinary vehicle.

Reacquisition **scoring formula** remains unspecified.

### A.7 Evidence, ALPR, confidence, review, workload

* Evidence buffers: **6 seconds before** + violation period + **3 seconds after**. These are evidence-buffer durations, not violation thresholds.
* ALPR/OCR is not continuous on every frame. Trigger after candidate/evidence extraction. Do not invent plate characters.
* Detection confidence ≠ violation confidence. Prefer per-violation operator sliders. No universal hard-coded “correct” confidence. Initial defaults later via validation.
* Manual review includes borderline violation confidence, uncertain class, uncertain reacquisition, ambiguous maneuver, conflicting context, unclear markings/signs, uncertain helmet type, insufficient mirror visibility, plausible legitimate explanation.
* Three-way outcome: clearly not a violation → no candidate; clearly supported → candidate; plausible but uncertain → manual review.
* Operator UI is not assumed to run all expensive CV. Dedicated processing machine vs operator workstation is the intended split. Exact deployment topology is not frozen.

### A.8 Time concepts must not be conflated

Counterflow persistence, track-loss window, evidence buffers, truck-ban schedule, and parking/terminal duration are different parameters.

---

## B. Conflicts

### B.1 Canonical roster — RESOLVED (2026-08-16, project owner)

**Decision:** Illegal Parking and Illegal Terminal are **separate** canonical violation types. Do **not** merge them into `Illegal Parking / Illegal Terminal`.

They may share upstream vehicle-state and contextual analysis. They remain **separate violation rules and separate records**.

The fused wording that appeared in this design update’s original §23 is **superseded**.

**Canonical roster (count = 12):**

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

No other canonical type was added, removed, renamed, or merged by this decision.

Numerical thresholds for Illegal Parking and Illegal Terminal were **not** invented or frozen by this decision.

Existing production code may still use a fused label until implementation is explicitly authorized.

### B.2 Pipeline order vs classification spec — OPEN / implementation-flexible

Vehicle Classification spec §16:

```text
YOLOv8m → Object Detection → Vehicle Classification → ByteTrack → Track ID → Track History
```

This update:

```text
YOLOv8m → ByteTrack → Vehicle Classification → …
```

The classification spec already allows implementation order to vary if semantic responsibilities stay separated. Current code runs ByteTrack inside Ultralytics `model.track()` together with detection. Do not treat order as a frozen rewrite of either spec without an explicit decision.

### B.3 Vehicle-state labels — minor, compatible pending wording

Prior VE spec: `TEMPORARILY_STOPPED` (no `STOPPED` alias).

This update: `STOPPED / TEMPORARILY_STOPPED`.

Treat as the same conceptual state unless the owner later splits them.

### B.4 Pavement “solid line” wording — refinement, not silently overwriting

Prior VE spec: do not implement `Vehicle crossed solid line = automatic violation`.

This update: double-solid crossing is a candidate; single-solid crossing is not automatically a violation.

Treat the new double/single/broken working rules as filling the prior “taxonomy TBD” **only for those three families**. Other markings remain unspecified. Do not encode a generic “any solid line” automatic violation.

### B.5 Classification coverage vs cargo / pickup types — resolved for pickup

**Owner decision (2026-08-21):** `pickup_truck` is a separate frozen vehicle detector class, distinct from `truck`. Both map under the derived `commercial_vehicle` hierarchy. Cargo-area passenger applicability includes both `truck` and `pickup_truck`. Truck-ban applicability remains explicit/configurable and defaults to `truck` only.

Dump / flatbed subtypes beyond `truck` vs `pickup_truck` remain unspecified; do not invent additional detector labels without an owner decision.

### B.6 Sign list still partly unspecified

“Specific parking/stopping restrictions” and fire-hydrant restrictions “where visibly signed” are not a closed taxonomy. Individual sign behavioral triggers remain unfinished.

### B.7 Occupancy vs prior TBD

Prior VE spec §38 listed motorcycle overloading occupancy as not frozen.

This update states **maximum two occupants** as the applicable project rule.

Treat **max two on-motorcycle occupants** as current design direction (explicit owner statement in this update). It does not define every edge case (child in arms, sidecar, etc.). Those remain unspecified.

### B.8 Track-loss timeout vs prior TBD

Prior VE spec: loss timeout not frozen.

This update: initial **5-second** temporary lost-track window, with context beyond time.

Treat **5 seconds** as the current initial heuristic, not a legal or final scoring formula.

### B.9 Parking / obstruction / terminal persistence numbers

This update does **not** restate the earlier (also not-frozen) heuristics:

* Illegal Parking ~10 seconds
* Obstruction ~3 seconds
* Illegal Terminal ~15 seconds

Those remain unfrozen heuristics from the base VE spec. This update emphasizes context over time-alone. Do not treat either set as a legal grace period.

### B.10 Codebase vs all specs (unchanged; not resolved by this addendum)

Existing `core/detection_config.py` must use the 12-type roster with separate Illegal Parking and Illegal Terminal identifiers. The fused parking/terminal string is legacy-only. Existing `core/violation_engine.py` remains largely zone/dwell-based for several rules; this addendum does not invent unfinished thresholds.

---

## C. Still unspecified (do not invent)

* Numeric MOVING / SLOW_MOVING / STATIONARY / PARKED transition thresholds
* Reacquisition similarity scoring formula
* Exact Obstruction / Illegal Parking / Illegal Terminal persistence values
* Closed list of “specific parking/stopping restrictions”
* Per-sign behavioral trigger tables
* Pavement markings outside the three working families above, including crossing treatment on the solid side of solid+broken pending LTO wording in implementation
* Sidecar / child / other overloading edge cases
* Cargo-area geometry algorithm
* Default detection/violation confidence numbers
* Final manual-review numeric bands
* Final event-deduplication state machine
* Full vehicle-type applicability matrix
* Processing-machine vs operator-workstation deployment topology

---

## D. Documents this addendum complements

* `docs/VEHICLE_CLASSIFICATION_SPECIFICATION.md`
* `docs/VIOLATION_ENGINE_SPECIFICATION.md`
