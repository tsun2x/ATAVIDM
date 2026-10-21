# Pickup Detail Offline Evaluation Contract

## Scope

`scripts/evaluate_pickup_detail.py` is an offline evidence collector for an explicitly selected local video and YOLO checkpoint. Its default mode reads container metadata only. The `--run-inference` mode is bounded by a frame limit, optional time range, and sampling interval. It compares full-frame detections with inference on contextual pickup crops that include space above the vehicle. It does not track riders, set `cargo_region_observable`, decide that a bed is empty, create cases, or establish legal status.

The checkpoint must expose contiguous `pickup_truck` and `person` class names. The tool never downloads weights. The crop uses source-frame pixels, clips at image edges, and preserves the source aspect ratio. Ultralytics prediction boxes are returned in the input image's coordinate system; crop detections are translated by the crop origin and clipped to the original frame.

## Deferred execution

Do not run inference on real footage until that experiment is explicitly authorized. Keep the referenced video local. Do not use annotated screenshots as training images. The output is a review aid, not training data or a violation record.

Example metadata-only command:

```powershell
python scripts/evaluate_pickup_detail.py --video "D:\tavidm\dataset\raw\VID_20261006_145443.mp4" --dry-run
```

Example bounded inference command for a separately authorized run:

```powershell
python scripts/evaluate_pickup_detail.py `
  --video "D:\tavidm\dataset\raw\VID_20261006_145443.mp4" `
  --run-inference `
  --weights "D:\path\to\explicit-compatible-person-and-pickup-checkpoint.pt" `
  --output "D:\tavidm\artifacts\pickup-detail-evaluation.json" `
  --start-sec 8 --end-sec 15 --max-frames 120 --sample-every-frames 2
```

The weight path is a placeholder. Supply an explicitly chosen local checkpoint and verify its person-class capability before running. Do not replace the main detector or infer that screenshots prove a missing-model cause.

## Evidence output

The JSON result records the source path, size, modification time and SHA-256; checkpoint path, SHA-256 and ordered class map; frame index and container/FPS-derived timestamp; source dimensions; raw detections and the separately marked confidence threshold; pickup box; crop bounds, clipping and dimensions; crop person detections; and mapped source-frame boxes. `body_extent_state` and `association_state` remain unknown because box geometry alone cannot establish visible feet or bed occupancy.

Compare full-frame and crop detections on the same selected frames. Review false detections on nearby pedestrians and cab occupants, partial bodies, clipped crops, pickup classification changes and camera scale. Record reviewer labels independently from detector output. Report people-level precision/recall only against a reviewed manifest with explicit observable-extent labels and denominators; report unknown/unreviewable cases separately.

## Annotation/evaluation manifest schema

One JSON object per reviewed frame/crop pair. These examples are synthetic and do not describe the referenced footage.

```json
{
  "manifest_version": "pickup-detail-v1",
  "source": {
    "source_id": "synthetic-video-a",
    "source_family": "synthetic-camera-a",
    "sha256": "synthetic-only",
    "split": "validation"
  },
  "frame": {
    "frame_index": 120,
    "timestamp_sec": 4.0,
    "source_size": [1280, 720]
  },
  "pickup": {
    "detection_id": "synthetic-det-17",
    "bbox_xyxy": [410, 280, 890, 560],
    "class_label": "pickup_truck",
    "confidence": 0.82
  },
  "crop": {
    "bbox_xyxy": [314, 70, 986, 602],
    "crop_size": [672, 532],
    "clipped_at_frame_edge": false
  },
  "people": [
    {
      "person_id": "synthetic-person-1",
      "bbox_xyxy_source": [600, 250, 670, 390],
      "observable_extent": "upper_body_only",
      "bed_association": "unknown",
      "reviewer_label": "uncertain"
    }
  ],
  "bed_visibility": "partially_obstructed",
  "reviewer_id": "synthetic-reviewer",
  "notes": "Synthetic example; no inference or legal conclusion."
}
```

Allowed reviewer states should include `occupied_observed`, `empty_observed`, `obstructed`, and `unknown`; person extent should distinguish full body, upper body only, clipped, and unknown. A missing person box is not an `empty_observed` label. Store any future annotation derivative separately from immutable originals, include provenance and hashes, and assign all frames from one source video/family to a single split.

## Production boundary

The production cargo rule now requires explicit `lower_body_visible: true` before using a person's box bottom as an anchor. Current production detections do not provide that field, so this change intentionally suppresses unsupported bed-rider candidates until a separately validated visibility producer and association contract exist. This offline tool does not provide that producer.
