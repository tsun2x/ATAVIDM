"""Validate reviewed Label Studio vehicle tasks against selected-frame manifests."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


class ValidationError(RuntimeError):
    """Raised when reviewed annotations are incomplete or unsafe."""


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"invalid JSON: {path}: {exc}") from exc


def _classes(schema: Path) -> list[str]:
    payload = _load_json(schema)
    try:
        names = payload["vehicle_detector_classes"]
    except (TypeError, KeyError) as exc:
        raise ValidationError("schema is missing vehicle_detector_classes") from exc
    if not isinstance(names, list) or len(names) != 10 or len(set(names)) != 10:
        raise ValidationError("schema must contain exactly 10 unique vehicle classes")
    return [str(name) for name in names]


def _manifest_index(paths: list[Path]) -> tuple[dict[str, dict], list[str]]:
    frames: dict[str, dict] = {}
    hashes: dict[str, str] = {}
    groups: set[str] = set()
    for path in paths:
        payload = _load_json(path)
        if payload.get("status") != "selected_frames_pending_annotation":
            raise ValidationError(f"manifest has unsafe status: {path}")
        group = payload.get("source_group")
        if not isinstance(group, str) or not group or group in groups:
            raise ValidationError(f"blank or duplicate source group: {group!r}")
        groups.add(group)
        for row in payload.get("frames", []):
            name = Path(str(row.get("relative_path", ""))).name
            digest = str(row.get("sha256", ""))
            if not name or name in frames:
                raise ValidationError(f"blank or duplicate frame name: {name!r}")
            if len(digest) != 64:
                raise ValidationError(f"invalid frame hash: {name}")
            if digest in hashes:
                raise ValidationError(f"duplicate frame content across source groups: {hashes[digest]} and {name}")
            hashes[digest] = name
            frames[name] = {**row, "source_group": group}
    if not frames:
        raise ValidationError("manifests contain no frames")
    return frames, sorted(groups)


def validate(export: Path, manifests: list[Path], schema: Path, output: Path) -> dict:
    if output.exists():
        raise ValidationError(f"refusing to overwrite output: {output}")
    names = _classes(schema)
    class_to_id = {name: index for index, name in enumerate(names)}
    expected, source_groups = _manifest_index(manifests)
    tasks = _load_json(export)
    if not isinstance(tasks, list):
        raise ValidationError("Label Studio export must be a task list")
    seen: set[str] = set()
    frames = []
    counts: Counter[str] = Counter()
    for task in tasks:
        data = task.get("data", {})
        name = str(data.get("image_name", ""))
        if name not in expected or name in seen:
            raise ValidationError(f"unexpected or duplicate reviewed task: {name!r}")
        seen.add(name)
        expected_group = expected[name]["source_group"]
        if data.get("source_group") != expected_group:
            raise ValidationError(f"source-group mismatch for {name}")
        annotations = [row for row in task.get("annotations", []) if not row.get("was_cancelled") and not row.get("skipped")]
        if len(annotations) != 1:
            raise ValidationError(f"frame must have exactly one completed annotation: {name}")
        boxes = []
        review_states = []
        for result in annotations[0].get("result", []):
            if result.get("from_name") == "review_state" and result.get("type") == "choices":
                review_states.extend(result.get("value", {}).get("choices", []))
                continue
            if result.get("from_name") != "vehicle" or result.get("type") != "rectanglelabels":
                continue
            value = result.get("value", {})
            labels = value.get("rectanglelabels", [])
            if len(labels) != 1 or labels[0] not in class_to_id:
                raise ValidationError(f"unknown or ambiguous vehicle class in {name}")
            try:
                x, y, width, height = (float(value[key]) for key in ("x", "y", "width", "height"))
                rotation = float(value.get("rotation", 0))
            except (KeyError, TypeError, ValueError) as exc:
                raise ValidationError(f"invalid rectangle in {name}") from exc
            if rotation != 0 or not (0 <= x <= 100 and 0 <= y <= 100 and 0 < width <= 100 and 0 < height <= 100 and x + width <= 100 and y + height <= 100):
                raise ValidationError(f"invalid rectangle geometry in {name}")
            label = labels[0]
            counts[label] += 1
            boxes.append({
                "class_id": class_to_id[label],
                "class_name": label,
                "x_center": round((x + width / 2) / 100, 8),
                "y_center": round((y + height / 2) / 100, 8),
                "width": round(width / 100, 8),
                "height": round(height / 100, 8),
            })
        if review_states == ["Contains uncertain vehicle"]:
            raise ValidationError(f"unresolved uncertain vehicle in {name}")
        if review_states != ["Review complete"]:
            raise ValidationError(f"missing explicit review-complete state in {name}")
        frames.append({
            "image_name": name,
            "source_group": expected_group,
            "sha256": expected[name]["sha256"],
            "frame_index": expected[name]["frame_index"],
            "timestamp_ms": expected[name]["timestamp_ms"],
            "boxes": boxes,
        })
    missing = sorted(set(expected) - seen)
    if missing:
        raise ValidationError(f"selected frames missing completed review: {len(missing)}")
    report = {
        "schema_version": "1.0.0",
        "status": "validated_for_dataset_build_review",
        "source_groups": source_groups,
        "reviewed_frames": len(frames),
        "class_order": names,
        "class_counts": dict(sorted(counts.items())),
        "frames": sorted(frames, key=lambda row: (row["source_group"], row["frame_index"])),
        "training_authorized": False,
        "next_gate": "Assign whole source groups to splits and obtain explicit dataset-build approval.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(output)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, action="append", required=True)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = validate(args.export.resolve(), [path.resolve() for path in args.manifest], args.schema.resolve(), args.output.resolve())
    except (ValidationError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": report["status"], "reviewed_frames": report["reviewed_frames"], "source_groups": report["source_groups"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
