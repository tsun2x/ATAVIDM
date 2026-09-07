"""Convert an attributed YOLO dataset into prediction-only Label Studio tasks."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from PIL import Image


class TaskBuildError(RuntimeError):
    """Raised when an input cannot safely become a review task."""


SOURCE_RE = re.compile(r"(?:^|__)ph\d+_(?P<group>.+?)_mp4-\d+_jpg", re.IGNORECASE)


def _classes(schema_path: Path) -> list[str]:
    try:
        payload = json.loads(schema_path.read_text(encoding="utf-8"))
        names = payload["vehicle_detector_classes"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError) as exc:
        raise TaskBuildError(f"invalid class schema: {exc}") from exc
    if not isinstance(names, list) or len(names) != 10 or len(set(names)) != 10:
        raise TaskBuildError("class schema must contain exactly 10 unique vehicle classes")
    return [str(name) for name in names]


def _source_group(stem: str) -> str:
    match = SOURCE_RE.search(stem)
    if not match:
        raise TaskBuildError(f"source video group is not attributable: {stem}")
    return match.group("group")


def _results(label_path: Path, names: list[str], width: int, height: int) -> list[dict]:
    results = []
    for index, line in enumerate(label_path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 5:
            raise TaskBuildError(f"malformed YOLO row: {label_path}:{index + 1}")
        try:
            class_id = int(fields[0])
            xc, yc, box_width, box_height = map(float, fields[1:])
        except ValueError as exc:
            raise TaskBuildError(f"malformed YOLO row: {label_path}:{index + 1}") from exc
        if not 0 <= class_id < len(names):
            raise TaskBuildError(f"class id out of range: {label_path}:{index + 1}")
        if not (0 <= xc <= 1 and 0 <= yc <= 1 and 0 < box_width <= 1 and 0 < box_height <= 1):
            raise TaskBuildError(f"invalid YOLO coordinates: {label_path}:{index + 1}")
        if xc - box_width / 2 < 0 or yc - box_height / 2 < 0 or xc + box_width / 2 > 1 or yc + box_height / 2 > 1:
            raise TaskBuildError(f"YOLO box exceeds image bounds: {label_path}:{index + 1}")
        results.append({
            "id": f"existing-{index + 1}",
            "from_name": "vehicle",
            "to_name": "image",
            "type": "rectanglelabels",
            "original_width": width,
            "original_height": height,
            "image_rotation": 0,
            "value": {
                "x": round((xc - box_width / 2) * 100, 6),
                "y": round((yc - box_height / 2) * 100, 6),
                "width": round(box_width * 100, 6),
                "height": round(box_height * 100, 6),
                "rotation": 0,
                "rectanglelabels": [names[class_id]],
            },
        })
    return results


def build(dataset: Path, schema: Path, output: Path, attributed_only: bool = False) -> dict:
    if output.exists():
        raise TaskBuildError(f"refusing to overwrite output: {output}")
    dataset = dataset.resolve()
    names = _classes(schema.resolve())
    tasks = []
    skipped_unattributable = 0
    for split in ("train", "valid", "test"):
        image_dir = dataset / split / "images"
        label_dir = dataset / split / "labels"
        if not image_dir.exists() and not label_dir.exists():
            continue
        if not image_dir.is_dir() or not label_dir.is_dir():
            raise TaskBuildError(f"incomplete split directories: {split}")
        for label_path in sorted(label_dir.glob("*.txt")):
            matches = [path for path in image_dir.glob(label_path.stem + ".*") if path.suffix.lower() in {".jpg", ".jpeg", ".png"}]
            if len(matches) != 1:
                raise TaskBuildError(f"expected exactly one image for label: {label_path}")
            image_path = matches[0]
            try:
                with Image.open(image_path) as opened:
                    width, height = opened.size
                    opened.verify()
            except OSError as exc:
                raise TaskBuildError(f"unreadable image: {image_path}") from exc
            relative = image_path.relative_to(dataset).as_posix()
            try:
                source_group = _source_group(image_path.stem)
            except TaskBuildError:
                if not attributed_only:
                    raise
                skipped_unattributable += 1
                continue
            tasks.append({
                "data": {
                    "image": f"/data/local-files/?d={relative}",
                    "image_name": image_path.name,
                    "split": split,
                    "source_group": source_group,
                    "review_required": True,
                    "class_order": names,
                },
                "predictions": [{
                    "model_version": "existing-yolo-labels-unverified",
                    "score": 0.0,
                    "result": _results(label_path, names, width, height),
                }],
            })
    if not tasks:
        raise TaskBuildError("dataset contains no attributable labeled images")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(tasks, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    return {
        "tasks": len(tasks),
        "output": str(output),
        "classes": names,
        "annotations_accepted": 0,
        "skipped_unattributable": skipped_unattributable,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--attributed-only", action="store_true")
    args = parser.parse_args()
    try:
        report = build(args.dataset, args.schema, args.output, args.attributed_only)
    except (TaskBuildError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
