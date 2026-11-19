# TAVIDM — Current Project State

**Reviewed:** 2026-10-25 (Australia/Perth)
**Workspace:** `D:\tavidm`
**Branch:** `cursor/phase-1-foundation`
**Source baseline:** `9b3a4b8d05912067f914302d285b73299c7ad7a6`, plus existing local changes. This assessment describes the local checkout, including the pre-existing change in `tests/test_amendment_gates_5_8.py`. It does not establish the state of a deployed instance or a clean clone.

## Current phase

**Phase 2 — model integration, evaluation, and system validation; pre-field-pilot.**

The application foundation and most user workflows are implemented. A compatible custom main detector is installed locally. The remaining work is to establish how reliably the complete system behaves on representative footage, resolve gated evidence paths, and complete acceptance testing before a field pilot.

This is an engineering assessment against the repository's existing Phase 2 terminology, not a newly approved phase numbering scheme. The branch name `phase-1-foundation` does not describe the current maturity. No defensible overall completion percentage, field accuracy, FPS, or production-readiness claim follows from this review.

- **Foundation:** implemented Flask/SQLite application, roles, uploads, annotations, processing, evidence, review, analytics, and reports.
- **Model integration:** selected local main weights match the exact ordered 15-object contract.
- **Validation and stabilization:** active; automated results and unresolved gaps are recorded below.
- **Field pilot / production acceptance:** not established by this scan.

## What was inspected

Reviewed the application entrypoint and route surface, configuration, detector/tracker and rule contracts, scene annotations, upload pipeline, job scheduling, evidence lifecycle, review and account scoping, reports, optional detail/OCR workflows, training-readiness controls, current documentation, and recent development logs. Parsed all 179 tracked Python files and all 13 tracked HTML templates without syntax errors. Inventoried 93 Flask route decorators, 83 Python test modules, 12 JavaScript harness files, and 14 SQL migration files.

This is a broad source and regression assessment, not a line-by-line security audit. No operational database, private account records, camera credentials, or source footage was inspected. No training, operational migration, model promotion, deployment, or real-video inference was performed. Existing unrelated work was preserved.

## Implemented application workflows

- **Access:** admin/enforcer/viewer roles, account management, session revalidation, and server-derived account access checks. Administrative cross-account views are explicit.
- **Upload and annotation:** MP4 upload, a 3 GiB default upload limit, disk-headroom checks, first-frame extraction, reusable templates, structured scene objects, lanes, flow arrows, signs, markings, activity regions, and threshold/counting lines.
- **Processing:** YOLOv8m + ByteTrack, SQLite-backed job claims, progress/preview, cancellation, history, annotated replay, scoped results, and result cleanup. Defaults allow three active video jobs globally and one per account, with two local worker threads. The shared local GPU reservation has capacity two; it is process-local. These are configured limits, not measured hardware capacity or a cross-process GPU guarantee.
- **Evidence and review:** private evidence routes, multi-frame episodes, review outcomes and provenance, account-scoped review queues, and vehicle/case grouping that retains association evidence.
- **Detail review:** separate Motorcycle Review and Pickup Review paths. Pickup/person overlap and shared motion are review leads; they do not prove that a person occupies the cargo bed and do not create an ordinary cargo violation case.
- **Reports and analytics:** video-scoped report preview, filters/pagination, persisted review metadata, PDF/Excel export, and a 5,000-case export cap. Analytics and review counts do not measure detector accuracy.
- **Policy and identity:** plate verification, reviewed event time, case confirmation, recurrence helpers, and notice/print workflows exist with policy gates. Their presence does not authorize legal enforcement.
- **Recent queue improvements:** lazy loading of plate details, a default review page size of 20, and sidebar-count refresh on focus/visibility are present. No before/after response-time benchmark was collected here.

Primary sources: `app.py`, `config.py`, `core/auth.py`, `core/video_processor.py`, `core/scene_annotation.py`, `core/processing_jobs.py`, `core/gpu_inference_slot.py`, `database/processing_limits.py`, `core/video_lifecycle.py`, `core/review_decision_service.py`, `core/vehicle_grouping.py`, and `core/reports.py`.

## Model and class state

The authoritative schema is `config/training/class_schema.json`, version **1.3.0**: **10 vehicle classes**, **15 object classes**, and **9 scene classes** (24 annotation labels). The 15-object main checkpoint does not by itself detect the nine scene labels.

Vehicle classes, in detector order: `car`, `van`, `jeepney`, `tricycle`, `autorickshaw`, `bus`, `truck`, `pickup_truck`, `motorcycle`, `bicycle`. The object roster then adds `person`, `rider`, `helmet_acceptable`, `helmet_nut_shell`, and `side_mirror`.

Fresh local checkpoint inspection loaded the files and read their class maps without running inference:

- **Selected by the resolver:** `models/tavidm_yolov8m.pt`; all 15 object labels match in exact ID order. SHA-256: `1b55b7cfa92c75043b883e0aea2a409cceed29da3dd52596f410fbaa499cd8cc`.
- **Lower-priority legacy file:** `models/best.pt`; seven labels: bicycle, bus, car, jeepney, motorcycle, tricycle, truck. SHA-256: `a693d7909929e5f3baa6d3af254f0f0508ae4a61d2a8bb076e512abafc9e12bc`.
- **Separate detail file:** `models/motorcycle_detail_best.pt`; `helmet_nut_shell`, `helmet_acceptable`, `side_mirror`, in that order. It has no positive `no_helmet` class. SHA-256: `e330138c3f84a0579b36dd5b4eb3a52e4680a4c8b67182a4ceee6b5a7c9b4f7e`.

These weight files are local artifacts; only `models/.gitkeep` is tracked. Their existence does not prove that a new clone has the same model. The detail weights need explicit configuration; file presence alone does not establish activation. Operational process settings were not inspected. Class compatibility and successful loading do not establish accuracy, suitability for a camera, or GPU throughput.

## The 12-rule roster and its limits

The registry currently labels **four** rules implemented and **eight** partial. This is code coverage, not a count of validated enforcement capabilities.

- **Obstruction — implemented:** requires relevant zone geometry and sustained scene evidence; real-world correctness remains to be measured.
- **Counterflow — implemented:** requires an applicable lane and saved flow direction; scene configuration and trajectories need validation.
- **Truck-Ban Violation — implemented:** requires the truck class, applicable zone, and recording date/time for schedule evaluation.
- **Motorcycle Overloading — implemented:** requires motorcycle/rider detections and defensible rider association. The installed main model supplies the required label names, not proof of association accuracy.
- **Illegal Parking — partial:** stationary dwell is a review proxy; it does not establish parking intent.
- **Illegal Terminal — partial:** requires applicable loading/unloading and activity evidence; full terminal behavior is not established by a dwell box.
- **Disregarding Traffic Sign — partial:** annotated sign/maneuver scope, including no-entry threshold/direction constraints; this is not general recognition of every sign violation.
- **Failure to Follow Road/Pavement Markings — partial:** configured marking/trajectory crossings; not all possible road-marking offenses.
- **No Helmet — partial and automatically blocked:** `POSITIVE_UNCOVERED_HEAD_OBSERVATION_APPROVED=False`. No helmet detection means unknown. The main roster has no uncovered-head label, and detail output cannot create or confirm this violation through the ordinary path.
- **No Side Mirror — partial and evidence-gated:** requires positive visibility of both mounting areas. No production `mirror_roi_visibility` producer was found; missing detections cannot establish absence.
- **Unauthorized Cargo-Area Passenger — partial and evidence-gated:** requires `cargo_region_observable` and `lower_body_visible`. No validated production producer was found. Pickup Review remains a separate lead-review workflow; live cargo evaluation is suppressed.
- **Substandard / Nut-Shell Helmet — partial:** helmet-type review evidence exists, but rider/vehicle attribution and field performance require validation.

Sources: `core/detection_config.py`, `core/model_capability.py`, `core/violation_engine.py`, `core/cargo_passenger.py`, `core/pickup_detail_review.py`, `core/motorcycle_detail_contract.py`, and `core/live_stream.py`.

## Training, OCR, and legal gates

**Recorded Phase 2 training entry gate: closed.** A fresh `python -m core.phase2_readiness` returned `ready=false` with four registered datasets. The registry reports no accepted dataset; collection permission, privacy/retention approval, gold review, pilot pass, and acceptance thresholds remain pending in the checked-in controls. `config/training/gold_set_review.json` is pending with no items. Local training outputs and installed weights show that model work exists, but they do not complete these recorded acceptance controls. Reconcile the registry with traceable dataset/model evidence; do not mark approvals passed merely because a checkpoint exists.

**Plate OCR: experimental.** Local ONNX processing, bounded jobs, attempt manifests, and human verification are implemented. Explicit configuration and runtime authorization are required. Current source permits an isolated supervised demo without a passing evaluation record; older documentation that says a passing record is always required is stale. No fresh OCR quality evaluation was performed here. A stored/demo measurement is not representative CCTV accuracy.

**Legal mapping: unresolved in the checked-in configuration.** `config/violation_legal_mappings.json` contains eight `unverified` and four `flag_only` mappings; none is `verified`. This is a statement about local configuration, not a fresh legal determination. Production database policy versions were not inspected. Authoritative policy review and applicable approvals remain separate from candidate detection and human review.

## Fresh verification — 2026-10-25

Python regression: `python -m pytest tests/ -q --tb=short` completed with **1,468 passed, 49 failed, 2 skipped, and 1 warning** in **714.62 seconds** (exit 1). SQLite and upload/frame/annotated/evidence/report paths were redirected to temporary storage before imports, with disposable test credentials. The suite is not green. Exact failing tests are retained in [regression-failures.txt](docs/audits/2026-10-25-system-state/regression-failures.txt).

Failure triage distinguishes observed symptoms from confirmed runtime bugs:

- Several API/detail tests receive 404s or empty scoped lists; for example, the annotation test creates an ownerless video and then requests it as an enforcer. Compare fixtures with the current ownership contract before changing access controls.
- Capability tests still expect automatic No Helmet or No Side Mirror behavior despite the explicit positive-evidence gates; one status assertion expects `model_dependent` where the registry now returns `partial`.
- Two migration-007 legacy-fixture tests fail with `no such column: worker_id` when calling the current `finish_processing_run`. These use partial legacy schemas; this does not prove that a fully migrated operational database has the same defect.
- Grouping tests include empty/provenance results and mocks that reject the new `owner_user_id` argument. Detail-review tests include wording and human-confirmation status expectations. These require contract-by-contract reconciliation.
- Four OCR-default failures are **environment-sensitive**: a targeted rerun explicitly initializing `TAVIDM_PLATE_OCR_APP`, `TAVIDM_PLATE_OCR_DEMO`, and `TAVIDM_PLATE_OCR_EVALUATION` to `0` passed **all nine** OCR-default tests. The full run had not neutralized those inherited mode flags. Do not classify those four failures as established OCR runtime defects or substitute the targeted result for a second full-suite run.
- The upload UI test expects the missing text `Extracting metadata`; report the stale expectation separately from functional browser behavior.

The targeted triage run had **9 passed / 3 failed** in 4.05 seconds: selected annotation, partial-migration, and mirror-capability failures reproduced. No runtime code or tests were changed to obtain these results. Remaining full-run failures still need individual triage.

JavaScript harnesses: **11 of 12 files passed**. `tests/js/test_ui_ux_helpers.cjs` fails while importing `static/js/main.js:85`: its document mock implements only `getElementById`, while the navigation-count code calls `document.querySelectorAll`. This is a demonstrated harness incompatibility; it does not by itself demonstrate a browser failure. The file needs an updated DOM fixture and focused rerun before the JavaScript suite can be called green.

Isolated Flask smoke: login returned HTTP 302, and **16 page/API GETs returned HTTP 200**, using a fresh temporary SQLite database, disposable test credentials, empty storage, and disabled workers. Covered dashboard, live-monitor shell, violations, analytics, reports, settings, all three review areas, annotation help, developer page, navigation counts, processing jobs, zone types, video list, and OCR status. This checks server rendering/routing for an admin on an empty database; it does not exercise browser JavaScript, populated production data, camera input, or inference.

Static verification: all **179 tracked Python files** and **13 tracked HTML templates** parsed successfully; all **16 application JavaScript files** passed `node --check`. All three installed checkpoint class maps were inspected. Training gate failure is an explicit readiness result, not a test runner crash. Machine-readable evidence is recorded in [verification.json](docs/audits/2026-10-25-system-state/verification.json).

## Next milestones and acceptance evidence

1. **Restore regression confidence:** triage each fresh Python failure and the JavaScript mock failure, distinguishing stale expectations, test isolation, and runtime defects. Require passing focused regressions and a fresh full run after fixes.
2. **Reconcile model/data records:** connect installed weight hashes to approved sources, source-group splits, reviewed labels, permission/retention evidence, and recorded acceptance thresholds. Preserve unseen footage for final evaluation.
3. **Measure the complete pipeline:** on approved representative footage, record per-class precision/recall and mAP, violation-level false positives/misses against independent human labels, tracking/association errors, and uncertain/occluded outcomes. Include the intended camera, lighting, distance, and weather conditions. Declare denominators and sample scope.
4. **Resolve evidence-limited rules:** validate mounting-area visibility and cargo/lower-body observability before enabling those paths; obtain the explicit uncovered-head evidence decision for No Helmet. Preserve unknown outcomes until evidence exists.
5. **Complete user acceptance:** browser checks for admin/enforcer/viewer, account isolation, uploads and annotation, processing/cancel/restart, review outcomes, detail queues, reports, and cleanup with realistic data. Exercise stale requests and role changes.
6. **Validate live and capacity behavior:** approved RTSP sources, disconnect/reconnect, simultaneous offline/live/detail work, long-video memory/disk use, GPU peak memory, latency/throughput, and crash recovery. Choose measured admission limits for the actual deployment topology.
7. **Approve field-pilot scope:** resolve applicable legal-policy gates, backup/restore, access/retention procedures, and acceptance sign-off. Only then label the approved scope pilot-ready.

## Documentation map

- [Setup and first use](README.md)
- [Source-of-truth map](docs/SYSTEM_TRUTH_INDEX.md)
- [Training acceptance requirements](docs/PHASE_2_AI_TRAINING_READINESS.md)
- [Historical policy implementation checklist](docs/IMPLEMENTATION_CHECKLIST.md)
- [Historical adviser-task progress](progress.md)
- [Development history](docs/development_logs/)

Older logs and plans describe their dates and scopes. Their completed tasks do not establish that every rule, model, legal mapping, or deployment gate is complete. Refresh this document after a material implementation change or measured acceptance run, keeping implemented, tested, and field-validated claims separate.
