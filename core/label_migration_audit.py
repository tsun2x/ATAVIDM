"""Dry-run audit for legacy vehicle label names (no dataset rewrites).

Usage:
  python -m core.label_migration_audit
  python -m core.label_migration_audit --labels-dir path/to/labels

Scans ``.txt`` YOLO label files and/or reports the name-based migration
manifest. Never modifies files.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from core.detection_config import (
    FROZEN_VEHICLE_DETECTOR_CLASSES,
    LEGACY_CLASS_PIAGGIO,
    LEGACY_CLASS_UV_EXPRESS_VAN,
    SEVEN_CLASS_BASELINE_MISSING,
    SEVEN_CLASS_BASELINE_VEHICLES,
)

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "config" / "training" / "label_migration_1_1_to_1_2.json"
SCHEMA_PATH = ROOT / "config" / "training" / "class_schema.json"


def load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def audit_label_directory(labels_dir: Path) -> dict:
    """Count class-id occurrences; cannot map IDs to names without a names file.

    Reports file counts and warns that numeric IDs must be validated against
    the source dataset's data.yaml / names map before any rewrite.
    """
    txt_files = list(labels_dir.rglob("*.txt")) if labels_dir.is_dir() else []
    id_counts: Counter[int] = Counter()
    for path in txt_files:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line in text.splitlines():
            parts = line.strip().split()
            if not parts:
                continue
            try:
                class_id = int(float(parts[0]))
            except ValueError:
                continue
            id_counts[class_id] += 1
    return {
        "labels_dir": str(labels_dir),
        "label_files": len(txt_files),
        "class_id_counts": dict(sorted(id_counts.items())),
        "warning": (
            "Numeric class IDs are dataset-specific. Do not assume they match "
            "the 1.1.0 or 1.2.0 name order without validating the source names map. "
            f"Legacy name consolidations: {LEGACY_CLASS_UV_EXPRESS_VAN}→van (safe); "
            f"{LEGACY_CLASS_PIAGGIO}→tricycle|autorickshaw (manual review only)."
        ),
    }


def summarize_contract() -> dict:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    manifest = load_manifest()
    return {
        "schema_version": schema.get("schema_version"),
        "vehicle_detector_classes": list(schema.get("vehicle_detector_classes", [])),
        "runtime_frozen": list(FROZEN_VEHICLE_DETECTOR_CLASSES),
        "runtime_matches_schema": tuple(schema.get("vehicle_detector_classes", []))
        == FROZEN_VEHICLE_DETECTOR_CLASSES,
        "safe_consolidations": manifest.get("safe_consolidations"),
        "requires_image_review": manifest.get("requires_image_review"),
        "seven_class_baseline": list(SEVEN_CLASS_BASELINE_VEHICLES),
        "seven_class_baseline_missing": list(SEVEN_CLASS_BASELINE_MISSING),
        "object_count": len(schema.get("object_classes", [])),
        "scene_count": len(schema.get("scene_classes", [])),
        "total_labels": len(schema.get("object_classes", []))
        + len(schema.get("scene_classes", [])),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--labels-dir",
        type=Path,
        default=None,
        help="Optional YOLO labels directory to count class IDs (read-only).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON.",
    )
    args = parser.parse_args(argv)
    report = {
        "contract": summarize_contract(),
        "manifest_path": str(MANIFEST_PATH.relative_to(ROOT)).replace("\\", "/"),
    }
    if args.labels_dir is not None:
        report["label_audit"] = audit_label_directory(args.labels_dir)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        contract = report["contract"]
        print(f"Schema {contract['schema_version']}: {contract['total_labels']} labels "
              f"({contract['object_count']} object + {contract['scene_count']} scene)")
        print(f"Vehicles ({len(contract['vehicle_detector_classes'])}): "
              f"{', '.join(contract['vehicle_detector_classes'])}")
        print(f"Runtime matches schema: {contract['runtime_matches_schema']}")
        print("Safe consolidations:")
        for item in contract["safe_consolidations"] or []:
            print(f"  {item['from']} -> {item['to']} ({item['action']})")
        print("Requires image review:")
        for item in contract["requires_image_review"] or []:
            print(f"  {item['from']} -> {'|'.join(item['candidates'])} ({item['action']})")
        print(f"7-class baseline missing: {', '.join(contract['seven_class_baseline_missing'])}")
        if "label_audit" in report:
            audit = report["label_audit"]
            print(f"Label files scanned: {audit['label_files']}")
            print(f"Class ID counts: {audit['class_id_counts']}")
            print(audit["warning"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
