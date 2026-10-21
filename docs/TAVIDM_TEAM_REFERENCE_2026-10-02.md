# TAVIDM — Team Reference for the Project Progress Form

**Expanded report:** See [the detailed capstone report reference](TAVIDM_CAPSTONE_REPORT_REFERENCE_2026-10-02.md), revised using the supplied GURO report's section coverage. This file remains the shorter form-filling guide.

**Audit date:** 2 October 2026  
**System inspected:** Current working copy at `D:\tavidm`  
**Purpose:** Help group members describe the same project accurately in progress forms, presentations, and discussions.

This guide separates code that exists, local model/data evidence, and behavior that still needs a real demonstration. The working copy contains uncommitted changes, so this describes the current local system, not necessarily the version available from Git. No application code, model weights, or training datasets were changed for this audit.

## 1. Suggested answers for the form

### Group Members

- Mark Christian E. Osumo
- Fernandez — confirm complete name
- Parao — confirm complete name

The submitted member list repeats “Osumo” and leaves other names incomplete. Confirm the official names and order before submission.

### Project Title

**TAVIDM: Traffic Violation Detection and Monitoring System Using Computer Vision and Deep Learning**

### Project Description

TAVIDM is an AI-assisted traffic monitoring web application that processes uploaded road videos and supports live CCTV streams. It uses computer vision and deep learning to detect road users, track their movement, and evaluate possible traffic violations using configured zones, direction, time, and other observable evidence. The system provides evidence images, a human review queue, traffic summaries, violation records, and downloadable reports. It is designed to assist authorized personnel in reviewing possible violations, with human validation before case confirmation.

### AI/ML Model, Algorithm, IoT, or Other Key Technology Used/Implemented

The main detection pipeline uses **YOLOv8m** through Ultralytics and **ByteTrack** for multi-object tracking. **OpenCV** handles video frames, image processing, and stream capture. A **rule-based engine** combines detections with regions of interest, movement direction, trajectories, object counts, dwell time, and configured schedules to identify possible violations. The application uses **Python, Flask, SQLite, Bootstrap, and JavaScript**, with PDF and Excel report generation.

A separate **YOLOv8n motorcycle-detail model** has been trained experimentally for helmet and side-mirror observations. Its review workflow is implemented, but activation in the inspected configuration was not established. It supplies observations for human review and does not create or confirm violations.

The system supports CCTV/RTSP input; this audit did not establish a dedicated IoT sensor, embedded controller, or custom IoT hardware implementation.

### Current Completion Percentage (%)

**Approximately 65% — proposed audit estimate, subject to adviser/team agreement.**

The application and processing workflow are substantially implemented. Remaining work includes improving and validating the trained models, completing partial violation behavior, integrating real plate OCR, and demonstrating the complete system on representative local traffic footage.

This estimate describes overall project delivery. It is **not model accuracy**, not a measured percentage stored by the application, and not proof of deployment readiness. If your adviser has an approved milestone checklist, use that checklist's percentage instead.

### Project Status

**Advanced prototype / integration and validation stage.**

**Implemented in the current code:** role-based login; video upload and annotation; background processing and progress tracking; YOLOv8m detection and ByteTrack tracking; live-camera processing support; vehicle crossing counts; evidence capture; review and case workflows; dashboard and analytics; PDF/Excel reports; and user, camera, and system settings.

**Implemented with prerequisites:** four core violation rules—Obstruction, Counterflow, Truck-Ban Violation, and Motorcycle Overloading. Their availability depends on suitable model classes, scene annotations, settings, and visible evidence. These are not four independently demonstrated field-ready detectors.

**Partial or still under development:** No Helmet, Illegal Parking, Illegal Terminal, Failure to Follow Road/Pavement Markings, No Side Mirror, Unauthorized Passenger in Applicable Truck/Pickup Cargo Area, Substandard / Nut-Shell Helmet, and Disregarding Traffic Sign. No Helmet remains blocked in the main rule engine. A compatible four-class detail scan may add an ordinary pending review item after repeated positive uncovered-head observations are attributed to the same main-detector rider and current enabled source run. A person confirms or dismisses that item; the scan never confirms a violation. These rules have evaluators or review logic but are not classified as production-complete by the current code.

**Remaining milestones:** representative end-to-end video and live-camera demonstrations; stronger weak-class evaluation; genuinely unseen-location testing; completion of applicable partial rules and supporting configuration; real OCR backend integration; and operational reliability testing.

### Data/Dataset Details

The project uses road-traffic images and video frames, including project CCTV/road-footage material and Roboflow-exported development and class-specific datasets. Training data uses YOLO-format bounding-box annotations for vehicles, persons, riders, helmets, and side mirrors. The inspected manifests list development-review, vehicle/helmet staging, reannotated footage, new-location footage, close-up training material, and supplementary held-out packages.

The inspected **main V9.1 training derivative contains 7,303 image–label pairs**: **6,879 training**, **211 validation**, and **213 test** images. Its manifest records **42,082 annotated objects** across these splits. It uses **15 object labels**, of which **10 are vehicle labels**.

The separate **three-class motorcycle-detail derivative contains 2,037 image–label pairs**: **1,575 training**, **219 validation**, and **243 test** images. Its classes are `helmet_nut_shell`, `helmet_acceptable`, and `side_mirror`.

These are counts for specific inspected dataset versions, not the total number of unique images ever collected. The detail dataset reuses V9.1 material, so **do not add 7,303 and 2,037 and call the result a unique-image total**. Some supplemental provenance and annotation quality remain unresolved. The dataset documentation reserves WMSU material for unseen-location Gold v3 evaluation; this audit did not verify that final evaluation set.

## 2. What the team should understand before presenting

### Detection, tracking, and violation evaluation are different

1. YOLO detects observable objects and attributes in a frame.
2. ByteTrack associates detections across frames with track IDs.
3. The rule engine evaluates movement and context over time.
4. Evidence and suspected events are presented for review.
5. Authorized personnel review evidence and confirm or dismiss the case through the application's workflow.

YOLO does not directly classify all 12 violations. A missing helmet or mirror detection is not, by itself, conclusive evidence of a violation.

### Class counts: use the current schema

The current schema is **v1.3.0**:

- **10 vehicle labels:** car, van, jeepney, tricycle, autorickshaw, bus, truck, pickup_truck, motorcycle, bicycle.
- **5 other object labels:** person, rider, helmet_acceptable, helmet_nut_shell, side_mirror.
- **9 scene labels** remain in the training specification, giving **24 specified labels** overall.
- **12 canonical violation types** are rule-engine categories, separate from detector labels.

The preferred local main checkpoint has the 15 object labels; it does not contain all 24 specified labels. Vehicle crossing totals count the ten vehicle classes, excluding persons, riders, helmets, and mirrors.

The zone registry contains six types: no parking, active lane, pedestrian crossing, truck-ban zone, loading/unloading, and restricted lane. Sign and marking annotations provide additional scene context; their existence does not establish a trained scene detector.

### Current model evidence

The default weight resolver currently selects `models/tavidm_yolov8m.pt`. Loading that local checkpoint exposed the correctly ordered 15-class object roster. `models/best.pt` also exists, but exposes an older seven-class roster. File presence and class compatibility do not prove that a running server has reloaded those weights or that field accuracy is acceptable.

A recorded main-detector experiment, `tavidm_v9_1_coco_yolov8m_20260928_run02`, contains 20 epochs. Its best recorded validation mAP50–95 is **0.22341**, at epoch 15; mAP50 at that epoch is **0.38167**. This audit did not prove that the currently selected model file is the exact checkpoint from that experiment.

The latest inspected motorcycle-detail summary records **39 completed epochs**, a best validation mAP50–95 of **0.0871**, and test mAP50 of **0.3382** / mAP50–95 of **0.1140**. These recorded results support treating it as experimental. mAP is a detection evaluation measure, not the percentage of all violations correctly detected. The summary's test metrics were read, not independently recomputed.

No detail checkpoint designation was verified for the running deployment. A separately launched server could have different environment settings. The three-class detail workflow and four-class contract support therefore should not be described as a verified active feature. The four-class detail path can create a pending No Helmet review item under the stated evidence and run checks, but this does not establish a trained, designated, or active four-class model.

### Important feature limits

- **Illegal Parking:** current logic uses stationary dwell as a review proxy; dwell alone does not establish parking.
- **Illegal Terminal:** requires public-utility context, a suitable activity region, and temporal passenger-activity evidence; current behavior remains partial and review oriented.
- **Traffic signs:** the evaluator supports configured no-entry behavior; other sign types remain unsupported/review-only in this release. Do not claim general automatic recognition of every traffic sign.
- **No Side Mirror:** permanently manual-review only in current logic. Missing detections cannot prove absence, and one detected mirror cannot establish two mirrors.
- **Helmet/detail scans:** observations remain separate from violations and cases. Human detail-review outcomes do not automatically create a violation.
- **Plate recognition:** a standalone FastALPR-compatible adapter exists, but it is not called by the inspected main video/live pipelines. Real backend construction is deferred. Human plate verification is implemented separately.
- **Live CCTV:** stream capture and processing code exists; a real connected camera was not exercised in this audit.
- **Deployment:** the uploaded-video queue is in-process and thread based. Multi-process coordination and worst-case database/concurrency resilience were not established here.

### How the provisional 65% estimate was formed

This is an explicit judgment-based rubric, not an automated measurement:

- Application/UI/data workflows: 35% weight × approximately 85% progress = 29.75 points.
- Main detection, tracking, and model work: 25% × approximately 65% = 16.25 points.
- Full canonical violation behavior: 25% × approximately 50% = 12.50 points.
- Field validation and operational readiness: 15% × approximately 35% = 5.25 points.

Total: **63.75%, rounded to approximately 65%**. The component estimates and weights are proposed for team discussion. Do not present them as approved milestones. Five core rules out of twelve is also not a valid overall-project completion calculation.

## 3. Evidence and audit scope

Sources used to check the guide:

- `app.py`: routes, upload/processing queue, review, cases, reports, settings, and cameras.
- `config.py`: upload/storage configuration and explicit motorcycle-detail designation.
- `config/training/class_schema.json`: current label counts and ordering.
- `core/detection_config.py`: canonical violations, core/partial classifications, and weight priority.
- `core/detector.py`, `core/tracker.py`, `core/video_processor.py`, `core/live_stream.py`: main pipeline and stream implementation.
- `core/model_capability.py`, `core/violation_engine.py`: rule prerequisites and partial behavior.
- `core/zone_config.py`: six current zone types.
- `core/motorcycle_detail_scan.py`, `core/motorcycle_detail_contract.py`: separate review-only scan and checkpoint contracts.
- `core/plate_processing.py`, `core/reports.py`: OCR adapter boundaries and report generation.
- `dataset/derived/tavidm_v9_1_trainprep_closeups_trainonly_20260927/manifest.json` and `data.yaml`: main data split counts, annotations, and source packages.
- `dataset/derived/tavidm_v9_1_detail_3c_plus_v2_valnutshell_20261001/summary.json` and `README.md`: detail dataset counts and limitations.
- `dataset/training_runs/tavidm_v9_1_coco_yolov8m_20260928_run02/results.csv`: recorded main training metrics.
- `dataset/training_runs/tavidm_v9_1_detail_yolov8n_20261001_valnutshell/training_summary.json`: recorded detail training results.

Main and detail image/label file counts were independently counted on disk and matched the recorded split counts. Main annotation rows were also counted by class and split: all matched the manifest, with 28,907 training, 4,706 validation, and 8,469 test objects. Annotation correctness was not re-audited visually.

Older documentation conflicts with current evidence: `AGENTS.md` refers to a 12-class detector in one paragraph while the schema specifies ten vehicle classes. `docs/SYSTEM_TRUTH_INDEX.md` says no local baseline is integrated, but both local model files now exist. This guide follows inspected code, schema, and checkpoint metadata and records those discrepancies instead of repeating the older claims.

**Automated verification:** 334 tests passed in 90.71 seconds across `test_vehicle_class_schema.py`, `test_violation_roster.py`, `test_object_roster_v9_1_readiness.py`, `test_plate_processing.py`, `test_motorcycle_detail_three_class.py`, and `test_vehicle_crossing_counter.py`. Tests used a separate database configuration and disabled background application workers. These tests check label/rule contracts, OCR adapter behavior, detail-review boundaries, and crossing behavior; they do not measure field accuracy or establish that all application workflows work end to end.

A full test-suite run, browser demonstration, live RTSP test, fresh model evaluation, and unseen-location assessment were not performed. Consequently, “implemented in code” should not be replaced with “demonstrated working in deployment.”

## 4. Suggested short answer during a presentation

“TAVIDM is an AI-assisted traffic monitoring prototype using YOLOv8m for detection, ByteTrack for tracking, and a rule-based engine for possible violations. Our web application, processing, evidence, review, and reporting workflows are implemented. We retain twelve violation categories, with five core rules and seven partial rules. Our inspected main training dataset contains 7,303 annotated images across training, validation, and test splits. We estimate about 65% overall completion, with model improvement, partial-rule completion, OCR integration, and real-world validation still remaining.”
