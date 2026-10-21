"""Bounded, offline pickup-person crop evaluation.

Dry-run is the default and reads video metadata only. Actual frame inference
requires explicit local weight/output paths and ``--run-inference``. The tool
does not label a cargo bed, infer an empty bed, or create production cases.
Ultralytics maps prediction boxes back to the supplied image dimensions; crop
coordinates are translated to source-frame coordinates below.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any, Sequence


def padded_pickup_crop(
    box_xyxy: Sequence[float],
    frame_width: int,
    frame_height: int,
    *,
    pad_above: float = 0.75,
    pad_sides: float = 0.20,
    pad_below: float = 0.15,
) -> dict[str, int | bool]:
    """Return clipped contextual crop coordinates around a pickup detection."""
    if len(box_xyxy) != 4 or frame_width <= 0 or frame_height <= 0:
        raise ValueError("A four-coordinate box and positive frame size are required.")
    x1, y1, x2, y2 = (float(value) for value in box_xyxy)
    if not all(math.isfinite(value) for value in (x1, y1, x2, y2)) or x2 <= x1 or y2 <= y1:
        raise ValueError("Pickup box coordinates must be finite and have positive area.")
    if min(pad_above, pad_sides, pad_below) < 0:
        raise ValueError("Crop padding ratios cannot be negative.")
    width, height = x2 - x1, y2 - y1
    raw = (
        math.floor(x1 - width * pad_sides),
        math.floor(y1 - height * pad_above),
        math.ceil(x2 + width * pad_sides),
        math.ceil(y2 + height * pad_below),
    )
    left = max(0, min(frame_width, raw[0]))
    top = max(0, min(frame_height, raw[1]))
    right = max(0, min(frame_width, raw[2]))
    bottom = max(0, min(frame_height, raw[3]))
    if right <= left or bottom <= top:
        raise ValueError("Pickup box does not overlap the source frame.")
    return {
        "x1": left,
        "y1": top,
        "x2": right,
        "y2": bottom,
        "clipped": (left, top, right, bottom) != raw,
    }


def map_crop_box_to_source(
    box_xyxy: Sequence[float], crop: dict[str, int | bool], frame_width: int, frame_height: int
) -> tuple[float, float, float, float]:
    """Translate a crop-sized prediction to clipped source-frame coordinates."""
    if len(box_xyxy) != 4:
        raise ValueError("A four-coordinate detection box is required.")
    left, top = int(crop["x1"]), int(crop["y1"])
    right, bottom = int(crop["x2"]), int(crop["y2"])
    x1, y1, x2, y2 = (float(value) for value in box_xyxy)
    return (
        max(0.0, min(float(frame_width), left + x1)),
        max(0.0, min(float(frame_height), top + y1)),
        max(0.0, min(float(frame_width), left + x2)),
        max(0.0, min(float(frame_height), top + y2)),
    )


def normalize_class_names(names: Any) -> tuple[str, ...]:
    """Read an ordered Ultralytics name map without silently reordering IDs."""
    if isinstance(names, dict):
        try:
            keys = sorted(names, key=lambda key: int(key))
            if [int(key) for key in keys] != list(range(len(keys))):
                raise ValueError
            values = tuple(str(names[key]).strip().lower() for key in keys)
        except (TypeError, ValueError):
            raise ValueError("Model class map must use contiguous integer IDs.") from None
    elif isinstance(names, (list, tuple)):
        values = tuple(str(name).strip().lower() for name in names)
    else:
        raise ValueError("Model does not expose a readable class map.")
    if not values or len(set(values)) != len(values):
        raise ValueError("Model class map is empty or contains duplicate names.")
    missing = {"person", "pickup_truck"} - set(values)
    if missing:
        raise ValueError("Model class map is missing: " + ", ".join(sorted(missing)))
    return values


def video_metadata(path: Path) -> dict[str, Any]:
    """Read container metadata without decoding frames."""
    import cv2

    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError("Video metadata could not be read.")
    try:
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    finally:
        capture.release()
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "size_bytes": stat.st_size,
        "modified_utc_ns": stat.st_mtime_ns,
        "width": width,
        "height": height,
        "reported_frame_count": frame_count,
        "reported_fps": fps if math.isfinite(fps) and fps > 0 else None,
        "duration_sec": round(frame_count / fps, 6) if frame_count > 0 and fps > 0 else None,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _result_detections(result: Any, names: Sequence[str], threshold: float) -> list[dict[str, Any]]:
    detections = []
    boxes = getattr(result, "boxes", None)
    for box in boxes or []:
        class_id = int(box.cls[0])
        if not 0 <= class_id < len(names):
            continue
        coords = tuple(float(value) for value in box.xyxy[0].tolist())
        confidence = float(box.conf[0])
        detections.append({
            "class_id": class_id,
            "class_name": names[class_id],
            "confidence": confidence,
            "accepted_at_threshold": confidence >= threshold,
            "bbox_xyxy": coords,
        })
    return detections


def run_inference(
    video_path: Path,
    weights_path: Path,
    output_path: Path,
    *,
    start_sec: float,
    end_sec: float | None,
    max_frames: int,
    sample_every_frames: int,
    confidence: float,
    image_size: int,
    device: str | None,
) -> dict[str, Any]:
    """Run matched full-frame and padded-crop inference on bounded frames."""
    import cv2
    from ultralytics import YOLO

    if not video_path.is_file() or not weights_path.is_file():
        raise ValueError("Video and model weights must be existing local files.")
    if max_frames < 1 or max_frames > 3000 or sample_every_frames < 1:
        raise ValueError("Frame limits must be bounded (1–3000) and sampling positive.")
    if (
        not math.isfinite(start_sec)
        or end_sec is None
        or not math.isfinite(end_sec)
        or start_sec < 0
        or end_sec <= start_sec
        or end_sec - start_sec > 120
    ):
        raise ValueError("Inference requires an explicit time range no longer than 120 seconds.")
    if not math.isfinite(confidence) or not 0 < confidence < 1 or image_size < 32:
        raise ValueError("Confidence and image size are invalid.")

    model = YOLO(str(weights_path.resolve()))
    names = normalize_class_names(model.names)
    pickup_class = names.index("pickup_truck")
    person_class = names.index("person")
    model_hash = _sha256(weights_path)
    source_hash = _sha256(video_path)
    metadata = video_metadata(video_path)
    duration = metadata.get("duration_sec")
    if duration is not None and (start_sec >= duration or end_sec > duration):
        raise ValueError("The explicit inference time range must fit within the video duration.")
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise ValueError("Video could not be opened for the explicitly requested evaluation.")
    if start_sec > 0 and not capture.set(cv2.CAP_PROP_POS_MSEC, start_sec * 1000.0):
        capture.release()
        raise ValueError("Video reader could not seek to the explicit inference start time.")

    frames = []
    frame_index = (
        max(-1, round(start_sec * float(metadata["reported_fps"])) - 1)
        if metadata["reported_fps"] else -1
    )
    evaluated = 0
    try:
        while evaluated < max_frames:
            ok, frame = capture.read()
            if not ok:
                break
            frame_index += 1
            reported_ms = float(capture.get(cv2.CAP_PROP_POS_MSEC) or 0.0)
            timestamp = reported_ms / 1000.0 if reported_ms > 0 else None
            timestamp_source = "container" if timestamp is not None else "unknown"
            if timestamp is None and metadata["reported_fps"]:
                timestamp = frame_index / float(metadata["reported_fps"])
                timestamp_source = "fps_derived"
            if timestamp is None:
                raise ValueError("Source timing is unknown; bounded inference requires timestamps or reported FPS.")
            if timestamp < start_sec:
                continue
            if timestamp > end_sec:
                break
            if frame_index % sample_every_frames:
                continue
            frame_height, frame_width = frame.shape[:2]
            common = {"conf": 0.001, "imgsz": image_size, "verbose": False}
            if device:
                common["device"] = device
            result = model.predict(source=frame, **common)[0]
            detections = _result_detections(result, names, confidence)
            accepted_pickups = [
                item for item in detections
                if item["class_id"] == pickup_class and item["accepted_at_threshold"]
            ]
            crops = []
            for pickup in accepted_pickups:
                crop = padded_pickup_crop(
                    pickup["bbox_xyxy"], frame_width, frame_height
                )
                crop_image = frame[int(crop["y1"]):int(crop["y2"]), int(crop["x1"]):int(crop["x2"])]
                crop_result = model.predict(source=crop_image, **common)[0]
                crop_detections = _result_detections(crop_result, names, confidence)
                people = []
                for item in crop_detections:
                    if item["class_id"] != person_class:
                        continue
                    item["source_bbox_xyxy"] = map_crop_box_to_source(
                        item["bbox_xyxy"], crop, frame_width, frame_height
                    )
                    # A box does not establish feet, bed occupancy, or legal status.
                    item["body_extent_state"] = "unknown_from_bbox"
                    item["association_state"] = "unknown"
                    people.append(item)
                crops.append({
                    "pickup_bbox_xyxy": pickup["bbox_xyxy"],
                    "crop": crop,
                    "crop_size": [int(crop["x2"]) - int(crop["x1"]), int(crop["y2"]) - int(crop["y1"])],
                    "person_detections": people,
                })
            frames.append({
                "frame_index": frame_index,
                "timestamp_sec": timestamp,
                "timestamp_source": timestamp_source,
                "source_size": [frame_width, frame_height],
                "full_frame_detections": [
                    item for item in detections
                    if item["class_id"] in {pickup_class, person_class}
                ],
                "pickup_crops": crops,
            })
            evaluated += 1
    finally:
        capture.release()

    result = {
        "mode": "offline_pickup_detail_evaluation",
        "source": {**metadata, "sha256": source_hash},
        "model": {
            "path": str(weights_path.resolve()),
            "sha256": model_hash,
            "class_names": list(names),
        },
        "settings": {
            "raw_inference_confidence": 0.001,
            "reported_acceptance_threshold": confidence,
            "image_size": image_size,
            "start_sec": start_sec,
            "end_sec": end_sec,
            "max_frames": max_frames,
            "sample_every_frames": sample_every_frames,
        },
        "frames_evaluated": evaluated,
        "frames": frames,
        "interpretation": (
            "Detection evidence only. Body extent, cargo visibility, bed association, "
            "empty-bed state and legal outcome remain unknown."
        ),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", required=True, type=Path, help="Explicit local source video")
    parser.add_argument("--dry-run", action="store_true", help="Read video metadata only (default)")
    parser.add_argument("--run-inference", action="store_true", help="Explicitly run bounded local inference")
    parser.add_argument("--weights", type=Path, help="Explicit local YOLO checkpoint; no downloads")
    parser.add_argument("--output", type=Path, help="JSON result path required for inference")
    parser.add_argument("--start-sec", type=float, default=0.0)
    parser.add_argument("--end-sec", type=float)
    parser.add_argument("--max-frames", type=int, default=300)
    parser.add_argument("--sample-every-frames", type=int, default=1)
    parser.add_argument("--confidence", type=float, default=0.25)
    parser.add_argument("--image-size", type=int, default=640)
    parser.add_argument("--device", help="Optional explicit Ultralytics device")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.video.is_file():
        parser.error("--video must identify an existing local file")
    try:
        if args.run_inference:
            if not args.weights or not args.output:
                parser.error("--run-inference requires explicit --weights and --output")
            result = run_inference(
                args.video, args.weights, args.output,
                start_sec=args.start_sec, end_sec=args.end_sec,
                max_frames=args.max_frames,
                sample_every_frames=args.sample_every_frames,
                confidence=args.confidence, image_size=args.image_size,
                device=args.device,
            )
        else:
            result = {
                "mode": "metadata_only_dry_run",
                "source": video_metadata(args.video),
                "frames_decoded": 0,
                "inference_performed": False,
            }
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError) as exc:
        print(f"pickup evaluation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
