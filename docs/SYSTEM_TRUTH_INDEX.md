# TAVIDM System Truth Index

Concise map of where authoritative TAVIDM knowledge lives. This file identifies sources; it does not duplicate their contents.

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

- Current implemented schema **v1.2.0**: **11** vehicle detector classes (`autorickshaw`; single `van`; no canonical `uv_express_van` / `piaggio`); **16** object + **9** scene = **25** labels.
- Legacy compatibility: `uv_express_van` normalizes to `van`; ambiguous `piaggio` stays UNCERTAIN until body-form review (`sidecar`→`tricycle`, `integrated`→`autorickshaw`). Migration manifest: `config/training/label_migration_1_1_to_1_2.json`.
- Seven-class baseline **label set** (`bicycle`, `bus`, `car`, `jeepney`, `motorcycle`, `tricycle`, `truck`) is a valid subset of the 11-class roster; missing production classes `suv_crossover`, `van`, `autorickshaw`, `pickup_truck` must be reported and fail-closed where required. The trained seven-class `best.pt` remains in an **external** training output directory and is **not** integrated into `D:\tavidm\models` (do not claim `models/best.pt` exists).
- Fixed-camera pavement markings still use operator-saved templates in the design direction; scene marking YOLO classes remain in the pilot schema until a separate markings migration.
- **12** canonical traffic violations (unchanged by the vehicle-class revision)
- Violation-engine remediation (2026-08-24): dual confidence, state expiry, footprint membership, geometry profiles, temporal evidence, capability gate — see `docs/VIOLATION_ENGINE_SPECIFICATION.md` §43.

Vehicle collision / incident classification is a separate future requirement and does not alter the frozen vehicle or violation rosters.

## Agent operating rule

Inspect code and tests first. Load the relevant specification and `class_schema.json` before changing classification or violation behavior. Use development logs for history and Mnemosyne for durable decisions. Never invent unfinished legal thresholds.
