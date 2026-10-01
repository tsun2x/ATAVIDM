# Motorcycle Detail Review — Design and Measurement Plan

**Status:** implemented (uploaded-video processing only), not activated
**Date:** 2026-09-28
**Scope owner:** Hermes (primary developer); Cursor/Codex review pending

## 1. Purpose and hard boundaries

For uploaded videos, group motorcycle detections by track occurrence, retain at
most two useful source-resolution frames per occurrence, run a queued second
YOLOv8 pass over the padded crops, and present helmet / side-mirror
observations in a **separate human-review queue**.

The crop scan may supply evidence or raise a review flag. It must never:

- declare, confirm, or create a violation case;
- write to `violations` or `review_queue`;
- reach `core.violation_engine` (the engine never imports the detail modules);
- turn a missed detection into proof of absence.

The existing `review_queue` table, its confirm/dismiss API, and the 12 canonical
violation types are unchanged. There is deliberately **no** confirm route on the
new queue; `human_outcome` has no `confirmed` value.

## 2. Data flow

```
process_video (uploaded video, run-scoped)
  └─ MotorcycleDetailCollector.observe(frame, tracked, ...)   # already-processed frames only
       ├─ TrackOccurrenceRegistry  (expiry / class change / ID reuse → t<id>g<gen>)
       ├─ score_frame  (visibility, sharpness, size, clipping, occlusion, viewpoint)
       ├─ ≤2 temporally separated slots per occurrence
       ├─ build_main_context → frames_json[].main_context (target track, main rider
       │    state/box, other main-detector motorcycles/riders inside the crop)
       └─ save_detail_evidence → <EVIDENCE>/detail/<run_key>/<occurrence>/{scene,crop}_fN.jpg
  └─ collector.persist() → motorcycle_detail_candidates (one row per occurrence)

MotorcycleDetailScanner (background thread, GPU-idle gated)
  └─ claim queued candidate → read crop → optional upscale → 3-class YOLOv8 predict
       ├─ map boxes back to source space (crop rect ÷ scan scale, clamped)
       ├─ associate helmet / side_mirror against the saved main-detector context
       │    (rider and motorcycles come from the main detector, never person)
       └─ write observations + overlay → scan_state = ready

Reviewer (enforcer) at /motorcycle-detail-review
  └─ record reviewed | uncertain | dismissed   (no case-confirm action)
```

## 3. Checkpoint designation and the fail-closed gate

The detail checkpoint is **designated explicitly**:

- `TAVIDM_MOTORCYCLE_DETAIL_WEIGHTS` (env or `.env`), or
- the `motorcycle_detail_weights` system setting.

Nothing defaults to a training candidate. The detail model is a **separate
crop detector**, not the main 15-class object checkpoint. `load_detail_checkpoint`
accepts only the two explicit contracts in `core/motorcycle_detail_contract.py`.
`gate.contract` remains the three-class contract for existing clients.
`gate.supported_contracts` lists both.

`md-detail-3c-v1` (`DETAIL_CONTRACT_VERSION`, identity prefix `md3c`,
`motorcycle_detail_3class`):

| ID | Class |
|---|---|
| 0 | `helmet_nut_shell` |
| 1 | `helmet_acceptable` |
| 2 | `side_mirror` |

This is the order declared by the Sept 30 draft dataset's `data.yaml`. A
three-class checkpoint cannot emit `no_helmet`. No helmet-class detection stays
`unknown`; the scanner does not infer an uncovered head.

`md-detail-4c-v1` (`DETAIL_CONTRACT_VERSION_4C`, identity prefix `md4c`,
`motorcycle_detail_4class`) keeps that order and appends one class:

| ID | Class |
|---|---|
| 0 | `helmet_nut_shell` |
| 1 | `helmet_acceptable` |
| 2 | `side_mirror` |
| 3 | `no_helmet` |

`no_helmet` is a **positive** observation of a visible uncovered head, shown to
reviewers as “Uncovered head observed”. It is review evidence that requires
human verification. It is not a confirmed no-helmet violation, not a compliance
decision, and it never creates or feeds a violation, case, or ordinary
`review_queue` row. If `no_helmet` and a helmet class plausibly describe the
same rider's head, or selected frames disagree, the summary stays `ambiguous`
and the boxes are kept. A clipped or unclear head box, a missing rider, another
rider who could own the box, or truncated/unrecorded nearby-rider context is
not a definite uncovered-head attribution.

The exploratory YOLOv8n pilots were trained with `0=side_mirror,
1=helmet_nut_shell, 2=helmet_acceptable`; that order is rejected, and IDs are
never relabeled to make a checkpoint fit.

Before the first crop is scanned, `load_detail_checkpoint` resolves the map
with `resolve_detail_contract` (contiguous IDs from 0, exact count, exact
names, exact order, task exactly `detect`):

| Condition | Result |
|---|---|
| No designation | `queued` + `scan_error=no_designated_detail_checkpoint` (no attempt consumed) |
| File missing | `queued` + `detail_checkpoint_missing` |
| Unreadable/corrupt | `queued` + `detail_checkpoint_unreadable:<err>` |
| Task metadata missing or blank | `queued` + `detail_checkpoint_task_missing` |
| Task metadata not a string | `queued` + `detail_checkpoint_task_malformed:<type>` |
| Any task other than exactly `detect` (e.g. `classify`, `segment`) | `queued` + `detail_checkpoint_wrong_task:<task>` |
| Missing / malformed / non-contiguous / duplicate / wrong count / renamed / reordered map (includes the main 15-class roster and the pilot order) | `queued` + `detail_class_map_rejected:<code>: <reason>` |
| Exact `md-detail-3c-v1` order | scan proceeds; identity = `md3c:<file>:<sha256[:12]>` |
| Exact `md-detail-4c-v1` order | scan proceeds; identity = `md4c:<file>:<sha256[:12]>` |

At inference, `UltralyticsCropPredictor` re-checks `result.names` against the
validated map and rejects out-of-range class IDs, so a mismatched result fails
the attempt instead of being relabeled. Each ready row stores the model
identity, architecture hint (e.g. `yolov8n`, or `unverified`), contract
version, and ID→name map under `observations.scan`.

Gate failures do **not** consume the retry budget because no inference was
attempted. Inference failures do: `scan_attempts` increments per claim, and a
candidate becomes `failed` once attempts are spent (`DETAIL_SCAN_MAX_ATTEMPTS=2`).

## 4. Selection policy (heuristics, unvalidated)

`core/motorcycle_detail.py` scores only frames the main pipeline already
processed and only frames containing a `motorcycle` detection with a valid
ByteTrack ID. Weights (sum = 1.0): visibility .20, sharpness .22, size .16,
clipping .14, occlusion .14, viewpoint .14.

Bounds: ≤ 2 frames/occurrence, ≤ 60 scored frames/occurrence, ≤ 6 evidence
writes/occurrence, ≤ 24 active occurrences, ≤ 120 candidates/run, ≤ 3× scan
upscale.

Disk bound: a candidate stores one source-resolution scene JPEG (quality 85) and
one padded crop JPEG (quality 92) per retained frame, so a run writes at most
240 image files. At 1080p that is roughly 60–120 MB per run; at 4K, a few
hundred MB. The directories are removed with `remove_processing_results`,
annotation-change invalidation, or permanent delete. Operators who need a
tighter bound can lower `MAX_CANDIDATES_PER_RUN` in
`core/motorcycle_detail.py`.

Crop margins: left/right 20 %, top 35 % (helmets and mirrors sit above a tight
vehicle box), bottom 12 %, unioned with the associated rider box, grown to a
64 px minimum side, clamped to the frame.

## 5. Association rules

- Attribution comes from the **main detector**, not the detail model. During
  collection each retained frame stores an additive `main_context` object in
  `frames_json` (no schema migration): the target motorcycle track, the main
  detector's rider association (state, box, track), and up to 8 other
  main-detector `motorcycle`/`rider` boxes per list that intersect the crop.
  `truncated_lists: {"motorcycles": bool, "riders": bool}` records which list
  was cut (context version 2); the generic `truncated` flag is kept as "either
  list". `person` detections are never stored and never count toward riders.
- **Incomplete context fails closed.** A list is complete only when it was
  recorded and not truncated. With the rider list incomplete, a helmet in the
  target rider's head region is left `ambiguous`
  (`nearby_rider_context_incomplete_helmet_unattributed`); with the motorcycle
  list incomplete, a mirror in the target's mounting area is left `ambiguous`
  (`nearby_motorcycle_context_incomplete_mirror_unattributed`). The other
  attribute keeps a definite state when its own list is complete. Nothing seen
  still means `unknown` / `none_visible`, never ambiguity or absence. Version 1
  rows with only `truncated: true` are read as both lists truncated; a context
  with no readable truncation flag is also treated as truncated.
- `rider` only — generic `person` never substitutes. An ambiguous or missing
  main-detector rider leaves the helmet `unknown`.
- Helmet labels attach to the rider's head region (top 45 % band, widened 8 %).
  A helmet whose centre also lies in another main-detector rider's head region
  is left unattributed (`helmet_in_other_rider_head_region_unattributed`).
  Legacy generic `helmet` never becomes "acceptable".
- `side_mirror` attaches to the upper mounting band of the motorcycle box,
  split left/right; implausibly wide boxes are flagged. A heavily overlapping
  main-detector motorcycle (IoU ≥ 0.20) makes the mirror state `ambiguous`; a
  mirror inside another motorcycle's mounting area is left unattributed
  (`mirror_in_shared_mounting_area_unattributed`).
- Rows written before `main_context` existed stay readable: the stored
  `rider_bbox` is used when present, otherwise the rider is `unrecorded`, and
  `context:nearby_motorcycles_not_recorded_for_this_row` /
  `context:nearby_riders_not_recorded_for_this_row` are added to the
  uncertainty list. Their neighbour lists are unrecorded (incomplete), so a
  helmet or mirror found there is `ambiguous`, not attributed.
- No mirror observation → `none_visible` **plus** an explicit
  `mirror:absence_not_proven_unknown` uncertainty reason. Absence is never
  proven.
- `no_helmet` (four-class contract only) attaches to the same head region as a
  helmet box, and only to the main detector's associated `rider`. It is not
  inferred when the class is absent. A contradictory helmet label on that head,
  or across the selected frames, stays `ambiguous`. A box clipped by the crop
  or the source frame is not a definite uncovered-head attribution.

## 5b. Proposed four-class dataset — not created

Do not modify the September 30 three-class derivative, its scripts, or the
historical pilot weights. That draft is not training-ready, and its test split
is not threshold evidence. WMSU stays untouched for unseen-location evaluation.

A later dataset, proposed as `md-detail-4c-v1`, would use the ordered names
above. Annotation policy (approved, not yet executed):

- label `no_helmet` only when an uncovered head is clearly visible and
  attributable to a motorcycle rider, with a box around the visible head;
- mark the example uncertain or exclude it when the head is tiny, blurred,
  occluded, clipped by the image or crop, or hard to associate with a rider;
- never derive a positive label from a missing helmet box;
- review conflicting helmet labels and hard negatives.

Before any training run or checkpoint designation: human review of source
boxes, whole-capture-group split independence, a labeled development set for
thresholds and conflict analysis, and WMSU kept held out. Latency, GPU memory,
queue volume, and reviewer workload for this four-class contract are
**unmeasured** until a representative measurement is actually run. No
checkpoint is activated by accepting the contract in application code.

## 6. State model

```
scan_state:   queued ──claim──> scanning ──ok──> ready
                    ^              |
                    └──retry───────┴──attempt<max──> failed
                    (restart recovery: stale scanning ──> queued | failed)

human_outcome: pending ──> reviewed | dismissed | uncertain
```

Transitions are explicit: `record_motorcycle_detail_review` rejects anything
outside the three review outcomes, and no adapter function in the detail
section touches `violations` or `review_queue`.

## 7. Persistence

Additive table `motorcycle_detail_candidates` +
`database/migrations/011_motorcycle_detail_review.sql` (idempotent DDL only).
No existing table, column, constraint, or index is altered.
`dedup_key = "<run scope>|<occurrence_key>|<selector_version>"` makes a retry
update the same row. Evidence lives under `<EVIDENCE>/detail/<run_key>/` and is
served only through the authenticated detail evidence route; the existing
`/static/evidence/` block in `app.py` still prevents public static access.

Retention: `remove_processing_results`, annotation-change invalidation, and
permanent delete all remove detail rows and the run-scoped detail directories.

## 8. Access control

| Route | Auth |
|---|---|
| `GET /motorcycle-detail-review` | enforcer (page) |
| `GET /api/motorcycle-detail-review` | login |
| `GET /api/motorcycle-detail-review/<id>/evidence/{scene,crop,overlay}` | enforcer |
| `POST /api/motorcycle-detail-review/<id>/outcome` | enforcer |
| `POST /api/motorcycle-detail-review/scan-now` | enforcer, GPU-idle only |

`/confirm`, `/case`, and `/violation` sub-paths do not exist (404).


## 9. Development evaluation set — MISSING GATE

A separate labeled development evaluation set was **not** assembled. Reasons:

1. The prepared V9.1 dataset under
   `dataset/derived/tavidm_v9_1_provisional_20260927` must not be altered, and
   its `test/` split is the already-evaluated split, which must not be used for
   tuning.
2. WMSU is held out for unseen-location Gold v3 evaluation.
3. `dataset/raw` contains only three authorized clips and no helmet/mirror
   labels, so no labeled source exists to copy from without inventing labels.

Annotation protocol for when the gate is cleared (separate location and source
group from both V9.1 and WMSU):

- unit of annotation = a **track occurrence** (`t<track_id>g<generation>`), not
  a frame;
- labels per occurrence: helmet form ∈ {acceptable, nut_shell, none_visible,
  unknown}, mirror sides ∈ {both, left_only, right_only, none_visible, unknown},
  rider association ∈ {associated, ambiguous, none};
- `none_visible` only when a human reviewer can see both mounting areas clearly;
  otherwise `unknown`;
- ≥ 30 occurrences per class stratum, ≥ 3 distinct locations, ≥ 2 source groups
  per location, and no source group crossing the train/eval boundary;
- two annotators per occurrence, disagreements adjudicated to `uncertain`;
- released as YOLO-format labels plus an occurrence-level CSV so the selector,
  the crop scan, and the association rules can be scored separately.

## 10. Measurement status

Measured locally with `scripts/measure_motorcycle_detail.py` (see the
completion report for the numbers): full-frame and hybrid per-frame latency
(median/p95), throughput, peak VRAM/RAM, crops and bytes per track, candidates
per hour of footage, scan failures, and queue-wait bounds.

Unmeasured (and why):

- reviewer time and review outcomes — need the labeled development set;
- helmet/mirror and association precision/recall — need the same set;
- crop-pass latency — measured only when a detail checkpoint is designated; with
  no designation the scan fails closed and the numbers are reported as
  `unmeasured` rather than guessed.

Training-validation timing is explicitly **not** used as an application
inference benchmark.
