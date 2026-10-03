# Experimental local plate detection and OCR

**Status: implemented, tested, measured — and NOT enabled.** The experimental
defense-demo gate **failed** on the owner-selected footage, so the demo
launcher refuses to start. See [Demo gate verdict](#demo-gate-verdict).

This feature is:

* **disabled by default** — a stock checkout never runs plate OCR;
* **experimental** — every surface is labelled as such, and machine text is a
  *candidate* only;
* **human-in-the-loop** — only an active System Administrator can confirm a
  plate identity, and only through an explicit action.

It never creates or confirms a violation, never identifies a person, never
initiates enforcement, and never uploads footage, crops, or results anywhere.

---

## 1. Contents

| File | Role |
|---|---|
| `core/plate_settings.py` | Explicit enablement, frozen bounds/quality, artifact hashing, demo gate |
| `core/plate_onnx_backend.py` | Local ONNX Runtime backend (detector + OCR stages) |
| `core/plate_manifest.py` | Private, versioned, atomic machine-attempt storage |
| `core/plate_jobs.py` | Bounded collector, single inference worker, budgets, outcome taxonomy |
| `core/plate_review.py` | Read-side helpers, candidate resolution, admin confirmation provenance |
| `core/plate_runtime.py` | Process-scoped runtime holder |
| `scripts/plate_ocr_evaluate.py` | Local evaluation harness (freezes units, scores the gate) |
| `scripts/plate_ocr_benchmark.py` | Latency / memory benchmark |
| `scripts/run_plate_ocr_demo.py` | Process-scoped demo launcher (refuses to start while the gate fails) |
| `tests/test_plate_ocr.py` | Focused feature tests (temporary DB + temporary evidence) |
| `tests/test_plate_ocr_offline.py` | Offline / real-ONNX tests (sockets and hub downloads blocked) |
| `tests/js/test_plate_ocr_labels.cjs` | Presentation-layer contracts (states, escaping, admin-only controls) |

`core/plate_processing.py` and `core/case_review_service.py::process_plate_for_evidence`
were **not** modified; the injected adapter contract is used as designed.

---

## 2. Artifacts

Both artifacts were acquired once, out of band, and are used offline at runtime.

### Detector — YOLOv9-tiny license-plate end-to-end

| Field | Value |
|---|---|
| Registry name | `yolo-v9-t-384-license-plate-end2end` |
| Published filename | `yolo-v9-t-384-license-plates-end2end.onnx` (**plural**) |
| Publisher | ankandrew (`open-image-models`) |
| Release | tag `assets`, published 2024-09-29 |
| URL | `https://github.com/ankandrew/open-image-models/releases/download/assets/yolo-v9-t-384-license-plates-end2end.onnx` |
| Size | 7 771 218 bytes |
| SHA-256 | `888397b96d761c89db40bc9c305838e8652660f5e282c2cadebbe8d2951a77a8` |

> **Upstream discrepancy:** the registry key is singular (`...license-plate-end2end`)
> while the published asset is plural (`...license-plates-end2end.onnx`). The
> registry maps one to the other. Verified in `open_image_models/detection/core/hub.py`.

ONNX signature (verified locally with onnxruntime 1.30.0):

```
input   images   tensor(float)  [1, 3, 384, 384]
output  output0  tensor(float)  ['batch', 7]
```

Rows are `[batch_index, x1, y1, x2, y2, class_id, score]`. **NMS is embedded in
the exported graph** ("end2end"); this build never runs a second NMS. Preprocessing
reproduces the upstream letterbox exactly (pad 114, aspect-preserving resize,
BGR→RGB, `/255`, float32, NCHW, batch 1), and un-letterboxing subtracts the
*pre-round* padding before dividing by the ratio.

### OCR — FastPlateOCR CCT-S v2 (global)

| Field | Value |
|---|---|
| Registry name | `cct-s-v2-global-model` |
| Published filename | `cct_s_v2_global.onnx` (+ `cct_s_v2_global_plate_config.yaml`) |
| Publisher | ankandrew (`fast-plate-ocr`) |
| Release | tag `arg-plates` ("HUB"), published 2024-04-08 |
| URL | `https://github.com/ankandrew/fast-plate-ocr/releases/download/arg-plates/cct_s_v2_global.onnx` |
| Size | 5 262 230 bytes |
| SHA-256 | `384bbbd2cea3ef54761d3df70822ef3a349ee1a112aeafddbe0e3ba06bc6e47b` |
| Plate config SHA-256 | `0335c74a305173bb6f393efed0fde03cadeaa0b649ed8e19f431016d8232d0a6` |

ONNX signature:

```
input   input   tensor(uint8)  ['unk__909', 64, 128, 3]
output  plate   tensor(float)  ['unk__910', 10, 37]
output  region  tensor(float)  ['unk__911', 66]
```

Plate configuration (from the published `plate_config.yaml`):

* `alphabet = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ_'` (37 classes)
* `pad_char = '_'`, `max_plate_slots = 10`
* `img_height = 64`, `img_width = 128`, `keep_aspect_ratio = false`,
  `interpolation = linear`
* `plate_regions` is commented out upstream, so the `region` head is **not**
  used and no region is ever reported.

> **Upstream discrepancy:** the published plate config omits `image_color_mode`,
> whose library default is `"grayscale"` (1 input channel), but this ONNX export
> requires 3 channels — feeding `[N,64,128,1]` fails with
> `INVALID_ARGUMENT`. Colour handling is therefore an **explicit declared
> setting** (`ocr_color_mode`), frozen for the reported evaluation, not an
> inferred default.

> **Confidence semantics:** the `plate` head emits **raw logits**, not a softmax.
> Upstream's own decoder takes `argmax` and reports `np.max` as "char prob".
> This build reports those numbers as **uncalibrated model scores** and never
> as probabilities or accuracy estimates. Absent confidence stays `null`; a
> malformed per-character list is never shortened or zero-filled.

### Licensing and training data — unresolved

| Question | Finding |
|---|---|
| Repository code license | MIT (both `ankandrew/fast-plate-ocr` and `ankandrew/open-image-models`) |
| Weight-specific license | **Not published.** The ONNX weights are GitHub *release assets* of those MIT repositories. No `MODEL_LICENSE`, model card, or separate weight terms exist. |
| Contradicting restrictions | None found (no copyleft, non-commercial, or field-of-use clause) |
| Training data | **Not disclosed** for either artifact. The legacy models' datasets are described on the model-zoo page; the v2 "global" OCR model and the plate detector have no published dataset description. |

This is a **documented ambiguity, not a clean pass**. Consequences:

* The weights are used **locally only** and are **not redistributed**. `.gitignore`
  already excludes `*.onnx`, so they cannot be committed.
* `artifacts/plate_alpr/provenance/` keeps the upstream `LICENSE` files, the
  published configs, and every SHA-256 so the provenance chain is auditable.
* This ambiguity is recorded as a blocker in the evaluation record and is one of
  the reasons the demo cannot be enabled without explicit owner acknowledgement.
* Nothing here supports any production or real-CCTV accuracy claim.

---

## 3. Isolated Windows environment

The application environment keeps `opencv-python`. The plate work uses a
**separate** interpreter so no competing `cv2` distribution is ever installed
into the application environment.

```powershell
cd D:\tavidm
& "C:\Program Files\Python311\python.exe" -m venv .venv\plate-onnx
.\.venv\plate-onnx\Scripts\python.exe -m pip install --upgrade pip
.\.venv\plate-onnx\Scripts\python.exe -m pip install `
    "onnxruntime==1.30.0" `
    "opencv-python-headless==4.13.0.92" `
    "numpy==2.2.6" `
    "PyYAML==6.0.3" `
    "pytest==9.1.1"
```

Resolved versions in that environment:

| Package | Version |
|---|---|
| onnxruntime | 1.30.0 (CPU) |
| opencv-python-headless | 4.13.0.92 |
| numpy | 2.2.6 |
| PyYAML | 6.0.3 |
| pytest | 9.1.1 |
| protobuf (transitive) | 7.36.2 |
| flatbuffers (transitive) | 25.12.19 |

Existing environments (`.venv`, `venv`, `bench_env`) were **not** modified
except for one documented addition, below.

### The one application-environment addition

`onnxruntime==1.30.0` was installed into `D:\tavidm\venv` (the environment that
runs the app) because OCR must run in the app process. Verified afterwards:

```
opencv-python 4.13.0.92      (unchanged, headless NOT added)
onnxruntime   1.30.0
numpy         2.4.4
flatbuffers   25.12.19        (new, transitive)
protobuf      7.36.2          (new, transitive)
cv2 4.13.0  ->  D:\tavidm\venv\Lib\site-packages\cv2\__init__.py
```

`onnxruntime` declares no computer-vision dependency, so the `opencv-python`
versus `opencv-python-headless` overlap is resolved by **isolation**, not by
installing a second `cv2` into the application environment. CUDA was **not**
used: the requirement was an explicit `CPUExecutionProvider`, the reported
numbers are CPU numbers, and no CUDA/cuDNN/PyTorch compatibility claim is made.

---

## 4. Running it

### Offline / real-ONNX tests (isolated environment)

```powershell
.\.venv\plate-onnx\Scripts\python.exe -m pytest tests/test_plate_ocr_offline.py -q
```

This module replaces `socket.socket`, `socket.create_connection`,
`urllib.request.urlopen`, and `os.system` with raising stubs and points the
model cache at an empty temporary directory, so a hub download or any other
network call fails loudly.

### Feature tests (application environment)

```powershell
$env:TAVIDM_BOOTSTRAP_ADMIN_PASSWORD = "isolated-plate-ocr-test-pw"   # only if unset
.\venv\Scripts\python.exe -m pytest tests/test_plate_ocr.py -q
```

All tests use a temporary SQLite database (configured before `database` is
imported) and a temporary plate evidence root
(`TAVIDM_PLATE_OCR_EVIDENCE_ROOT`). The canonical user database and the
canonical evidence root are never written, and no private footage is used.
Isolated runtime checks require this override to resolve exactly to
`<EVIDENCE_FOLDER>/plate_ocr`; inherited external or repository paths are
rejected before runtime startup or demo recovery.

### Presentation contracts (Node)

```powershell
node tests/js/test_plate_ocr_labels.cjs
```

### Isolated evaluation and frozen scoring

The evaluator creates a unique root under the system temporary directory by
default. If `--isolation-root`, `--db`, `--evidence-root`, or `--out` is
supplied, every output must resolve beneath the same isolation root, outside
the repository and away from source footage, configuration, model artifacts,
labels, and frozen-package inputs. The evaluator refuses path overlaps before
creating output directories. Existing databases, evidence, crops, manifests,
labels, and report files are never automatically deleted or overwritten; an
existing database requires an explicit `--reuse-db`, and immutable manifest or
crop conflicts are reported.

A processing run saves `frozen_units.json` with a content hash and a separate
`labeling_package.json` plus vehicle crops. The labeling package contains stable
unit IDs and blank `readable`, `uncertain`, `text`, and `note` fields; it never
contains OCR text. Fill labels only from the supplied crop package. Readable
no-reads remain in the denominator; unreadable and uncertain labels are
reported separately and excluded. Both label flags must be booleans, and a
readable label must include the exact full text.

Score only from the frozen package, without rebuilding units from current
manifests, initializing a database, starting the runtime, or running inference:

```powershell
python scripts\plate_ocr_evaluate.py `
  --score-only `
  --config artifacts\plate_alpr\plate_ocr_demo.json `
  --frozen-units <evaluation-root>\frozen_units.json `
  --labels <label-input.json>
```

The score-only run verifies the frozen package hash and writes a new report to
its own unique temporary output root. To produce a qualified gate record,
provide `--qualification-evidence <reviewed-evidence.json>` containing the
per-unit `association_results`, the offline, isolation, admin-authorization,
retry-preservation, and browser-review checks, and the unresolved licensing
blocker list. Each check must be the JSON boolean `true`; association results
must cover every frozen unit with `associated` or `no_candidate`, and the
license blocker list must be empty. The evaluator writes a versioned
`evaluation_gate_record.json` beside its output and binds it to the exact
frozen-package hash, model hashes, provider, and settings hash. The record is
derived from actual labels and verification evidence; changing a result label
or supplying acknowledgement in place of licensing evidence cannot qualify the
demo. The checked-in failed record is not replaced by an evaluation run.

---

## 5. Configuration

Plate OCR is enabled only by an explicit JSON file pointed at by
`TAVIDM_PLATE_OCR_CONFIG`. See `artifacts/plate_alpr/plate_ocr_demo.json`.
An enabled config alone does not start OCR: application runtime startup also
requires exactly one explicit isolated mode (`TAVIDM_PLATE_OCR_DEMO=1` after a
passing gate, or `TAVIDM_PLATE_OCR_EVALUATION=1` with isolated paths and
verified artifacts). Ordinary application startup and status serialization
remain disabled.

Required keys: `enabled`, `provider`, `detector_path`, `detector_sha256`,
`ocr_path`, `ocr_sha256`, `ocr_config_path`, `ocr_config_sha256`,
`evaluation_record`. Optional: `ocr_color_mode`, `detector_conf_threshold`,
`ocr_min_char_confidence`, `quality`, `bounds`.

The loader **fails closed**: a missing key, a non-hex or wrong-length hash, a
path that does not exist, an unknown provider, `auto`, an unknown quality or
bounds key, or unreadable JSON all raise `PlateOcrConfigError` instead of being
repaired.

Frozen bounds and quality thresholds (recorded in every manifest):

| Bound | Value |
|---|---|
| Post-trigger collection window | 2.0 s |
| Sampling rate | 2 fps |
| Selected vehicle crops per observation | 3 |
| Detector calls per observation | 3 |
| OCR calls per observation | 6 |
| Inference workers | 1 |
| Queued jobs | 16 |
| Retained-crop memory budget | 64 MiB |
| Inference scheduling budget | 1.0 s |

| Quality gate | Value |
|---|---|
| Minimum plate size | 24 × 10 px (native crop, before OCR resize) |
| Laplacian variance floor | 18.0 |
| Maximum blown-highlight ratio | 0.35 |
| Aspect-ratio band | 1.6 – 9.0 |
| Maximum vehicle overlap IoU | 0.30 |
| Minimum plate area retention after clamping | 0.55 |

Occlusion is **not** detected. A partially occluded plate can pass the gate and
produce a wrong string; that uncertainty is carried by the outcome, not by a
claim of occlusion handling.

### On the scheduling budget

The 1.0 s budget bounds **when the next inference call may start**. It cannot
interrupt a synchronous ONNX Runtime call that is already running, and this
build never claims otherwise. A job that exhausts the budget reports partial
results plus `budget_exhausted`. If a hard deadline is ever required, the
supported route is a supervised local process with bounded shutdown.

---

## 6. Attribution rules

* Evidence is keyed by `(source, run_key, live_session_id, track_id,
  identity_epoch, review_id)`. ByteTrack ids are reused, so the identity epoch
  is in every key and every manifest.
* Every crop comes from **its own frame** and that frame's own tracked box. The
  event-frame box is never reused on a later frame.
* A live reconnect creates a new `live_session_id`.
* A vehicle overlapping another tracked vehicle above IoU 0.30 is marked
  `association_uncertain` and is not sent for OCR.
* The plate detector only ever runs on a selected, attributable vehicle crop.
  Full-frame plate detections without trustworthy vehicle matching are never
  used.

---

## 7. Storage and recovery

```
<EVIDENCE_FOLDER>/plate_ocr/          # explicit isolated manifest root
    attempts/<attempt_id>.json        immutable machine manifests
    index/<review_key>.json           atomic pointer to the current attempt
    crops/<attempt_id>/*.jpg|png      server-generated crops, content-hashed
```

The demo child and evaluation launcher set
`TAVIDM_PLATE_OCR_EVIDENCE_ROOT` to this directory. The runtime resolves and
validates it against the isolated evidence root, database, repository, and
protected model/configuration inputs before plate processing. Recovery runs
only after the same path authorization succeeds.

* Server-generated paths only, with containment checks on every component.
* Attempts are written once (`O_EXCL` + rename). A retry writes a **new**
  attempt id; history is never edited.
* Manifest and index writes are temp-file + `os.replace`, so a crash leaves
  either the old complete file or the new complete one.
* Manifests are capped at 512 KiB.
* `sweep_partial_writes()` removes only this module's own `*.tmp` files inside
  the plate tree. It never deletes footage, manifests, crops, or unrelated
  files.
* `orphan_report()` reports unreadable attempts, attempts whose crops have been
  deleted, and orphaned index entries — read-only, no automatic deletion.
* Missing or corrupt manifests surface as explicit statuses
  (`missing` / `corrupt` / `unsafe_identifier` / `oversized`), never as
  "no machine result".

No table, column, migration, or `CHECK` value was added. Case↔attempt linkage
uses the existing `case_action_events.review_id` column. Human machine
provenance is stored in the existing
`plate_verifications.processing_diagnostics_json` and is **retained** across
later corrections.

---

## 8. Outcome taxonomy (orchestration layer)

Distinct from human `plate_verifications.plate_status` values:

`disabled`, `queued`, `processing`, `unavailable`, `failed`,
`no_candidate_detected`, `detected_unreadable`, `quality_rejected`,
`association_uncertain`, `cancelled`, `budget_exhausted`, `candidate_found`.

> **"No result" never means "no plate exists" and never means a human
> `not_visible`.** Every ambiguous outcome carries
> `outcome_is_ambiguous: true` in its manifest, and the UI wording is enforced
> by `tests/js/test_plate_ocr_labels.cjs`.

---

## 9. Confirmation boundary

* `POST /api/cases/<id>/plate` is **admin-only** (`role_required("admin")`).
* `core/case_review_service.verify_plate_identity` independently requires
  `adapter.can_confirm_plate_identity()`, so a **direct service call cannot
  bypass** the restriction.
* `can_verify_plate`, `ENFORCEMENT_ROLES`, `can_confirm_case`,
  `can_confirm_event_time`, review decisions, and every other enforcer permission
  are **unchanged**. An explicit `verify_plate` grant does **not** bypass the
  admin-only restriction.
* The actor always comes from the authenticated session.
* A `candidate_id` is a **lookup key only**. OCR text, crop reference, and
  provenance are read from the server-owned manifest for *that* case; a spoofed
  id or a cross-case reference is refused.
* Plate crops are served only through authenticated record-scoped endpoints
  (`/api/cases/<id>/plate-crop/<attempt_id>/<name>` and the review-queue
  equivalent) with containment re-checks. Arbitrary paths and traversal are
  refused.
* Machine jobs never write accepted identity, `verified_by` / `verified_at`, the
  legacy recognized status, violation decisions, or enforcement actions. A late
  machine result cannot overwrite an accepted plate.
* Payload compatibility is preserved: existing keys still work, including manual
  correction / manual entry, which stays available to an admin whenever the
  evidence is readable.

---

## 10. UI states

Shown in the review-queue evidence modal and on the case detail:

* `Experimental local OCR — human admin review required`
* Disabled / Queued / Processing / Unavailable / Failed
* Machine candidate (including conflicting and wrong reads, with the number of
  conflicting reads per candidate)
* Plate detected; text unreadable
* No candidate detected in selected evidence
* Association uncertain; candidate not eligible for confirmation
* Human-verified plate — a **separate** state

The candidate crop is shown together with the associated vehicle/frame box,
frame number, timestamp, track id, and identity epoch so an admin can verify
attribution. All OCR text is HTML-escaped. Confirmation controls render only for
an admin **and** only for a machine-eligible outcome, and they require an
explicit action — candidate text is never pre-accepted. Review-queue status is
unchanged and OCR never confirms a violation.

---

## 11. Demo gate verdict

Predeclared before scoring:

* Evaluation unit: one per `(run, live_session, track_id, identity_epoch, plate
  episode)`, where a gap greater than **5.0 s** starts a new episode.
* Scored candidate: the **earliest attempt by (frame_number, attempt_id)** in the
  episode, using that manifest's own declared primary candidate. Ground truth is
  never consulted and no candidate is picked retrospectively.
* Primary-candidate rule (declared, not retrospective): group reads by the
  declared presentation normalization (uppercase, strip non-alphanumeric —
  **never** repairing `O`/`0` or `I`/`1`); the group supported by the most
  distinct sampled frames wins, ties broken on the highest mean uncalibrated
  score then the lower candidate id. Agreement only ranks candidates.
* Human labelling was performed on the **vehicle crop**, before any OCR output
  was inspected.

```
read rate = exact full-plate string matches
            / plates independently judged readable by a human
```

### Measured

| Metric | Value |
|---|---|
| Total evaluated units | 1 |
| Human-readable denominator | **0** |
| Human-unreadable / uncertain | 1 |
| Exact matches | 0 |
| Wrong non-empty reads | 0 |
| No-read / unreadable machine outcomes | 0 (outcome was `no_candidate_detected`) |
| Read rate | **undefined (denominator 0)** |
| False non-empty reads on human-unreadable evidence | 0 |
| Candidate-conflict units | 0 |
| Association-uncertain units | 0 |
| Quality-excluded units | 0 |
| Budget overruns | 0 |
| Detector calls / OCR calls | 1 / 0 (limits 3 / 6) |

Footage: `dataset/raw/test_pick-up_cargo_person_1.mp4` (owner-selected). Scene
annotation: a single `active_lane` polygon aligned with the observed travel
direction, supplied as an operator input. The existing rule engine alone decided
whether anything was a violation: exactly one `Obstruction` event fired, for a
truck genuinely stationary for ~29 s. **No review row was fabricated to obtain a
persistence ID.**

Scene note: the clip is a wide-angle elevated traffic camera. The offending
vehicle occupies roughly 100 × 100 px of a 1920 × 1080 frame, so any plate is
~15–30 px wide. The attributable vehicle crop was **101 × 99 px** and no plate
characters are resolvable in it by a human or by the models.

### Verdict: **FAIL**

* `readable_denominator_above_zero` — **failed** (denominator = 0).
* `read_rate_at_least_40_percent` — **not evaluable**.

This is an **experimental simulation demo gate**. It is not a
production-readiness threshold and it establishes no accuracy on real CCTV.

### Causes and what would be needed

1. **Resolution.** The detector found no plate above the frozen 0.25 threshold in
   a 101 × 99 vehicle crop, and even a clean synthetic 240 × 70 plate on a plain
   background produced a single raw row scoring 0.009. The OCR stage itself works
   (a direct crop read returned `ABC123`), so the detector stage and the source
   resolution are the limiting factors.
2. **Sample size.** One attributable episode is statistically meaningless even if
   it had been readable.
3. **Scenes.** The default enabled-violation set produced zero review
   observations on this clip under a neutral annotation.

Needed improvements, none of which were applied here:

* gate-facing footage with plates at or above roughly 60–80 px wide in the source
  frame (closer capture, optical zoom, or a higher-resolution source);
* a scene that legitimately produces several review observations;
* an explicitly declared alternative detector with better small-plate recall, or
  a multi-scale plate detector — a **different model stack**, which would
  invalidate the recorded evaluation and require re-evaluation before any demo
  enablement;
* explicit owner acknowledgement of the weight-licensing and training-data
  ambiguity in section 2, or replacement artifacts with an unambiguous model
  license.

Nothing was relabelled, cherry-picked, or tuned on the scored set.

---

## 12. Latency and resources

Measured in the isolated environment on this host (Windows 10 19045, 12 logical
CPUs, Python 3.11.9, `CPUExecutionProvider`, 101 × 99 crops):

| Stage | n | p50 | p95 | p99 | max |
|---|---|---|---|---|---|
| Cold detector (incl. session + graph setup) | 1 | — | — | — | 252.0 ms |
| Cold OCR (incl. session setup) | 1 | — | — | — | 28.5 ms |
| Warm detector | 120 | 34.3 ms | 38.7 ms | 40.0 ms | 40.5 ms |
| Warm OCR | 120 | 26.4 ms | 27.0 ms | 27.4 ms | 27.4 ms |
| End-to-end detect → quality → OCR (101 × 99) | 60 | 33.7 ms | 34.7 ms | 35.0 ms | 35.2 ms |

In the real pipeline: cold detector 245.0 ms, queue wait 234.0 ms, 1 sample,
0 quality rejections, no truncation, no budget overrun.

Memory (bare process, working set):

| Stage | Working set |
|---|---|
| Interpreter + numpy/cv2 baseline | 10.7 MiB |
| After detector session | 63.0 MiB |
| After OCR session | 71.9 MiB (peak 75.1 MiB) |

The two ONNX sessions cost about **61 MiB**. That is separate from the 64 MiB
**retained-crop** budget, which covers numpy crop arrays held by collectors and
queued jobs and is enforced independently.

Disk: the plate tree holds one 5 045-byte vehicle JPEG plus a ~2 KiB manifest
per attempt.

GPU: not used. The vehicle pipeline keeps its single reservation
(`core/gpu_inference_slot.py`) untouched, and all detector/OCR calls run on the
background worker thread outside the live-frame path and outside that
reservation.

Effect on existing processing: the uploaded-video pipeline took 342.5 s wall
clock for 2 145 frames with the experiment enabled, versus 341.2 s in the same
harness with plate OCR **disabled** (onnxruntime absent) — a ~0.4 % difference,
within noise. OCR ran once, on a background thread, and did not slow the frame
loop.

---

## 13. Test evidence

| Command | Result |
|---|---|
| `venv\Scripts\python.exe -m pytest tests/test_plate_ocr.py -q` | **103 passed** |
| `venv\Scripts\python.exe -m pytest tests/test_plate_ocr_offline.py -q` | **18 passed** |
| `.venv\plate-onnx\Scripts\python.exe -m pytest tests/test_plate_ocr_offline.py -q` | **18 passed** |
| `venv\Scripts\python.exe -m pytest tests/test_plate_processing.py tests/test_case_review_service.py -q` | 53 passed |
| `venv\Scripts\python.exe -m pytest tests/test_recurrence_policy.py tests/test_case_policy_e2e.py -q` | 2 passed, 1 pre-existing error (see below) |
| `venv\Scripts\python.exe -m pytest tests/test_review_queue.py tests/test_review_decisions.py tests/test_review_decision_races.py tests/test_review_decision_regressions.py tests/test_processing.py tests/test_auth.py tests/test_motorcycle_detail_integration.py -q` | 99 passed, 2 pre-existing failures |
| `node tests/js/test_plate_ocr_labels.cjs` | passed |

### Broader suite

```
$env:TAVIDM_BOOTSTRAP_ADMIN_PASSWORD = "isolated-full-suite-run-pw"
venv\Scripts\python.exe -m pytest tests/ -q
=> 10 failed, 1319 passed, 1 skipped
```

**All 10 failures are pre-existing and were reproduced / root-caused.** None is a
regression from this feature, and none touches plate code:

| Failure | Count | Root cause | Regression? |
|---|---|---|---|
| `test_evidence.py::TestVehicleCrop::test_returns_path_within_evidence_dir`, `..._test_scene_snapshot_unchanged` | 2 | The test points `EVIDENCE_FOLDER` at a temp dir outside the repo and then asserts the returned path contains `/evidence/video_1/`; `core/evidence.py` returns an absolute path in that case. `core/evidence.py` is **unmodified** (`git diff` empty). | No |
| `test_settings_gate1.py::TestSettingsRouteGate1::*` | 4 | The tests log in with a hard-coded `admin123`, but `core/auth.ensure_default_admin` refuses to create the admin unless `TAVIDM_BOOTSTRAP_ADMIN_PASSWORD` is at least 12 characters. | No |
| `test_ui_accessibility.py::test_shared_shell_uses_truthful_status_and_named_search`, `..._test_review_queue_exposes_filter_and_action_state`, `..._test_icon_actions_and_dialog_closers_have_names` | 3 | Same hard-coded `admin123` login. None of these tests references plate/OCR. | No |
| `test_case_policy_e2e.py::test_flask_case_apis_isolated` | 1 | Same hard-coded `admin123` login. **Verified**: with `TAVIDM_BOOTSTRAP_ADMIN_PASSWORD=admin1234567890` and the test's literal updated to match, the file passes 2/2 — so the plate authorization changes in that test are correct. | No |

Node suites: all pass except `tests/js/test_ui_ux_helpers.cjs`, which fails
because the pre-existing uncommitted `static/js/review_queue.js` calls
`document.querySelectorAll(...)` at load time while the test's `document` stub
only defines `getElementById`. That line is not part of this change.

Tests that were updated because of the **authorized** admin-only restriction
(plate confirmation now requires an active System Administrator):

* `tests/test_case_review_service.py` — plate-confirmation actors switched to an
  admin; a new `TestPlateAdminOnlyBoundary` class covers the denials.
* `tests/test_recurrence_policy.py` — added a `_plate_admin()` helper so plate
  confirmation uses an admin while case confirmation and event-time review stay
  with the enforcer (which also proves the restriction is narrow).
* `tests/test_case_policy_e2e.py` — added an `admin` fixture, an explicit
  enforcer-denial assertion, and split plate confirmation (admin) from
  event-time review (enforcer).

---

## 14. Demo enablement and rollback

### Enable (only after the gate passes)

```powershell
cd D:\tavidm
.\venv\Scripts\python.exe scripts\run_plate_ocr_demo.py `
    --config artifacts\plate_alpr\plate_ocr_demo.json
```

Optional: `--db` and `--evidence-root` together under one isolated parent,
`--host`, `--port`, `--no-browser`, `--check-only`. By default, the launcher
allocates a unique root under the system temporary directory; it does not reuse
or delete an earlier demo directory.

The launcher sets everything **inside the child process only**:

| Variable | Value |
|---|---|
| `TAVIDM_PLATE_OCR_CONFIG` | the explicit demo config |
| `TAVIDM_PLATE_OCR_DEMO` | `1` |
| `TAVIDM_PLATE_OCR_ISOLATED_ROOT` | unique demo output root outside the repository |
| `SQLITE_PATH` / `DATABASE_URL` | a new database inside the isolated root |
| `EVIDENCE_FOLDER` | a private evidence directory inside the isolated root |
| `UPLOAD_FOLDER`, `FRAMES_FOLDER`, `ANNOTATED_FOLDER`, `REPORTS_FOLDER` | inside the isolated root |

It never writes the canonical database, the canonical evidence root, the user's
global environment, or any application setting. **Admin role alone does not
enable OCR.**

The launcher refuses to start (exit code 2 or 3) when the config is invalid, an
artifact is missing or hash-mismatched, `onnxruntime` is absent, the requested
provider is unavailable, or the recorded evaluation is not a pass for these exact
hashes, provider, colour mode, and configuration hash.

### Stop / disable

1. Press **Ctrl+C** in the demo console, or close the window. The child process
   exits; the worker thread is a daemon and dies with it.
2. Plate OCR is off in every other start path. A normal `python app.py` cannot
   start OCR from `TAVIDM_PLATE_OCR_CONFIG` alone; runtime mode and isolated
   path validation are enforced inside the application.

### Verify the demo is stopped

```powershell
# 1. The feature status endpoint must report "disabled".
#    (start a normal, non-demo app first, log in, then:)
curl.exe -b cookies.txt http://127.0.0.1:5000/api/plate-ocr/status
# expect: "state": "disabled"

# 2. No demo process remains.
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Select-Object ProcessId, CommandLine
# expect: no command line containing run_plate_ocr_demo.py

# 3. No machine attempts are being written.
Get-ChildItem output\plate_demo\evidence\plate_ocr\attempts -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending | Select-Object -First 5 Name, LastWriteTime
# record the newest timestamp, wait, and confirm it does not change.
```

### Retention

Human-confirmed demo state and evidence follow the documented retention policy:
attempt manifests and crops are **immutable** and are never deleted by the
plate code. `sweep_partial_writes()` runs inside the child only for a newly
allocated isolated root; it is skipped for an existing caller-supplied root so
another process's active temporary files are not swept. Deleting demo evidence
is an explicit operator action on the demo directory, and `orphan_report()`
will then report attempts whose crops are gone instead of failing silently.

### Invalidating the gate

Changing a model, a model config, `ocr_color_mode`, the provider, or any declared
bound changes the configuration hash or the artifact hashes, and
`demo_gate_status()` then refuses the recorded evaluation. Re-evaluation is
required before the demo can be enabled again.

---

## 15. Remaining risks and limitations

1. **Weight licensing and training data are unresolved** (section 2). Local,
   non-redistributed use only; owner acknowledgement required.
2. **The measured read rate on the approved footage is undefined** because no
   plate was human-readable. Nothing here supports any accuracy claim.
3. **Occlusion is not detected.** A partially occluded plate can pass the quality
   gate and produce a wrong string.
4. **Confidence is uncalibrated.** Raw logits are stored and displayed as such.
5. **Very small samples.** One evaluation unit; the result is a measurement of a
   failure, not a performance estimate.
6. **A collection window of 2 s of wall clock** yields few samples when the video
   pipeline is slow. That is a measured effect, not a bug, but it means the
   per-observation crop budget is rarely reached on this hardware.
7. **No hard inference deadline.** The scheduling budget cannot interrupt a
   running synchronous ONNX call.
8. **CPU only.** No CUDA was enabled, so no GPU compatibility claim is made.
9. **New model weights were not added**; the existing vehicle weights, ByteTrack,
   and the violation engine are unchanged.
