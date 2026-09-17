# TAVIDM Violation-Policy Implementation Checklist

## Overview

This document tracks the staged implementation of the approved violation-policy,
legal-mapping, plate-review, recurrence, and notice-printing features. Work is
performed against the repository at `D:\tavidm` on branch
`cursor/phase-1-foundation` (HEAD at start of this run:
`942156609093297c26767fee98da14ee087de0d8`).

**Interpreter:** `C:\Program Files\Python311\python.exe` (Python 3.11.9)

**Migrations:** `db.init_db()` / `_run_migrations` auto-apply 008/009 on app
import. Do **not** launch the operational app against `database/tavidm.db`
without an explicit migration approval. Tests use temporary `SQLITE_PATH` only.

## Preserved Constraints (NEVER VIOLATE)

- 12 canonical violation identifiers and their exact order.
- YOLOv8m + ByteTrack detection pipeline (no detector class/weight changes).
- SQLite schema preserved (additive migrations only).
- Flask monolith (no FastAPI, React, or microservices).
- Rule-based engine architecture.
- Role-based auth: admin / enforcer / viewer.
- Single-worker / single inference slot safeguard (RTX 3050 6GB).

---

## Ordered work queue (2026-09-07 continuation)

| Stage | Status | Exact files | Tests / evidence | Remaining activation gates |
|-------|--------|-------------|------------------|----------------------------|
| 1 Inspect + queue | **tested** | this checklist; git/branch/interpreter checks | Baseline recheck: 204 passed, 1 skipped | — |
| 2 Case identity + grouping | **tested** | `core/case_identity.py`, `core/case_review_service.py`, `database/sqlite_adapter.py` helpers | `tests/test_case_identity.py` (11) | Engine-time fusion during live detection still deferred (review/confirm path wired) |
| 3 Plate + event-time persistence | **tested** | `core/case_review_service.py`, adapter permission helpers | `tests/test_case_review_service.py` (14) | Real FastALPR install/config; CCTV timestamp OCR |
| 4 Recurrence + policy workflow | **tested** | `core/recurrence_policy.py`, `record_print_batch`, adapter | `tests/test_recurrence_policy.py` (9) | CTEU-approved lookback + `offense_suggestions_enabled`; verified legal categories |
| 5 Review screens + printing | **tested** (API + UI wired; browser deferred) | `app.py` routes, `static/js/violations.js`, `static/js/review_queue.js` | `tests/test_case_policy_e2e.py` Flask APIs | Browser validation on isolated instance; real print hardware |
| 6 Legal configuration | **blocked** (identity noted; mappings inactive) | `config/violation_legal_mappings.json` | Register titles for CO248/CO944 verified; Drive text inaccessible | Owner/CTEU approve any status promotions after full text review |
| 7 Compatibility / E2E / reports | **tested** | `core/reports.py`, E2E suite, migrations already additive | Focused suite **274 passed, 1 skipped** (follow-up repairs 2026-09-07) | Operational migration; broader suite; training/GPU not run |

Do not mark a stage complete when only library functions exist. Stages 2–5 now
include persistence/service/API wiring plus focused tests.

---

## Stage A — Baseline and Legal-Policy Layer

- [x] Inspect current code: detection_config.py, violation_config.py,
      violation_engine.py, schema.sql, sqlite_adapter.py, auth.py, app.py,
      tests/conftest.py, existing tests.
- [x] Baseline test pass: 291 passed (historical); 2026-09-07 recheck subset below.
- [x] Create `config/violation_legal_mappings.json`.
- [x] Create `core/violation_policy.py` (statuses, proposed/verified, fusion helpers).
- [x] Write Stage A tests.
- [x] Run Stage A focused tests.

## Stage B — Additive Persistence and Permissions

- [x] Migrations 008/009 + schema.sql + sqlite_adapter apply paths.
- [x] Policy propose/approve, case actions, permissions, confirm/print gates.
- [x] Inactive-user and dismissed-case blocking.
- [x] Focused persistence tests.
- [ ] Broader full-suite regression (deferred; safety-checked focused suites run).

## Stage C — Evidence, FastALPR, and Event Time

### Stage C1 — Standalone FastALPR adapter (mocked only)

- [x] `core/plate_processing.py` isolated adapter + mocked tests.
- [ ] FastALPR package install + local model configuration (**blocked**).
- [x] Application/service integration via injected fakes
      (`process_plate_for_evidence`, `verify_plate_identity`).
- [x] Human plate verification workflow + audit (`ACTION_PLATE_VERIFIED`).

### Stage C2 — Event-time helpers + persistence

- [x] `core/event_time.py` helpers + focused tests.
- [x] Persist via `persist_event_time_review` → `record_event_time` + provenance.
- [x] Authenticated API `/api/cases/<id>/event-time` (session actor only).
- [ ] CCTV timestamp OCR (**unavailable**; UI states this honestly).
- [x] Recurrence consumes confirmed event instants when present.

## Stage D — Recurrence and Printing

- [x] `core/recurrence_policy.py`:
      `find_recurrence_matches`, `evaluate_recurrence_eligibility`,
      `summarize_recurrence`, `persist_recurrence_evaluation`.
- [x] Default lookback 365; offense suggestions **disabled** unless approved
      policy sets `offense_suggestions_enabled`.
- [x] Unresolved boundaries: lower-bound inclusion, same-instant ordering,
      category-version equivalence, missing historical audit evidence.
- [x] `confirm_case`, `confirm_notice_printed`, `record_print_batch`.
- [x] Plate gate: `verified_readable` only.
- [x] Policy propose (admin) / approve (explicit `approve_policy` only).
- [x] Focused recurrence tests.

## Stage E — Integration and Handoff

- [x] Flask routes (additive): case get, plate, event-time, confirm-case,
      notice-printed, batch print, printable preview, policy propose/approve/reject.
- [x] `_review_to_ui` / `_violation_to_ui` extended with legal/plate/case fields.
- [x] Violations detail UI actions; review-queue legal status display.
- [x] Reports distinguish canonical behavior, official category, case outcome;
      Excel keeps full official wording; PDF wraps official category.
- [x] E2E isolated tests (`tests/test_case_policy_e2e.py`).
- [x] `git diff --check` (no whitespace errors; CRLF warnings only).
- [ ] Browser validation on isolated instance (**deferred**).
- [ ] Operational migration / deployment (**not performed**).

---

## Legal mapping status (still inactive)

| Canonical rule | Status | Notes |
|----------------|--------|-------|
| Illegal Parking | unverified | Proposed fusion wording only |
| Obstruction | unverified | Proposed “Obstruction of Traffic Flow” |
| Counterflow | unverified | Disregarding Traffic Signals applicability unresolved |
| Truck-Ban Violation | unverified | |
| No Helmet | unverified | |
| No Side Mirror | flag_only | ₱200 / Incomplete Accessories unresolved |
| Motorcycle Overloading | flag_only | |
| Disregarding Traffic Sign | unverified | |
| Failure to Follow Road/Pavement Markings | unverified | Counterflow overlap unresolved |
| Illegal Terminal | flag_only | |
| Unauthorized Passenger… Cargo Area | flag_only | Exceptions unresolved |
| Substandard / Nut-Shell Helmet | unverified | Applicability unresolved |

**Register (2026-09-07):** CO248 and CO944 titles/dates confirmed on
https://zamboangacity.gov.ph/regulatory/. Drive PDFs remain inaccessible for
text extraction. **No mapping promoted to verified.**

---

## Deferred / blocked activation gates

1. Real FastALPR install + local models + Philippine CCTV evaluation.
2. Operational DB migration approval (auto-migrate on startup remains a risk).
3. CTEU Head/Supervisor account grants for `approve_policy`.
4. Approved lookback / `offense_suggestions_enabled` activation.
5. Authoritative full-text legal verification and mapping promotions.
6. Browser UI validation on a separate test instance/DB.
7. Live citations, physical printing, payments.

---

## Latest focused evidence (2026-09-07)

```text
# Mixed-state switch UI repair
node tests/js/test_violation_switch_mixed_state.cjs
→ PASS (exit 0)
  Pre-fix reproduction: Mixed → Inactive after DOMContentLoaded
  Post-fix: Mixed survives; Mixed→on/off; untouched mixed save; binary/disabled OK

C:\Program Files\Python311\python.exe -m pytest
  tests/test_owner_corrections_grouping_settings_admin.py::TestGroupedSettings
  tests/test_upload_processing_ux.py::TestToggleSnapshots::test_switch_html_has_active_inactive_and_role
  tests/test_violation_config.py
  -q --tb=short
→ 19 passed (exit 0)

git diff --check (repair paths) → exit 0
```

Grouped-switch Mixed state preserved across shared `violation_switch.js`
DOMContentLoaded sync; settings.js coordinates via `TavidmViolationSwitch.sync`.

Browser UI pass: not performed. Operational DB / training / deps / git /
Mnemosyne untouched.
