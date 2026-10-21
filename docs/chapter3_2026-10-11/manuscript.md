# TAVIDM Technical Background and Standard Operating Procedure

Traffic Violation Detection and Monitoring System Using Computer Vision and Deep Learning

Prepared for the TAVIDM project team

Source review date 11 October 2026

## Purpose and manuscript placement

This document provides a Chapter 3 draft based on the current TAVIDM implementation and a separate Standard Operating Procedure for authorized users. Chapter 3 follows the supplied manuscript headings: 3.1 Conceptual Framework and 3.2 Technology Requirements. Subsections explain the system in greater detail while preserving those two main headings.

TAVIDM combines object detection, tracking, scene annotations, temporal rules, evidence recording, and human review. Its outputs are suspected events for assessment. Recognition of an object, a high confidence score, or the presence of an evaluator does not establish that every violation category has been validated in field use.

The SOP is an operational appendix rather than an additional Chapter 3 section. Research design, developmental methodology, formal architecture diagrams, data collection, requirement analysis, detailed system design, and evaluation procedures belong in Chapter 4 under the outline supplied by the team.

## Document guide

- Chapter 3 Technical Background — conceptual framework, processing concepts, violation scope, and technology requirements.
- Appendix A Standard Operating Procedure — preparation, upload, annotation, processing, review, reporting, live monitoring, and administration.
- Appendix B Evidence and manuscript completion notes — source references, scan scope, and items to verify before submission.

## Draft status

The technical descriptions reflect a static review of the local source at D:\tavidm. This work did not run a field experiment, load model weights, access operational database contents, or exercise the application in a browser. No participant counts, accuracy results, deployment claims, or approved hardware minimums have been invented. Internal source identifiers such as [S1] are explained in Appendix B.

<!-- pagebreak -->
## CHAPTER 3 TECHNICAL BACKGROUND

### 3.1 Conceptual Framework

TAVIDM is an AI-assisted system that helps authorized personnel examine traffic footage, locate suspected violations, and organize evidence for review. Its conceptual framework connects observable road activity to a sequence of computational and human decisions. Video supplies observations; configured road geometry and rule parameters give those observations context; authorized users determine the appropriate disposition of the resulting evidence.

The framework uses an input–process–output model with a controlled feedback loop. Inputs include uploaded MP4 files or configured camera streams, scene annotations, enabled rules, available model capabilities, and recording time when known. Processing converts frames into object observations, associates observations over time, evaluates applicable rules, and preserves candidate evidence. Outputs include review items, processing summaries, annotated replay where available, vehicle passage counts, reviewed records, and reports. [S1–S5]

[FIGURE]

Figure 3.1 Conceptual framework of TAVIDM. The diagram summarizes the inspected application; it is not a claim that every optional component is enabled.

Human review forms part of the processing framework. All confidence bands lead to operator assessment rather than automatic confirmation. A reviewer may confirm a proposed category, select another canonical category, determine that no violation occurred, or close the item because the evidence is insufficient. These choices preserve the distinction between an algorithmic suggestion and a human finding. [S6]

### 3.1.1 Feedback and system boundaries

Review outcomes and diagnostic messages can identify unsuitable geometry, missing context, or weaknesses requiring further investigation. Authorized personnel may then revise annotations or approved settings and start a new run. This is a managed improvement cycle. The application does not automatically retrain its main model from review decisions. Training, model selection, and acceptance require a separate documented process.

The system boundary comprises the web application, local processing modules, SQLite persistence, configured model artifacts, and private media storage. Camera equipment, source-video ownership, research approvals, and decisions by the responsible traffic authority remain external to the application. Chapter 4 should present the formal context diagram and data flow diagram using this same boundary.

<!-- pagebreak -->
### 3.1.2 Visual detection and tracking

The main object detector uses the YOLOv8m family through Ultralytics. YOLOv8 supports object detection, and TAVIDM uses the medium variant specified by the project. The local resolver gives preference to designated custom checkpoint filenames before the development fallback. Actual availability and class coverage must be checked against the checkpoint loaded for a run. This source review confirms the selection logic and expected class contract; it does not identify the model held by a separately running server. [S2; E1]

Detection produces observations such as class labels, bounding boxes, and confidence scores. ByteTrack associates detections across frames so the application can maintain motion history. The published ByteTrack method uses detection-to-track association, including lower-confidence detections in its association process, to reduce track fragmentation. TAVIDM calls the Ultralytics tracking interface with persistent state and bytetrack.yaml. A fresh detector/tracker instance belongs to a video or stream. [S2; E2]

Track identifiers represent temporary processing identities. They are not registration numbers and do not establish that two tracks in different cameras belong to the same vehicle. Occlusion, viewpoint, class ambiguity, and tracking interruptions can affect motion estimates and downstream decisions.

### 3.1.3 Object classes and scene context

The current schema specifies ten vehicle classes: car, van, jeepney, tricycle, autorickshaw, bus, truck, pickup_truck, motorcycle, and bicycle. Five additional object or attribute classes are person, rider, helmet_acceptable, helmet_nut_shell, and side_mirror. Together they form the ordered fifteen-class object contract. SUVs and crossovers fall under car in the current schema; pickup_truck remains distinct from truck. [S1]

Nine scene labels are also specified: sign_no_entry, sign_no_overtaking, sign_no_left_turn, sign_no_u_turn, sign_no_parking, sign_no_stopping, marking_double_solid, marking_single_solid, and marking_solid_broken. These are separate from the fifteen-class object contract. The schema therefore describes twenty-four labels overall, not twenty-four vehicle classes. A training schema alone does not prove that a loaded model detects all specified scene labels.

Runtime road context is supplied through scene annotations. Six zone keys are supported: no_parking, active_lane, pedestrian_crossing, truck_ban_zone, loading_unloading, and restricted_lane. The loading_unloading key is displayed as No Loading/Unloading Zone. Structured annotations additionally represent lanes, flow arrows, markings, signs, threshold lines, counting lines, and activity regions. These spatial relationships let the engine distinguish a vehicle merely visible in a frame from movement through a relevant road region. [S3]

<!-- pagebreak -->
### 3.1.4 Temporal and spatial reasoning

The rule engine evaluates observations using position, movement, persistence, class applicability, and available context. The tracker maintains timestamped centroid histories. Image-space speed is estimated from displacement over a recent time interval, and direction is derived from the change in position. Insufficient displacement or history cannot establish a reliable heading. These quantities support movement and dwell reasoning; they are not calibrated road speed measurements in kilometres per hour. [S2; S4]

A stationary condition must persist for the applicable duration before a dwell rule can produce a candidate. The current defaults include 30 seconds for parking, 10 seconds for active-lane obstruction, 3 seconds for pedestrian-crossing blockage, and 8 seconds for loading-zone dwell. Runtime settings can override defaults. These values describe implementation parameters, not independently verified statutory thresholds. Illegal Parking remains a possible-parking indicator because a sustained stop alone does not resolve every contextual exception. [S1; S4]

Counterflow compares a sufficiently established movement direction with an annotated allowed direction. Truck-ban evaluation combines the applicable vehicle class, zone membership, schedule, and usable event time. Marking and sign evaluators rely on explicitly related scene objects and movement history. When required information is missing or ambiguous, the system should suppress the unsupported conclusion and expose the reason in its diagnostics.

### 3.1.5 Confidence and evidence sufficiency

Detector confidence and violation confidence are separate quantities. The rule-scoring helper combines available factors for persistence, geometry stability, association quality, contextual availability, and detection reliability. Its default weights are 0.30, 0.20, 0.20, 0.15, and 0.15, respectively. Weights are normalized over the factors that are available. Missing factors remain recorded, and a separate sufficiency value reflects available core factors. A high score does not replace an absent prerequisite. [S4]

The application defines three review bands: at least 0.95 is auto-queued for validation; at least 0.80 but below 0.95 calls for careful review; below 0.80 is low-confidence monitoring or review. These are routing labels, not measured accuracy percentages and not automatic case-confirmation thresholds. The detector also has its own configurable admission threshold, whose source default is 0.60. [S1]

### 3.1.6 Vehicle passage counting

Vehicle passage counting uses a vehicle bounding box’s bottom-centre point and a configured counting line. A side transition beyond a hysteresis band can produce a count. Only the ten vehicle classes contribute; persons, riders, helmets, and mirrors do not. A line–track pair is counted at most once in a processing run. Counts from several lines represent passages at those lines and may include the same vehicle more than once across the network. Missing geometry means counting is unavailable, not that traffic volume is zero. [S5]

<!-- pagebreak -->
### 3.1.7 Canonical violation scope

TAVIDM preserves twelve canonical violation categories. The current registry classifies four as implemented candidate paths and eight as partial. The distinction is an implementation classification, not proof of accuracy or readiness for enforcement. Every category still depends on suitable inputs, available capabilities, and human assessment. The twelve categories are summarized in the following two tables. [S1; S4]

| Canonical category | Registry status | Evidence and present boundary |
| --- | --- | --- |
| Illegal Parking | Partial | Sustained observed stopping in a configured No Parking Zone. Produces a possible-parking review candidate; scene context and exceptions remain for review. |
| Obstruction | Implemented | Sustained stationary presence in an active lane or pedestrian crossing using the applicable zone and dwell parameter. Evidence must support the actual obstruction context. |
| Counterflow | Implemented | Movement history and allowed lane flow support an opposing-direction assessment. Missing or ambiguous lane context prevents a supported conclusion. |
| Truck-Ban Violation | Implemented | Applicable truck class in a truck-ban zone during a configured time window. Recording time must be usable. pickup_truck is not automatically included in the default truck class set. |
| No Helmet | Partial | Automatic evaluation is blocked pending approved positive uncovered-head evidence. Failure to detect a helmet is not evidence of an uncovered head. |
| No Side Mirror | Partial | Needs mounting-area visibility and supported mirror evidence. The current capability gate identifies the missing visibility prerequisite; an undetected or occluded mirror is not proof of absence. |

The main object roster, canonical violation roster, scene-label roster, and user-visible grouped categories have different purposes. For example, a grouped presentation can combine related behaviours for navigation without changing the underlying canonical rules. Chapter 3 should retain these distinctions so readers do not mistake a configuration option for a detector class or a completed rule.

Enabling a rule requests evaluation when prerequisites permit it. It does not bypass missing evidence or guarantee that a candidate will be produced. Conversely, a completed run with no candidates is not sufficient evidence that no violation occurred. The operator must check enabled rules, capability diagnostics, geometry, source visibility, and the relevant time context before interpreting a negative result.

<!-- pagebreak -->
### 3.1.8 Context dependent violation categories

| Canonical category | Registry status | Evidence and present boundary |
| --- | --- | --- |
| Motorcycle Overloading | Implemented | Uses motorcycle and rider observations with association checks. Nearby people must not be treated as occupants without supported attribution. |
| Disregarding Traffic Sign | Partial | Current executable paths cover no-entry and review candidates for prohibited left or right turns, U-turns, and overtaking with matching geometry and histories. Stop and speed-limit evaluation is excluded in this path. |
| Failure to Follow Road/Pavement Markings | Partial | Uses supported saved marking geometry and prohibited-side crossing evidence. Driving alongside a marking does not alone establish a prohibited crossing. |
| Illegal Terminal | Partial | Requires the relevant no-loading zone, vehicle context, dwell, and supported activity evidence. A stop alone is insufficient. It remains distinct from Illegal Parking. |
| Unauthorized Passenger in Applicable Truck/Pickup Cargo Area | Partial | Uploaded-video evidence supports review of applicable cargo-area observations. Automatic live output is suppressed; person detection alone does not establish cargo-area occupancy. |
| Substandard / Nut-Shell Helmet | Partial | Needs compatible nut-shell helmet observations with rider and motorcycle context. Visual classification remains subject to human review and does not certify material construction. |

The traffic-sign implementation demonstrates why executable source must take precedence over older descriptions. Its current body dispatches to turn and overtaking helpers, and corresponding tests are present, even though its leading comment and an older operator checklist describe no-entry-only behaviour. The broader candidate paths are therefore described here as present but partial. This review did not execute those tests or measure those paths on real footage. [S4; S12]

### 3.1.9 Supplementary motorcycle detail analysis

The motorcycle-detail workflow collects selected uploaded-video crops and can scan them with an explicitly designated compatible checkpoint. It has a separate review queue and supports an ordered three-class contract for helmet_nut_shell, helmet_acceptable, and side_mirror, as well as a four-class contract that appends no_helmet. Support for a contract does not establish the presence, activation, or acceptance of a matching trained model. [S7]

Detail observations remain separate from the main detector’s tracking and violation pipeline. A compatible four-class scan can add an ordinary pending No Helmet review item when repeated clear positive uncovered-head observations are attributed to the same main-detector rider and the source run is current and enabled. This does not enable the blocked main No Helmet rule or confirm a violation. The main detector’s stored context supplies rider and motorcycle attribution. An empty or unclear detail result must remain unknown rather than being converted into an absence finding.

<!-- pagebreak -->
### 3.1.10 Evidence recording and event time

The processing pipeline can retain frame evidence, vehicle crops, reason logs, confidence information, and temporal clips or frame sequences when available. Uploaded processing associates outputs with the source video and processing run. Temporal evidence provides context before and after an event, but source boundaries or incomplete finalization can limit the material available to a reviewer. The review service blocks confirmation of tagged temporal evidence until the required output is finalized. [S5; S6]

Video-relative time, recording date and time, upload time, and processing time represent different facts. The relative timestamp identifies a position in a clip. Recording time is needed to interpret when the road event occurred. Upload and processing timestamps describe application activity. When the true recording time is unknown, it must not be replaced by upload time merely to satisfy a time-based rule. The event-time workflow records reviewed time information separately. [S8]

### 3.1.11 Human review and case records

The Review Queue preserves the original suggestion and supports four decisions. Confirm proposed violation retains the proposed canonical rule when evidence supports it. Correct to another canonical violation records the selected rule while retaining the original suggestion. No violation and Insufficient evidence close the review item without creating a case. Insufficient evidence is neither confirmation nor a finding that no violation occurred. A decision requires a reason and an authenticated authorized actor. [S6]

Confirmation and correction invoke case materialization with the selected canonical rule and associated evidence and policy context. Case review, plate identity verification, event-time review, policy approval, case confirmation, and notice-print recording are separate actions with their own permissions and prerequisites. Opening a printable record does not prove that a notice has been printed. These separations protect the meaning of each stage in the workflow.

### 3.1.12 Optional plate recognition

The current uploaded-video and live modules contain integration hooks for optional local plate OCR. The runtime is disabled by default and requires explicit configuration and an authorized execution mode. Model artifacts and configuration hashes are checked, evidence paths are constrained, and work is bounded through a queue and crop budget. Both regular application mode and isolated demo or evaluation modes appear in the current source. Activation in a running server was not checked. [S9]

Machine plate candidates are not verified identities. Human identity verification remains a separate administrator-restricted action in the inspected routes. A No attempt state means no persisted attempt is available; it should not be described as a failed reading. This manuscript must not claim universal number-plate recognition or validated OCR accuracy without a separate evaluation record covering the intended footage.

<!-- pagebreak -->
### 3.2 Technology Requirements

TAVIDM is implemented as a Python and Flask monolith with server-rendered HTML and JavaScript. Computer-vision and rule modules execute on the application host, SQLite stores structured records, and media artifacts are stored in configured private directories. This arrangement connects processing and review within one application. It does not establish an independently deployed worker service or a distributed inference system. [S2; S5; S10]

### 3.2.1 Software components

| Component | Source requirement or implementation |
| --- | --- |
| Python | Project guidance specifies Python 3.10 or later. The actual deployment interpreter and full dependency environment must be recorded for reproducibility. |
| Flask | requirements.txt specifies Flask 3.0 or later. Handles pages, API routes, sessions, and application orchestration. |
| Ultralytics and YOLOv8m | Ultralytics 8.0 or later is declared. The main detector uses the YOLOv8m family and compatible class metadata. |
| ByteTrack | Selected through bytetrack.yaml in the Ultralytics tracking call. Maintains per-source track association. |
| OpenCV and NumPy | opencv-python 4.8 or later and NumPy 1.24 or later support media frames, arrays, and image processing. |
| Supporting numerical packages | supervision is constrained to at least 0.29.1 and below 0.30.0; lap and SciPy are also declared dependencies. |
| SQLite | Local relational storage accessed through the project database adapters. The operational database is separate from the source schema definition. |
| HTML CSS and JavaScript | Server-rendered templates with Bootstrap 5.3.3 and Bootstrap Icons referenced by the base template. |
| Report generation | fpdf2 2.7 or later creates PDF reports; openpyxl 3.1 or later creates Excel reports. |
| Authentication | bcrypt 4.0 or later provides password hashing; Flask sessions identify authenticated requests. |
| Testing tools | pytest and pytest-cov are declared. JavaScript regression harnesses also exist in the repository. |

These are declared requirements and inspected integration points, not an installed-package inventory. Several Python dependencies use lower bounds rather than exact locks, so the team should preserve an environment report for every formal evaluation. Current Flask and SQLite documentation provides technology background, while TAVIDM source determines what the application actually implements. [S10; E3; E4]

<!-- pagebreak -->
### 3.2.2 Hardware and deployment requirements

The host must support Python, the selected computer-vision packages, the chosen checkpoint, video decoding, SQLite, and sufficient storage for source and generated media. A compatible GPU is relevant to practical inference throughput, but a universal minimum GPU, RAM capacity, camera count, or real-time frame rate cannot be established from source code alone. Code comments refer to an RTX 3050 with 6 GB memory as the design context; this is not a measured minimum or a live hardware inventory. [S5]

| Resource | Requirement to establish before evaluation |
| --- | --- |
| Processing host | Record operating system, CPU, RAM, GPU model and memory, drivers, interpreter, and installed package versions. Benchmark the actual model and representative video resolution. |
| Storage | Reserve capacity for raw uploads, extracted frames, replay videos, evidence, reports, database files, and backups. Check free space before long processing sessions. |
| Camera source | Supply an authorized and reachable stream compatible with the configured capture path. Record resolution, viewpoint, frame rate, and clock provenance. |
| Client device | Use a supported browser with network access to the authorized application address. Check upload, annotation, playback, and review controls on the intended display. |
| Network | Provide sufficient capacity for video upload, streams, and evidence retrieval. Current templates reference external Bootstrap resources, so verify those assets load in the intended network. |
| Evaluation environment | Record input identifiers, checkpoint identity, selected rules, geometry, settings, and run identifiers so observations can be reproduced. |

### 3.2.3 Processing capacity and resource sharing

Uploaded jobs are handled through an in-process sequential queue. Processing runs record states including queued, running, completed, failed, and cancelled. Users can follow progress while navigating other pages. Process in Background and Process and Watch Live are viewing choices for an uploaded-video job; the latter does not create a live-camera source. [S5]

The shared GPU reservation gives uploaded jobs priority over detail scans and live-frame inference. An in-progress holder is not interrupted. Live frames do not wait for the GPU and can be skipped when it is busy. This creates an operational tradeoff: long uploaded jobs can reduce live inference availability. The SOP therefore calls for checking competing workloads before a live demonstration.

The source-default upload limit is 3,072 MB, with configurable disk headroom checks in the upload helper. The actual limit visible to a user may also depend on environment settings and the deployment server. Neither the declared upload limit nor an accepted upload establishes successful processing of a file of that size. [S10]

<!-- pagebreak -->
### 3.2.4 Data storage and provenance requirements

SQLite stores users, videos, annotations, detections, review items, processing runs, violation records, settings, reports, and supporting case records. The source schema also includes review decisions, plate verifications, legal-policy versions and mappings, case actions, recurrence reviews, and permission assignments. These records link the original suggestion to later decisions and outputs. The scan examined definitions and adapter code rather than the contents of the operational database. [S6; S11]

Processing runs retain configuration and diagnostic information, including enabled rules, geometry snapshots, model identifiers, counts, status, and replay availability where recorded. Reprocessing must be treated as a new attempt. A failed new attempt and an earlier completed result have different identities, and operators should verify which result they are viewing before making comparisons. Source media, evidence, and generated reports remain file artifacts associated with database records. [S5; S11]

### 3.2.5 Access control and information protection

The implemented roles are System Administrator, Traffic Enforcement Officer, and Guest Viewer. Role checks restrict management, processing, review, and reporting actions. Authentication checks the current database user and session stamp; inactive users and invalid sessions are rejected. Supported legal capabilities add action-level checks. Administrator status should not be described as a substitute for every explicit policy permission. [S6; S10]

Evidence and reports are served through authenticated routes rather than public static-file paths. Passwords use bcrypt hashing. Production configuration requires an explicit Flask secret and enables secure cookies; session cookies are also marked HttpOnly and SameSite Lax in application setup. These controls are implementation features, not a complete security certification or proof that a particular deployment has been configured correctly.

Source footage can contain identifiable persons, plates, or sensitive locations. The project’s local governance document requires authorization covering the data owner, approved uses, access, retention, and sharing. Raw footage should remain in approved restricted storage, and public manuscript figures should use authorized or appropriately de-identified examples. No legal-compliance certification is asserted by this draft. [S13]

### 3.2.6 Model and dataset requirements

Training and evaluation data must respect the frozen class schema and distinguish observable labels from rule outcomes. Dataset versions should preserve source provenance and separate training, validation, and test material by whole source groups where possible, avoiding near-duplicate frames across splits. This is a recommended evaluation safeguard; the present scan did not certify the composition or independence of every existing dataset derivative.

The main detector, optional detail detector, and plate models have separate contracts and purposes. Replacing a checkpoint requires checking class order, capability coverage, provenance, and relevant evaluation evidence. An old checkpoint with a smaller roster may not support the same rule set as a fifteen-class checkpoint. No dataset size, training metric, or model promotion result is asserted here without a fresh evidence record.

<!-- pagebreak -->
### 3.2.7 Operational limitations and Chapter 4 alignment

TAVIDM’s useful output depends on visibility, camera geometry, tracking continuity, saved scene annotations, rule selection, and evidence completeness. Low resolution, occlusion, glare, camera movement, and ambiguous occupant associations can limit interpretation. Time-based rules also depend on usable recording time. A result should be reported with these conditions rather than presented as an unconditional capability.

The current application provides review-oriented candidate generation across its canonical scope, with four registry-implemented and eight partial categories. No Helmet remains blocked in the violation engine. A compatible four-class detail scan can add a separate ordinary pending review item from repeated positive uncovered-head evidence, but it does not confirm a violation. No Side Mirror lacks reliable mounting-area visibility evidence. Supplementary detail analysis and optional OCR do not remove these restrictions. Rule confidence is not a substitute for measured precision or recall.

Current source also contains newer behaviour than some documentation. The plate runtime now supports an explicit application mode in addition to isolated modes, and the traffic-sign evaluator includes additional maneuver candidate paths. The source-backed descriptions in this chapter should replace older blanket statements that OCR is entirely unwired or that only no-entry code exists. Actual runtime activation and real-video quality remain separate questions.

### Recommended enhancements to the supplied Chapter 4 outline

Keep the supplied Chapter 4 headings, with the following additions inside them rather than moving them into Chapter 3:

- 4.1 Research Design — state the actual study design, locale, participants, inclusion criteria, and approval basis. Confirm these from research records.
- 4.2 Developmental Methodology — name the process actually followed and support its phases with development artifacts; do not retrospectively claim a methodology solely from the code layout.
- 4.3 System Architecture — distinguish browser, Flask application, queue, inference, review services, database, and private media storage; show optional OCR and detail paths separately.
- 4.4 Data Collection Methods — add source authorization, provenance, annotation review, source-group splits, retention, and controls against duplicate-frame leakage.
- 4.5 Requirement Analysis and 4.6 Requirement Specification — map each requirement to an actor, prerequisite, expected output, failure condition, and verification method. Preserve candidate versus confirmed-case states.
- 4.7 System Design and 4.8 Tools and Technology — document actual interfaces and exact evaluated versions; avoid repeating Chapter 3’s general technology explanations.
- 4.9 Evaluation and Testing — separate software correctness, detector quality, tracking quality, event-level precision and recall, passage-count error, processing performance, OCR evaluation, and user evaluation. Report unavailable or unknown outcomes separately.

The repository’s evaluation harness requires human-reviewed ground truth and distinguishes positives, hard negatives, and unknown cases. Its handling of suppressed unknowns must be explained alongside reported metrics; a candidate count alone is not accuracy. Formal results, acceptance thresholds, respondent numbers, and questionnaire validation belong to Chapter 4 and the results chapter once supported by evidence. [S12]

<!-- pagebreak -->
## Appendix A Standard Operating Procedure

### A1 Purpose scope and responsibility

This SOP describes the normal use of TAVIDM for authorized footage preparation, candidate detection, evidence review, case-related actions, and reporting. It is based on the inspected interfaces and source. The project owner should approve its local account assignments, storage locations, retention schedule, and escalation contacts before operational adoption. It does not authorize collection, release, or enforcement action beyond an operator’s existing role.

| Role | Main operating responsibilities |
| --- | --- |
| System Administrator | Maintain accounts, settings, and camera records; manage supported access assignments; perform administrator-restricted plate verification; supervise source deletion and configuration changes. |
| Traffic Enforcement Officer | Prepare authorized videos and annotations, start processing, inspect candidates, save permitted review decisions, and generate reports. Case actions depend on effective permissions. |
| Guest Viewer | Inspect permitted dashboards, monitoring views, violation listings, and analytics. Do not assume access to upload, review, reporting, or administrative actions. |
| Project owner or designated supervisor | Approve operating context and data use, identify the escalation contact, and authorize the local handling of unresolved cases and policy questions. This is an operating responsibility, not an additional built-in account role. |

### A2 Before each session

1. Open the application address supplied by the administrator and sign in with your own account. Confirm the displayed role and intended system instance.

2. Use only footage or a camera source approved for the task. Keep the original source unchanged. For a test session, use agreed test data and clearly label test records.

3. Record the source identifier, camera or viewpoint, recording date and time when known, and the purpose of the session. Leave unknown facts unknown.

4. Confirm adequate free space, the expected model or capability status, and the relevant system settings. Ask the administrator to resolve unavailable prerequisites rather than bypassing the controls.

5. Check Processing Jobs for competing work. Schedule long video processing and live-camera demonstrations with the shared inference resource in mind.

6. Prepare a session log with the operator, date, source, intended rules, annotation or template, run identifier when created, and actual outcome. Do not record passwords or camera credentials.

Completion check: the correct account, authorized input, intended camera geometry, and available capabilities have been identified before processing begins.

<!-- pagebreak -->
### A3 Upload a video

1. Open Live Monitor and select one MP4 using Browse Files or the upload area. Verify the filename and select the appropriate Traffic Condition.

2. Enter the actual recording date and time only when supported by the source. Do not use the upload date as an estimate of recording time.

3. Check the displayed size limit and start Upload. Wait for the upload to finish. If validation or decoding fails, record the error and use a verified copy of the source; do not treat a renamed non-video file as a valid MP4.

4. Confirm the uploaded video appears with the correct identity. Uploading saves the input; processing is a separate step. To prepare a batch, upload videos individually.

Completion check: the intended source appears once and is available for annotation. An upload failure must be resolved before annotation or processing is considered complete.

### A4 Create or apply scene annotations

1. Choose Create New Zone Annotation and Continue to Editor, or select a verified template matching the same camera position, field of view, and road layout.

2. Draw the applicable road regions. Use No Parking Zone for parking analysis, Active Lane or Pedestrian Crossing for obstruction, Truck Ban Zone for a time-based truck restriction, and No Loading/Unloading Zone for relevant terminal analysis. Draw only geometry supported by the footage.

3. For directional rules, define the lane and its allowed flow arrow. For sign or marking candidates, save the required related sign, lane, threshold, or marking objects. Define the prohibited side correctly when relevant. A vehicle counting line alone does not establish a no-entry threshold.

4. For prohibited-turn candidates, the current evaluator requires one matching approach flow and threshold for the sign’s lane. For overtaking candidates, it requires matching signed lane geometry and flow. Treat these as partial candidate features and validate the resulting evidence carefully.

5. Add supported activity regions where the intended rule requires activity context. Do not interpret an activity-region drawing as proof that activity occurred.

6. Select Save Annotation. Choose Use for This Video Only unless a deliberate template action is intended, then Confirm Save. Reopen the annotation and verify positions and relationships.

Completion check: the saved geometry matches the current view and the video is eligible for processing. Reusing a template after camera movement requires checking and, where necessary, redrawing the geometry.

<!-- pagebreak -->
### A5 Process and inspect results

1. Open the video’s processing action. In Confirm Processing Run, review enabled, disabled, and unavailable rules. Select the relevant available rules and record the selection.

2. Choose Process in Background or Process and Watch Live, then Start Processing. Both choices process the uploaded file. The watch option is a preview, not a connection to a camera.

3. Follow the queue and run status. The run can finish as completed, failed, or cancelled. Do not infer completion from a still image or a page that stopped updating. If you cancel, wait for the final state before submitting another attempt.

4. For several prepared videos, use the selection controls and Process Selected. Review the accepted, rejected, or already-queued feedback. The bulk workflow does not offer the same per-video rule-selection popup; verify the relevant defaults before starting a batch.

5. When processing completes, verify the source and run identity, open available replay and evidence, and inspect diagnostics. Record unavailable rules separately from rules that ran without producing candidates.

6. Compare at least one suspected event and one normal interval with the source. If counting lines are configured, compare passage totals against a manual count over the same interval and line. Record false alarms, missed events, and uncertain examples by video time.

Completion check: the intended run has a final status, its outputs are linked to the correct source, and any missing evidence or capability is recorded.

### A6 Decide a Review Queue item

1. Open Review Queue with an authorized account. Check the source, event time, original suggestion, reason log, confidence, stills, crops, and temporal evidence available.

2. Select Confirm proposed violation only when the proposed category is supported. Select the correction option when another canonical category is supported, and choose that category explicitly.

3. Select No violation when the evidence supports that finding. Select Insufficient evidence when the available material cannot support a reliable decision. Do not use these outcomes interchangeably.

4. Enter a specific reason and save. Reopen the item to verify the stored decision. If another reviewer has already acted and the server reports a conflict, reload and inspect the saved decision before continuing.

5. If temporal evidence is still finalizing, wait for completion. Do not attempt to bypass the evidence requirement.

Completion check: the saved disposition and reason match the reviewer’s assessment. Confirmation or correction may create or link a case; no-violation and insufficient-evidence decisions do not create a case.

<!-- pagebreak -->
### A7 Review identity time and case actions

1. Open the reviewed case and verify that its observations belong to the intended source, track context, and canonical category. A track number alone does not identify an owner or registration.

2. Inspect available plate evidence. An OCR suggestion is a machine candidate. The administrator authorized to verify plate identity must check readable evidence and record only a supported result. Preserve unreadable or unknown states when appropriate.

3. Inspect event-time provenance. Use the event-time review workflow when a supported correction is needed. Keep recording time separate from upload and processing times.

4. Read the displayed policy status and case prerequisites. Perform case confirmation only when the account’s effective permissions and the case state allow it. Escalate missing authority or unsupported policy mappings to the designated supervisor.

5. Open the printable record and check the source details, category, identity status, and time. Record Notice printed only after the actual agreed printing action has occurred. A preview or PDF download is not proof of printing.

Completion check: identity, event time, policy status, and action records accurately reflect what was verified and performed. Any unresolved element remains visible.

### A8 Generate and verify reports

1. Open Reports using an authorized account. Enter a Report Name and a date range relevant to the required records. Apply the needed violation and source filters.

2. Generate the required PDF or Excel report. Open the downloaded file and check headings, dates, selected records, plate verification labels, legal mapping status, and readability.

3. Confirm the report appears in Report History. Compare a small sample of exported rows with the corresponding application records.

4. The inspected reporting module has a 5,000-row cap. For large reporting periods, use narrower filters and verify completeness rather than assuming one export contains every record.

5. Store or share the report only through the approved project procedure. Do not present a machine candidate or unverified identity as a confirmed finding in a presentation or summary.

Completion check: the export opens, matches its filters, and accurately represents the stored status of its records. An empty report should be checked against the selected filters before being interpreted as no activity.

<!-- pagebreak -->
### A9 Live camera and optional analysis

1. The administrator creates or maintains the authorized camera record. Keep connection credentials out of screenshots, reports, and shared logs.

2. Start the intended stream from Live Monitor. Verify the camera view and status. Check that the available geometry, clock context, and model capabilities are suitable for the intended rules; do not assume they match a separately uploaded video.

3. Observe diagnostics and competing processing jobs. A busy GPU can cause live frames to be skipped. A visible stream is not proof that every frame is being evaluated.

4. Stop the stream when finished and verify its status. If the connection fails or the view changes, stop interpreting new outputs until the source and context have been checked.

5. Use Motorcycle Detail Review only when the designated compatible model and evidence are available. Save detail outcomes within that workflow; do not treat them as automatically confirmed cases.

6. If optional plate OCR is authorized and configured, inspect its reported status and evidence. If it is disabled, unavailable, or shows No attempt, record that state. Do not change model paths or activation settings simply to force a result during a routine review session.

### A10 Handling operational exceptions

| Situation | Required response |
| --- | --- |
| Upload rejected or disk space insufficient | Read the error, verify format and size, and ask the administrator to resolve capacity. Preserve the source file. |
| Processing fails or appears stalled | Record source, run identifier, visible status, elapsed time, and error. Check queue and resource status before another attempt. |
| No candidates produced | Check selected rules, capability diagnostics, saved geometry, visibility, and time context. Do not equate zero candidates with proven compliance. |
| Evidence missing or incomplete | Wait if finalization is pending; otherwise record the problem and use Insufficient evidence when a decision is required and unsupported. |
| Another reviewer saved first | Reload and inspect the authoritative decision. Do not try to overwrite it through repeated submissions. |
| Wrong source or annotation shown | Stop the decision or processing action, record identifiers, and correct the source–annotation relationship before a new run. |
| Unexpected permissions | Stop the restricted action and ask the administrator to verify the account’s effective access. |
| Camera offline or inference busy | Check authorized connectivity and competing work. Resume assessment only after verifying the correct live source and status. |

<!-- pagebreak -->
### A11 Administrative maintenance and session closure

1. Create individual accounts with the appropriate role. Use the access-management interface for supported capability assignments. Verify the selected account before saving a change, then reopen its access summary to confirm the effective state.

2. Review default rules and parameters before a demonstration or evaluation. Record any change, its reason, and the original value. Changes intended for one processing run should not be mistaken for a change to all global defaults.

3. Use Remove Results only when removal of that video’s processing outputs is authorized. It is distinct from deleting the source video. Source deletion is an administrator action and should target only the intended record after required retention and backup checks.

4. Maintain coordinated backups of the database and related media using an approved procedure. The inspected application does not establish a complete automated backup and restore service. Test restoration separately before relying on a backup as recovery evidence.

5. At session end, verify saved reviews and reports, record unresolved issues, restore temporary test settings where appropriate, stop unnecessary camera streams, and sign out. Share only authorized evidence with the designated supervisor.

### A12 Operating record template

Copy the following fields into the team’s approved session log. Enter Not applicable or Unknown where appropriate rather than supplying invented values.

| Field | Entry to record |
| --- | --- |
| Session | Date, operator, role, system instance, and purpose |
| Source | Video or camera identifier, original filename where relevant, and authorized data owner |
| Time context | Recording time and basis, or Unknown; do not substitute upload time |
| Geometry | Annotation or template identifier and confirmation that the view matches |
| Processing | Run identifier, selected rules, relevant settings, model identifier when available, and final status |
| Evidence review | Review item, event-relative time, disposition, reason, and any unavailable evidence |
| Case actions | Case identifier, identity and time verification status, permitted actions actually performed |
| Reports | Report identifier, filters, export format, storage location, and completeness check |
| Problems and handover | Reproduction steps, actual outcome, screenshot reference, unresolved prerequisite, and person assigned |

### A13 Approval before operational adoption

Procedure owner: [confirm designated person or office]. Approved application version: [confirm release or commit]. Approved data storage and retention: [confirm local policy]. Incident and technical contact: [confirm contact]. SOP approval date and approver: [complete after review].

These fields are organizational decisions that source code cannot supply. The SOP can be used as a draft training guide immediately, but its approval fields should be completed before it is represented as the organization’s adopted procedure.

<!-- pagebreak -->
## Appendix B Evidence and manuscript completion notes

### B1 Scope of the system scan

The scan covered the application route surface, Python module inventory, database schema and migration definitions, current class and violation registries, selected implementation bodies, templates and JavaScript for the principal workflows, test inventory, configuration defaults, and relevant documentation. A local source inventory records file paths and hashes for traceability. The source review prioritized ingestion, annotation, inference, tracking, rules, processing jobs, evidence, review decisions, cases, identity, reports, access, and optional analysis.

This is a source-based documentation scan, not an exhaustive vulnerability audit or a completed deployment acceptance test. The application, full test suite, real CCTV streams, model inference, operational database contents, and installed package environment were not exercised or certified for this document. Existing test files and previous completion logs establish areas of intended coverage, not fresh pass results. User-owned uncommitted files were preserved.

### B2 Internal source register

All internal paths below are relative to D:\tavidm and were inspected for this draft on 11 October 2026. These identifiers support manuscript traceability; they are not published academic references.

| ID | Sources and subject supported |
| --- | --- |
| S1 | config/training/class_schema.json; core/detection_config.py — class counts, canonical categories, registry status, confidence bands, and defaults. |
| S2 | core/detector.py; core/tracker.py — weight-selection and class-contract logic, ByteTrack call, histories, direction, and image-space motion. |
| S3 | core/zone_config.py; core/scene_annotation.py — zone names, structured scene objects, validation, and context projection. |
| S4 | core/violation_engine.py; core/model_capability.py; core/rule_confidence.py — candidate paths, evidence prerequisites, blocked rules, and score composition. |
| S5 | app.py; core/video_processor.py; core/live_stream.py; core/processing_jobs.py; core/gpu_inference_slot.py; core/vehicle_crossing_counter.py — orchestration, jobs, resource sharing, and passage counting. |
| S6 | core/review_decision_service.py; core/case_review_service.py; templates/review_queue.html; app.py — four decisions, reasons, evidence readiness, case handling, and action routes. |
| S7 | core/motorcycle_detail_contract.py; core/motorcycle_detail.py; core/motorcycle_detail_scan.py; config.py — separate detail analysis and compatible checkpoint contracts. |
| S8 | core/event_time.py; core/recording_time.py; core/case_review_service.py — recording-time provenance and reviewed event-time handling. |

<!-- pagebreak -->
### B3 Additional source register and references

| ID | Sources and subject supported |
| --- | --- |
| S9 | core/plate_runtime.py; core/plate_settings.py; core/plate_jobs.py; core/plate_manifest.py; core/plate_review.py; upload and live integration hooks — optional OCR, execution modes, evidence boundaries, and review. |
| S10 | requirements.txt; config.py; core/auth.py; core/upload.py; core/reports.py; templates/base.html; templates/live_monitor.html; templates/reports.html — dependencies, roles, upload controls, report cap, and UI labels. |
| S11 | database/schema.sql; database/migrations; database/sqlite_adapter.py; database/db.py — persistence definitions, run attribution, review decisions, and case-support records. |
| S12 | core/evaluation_harness.py; tests/test_traffic_sign_maneuver_rules.py; repository test inventory — evaluation contract and intended regression coverage. Tests were not executed for this document. |
| S13 | docs/LOCAL_DATA_GOVERNANCE_PHASE_2.md; docs/testing/2026-10-10/START-HERE-CHECKLIST.md — local data authorization and operator workflow context. Older checklist statements are superseded where current source differs. |

E1. Ultralytics. (n.d.). Explore Ultralytics YOLOv8. Official documentation. Retrieved 11 October 2026 from https://docs.ultralytics.com/models/yolov8/

E2. Zhang, Y., Sun, P., Jiang, Y., Yu, D., Weng, F., Yuan, Z., Luo, P., Liu, W., and Wang, X. (2022). ByteTrack: Multi-Object Tracking by Associating Every Detection Box. arXiv:2110.06864, version 3. https://doi.org/10.48550/arXiv.2110.06864

E3. Pallets. (n.d.). Flask documentation. Retrieved 11 October 2026 from https://flask.palletsprojects.com/en/stable/

E4. SQLite. (n.d.). Appropriate Uses For SQLite. Retrieved 11 October 2026 from https://www.sqlite.org/whentouse.html

### B4 Items to complete before manuscript submission

Confirm the institution’s required font, spacing, margins, citation style, and official project title. Replace manuscript pagination using the actual combined thesis; the page numbers from the supplied outline were not copied into this standalone document. Verify the exact release, loaded checkpoints, installed versions, hardware, and camera setup used in the study.

Add approved diagrams and screenshots to Chapter 4, preserving the distinction between source capabilities and observed results. Supply research locale, participants, dataset provenance, evaluation sample sizes, acceptance criteria, and measured results from actual records. Do not transfer numerical benchmark results from the ByteTrack paper or Ultralytics documentation into TAVIDM’s findings. Review and approve the SOP’s organizational fields before adoption.
