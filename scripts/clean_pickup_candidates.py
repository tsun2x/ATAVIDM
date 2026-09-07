"""Build a versioned, provisional pickup-candidate dataset without mutating inputs.

Only objective file/annotation defects are automatically culled. Existing manual
decisions are authoritative. Structurally valid undecided candidates are marked
provisional and remain unapproved for merging or training.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image


QUEUE_FIELDS = (
    "output_image", "source", "source_split", "source_member", "source_group",
    "image_sha256", "pickup_instances", "context_instances", "visual_review_status",
)
DECISION_FIELDS = ("output_image", "source", "image_sha256", "decision", "reviewed_at", "note")
AUDIT_FIELDS = (
    "output_image", "source", "source_split", "source_group", "image_sha256",
    "manual_decision", "final_status", "reasons", "pickup_instances", "context_instances",
)
ALLOWED_DECISIONS = {"keep", "reject", "uncertain"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


class CleaningError(RuntimeError):
    """A fail-closed input or contract violation."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path: Path, required: tuple[str, ...]) -> list[dict[str, str]]:
    if not path.is_file():
        raise CleaningError(f"required CSV missing: {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = set(required) - set(reader.fieldnames or ())
        if missing:
            raise CleaningError(f"{path.name} missing columns: {sorted(missing)}")
        return list(reader)


def validate_label(path: Path, class_count: int, pickup_class_id: int) -> tuple[list[str], int, int]:
    reasons: set[str] = set()
    pickup_count = 0
    context_count = 0
    if not path.is_file():
        return ["label_missing"], 0, 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return ["label_unreadable"], 0, 0
    if not lines:
        return ["label_empty"], 0, 0
    for line in lines:
        parts = line.split()
        if len(parts) != 5:
            reasons.add("label_field_count_invalid")
            continue
        try:
            class_id = int(parts[0])
            values = [float(value) for value in parts[1:]]
        except ValueError:
            reasons.add("label_value_invalid")
            continue
        if class_id < 0 or class_id >= class_count:
            reasons.add("label_class_out_of_range")
        x, y, width, height = values
        if not all(value == value and abs(value) != float("inf") for value in values):
            reasons.add("label_value_nonfinite")
        elif width <= 0 or height <= 0:
            reasons.add("label_dimension_nonpositive")
        elif not (0 <= x <= 1 and 0 <= y <= 1 and 0 < width <= 1 and 0 < height <= 1):
            reasons.add("label_coordinate_out_of_range")
        elif x - width / 2 < 0 or x + width / 2 > 1 or y - height / 2 < 0 or y + height / 2 > 1:
            reasons.add("label_box_exceeds_image")
        if class_id == pickup_class_id:
            pickup_count += 1
        else:
            context_count += 1
    if pickup_count == 0:
        reasons.add("pickup_annotation_missing")
    return sorted(reasons), pickup_count, context_count


def validate_image(path: Path, expected_hash: str) -> list[str]:
    reasons: set[str] = set()
    if not path.is_file():
        return ["image_missing"]
    if path.suffix.lower() not in IMAGE_SUFFIXES:
        reasons.add("image_extension_unsupported")
    try:
        if sha256(path) != expected_hash.lower():
            reasons.add("image_hash_mismatch")
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            if image.width <= 0 or image.height <= 0:
                reasons.add("image_dimensions_invalid")
    except (OSError, ValueError):
        reasons.add("image_unreadable")
    return sorted(reasons)


def copy_tree_exact(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise CleaningError(f"required directory missing: {source}")
    shutil.copytree(source, destination, copy_function=shutil.copy2)


def write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def inventory(root: Path) -> list[dict[str, str]]:
    rows = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative in {"reports/file_inventory.csv", "manifest.json"}:
            continue
        rows.append({"path": relative, "bytes": str(path.stat().st_size), "sha256": sha256(path)})
    return rows


def build(candidates: Path, benchmark: Path, output: Path, audit_per_source: int) -> dict[str, object]:
    if output.exists():
        raise CleaningError(f"output already exists; choose a new version: {output}")
    if audit_per_source < 1:
        raise CleaningError("audit-per-source must be positive")
    manifest_path = candidates / "manifest.json"
    try:
        source_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        names = source_manifest["names"]
        pickup_class_id = int(source_manifest["pickup_class_id"])
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise CleaningError(f"invalid candidate manifest: {exc}") from exc
    if not isinstance(names, list) or not names or not all(isinstance(name, str) for name in names):
        raise CleaningError("candidate manifest names must be a non-empty string list")
    if pickup_class_id < 0 or pickup_class_id >= len(names) or names[pickup_class_id] != "pickup_truck":
        raise CleaningError("candidate pickup_class_id does not identify pickup_truck")

    queue = read_csv(candidates / "review_queue.csv", QUEUE_FIELDS)
    decisions = read_csv(candidates / "review_decisions.csv", DECISION_FIELDS)
    queue_by_name: dict[str, dict[str, str]] = {}
    for row in queue:
        name = row["output_image"]
        if not name or Path(name).name != name:
            raise CleaningError(f"unsafe output_image in review queue: {name!r}")
        if name in queue_by_name:
            raise CleaningError(f"duplicate output_image in review queue: {name}")
        queue_by_name[name] = row

    decisions_by_name: dict[str, dict[str, str]] = {}
    for decision in decisions:
        name = decision["output_image"]
        if name in decisions_by_name:
            raise CleaningError(f"duplicate manual decision: {name}")
        queue_row = queue_by_name.get(name)
        if queue_row is None:
            raise CleaningError(f"manual decision has no review-queue row: {name}")
        if decision["decision"] not in ALLOWED_DECISIONS:
            raise CleaningError(f"invalid manual decision for {name}: {decision['decision']!r}")
        if decision["source"] != queue_row["source"]:
            raise CleaningError(f"manual decision source mismatch: {name}")
        if decision["image_sha256"].lower() != queue_row["image_sha256"].lower():
            raise CleaningError(f"manual decision hash mismatch: {name}")
        decisions_by_name[name] = decision

    temp = output.with_name(output.name + ".building")
    if temp.exists():
        raise CleaningError(f"temporary output already exists: {temp}")
    audit_rows: list[dict[str, str]] = []
    accepted_by_source: dict[str, list[dict[str, str]]] = defaultdict(list)
    counts: Counter[str] = Counter()
    try:
        for directory in (temp / "train" / "images", temp / "train" / "labels", temp / "reports"):
            directory.mkdir(parents=True, exist_ok=True)
        for name, row in sorted(queue_by_name.items()):
            image_path = candidates / "train" / "images" / name
            label_path = candidates / "train" / "labels" / f"{Path(name).stem}.txt"
            reasons = validate_image(image_path, row["image_sha256"])
            label_reasons, pickup_count, context_count = validate_label(label_path, len(names), pickup_class_id)
            reasons = sorted(set(reasons + label_reasons))
            decision = decisions_by_name.get(name, {}).get("decision", "")
            if reasons:
                final_status = "culled_structural_error"
            elif decision == "keep":
                final_status = "accepted_manual_keep"
            elif decision == "reject":
                final_status = "excluded_manual_reject"
            elif decision == "uncertain":
                final_status = "excluded_manual_uncertain"
            else:
                final_status = "accepted_provisional"
            audit_row = {
                "output_image": name,
                "source": row["source"],
                "source_split": row["source_split"],
                "source_group": row["source_group"],
                "image_sha256": row["image_sha256"],
                "manual_decision": decision,
                "final_status": final_status,
                "reasons": ";".join(reasons),
                "pickup_instances": str(pickup_count),
                "context_instances": str(context_count),
            }
            audit_rows.append(audit_row)
            counts[final_status] += 1
            if final_status.startswith("accepted_"):
                shutil.copy2(image_path, temp / "train" / "images" / name)
                shutil.copy2(label_path, temp / "train" / "labels" / label_path.name)
                accepted_by_source[row["source"]].append(audit_row)

        for split in ("valid", "test"):
            copy_tree_exact(benchmark / split, temp / split)
        shutil.copy2(benchmark / "data.yaml", temp / "benchmark_data.yaml")
        shutil.copy2(candidates / "review_decisions.csv", temp / "reports" / "manual_decisions_snapshot.csv")
        shutil.copy2(candidates / "review_queue.csv", temp / "reports" / "review_queue_snapshot.csv")
        write_csv(temp / "reports" / "cleaning_audit.csv", AUDIT_FIELDS, audit_rows)
        write_csv(temp / "reports" / "structural_culls.csv", AUDIT_FIELDS, [row for row in audit_rows if row["final_status"] == "culled_structural_error"])

        sample: list[dict[str, str]] = []
        for source, rows in sorted(accepted_by_source.items()):
            ranked = sorted(rows, key=lambda row: hashlib.sha256(("pickup-audit-v1|" + row["image_sha256"]).encode()).hexdigest())
            sample.extend(ranked[:audit_per_source])
        write_csv(temp / "reports" / "source_balanced_audit_sample.csv", AUDIT_FIELDS, sample)

        data_yaml = "path: .\ntrain: train/images\nval: valid/images\ntest: test/images\nnames:\n" + "".join(f"  {index}: {name}\n" for index, name in enumerate(names))
        (temp / "data.yaml").write_text(data_yaml, encoding="utf-8")
        inventory_rows = inventory(temp)
        write_csv(temp / "reports" / "file_inventory.csv", ("path", "bytes", "sha256"), inventory_rows)
        result: dict[str, object] = {
            "dataset_version": output.name,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "provisional_not_approved_for_training",
            "policy": "Manual decisions preserved. Only objective structural/file failures automatically culled. Undecided valid candidates are provisional. No merge or training authorized.",
            "sources": {
                "candidate_quarantine": str(candidates.resolve()),
                "candidate_manifest_sha256": sha256(manifest_path),
                "review_queue_sha256": sha256(candidates / "review_queue.csv"),
                "manual_decisions_sha256": sha256(candidates / "review_decisions.csv"),
                "benchmark_dataset": str(benchmark.resolve()),
                "benchmark_data_yaml_sha256": sha256(benchmark / "data.yaml"),
            },
            "names": names,
            "pickup_class_id": pickup_class_id,
            "counts": dict(sorted(counts.items())),
            "audit": {"per_source_requested": audit_per_source, "rows": len(sample), "counts_by_source": dict(sorted(Counter(row["source"] for row in sample).items()))},
            "reports": ["reports/cleaning_audit.csv", "reports/structural_culls.csv", "reports/source_balanced_audit_sample.csv", "reports/manual_decisions_snapshot.csv", "reports/review_queue_snapshot.csv", "reports/file_inventory.csv"],
        }
        (temp / "manifest.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temp.rename(output)
        return result
    except Exception:
        if temp.exists():
            shutil.rmtree(temp)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, required=True, help="Reviewed dataset whose valid/test splits are copied unchanged")
    parser.add_argument("--output", type=Path, required=True, help="New versioned output; must not already exist")
    parser.add_argument("--audit-per-source", type=int, default=100)
    args = parser.parse_args()
    try:
        result = build(args.candidates.resolve(), args.benchmark.resolve(), args.output.resolve(), args.audit_per_source)
    except (CleaningError, OSError, csv.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
