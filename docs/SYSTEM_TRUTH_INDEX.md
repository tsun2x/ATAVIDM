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

## Frozen Phase 2 counts (see schema)

- **12** vehicle detector classes (includes separate `pickup_truck`)
- **17** object classes + **9** scene classes = **26** pilot labels
- **12** canonical traffic violations (unchanged by the vehicle-class freeze)

Vehicle collision / incident classification is a separate future requirement and does not alter the frozen vehicle or violation rosters.

## Agent operating rule

Inspect code and tests first. Load the relevant specification and `class_schema.json` before changing classification or violation behavior. Use development logs for history and Mnemosyne for durable decisions. Never invent unfinished legal thresholds.
