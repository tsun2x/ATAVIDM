# TAVIDM run and bug records

Copy this form for each execution that needs detail. Do not erase a failed execution during a retest.

## Detailed execution

- Execution_ID / Test_ID:
- Tester / reviewer:
- Test build commit + relevant source hashes:
- Test server URL (no credentials):
- Date/time/timezone:
- Device, OS, browser/version, viewport:
- Account username + role + relevant explicit grants (NO passwords):
- Prerequisites checked:
- Input fixture ID, filename/hash, duration/FPS; camera ID if live:
- Actual recording time/timezone or UNKNOWN:
- Video/run/review/case IDs:
- Baseline settings/annotations:
- Before state and counts:
- Exact steps performed, in order:
  1.
  2.
  3.
- Expected result (copy scenario expectation):
- Actual result (values, IDs, state, count, wording; not “works”):
- Persistence check after reload/reopen:
- Status: PASS / FAIL / BLOCKED / LIMITATION / NOT_RUN
- Error text/HTTP status, if any:
- Timing: upload / queue wait / active processing / refresh / recovery:
- Evidence filenames and what EACH proves:
- Required subchecks not completed:
- Restoration/cleanup performed on disposable data:
- Bug ID or blocker and responsible person:
- Coordinator sign-off:

## Bug report

- Bug_ID: BUG-YYYYMMDD-initials-##
- Short title: action + wrong behavior + impact
- Severity: Critical / High / Medium / Low
- Related Test_ID and Execution_ID:
- Build/version + device/browser:
- Preconditions and relevant permissions:
- Fixture/hash and exact video/run/item/case IDs:
- Reproduction steps (another person must be able to follow):
  1.
  2.
  3.
- Expected:
- Actual:
- Reproduction frequency: X failures / Y attempts
- Started occurring after / conditions where it does not occur:
- Full exact error, redacted:
- Data/authority impact; related records changed unexpectedly:
- Screenshots/recording/network evidence:
- Temporary workaround observed, if any:
- Retest execution IDs/results; do not overwrite original report:
- Owner / status / coordinator decision:

## Example of a sufficiently specific result (illustrative, not an executed test)

Execution_ID: 20261010-A-ACC04-01
Test_ID: ACC-04
Input/account: TEST_officer_2, Viewer, account ID 17 on disposable deployment
Action: clicked Grant confirm_case, then attest_print while first request pending
Expected: one active write; all controls disabled until saved access reload
Actual example: second click sent no PUT; six controls disabled. First PUT returned 200; following GET returned confirm_case granted. After controls unlocked, attest_print PUT returned 200; following GET and reopening showed both rows “Explicit grant Yes / Effective Allowed / Revoke grant.”
Evidence: execution recording + redacted request order + reopened panel screenshot
Status: PASS only if those observations actually occurred.

Bad result: “ACC-04 passed, everything okay.” Reject this as incomplete.

## Final round summary

- Round/date/build/source hashes:
- Tester names and assignments:
- Total scenarios planned / executed / PASS / FAIL / BLOCKED / LIMITATION / NOT_RUN:
- Outstanding P0 IDs:
- Critical/High/Medium/Low bugs (IDs):
- Full upload-to-report example IDs and evidence:
- Rule-by-rule TP/FP/FN/duplicate/UNKNOWN/BLOCKED, precision/recall denominators and sample size:
- Ten-class sample coverage and errors:
- Footage conditions/locations not covered:
- Performance ranges and machine/concurrency context:
- Optional OCR/detail/live paths enabled, blocked or untested:
- Existing limitations affecting the intended demo/deployment:
- Evidence folder and result files:
- Recommendation: accept for stated scope / fix and retest / insufficient evidence
- Coordinator approval and exact scope accepted:
