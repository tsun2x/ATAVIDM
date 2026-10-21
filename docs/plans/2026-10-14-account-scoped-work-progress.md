# Execution progress

Plan: `docs/plans/2026-10-14-account-scoped-work.md`

Task 1: complete — Added scoped reads for uploaded videos, linked violations/reviews/detail candidates, review decisions, report history, and report sources. Report generation applies the authenticated uploader constraint independently from client filters. Existing SQLite ownership fields and video links were sufficient; no schema change or ownership backfill was needed.

Task 2: complete — Enforced owner access on video lists, direct video/media/annotation/process/history routes, mixed-owner bulk processing, and processing-job snapshots. The 97-test upload/processing/auth run had 96 passes and one unrelated pre-existing static assertion (`Extracting metadata` is absent from the already-dirty `static/js/upload.js`). Ownerless legacy videos remain admin-only for enforcers; route-test fixtures now explicitly assign their test uploader.

Task 3: complete — Scoped violation, review, and motorcycle-detail lists and guarded linked evidence, decisions, and case actions. Source-less violation/review records remain shared; video-derived motorcycle candidates require an accessible source video. A 95-test review/case regression run had 91 passes and four failures before the cross-account stale-review fixture was updated; the updated ownership/race/detail checks then passed (25 tests). Three pre-existing `test_motorcycle_detail_api.py` assertions remain inconsistent with the current dirty UI/confirmation contract: page heading wording, missing `human_confirmed` expected as 409 instead of 400, and positive confirm without that flag expected as 200 instead of 400.

Task 4: complete — Scoped dashboard, analytics, confidence bands, review totals, video totals, and upload/processing analytics. Admin account selectors are available on dashboard, analytics, violations, review queue, motorcycle detail, Live Monitor, and report generation. Account aggregate/upload analytics assertions passed.

Task 5: complete — Added My Reports / All Completed history selection, shared authorized report history/downloads, and an admin-only source-account selector. Enforcer report scope is session-derived and cannot be overridden by client uploader/video filters. Report, review, account-scope, and analytics focused set: 34 passed before the final added aggregate assertions; the account-scope suite then passed 7/7.

Final checks: `python -m compileall` passed for changed Python modules/tests; `git diff --check` reported no whitespace errors (only Git line-ending normalization notices). Changes remain unstaged and uncommitted. The checkout's other pre-existing dirty content is preserved.
