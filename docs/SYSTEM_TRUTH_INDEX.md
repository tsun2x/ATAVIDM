# TAVIDM System Truth Index

Concise map of where authoritative TAVIDM knowledge lives. This file identifies sources; it does not duplicate their contents.

**Current state reviewed 2026-10-25:** [project.md](../project.md) records the current phase, local checkpoint identities, fresh verification, and remaining validation gates. Phase assessment: **Phase 2 — model integration, evaluation, and system validation; pre-field-pilot**.

## Hierarchy

```text
Code and tests              = implemented truth
Specifications + config     = approved design truth
Development logs            = history
Mnemosyne                   = institutional memory
```

When documentation and implementation conflict, **report and reconcile** the conflict. Do not silently choose one side.

## Authoritative sources

| Concern | Authoritative location |
|---|---|
| Implemented behavior | Current source under `core/`, `app.py`, `database/`, and automated tests in `tests/` |
| Machine-readable training labels | `config/training/class_schema.json` |
| Vehicle-classification decisions | `docs/VEHICLE_CLASSIFICATION_SPECIFICATION.md` |
| Canonical violation rules (12 types) | `docs/VIOLATION_ENGINE_SPECIFICATION.md` |
| Annotation practice | `docs/ANNOTATION_GUIDE_PHASE_2.md` |
| Collection, privacy, retention | `docs/LOCAL_DATA_GOVERNANCE_PHASE_2.md` |
| Training-readiness gates | `docs/PHASE_2_AI_TRAINING_READINESS.md` |
| Chronological development history | `docs/development_logs/` |
| Durable cross-session memory | Mnemosyne (institutional memory; not a change log) |

## Current implementation and approved next revision

- Current implemented schema **v1.3.0**: **10** vehicle detector classes (`autorickshaw`; single `van`; SUV/crossover consolidated into `car`; no canonical `uv_express_van` / `piaggio`); **15** object + **9** scene = **24** labels.
- Legacy compatibility: `uv_express_van` normalizes to `van`; ambiguous `piaggio` stays UNCERTAIN until body-form review (`sidecar`→`tricycle`, `integrated`→`autorickshaw`). Migration manifest: `config/training/label_migration_1_1_to_1_2.json`.
- Fresh local checkpoint inspection (2026-10-25): the resolver selects `models/tavidm_yolov8m.pt`, whose ordered **15-object** map matches schema v1.3.0. A lower-priority `models/best.pt` also exists and has the seven-class baseline label set (`bicycle`, `bus`, `car`, `jeepney`, `motorcycle`, `tricycle`, `truck`). The separate `models/motorcycle_detail_best.pt` has three attributes (`helmet_nut_shell`, `helmet_acceptable`, `side_mirror`). See `project.md` for hashes and limits. The weights are local, untracked artifacts; class-map loading does not prove field accuracy or operational activation of the separate detail model.
- Fixed-camera pavement markings still use operator-saved templates in the design direction; scene marking YOLO classes remain in the pilot schema until a separate markings migration.
- **12** canonical traffic violations (unchanged by the vehicle-class revision)
- Current registry: **4 implemented / 8 partial**. Automatic No Helmet remains blocked pending an approved positive uncovered-head source; mirror and cargo absence claims remain gated by missing production visibility evidence. A registered rule is not a field-validated enforcement capability.
- The recorded training entry gate still returns `ready=false`; legal mappings remain `unverified` / `flag_only` in checked-in configuration. See `project.md` for the distinction between local integration and acceptance readiness.
- Violation-engine remediation (2026-08-24): dual confidence, state expiry, footprint membership, geometry profiles, temporal evidence, capability gate — see `docs/VIOLATION_ENGINE_SPECIFICATION.md` §43.

Vehicle collision / incident classification is a separate future requirement and does not alter the frozen vehicle or violation rosters.

## Agent operating rule

Inspect code and tests first. Load the relevant specification and `class_schema.json` before changing classification or violation behavior. Use development logs for history and Mnemosyne for durable decisions. Never invent unfinished legal thresholds.
