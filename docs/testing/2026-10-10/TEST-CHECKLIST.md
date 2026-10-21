# TAVIDM group testing packet

**For classmates:** use the simpler [START-HERE-CHECKLIST.md](START-HERE-CHECKLIST.md), [ANNOTATION-GUIDE.md](ANNOTATION-GUIDE.md), and [SIMPLE-RESULTS.md](SIMPLE-RESULTS.md). This longer packet is an optional technical reference for the coordinator/developer.

Prepared 10 October 2026 for the current local working copy in D:\tavidm. This is a manual test plan, not a claim that these tests have been performed. The base commit inspected was a8273f62; uncommitted account-access changes are part of the reviewed working copy. A fresh Git clone of that commit alone may not include those changes.

## 1. What every tester must submit

There are **80 scenarios: 44 application scenarios and 36 detection scenarios**. Each assigned scenario needs a row in test-results.csv, exact observed behavior, and evidence. “Works,” “okay,” “done,” a tick without evidence, or a screenshot with no test ID does not count as a completed result.

Use these files:
- TEST-CHECKLIST.md: exact scenario prerequisites, steps, expected outcomes and evidence.
- test-results.csv: already contains all 80 scenarios; open in Excel, enable wrapped text and freeze the header. Save a separate copy per tester.
- footage-manifest.csv: record every input clip before testing.
- detection-events.csv: one row per independently labelled event, missed event, or unmatched system candidate.
- RESULT-AND-BUG-TEMPLATE.md: detailed run report and reproducible bug report.
- HANDOFF-MESSAGE.txt: message the coordinator can send to the group.

Do not replace an earlier failed result with a later PASS. Make a new execution row and link the earlier execution/bug. The original failure remains part of the record.

## 2. Coordinator setup — complete before giving access

Only the coordinator performs environment setup and prepares accounts/fixtures.
1. Provide a disposable test deployment and test database/output folders. Do not run destructive scenarios against operational evidence, accounts or another person's work. Back up the chosen test dataset before starting.
2. Supply the exact LAN URL and verify reachability from each client. A remote client uses the server's LAN address, not localhost. Record the host computer, OS, browser versions, CPU, RAM, GPU, server launch method and LAN type.
3. Freeze one build for a test round. Record Git commit, branch, dirty status and SHA-256 of app.py, core/auth.py, database/sqlite_adapter.py, static/js/settings.js and static/js/violations.js. Ask the developer for these values; do not edit source. If the build changes, start a new round and retest affected cases.
4. Create separate TEST accounts for Admin, Enforcer, and Viewer, plus two spare disposable officers for permission/race tests. At least two testers must have distinct Enforcer accounts for conflict tests. Supply credentials privately; do not put passwords, cookies, tokens or RTSP credentials in the results.
5. Record baseline enabled rules, dwell thresholds, lane direction tolerances, truck-ban window/classes, frame skip, detector confidence, active model filename/hash and actual model class list. Record optional OCR/detail capability as enabled/disabled/blocked. Do not silently enable optional features or change models to get a PASS.
6. Prepare labelled, approved local MP4s: clear daylight, dim/night, occlusion/crowding, a clean legal-control clip, a corrupt MP4, and paired positive/legal/ambiguous scenes for each available rule. MP4 is the current allowed upload extension. Use actual source recording time only when known. Record file hash, duration, dimensions, FPS, date/time/timezone and source.
7. Prepare disposable queue items and eligible/ineligible cases for the four dispositions, conflicts, case confirmation and test printing; two sources with distinguishable evidence; 12+ report rows including long text and mixed behavior/category records. Do not ask testers to invent records by editing the database.
8. Provide one approved test camera if live scope is being tested. Only the coordinator may disconnect/restart it. No camera means live scenarios are BLOCKED, not PASS.
9. Agree beforehand what is required for acceptance, performance targets and allowable precision/recall. This packet does not invent an accuracy target or treat source configuration as proof of field readiness.

## 3. Assign work and order

| Owner | Primary assignment | Required collaboration |
|---|---|---|
| Tester A | NET, AUTH, ACC | Admin + staff windows; another device for LAN checks |
| Tester B | UP, ZONE, PROC, LIVE, REC, DEL | Owner for restarts/deletion/camera; Tester C for detection truth |
| Tester C | REV, CASE, REP, DATA | Second reviewer for conflicts; Tester B for source evidence |
| All testers | UX and DET | Independent human labelling; coordinator consolidates results |

With two groupmates, split A from B/C, but conflict and independent evidence checks still require two accounts/people. Do not share one logged-in account.

Run in order: setup -> LAN/login -> account permissions -> upload/annotation -> processing -> detection comparison -> review/cases -> reports/analytics -> live/recovery -> disposable deletion. Capture pre-state before a mutation. Return changed test settings to the recorded baseline at the end of each scenario.

For each test:
1. Claim its ID and give it a unique Execution_ID, for example 20261010-A-ACC04-01.
2. Check every prerequisite. Missing prerequisite means BLOCKED with the exact missing item, not an improvised test.
3. Record account/role, browser/device, build and input IDs.
4. Perform the steps in order. Record exact clicks/input values and start/end timestamps.
5. Compare actual to expected; capture evidence at the point of outcome/error.
6. Reload/reopen after a saved change. Record persisted state, not just a success toast.
7. Fill the row immediately. Submit bug details for FAIL; owner-confirmed limitation detail for LIMITATION.

## 4. Result status rules

| Status | Meaning | Required evidence |
|---|---|---|
| NOT_RUN | No attempt | Assignment/next action |
| PASS | All expected assertions were observed | Actual behavior, exact values/IDs, evidence files |
| FAIL | Observed behavior contradicts expected behavior | Reproduction, expected vs actual, severity, evidence, bug ID |
| BLOCKED | The scenario cannot execute because a prerequisite/capability is unavailable | Missing prerequisite, blocker owner, exact message/status, next action |
| LIMITATION | An existing scope/product limit was actually observed and accepted as such by the coordinator | Observed limit, impact, coordinator decision; never silently treat as full feature PASS |

If one scenario contains several subchecks, it passes only if every required subcheck passes. Record individual subresults in Notes or the detailed run report. A partially successful scenario is FAIL or BLOCKED as appropriate. A known limitation can still fail the group's intended acceptance requirement.

Evidence naming: Execution_ID_01_before.png, Execution_ID_02_after.png, Execution_ID_requests.txt, Execution_ID_recording.mp4. Use the test ID and input/run ID in filenames. Store a redacted copy for sharing; preserve the original restricted evidence with the coordinator. Keep identifiable local CCTV/evidence inside the approved team storage. Use fake/test plate data for reports when possible.

For each screenshot include enough context to identify the page, selected account/source and outcome. For a race, spinner, lag or intermittent failure use a short screen recording and note elapsed seconds. Redact secrets from the URL, developer tools and messages. Do not clear failures before recording them.

## 5. Detection testing: required rules and scoring

Current source declares ten vehicle detector classes: car, van, jeepney, tricycle, autorickshaw, bus, truck, pickup_truck, motorcycle, bicycle. Person, rider, helmets and mirrors are not additional vehicle classes.

For each available vehicle class, record at least three clear independent vehicle events if footage exists; include night/occlusion separately. Record actual human label, predicted class, tracking discontinuities and missed vehicles. If a class has no suitable footage, mark that coverage BLOCKED. Do not invent samples or infer accuracy from a model filename.

All 12 canonical violations are included below. Current source lists Obstruction, Counterflow, Truck-Ban Violation and Motorcycle Overloading as implemented candidate paths; the other eight are partial. Automatic No Helmet is deliberately blocked; normal production currently lacks the mirror-mount visibility prerequisite. Runtime diagnostics and actual inputs decide whether a particular scenario can execute.

Positive, legal-control and unknown-context scenarios serve different purposes:
- Positive: compare a supported configured behavior against independently labelled footage. A missing candidate is a miss only when prerequisites/capability genuinely exist.
- Legal control: no candidate should assert a violation for the clearly legal event.
- Unknown context: missing/ambiguous evidence must suppress unsupported claims or state uncertainty. Suppression here is a safety check, not proof of positive detection accuracy.
- For blocked positive paths, enter BLOCKED and the exact capability reason. You may pass the separate uncertainty-control test if you actually observed correct suppression.
- Never generate unsafe behavior on a public road. Use existing approved footage; annotating a test fixture is not a legal determination.

Label source footage before looking at model output. Two people should agree on the behavior; record disagreement as UNKNOWN. Aim for three independent positive events, three legal controls and three ambiguous events per executable rule. Mark a smaller sample as limited coverage. Split clear/night/occluded results rather than averaging away poor conditions.

Use detection-events.csv:
- TP: one candidate matched to one eligible human-positive event, with correct behavior and timestamp.
- FP: candidate without a matching eligible human-positive event, or wrong behavior.
- FN: eligible human-positive event with no matching candidate.
- UNKNOWN: human/context evidence insufficient; exclude from precision/recall and report separately.
- BLOCKED: evaluator prerequisite/capability absent; exclude from eligible event totals and show separately.
- DUPLICATE: extra candidate for an already matched event; list explicitly and count as unmatched FP for candidate precision. Do not match it as another TP.

Before the round, agree event boundaries and matching tolerance in seconds. Use source/run/vehicle identity and overlapping event intervals; avoid treating every frame as a separate violation. One candidate cannot explain multiple independent human events. Legal clips have an FP count even if they have no human-positive events.

Report per rule and per condition:
Precision = TP / (TP + FP).
Recall = TP / (TP + FN).
If denominator is zero, report N/A with the reason; do not write 100%.
Always include raw TP/FP/FN, duplicates, UNKNOWN and BLOCKED counts, number of clips, footage duration and sample limitations. A small selected sample is not a production accuracy claim.

For performance record upload seconds, processing start/end, source duration, wall processing seconds, completion/failure state, CPU/RAM/GPU observations and number of simultaneous viewers/jobs. Processing speed ratio = source duration / processing wall time; distinguish queued time from active processing. Record whether accelerated playback/frame skip was configured. No unapproved benchmark target is implied.

## 6. Optional technical checks for ACC-07

Use only the disposable test deployment and target account ID supplied by the coordinator. Open browser developer tools Console while signed in. Capture status and response; never paste cookies or passwords. This example uses the existing logged-in session:

```javascript
async function testAccessUpdate(targetId, payload) {
  if (!Number.isInteger(targetId) || targetId <= 0) throw new Error("Use a supplied test-account ID");
  const response = await fetch("/api/users/" + targetId + "/access", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  console.log(response.status, await response.json());
}
```

Run separately and recheck saved state after each:
- Enforcer or Viewer: {grants: [{permission: "confirm_case"}]} -> 403; no change.
- Admin against own account: {grants: [{permission: "approve_policy"}]} -> 403; no self-grant.
- Admin against disposable target: {grants: [{permission: "not_a_permission"}]} -> 400; no change.
- Admin against disposable target: {grants: [{permission: "confirm_case"}], revokes: ["not_a_permission"]} -> 400; no partial change.
- Admin against disposable target: {grants: [{permission: "confirm_case"}], actor_user_id: 999999} -> 400; forged actor field rejected.

Do not run other console commands against operational accounts. If testers cannot use developer tools, coordinator/technical tester must perform these checks; mark delegated/blocker until recorded.

## 7. Release decision and coordinator review

Before letting this become a deployment/demo acceptance:
- Every assigned row has a real result, owner and evidence; all NOT_RUN/BLOCKED items are explicitly listed.
- All required P0 checks pass; no unresolved permission bypass, wrong-account mutation, wrong-source evidence, lost decision/data or misleading confirmed/printed state.
- Detection-positive coverage for each intended supported rule is demonstrated; blocked/partial rules are labelled honestly in the presentation.
- A complete sample goes from upload -> annotation -> processing -> review -> eligible case -> test print attestation -> matching report.
- Results include actual LAN client evidence and a real browser walkthrough; earlier automated tests do not substitute.
- The coordinator lists remaining FAIL/LIMITATION/BLOCKED cases and decides whether each blocks the intended demo/deployment. This checklist does not automatically certify readiness.

Mandatory summary: build identity; planned/executed/pass/fail/blocked/limitation/not-run totals; required P0 outstanding; bugs by severity; rule coverage with counts; conditions not tested; performance observations; evidence folder; explicit recommendation and sign-off.

Severity for observed bugs:
Critical: unauthorized authority, another officer/source changed, private data exposed, evidence/decisions lost.
High: core upload/process/review/report workflow crashes or cannot complete.
Medium: wrong count/filter/status, misleading saved access, report row omission, recoverable stuck state.
Low: cosmetic defect with no functional or data impact.
Priority P0/P1 in this packet is test execution priority, not bug severity.

## 8. Scenario checklist

Perform the listed prerequisites and steps. Each test needs a separate recorded execution.

### NET-01 — Open from two LAN devices

- **Owner / priority:** Tester A / P0
- **Prerequisites:** Owner supplies test server URL; two devices on the intended LAN.
- **Steps:** Open /login on the server, then on both client devices. Log in with separate accounts; load dashboard and /live-monitor; reload once.
- **Expected:** All devices reach the same server and data. No localhost address is used on remote clients. Record failures separately for server and each client.
- **Record:** URL without credentials; device/browser versions; screenshot from each device; load times.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### AUTH-01 — First installation bootstrap

- **Owner / priority:** Tester A / P0
- **Prerequisites:** Owner only; fresh disposable installation; no operational database.
- **Steps:** Owner starts the fresh installation using the documented explicit bootstrap configuration. Sign in with the initial administrator. Record account creation outcome without displaying the secret.
- **Expected:** Initial admin works using the configured secret; no public first-visitor admin claim or shared default password. This is blocked if no fresh test installation exists.
- **Record:** Setup outcome; redacted startup message; logged-in role screenshot; no passwords.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### AUTH-02 — Admin creates individual accounts

- **Owner / priority:** Tester A / P0
- **Prerequisites:** Signed-in test admin; unique TEST_<initials>_ names.
- **Steps:** Settings > Individual Account Access > Add User. Create one Enforcer and one Viewer. Log in to each in a separate private window; compare displayed identity/role.
- **Expected:** Both accounts authenticate and show the assigned role. Each person has a distinct account.
- **Record:** Usernames/roles; creation screenshots; login outcomes.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### AUTH-03 — Invalid login and logout

- **Owner / priority:** Tester A / P0
- **Prerequisites:** Disposable account; one private window.
- **Steps:** Try one incorrect password and one nonexistent username. Then sign in correctly, log out, revisit /settings and a previously opened evidence URL.
- **Expected:** Invalid logins get a clear rejection and no session; logged-out protected pages/data are denied. Browser history must not permit new authenticated actions.
- **Record:** Exact error text; logout screenshot; protected request status where available.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### AUTH-04 — Role access and direct navigation

- **Owner / priority:** Tester A / P0
- **Prerequisites:** Admin, Enforcer, Viewer with no extra grants.
- **Steps:** For each role visit /, /analytics, /live-monitor, /violations, /review-queue, /reports, /settings. Also open /api/users and /api/review-queue directly.
- **Expected:** All can view first four pages. Admin/Enforcer can use review queue and reports. Only Admin manages Settings/users. Viewer review API access is denied. Record every role/page pair.
- **Record:** One mini-matrix per role with URL, allowed/denied, HTTP status or redirect, screenshot.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### AUTH-05 — Disable an already signed-in account

- **Owner / priority:** Tester A / P0
- **Prerequisites:** Two browser sessions; disposable staff account.
- **Steps:** Keep staff signed in. Admin edits that account and disables it. Staff reloads /violations and tries a previously available action. Re-enable and sign in again.
- **Expected:** Disabled account cannot continue protected work despite its old cookie. Re-enabled account can log in normally.
- **Record:** Before/after active flag; staff response; no cookie/token contents.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### AUTH-06 — Password change invalidates old session

- **Owner / priority:** Tester A / P0
- **Prerequisites:** Two sessions; disposable staff account.
- **Steps:** Staff stays signed in. Admin changes its password through Edit. Staff reloads a protected page; attempt old then new password in a private window.
- **Expected:** Old session is invalidated on the next request; old password rejected; new password accepted. Blank password when editing other fields retains the password.
- **Record:** Outcomes and screenshots without either password.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### ACC-01 — Inspect defaults and special authority

- **Owner / priority:** Tester A / P0
- **Prerequisites:** Admin; fresh test Enforcer and Viewer.
- **Steps:** Open Access for each role; record every row's Role default, Explicit grant, Effective and Change. Inspect Approve legal policy and Confirm plate identity.
- **Expected:** Defaults, grants and effective access are distinct. Admin status alone does not grant policy approval. Plate identity is informational/admin-only with no Grant button.
- **Record:** Full panel screenshots and row-by-row observations.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### ACC-02 — Grant and revoke in an existing session

- **Owner / priority:** Tester A / P0
- **Prerequisites:** Viewer signed in; test confirmed review case; admin in another session.
- **Steps:** Viewer attempts Confirm Case and is denied. Admin grants confirm_case to that Viewer. Viewer reloads violations and performs the permitted test action. Admin revokes; Viewer reloads and tries a different untouched test case.
- **Expected:** Grant/revoke apply without logging out. New requests reflect authority. After revoke, new case confirmation is denied. Do not reuse already completed action as the denial check.
- **Record:** Account ID; two case IDs; before/granted/revoked screens; request outcomes.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### ACC-03 — Revoking an additive grant preserves role defaults

- **Owner / priority:** Tester A / P0
- **Prerequisites:** Disposable Enforcer.
- **Steps:** Grant confirm_case to the Enforcer, reload, revoke that explicit grant, reload again.
- **Expected:** Explicit grant disappears; inherited Enforcer case-confirmation access remains. Do not report this expected behavior as a bug.
- **Record:** All three column states before/after.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### ACC-04 — Rapid permission changes

- **Owner / priority:** Tester A / P0
- **Prerequisites:** Disposable Viewer; DevTools Network slow throttling or screen recording.
- **Steps:** Open Access. Click Grant confirm_case, then immediately click attest_print before completion. Wait for refresh; complete attest_print once controls unlock. Close/reopen; inspect both grants. Repeat with revoke.
- **Expected:** At most one mutation is active in this modal; other controls are disabled until refresh. Both completed changes are represented accurately after reopening.
- **Record:** Video of clicks/disabled controls; ordered Network request list with secrets removed; final grants.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### ACC-05 — Switch accounts during pending requests

- **Owner / priority:** Tester A / P0
- **Prerequisites:** Two disposable accounts with visibly different grants; slow network.
- **Steps:** Open A's Access, close it, open B quickly; repeat several times. Also start A's grant, then switch to B while pending. Reopen A and inspect saved state.
- **Expected:** B's title, username and capabilities all describe B. A's callbacks/errors do not overwrite B; submitted changes remain on A. Inspect both accounts for unintended changes.
- **Record:** Recording; A/B IDs; before/after grants for both; request targets.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### ACC-06 — Uncertain update and recovery

- **Owner / priority:** Tester A / P0
- **Prerequisites:** Disposable Viewer; administrator session; browser DevTools.
- **Steps:** Start a grant under throttling; set the client offline during the request. Record the warning. Restore connection, close/reopen Access and inspect actual saved state before retrying.
- **Expected:** No false success or rollback claim; clear recovery guidance. If refresh also fails, no stale editable controls remain. The final report distinguishes saved, unsaved and uncertain outcomes.
- **Record:** Timing of offline/online; message text; final authoritative state after recovery.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### ACC-07 — Rejected authority and malformed updates

- **Owner / priority:** Tester A / P0
- **Prerequisites:** Technical tester; disposable account; browser console helper in this guide.
- **Steps:** As Enforcer/Viewer attempt access PUT. As Admin attempt self-grant approve_policy, an unknown permission, and a mixed valid/invalid request. Reload target Access each time.
- **Expected:** Non-admin/self-grant denied; invalid names rejected; mixed-invalid request makes no partial grant. No client-supplied actor can extend authority.
- **Record:** Request payload without secrets; response status/body; unchanged before/after grants.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### UP-01 — Upload valid MP4 and provenance

- **Owner / priority:** Tester B / P0
- **Prerequisites:** Owner-approved 20-60 second MP4; actual recording date/time if known.
- **Steps:** Live Monitor > upload video. Use unique TEST name; enter known recording timestamp and location/source details where offered. Save; open uploaded video and inspect details.
- **Expected:** Playable test video with correct filename/source; actual recording time is preserved. Unknown time must not be fabricated or replaced with upload time.
- **Record:** Filename/hash/size/duration/FPS; video ID; supplied time/timezone and displayed time.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### UP-02 — Reject unsupported or invalid files

- **Owner / priority:** Tester B / P1
- **Prerequisites:** Harmless .txt file and a corrupt/renamed test MP4; no executable files.
- **Steps:** Attempt .txt upload; attempt corrupt MP4. Reload video list and try a valid MP4 afterward.
- **Expected:** Invalid input gets a useful rejection; no usable phantom video/process job. Valid upload still works afterward.
- **Record:** File type/size; exact errors; list before/after.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### UP-03 — Upload limit and same filename

- **Owner / priority:** Tester B / P1
- **Prerequisites:** Owner provides safe file near configured limit; do not fill the disk.
- **Steps:** Record configured max upload size. Try an above-limit file only if provided; upload two different MP4s with the same filename.
- **Expected:** Above-limit upload is clearly rejected. Same-name inputs remain distinguishable by ID; neither silently overwrites the other's source/results.
- **Record:** Limit; actual byte sizes; both IDs and source playback.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### ZONE-01 — Draw, save and reload zones

- **Owner / priority:** Tester B / P0
- **Prerequisites:** Test video; owner-defined scene geometry.
- **Steps:** Open Zone Annotation; draw applicable polygons inside image; save for this video; close and reopen; compare vertices and zone types.
- **Expected:** Saved geometry persists on the correct video and matches the actual roadway. Labels resolve to the intended zone type.
- **Record:** Annotation screenshot; video ID; zone types and saved coordinates/export if available.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### ZONE-02 — Invalid geometry and template independence

- **Owner / priority:** Tester B / P1
- **Prerequisites:** Two disposable videos and a TEST template.
- **Steps:** Attempt incomplete/out-of-image geometry through supported UI; cancel without saving. Create template, apply to second video, edit second video only, reopen first/template.
- **Expected:** Invalid geometry is rejected or cannot be saved. Cancel preserves prior annotation. Video-only edits do not silently alter other videos or reusable template.
- **Record:** Before/after geometry for both videos/template; exact error if rejected.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### ZONE-03 — Directions, signs and markings

- **Owner / priority:** Tester B / P0
- **Prerequisites:** Owner-approved configured counterflow/sign/marking scenes; controls must exist.
- **Steps:** Record lane-flow direction, associated lanes, sign threshold and prohibited direction, marking geometry. Save/reopen. Run an opposite-direction example and legal-direction control.
- **Expected:** Saved context remains correctly associated. Counting lines alone must not act as no-entry thresholds. Unsupported annotation controls are recorded as BLOCKED with owner-confirmed reason.
- **Record:** Scene sketch/screenshot with arrows; geometry/context details; exact sign/marking and test clip IDs.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### PROC-01 — Background processing end to end

- **Owner / priority:** Tester B / P0
- **Prerequisites:** Valid annotated MP4; enabled supported rule.
- **Steps:** Choose Process in Background > Start Processing. Observe queued/running/completed; navigate away and return; inspect results, diagnostics and source playback.
- **Expected:** Processing completes without browser staying open. Correct video/run receives results. Candidates remain for human review, not automatically confirmed.
- **Record:** Video/run IDs; start/end time; stage history; final totals; diagnostics and evidence links.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### PROC-02 — Watch-live versus background

- **Owner / priority:** Tester B / P0
- **Prerequisites:** Same source clip; two separate disposable uploads with identical annotations/settings.
- **Steps:** Run one in background and one with Process and Watch Live. Record boxes, track IDs, progress and final summaries. Compare unique event identities/counts.
- **Expected:** Both finish with consistent final behavior within documented processing settings; preview doesn't create a second processing run for one start. Differences require exact examples.
- **Record:** Both run IDs; settings; candidate counts; preview recording; mismatch timestamps.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### PROC-03 — Queue and multiple clients

- **Owner / priority:** Tester B / P0
- **Prerequisites:** Three disposable annotated clips; two client devices.
- **Steps:** Queue three clips; while first runs, another client requests the same video's processing. Observe jobs and ordering until completion.
- **Expected:** No duplicate simultaneous processing of the same run; jobs resolve without stuck states. Record actual concurrency/order rather than assuming throughput.
- **Record:** Submission times/order; job states and IDs; duplicate response; durations.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### PROC-04 — Cancel queued/running jobs

- **Owner / priority:** Tester B / P0
- **Prerequisites:** Only disposable clips; no groupmate's active job.
- **Steps:** Queue clips, cancel one queued job. Separately cancel a running job using its control. Reload status and list; restart an eligible test clip.
- **Expected:** Cancellation affects only the selected job/run. Terminal state is accurate, no permanent spinner, other jobs continue. Restart creates an identifiable run.
- **Record:** IDs/status before/after; cancellation timing; effects on other jobs.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### PROC-05 — Reprocessing and zero-result run

- **Owner / priority:** Tester B / P0
- **Prerequisites:** Disposable clip with prior candidates; matching legal/control clip or all-off run approved by owner.
- **Steps:** Record prior run/results; reprocess using documented different rule selection that produces zero results. Reload violations/crossing summary/history.
- **Expected:** Latest completed run is correctly identified; old detections do not masquerade as current zero-result output. Historical results retain provenance where available.
- **Record:** Old/new run IDs; expected zero reason; current counts; history screenshot.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### PROC-06 — Rule selection persistence and disabled rules

- **Owner / priority:** Tester B / P1
- **Prerequisites:** Admin; test clip known to produce one selected behavior; saved original settings.
- **Steps:** Disable that rule for test run using the supported selection; process and verify. Restore original selection and rerun. Reopen settings/process modal.
- **Expected:** Disabled rule creates no new candidates for that run; changing run selection does not unexpectedly rewrite global settings. Restored run is separately identifiable.
- **Record:** Enabled lists per run and global before/after; candidate IDs/counts.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### REV-01 — Evidence matches candidate

- **Owner / priority:** Tester C / P0
- **Prerequisites:** At least five pending test observations across two sources.
- **Steps:** Open each review item. Compare source, timestamp, vehicle/track, scene frame, crop and clip/sequence if present with source playback.
- **Expected:** Evidence belongs to correct source/event and is useful for assessment; absent media has an explicit explanation, not another item's evidence. Flag any wrong-source evidence immediately.
- **Record:** Five item/run/track IDs and timestamps; media filenames/links and annotated screenshots.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### REV-02 — Four human-review dispositions

- **Owner / priority:** Tester C / P0
- **Prerequisites:** Four different untouched test review items.
- **Steps:** Record pre-state. On separate items choose Confirmed proposed violation, Corrected canonical violation, No violation and Insufficient evidence; supply specific reasons. Reload history/cases.
- **Expected:** Confirm/correction creates the appropriate case mapping; no violation and insufficient remain distinct and do not create enforcement cases. Reason and actual reviewer are retained.
- **Record:** Four IDs; exact reasons; chosen/corrected rule; cases before/after; history.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### REV-03 — Require reason and prevent duplicate decisions

- **Owner / priority:** Tester C / P0
- **Prerequisites:** Untouched item; two Enforcer sessions.
- **Steps:** Try submitting a decision with blank reason. Then submit valid reason; retry/double-click same action; reload history and cases.
- **Expected:** Blank reason rejected. Retries do not create duplicate cases/events. Closed status and reason remain consistent.
- **Record:** Request outcomes; case/decision counts; final history.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### REV-04 — Two reviewers conflict

- **Owner / priority:** Tester C / P0
- **Prerequisites:** One untouched test item; two separate Enforcer accounts.
- **Steps:** Both open same item. A confirms with reason; without reloading B submits No violation with a different reason. Repeat reversed order on a second item.
- **Expected:** First persisted decision wins; stale conflicting write is clearly rejected (HTTP 409 when captured). No silent overwrite, second case or partial state.
- **Record:** Both users/IDs/times/reasons; statuses; final persisted history/case.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### REV-05 — Confidence and uncertain evidence

- **Owner / priority:** Tester C / P0
- **Prerequisites:** Clear item plus ambiguous/occluded candidate; read its diagnostics.
- **Steps:** Record detector confidence and rule/violation confidence separately; inspect reasons. On ambiguous item choose Insufficient evidence rather than guessing.
- **Expected:** Scores are described accurately and aren't assumed to mean legal certainty. Weak/unknown evidence is not auto-confirmed. Evidence limitations stay visible.
- **Record:** Both score values/labels; exact reasons; reviewer disposition.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### CASE-01 — Case confirmation and print attestation

- **Owner / priority:** Tester C / P0
- **Prerequisites:** Owner-prepared eligible test case and a flag-only/unverified mapping control.
- **Steps:** Try Confirm Case on eligible case and ineligible control. Open Printable Record/preview/download for eligible case, then reload outcome before any print attestation.
- **Expected:** Confirmation obeys eligibility. Preview/download alone does not set Notice Printed. Ineligible mapping is clearly blocked or review-only.
- **Record:** Case IDs; eligibility context; before/after outcome; preview artifact.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### CASE-02 — Explicit notice-print attestation

- **Owner / priority:** Tester C / P0
- **Prerequisites:** Eligible confirmed test case; authorized staff; print-to-PDF labelled TEST if physical printer unavailable.
- **Steps:** Perform actual test print and explicitly attest only after test printing. Reload case/history/report. Attempt same attestation again.
- **Expected:** Notice Printed is recorded only through explicit authorized attestation; actor/time retained; retry does not duplicate the event. Test document is not an operational ticket.
- **Record:** Print artifact marked TEST; attestation actor/time; event count; report outcome.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### CASE-03 — Event time and plate verification boundaries

- **Owner / priority:** Tester C / P0
- **Prerequisites:** Owner-prepared test cases; readable and unreadable examples; Admin and Enforcer.
- **Steps:** Inspect event time; submit a supported correction with provenance. Inspect plate status. Enforcer attempts admin-only plate identity action; Admin uses documented review flow only if enabled.
- **Expected:** Unknown time/plate remains unknown; uploaded time isn't proof of recording time; machine OCR isn't human verification. Enforcer cannot bypass admin-only identity confirmation.
- **Record:** Original/corrected time with basis; statuses; redacted crops; denied/allowed outcomes.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### REP-01 — Filters agree across UI, PDF and Excel

- **Owner / priority:** Tester C / P0
- **Prerequisites:** Test cases with known dates, behaviors, sources and statuses.
- **Steps:** Reports: choose date range, violation type, source and status. Before exporting, list expected case IDs manually. Generate PDF and Excel; compare every included ID.
- **Expected:** Both files contain the same expected filtered cases, no excluded case, correct category/status pairs and plate verification labels.
- **Record:** Exact filters/timezone interpretation; expected IDs; actual IDs from both files; export files.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### REP-02 — Pagination and long content

- **Owner / priority:** Tester C / P0
- **Prerequisites:** Owner-prepared 12+ cases with long reason/category text and one multi-behavior case.
- **Steps:** Export PDF/Excel. Inspect every page at normal zoom: first/last rows, repeated headers, wrapped text, continuation IDs, column alignment; compare long text to source.
- **Expected:** No missing rows/text, split-cell pages, incorrect category/status association or clipped essential values. Record any header clipping separately.
- **Record:** Page count; ID count; page screenshots; original and exported long text.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### REP-03 — Empty report and large export limit

- **Owner / priority:** Tester C / P1
- **Prerequisites:** Empty filter range; optional owner-prepared >5000 record test dataset.
- **Steps:** Generate both formats with no matching cases. For large dataset compare matching UI total against exported rows and identify truncation.
- **Expected:** Empty result is understandable. Existing export code has a 5000-row cap: report observed truncation as LIMITATION/FAIL against export-all requirement, never silently PASS full export.
- **Record:** Matching total, exported total, missing IDs/range, messages; no fabricated large dataset.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### DATA-01 — Totals and class accounting

- **Owner / priority:** Tester C / P0
- **Prerequisites:** One manually counted short clip; record identical filters/run scope.
- **Steps:** Compare dashboard, analytics, violations and crossing summary for same source/current run. Manually count 10 vehicle classes crossing the annotated line; distinguish unique crossings from detections.
- **Expected:** Counts use explainable matching scope; persons/riders/helmets/mirrors aren't vehicle classes. Reprocessing does not silently mix old and new totals.
- **Record:** Manual/system per-class counts; source/run/filter scope; count definitions.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### LIVE-01 — Start, stop and recover stream

- **Owner / priority:** Tester B / P0
- **Prerequisites:** Owner-approved test RTSP camera; credentials stay private.
- **Steps:** Admin adds test camera through Settings; two clients view it. Authorized Enforcer stops stream; restart. Owner briefly disconnects ONLY this test camera and reconnects.
- **Expected:** Correct feed/status on both clients; stop affects selected camera; interruption is explicit and recovery possible; no permanent spinner.
- **Record:** Camera ID; live timestamps; startup/recovery times; redacted screenshots.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### LIVE-02 — Live candidate through review and case

- **Owner / priority:** Tester C / P0
- **Prerequisites:** Approved test live scene with an independently observed supported behavior.
- **Steps:** Record camera/time/track when candidate appears; open queue/evidence; review and attempt case creation on this test observation.
- **Expected:** Live provenance/evidence are correct and eligible decision persists without a null-video-ID crash. If no candidate, record prerequisites and classify accuracy versus capability blockage.
- **Record:** Camera/session/item/case IDs; full timestamps; diagnostic/error text.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### LIVE-03 — Live diagnostics and multiple viewers

- **Owner / priority:** Tester B / P1
- **Prerequisites:** Test camera running; missing-context diagnostic known.
- **Steps:** Open same feed on two clients for 10 minutes; record CPU/RAM and diagnostics at start/end; reload one client; test truck-ban time context if available.
- **Expected:** Viewing doesn't silently double the inference pipeline. Diagnostics remain bounded/useful; lack of trusted scene time suppresses time-dependent candidates with an explanation.
- **Record:** Client count; diagnostic text/count; CPU/RAM samples; elapsed time; observed time source.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### REC-01 — Server restart with persisted data

- **Owner / priority:** Owner + Tester B / P0
- **Prerequisites:** Owner coordinates; test deployment only; no unrelated jobs.
- **Steps:** Record accounts, annotations, completed run, case and report IDs. Restart test server once; reopen on both clients. Inspect records and any interrupted job.
- **Expected:** Persisted records remain intact; interrupted work has truthful recoverable status; no fabricated completion or lost decisions.
- **Record:** Restart time; before/after IDs/counts; job state; startup errors.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### DEL-01 — Remove results versus delete video

- **Owner / priority:** Owner + Tester B / P0
- **Prerequisites:** Two disposable TEST videos only; snapshot IDs/results/linked cases beforehand.
- **Steps:** On first use Remove Results if offered; verify source playback and affected output. On second choose Delete Permanently; record confirmation and dependency handling. Check unrelated video afterward.
- **Expected:** Results-only removal is distinct from source deletion. Dependency refusal is acceptable only with clear reason and unchanged data. Permanent deletion affects the selected test item only.
- **Record:** IDs; exact confirmation/refusal; source availability and dependent records before/after.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### UX-01 — Search, paging and narrow screens

- **Owner / priority:** All testers / P1
- **Prerequisites:** At least two list pages; desktop and narrow/mobile viewport.
- **Steps:** Search by visible known value; filter status/type/source; navigate pages; reset filters. At 360px and desktop width inspect controls, modal scrolling, evidence, errors; tab through primary actions.
- **Expected:** Search/filter/page results agree; controls usable without hidden essential actions or text; empty/error states clear. Record exact device and each failing viewport.
- **Record:** Queries; expected IDs; page sizes; viewport dimensions; screenshots and keyboard notes.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### DET-01P — Illegal Parking — positive

- **Owner / priority:** Tester B + Tester C / P1
- **Prerequisites:** no_parking zone; saved parking dwell; stationary clearly tracked vehicle; capability status: partial. Owner supplies independently labelled footage.
- **Steps:** Use a separate TEST upload/run. Vehicle remains stationary inside no_parking longer than configured dwell plus enough pre/post frames. Record annotation, enabled rule, settings, clip start/end and every matching candidate. Inspect evidence and diagnostics; review independently before comparing system output.
- **Expected:** Candidate expected only when executable prerequisites are satisfied; partial pipeline gap is recorded BLOCKED/LIMITATION, not a clean pass. All candidates require human review; no automatic enforcement confirmation.
- **Record:** Clip/hash/run ID; event start/end and track; human label; prerequisite checklist; candidate or suppression reason; TP/FP/FN/UNKNOWN classification.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### DET-01N — Illegal Parking — legal control

- **Owner / priority:** Tester B + Tester C / P0
- **Prerequisites:** no_parking zone; saved parking dwell; stationary clearly tracked vehicle; capability status: partial. Owner supplies independently labelled footage.
- **Steps:** Use a separate TEST upload/run. Same vehicle passes through or stops below threshold; outside-zone stationary vehicle. Record annotation, enabled rule, settings, clip start/end and every matching candidate. Inspect evidence and diagnostics; review independently before comparing system output.
- **Expected:** Candidate expected only when executable prerequisites are satisfied; partial pipeline gap is recorded BLOCKED/LIMITATION, not a clean pass. All candidates require human review; no automatic enforcement confirmation.
- **Record:** Clip/hash/run ID; event start/end and track; human label; prerequisite checklist; candidate or suppression reason; TP/FP/FN/UNKNOWN classification.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### DET-01U — Illegal Parking — unknown context

- **Owner / priority:** Tester B + Tester C / P0
- **Prerequisites:** no_parking zone; saved parking dwell; stationary clearly tracked vehicle; capability status: partial. Owner supplies independently labelled footage.
- **Steps:** Use a separate TEST upload/run. Remove no_parking annotation or use uncertain boundary/occlusion. Record annotation, enabled rule, settings, clip start/end and every matching candidate. Inspect evidence and diagnostics; review independently before comparing system output.
- **Expected:** Candidate expected only when executable prerequisites are satisfied; partial pipeline gap is recorded BLOCKED/LIMITATION, not a clean pass. All candidates require human review; no automatic enforcement confirmation.
- **Record:** Clip/hash/run ID; event start/end and track; human label; prerequisite checklist; candidate or suppression reason; TP/FP/FN/UNKNOWN classification.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### DET-02P — Obstruction — positive

- **Owner / priority:** Tester B + Tester C / P1
- **Prerequisites:** active lane or crossing polygon; saved obstruction/crossing dwell; capability status: implemented. Owner supplies independently labelled footage.
- **Steps:** Use a separate TEST upload/run. Clearly tracked stationary vehicle occupies active lane or blocks crossing longer than configured dwell. Record annotation, enabled rule, settings, clip start/end and every matching candidate. Inspect evidence and diagnostics; review independently before comparing system output.
- **Expected:** Expect a supported candidate for the positive clip and no candidate for legal/insufficient-context controls. All candidates require human review; no automatic enforcement confirmation.
- **Record:** Clip/hash/run ID; event start/end and track; human label; prerequisite checklist; candidate or suppression reason; TP/FP/FN/UNKNOWN classification.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### DET-02N — Obstruction — legal control

- **Owner / priority:** Tester B + Tester C / P0
- **Prerequisites:** active lane or crossing polygon; saved obstruction/crossing dwell; capability status: implemented. Owner supplies independently labelled footage.
- **Steps:** Use a separate TEST upload/run. Moving vehicle passes through; stationary vehicle is outside lane/crossing. Record annotation, enabled rule, settings, clip start/end and every matching candidate. Inspect evidence and diagnostics; review independently before comparing system output.
- **Expected:** Expect a supported candidate for the positive clip and no candidate for legal/insufficient-context controls. All candidates require human review; no automatic enforcement confirmation.
- **Record:** Clip/hash/run ID; event start/end and track; human label; prerequisite checklist; candidate or suppression reason; TP/FP/FN/UNKNOWN classification.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### DET-02U — Obstruction — unknown context

- **Owner / priority:** Tester B + Tester C / P0
- **Prerequisites:** active lane or crossing polygon; saved obstruction/crossing dwell; capability status: implemented. Owner supplies independently labelled footage.
- **Steps:** Use a separate TEST upload/run. Remove relevant zone or use too-short/ambiguous occupancy. Record annotation, enabled rule, settings, clip start/end and every matching candidate. Inspect evidence and diagnostics; review independently before comparing system output.
- **Expected:** Expect a supported candidate for the positive clip and no candidate for legal/insufficient-context controls. All candidates require human review; no automatic enforcement confirmation.
- **Record:** Clip/hash/run ID; event start/end and track; human label; prerequisite checklist; candidate or suppression reason; TP/FP/FN/UNKNOWN classification.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### DET-03P — Counterflow — positive

- **Owner / priority:** Tester B + Tester C / P1
- **Prerequisites:** active_lane zone with correctly saved lane-flow direction; capability status: implemented. Owner supplies independently labelled footage.
- **Steps:** Use a separate TEST upload/run. Vehicle maintains movement against configured lane flow over sufficient trajectory. Record annotation, enabled rule, settings, clip start/end and every matching candidate. Inspect evidence and diagnostics; review independently before comparing system output.
- **Expected:** Expect positive candidate only with stable opposite motion; missing/conflicting direction must not invent counterflow. All candidates require human review; no automatic enforcement confirmation.
- **Record:** Clip/hash/run ID; event start/end and track; human label; prerequisite checklist; candidate or suppression reason; TP/FP/FN/UNKNOWN classification.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### DET-03N — Counterflow — legal control

- **Owner / priority:** Tester B + Tester C / P0
- **Prerequisites:** active_lane zone with correctly saved lane-flow direction; capability status: implemented. Owner supplies independently labelled footage.
- **Steps:** Use a separate TEST upload/run. Vehicle follows flow; vehicle remains stationary. Record annotation, enabled rule, settings, clip start/end and every matching candidate. Inspect evidence and diagnostics; review independently before comparing system output.
- **Expected:** Expect positive candidate only with stable opposite motion; missing/conflicting direction must not invent counterflow. All candidates require human review; no automatic enforcement confirmation.
- **Record:** Clip/hash/run ID; event start/end and track; human label; prerequisite checklist; candidate or suppression reason; TP/FP/FN/UNKNOWN classification.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### DET-03U — Counterflow — unknown context

- **Owner / priority:** Tester B + Tester C / P0
- **Prerequisites:** active_lane zone with correctly saved lane-flow direction; capability status: implemented. Owner supplies independently labelled footage.
- **Steps:** Use a separate TEST upload/run. Missing direction, overlapping conflicting lane directions or too-short trajectory. Record annotation, enabled rule, settings, clip start/end and every matching candidate. Inspect evidence and diagnostics; review independently before comparing system output.
- **Expected:** Expect positive candidate only with stable opposite motion; missing/conflicting direction must not invent counterflow. All candidates require human review; no automatic enforcement confirmation.
- **Record:** Clip/hash/run ID; event start/end and track; human label; prerequisite checklist; candidate or suppression reason; TP/FP/FN/UNKNOWN classification.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### DET-04P — Truck-Ban Violation — positive

- **Owner / priority:** Tester B + Tester C / P1
- **Prerequisites:** truck_ban_zone; applicable truck class; actual recording datetime and ban window; capability status: implemented. Owner supplies independently labelled footage.
- **Steps:** Use a separate TEST upload/run. Applicable truck enters zone during configured ban period. Record annotation, enabled rule, settings, clip start/end and every matching candidate. Inspect evidence and diagnostics; review independently before comparing system output.
- **Expected:** Expect time-based candidate only with trusted recording time. Missing time suppresses evaluation; upload clock must not substitute. All candidates require human review; no automatic enforcement confirmation.
- **Record:** Clip/hash/run ID; event start/end and track; human label; prerequisite checklist; candidate or suppression reason; TP/FP/FN/UNKNOWN classification.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### DET-04N — Truck-Ban Violation — legal control

- **Owner / priority:** Tester B + Tester C / P0
- **Prerequisites:** truck_ban_zone; applicable truck class; actual recording datetime and ban window; capability status: implemented. Owner supplies independently labelled footage.
- **Steps:** Use a separate TEST upload/run. Same class enters outside ban period; non-applicable class as owner-defined control. Record annotation, enabled rule, settings, clip start/end and every matching candidate. Inspect evidence and diagnostics; review independently before comparing system output.
- **Expected:** Expect time-based candidate only with trusted recording time. Missing time suppresses evaluation; upload clock must not substitute. All candidates require human review; no automatic enforcement confirmation.
- **Record:** Clip/hash/run ID; event start/end and track; human label; prerequisite checklist; candidate or suppression reason; TP/FP/FN/UNKNOWN classification.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### DET-04U — Truck-Ban Violation — unknown context

- **Owner / priority:** Tester B + Tester C / P0
- **Prerequisites:** truck_ban_zone; applicable truck class; actual recording datetime and ban window; capability status: implemented. Owner supplies independently labelled footage.
- **Steps:** Use a separate TEST upload/run. Remove actual recording datetime; record upload time separately. Record annotation, enabled rule, settings, clip start/end and every matching candidate. Inspect evidence and diagnostics; review independently before comparing system output.
- **Expected:** Expect time-based candidate only with trusted recording time. Missing time suppresses evaluation; upload clock must not substitute. All candidates require human review; no automatic enforcement confirmation.
- **Record:** Clip/hash/run ID; event start/end and track; human label; prerequisite checklist; candidate or suppression reason; TP/FP/FN/UNKNOWN classification.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### DET-05P — No Helmet — positive

- **Owner / priority:** Tester B + Tester C / P1
- **Prerequisites:** rider/head clearly visible in human-labelled footage; record class/capability diagnostics; capability status: blocked automatic path. Owner supplies independently labelled footage.
- **Steps:** Use a separate TEST upload/run. Independently labelled uncovered rider head. Record annotation, enabled rule, settings, clip start/end and every matching candidate. Inspect evidence and diagnostics; review independently before comparing system output.
- **Expected:** Current automatic rule is deliberately blocked. Report positive automation BLOCKED; safety check passes only if missing helmet detections are not treated as proof. Never call this successful no-helmet accuracy. All candidates require human review; no automatic enforcement confirmation.
- **Record:** Clip/hash/run ID; event start/end and track; human label; prerequisite checklist; candidate or suppression reason; TP/FP/FN/UNKNOWN classification.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### DET-05N — No Helmet — legal control

- **Owner / priority:** Tester B + Tester C / P0
- **Prerequisites:** rider/head clearly visible in human-labelled footage; record class/capability diagnostics; capability status: blocked automatic path. Owner supplies independently labelled footage.
- **Steps:** Use a separate TEST upload/run. Clearly acceptable helmeted rider. Record annotation, enabled rule, settings, clip start/end and every matching candidate. Inspect evidence and diagnostics; review independently before comparing system output.
- **Expected:** Current automatic rule is deliberately blocked. Report positive automation BLOCKED; safety check passes only if missing helmet detections are not treated as proof. Never call this successful no-helmet accuracy. All candidates require human review; no automatic enforcement confirmation.
- **Record:** Clip/hash/run ID; event start/end and track; human label; prerequisite checklist; candidate or suppression reason; TP/FP/FN/UNKNOWN classification.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### DET-05U — No Helmet — unknown context

- **Owner / priority:** Tester B + Tester C / P0
- **Prerequisites:** rider/head clearly visible in human-labelled footage; record class/capability diagnostics; capability status: blocked automatic path. Owner supplies independently labelled footage.
- **Steps:** Use a separate TEST upload/run. Head occluded or helmet detection absent without positive uncovered-head evidence. Record annotation, enabled rule, settings, clip start/end and every matching candidate. Inspect evidence and diagnostics; review independently before comparing system output.
- **Expected:** Current automatic rule is deliberately blocked. Report positive automation BLOCKED; safety check passes only if missing helmet detections are not treated as proof. Never call this successful no-helmet accuracy. All candidates require human review; no automatic enforcement confirmation.
- **Record:** Clip/hash/run ID; event start/end and track; human label; prerequisite checklist; candidate or suppression reason; TP/FP/FN/UNKNOWN classification.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### DET-06P — No Side Mirror — positive

- **Owner / priority:** Tester B + Tester C / P1
- **Prerequisites:** motorcycle mirror mounts visible; saved capability diagnostics; capability status: partial; visibility prerequisite absent in normal pipeline. Owner supplies independently labelled footage.
- **Steps:** Use a separate TEST upload/run. Human-labelled missing mirrors with clear mounting regions. Record annotation, enabled rule, settings, clip start/end and every matching candidate. Inspect evidence and diagnostics; review independently before comparing system output.
- **Expected:** Without the required visibility producer, positive automation is BLOCKED. No mirror box alone must not establish missing mirrors. All candidates require human review; no automatic enforcement confirmation.
- **Record:** Clip/hash/run ID; event start/end and track; human label; prerequisite checklist; candidate or suppression reason; TP/FP/FN/UNKNOWN classification.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### DET-06N — No Side Mirror — legal control

- **Owner / priority:** Tester B + Tester C / P0
- **Prerequisites:** motorcycle mirror mounts visible; saved capability diagnostics; capability status: partial; visibility prerequisite absent in normal pipeline. Owner supplies independently labelled footage.
- **Steps:** Use a separate TEST upload/run. Visible mirrors on motorcycle. Record annotation, enabled rule, settings, clip start/end and every matching candidate. Inspect evidence and diagnostics; review independently before comparing system output.
- **Expected:** Without the required visibility producer, positive automation is BLOCKED. No mirror box alone must not establish missing mirrors. All candidates require human review; no automatic enforcement confirmation.
- **Record:** Clip/hash/run ID; event start/end and track; human label; prerequisite checklist; candidate or suppression reason; TP/FP/FN/UNKNOWN classification.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### DET-06U — No Side Mirror — unknown context

- **Owner / priority:** Tester B + Tester C / P0
- **Prerequisites:** motorcycle mirror mounts visible; saved capability diagnostics; capability status: partial; visibility prerequisite absent in normal pipeline. Owner supplies independently labelled footage.
- **Steps:** Use a separate TEST upload/run. Mounts occluded/out of frame; no side_mirror detection alone. Record annotation, enabled rule, settings, clip start/end and every matching candidate. Inspect evidence and diagnostics; review independently before comparing system output.
- **Expected:** Without the required visibility producer, positive automation is BLOCKED. No mirror box alone must not establish missing mirrors. All candidates require human review; no automatic enforcement confirmation.
- **Record:** Clip/hash/run ID; event start/end and track; human label; prerequisite checklist; candidate or suppression reason; TP/FP/FN/UNKNOWN classification.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### DET-07P — Motorcycle Overloading — positive

- **Owner / priority:** Tester B + Tester C / P1
- **Prerequisites:** motorcycle/rider association supported; saved threshold; clear view; capability status: implemented. Owner supplies independently labelled footage.
- **Steps:** Use a separate TEST upload/run. More riders than configured allowed count associated with the same motorcycle across frames. Record annotation, enabled rule, settings, clip start/end and every matching candidate. Inspect evidence and diagnostics; review independently before comparing system output.
- **Expected:** Expect candidate only for supported persistent rider association/count; record individual rider boxes and association errors. All candidates require human review; no automatic enforcement confirmation.
- **Record:** Clip/hash/run ID; event start/end and track; human label; prerequisite checklist; candidate or suppression reason; TP/FP/FN/UNKNOWN classification.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### DET-07N — Motorcycle Overloading — legal control

- **Owner / priority:** Tester B + Tester C / P0
- **Prerequisites:** motorcycle/rider association supported; saved threshold; clear view; capability status: implemented. Owner supplies independently labelled footage.
- **Steps:** Use a separate TEST upload/run. Allowed rider count; nearby pedestrian is not riding. Record annotation, enabled rule, settings, clip start/end and every matching candidate. Inspect evidence and diagnostics; review independently before comparing system output.
- **Expected:** Expect candidate only for supported persistent rider association/count; record individual rider boxes and association errors. All candidates require human review; no automatic enforcement confirmation.
- **Record:** Clip/hash/run ID; event start/end and track; human label; prerequisite checklist; candidate or suppression reason; TP/FP/FN/UNKNOWN classification.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### DET-07U — Motorcycle Overloading — unknown context

- **Owner / priority:** Tester B + Tester C / P0
- **Prerequisites:** motorcycle/rider association supported; saved threshold; clear view; capability status: implemented. Owner supplies independently labelled footage.
- **Steps:** Use a separate TEST upload/run. Overlapping motorcycles, occluded riders or unrelated people near vehicle. Record annotation, enabled rule, settings, clip start/end and every matching candidate. Inspect evidence and diagnostics; review independently before comparing system output.
- **Expected:** Expect candidate only for supported persistent rider association/count; record individual rider boxes and association errors. All candidates require human review; no automatic enforcement confirmation.
- **Record:** Clip/hash/run ID; event start/end and track; human label; prerequisite checklist; candidate or suppression reason; TP/FP/FN/UNKNOWN classification.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### DET-08P — Disregarding Traffic Sign — positive

- **Owner / priority:** Tester B + Tester C / P1
- **Prerequisites:** supported sign annotation; associated lane and valid maneuver threshold/direction; capability status: partial. Owner supplies independently labelled footage.
- **Steps:** Use a separate TEST upload/run. Vehicle crosses a configured prohibited threshold in prohibited direction. Record annotation, enabled rule, settings, clip start/end and every matching candidate. Inspect evidence and diagnostics; review independently before comparing system output.
- **Expected:** Candidate only for supported fully configured maneuver. Counting line alone and ambiguous/missing context must not produce a sign-violation claim. All candidates require human review; no automatic enforcement confirmation.
- **Record:** Clip/hash/run ID; event start/end and track; human label; prerequisite checklist; candidate or suppression reason; TP/FP/FN/UNKNOWN classification.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### DET-08N — Disregarding Traffic Sign — legal control

- **Owner / priority:** Tester B + Tester C / P0
- **Prerequisites:** supported sign annotation; associated lane and valid maneuver threshold/direction; capability status: partial. Owner supplies independently labelled footage.
- **Steps:** Use a separate TEST upload/run. Legal direction/allowed maneuver; a counting line without sign threshold. Record annotation, enabled rule, settings, clip start/end and every matching candidate. Inspect evidence and diagnostics; review independently before comparing system output.
- **Expected:** Candidate only for supported fully configured maneuver. Counting line alone and ambiguous/missing context must not produce a sign-violation claim. All candidates require human review; no automatic enforcement confirmation.
- **Record:** Clip/hash/run ID; event start/end and track; human label; prerequisite checklist; candidate or suppression reason; TP/FP/FN/UNKNOWN classification.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### DET-08U — Disregarding Traffic Sign — unknown context

- **Owner / priority:** Tester B + Tester C / P0
- **Prerequisites:** supported sign annotation; associated lane and valid maneuver threshold/direction; capability status: partial. Owner supplies independently labelled footage.
- **Steps:** Use a separate TEST upload/run. Remove supported sign context or create ambiguous threshold association. Record annotation, enabled rule, settings, clip start/end and every matching candidate. Inspect evidence and diagnostics; review independently before comparing system output.
- **Expected:** Candidate only for supported fully configured maneuver. Counting line alone and ambiguous/missing context must not produce a sign-violation claim. All candidates require human review; no automatic enforcement confirmation.
- **Record:** Clip/hash/run ID; event start/end and track; human label; prerequisite checklist; candidate or suppression reason; TP/FP/FN/UNKNOWN classification.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### DET-09P — Failure to Follow Road/Pavement Markings — positive

- **Owner / priority:** Tester B + Tester C / P1
- **Prerequisites:** owner-defined supported marking geometry and associated lane/maneuver; capability status: partial. Owner supplies independently labelled footage.
- **Steps:** Use a separate TEST upload/run. Vehicle visibly performs the supported prohibited crossing/maneuver. Record annotation, enabled rule, settings, clip start/end and every matching candidate. Inspect evidence and diagnostics; review independently before comparing system output.
- **Expected:** Test supported configured behavior; unimplemented marking types are BLOCKED. No markings/context must not be treated as a violation. All candidates require human review; no automatic enforcement confirmation.
- **Record:** Clip/hash/run ID; event start/end and track; human label; prerequisite checklist; candidate or suppression reason; TP/FP/FN/UNKNOWN classification.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### DET-09N — Failure to Follow Road/Pavement Markings — legal control

- **Owner / priority:** Tester B + Tester C / P0
- **Prerequisites:** owner-defined supported marking geometry and associated lane/maneuver; capability status: partial. Owner supplies independently labelled footage.
- **Steps:** Use a separate TEST upload/run. Vehicle follows markings legally without prohibited crossing. Record annotation, enabled rule, settings, clip start/end and every matching candidate. Inspect evidence and diagnostics; review independently before comparing system output.
- **Expected:** Test supported configured behavior; unimplemented marking types are BLOCKED. No markings/context must not be treated as a violation. All candidates require human review; no automatic enforcement confirmation.
- **Record:** Clip/hash/run ID; event start/end and track; human label; prerequisite checklist; candidate or suppression reason; TP/FP/FN/UNKNOWN classification.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### DET-09U — Failure to Follow Road/Pavement Markings — unknown context

- **Owner / priority:** Tester B + Tester C / P0
- **Prerequisites:** owner-defined supported marking geometry and associated lane/maneuver; capability status: partial. Owner supplies independently labelled footage.
- **Steps:** Use a separate TEST upload/run. Missing marking geometry or visually ambiguous crossing. Record annotation, enabled rule, settings, clip start/end and every matching candidate. Inspect evidence and diagnostics; review independently before comparing system output.
- **Expected:** Test supported configured behavior; unimplemented marking types are BLOCKED. No markings/context must not be treated as a violation. All candidates require human review; no automatic enforcement confirmation.
- **Record:** Clip/hash/run ID; event start/end and track; human label; prerequisite checklist; candidate or suppression reason; TP/FP/FN/UNKNOWN classification.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### DET-10P — Illegal Terminal — positive

- **Owner / priority:** Tester B + Tester C / P1
- **Prerequisites:** loading/unloading zone; supported public-utility context; explicit activity evidence; capability status: partial. Owner supplies independently labelled footage.
- **Steps:** Use a separate TEST upload/run. Supported public-utility vehicle performs prohibited terminal/loading activity with required contextual evidence. Record annotation, enabled rule, settings, clip start/end and every matching candidate. Inspect evidence and diagnostics; review independently before comparing system output.
- **Expected:** Parking alone must not imply terminal activity. Positive automation needs explicit prerequisites; record absent pipeline support as BLOCKED. All candidates require human review; no automatic enforcement confirmation.
- **Record:** Clip/hash/run ID; event start/end and track; human label; prerequisite checklist; candidate or suppression reason; TP/FP/FN/UNKNOWN classification.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### DET-10N — Illegal Terminal — legal control

- **Owner / priority:** Tester B + Tester C / P0
- **Prerequisites:** loading/unloading zone; supported public-utility context; explicit activity evidence; capability status: partial. Owner supplies independently labelled footage.
- **Steps:** Use a separate TEST upload/run. Ordinary stationary vehicle without terminal activity; legal activity control. Record annotation, enabled rule, settings, clip start/end and every matching candidate. Inspect evidence and diagnostics; review independently before comparing system output.
- **Expected:** Parking alone must not imply terminal activity. Positive automation needs explicit prerequisites; record absent pipeline support as BLOCKED. All candidates require human review; no automatic enforcement confirmation.
- **Record:** Clip/hash/run ID; event start/end and track; human label; prerequisite checklist; candidate or suppression reason; TP/FP/FN/UNKNOWN classification.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### DET-10U — Illegal Terminal — unknown context

- **Owner / priority:** Tester B + Tester C / P0
- **Prerequisites:** loading/unloading zone; supported public-utility context; explicit activity evidence; capability status: partial. Owner supplies independently labelled footage.
- **Steps:** Use a separate TEST upload/run. No loading/unloading zone, activity unavailable, or van without reliable public/for-hire context. Record annotation, enabled rule, settings, clip start/end and every matching candidate. Inspect evidence and diagnostics; review independently before comparing system output.
- **Expected:** Parking alone must not imply terminal activity. Positive automation needs explicit prerequisites; record absent pipeline support as BLOCKED. All candidates require human review; no automatic enforcement confirmation.
- **Record:** Clip/hash/run ID; event start/end and track; human label; prerequisite checklist; candidate or suppression reason; TP/FP/FN/UNKNOWN classification.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### DET-11P — Unauthorized Passenger in Applicable Truck/Pickup Cargo Area — positive

- **Owner / priority:** Tester B + Tester C / P1
- **Prerequisites:** applicable truck/pickup; person clearly in cargo region across frames; owner-approved uploaded clip; capability status: partial; uploaded-video evidence scope. Owner supplies independently labelled footage.
- **Steps:** Use a separate TEST upload/run. Passenger clearly occupies applicable cargo area with multi-frame evidence. Record annotation, enabled rule, settings, clip start/end and every matching candidate. Inspect evidence and diagnostics; review independently before comparing system output.
- **Expected:** Uploaded candidates remain manual-review evidence. Live candidate suppression is an existing scope limitation; do not equate person near truck with cargo passenger. All candidates require human review; no automatic enforcement confirmation.
- **Record:** Clip/hash/run ID; event start/end and track; human label; prerequisite checklist; candidate or suppression reason; TP/FP/FN/UNKNOWN classification.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### DET-11N — Unauthorized Passenger in Applicable Truck/Pickup Cargo Area — legal control

- **Owner / priority:** Tester B + Tester C / P0
- **Prerequisites:** applicable truck/pickup; person clearly in cargo region across frames; owner-approved uploaded clip; capability status: partial; uploaded-video evidence scope. Owner supplies independently labelled footage.
- **Steps:** Use a separate TEST upload/run. Person stands beside vehicle; driver in cab; empty cargo area. Record annotation, enabled rule, settings, clip start/end and every matching candidate. Inspect evidence and diagnostics; review independently before comparing system output.
- **Expected:** Uploaded candidates remain manual-review evidence. Live candidate suppression is an existing scope limitation; do not equate person near truck with cargo passenger. All candidates require human review; no automatic enforcement confirmation.
- **Record:** Clip/hash/run ID; event start/end and track; human label; prerequisite checklist; candidate or suppression reason; TP/FP/FN/UNKNOWN classification.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### DET-11U — Unauthorized Passenger in Applicable Truck/Pickup Cargo Area — unknown context

- **Owner / priority:** Tester B + Tester C / P0
- **Prerequisites:** applicable truck/pickup; person clearly in cargo region across frames; owner-approved uploaded clip; capability status: partial; uploaded-video evidence scope. Owner supplies independently labelled footage.
- **Steps:** Use a separate TEST upload/run. Cargo area occluded or insufficient frames; compare live scope separately. Record annotation, enabled rule, settings, clip start/end and every matching candidate. Inspect evidence and diagnostics; review independently before comparing system output.
- **Expected:** Uploaded candidates remain manual-review evidence. Live candidate suppression is an existing scope limitation; do not equate person near truck with cargo passenger. All candidates require human review; no automatic enforcement confirmation.
- **Record:** Clip/hash/run ID; event start/end and track; human label; prerequisite checklist; candidate or suppression reason; TP/FP/FN/UNKNOWN classification.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### DET-12P — Substandard / Nut-Shell Helmet — positive

- **Owner / priority:** Tester B + Tester C / P1
- **Prerequisites:** supported helmet/rider classes; detail workflow only if explicitly enabled; capability status: partial; model-dependent. Owner supplies independently labelled footage.
- **Steps:** Use a separate TEST upload/run. Visible nut-shell helmet positively detected/associated with rider. Record annotation, enabled rule, settings, clip start/end and every matching candidate. Inspect evidence and diagnostics; review independently before comparing system output.
- **Expected:** Keep category distinct from No Helmet. Insufficient helmet evidence must remain unknown; optional detail results cannot auto-confirm a case. All candidates require human review; no automatic enforcement confirmation.
- **Record:** Clip/hash/run ID; event start/end and track; human label; prerequisite checklist; candidate or suppression reason; TP/FP/FN/UNKNOWN classification.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### DET-12N — Substandard / Nut-Shell Helmet — legal control

- **Owner / priority:** Tester B + Tester C / P0
- **Prerequisites:** supported helmet/rider classes; detail workflow only if explicitly enabled; capability status: partial; model-dependent. Owner supplies independently labelled footage.
- **Steps:** Use a separate TEST upload/run. Clearly acceptable helmet on rider. Record annotation, enabled rule, settings, clip start/end and every matching candidate. Inspect evidence and diagnostics; review independently before comparing system output.
- **Expected:** Keep category distinct from No Helmet. Insufficient helmet evidence must remain unknown; optional detail results cannot auto-confirm a case. All candidates require human review; no automatic enforcement confirmation.
- **Record:** Clip/hash/run ID; event start/end and track; human label; prerequisite checklist; candidate or suppression reason; TP/FP/FN/UNKNOWN classification.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

### DET-12U — Substandard / Nut-Shell Helmet — unknown context

- **Owner / priority:** Tester B + Tester C / P0
- **Prerequisites:** supported helmet/rider classes; detail workflow only if explicitly enabled; capability status: partial; model-dependent. Owner supplies independently labelled footage.
- **Steps:** Use a separate TEST upload/run. Helmet too small/occluded or absent detection. Record annotation, enabled rule, settings, clip start/end and every matching candidate. Inspect evidence and diagnostics; review independently before comparing system output.
- **Expected:** Keep category distinct from No Helmet. Insufficient helmet evidence must remain unknown; optional detail results cannot auto-confirm a case. All candidates require human review; no automatic enforcement confirmation.
- **Record:** Clip/hash/run ID; event start/end and track; human label; prerequisite checklist; candidate or suppression reason; TP/FP/FN/UNKNOWN classification.
- [ ] Executed and result recorded with evidence, or explicitly blocked with owner/next action.

## Source basis

Prepared from current app.py routes; core/auth.py roles/bootstrap; templates/settings.html, live_monitor.html and reports.html; core/detection_config.py canonical roster/status; core/model_capability.py prerequisites; core/review_decision_service.py dispositions; core/reports.py export cap; and the account-access amendment reviewed in this chat. The older team reference provided context; current source takes precedence. Recheck expectations if the build changes.
