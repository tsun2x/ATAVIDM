# Cargo-Passenger Review Candidate Design

**Status:** Owner-approved design; implemented in repository for uploaded-video review candidates (live evaluation suppressed)

**Date:** 2026-09-10

**Canonical rule:** Unauthorized Passenger in Applicable Truck/Pickup Cargo Area

## Objective

Amend the existing rule so TAVIDM can identify plausible cargo-area passenger episodes using the existing `truck`, `pickup_truck`, and `person` detections, ByteTrack histories, and multi-frame geometric evidence. The result remains a review candidate and never automatically confirms a violation.

## Constraints

- Preserve the Flask monolith, SQLite persistence, YOLOv8m detector, ByteTrack tracker, and rule-based engine.
- Do not add a `cargo_bed`, `cargo_passenger`, `person_in_cargo_bed`, or `violation` detector class for this version.
- Preserve existing bounding-box annotations and raw datasets; they remain required inputs.
- Keep `truck` and `pickup_truck` distinct.
- Do not infer a cargo passenger from one-frame box overlap.
- Default to pending manual review for every qualifying candidate. Optional automatic triage may only prioritize a pending candidate; it must never confirm a case automatically or discard a lower-scored qualifying candidate.
- Dataset changes, training, weight promotion, deployment, and legal-policy activation remain separate approval gates.

## Considered Approaches

### A. Existing boxes plus temporal geometry — selected

Use vehicle/person boxes, tracker histories, estimated vehicle motion, a movement-relative cargo region, lower-body anchors, shared motion, stable relative position, rejection rules, and evidence selection.

This is the selected defense-ready approach because it needs no new model class or retraining and can be evaluated independently on representative CCTV.

### B. Camera-specific fixed cargo polygons — rejected as the primary method

A fixed polygon can describe a road location but cannot remain attached to a moving cargo bed. Camera annotations may provide lane direction or an applicability region, but they cannot replace the vehicle-relative cargo region.

### C. Cargo-bed detector or segmentation — deferred

A new detector could localize the bed more precisely, but it would require new annotations, training, evaluation, and promotion. Consider it only after representative CCTV evidence demonstrates that approach A is insufficient and after separate owner authorization.

## Inputs

For each processed frame, the rule consumes:

- tracked `truck` and `pickup_truck` detections;
- tracked `person` detections;
- bounding boxes, track IDs, timestamps, centroids, movement direction, speed, and detection confidence;
- recent per-track history retained within the existing tracker-aligned state lifecycle;
- configurable review-mode and association thresholds.

The rule must not treat raw detector confidence as violation confidence.

## Processing Design

### 1. Establish usable vehicle motion

Estimate the dominant movement vector from multiple centroid observations for the same vehicle track. Small detection-box jitter must not establish direction. Direction remains `UNKNOWN` when displacement is insufficient, the heading is unstable, the track was recently reacquired, or reversing behavior is suspected.

For this version, the trailing end is defined as the side opposite the stable movement vector. This is a motion-based approximation, not visual proof of vehicle front/rear orientation. A vehicle that is stationary before a usable direction history exists cannot produce a qualifying candidate.

### 2. Construct a movement-relative cargo candidate region

Build the cargo candidate region from the trailing portion of the vehicle box projected along its movement axis. The region moves and resizes with the vehicle each frame and must support horizontal, vertical, and diagonal motion.

The region is deliberately conservative and excludes as much of the leading/cab portion as practical. Enclosed vehicles or views where the load-carrying area is not visually observable remain `UNKNOWN`. Initial geometry ratios are engineering calibration values and must not be presented as legally authoritative.

### 3. Associate a person using the lower-body anchor

Represent the person's location using the bottom-center of the person box. Require that anchor to lie within the current cargo candidate region. Whole-box overlap without an anchor inside the region is insufficient.

If the lower body is cropped or substantially occluded, mark anchor evidence unavailable rather than guessing.

### 4. Require shared movement

Compare vehicle and person displacement vectors over overlapping time windows. Shared movement requires compatible direction, speed, and timing within calibrated tolerances. If both tracks are temporarily stationary, shared motion alone supplies no positive evidence; the rule must rely on prior/post-stop motion plus stable relative position.

### 5. Require stable relative position

Normalize the person's anchor within the moving vehicle box. A plausible passenger remains in approximately the same vehicle-relative area across multiple frames even as the image-space boxes translate or scale.

Rapid travel from one side of the vehicle to the other indicates a crossing pedestrian and rejects the association. Stability must persist for a configurable duration; the current one-frame overlap path is not retained.

### 6. Apply rejection conditions

Do not enqueue a candidate when any decisive rejection applies:

- overlap is brief;
- person crosses behind or beside the vehicle;
- movement direction or speed is incompatible;
- vehicle-relative position changes rapidly;
- person continues independently after the vehicle departs;
- vehicle direction is unknown or unstable;
- the load-carrying region is enclosed or not visibly observable;
- person anchor is cropped, occluded, or unavailable;
- vehicle or person track identity changes within the evidence window.

Rejected observations may produce diagnostics but must not delete or rewrite their underlying detections.

### 7. Score the complete association

Calculate a rule-evidence score from independently named factors:

- anchor containment;
- shared-motion agreement;
- relative-position stability;
- persistence duration;
- orientation stability;
- person visibility;
- vehicle/cargo-region visibility;
- detector confidence as only one supporting factor.

Missing important evidence reduces sufficiency or makes the outcome `UNKNOWN`. A high-confidence `person` box alone can never produce a high-confidence cargo-passenger conclusion.

## Evidence Selection

Maintain a bounded buffer for each vehicle-person association. Select evidence from the same continuous episode, not unrelated appearances of the same track ID.

Choose:

1. a wide frame showing the complete vehicle and surrounding road context;
2. the clearest close frame showing the person in the estimated cargo region;
3. one or more supporting frames showing continued association; and
4. a short clip spanning before, during, and after the strongest evidence.

The clearest-person score should consider visible box area, frame-boundary clipping, sharpness, detection confidence, cargo-region containment, occlusion indicators, and association stability. Largest box area alone is insufficient because perspective can enlarge a nearby pedestrian.

Evidence selection improves reviewer visibility; it does not replace the multi-frame trigger conditions.

## Review Modes

### Disabled

The rule does not evaluate or enqueue candidates.

### Manual review queue — default

Every qualifying candidate is inserted into the pending review queue. An authorized reviewer must confirm, dismiss, or mark it uncertain. This matches the current review persistence model and never confirms a violation automatically.

### Automatic high-confidence triage — optional

When evidence sufficiency and the rule-evidence score meet the configured threshold, TAVIDM may mark the pending candidate as high-priority or auto-triaged. Below-threshold qualifying candidates remain in the ordinary manual-review queue rather than being discarded. An authorized reviewer must still confirm or dismiss every candidate.

There is no automatic-confirmation mode. Switching modes must not change previously reviewed outcomes or reinterpret historical candidates.

## Reviewer Presentation

The review item should show:

- vehicle class and track ID;
- associated person track ID;
- association duration;
- evidence score and individual factors;
- unavailable or uncertain factors;
- estimated movement direction;
- overlaid cargo candidate region and lower-body anchor;
- selected evidence frames and clip;
- explicit `manual review required` status.

The reviewer must be able to confirm, dismiss, or mark the evidence uncertain without changing raw detections.

## Failure and Unknown Behavior

- Missing required model classes disables evaluation with a diagnostic.
- Missing or inadequate track history produces `UNKNOWN`.
- No usable orientation produces `UNKNOWN`.
- Codec/evidence-writing failure must not promote a candidate without reviewable evidence.
- A malformed threshold or mode must be rejected during settings validation rather than silently replaced during the run.
- Track expiry must clear association state so unrelated later objects cannot inherit it.

## Verification Requirements

### Unit and behavioral tests

- Stable vehicle/person shared motion inside the trailing region produces a review candidate after persistence.
- A one-frame overlap does not produce a candidate.
- A pedestrian crossing behind the vehicle is rejected.
- A person moving beside the vehicle but outside the cargo region is rejected.
- Unknown or unstable vehicle direction fails closed.
- Stationary evidence without prior/post-stop shared movement fails closed.
- Vertical and diagonal vehicle movement select the correct trailing region.
- Track expiry clears association state.
- Default mode enqueues every qualifying candidate as pending manual review.
- Optional automatic triage changes prioritization only and never confirms a violation.
- Below-threshold qualifying candidates are retained for ordinary manual review.
- Selected evidence belongs to the same vehicle-person episode and includes contextual and clear-person views.

### Integration validation

Run the rule through the service/video-processing boundary using an isolated temporary database and inspect persisted review state and evidence references. Confirm that rejected or unknown episodes do not create confirmed violations.

### Representative CCTV evaluation

Evaluate without retraining on owner-approved representative footage containing left/right, vertical/diagonal, stopped, approaching/departing, partially occluded, genuine cargo-passenger, and pedestrian-crossing cases. Report precision/recall only against human-reviewed ground truth; raw candidate counts are not accuracy.

## Deferred Work

- Cargo-bed detection, vehicle keypoints, or segmentation.
- Training or dataset amendments.
- Automatic case confirmation.
- Model promotion or operational deployment.
- Legal interpretation or activation of enforcement policy.

## Approval Record

The owner approved the no-new-class, manual-review-default design direction on 2026-09-10. Approval covers this design specification only and does not authorize implementation, annotation changes, training, promotion, migration, deployment, committing, or pushing.
