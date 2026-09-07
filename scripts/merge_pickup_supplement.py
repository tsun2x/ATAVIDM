"""Merge an owner-approved pickup supplement into a new training dataset version."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


class MergeError(RuntimeError):
    """A fail-closed schema, provenance, or dataset error."""


AUDIT_FIELDS = ("output_image", "source", "image_sha256", "merge_status", "output_image_name", "reason")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _base_names(path: Path) -> list[str]:
    names: dict[int, str] = {}
    in_names = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() == "names:":
            in_names = True
            continue
        if in_names:
            match = re.match(r"\s+(\d+):\s*(\S.*?)\s*$", line)
            if match:
                names[int(match.group(1))] = match.group(2)
            elif line.strip():
                break
    if not names or sorted(names) != list(range(len(names))):
        raise MergeError("base data.yaml has invalid names mapping")
    return [names[index] for index in range(len(names))]


def _pairs(root: Path, split: str) -> list[tuple[Path, Path]]:
    image_dir, label_dir = root / split / "images", root / split / "labels"
    if not image_dir.is_dir() or not label_dir.is_dir():
        raise MergeError(f"base split missing: {split}")
    images = sorted(path for path in image_dir.iterdir() if path.is_file())
    labels = {path.stem: path for path in label_dir.iterdir() if path.is_file()}
    if len(images) != len(labels):
        raise MergeError(f"base image/label count mismatch: {split}")
    result = []
    for image in images:
        label = labels.get(image.stem)
        if label is None:
            raise MergeError(f"base label missing: {split}/{image.name}")
        result.append((image, label))
    return result


def _count_label(path: Path, class_count: int) -> Counter[int]:
    counts: Counter[int] = Counter()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise MergeError(f"label unreadable: {path}: {exc}") from exc
    for line in lines:
        parts = line.split()
        if len(parts) != 5:
            raise MergeError(f"label malformed: {path}")
        try:
            class_id = int(parts[0])
            values = [float(value) for value in parts[1:]]
        except ValueError as exc:
            raise MergeError(f"label malformed: {path}") from exc
        if not 0 <= class_id < class_count or not all(value == value and abs(value) != float("inf") for value in values):
            raise MergeError(f"label class/value invalid: {path}")
        counts[class_id] += 1
    return counts


def merge(base: Path, supplement: Path, approval: Path, output: Path, owner_approved: bool) -> dict[str, object]:
    if not owner_approved:
        raise MergeError("explicit owner approval flag is required")
    if output.exists():
        raise MergeError(f"output already exists; choose a new version: {output}")
    base_yaml = base / "data.yaml"
    base_names = _base_names(base_yaml)
    try:
        supplement_manifest = json.loads((supplement / "manifest.json").read_text(encoding="utf-8"))
        approval_manifest = json.loads((approval / "approval_manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MergeError(f"invalid supplement/approval manifest: {exc}") from exc
    if supplement_manifest.get("status") != "high_confidence_candidates_not_semantically_verified" or supplement_manifest.get("training_only") is not True:
        raise MergeError("supplement status/governance mismatch")
    if supplement_manifest.get("names") != base_names or supplement_manifest.get("pickup_class_id") != base_names.index("pickup_truck"):
        raise MergeError("base and supplement class schemas differ")
    if approval_manifest.get("status") != "owner_semantic_sample_audit_passed" or approval_manifest.get("decision_counts", {}).get("keep", 0) < 1:
        raise MergeError("owner-reviewed audit approval is missing")

    base_pairs = {split: _pairs(base, split) for split in ("train", "valid", "test")}
    hashes_by_split: dict[str, set[str]] = {}
    for split, pairs in base_pairs.items():
        hashes_by_split[split] = {_sha256(image) for image, _ in pairs}

    selected_path = supplement / "reports" / "selected_candidates.csv"
    if not selected_path.is_file():
        raise MergeError("supplement selected-candidates report missing")
    with selected_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {"output_image", "source", "image_sha256"}
        if required - set(reader.fieldnames or ()):
            raise MergeError("supplement selected-candidates report schema mismatch")
        selected = list(reader)
    seen_names: set[str] = set()
    seen_supplement_hashes: set[str] = set()
    audit_rows: list[dict[str, str]] = []
    additions: list[tuple[Path, Path, str]] = []
    for row in selected:
        name = row["output_image"]
        if not name or Path(name).name != name or name in seen_names:
            raise MergeError(f"unsafe or duplicate supplement filename: {name!r}")
        seen_names.add(name)
        image = supplement / "train" / "images" / name
        label = supplement / "train" / "labels" / f"{Path(name).stem}.txt"
        if not image.is_file() or not label.is_file():
            raise MergeError(f"supplement pair missing: {name}")
        digest = _sha256(image)
        if digest != row["image_sha256"].lower():
            raise MergeError(f"supplement image hash mismatch: {name}")
        _count_label(label, len(base_names))
        duplicate_split = next((split for split in ("valid", "test", "train") if digest in hashes_by_split[split]), None)
        output_name = f"pickup_hc_v1__{name}"
        if duplicate_split:
            status, reason = f"excluded_exact_duplicate_{duplicate_split}", f"image SHA-256 already present in base {duplicate_split}"
        elif digest in seen_supplement_hashes:
            status, reason = "excluded_exact_duplicate_supplement", "duplicate image SHA-256 within supplement"
        else:
            status, reason = "merged_train_only", "owner-approved high-confidence pickup supplement"
            additions.append((image, label, output_name))
            seen_supplement_hashes.add(digest)
        audit_rows.append({"output_image": name, "source": row["source"], "image_sha256": digest, "merge_status": status, "output_image_name": output_name if status == "merged_train_only" else "", "reason": reason})

    temp = output.with_name(output.name + ".building")
    if temp.exists():
        raise MergeError(f"temporary output already exists: {temp}")
    try:
        shutil.copytree(base, temp, copy_function=shutil.copy2)
        reports = temp / "reports"
        reports.mkdir(exist_ok=True)
        for image, label, output_name in additions:
            destination_image = temp / "train" / "images" / output_name
            destination_label = temp / "train" / "labels" / f"{Path(output_name).stem}.txt"
            if destination_image.exists() or destination_label.exists():
                raise MergeError(f"merge output collision: {output_name}")
            shutil.copy2(image, destination_image)
            shutil.copy2(label, destination_label)
        with (reports / "pickup_merge_audit.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=AUDIT_FIELDS)
            writer.writeheader(); writer.writerows(audit_rows)

        instances_by_split: dict[str, dict[str, int]] = {}
        images_by_split: dict[str, int] = {}
        for split in ("train", "valid", "test"):
            pairs = _pairs(temp, split)
            images_by_split[split] = len(pairs)
            counts: Counter[int] = Counter()
            for _, label in pairs:
                counts.update(_count_label(label, len(base_names)))
            instances_by_split[split] = {base_names[index]: counts[index] for index in range(len(base_names))}
        data_yaml = f"path: {output.as_posix()}\ntrain: train/images\nval: valid/images\ntest: test/images\n\nnames:\n" + "".join(f"  {index}: {name}\n" for index, name in enumerate(base_names))
        (temp / "data.yaml").write_text(data_yaml, encoding="utf-8")
        merge_counts = Counter(row["merge_status"] for row in audit_rows)
        result: dict[str, object] = {
            "dataset_version": output.name,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "merged_dataset_ready_for_training_review",
            "merge_authorization": "Explicit project-owner approval in current workflow.",
            "training_authorized": False,
            "base_dataset": str(base),
            "base_data_yaml_sha256": _sha256(base_yaml),
            "supplement_dataset": str(supplement),
            "supplement_manifest_sha256": _sha256(supplement / "manifest.json"),
            "approval_record": str(approval),
            "approval_manifest_sha256": _sha256(approval / "approval_manifest.json"),
            "names": base_names,
            "pickup_class_id": base_names.index("pickup_truck"),
            "supplement_candidates": len(selected),
            "supplement_merged": len(additions),
            "merge_counts": dict(sorted(merge_counts.items())),
            "images_by_split": images_by_split,
            "instances_by_split": instances_by_split,
            "evaluation_policy": "Base validation and test splits copied unchanged; supplement added to train only.",
            "report": "reports/pickup_merge_audit.csv",
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
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--supplement", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--owner-approved", action="store_true")
    args = parser.parse_args()
    try:
        result = merge(args.base.resolve(), args.supplement.resolve(), args.approval.resolve(), args.output.resolve(), args.owner_approved)
    except (MergeError, OSError, csv.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
