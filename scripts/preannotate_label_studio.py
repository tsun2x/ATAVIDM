"""Create human-review-only Label Studio predictions from a compatible detector."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


class PreannotationError(RuntimeError):
    """Raised when model suggestions cannot be generated safely."""


def validate_model_roster(model_names, canonical):
    if isinstance(model_names, dict):
        normalized = [str(model_names[index]) for index in sorted(model_names)]
    else:
        normalized = [str(name) for name in model_names]
    if normalized != list(canonical):
        raise PreannotationError(f"checkpoint class roster does not match canonical order: {normalized}")
    return normalized


def detection_results(*, image_name, source_group, width, height, detections, names, model_version):
    if width <= 0 or height <= 0:
        raise PreannotationError("image dimensions must be positive")
    results = []
    for index, detection in enumerate(detections, start=1):
        class_id = int(detection["class_id"])
        if not 0 <= class_id < len(names):
            raise PreannotationError(f"prediction class id out of range: {class_id}")
        left, top, right, bottom = map(float, detection["xyxy"])
        if not (0 <= left < right <= width and 0 <= top < bottom <= height):
            raise PreannotationError(f"prediction box outside image: {detection['xyxy']}")
        confidence = float(detection["confidence"])
        if not 0 <= confidence <= 1:
            raise PreannotationError(f"prediction confidence out of range: {confidence}")
        results.append({
            "id": f"model-{index}",
            "from_name": "vehicle",
            "to_name": "image",
            "type": "rectanglelabels",
            "score": confidence,
            "original_width": width,
            "original_height": height,
            "image_rotation": 0,
            "value": {
                "x": round(left / width * 100, 6),
                "y": round(top / height * 100, 6),
                "width": round((right - left) / width * 100, 6),
                "height": round((bottom - top) / height * 100, 6),
                "rotation": 0,
                "rectanglelabels": [names[class_id]],
            },
        })
    return {
        "data": {
            "image_name": image_name,
            "source_group": source_group,
            "review_required": True,
            "class_order": list(names),
        },
        "predictions": [{
            "model_version": model_version,
            "score": max((result["score"] for result in results), default=0.0),
            "result": results,
        }],
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build(manifest_path: Path, schema_path: Path, checkpoint: Path, document_root: Path, output: Path, confidence: float) -> dict:
    if output.exists():
        raise PreannotationError(f"refusing to overwrite output: {output}")
    if not 0 < confidence < 1:
        raise PreannotationError("confidence must be between zero and one")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    names = [str(name) for name in schema["vehicle_detector_classes"]]
    if manifest.get("status") != "selected_frames_pending_annotation":
        raise PreannotationError("frame manifest has unsafe status")
    if not checkpoint.is_file():
        raise PreannotationError(f"checkpoint not found: {checkpoint}")
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise PreannotationError("Ultralytics is not installed in the TAVIDM environment") from exc
    model = YOLO(str(checkpoint))
    validate_model_roster(model.names, names)
    model_version = f"{checkpoint.name}:{_sha256(checkpoint)[:12]}"
    tasks = []
    package_root = manifest_path.parent.resolve()
    document_root = document_root.resolve()
    for row in manifest.get("frames", []):
        image_path = (package_root / row["relative_path"]).resolve()
        if not image_path.is_file() or _sha256(image_path) != row["sha256"]:
            raise PreannotationError(f"selected image missing or hash mismatch: {image_path}")
        try:
            relative = image_path.relative_to(document_root).as_posix()
        except ValueError as exc:
            raise PreannotationError(f"selected image is outside document root: {image_path}") from exc
        prediction = model.predict(source=str(image_path), conf=confidence, verbose=False)[0]
        detections = [
            {"class_id": int(class_id), "confidence": float(score), "xyxy": xyxy}
            for xyxy, class_id, score in zip(
                prediction.boxes.xyxy.cpu().tolist(),
                prediction.boxes.cls.cpu().tolist(),
                prediction.boxes.conf.cpu().tolist(),
            )
        ]
        task = detection_results(
            image_name=image_path.name,
            source_group=manifest["source_group"],
            width=int(prediction.orig_shape[1]),
            height=int(prediction.orig_shape[0]),
            detections=detections,
            names=names,
            model_version=model_version,
        )
        task["data"]["image"] = f"/data/local-files/?d={relative}"
        task["data"]["frame_index"] = row["frame_index"]
        task["data"]["timestamp_ms"] = row["timestamp_ms"]
        tasks.append(task)
    temporary = output.with_suffix(output.suffix + ".tmp")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_text(json.dumps(tasks, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    return {"tasks": len(tasks), "model_version": model_version, "annotations_accepted": 0, "review_required": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--document-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--confidence", type=float, default=0.15)
    args = parser.parse_args()
    try:
        report = build(args.manifest.resolve(), args.schema.resolve(), args.checkpoint.resolve(), args.document_root.resolve(), args.output.resolve(), args.confidence)
    except (PreannotationError, OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
