# Phase 2 AI Training Readiness

**Status:** Preparation active; full training blocked by the automated entry gate.

## Frozen pilot contract

The Phase 2 pilot uses YOLOv8m with ByteTrack on the RTX 3050 6 GB target. `config/training/class_schema.json` (schema_version **1.2.0**) is the versioned annotation contract. It freezes observable labels for the pilot without freezing unfinished legal or rule-engine details. A detection label is never itself a violation. The vehicle detector roster has **11** classes (`autorickshaw`; single `van`; no canonical `uv_express_van` / `piaggio`).

The schema freezes **12** vehicle detector classes (including separate `pickup_truck`), **17** object classes, and **9** scene classes (**26** pilot labels total). Philippine PUV types remain distinct. Helmet presence/type and mirror observability are separate attribute labels. STOP and speed-limit signs are excluded from the current scope. Unknown or occluded attributes must remain unknown rather than receiving forced negative labels. See `docs/SYSTEM_TRUTH_INDEX.md` for the source hierarchy.

## Dataset acceptance

Every source must be registered in `dataset/registry/datasets.json`. Acceptance requires a verified usage license, downloaded files and checksums, representative visual inspection, annotation QC, recorded class counts, and splits grouped by camera, location, source video, and time.

Philippine CCTV is authoritative for final training and evaluation. Philippine signs and pavement markings are required for final sign/marking work. Foreign sources may be used only for generic pretraining after label and license review.

## Local collection and annotation

Before collecting new footage, record authorization, purpose, retention duration, storage location, access roles, de-identification procedure, and deletion procedure. Collection should cover daylight, night, rain, shadows, congestion, occlusion, small/distant objects, and intended camera angles.

Create a reviewed gold set before bulk annotation. Review difficult labels independently, including PUV subtype, rider association, helmet form, mirror visibility, sign applicability, and pavement markings. Split entire source groups before frame extraction so adjacent frames cannot cross dataset boundaries.

## Pilot and entry gate

`config/training/pilot.json` contains conservative initial settings. The pilot must report per-class precision and recall, mAP50, mAP50-95, confusion matrix, small-object results, Philippine-domain results, GPU peak memory, and complete unseen-video tracking behavior. Numerical acceptance thresholds must be recorded after baseline review; they are not invented in advance.

Run `.\venv\Scripts\python.exe -m core.phase2_readiness`. The gate remains closed until dataset evidence, permission/privacy controls, the gold set, pilot training, and acceptance thresholds all pass.
