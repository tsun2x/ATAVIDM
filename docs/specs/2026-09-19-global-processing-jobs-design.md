# Global Processing Jobs and Results History Design

**Project:** TAVIDM  
**Date:** 2026-09-19  
**Status:** Proposed for implementation  
**Scope:** Processing-job persistence and end-user processing history UX

## Objective

Make video-processing activity remain visible and recoverable when a user switches videos, navigates away from Live Monitor, or reloads the page. Provide a compact global job monitor on every authenticated page and a detailed Active Jobs / Completed Jobs experience in Live Monitor.

## Constraints

- Preserve the Flask monolith, SQLite database, YOLOv8m/ByteTrack pipeline, sequential GPU worker, current authentication roles, and existing processing-run schema.
- `processing_runs` remains the authoritative job and history record. Browser memory and mounted DOM state are presentation caches only.
- A queued, running, failed, or cancelled attempt must not replace an earlier authoritative completed result.
- Runs with `results_removed_at` are historical entries without previewable current results.
- Do not create a second queue, worker, job table, or client-owned job lifecycle.
- Existing stop-processing authorization and run-scoped cancellation behavior remain unchanged.

## User Experience

### Global processing indicator

Every authenticated page displays a compact top-navigation control with:

- active job count;
- running/queued state indicator;
- an accessible label such as `2 active processing jobs`;
- a drawer or dropdown containing active jobs and a bounded list of recent terminal jobs.

The compact list shows filename, state, progress, stage, and ETA when available. Selecting an item navigates to Live Monitor with that video and processing run selected.

The indicator polls while any job is queued or running. With no active jobs, it polls less frequently so normal navigation does not create unnecessary traffic.

### Live Monitor job workspace

Live Monitor gains two job tabs or sections:

1. **Active Jobs**
   - queued and running attempts;
   - filename, run ID, progress, stage, elapsed time, FPS, ETA, and queue position;
   - stop action for authorized users;
   - selecting an item opens the associated video and resumes the existing preview/status experience without starting another run.

2. **Completed Jobs**
   - completed, failed, and cancelled attempts ordered newest first;
   - terminal status, start/finish time, model identifier, detection observations, candidate count, vehicle crossings, and the saved annotation snapshot identifier;
   - completed current results expose Preview Annotated and Download actions when the artifact is available;
   - failed/cancelled/removed results show their status and explanation but do not pretend to have a current annotated result.

The existing per-video History modal remains available for detailed audit events. The new tabs provide cross-video discovery and visual result access rather than replacing audit history.

## Server Architecture

### Authoritative query service

Add a bounded processing-job query service backed by `processing_runs` joined to `videos`. It returns normalized job records and separates:

- `active`: `queued` and `running`;
- `recent`: `completed`, `failed`, and `cancelled`.

Each record includes only presentation-safe fields:

- run and video identifiers;
- filename and condition;
- run status, stage, viewer mode, and timestamps;
- progress, frames, elapsed time, FPS, and ETA where derivable;
- detection, crossing, and candidate summaries;
- model identifier and error message;
- `is_current_result`, `is_latest_attempt`, and `results_removed` flags;
- media, reference-frame, annotated-preview, download, and Live Monitor navigation URLs only when valid.

### API

Add authenticated `GET /api/processing-jobs` with bounded query parameters:

- `recent_limit`, constrained to a safe maximum;
- optional `video_id` for a single-video projection.

The response includes server time and a suggested polling interval so all consumers use one contract.

The endpoint performs no mutation. Existing process, status, cancel, remove-results, and delete endpoints remain authoritative for actions.

### Result authority

Annotated preview URLs are emitted only when all conditions hold:

- run status is `completed`;
- `results_removed_at` is null;
- the run is the current authoritative completed result for the video;
- `annotated_video_ready` is true and the artifact path remains valid under approved media roots.

Historical completed attempts remain visible but are explicitly marked superseded when they are not the current result.

## Client Architecture

### Global controller

Create one global processing-jobs controller loaded through the authenticated base template. It owns:

- one polling timer per browser page;
- the last normalized server snapshot;
- rendering the global indicator/drawer;
- dispatching a document event when the snapshot changes.

Live Monitor subscribes to that document event and renders its detailed tabs. Selecting a video no longer creates or destroys the global job model. Its existing per-video high-frequency polling may continue only for the actively watched run and live JPEG preview.

### Navigation and selection

Job links use query parameters such as `?video_id=<id>&run_id=<id>&jobs=completed`. On Live Monitor load:

- the requested video is selected if it still exists;
- the requested tab is activated;
- an active run resumes detailed polling;
- a completed current run opens its annotated result when available;
- unavailable/deleted/stale targets produce a non-destructive message and leave the job lists usable.

### State synchronization

Processing transitions update both the server record and all visible projections:

- queued/running videos visibly show Queued or Processing;
- completion changes the row to Processed and enables valid result preview;
- failure/cancellation restores the correct Ready or Processed video status without erasing history;
- removal invalidates result actions while retaining the historical run;
- permanent video deletion removes the job entries after the next snapshot and clears selection controls.

## Error and Recovery Behavior

- A temporary API failure keeps the last successful snapshot, displays a stale/reconnecting indication, and retries with bounded backoff.
- Page reload reconstructs the entire visible state from SQLite.
- Startup orphan recovery remains responsible for runs left queued/running after process termination.
- Missing annotated artifacts do not crash the drawer or history view; preview is disabled with an unavailable label.
- Unauthorized users never receive controls they cannot execute, although authenticated viewers may see read-only status according to existing role policy.

## Security and Privacy

- All job APIs require authentication.
- Filenames and errors are rendered as text, not injected HTML.
- Media URLs use existing guarded serving routes.
- Stop, delete, and result-removal actions retain their existing role checks and explicit confirmations.
- No local filesystem paths are exposed in API responses.

## Testing Strategy

### Server tests

- Active and terminal grouping is correct and newest-first.
- Limits are bounded and invalid parameters fail safely.
- Current result, superseded result, removed result, zero-detection completion, failed attempt, and cancelled attempt are represented correctly.
- Preview/download URLs are absent for non-authoritative or unavailable artifacts.
- Authentication is required.
- Deleting a video removes it from global results; removing results retains history but disables preview.

### Client contract tests

- The global controller has exactly one polling loop.
- Navigation/query selection opens the intended video and tab.
- Switching selected videos does not clear active jobs.
- Job-state events update Live Monitor rows and job tabs.
- Completed jobs expose preview only when the API authorizes it.
- Deletion and result removal clear stale checkbox, selection, and preview state.

### Regression tests

- Existing queue ordering and one-GPU-worker behavior remain unchanged.
- Existing per-video processing, cancellation, result authority, annotated media, history, and review-queue tests continue passing.
- JavaScript syntax, Python compilation, and `git diff --check` pass.

## Acceptance Criteria

1. Start processing a video, navigate to another authenticated page, and observe the same active job in the global drawer.
2. Return to Live Monitor and see the job still active without restarting or duplicating it.
3. Switch among videos while at least one job runs; all jobs remain visible and accurate.
4. When processing completes, its row visibly changes to Processed and the run moves to Completed Jobs.
5. A current completed result can be previewed visually and downloaded from the Completed Jobs section.
6. Failed, cancelled, superseded, removed, and missing-artifact runs remain truthful and cannot masquerade as previewable current results.
7. Refreshing or reopening the page reconstructs state from SQLite.

## Out of Scope

- Parallel GPU processing or additional workers.
- WebSockets or a new message broker.
- Changes to detector, tracker, violation rules, database schema, or model weights.
- Deleting processing history independently of the existing video lifecycle.
- Cross-device push notifications.
