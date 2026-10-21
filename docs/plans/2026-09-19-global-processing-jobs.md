# Global Processing Jobs and Results History Implementation Plan

> **For agentic workers:** Use the host's available task-by-task implementation workflow. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist processing-job visibility across navigation and add global and Live Monitor views for active and completed runs with truthful result previews.

**Architecture:** Keep SQLite `processing_runs` and the serialized worker as the only job authority. Add one read-only cross-video jobs API, one global browser controller loaded from the authenticated base template, and Live Monitor projections that consume the same snapshot while retaining high-frequency polling only for the actively watched run.

**Tech Stack:** Python 3.11, Flask, SQLite, Jinja2, vanilla JavaScript, Bootstrap 5, pytest, Node.js.

## Global Constraints

- Preserve Flask, SQLite, YOLOv8m/ByteTrack, the sequential GPU worker, authentication roles, and the current `processing_runs` schema.
- Do not introduce WebSockets, Redis, Celery, another queue/worker, or browser-owned authoritative state.
- Failed, cancelled, queued, or running attempts never replace an authoritative completed result.
- Removed results remain historical but cannot expose preview actions.
- Do not expose filesystem paths. Preserve guarded media routes and existing mutation role checks.
- Preserve unrelated dirty changes. Do not reset, clean, stage, commit, or push without explicit owner approval.

### Task 1: Cross-video jobs query and API

**Files:** Modify `database/sqlite_adapter.py`, `database/adapter_base.py`, `database/db.py`, `app.py`; create `core/processing_jobs.py`, `tests/test_processing_jobs.py`.

**Interfaces:** Consume `processing_runs`, `videos`, and current-result helpers. Produce `db.list_processing_jobs(*, recent_limit=20, video_id=None)` and authenticated `GET /api/processing-jobs` returning `{success, server_time, poll_after_ms, active, recent}`.

- [ ] **Step 1: Add focused failing tests** for active/terminal grouping, newest-first order, zero-detection authority, superseded/removed truthfulness, preview gating, video filtering, limit bounds, invalid parameters, and authentication.
- [ ] **Step 2: Verify red** with `python -m pytest tests/test_processing_jobs.py -q`; expect missing service/endpoint failures.
- [ ] **Step 3: Implement minimum behavior** using one bounded join query. Normalize JSON safely. Return IDs, filename/condition, status/stage, progress/frame/FPS/ETA/queue fields, summaries, model/error/timestamps, authority flags, Live Monitor URL, and nullable annotated URLs—never paths. Poll after 1500 ms with active jobs and 10000 ms otherwise.
- [ ] **Step 4: Verify green** with `python -m pytest tests/test_processing_jobs.py -q`.
- [ ] **Step 5: Integrate** with `python -m pytest tests/test_processing.py tests/test_correction_pass2.py tests/test_upload_processing_ux.py -k "history or current or status or result or processing_jobs" -q`.
- [ ] **Step 6: Review gate:** scoped diff plus `git diff --check`; no stage/commit without approval.

---

### Task 2: Global indicator and single polling controller

**Files:** Create `static/js/processing_jobs.js`, `tests/js/test_processing_jobs_controller.cjs`; modify `templates/base.html`, `static/css/style.css`, `tests/test_processing_jobs.py`.

**Interfaces:** Consume the jobs API. Produce `window.TAVIDMProcessingJobs` and `tavidm:processing-jobs-updated` events containing the latest snapshot.

- [ ] **Step 1: Add focused failing tests** in a Node DOM/fetch/timer harness for idempotent start, immediate fetch, one timer, fast/slow intervals, retained snapshot on failure, bounded 5–30 second retry, safe text rendering, server-URL navigation, and event dispatch. Assert authenticated templates load the component once and login does not poll.
- [ ] **Step 2: Verify red** with `node tests/js/test_processing_jobs_controller.cjs` and `python -m pytest tests/test_processing_jobs.py -k global_indicator -q`.
- [ ] **Step 3: Implement minimum behavior:** accessible top-nav button and Bootstrap drawer; one self-scheduling timeout; obsolete-response protection; active plus bounded recent rendering; `Reconnecting…` with last snapshot retained; read-only links to Live Monitor.
- [ ] **Step 4: Verify green** by rerunning Step 2.
- [ ] **Step 5: Integrate** with `node --check static/js/processing_jobs.js` and `python -m pytest tests/test_auth.py tests/test_processing_jobs.py -q`.
- [ ] **Step 6: Review gate:** scoped diff plus `git diff --check`; no stage/commit without approval.

---

### Task 3: Live Monitor Active and Completed Jobs workspace

**Files:** Modify `templates/live_monitor.html`, `static/js/live_monitor.js`, `static/css/style.css`; extend `tests/js/test_processing_jobs_controller.cjs`, `tests/test_processing_jobs.py`, `tests/test_upload_processing_ux.py`.

**Interfaces:** Consume the global update event and server job URLs. Produce Active/Completed tabs and query selection via `?video_id=<id>&run_id=<id>&jobs=<active|completed>`.

- [ ] **Step 1: Add focused failing tests** proving video switching retains global jobs; active cards show progress/queue/FPS/ETA and authorized stop; terminal cards distinguish completed/failed/cancelled/current/superseded/removed; previews require API URLs; active selection resumes polling without POST; completed selection opens Annotated Result; query targets select tab/video/run; missing targets warn safely; completion marks Processed and moves cards; delete/remove-results clears stale selection/preview.
- [ ] **Step 2: Verify red** with `node tests/js/test_processing_jobs_controller.cjs` and `python -m pytest tests/test_upload_processing_ux.py -k "job or history or navigation" -q`.
- [ ] **Step 3: Implement minimum behavior:** render only from the global snapshot; retain per-video `/process-status` polling only for an actively watched run; route Stop through the existing run-scoped cancel endpoint; show observations, crossings, candidates, model, finish time, and annotation snapshot; keep failed/cancelled/superseded/removed/missing artifacts read-only without preview.
- [ ] **Step 4: Verify green** by rerunning Step 2.
- [ ] **Step 5: Integrate** with `python -m pytest tests/test_processing_jobs.py tests/test_upload_processing_ux.py tests/test_correction_pass2.py tests/test_migration_010_cancellation.py -q`.
- [ ] **Step 6: Review gate:** scoped diff plus `git diff --check`; no stage/commit without approval.

---

### Task 4: Acceptance verification and documentation

**Files:** Modify or create `docs/development_logs/2026-09-19_to_2026-09-21.md`; verify all Task 1–3 files.

**Interfaces:** Consume completed API/global/Live Monitor contracts. Produce acceptance evidence and an accurate development log.

- [ ] **Step 1: Add acceptance coverage** that seeds queued/running/completed/failed/cancelled runs, requests the API, completes a run, reloads, and proves reconstruction solely from SQLite.
- [ ] **Step 2: Evaluate the test.** If Tasks 1–3 already satisfy it, record that no extra production repair is needed; do not manufacture a failure.
- [ ] **Step 3: Browser validation with disposable data:** start a run; navigate to Dashboard and verify global status; return without duplication; switch videos; finish and verify Completed plus Processed; preview playback; refresh and verify reconstruction; verify cancelled/failed/removed runs lack misleading preview.
- [ ] **Step 4: Final automation:** run `python -m pytest tests/ -q`, the Node controller test, both JS syntax checks, Python compilation for changed modules, and `git diff --check`. Expect only documented skips.
- [ ] **Step 5: Update the development log** with exact files, API/UI behavior, totals, browser evidence, limitations, and confirmation that no migration or worker replacement occurred.
- [ ] **Step 6: Owner review gate:** report files, evidence, limitations, and Git status; do not stage, commit, or push without explicit authorization.

## Unresolved Product Decisions

None. The approved design makes the global drawer read-only, keeps mutation controls in Live Monitor, retains bounded terminal history, and preserves existing authorization.
