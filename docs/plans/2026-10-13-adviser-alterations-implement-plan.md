# Reusable prompt: adviser alterations implement plan
Project: TAVIDM, D:\tavidm
Prepared: 2026-10-13, Australia/Perth
Companion prompt: adviser alterations
Intended executor: the user's selected Luna 6 with medium reasoning. This is a portable implementation handoff; it makes no assumptions about model-specific capabilities.

## Outcome

Implement the seven adviser requests in the existing TAVIDM application through small, ordered changes with evidence for each result. Deliver clearer evidence presentation and selection, accurate confidence explanations, a vehicle-grouped view preserving distinct events, visible persisted motorcycle detail observations, reliable account creation, the heading "Violation Type," and branded generated reports.

Saving or loading this prompt does not start implementation. Execute only when the user separately instructs you to run it. At execution, re-inspect the repository and proceed within the permissions below. Do not repeatedly ask approval for ordinary reversible implementation already authorized by that run instruction.

## Success criteria

### Phase 0 — Establish the current state and task checkpoints

Read AGENTS.md, relevant nested instructions, Git status/diffs, current source, tests, config/training/class_schema.json, core/detection_config.py, and recent development logs. Record a baseline of dirty files without opening secrets. Treat the source findings below as dated planning evidence, not runtime verification. Preserve unrelated changes and refresh all file/function anchors before editing.

Source findings observed on 2026-10-13:
- core/evidence.py has save_evidence_snapshot for an annotated scene and save_vehicle_crop for an unannotated tight crop. The annotated writer currently does not check cv2.imwrite's return value. Source inspection alone does not establish every caller's behavior.
- core/temporal_evidence.py already retains episode evidence and creates raw and annotated clip derivatives; reuse these mechanisms rather than building a second video spool.
- core/motorcycle_detail.py already scores processed frames with multiple components including sharpness, size and context; its "visibility" component is based on detector confidence, so it is not independent proof of visibility.
- core/rule_confidence.py maps legacy confidence to violation_confidence and stores detector confidence separately. Inspect serializers, UI, analytics and reports before interpreting a displayed percentage.
- app.py's detail outcome endpoint and database/sqlite_adapter.py's record_motorcycle_detail_review persist reviewed/dismissed/uncertain outcomes in motorcycle_detail_candidates. They intentionally do not confirm a legal case.
- finish_motorcycle_detail_scan already has a guarded, deduplicated No Helmet pending-review insert. Preserve current-run, enabled-rule, attribution and transaction gates. Do not revert to older descriptions that all detail evidence is permanently outside ordinary review.
- core/case_review_service.py and core/case_identity.py already group some observations/cases. A vehicle display group must respect this existing case lifecycle.
- POST /api/users already validates roles and writes through db.create_user; static/js/settings.js submits and reloads on success. The reported account disappearance has not been reproduced.
- templates/violations.html still has a sortable table heading "Type" with data-sort="type".
- core/reports.py generates PDF and Excel. No dedicated TAVIDM logo file was found under static/images during this inspection; the sidebar uses a brand icon. Search approved project assets before declaring the logo unavailable.
- The current class schema lists ten vehicle_detector_classes, including pickup_truck. AGENTS.md contains inconsistent roster counts; preserve the actual schema and canonical registry rather than editing classes to match prose.
- The planning session ran no tests and made no runtime, camera, checkpoint accuracy or deployment claims.

Create a short execution checklist with tasks, affected paths, acceptance conditions and evidence. Update it after each phase. For a medium-reasoning executor, finish and check one phase before expanding the next; re-read shared interfaces after changing them. No subagent dispatch is required.

Recommended implementation order: Phase 1 accounts; Phase 2 wording and confidence semantics; Phase 3 evidence storage/presentation; Phase 4 event frame selection; Phase 5 detail observation visibility; Phase 6 vehicle grouping; Phase 7 report branding; Phase 8 integrated verification. If a phase is blocked by a genuine missing asset or contract, complete independent phases.

### Phase 1 — Reproduce and repair account creation/persistence

Affected paths: app.py user endpoints and serializers; static/js/settings.js; templates/settings.html; core/auth.py; database/db.py and database/sqlite_adapter.py. Extend tests/test_account_access.py and a focused JavaScript harness where necessary.

In an isolated test environment, trace admin-created Enforcer and Viewer accounts from editor values to POST payload, response status/body, validation, password hashing, transaction commit, GET /api/users, rendered list and later login. Check the actual configured test DB path and import-time configuration; differing DB paths are a hypothesis, not a confirmed cause. Inspect db_session commit/rollback and uniqueness handling.

Repair the demonstrated failure at the smallest responsible layer. Report errors accurately, retain form input after failure, prevent overlapping saves, restore controls in finally, and distinguish a failed refresh from a failed write. Handle non-JSON responses and authorization/session errors. An uncertain write must be checked by reading server state before offering a duplicate retry. Ensure the submitted active setting is honored or clearly constrained by the existing account contract.

Preserve password hashing, admin-only account administration, role restrictions, active-user checks, existing access-modal race guards and action grants. Do not log passwords or change operational accounts.

Acceptance: both roles survive a fresh list request, page reload and a newly opened connection to the same temporary DB; active test accounts log in with the intended role; duplicate/invalid/unauthorized attempts return controlled errors and create no extra row; rapid repeated clicks and late responses do not corrupt the UI. If the reported symptom cannot be reproduced, document the traced paths and add only justified improvements, without claiming a fabricated root cause.

### Phase 2 — Clarify confidence and change the table heading

Change the visible sortable heading to "Violation Type" while preserving data-sort="type", filtering and API keys. Check initial server rendering and JavaScript refresh rendering.

Trace detector confidence, rule evidence score, evidence sufficiency, detail selection score and human review status across core/detector.py, core/rule_confidence.py, core/rule_types.py, core/video_processor.py, core/live_stream.py, app.py serializers, core/analytics.py, core/reports.py and affected templates/scripts.

Present detector confidence as a model score and violation_confidence as the rule engine's evidence score, not a calibrated probability of guilt. Explain that manual review describes the workflow. Missing scores display "Unavailable"; genuine zero displays 0%. Preserve legacy confidence compatibility. Never silently substitute detector confidence for missing rule confidence, use crop selection quality as model confidence, clamp scores upwards, or edit stored scores to improve appearance.

Keep legal status, reviewer decision and confirmed case status separately visible. Review band labels across analytics, settings, lists and live monitoring for consistency, but retain the current thresholds unless the user authorizes an evidence-backed change.

Produce a short threshold evaluation protocol: reviewed holdout footage separated by whole source; per-class precision/recall and false-positive/false-negative counts; distance, lighting, occlusion and small-motorcycle strata; compare candidate detection floors and any class-specific option. The adviser’s 65–90% is a question, not a fixed target or authorization to change thresholds. Do not train, activate a model, generate dataset derivatives or tune defaults without separate authorization. If holdout results are unavailable, deliver the protocol and leave operating thresholds unchanged.

Acceptance: matching evidence has matching scores across upload/live/list/detail/report paths; null and zero remain distinct; no score or legal status changes merely because wording changed.

### Phase 3 — Add clear evidence views while retaining the scene

Affected paths: core/evidence.py; core/video_processor.py; core/live_stream.py; existing authenticated evidence routes in app.py; templates/violations.html and templates/review_queue.html; static/js/violations.js, static/js/review_queue.js; shared CSS. Inspect detail evidence endpoints before sharing helpers.

Show the scene and an enlarged motorcycle/vehicle crop together. Provide explicitly named Raw scene and Annotated scene controls and a clear enlarged crop view. Use source resolution and padded, clamped crops; browser enlargement must not be described as recovered image detail. Keep road markings legible with restrained overlay opacity/thickness and label placement outside relevant markings where feasible. Preserve aspect ratio and accessible controls.

Prefer compatible evidence sidecars under the existing private evidence tree, resolved from trusted stored references or a validated manifest. Do not add DB columns for raw paths. Verify a safe schema-preserving storage/reference convention before implementing; if one cannot preserve provenance and cleanup, report the decision needed. Keep existing evidence_path semantics and old clients working. Raw and annotated images must identify the same source frame.

New writes must check success before publishing references. Use run/episode/frame scoped artifact identities to prevent reprocessing collisions; retain the existing path handling for legacy records. A partial write must not become advertised evidence. Do not change finalized/reviewed evidence silently. Validate all evidence variants against the configured private root; require authentication/authorization and reject path traversal. Never accept an arbitrary client filesystem path.

Old annotated-only evidence remains usable; explain when raw evidence is unavailable. Do not infer a raw frame by removing boxes from an annotated JPEG. Re-extraction from original video, if used, must be an explicit bounded operation with matching provenance, not a heavy list-page action.

Acceptance: edge/boundary crop cases work; failed writes do not publish nonexistent evidence; view toggles preserve frame identity; no aspect distortion; raw and annotated requests enforce permissions; old records and missing files degrade clearly; reprocessing cannot overwrite unrelated run evidence.

### Phase 4 — Select useful frames for multi-frame events

Reuse bounded processed-frame sampling and existing temporal evidence instead of decoding the whole video again. Inspect event/episode boundaries before deciding when evidence can be finalized.

Rank candidates only after validating same source run, track occurrence and event identity. Prioritize a frame that actually shows the rule-relevant interaction, marking, zone or rider/head region. Include sharpness, crop area, truncation/occlusion signals when available and detector reliability as distinct factors. Confidence alone is not visibility. Record unavailable factors rather than inventing observations. Reuse the detail selector only where its crop-oriented scoring fits; it is not automatically suitable for crossing or dwell rules.

Use deterministic ranking and tie-breaking. Keep scoring components and a human-readable selection reason. Maintain bounded top-K candidates per open episode with configurable, justified caps; store compressed candidates/references and avoid retaining every full-resolution frame in RAM. No second GPU model or unbounded per-track processing.

Retain the rule trigger/decision frame and temporal context when a best-looking frame would omit the actual crossing or event. An optional preferred display frame must not replace the underlying proof or alter event timing/confidence. Finalization must respect cancellation, deletion, reprocessing and run scope; do not update a finalized case using a later unrelated observation. Apply shared presentation semantics to live events, but explicitly document any source-retention limitation instead of promising upload-style history.

Acceptance: a highest-confidence blurred/context-free frame loses to a valid useful frame; attractive frames outside the event cannot win; ties are stable; missing candidates preserve usable original evidence; memory bounds and canceled/stale run behavior are covered.

### Phase 5 — Make recorded detail observations visible in the review workflow

Affected paths: detail templates/scripts, detail outcome API, database/sqlite_adapter.py, ordinary review presentation and current-run filtering. Reuse existing outcome persistence rather than creating duplicate writes.

Reproduce pressing "Record reviewed observation" and inspect persisted human_outcome/reviewer/time/notes. Check whether the default pending filter hides the reviewed row after save. Show a clear acknowledgment and a route to reviewed observations.

Expose a separately labeled Detail observations section/tab within the review workflow using the existing detail list/evidence endpoints where practical. It must display pending/reviewed/uncertain/dismissed states, reviewer/time/notes where permitted, source/frame, crop/scene, uncertainty reasons, and any existing link to an ordinary pending review. If the violations page links to this section, label it as review observations; do not insert nonviolations into confirmed violation counts or legal reports.

Recording reviewed/uncertain/dismissed remains a detail outcome. It does not create a legal confirmation, invent a violation type, or automatically approve a case. Preserve the existing four-class No Helmet integration and its positive attribution, deduplication, enabled-rule/current-run and transaction checks. Do not turn missing helmet/mirror detections into proof of absence or generic person into rider.

Ensure retries/refreshes do not create duplicate ordinary review rows. Enforce current-run/source visibility and authorization in the backend. Handle deletion/reprocessing, two reviewers and late responses using existing transaction/HTTP conflict conventions. Decide whether repeated outcome writes should be idempotent or conflict from current contracts, then document and test that choice.

Acceptance: recorded observations can be found after reload, remain visibly unverified, retain provenance and reviewer fields, obey role permissions, and never inflate legal/confirmed counts. Existing guarded No Helmet positive and negative cases continue to pass.

### Phase 6 — Add vehicle grouping as a display layer

Inspect core/case_identity.py, core/case_review_service.py, existing review decisions, linked observation records, event-time semantics and run filters before grouping. Default to an optional vehicle-grouped display with expandable events and a switch back to individual records. Preserve distinct stored events and existing case fusion behavior.

Use a validated identity key composed of source identity, processing/session run, track ID and track occurrence/identity epoch where available. A recycled track ID is not a durable vehicle ID. Missing provenance must not merge records: retain separate rows or clearly mark association uncertain. Do not group every null track into one vehicle or use vehicle class as identity.

Display each group's vehicle class, source/run, event count and time span, then each child event's canonical type, timestamp, evidence, review/case status and available score. Detail observations may appear as separately labeled children only with a verified association; lack of identity proof leaves them separate.

Plate-based cross-video association is optional and may only use explicitly human-verified plate identity. OCR candidates and generic recognized status must be checked against the current verified-identity contract; do not infer verification from a name alone. Default delivery uses source-local grouping.

Group at query/service level before pagination; do not group only the current page and claim complete vehicle records. Preserve existing event API response shapes; use an additive opt-in mode or dedicated compatible endpoint if necessary. Bound queries and avoid N+1 fetching. Keep event counts, vehicle-group counts and legal-case counts clearly distinct. Filters must define whether child matches determine group inclusion and whether nonmatching children are displayed; default to matching children with a clear filtered label.

Acceptance: multiple events from one valid occurrence appear together; different runs, videos, cameras and recycled tracks stay separate; uncertain identity stays separate; child details/evidence remain intact; filters and pagination are stable; grouping does not confirm, dismiss, merge database rows or change case-policy snapshots.

### Phase 7 — Brand generated reports

Locate an approved existing logo in project documentation/assets. The sidebar icon is not automatically the official logo. If no usable approved logo exists, request its location or asset and complete independent work; do not generate or substitute branding silently.

Use core/reports.py and the current report endpoints. Add the approved logo to PDF and Excel outputs supported by the UI. Use an existing supported image format/dependency; conversion must use available tools and preserve the approved asset. No dependency installation without authorization.

Reserve header space, preserve aspect ratio, maintain readable title/filters/generated time and legal/review wording. Keep pagination, repeated table headers, row wrapping, long legal text, verified/unverified plate wording and totals correct. Set practical Excel image anchoring and print area. A missing/corrupt asset must produce an observable controlled fallback/error under the existing report contract, not crash an unrelated request.

Acceptance: inspect rendered PDF pages and the spreadsheet header/print layout; logo and table do not overlap; empty and long multi-page reports remain readable; data counts and legal meanings are unchanged. Never claim a visual check from text extraction alone.

### Phase 8 — Integrated verification and handoff

Use a fresh process configured with disposable database, upload, evidence, report and artifact directories before importing TAVIDM. Set test-only bootstrap credentials without exposing them. Inspect tests/conftest.py and module collection/import behavior; fixture timing alone is not proof that operational storage is isolated. Keep mocked/test detector inputs separate from real checkpoint claims. Check available workers and cleanup behavior.

Run meaningful focused tests for each changed layer, including existing account access/DOM races; evidence/crop/selection/temporal lifecycle; detail API/queue/scan/three-class and four-class boundaries; review decisions/races/case services; confidence/roster; report plate display and pagination. Select actual existing test names by inspection, add regression tests for demonstrated failures and new behavior, and report exact commands/results. Then run the repository-required pytest suite in the isolated environment, distinguish pre-existing failures and fix task-caused regressions. JavaScript syntax/DOM checks complement browser checks.

Perform isolated browser validation as admin, Enforcer and Viewer: create/reload/login test accounts; toggle evidence views; inspect useful scene/crop/markings; record/find a detail observation; expand a vehicle group with multiple events; generate/download reports and inspect layouts. If app binding/browser tools fail, report the exact blocked checks and continue unaffected checks. No operational credentials or public server exposure.

Use synthetic/local disposable fixtures for error cases. A short existing local clip may be used read-only only if the user has authorized processing it; never modify original CCTV. Actual camera access, deployment and checkpoint accuracy require separate evidence and authorization.

Finish with a scoped diff review, git diff --check, changed-file list, acceptance checklist, test commands/results, visual/browser evidence, remaining blockers and rollback notes. Update the segmented development log for implemented changes using its skill. Do not describe unfinished, untested or asset-blocked items as complete.

## Boundaries

- Preserve the Flask/Bootstrap/SQLite monolith, current authorized YOLOv8m-family weights, ByteTrack, rule engine, all canonical violation types and zone annotation behavior. Preserve the frozen class schema including pickup_truck.
- Preserve database table structures and existing API clients. Compatible additive representation changes are allowed; migrations/schema alterations need a separately reviewed proposal and authorization.
- About/Developers settings are deferred. Training, dataset edits, model activation, unrelated engine changes, microservices and framework replacements are outside scope.
- Preserve dirty work and existing account/review race, authorization, provenance and fail-closed safeguards. Do not use git reset/clean or broad staging.
- Never use absence of a detail detection as legal evidence, auto-confirm a legal case, inflate a confidence score, or falsely equate tracker identity with verified cross-video vehicle identity.

## Permissions

- This saved prompt authorizes no execution on load. After a separate run instruction, read relevant source/history, edit scoped application/test/documentation files, run isolated checks and use reversible local test artifacts.
- Do not stage, commit, push, deploy, install dependencies, migrate/change operational databases, modify operational accounts, access secrets or connect cameras without explicit separate authorization.
- Ask for a required approved logo asset, or a schema/API change decision only when it blocks dependent work. Continue independent authorized phases.
- Do not send messages to other chats or external recipients or create a new chat automatically.

## Tools

Use available filesystem tools and rg for source discovery, Git for read-only status/diffs, the existing Python/Node runtimes for isolated verification, and available authenticated browser/artifact rendering tools for visual checks. Use Mnemosyne to retrieve this prompt and relevant project context; verify against current source. Tool availability is not permission for external side effects.

## Evidence

Completion requires exact changed paths, phase acceptance outcomes and current-source references; focused and full-suite test outputs reported separately; browser/visual evidence for UI and reports; explicit missing-asset/runtime limitations; and a development-log entry describing actual changes. No tests or runtime verification were performed when this plan was prepared. Threshold tuning and field accuracy remain unverified unless separately evaluated.

## Stop conditions

Finish when all authorized acceptance criteria have evidence and the handoff distinguishes implemented, tested, visually checked and blocked work. Pause only dependent work for a missing required asset, contradictory identity/storage contract, necessary schema break, missing execution authorization or denied required capability. Explain the concrete blocker and requested decision. Preserve partial work, record a reproducible checkpoint and continue independent phases. After repeated failures with the same cause, stop speculative retries, report evidence and the smallest required external action. Never silently remove scope to claim completion.
