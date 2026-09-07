# CCTV Label Studio Preparation Implementation Plan

> **For agentic workers:** Execute inline, task by task, and preserve the recorded red/green evidence. Do not commit, stage, push, train, or deploy.

**Goal:** Make the future CCTV vehicle-annotation workflow local, provenance-safe, and ready for a leakage-safe Vehicle PH v6 dataset without starting training.

**Architecture:** Label Studio Community Edition is the only reviewer UI. Small command-line tools prepare attributable still frames, create model suggestions, validate reviewed exports, and gate a derived dataset; raw videos, accepted datasets, and weights remain immutable inputs.

**Tech Stack:** Python 3.11, OpenCV, Pillow, Ultralytics YOLOv8m, Label Studio Community Edition, pytest, JSON/YOLO text formats.

## Global Constraints

- Keep all footage local and preserve originals unchanged.
- Use the canonical ten-class order from `config/training/class_schema.json`; do not create a second class roster.
- `uncertain` is review metadata, never a YOLO detector class.
- Suggestions require human confirmation and never prove ground truth or violations.
- Split by original camera/video/time group; never randomly split adjacent frames.
- Preserve v5, all raw datasets, checkpoints, and current dirty-worktree changes.
- Do not retrain, activate weights, stage, commit, push, or upload data.

---

### Task 1: Local Label Studio project package and existing-frame audit import

**Files:**
- Create: `config/label_studio/vehicle_detection.xml`
- Create: `docs/LABEL_STUDIO_CCTV_WORKFLOW.md`
- Create: `scripts/build_label_studio_vehicle_tasks.py`
- Test: `tests/test_build_label_studio_vehicle_tasks.py`

**Interfaces:**
- Consumes: canonical schema JSON plus YOLO image/label directories.
- Produces: Label Studio JSON tasks with provenance fields, existing boxes as unaccepted predictions, and no detector-level `uncertain` class.

- [ ] Add focused tests for fixed class order, prediction-only import, source group retention, invalid class rejection, and missing-image rejection.
- [ ] Observe the missing-behavior failure.
- [ ] Implement the XML config, task converter, and local-only workflow instructions.
- [ ] Verify focused tests and generate a separate Philippine-frame audit import package.

### Task 2: Deterministic CCTV frame selection

**Files:**
- Create: `scripts/select_cctv_frames.py`
- Test: `tests/test_select_cctv_frames.py`

**Interfaces:**
- Consumes: one local video path, explicit source-group ID, interval, and output directory.
- Produces: lossless attributable frames plus a checksum/source manifest; refuses overwrite.

- [ ] Add focused tests using a synthetic video for deterministic naming, spacing, hashes, and overwrite refusal.
- [ ] Observe the missing-behavior failure.
- [ ] Implement extraction, exact/near-duplicate filtering, metadata capture, and atomic output publication.
- [ ] Verify focused tests; real CCTV validation remains deferred until footage arrives.

### Task 3: Human-review export validation and v6 readiness gate

**Files:**
- Create: `scripts/validate_vehicle_annotations.py`
- Test: `tests/test_validate_vehicle_annotations.py`
- Modify: `scripts/build_pickup_video_gold.py`
- Test: `tests/test_build_pickup_video_gold.py`

**Interfaces:**
- Consumes: selected-frame manifest and reviewed YOLO export.
- Produces: validation report with class counts, unresolved/missing review failures, duplicate hashes, and source groups; the existing gold audit consumes those groups.

- [ ] Add focused tests for complete review, unknown class, invalid geometry, missing label, duplicate content, and source-group leakage.
- [ ] Observe the missing-behavior failure.
- [ ] Implement fail-closed validation and connect its verified group report to the existing four-group readiness gate.
- [ ] Verify focused and integration tests.

### Task 4: Isolated Label Studio installation and launch verification

**Files:**
- External install only: `D:\apps\label-studio-tavidm\venv`
- No repository dependency changes.

**Interfaces:**
- Consumes: dedicated Python virtual environment.
- Produces: local `label-studio` executable and version evidence; does not start a persistent service or create an account.

- [ ] Create the isolated environment and install Label Studio Community Edition.
- [ ] Verify the executable and version from the isolated environment.
- [ ] Record the exact launch command with local-file serving rooted at a future controlled annotation workspace.

## Acceptance Evidence

- Focused pytest files pass from `D:\tavidm`.
- Generated audit import contains predictions, provenance, and the canonical ten classes.
- Synthetic video test proves deterministic extraction without touching source data.
- Export validator fails closed on incomplete or unsafe inputs.
- Label Studio version command succeeds from the isolated external environment.
- Real CCTV, browser annotation, YOLO suggestion accuracy, v6 construction, and retraining remain explicitly deferred.

## Unresolved Product Decisions

- Final CCTV source-group names, extraction interval, and split assignment depend on the received footage.
- Label Studio user credentials are created interactively by the project owner at first launch and are never stored by these tools.
