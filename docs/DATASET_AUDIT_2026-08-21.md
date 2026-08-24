# Dataset Audit — 2026-08-21

## Verdict

No dataset is accepted for final training, validation, or evaluation. The researched public datasets remain candidate sources for generic pretraining only. The unrelated gameplay test media has been removed from the repository and is not registered as a dataset.

## Candidate findings

- **TT100K:** official source reachable; official page describes 100,000 Tencent Street View images, about 30,000 traffic-sign instances, bounding boxes and masks, and a CC BY-NC license. Domain is foreign, so it cannot define TAVIDM Philippine sign classes.
- **BDD100K:** official Berkeley data portal and project description were located. The files, click-through terms, annotations, and representative samples have not been obtained or inspected.
- **India Driving Dataset:** official source reachable; it describes 10,000 images, 34 classes, 182 driving sequences, front-facing vehicle cameras, and primarily 1080p Indian road scenes. Its terms, files, and labels still require review.
- **Mapillary Vistas:** the official download page requires login. Dataset-specific terms, files, and samples have not been verified, so the candidate remains blocked.

Official sources are recorded in `dataset/registry/datasets.json`. License fields remain `unverified` unless the dataset's authoritative page exposed sufficiently clear terms during this audit.

## Local-media inspection

The four MP4 files were readable but byte-identical: 1920×1080, 168 frames, approximately 34.19 FPS, and 4.91 seconds. The two extracted JPEGs were also byte-identical. Visual inspection showed first-person shooter gameplay rather than road traffic. The project owner confirmed that these were upload-acceptance test files, so the four videos, two derived frames, obsolete checksum manifest, and registry entry were removed on 2026-08-21.

## Blocking work

Acquire authorized Philippine CCTV footage, approve privacy and retention controls, audit public-dataset terms and downloaded files, create the gold annotation set, produce leakage-safe source-group splits, and run the bounded YOLOv8m pilot before setting any readiness control to passed.
