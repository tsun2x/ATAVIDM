# Violation Engine Amendment Audit and Plan

**Status:** Implementation authorized and executed for Gates 1–9 (evaluation prep only for Gate 9); cargo-passenger amendment owner-approved

**Date:** 2026-09-10

**Scope:** Preserve all 12 canonical violations and amend their inputs, configuration, annotation, and review behavior without changing the YOLOv8m + ByteTrack + rule-engine architecture.

## Authorization Boundary

This document records an audit and proposed design amendments. It does not authorize implementation, database migration, dataset modification, annotation work, training, weight promotion, deployment, staging, committing, or pushing.

The cargo-passenger subsection is owner-approved as a design direction. Other amendments remain proposed until the owner approves them.

## Current-System Audit

### Verified architecture and contracts

- The registry contains exactly 12 canonical violations.
- Illegal Parking and Illegal Terminal remain separate canonical rules.
- The engine consumes YOLO detections, ByteTrack motion fields, operator-drawn zones, persisted rule parameters, and per-run enabled-rule snapshots.
- `obstruction_dwell_sec`, `crossing_block_sec`, and `loading_dwell_sec` are already configurable system settings.
- The current editor and annotation parser support polygon point lists for six zone types only.
- The current annotation path cannot preserve per-lane directions, arrows, threshold lines, semantic pavement markings, traffic-sign applicability, passenger-activity regions, or other structured metadata.
- Partial rules create review outcomes or fail closed where required evidence is unavailable.

### Confirmed gaps

1. Violation toggles do not show their related dwell values or link directly to the parameter controls.
2. Server-side settings validation does not enforce the numeric constraints shown by the HTML controls.
3. Counterflow uses one global flow angle instead of a direction attached to each lane.
4. No Helmet accepts `person` as well as `rider` for motorcycle association.
5. Motorcycle Overloading accepts `person` as well as `rider`.
6. No Side Mirror records presence/unknown but never emits a visibility-gated manual-review candidate.
7. Mirror visibility capability is treated as available without a real visibility/orientation observation.
8. Traffic-sign rule logic has no normal annotation-editor persistence path.
9. Pavement-marking logic has no normal semantic line-annotation persistence path; the restricted-lane polygon is only a proxy.
10. Illegal Terminal requires passenger-activity and for-hire flags that normal processing does not presently produce.
11. Cargo-passenger logic uses a fixed image-right rear-region approximation and does not account for vehicle movement direction.
12. Substandard / Nut-Shell Helmet uses the same `person`-or-`rider` association as No Helmet and was omitted from the initial rider-only gate.
13. Video processing passes loaded model classes into capability evaluation, but live-stream processing does not.
14. Rule functions receive per-frame derived motion fields rather than the underlying `TrackHistory.points` required for longer association analysis.
15. Every emitted video/live event is currently inserted into the review queue; temporal evidence buffering begins its linked episode after emission and does not select pre-emission best frames for a person/vehicle association.

## Global Amendment Principles

- Violations remain rule-engine outputs, never YOLO object classes.
- Missing visibility, direction, context, or model capability produces `UNKNOWN` or suppression rather than an invented violation.
- Partial/uncertain rules remain manual-review candidates.
- Raw detector confidence is not violation confidence.
- Existing annotations, database rows, APIs, and historical run snapshots remain readable.
- New annotation structures must be versioned and backward-compatible inside the existing annotation storage unless a separately approved database change becomes necessary.
- Dataset changes, training, model promotion, and operational activation remain separate gates.

## Proposed Amendments by Rule

### Obstruction — proposed

- Keep independent persisted settings for active-lane obstruction dwell and pedestrian-crossing obstruction dwell.
- Show both current values alongside the Obstruction toggle.
- Provide a direct link to anchored controls under Detection and Rule Parameters.
- Disabling the rule must not reset either saved value.
- Add atomic server-side validation for finite, owner-approved ranges.

### Counterflow and general traffic-flow annotation — proposed

- Allow multiple identified active-lane polygons.
- Attach one permitted-direction arrow to each lane.
- Derive the engine angle from arrow start/end coordinates.
- Permit human-facing labels such as incoming, outgoing, left-to-right, and right-to-left without making those labels separate algorithms.
- Evaluate a vehicle against the lane containing it.
- Ambiguous overlap between conflicting lanes produces `UNKNOWN`.
- Retain the global `lane_flow_degrees` value only as a legacy-annotation fallback.

### No Helmet — proposed

- Associate motorcycles with `rider` detections only.
- Do not substitute nearby `person` detections.
- Disable automatic evaluation with a diagnostic if the loaded model lacks `rider` or required helmet capability.
- Preserve acceptable, nut-shell, no-helmet, and unknown observation states.

### Substandard / Nut-Shell Helmet — proposed

- Use the same rider-only motorcycle association contract as No Helmet.
- Do not substitute `person` when `rider` is unavailable.
- Require the corresponding rider and nut-shell-helmet capability or fail closed with a diagnostic.

### No Side Mirror — proposed

- One detected mirror proves only that at least one mirror is present.
- A missing mirror detection never proves absence by itself.
- Two usable mirror observations produce no candidate.
- One mirror with only one observable side remains `UNKNOWN`.
- One mirror or no mirrors across multiple frames where both mounting areas are clearly observable creates a manual-review candidate.
- Cropped, blurred, occluded, distant, or unsuitable views remain `UNKNOWN`.
- No automatic-confirmation mode is permitted.

### Motorcycle Overloading — proposed

- Count only `rider` detections associated with the motorcycle.
- Require more than two stable rider associations for the configured persistence period.
- Disable automatic evaluation when `rider` is unavailable rather than falling back to `person`.

### Disregarding Traffic Sign — proposed

- Prefer operator annotation of permanent signs for fixed CCTV.
- Each sign records a stable ID, sign type, applicable lane IDs, applicability polygon, threshold line, prohibited movement, and applicable direction.
- Initially support no-entry through annotated threshold crossing in a prohibited direction.
- Keep no-left-turn, no-right-turn, no-U-turn, and overtaking review-only until the tracker-to-trajectory interface and maneuver classifiers are explicitly designed and tested. The existing unset `performed_*` flags are not an acceptable normal input path.
- Do not require a new sign-detection training run for the fixed-camera path.

### Failure to Follow Road/Pavement Markings — proposed

- Add semantic polyline/narrow-polygon and threshold-line annotation tools.
- Store marking type, applicable lanes, prohibited side, and permitted crossing direction.
- Initially support double-solid crossing, restricted-lane entry, and prohibited-direction threshold crossing.
- Keep single-solid, solid/broken, temporary, faded, obstruction-avoidance, and enforcer-directed cases review-only until policy and exception rules are approved.

### Illegal Terminal — proposed

- Keep `loading_dwell_sec` configurable and show it beside the rule toggle with a settings link.
- Require an applicable PUV, annotated activity/boarding region, dwell, and multi-frame passenger activity.
- Treat disappearance near a vehicle boundary as possible boarding only when approach trajectory and geometry agree.
- Treat emergence from the boundary as possible alighting only when temporal and geometric evidence agree.
- Add an explicit terminal-context producer that converts track history plus annotated activity regions into a tri-state passenger-activity observation consumed by the rule. Deprecate the currently unset per-detection flags as a normal pipeline dependency while retaining fail-closed compatibility handling.
- Mere person/vehicle box overlap is insufficient.
- Keep the outcome manual-review only.

### Unauthorized Passenger in Applicable Truck/Pickup Cargo Area — approved design amendment

Use the existing `truck`, `pickup_truck`, and `person` boxes; do not add another detector class for this version.

Required sequence:

1. Resolve a stable movement vector from the vehicle's ByteTrack history.
2. Treat insufficient movement, unstable heading, reacquisition, or suspected reversing as `UNKNOWN`.
3. Construct a conservative cargo candidate region from the trailing portion opposite the stable movement vector.
4. Require the person's bottom-center/lower-body anchor inside that region.
5. Require compatible person/vehicle movement.
6. Require stable normalized person position relative to the vehicle across multiple frames.
7. Reject brief crossings, incompatible motion, rapid relative movement, independent pedestrian continuation, unsuitable visibility, unknown orientation, and track-identity changes.
8. Retain a bounded evidence buffer and choose a wide contextual frame, the clearest person frame, supporting frames, and a short clip from the same episode.
9. Do not select evidence using box size alone; include visibility, clipping, sharpness, confidence, containment, occlusion, and association stability.
10. Default to the existing pending manual-review queue for every qualifying candidate. An optional high-confidence triage mode may prioritize qualifying candidates above a configured rule-evidence threshold, but it may never automatically confirm a violation or discard lower-confidence qualifying candidates.

The complete approved cargo design is specified in [Cargo-Passenger Review Candidate Design](2026-09-10-cargo-passenger-review-design.md).

## Shared Scene-Annotation Amendment — proposed

Introduce a versioned scene-annotation contract that can represent:

- legacy polygon zones;
- multiple identified lanes;
- per-lane flow arrows;
- threshold lines;
- semantic pavement markings;
- traffic signs and applicability metadata; and
- passenger-activity regions.

Legacy polygon-only annotations and templates must load unchanged. Reading an annotation must never silently rewrite it. Unknown fields must not be silently discarded.

### Breaking parser and consumer boundary

The current `parse_zones_json` / `dumps_zones` and JavaScript `parseZones` contracts silently retain only these exact polygon keys:

- `no_parking`
- `active_lane`
- `pedestrian_crossing`
- `truck_ban_zone`
- `loading_unloading`
- `restricted_lane`

The versioned contract must replace that destructive normalization path for structured documents while retaining an explicit legacy polygon projection. It must cover every consumer in the same compatibility design: `app.py` video-annotation, zone-template, and camera write routes that currently call `dumps_zones`; the upload annotation wizard; the Settings template editor and raw `editCameraZones` textarea; video processing; `LiveStreamWorker`; `build_geometry_profile`; `frame_annotate.annotate_frame`; and the live overlay path.

The proposed v2 document has one top-level `schema_version: 2` and arrays of identified objects: `zones`, `lanes`, `flow_arrows`, `threshold_lines`, `markings`, `signs`, and `activity_regions`. Every object has a stable unique `id`, a supported `type`, and validated image coordinates. Cross-object references such as `lane_ids` must resolve within the same document. The legacy projection exposes the six current zone keys to unchanged consumers during transition; multiple v2 lanes must never be collapsed into one ambiguous polygon for a rule that needs lane identity.

### Annotation projection interface

Before contextual rules consume the new document, add one explicit projection layer:

`SceneAnnotation -> RuleSceneContext`

`RuleSceneContext` must expose legacy zones, identified lanes and their flow vectors, supported sign annotations, marking geometry, threshold lines, and passenger-activity regions. Video and live processing must call the same projection. Rule functions must not read unvalidated raw annotation JSON or rely on sign/marking entries being placed in global rule parameters.

The target engine interface is `evaluate_detection_rules(..., scene: RuleSceneContext, history: TrackHistoryView | None, ...)`. Raw `zones` remains a compatibility input only while legacy callers migrate; `supported_signs` and `marking_geometry` are removed from the normal global-parameter path. The projection must reject conflicting structured and legacy inputs rather than selecting one silently.

### Track-history and association interface

Expose a bounded read-only track-history view to rules that require trajectory or association analysis. It must preserve tracker expiry, contain timestamped centroid observations, and avoid giving rule code mutable ownership of `TrackState`. Cargo evidence selection requires a separate pre-emission association buffer; the existing `TemporalEvidenceBuffer` remains responsible for candidate-linked pre/post-roll after emission unless deliberately extended through a tested interface.

Line-crossing rules consume consecutive bottom-center vehicle anchors from this history. For an oriented line from point A to point B, compute the signed cross product for the prior and current anchors. A crossing requires a sign change plus intersection of the motion segment with the finite annotated line; ordinary zone overlap is not a line crossing.

For pavement markings, `prohibited_from` is `left`, `right`, or `both` relative to the stored A-to-B line orientation. The prior signed side identifies the approach side. A candidate requires crossing from a prohibited side, stable track direction, and the marking's applicable lane. Double-solid uses `both`; single/solid-broken policies remain review-only until approved. Restricted-lane entry remains an outside-to-inside transition through zone-membership hysteresis, not line overlap.

For no-entry, the threshold stores a prohibited crossing direction vector. A candidate requires finite-line intersection and a positive motion-vector projection in the prohibited direction above the minimum-displacement gate. Crossing in the reverse/permitted direction, moving parallel to the line, endpoint jitter, or insufficient history does not trigger.

### Review-persistence interface

Define review disposition before persistence rather than assuming every future mode means the same thing. The approved safe baseline is: qualifying cargo and mirror candidates are inserted as pending manual review and never auto-confirmed. Optional automatic triage may change priority/status metadata only after its persistence representation is approved; it must not bypass `_persist_event`, live review insertion, or authorized human confirmation.

The existing `mirror_roi_visibility=True` and `cargo_roi_association=True` capability flags must be retired. Capability must be derived from actual observation producers and may be true, false, or unknown per applicable track/episode; absent producers fail closed.

## Delivery Plan

### Gate 1: Settings presentation and validation

- Add behavioral/API tests for displayed dwell values, anchored settings links, disabled-rule value preservation, and atomic rejection of invalid values.
- Implement the minimum UI and server-side validation changes.
- Verify settings save/reload against an isolated temporary database.

### Gate 2: Versioned scene-annotation contract

- Add parser/serializer contract tests covering legacy polygons and new structured objects.
- Introduce the backward-compatible annotation representation without changing operational data.
- Verify round-trip preservation, malformed input rejection, stable identifiers, and reference integrity.
- Implement and test the shared `SceneAnnotation -> RuleSceneContext` projection for both video and live processing.

### Gate 3: Shared track-history and trajectory primitives

- Add a bounded immutable `TrackHistoryView` projection from `TrackState` with timestamped centroids and tracker-aligned expiry.
- Add focused tests for finite-line intersection, oriented-side transitions, prohibited-direction projection, endpoint jitter, insufficient motion, and outside-to-inside restricted-lane entry.
- Make the same history projection available to uploaded-video and live processing without exposing mutable tracker state.

### Gate 4: Annotation editor expansion

- Add executable UI tests for polygons, multiple lanes, arrows, threshold lines, markings, signs, and activity regions.
- Implement creation, selection, movement, deletion, save, and reload behavior.
- Browser-verify rendered geometry and saved/reloaded state before claiming completion.

### Gate 5: Per-lane Counterflow evaluation

- Change Counterflow to select the identified lane containing the tracked vehicle and compare against that lane's projected flow vector.
- Produce `UNKNOWN` when stable membership overlaps lanes with conflicting flow directions.
- Retain `lane_flow_degrees` only for legacy annotations that contain one legacy `active_lane` and no v2 lane direction.
- Test multiple same-direction lanes, opposing lanes, boundary ambiguity, missing arrows, and the legacy fallback.

### Gate 6: Rider-only motorcycle rules and capability parity

- Add failing tests proving nearby `person` objects cannot trigger No Helmet, Substandard / Nut-Shell Helmet, or Motorcycle Overloading.
- Require `rider` capability for all three rules and add the missing overloading capability gate.
- Pass loaded model-class capability into both video and live evaluation paths.
- Verify COCO or baseline models without `rider` fail closed with diagnostics.

### Gate 7: Contextual review rules

- Add focused tests for mirror visibility states, annotated traffic-sign behaviors, pavement crossings, and Illegal Terminal passenger activity.
- Keep uncertain results review-only or `UNKNOWN`.
- Verify persisted review outcomes through service boundaries using an isolated temporary database.
- Implement the terminal-context producer from annotated activity regions and bounded track history; do not depend on normally unset `terminal_passenger_activity` / `apparent_for_hire` fields.
- For `jeepney`, `tricycle`, and `autorickshaw`, the producer may return passenger activity from validated boarding/alighting trajectories. For `bus`, require the same explicit activity evidence. `van` remains `UNKNOWN` until the owner approves a non-model source for apparent public/for-hire context; plate appearance alone is never sufficient.
- Implement only no-entry sign crossing in this gate using the Gate 3 finite-line/prohibited-direction contract. Other maneuver signs remain review-only.
- Retire unconditional mirror/cargo capability flags and connect capability to real observation availability.

### Gate 8: Approved cargo-passenger amendment

- Implement only from the approved cargo design.
- Reuse the Gate 3 track-history accessor; add the cargo-specific pre-emission association evidence buffer and explicit review-disposition interface required by the approved rule.
- Then add behavioral tests for direction-relative geometry, shared movement, stable relative position, rejection conditions, evidence selection, review modes, and track expiry.
- Run service/video-processing integration checks with an isolated temporary database.
- Do not train or add a class as part of this gate.
- Uploaded-video evidence must include the approved multi-frame/clip package. Live cargo evaluation remains suppressed unless the owner includes live evidence parity in this release; still-only live insertion cannot claim the approved evidence completeness.

### Gate 9: Representative CCTV evaluation

- Use separately authorized, human-reviewed representative footage.
- Evaluate genuine positives and hard negatives across camera viewpoints and movement directions.
- Report accuracy only against reviewed ground truth, not raw candidate counts.
- Treat dataset modification, training, promotion, and deployment as separate owner decisions.

## Verification Baseline from the Audit

- Static source inspection confirmed the current settings flow, annotation limitations, rule prerequisites, and cargo heuristic.
- A focused system-Python test run produced 106 passes and one codec-dependent integration failure.
- The failure occurred because the available OpenCV environment could not open VP80, VP90, or H.264/OpenH264 output; it did not demonstrate a violation-rule assertion failure.
- The repository virtual environment lacked `pytest`; no dependency was installed.
- Real CCTV, browser annotation behavior, and current-model rider/helmet/mirror performance were not verified.

## Git and Data Safety

The workspace was already dirty during the audit. All user-owned modifications and untracked files must be preserved. Any future release must stage explicit named paths only and requires separate approval before committing or pushing.

## Owner Decisions Still Required

1. Approve numeric ranges for configurable dwell values.
2. Approve No Side Mirror as permanently manual-review only.
3. Approve fixed-camera sign annotations as the primary sign-information source.
4. Approve rider-only fail-closed behavior when `rider` is unavailable.
5. Choose whether reusable camera templates are authoritative with per-video overrides or every video owns an independent full scene annotation.
6. Approve rider-only fail-closed behavior for Substandard / Nut-Shell Helmet as well as No Helmet and Motorcycle Overloading.
7. Decide how `van` public/for-hire context is established without introducing a detector class or inferring it from plate appearance alone.
8. Confirm that video annotations and live-camera configurations must adopt the same versioned scene contract in the same release.
9. Approve no-entry threshold crossing as the only initially executable sign behavior, with turn/U-turn/overtaking behaviors remaining review-only until their trajectory contracts are approved.
10. Approve a persistence representation for optional high-confidence triage; until then, implement pending manual review only and add no priority schema.
11. Decide whether cargo multi-frame/clip evidence must support live cameras in this release. Until approved, uploaded video is the complete evidence path and live cargo evaluation remains suppressed.

Cargo-passenger design direction is already approved and is not included in these unresolved decisions.
