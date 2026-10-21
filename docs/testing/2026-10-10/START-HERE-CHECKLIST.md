# TAVIDM: easy testing checklist

Start here. Follow the same steps an actual user would take. This checklist describes what to test; it does **not** mean the tests have already passed.

**Your job:** try the steps, compare what happens with the expected result, and record your result using [SIMPLE-RESULTS.md](SIMPLE-RESULTS.md). Use [ANNOTATION-GUIDE.md](ANNOTATION-GUIDE.md) when drawing on a video.

## Before you start

The group leader supplies the system link, your test account, and short sample videos. Open the supplied local-network link while connected to the same network. Use your own account. Ask the leader for permission to test Settings, accounts, cameras, or deletion using test data.

Prepare three clearly named MP4s: one normal traffic clip, one with a visible suspected violation, and one from a different camera view. Keep the originals. Use short clips first so you do not spend the session waiting.

Write your name and the date on your results. Choose **Works**, **Problem**, or **Couldn't test** for every assigned item. A checked box means you tried it and recorded the result, not automatically that it worked.

## Part 1 — Main user flow: one video from start to finish

### M01 — Sign in and open the system

- [ ] Open the supplied link and sign in with your test account.
- [ ] Open Dashboard, then Live Monitor.
- [ ] Check that text, menus, and buttons are readable on your device.

**Expected:** you reach the system without a blank page or error. The visible options match your account's access.

### M02 — Upload one video

- [ ] In Live Monitor, use **Browse Files** or drag one MP4 into Upload Video.
- [ ] Check that the selected filename is correct. Choose the Traffic Condition.
- [ ] Enter the actual Recording date/time if known; leave it blank if unknown.
- [ ] Click Upload. Watch the progress and wait for completion.

**Expected:** the upload completes, the correct video appears in the list, and you can continue to annotation. The file must fit the size limit shown on screen. Uploading is not the same as processing.

### M03 — Draw an annotation

- [ ] Choose **Create New Zone Annotation**, then **Continue to Editor**.
- [ ] Use the guide to draw only the areas needed for your clip. For example, draw a No Parking Zone around a real no-parking area.
- [ ] Move one point, delete one point, and add a new object to check the editing controls.
- [ ] Click **Save Annotation**, choose **Use for This Video Only**, then **Confirm Save**.

**Expected:** the drawing matches the road, saves successfully, and the video becomes ready for processing. Reopen the annotation: the shapes and their positions should still be there.

### M04 — Choose this video's violation rules

- [ ] Open the video's processing action.
- [ ] In **Confirm Processing Run**, note which rules are enabled, disabled, or unavailable.
- [ ] Select the rules relevant to your video. Take a screenshot before starting.

**Expected:** your choices are visible. If a rule is unavailable, the screen should explain why. Choosing a rule does not guarantee the video contains that violation.

### M05 — Process in the background

- [ ] Choose **Process in Background**, then **Start Processing**.
- [ ] Visit another page, return to Live Monitor, and check the video's status.
- [ ] Wait for completion and open the results.

**Expected:** the job continues while you navigate. The status moves through the queue/processing stages and ends with usable results or a clear failure message. The source filename remains correct.

### M06 — Process and watch

- [ ] Use another prepared test video. Choose **Process and Watch Live**.
- [ ] Check the displayed video, progress, and any detection overlays.
- [ ] Wait for completion and open the finished results.

**Expected:** you can watch processing and still get saved results. This is a viewing choice for an uploaded-video job; it is not a separate detection method or a camera connection.

### M07 — Check what the system found

- [ ] Open the processed video and inspect its summary and violations.
- [ ] Pick one result. Compare its image/clip, timestamp, vehicle, and violation name with the original video.
- [ ] Check one moment where traffic is behaving normally.
- [ ] Note any obvious event the system missed or any normal event it incorrectly flagged.

**Expected:** evidence belongs to this video and matches the displayed event. Record false alarms and missed events with the video time, such as `00:18`. Zero results alone does not prove the detector is correct.

### M08 — Review an event

- [ ] Open Review Queue with an authorized test account.
- [ ] Read the evidence before choosing a decision.
- [ ] On leader-provided test items, try: confirm the proposed violation, correct its violation type, mark No violation, and mark Insufficient evidence.
- [ ] Enter a useful reason, save, and reopen the item.

**Expected:** the saved decision and reason remain correct. Confirm only clear evidence. Use Insufficient evidence when the video cannot support a decision. If there are not enough sample items, record which decisions you couldn't test.

### M09 — Follow the case flow

- [ ] Open a leader-provided reviewed test case.
- [ ] If your account is allowed, confirm the case and open the print preview.
- [ ] Check the case details, violation, source evidence, and printable layout.
- [ ] Only record a notice as printed if you actually performed the agreed test printing step.

**Expected:** case information matches the reviewed evidence. Opening a preview or downloading a PDF should not by itself mean the notice was printed. If your account lacks access, it should be clearly blocked.

### M10 — Generate a report

- [ ] Open Reports. Enter a Report Name and a date range containing your test records.
- [ ] Filter by your video source or violation type. Generate a PDF, then an Excel report.
- [ ] Open both downloaded files. Check headings, dates, rows, and readability.
- [ ] Check Report History.

**Expected:** both files open, match the selected filters, and appear in history. If your chosen range has no records, the system should handle that clearly.

### M11 — Return later

- [ ] Refresh the page, sign out, and sign in again.
- [ ] Reopen your uploaded video, annotation, finished results, and saved review.

**Expected:** saved work is still available and linked to the correct video. Signing out should take you out of the protected system.

## Part 2 — Several videos / batch processing

**Current behavior:** the upload screen accepts **one video at a time**. You upload several videos separately, prepare each one, then process them together. A multi-file upload in one action is not currently provided.

### B01 — Prepare three videos

- [ ] Upload the three MP4s individually. Give each its correct annotation.
- [ ] Reuse a template only for the same camera view; draw a separate annotation for the different view.

**Expected:** three separate list entries, correct filenames, and correct annotations. No drawing from another view is accidentally applied.

### B02 — Process selected videos

- [ ] Select two ready videos using their checkboxes. Leave the third unselected.
- [ ] Click **Process Selected**. Watch their statuses until complete.

**Expected:** the selected videos are queued and processed. The unselected video is not started by this action. Results remain separate for each source.

### B03 — Select All and repeat-click check

- [ ] On another small test batch, try Select All for ready videos.
- [ ] Start processing, then check whether the same queued/processing videos can be submitted again.

**Expected:** only eligible videos are selected. Repeating an action should not create duplicate jobs. Read the accepted/rejected/already-queued message and record anything confusing.

### B04 — Unprepared video

- [ ] Leave one disposable uploaded video without a valid annotation.
- [ ] Check whether it can be included in processing.

**Expected:** an unready video is excluded or clearly rejected. It should not silently use another video's annotation. Batch processing does not show the single-video rule-selection popup; record any unclear rule behavior for the leader.

## Part 3 — Test every violation option

Do this with the leader's prepared clips and an account that can change the relevant settings. **Change one rule at a time.** Save/reopen settings where applicable, then start a new processing run. Old results will not disappear because you disabled a rule later.

For each row:

1. Check whether the rule is available and whether its explanation is readable.
2. If available, enable it and process the matching clip. Compare the suspected event with the original video.
3. Disable it and start a new run: that rule should not produce new candidates.
4. Try a normal/legal example when provided: it should not be falsely flagged.
5. Record what happened. If footage or a required capability is missing, choose Couldn't test and state why.

| Done | ID / violation | What to use and check |
|---|---|---|
| [ ] | V01 — Illegal Parking | A vehicle staying in a real No Parking Zone longer than the saved parking time. Compare with a vehicle just passing through. This is a stationary-time indicator for human review. |
| [ ] | V02 — Obstruction | A vehicle remaining in an Active Lane or Pedestrian Crossing beyond the applicable waiting time. Compare with traffic moving through normally. |
| [ ] | V03 — Counterflow | A clearly annotated lane with an allowed Flow arrow. Compare vehicles moving against the arrow with vehicles following it. |
| [ ] | V04 — Truck-Ban Violation | A truck in a Truck Ban Zone, with a known recording date/time. Compare recordings inside and outside the configured ban hours. Do not invent the recording time. |
| [ ] | V05 — No Helmet | Check the availability explanation. Current automatic detection is blocked pending approved evidence capability. A helmet that was not detected is not proof of No Helmet. Record unavailable behavior; do not expect automatic candidates from ordinary footage. |
| [ ] | V06 — No Side Mirror | Check the availability explanation. Ordinary video processing currently lacks the required mirror-visibility evidence. A hidden or undetected mirror is not proof that it is missing. |
| [ ] | V07 — Motorcycle Overloading | A clearly visible motorcycle/rider example and an ordinary motorcycle comparison. Check whether people belong to the same motorcycle; count uncertainty as unclear evidence. |
| [ ] | V08 — Disregarding Traffic Sign | Use a real No entry sign with a matching lane, entry threshold, and allowed flow. Compare prohibited entry with allowed movement. Other sign types can be drawn but are not automatically evaluated in this release. |
| [ ] | V09 — Failure to Follow Road/Pavement Markings | A vehicle crossing a supported solid marking from the prohibited side. Compare with driving alongside it or crossing from an allowed side. See the guide before choosing the side. |
| [ ] | V10 — Illegal Terminal | A relevant public-utility vehicle lingering in a No Loading/Unloading Zone with visible passenger activity. Add the Activity region. Compare with ordinary passing traffic; a stop alone does not prove terminal activity. |
| [ ] | V11 — Unauthorized Passenger in Applicable Truck/Pickup Cargo Area | An uploaded clip with a clearly visible person in the applicable cargo area. Inspect the review evidence; this is review-only support. Live automatic output is suppressed. |
| [ ] | V12 — Substandard / Nut-Shell Helmet | A clear supported nut-shell helmet example versus an ordinary helmet. Poor visibility is not proof. Results depend on available model evidence and require review. |

**Do not mark a detection test Works just because its switch turned on.** The switch check and the video result are separate observations. A model limitation may explain a missing result, but still record it for the leader.

## Part 4 — Other settings and functions

The main flow comes first. Split these between classmates afterwards. Use disposable test data for changes.

| Done | ID | Specific action | Expected result |
|---|---|---|---|
| [ ] | F01 — Upload mistakes | Try a non-MP4 file and a leader-provided broken MP4. Cancel an upload. | Clear rejection/failure/cancellation; no unusable video shown as successfully processed. |
| [ ] | F02 — Templates | Save a new named template, reuse it on the same view, edit a point, and reopen. Separately test Update Existing Template on a test template. | Correct shapes persist; the chosen save option affects the intended video/template. |
| [ ] | F03 — Rule defaults | Change one available default rule in Settings, save and refresh. Open a new single-video run and inspect defaults; change that run's choice. | Saved default persists. A choice made only for that run should not rewrite the global default. |
| [ ] | F04 — Waiting times | Test Parking Dwell, Obstruction, Crossing Block, and Loading Zone one at a time with clips lasting long enough. Write old/new values. | Values save and survive refresh; shorter/longer waiting rules behave consistently in new runs with the required evidence. Restore each original value. |
| [ ] | F05 — Other detection settings | Change Detection Confidence, Frame Skip, Stopping Dwell, Lane Flow Direction, and Counterflow Tolerance one at a time. Save, refresh, run a suitable clip, then restore. | Valid values persist; invalid values are rejected clearly. Record visible effects without assuming every change must produce a new violation. Structured lanes need their own flow arrows. |
| [ ] | F06 — Truck-ban schedule | Save test start/end times, refresh, and process known-time clips inside/outside the window. Restore the original schedule. | Saved hours persist; results follow recording time and the annotated Truck Ban Zone. |
| [ ] | F07 — Counting line | Draw a Vehicle counting line across one lane, attach it to that lane, and process vehicles crossing it. Manually count the same crossings. | Crossing results are available and can be compared with your count. A counting line alone should not produce a no-entry violation. |
| [ ] | F08 — Search and filters | In Violations, try one filter at a time, combine filters, then clear them. Open a result. | Shown records match the filters and evidence belongs to the chosen result. |
| [ ] | F09 — Dashboard and Analytics | Compare summaries for your selected dates with the related records; try a range with no records. | Numbers/labels are understandable and do not mix unrelated sources/ranges. Empty data is handled clearly. |
| [ ] | F10 — Account access | Leader uses Add User to create a test account. Sign in with it. Try the assigned role, grant/revoke one available power, and retry the action. | Account and saved access behave as configured. Unavailable powers explain their restriction. Ordinary users cannot grant themselves access. |
| [ ] | F11 — Camera | If a test camera is supplied, start a stream, inspect the picture, stop it, and start again. | Correct camera opens; stop/restart works or shows a useful error. No camera means Couldn't test. |
| [ ] | F12 — Remove results | On an agreed test video, use Remove Results. Reopen the video and annotation. | Processing results are removed; source and annotation remain available for a new run. |
| [ ] | F13 — Delete test video | Delete only the leader-approved disposable video. Refresh and search for it. | Correct video disappears; other videos remain intact. |
| [ ] | F14 — Two users | Two classmates use separate accounts to review the same test item. One saves first; the other then tries to save an older decision. | The second user sees a conflict/reload message instead of silently overwriting the first decision. |
| [ ] | F15 — Another device | Open the supplied LAN link on another computer/phone and complete upload-to-results if allowed. | Main controls remain readable and usable. Record small-screen or connection problems. |

## Finish and submit

- [ ] Every assigned item has Works / Problem / Couldn't test and a short actual result.
- [ ] Every problem names the video, time if relevant, steps, expected result, actual result, and screenshot.
- [ ] Restore settings you changed, or tell the leader exactly what is still changed.
- [ ] Send your results and screenshots together. Do not include passwords or camera credentials.

The longer [TEST-CHECKLIST.md](TEST-CHECKLIST.md) is an optional developer reference. Classmates can use this file and the short results form for their test session.
