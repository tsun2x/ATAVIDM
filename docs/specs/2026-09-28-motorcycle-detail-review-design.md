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
       └─ save_detail_evidence → <EVIDENCE>/detail/<run_key>/<occurrence>/{scene,crop}_fN.jpg
  └─ collector.persist() → motorcycle_detail_candidates (one row per occurrence)

MotorcycleDetailScanner (background thread, GPU-idle gated)
  └─ claim queued candidate → read crop → optional upscale → YOLOv8 predict
       ├─ map boxes back to source space (crop rect ÷ scan scale, clamped)
       ├─ associate rider (never person) / helmet / side_mirror
       └─ write observations + overlay → scan_state = ready

Reviewer (enforcer) at /motorcycle-detail-review
  └─ record reviewed | uncertain | dismissed   (no case-confirm action)
```

## 3. Checkpoint designation and the fail-closed gate

The detail checkpoint is **designated explicitly**:

- `TAVIDM_MOTORCYCLE_DETAIL_WEIGHTS` (env or `.env`), or
- the `motorcycle_detail_weights` system setting.

Nothing defaults to a training candidate. Before the first crop is scanned,
`load_detail_checkpoint` validates the checkpoint's ID→name map with
`validate_object_class_map(..., require_roster=True)`:

| Condition | Result |
|---|---|
| No designation | `queued` + `scan_error=no_designated_detail_checkpoint` (no attempt consumed) |
| File missing | `queued` + `detail_checkpoint_missing` |
| Unreadable/corrupt | `queued` + `detail_checkpoint_unreadable:<err>` |
| Reordered / renamed / truncated / legacy roster | `queued` + `detail_class_map_rejected:<summary>` |
| Exact 15-class ID order | scan proceeds; identity = `YOLOv8m:<file>:<sha256[:12]>` |

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

- `rider` only — generic `person` never substitutes.
- Helmet labels attach to the rider's head region (top 45 % band, widened 8 %).
  Legacy generic `helmet` never becomes "acceptable".
- `side_mirror` attaches to the upper mounting band of the motorcycle box,
  split left/right; implausibly wide boxes are flagged, and overlapping
  motorcycles produce `ambiguous` with no side assignment.
- No mirror observation → `none_visible` **plus** an explicit
  `mirror:absence_not_proven_unknown` uncertainty reason. Absence is never
  proven.

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
