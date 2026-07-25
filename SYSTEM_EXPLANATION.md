# SYSTEM_EXPLANATION.md

## TAVIDM: Traffic Violation Detection and Monitoring System Using Computer Vision and Deep Learning

This document is a technical reference for BSIT students preparing for a thesis proposal defense.  
It explains the current project scope and architecture based on the existing codebase.

---

## 1. Project Overview

### Purpose of the system
TAVIDM is an AI-assisted traffic monitoring system that helps operators analyze **uploaded recorded CCTV videos** and identify traffic violations for review.

### Problem being solved
Manual review of long CCTV recordings is slow, inconsistent, and labor-intensive.  
Operators may miss violations because they must watch videos continuously. TAVIDM aims to reduce this workload by assisting with structured detection and review.

### Objectives
- Provide a workflow for uploading and organizing recorded traffic videos.
- Allow per-video traffic **zone annotation** and reusable zone templates.
- Process videos through a computer vision pipeline (OpenCV -> YOLOv8m -> BYTETrack -> Rule Engine).
- Detect supported violations and store evidence for review.
- Present results through a Flask web interface with monitoring and reporting views.

### Scope and limitations (current)
**In scope**
- Upload MP4 videos and store metadata in SQLite; RTSP live camera streams.
- Annotate zones per video and save reusable templates (6 zone types).
- Full frame-by-frame inference loop (`core/video_processor.py`): OpenCV → YOLOv8m → ByteTrack → Rule Engine → evidence snapshots → review queue.
- Rule Engine implementation for 10 violations:
  Illegal Parking, Illegal Stopping, Obstruction, Counterflow Driving,
  Blocking Pedestrian Crossing, Truck Ban Violation, Illegal Loading/Unloading,
  Restricted Lane Violation, No Helmet Violation, Motorcycle Overloading.
- Authentication (bcrypt) with admin / enforcer / viewer roles.
- Analytics from database aggregations; PDF/Excel report generation.

**Limitations in current codebase**
- Helmet and rider-based rules require the custom-trained YOLOv8m weights
  (with helmet/rider classes) in `models/`; with the pretrained COCO fallback
  those rules stay inactive.
- Violations that need behavior classification (e.g. Reckless Driving),
  lane estimation (overtaking), or unavailable detection classes are registered
  as not implemented in `core/detection_config.py` with the reason recorded.

### Why uploaded CCTV videos instead of live CCTV
- **Feasibility**: easier to implement and test in a BSIT capstone scope.
- **Operational control**: allows repeatable testing, annotation, and reprocessing.
- **Lower deployment complexity**: no live stream ingestion, network jitter handling, or real-time infrastructure.
- **Research validity**: same video can be replayed for debugging and evaluation.

---

## 2. System Architecture

### Overall architecture
TAVIDM follows a Flask monolith architecture with modular processing components in `core/` and data access in `database/`.

### Major components
- **Web Layer (Flask + HTML/CSS/JS)**  
  Upload, annotation, configuration, monitoring, and report pages.
- **Processing Layer (`core/`)**  
  Frame extraction, detection, tracking, rule evaluation, evidence logic.
- **Data Layer (`database/`)**  
  SQLite schema and CRUD functions for videos, annotations, templates, detections, violations, review queue.

### Responsibilities of each component

| Component | Responsibility |
|---|---|
| `app.py` | Routes, API endpoints, template rendering, flow orchestration |
| `core/upload.py` | Upload validation and secure file handling |
| `core/frame_extract.py` | Extract first video frame for annotation |
| `core/zone_config.py` | Zone schema/validation and required zone types |
| `core/detector.py` | YOLOv8m inference + ByteTrack wrapper (Ultralytics) |
| `core/tracker.py` | Per-track motion history: trajectory, direction, dwell |
| `core/violation_engine.py` | Rule-based violation evaluation |
| `core/video_processor.py` | End-to-end pipeline orchestrator |
| `database/db.py` | SQLite access layer and migrations |
| `database/schema.sql` | Persistent schema definitions |

### End-to-end data flow
1. Operator uploads MP4.
2. System stores video and extracts first frame.
3. Operator annotates required zones or applies template.
4. Processing context is loaded (video path + zones).
5. Pipeline runs detection, tracking, then rule evaluation.
6. Violation events and evidence metadata are stored.
7. Results are presented in UI for review/reporting.

### ASCII architecture diagram
```text
                    +----------------------+
                    |    Flask Web UI      |
                    | Upload/Annotate/View |
                    +----------+-----------+
                               |
                               v
                    +----------------------+
                    |   app.py API/Routes  |
                    +----------+-----------+
                               |
          +--------------------+--------------------+
          |                                         |
          v                                         v
+----------------------+                 +----------------------+
|  core/ processing    |                 |   database/db.py     |
| OpenCV, YOLO, Track, |<--------------->|   SQLite operations  |
| Rule Engine          |                 |   schema + CRUD      |
+----------------------+                 +----------------------+
          |
          v
+----------------------+
| Violations/Evidence  |
| Review + Analytics   |
+----------------------+
```

---

## 3. Conceptual Framework

### Input-Process-Output (IPO) framework in TAVIDM
The conceptual framework models how the system transforms raw traffic video into actionable monitoring outputs.

### Inputs
- Uploaded CCTV MP4 files
- Zone annotations (`no_parking`, `active_lane`, `pedestrian_crossing`, `truck_ban_zone`)
- Template selections and rule parameters
- Model/runtime configuration

### Process
- Video/frame handling (OpenCV)
- Object detection (YOLOv8m wrapper)
- Multi-object tracking (BYTETrack stage)
- Rule evaluation on tracked entities
- Persistence to SQLite

### Outputs
- Violation records with metadata
- Evidence references
- Review queue items
- Dashboard/report summaries

### Why each stage exists
- **Input**: collects both visual data and scene context.
- **Process**: turns pixels into structured, explainable violation decisions.
- **Output**: supports operator review, accountability, and reporting.

### ASCII IPO diagram
```text
+------------------------- INPUT --------------------------+
| Uploaded Video | Zone Annotations | Templates | Settings |
+---------------------------+------------------------------+
                            |
                            v
+------------------------ PROCESS -------------------------+
| OpenCV -> YOLOv8m -> BYTETrack -> Rule Engine -> SQLite |
+---------------------------+------------------------------+
                            |
                            v
+------------------------- OUTPUT -------------------------+
| Violations | Evidence | Review Queue | Reports/Analytics |
+----------------------------------------------------------+
```

---

## 4. Technical Background

This section explains each technology and why it fits this project.

### Python
**Concept:** General-purpose language with strong AI/computer vision ecosystem.  
**Use in TAVIDM:** Backend logic, processing pipeline, DB access.  
**Why selected:** Fast development for BSIT scope, rich libraries, clear syntax.

### Flask
**Concept:** Lightweight Python web framework.  
**Use in TAVIDM:** Routes, APIs, template rendering, upload/annotation workflow.  
**Why selected:** Simple and modular for prototype-to-capstone builds.

#### Flask vs Django
| Criteria | Flask | Django |
|---|---|---|
| Learning curve | Lower | Higher |
| Built-in features | Minimal | Many built-in modules |
| Fit for TAVIDM | Good (custom pipeline control) | Could be heavier than needed |
| Decision | **Chosen** | Not chosen for current scope |

### OpenCV
**Concept:** Computer vision library for frame/video operations.  
**Use in TAVIDM:** First-frame extraction and planned frame iteration for inference.  
**Why selected:** Standard CV toolkit with proven video I/O support.

### YOLOv8m
**Concept:** Medium YOLOv8 variant — higher accuracy than nano/small at moderate inference cost.  
**Use in TAVIDM:** Detect motorcycles, persons, trucks, helmets (when trained/available).  
**Why selected:** Team decision for the production detector (manuscript Specific Objective also evaluates YOLOv8m).

#### YOLOv8m vs YOLOv8n
| Criteria | YOLOv8n | YOLOv8m |
|---|---|---|
| Speed | Faster | Slower than n |
| Accuracy | Lower | Higher |
| Resource demand | Lower | Higher |
| Decision | Not chosen | **Chosen** |

### BYTETrack
**Concept:** Multi-object tracker that keeps object IDs consistent across frames.  
**Use in TAVIDM:** Track continuity for time-based violations (parking duration, direction trends).  
**Why selected:** Strong performance in crowded scenes and suitable for traffic trajectories.

#### BYTETrack vs DeepSORT
| Criteria | BYTETrack | DeepSORT |
|---|---|---|
| Association strategy | Uses high+low score detections effectively | Uses appearance embedding + Kalman |
| Complexity | Lower integration overhead | More components to tune |
| Traffic use-case suitability | Strong | Also strong |
| Decision | **Chosen** for practical integration and performance |

### SQLite
**Concept:** Embedded relational database in a single file.  
**Use in TAVIDM:** Stores videos, zones, detections, violations, review queue.  
**Why selected:** Zero-server setup, ideal for single-node prototype deployments.

### Roboflow
**Concept:** Dataset management and annotation/training support platform for CV.  
**Use in TAVIDM:** Dataset preparation workflows for model training iterations.  
**Why selected:** Speeds up annotation lifecycle and export pipelines.

### HTML/CSS/JavaScript
**Concept:** Frontend stack for web interfaces.  
**Use in TAVIDM:** Upload interface, annotation tools, monitoring pages, analytics/report views.  
**Why selected:** Lightweight web delivery with no heavy frontend framework required.

---

## 5. Methodology

### End-to-end methodology (current and intended pipeline)

1. **Video Upload**
   - Operator uploads MP4 via Flask endpoint.
   - File validated and saved in `dataset/raw/`.
   - Video metadata inserted into `videos`.

2. **Frame Extraction**
   - First frame extracted using OpenCV.
   - Frame stored in `dataset/frames/` for annotation canvas.

3. **Zone Annotation**
   - Operator draws required polygons.
   - Zones validated through `core/zone_config.py`.
   - Annotation saved in `annotations`.

4. **Saved Annotation Templates**
   - Operator may save/reuse templates (`zone_templates`).
   - Selected template can be applied and adjusted per video.

5. **YOLO Object Detection**
   - Per-frame detections are produced by `core/detector.py` (Ultralytics YOLOv8m).

6. **ByteTrack Tracking**
   - Detections are associated into persistent tracks by ByteTrack; `core/tracker.py`
     maintains per-track trajectory, direction, and dwell-time history.

7. **Rule Engine Evaluation**
   - Rules evaluate tracked detections with persistence thresholds.
   - Zone/dwell rules: Illegal Parking, Illegal Stopping, Obstruction,
     Blocking Pedestrian Crossing, Illegal Loading/Unloading, Restricted Lane.
   - Direction rule: Counterflow Driving. Time-based rule: Truck Ban.
   - Detection rules: No Helmet Violation, Motorcycle Overloading (custom weights).

8. **Violation Detection**
   - Generated `ViolationEvent` objects represent candidate/confirmed violations.

9. **Evidence Generation**
   - Evidence path and reason logs are attached (framework exists; full clip extraction can be expanded).

10. **Database Storage**
    - `detections`, `violations`, and `review_queue` store structured outputs.

11. **Reports and Analytics**
    - Flask templates render violation and analytics pages.
    - Report generation tools are in project dependencies and UI flow.

### Step-by-step pipeline
```text
Upload MP4
  -> Save video metadata
  -> Extract first frame
  -> Annotate zones / apply template
  -> Load processing context
  -> Detect objects (YOLO)
  -> Track objects (BYTETrack)
  -> Evaluate rules
  -> Save violations/evidence
  -> Display results + reports
```

---

## 6. Rule Engine

### What a Rule Engine is
A Rule Engine is a module that uses explicit, human-readable logic (`if/then` conditions) to classify events as violations.

### Why it is needed
Object detection alone cannot reliably decide legal violations because many violations require:
- time persistence,
- zone context,
- behavior interpretation.

### Why AI alone is insufficient
YOLO detects objects per frame, but violations depend on:
- motion over multiple frames,
- track identity continuity,
- contextual restrictions (zone + policy schedule).

### How AI and Rule Engine work together
- **AI (YOLO/BYTETrack):** extracts entities and trajectories.
- **Rule Engine:** applies legal/operational logic to those entities.

### Advantages
- Transparent and explainable decisions.
- Faster to iterate policy thresholds than retraining full models.
- Better alignment with local traffic-rule interpretation.

### Limitations
- Sensitive to zone annotation quality.
- Can be brittle if thresholds are poorly tuned.
- Complex behaviors may still require manual review.

### Detection logic by violation (project scope)

> Note: In the current codebase, explicit implemented logic exists for **No Helmet** and **Motorcycle Overloading**.  
> Other listed violations are included in system scope and architecture, with zone-rule scaffolding for future implementation.

| Violation | Rule concept | Implementation status |
|---|---|---|
| Illegal Parking | Vehicle stationary in no-parking zone beyond threshold | Planned |
| Counterflowing | Vehicle trajectory opposite allowed lane direction | Planned |
| Obstruction of Traffic | Vehicle blocks active lane flow beyond threshold | Planned |
| Illegal Loading/Unloading | Stopping behavior in disallowed loading zones | Planned |
| Blocking Pedestrian Crossing | Vehicle overlaps crosswalk area while stationary | Planned |
| Truck Ban Violation | Truck detected in truck-ban zone/time window | Planned |
| No Helmet | Motorcycle-associated rider lacks helmet for persistence window | **Implemented in `violation_engine.py`** |
| Motorcycle Overloading (>2) | >2 riders associated with one motorcycle for persistence window | **Implemented in `violation_engine.py`** |
| Reckless Driving (Manual Review) | Suspicious motion patterns forwarded to review queue | Planned/manual-review design |

---

## 7. AI Pipeline

### Pipeline path
`OpenCV -> YOLOv8m -> BYTETrack -> Rule Engine -> SQLite`

### Data exchanged between stages

| Stage | Input | Output |
|---|---|---|
| OpenCV | Video file | Frames + timestamps |
| YOLOv8m | Frame | Detections: class, confidence, bbox |
| BYTETrack | Detections over time | Tracked detections with `track_id` |
| Rule Engine | Tracked objects + zones + thresholds | `ViolationEvent` objects |
| SQLite layer | Events + metadata | Persistent records for review/reporting |

### Current implementation note
- The pipeline orchestrator is defined in `core/video_processor.py` and is fully
  implemented: frame loop, detection, tracking, rule evaluation, evidence
  snapshots, and review-queue persistence. RTSP live streams run the same
  pipeline via `core/live_stream.py`.

---

## 8. Database

### Database purpose
Provide persistent, queryable records for videos, annotations, detections, and violations.

### Major tables

| Table | Purpose |
|---|---|
| `users` | User accounts and roles |
| `videos` | Uploaded video metadata and processing status |
| `zone_templates` | Reusable annotation templates |
| `annotations` | Per-video zone polygons |
| `detections` | Frame-level AI detections/tracks |
| `violations` | Confirmed violation records |
| `review_queue` | Items requiring human validation |

### Stored information
- Video metadata: filename, path, condition, status
- Zone geometry JSON
- Detection geometry/confidence per frame
- Violation type, track ID, confidence, reason log, evidence path, review status

### Why SQLite was chosen
- Easy setup for prototype and defense demos.
- File-based portability for development/testing.
- Sufficient for single-instance BSIT deployment context.

---

## 9. Design Decisions

### Why uploaded videos instead of live CCTV
- Lower infrastructure complexity and easier validation.
- Supports repeatable experiments and manual annotation workflow.

### Why YOLOv8m
- Better accuracy than nano while still lightweight enough for practical processing.

### Why BYTETrack
- Good track continuity with practical integration overhead for traffic scenes.

### Why Flask
- Fast backend development with straightforward API + template rendering.

### Why SQLite
- Minimal operational overhead and fast local prototyping.

### Why Rule Engine
- Transparent decision logic for legally contextual violations.
- Easier to justify in a proposal defense than black-box end-to-end classification.

### Why speed detection was removed
- Reliable speed estimation from arbitrary uploaded videos requires scene calibration and camera geometry; this exceeded current project validity constraints.

### Why reckless driving requires manual review
- Reckless driving is behaviorally complex and context-sensitive.
- Current scope treats it as decision support, not fully automated legal judgment.

---

## 10. System Strengths and Limitations

### Strengths
- Clear modular architecture.
- Practical upload + annotation workflow already implemented.
- Explainable violation logic through rule-based design.
- Database schema prepared for full pipeline expansion.
- Human-in-the-loop structure reduces over-automation risk.

### Limitations
- Helmet/rider rules require custom-trained YOLOv8m weights in `models/`.
- Violations needing behavior classification or unavailable classes are
  registered as not implemented (see `core/detection_config.py`).
- Accuracy depends heavily on model training quality and annotation precision.
- SQLite may become limiting for multi-user, high-throughput deployments.

---

## 11. Future Improvements

- Train and deploy the custom YOLOv8m weights (helmet/rider classes) in `models/`.
- Benchmark precision/recall/mAP/FPS on the custom dataset.
- Expand rule coverage as additional detection classes become available.
- Add stronger evidence generation (automatic clips around event windows).
- Introduce confidence calibration and uncertainty-aware review routing.
- Add authentication hardening and role-based access controls.
- Migrate to PostgreSQL for larger deployments.
- Add formal evaluation module (Precision, Recall, F1, mAP) using held-out data.

---

## 12. Possible Thesis Panel Questions

Below are likely questions and concise **key points** to prepare.

### A. Project Overview

| Likely Question | Key points to prepare |
|---|---|
| Why is this problem important? | Manual review is slow; system improves efficiency and consistency |
| Why no live CCTV yet? | Feasibility, controllability, and repeatable evaluation |

### B. Architecture and Framework

| Likely Question | Key points to prepare |
|---|---|
| Why modular architecture? | Separation of concerns, easier testing and upgrades |
| How does IPO support your objectives? | Inputs map to context, process to CV+rules, outputs to operator decisions |

### C. Technology Choices

| Likely Question | Key points to prepare |
|---|---|
| Why Flask not Django? | Lightweight and sufficient for current workflow |
| Why YOLOv8m not other variants? | Team-selected medium variant (higher accuracy than s/n; named in Specific Objective comparison) |
| Why BYTETrack over DeepSORT? | Practical performance and integration fit |

### D. Methodology and Rule Engine

| Likely Question | Key points to prepare |
|---|---|
| Why not AI-only violation detection? | Violations are contextual/time-based; rules provide explainability |
| How do rules reduce false positives? | Persistence windows, association constraints, review queue |

### E. Data and Database

| Likely Question | Key points to prepare |
|---|---|
| Why SQLite? | Prototype suitability and low ops overhead |
| What ensures traceability? | Structured tables for detections, violations, reason logs, statuses |

### F. Risks and Limitations

| Likely Question | Key points to prepare |
|---|---|
| What are your biggest technical risks? | Custom model training quality, tracking quality, annotation consistency |
| How will you improve this post-proposal? | Train custom weights, benchmark metrics, expand rule coverage |

---

## Appendix: Quick Reference Flow

```text
Recorded MP4 Upload
  -> First-frame extraction
  -> Zone annotation/template
  -> Processing context
  -> Detection (YOLOv8m)
  -> Tracking (BYTETrack)
  -> Rule-based violation decision
  -> SQLite persistence
  -> Review and reporting UI
```

