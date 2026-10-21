# TAVIDM: simple test results

Copy this into your own document or message. Fill it while testing. You do not need the larger CSV packet for this checklist.

**Tester:** __________  **Date:** __________  **Account role:** __________

**Device/browser:** __________  **Assigned items:** __________

## One short row for each checklist item

Use **Works**, **Problem**, or **Couldn't test**. Describe what actually happened; a tick or “done” is not enough. For a Works result, name the outcome, such as “All three selected videos finished and opened separate results.”

| Item | Video / sample used | Result | What actually happened | Screenshot filename |
|---|---|---|---|---|
| M02 | normal-road.mp4 | Works | Upload finished; correct filename appeared and annotation opened. | M02-upload.png |
| B02 | road-A.mp4, road-B.mp4 | Problem | A finished. B stayed queued for 8 minutes; no error shown. Unselected C stayed ready. | B02-queue.png |
| V04 | none supplied | Couldn't test | No clip with a known recording time was supplied. | — |
| YOUR ITEM | | | | |

Add more rows as needed. **Couldn't test is an honest result**, but say exactly what was missing: account access, camera, suitable footage, a disabled capability, or another prerequisite.

For each violation item, also write the results of **enabled**, **disabled/new run**, and **normal comparison**. Example: `Enabled: candidate at 00:18. Disabled: no new Counterflow candidate. Normal clip: no Counterflow candidate.` If one comparison was unavailable, state that.

## If something goes wrong, copy this small problem form

**Item:** __________

**Video filename and video time:** __________ (use “not applicable” for account/settings problems)

**What I clicked / entered:**

1. __________
2. __________
3. __________

**What I expected:** __________

**What actually happened:** __________ (copy the visible error if there is one)

**Screenshot:** __________

**Did it happen again when I tried once more?** Yes / No / Didn't retry because __________

### Example: missed event

**Item:** V03 — Counterflow

**Video/time:** wrong-way-test.mp4, 00:18–00:23

**Steps:** Drew one lane and its allowed upward flow arrow. Enabled Counterflow. Processed the clip and opened results.

**Expected:** the clearly visible downward-moving car would be flagged for review.

**Actual:** processing finished, but there was no Counterflow result for that car. The annotation still showed the upward arrow when reopened.

**Evidence:** V03-arrow.png and V03-results.png. Happened again on one new run.

This is an example of how to report a problem, not a finding from a completed test.

## Before sending your results

- [ ] All assigned items have a result and an actual observation.
- [ ] Problems include repeatable steps and the relevant screenshot.
- [ ] Detection problems include the video time so the leader can find the event.
- [ ] Screenshots have readable filenames, such as `M03-saved-annotation.png`.
- [ ] Changed settings were restored, or I listed the values still changed below.
- [ ] No passwords, sign-in tokens, or camera credentials are included.

**Settings still changed / questions for the leader:** __________

**Three biggest problems, if any:** __________
