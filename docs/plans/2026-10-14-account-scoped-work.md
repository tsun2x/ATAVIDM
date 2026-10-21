# Account-Scoped Videos, Results, and Reports Implementation Plan

> **For agentic workers:** Use the host's available task-by-task implementation workflow. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scope each enforcer's uploaded-video work to that account while preserving administrator-wide access and shared completed reports for Reports-authorized accounts.

**Architecture:** Keep the Flask monolith and SQLite schema. Resolve ownership scope from the authenticated actor in Flask and pass it to SQLite adapter reads, aggregation queries, and report source selection. Apply authorization to list and direct-object endpoints; use existing `videos.uploaded_by` and `reports.generated_by` fields.

**Tech Stack:** Python 3.10+, Flask, SQLite, pytest, Bootstrap templates, page-specific JavaScript.

## Global Constraints

- Use existing `videos.uploaded_by`, `reports.generated_by`, and direct result-to-video links. No schema migration or ownership backfill unless a concrete affected path cannot be scoped safely; stop and report that path before considering alteration.
- Enforcers see their uploaded videos and video-derived results/reviews. Administrators retain system-wide access and may filter by account. Viewer permissions do not change.
- Source-less/live-camera records retain current shared visibility under existing role checks. Rows linked to NULL-uploader or missing videos are administrator-only in user-scoped views. Never infer ownership from timestamps, filenames, or processing metadata.
- Completed reports and downloads are shared among users already authorized for Reports. Enforcers generate from their own uploaded-video results; administrators may generate across all accounts or for a selected uploader.
- Preserve Flask/SQLite architecture, roles, API contracts where practical, and unrelated working-tree changes. Do not stage or commit without separate authorization.

---

### Task 1: Add ownership-scoped adapter and report-source queries

**Files:** `database/adapter_base.py`, `database/sqlite_adapter.py`, `core/reports.py`, new `tests/test_account_scoped_work.py`.

**Interfaces:** Add explicit optional `owner_user_id` scopes to video, violation, review queue/decision, motorcycle-detail candidate, and analytics source queries. For ordinary result/review lists, the enforcer scope includes rows with `video_id IS NULL`, excludes linked NULL-owner or missing-video rows, and includes only rows whose linked video has matching `uploaded_by`. For report generation, enforce an explicit owner parameter separately from client filters and include only violations joined to that owner's videos; omit source-less results. Unscoped behavior remains available for administrator/system callers.

- [ ] Add failing adapter tests with two enforcers, an admin, one NULL-owner video, linked result/review/detail records, and a truly source-less row. Assert scoped inclusion/exclusion, shared source-less list behavior, report-source exclusion, correct count/page totals, and unchanged unscoped reads.
- [ ] Run `python -m pytest tests/test_account_scoped_work.py -q`; expect failures because ownership arguments/filters are absent.
- [ ] Implement bound SQL filters and protocol signatures. Use the identical ownership predicate for count and page queries. Keep forced report ownership separate from user-supplied filters.
- [ ] Re-run `python -m pytest tests/test_account_scoped_work.py -q`; expect all adapter assertions to pass.
- [ ] Run `python -m pytest tests/test_report_plate_display.py tests/test_review_queue.py tests/test_motorcycle_detail_api.py -q`; expect existing unscoped behaviors to remain passing.
- [ ] Record results; leave paths unstaged/uncommitted absent separate user authorization.

### Task 2: Enforce video ownership on list, direct media, and processing routes

**Files:** `app.py`, conditionally `templates/live_monitor.html`, `static/js/live_monitor.js`, `tests/test_account_scoped_work.py`.

**Interfaces:** Add one actor-to-video-scope helper. Apply to `/api/videos`; video frame status/frame; annotation GET/POST/PUT; process/cancel/status/preview; media/annotated media/history; remove-results/delete; and each ID in bulk processing. Admin may select an uploader; enforcers always use their authenticated ID; keep cameras on existing camera authorization paths.

- [ ] Add Flask tests for two enforcers and admin. Assert list isolation, admin all/account filtering, owner success, and cross-account media/frame/history/annotation/process/status/preview/delete/remove-results requests return current not-found/forbidden style without disclosing metadata. Assert mixed-owner bulk requests enqueue nothing.
- [ ] Run `python -m pytest tests/test_account_scoped_work.py -q`; expect failures on currently unscoped routes.
- [ ] Authorize scope before reading or mutating video objects and before media path resolution. Validate every bulk ID before any enqueue. Add admin-only uploader selection to Live Monitor if needed by current UI.
- [ ] Re-run the focused test; expect isolation, owner access, admin filtering, and atomic bulk rejection to pass.
- [ ] Run `python -m pytest tests/test_upload_processing_ux.py tests/test_processing.py tests/test_processing_jobs.py tests/test_upload.py tests/test_auth.py -q`; expect upload, processing, history, and role contracts to pass.
- [ ] Record results; leave paths unstaged/uncommitted absent separate authorization.

### Task 3: Scope violation, review queue, and motorcycle-detail records

**Files:** `app.py`, if required `database/sqlite_adapter.py`, `templates/violations.html`, `static/js/violations.js`, `templates/review_queue.html`, `static/js/review_queue.js`, `templates/motorcycle_detail_review.html`, `static/js/motorcycle_detail_review.js`, `tests/test_account_scoped_work.py`.

**Interfaces:** Scope `/violations`, `/api/violations`, `/api/violations/vehicle-groups`, `/review-queue`, `/api/review-queue`, decision history, and motorcycle-detail page/API lists. Direct violation/review/detail reads, evidence routes, and mutations must authorize against the linked video's owner before returning evidence or changing state. Also scope direct `/api/cases/<id>` reads, evidence, plate, event-time, confirm, notice-printed, printable, and batch notice-printed paths. Only admins may use uploader filters; expose those filters on violations, review queue, and motorcycle-detail views.

- [ ] Add failing tests for owner-specific lists/totals, permitted source-less rows, cross-owner candidate/evidence/plate/crop access, and cross-owner review/detail/confirm/dismiss/decision mutations with no state change. Assert admin account filters work and non-admin uploader filters follow the existing validation convention.
- [ ] Run the focused test; expect failures because ID-based handlers currently lack account checks.
- [ ] Use scoped adapter reads for consistent pagination and counts. Add a shared row authorization helper; source-less rows follow the approved shared rule, linked NULL/missing-video rows are admin-only. Check access before resolving private evidence paths. Preserve actor-derived audit fields.
- [ ] Re-run the focused test; expect listing, evidence, and mutation isolation to pass.
- [ ] Run `python -m pytest tests/test_review_queue.py tests/test_review_decisions.py tests/test_review_decision_races.py tests/test_motorcycle_detail_api.py tests/test_motorcycle_detail_integration.py tests/test_case_review_service.py -q`; expect review lifecycle, idempotency, and detail contracts to remain passing.
- [ ] Record results; leave paths unstaged/uncommitted absent separate authorization.

### Task 4: Scope dashboard and analytics aggregates

**Files:** `app.py`, `core/analytics.py`, `core/upload_analytics.py`, `database/sqlite_adapter.py`, `database/adapter_base.py`, `templates/dashboard.html`, `templates/analytics.html`, `static/js/dashboard.js`, `static/js/analytics.js`, `tests/test_account_scoped_work.py`.

**Interfaces:** Pass actor scope through dashboard, analytics, and upload-processing-analytics routes, analytics functions, and all supporting totals/trend/type/hour/confidence/review/video aggregates. Enforcers/non-admins see their account scope; admins may select an uploader or all accounts. Add the admin uploader filter to dashboard and analytics result views. Keep existing template response keys and chart ordering; uploaded-video totals are owner-scoped, source-less result treatment follows the approved rule, and admins receive system totals when all accounts is selected.

- [ ] Add failing tests asserting two enforcers get distinct stats, trends, type/hour breakdowns, confidence bands, processing/video totals, and review totals, while admin aggregates all accounts and aggregates match scoped lists.
- [ ] Run the focused test; expect per-account assertions to fail against current global aggregate queries.
- [ ] Thread bound scope parameters through `core.analytics`, `core.upload_analytics`, and adapter SQL, preserving current output shape and empty-series behavior. Add admin-only uploader selectors to dashboard and analytics templates/scripts and apply the same validated uploader filter to the upload-processing analytics endpoint.
- [ ] Re-run the focused test; expect owner/admin aggregates to pass.
- [ ] Run `python -m pytest tests/test_review_queue_nav_and_crossings.py tests/test_upload_processing_ux.py -q`; expect chart/stat contracts to remain passing.
- [ ] Record results; leave paths unstaged/uncommitted absent separate authorization.

### Task 5: Add report filters, forced generation scope, and shared history

**Files:** `app.py`, `core/reports.py`, `database/sqlite_adapter.py`, `database/adapter_base.py`, `templates/reports.html`, `static/js/reports.js`, `tests/test_account_scoped_work.py`.

**Interfaces:** `/api/reports` GET supports `mine` (filter `generated_by`) and `all`; both remain role-gated. POST derives enforcer owner from the session; client filters cannot override it. Admin can omit uploader for all or provide a validated uploader ID. Report downloads remain shared to existing Reports-authorized roles. UI offers “My Reports / All Completed” and an admin-only source-uploader filter.

- [ ] Add failing tests for enforcer report content isolation even with forged uploader/video filters, admin all/account report generation, shared completed list/download, mine/all history, viewer denial, and no history row after failed generation.
- [ ] Run the focused test; expect cross-account generation/history assertions to fail against global report-source/history queries.
- [ ] Enforce actor scope separately from report filters. Validate uploader selection only for admins. Add history and admin source filters to the report UI/API. Keep `generated_by` attribution and insert history only after file generation succeeds.
- [ ] Re-run the focused test; expect source isolation, shared history/download, selector, role, and failure-path assertions to pass.
- [ ] Run `python -m pytest tests/test_report_plate_display.py tests/test_auth.py -q`; expect report format/UI/API/download/role contracts to remain passing.
- [ ] Record results; leave paths unstaged/uncommitted absent separate authorization.

## Product decisions

One display default is not settled: whether report history initially opens on “My Reports” or “All Completed,” and whether an admin uploader selector initially opens on one account or “All Accounts.” Recommendation: default to “All Completed” and “All Accounts” to preserve the current global report/result view; enforcer source scope remains fixed to the authenticated account. Confirm or change these defaults before implementation. If any affected result path lacks a direct `video_id` or otherwise cannot be safely scoped, stop and report the exact path and whether a schema alteration is needed; do not infer ownership.




