"""Plan conservative source groups and development splits for V9 ZIP exports."""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import PurePosixPath


class GroupingError(RuntimeError):
    """Raised when a source cannot be grouped or assigned without guessing."""


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
LEDGER_FIELDS = (
    "archive",
    "image_member",
    "label_member",
    "image_sha256",
    "source_group",
    "group_confidence",
    "group_basis",
    "proposed_split",
    "duplicate_of",
    "instances",
)


def infer_source_group(archive: Path, member_name: str) -> tuple[str, str, str]:
    stem = Path(member_name).stem
    stem = re.sub(r"\.rf\.[0-9a-f]+$", "", stem, flags=re.IGNORECASE)
    if "V9 Development Review" in archive.name:
        match = re.fullmatch(
            r"tavidm_v9_(baliwasan|zppsu|car_detection_v15|truck_v4|roboflow_datasetv5_v2|ibt_footbridge)_\d+_jpg",
            stem,
            flags=re.IGNORECASE,
        )
        if not match:
            raise GroupingError(f"unrecognized controlled V9 filename: {member_name}")
        return f"v9:{match.group(1).lower()}", "high", "controlled_v9_filename_prefix"

    video_match = re.fullmatch(r"(.+)_mp4-\d+_jpg", stem, flags=re.IGNORECASE)
    if video_match:
        return f"cctv:{video_match.group(1)}", "high", "video_frame_filename"

    slug_match = re.search(r"TAVIDM[ _-]+(?:staging|Staging)[ _-]+(.+?)\.v\d", archive.name)
    if not slug_match:
        raise GroupingError(f"cannot derive archive fallback group: {archive.name}")
    slug = re.sub(r"[^a-z0-9]+", "-", slug_match.group(1).lower()).strip("-")
    return f"archive:tavidm-staging-{slug}", "conservative", "archive_fallback_missing_source_manifest"


def proposed_split(source_group: str) -> str:
    if source_group == "v9:baliwasan":
        return "valid"
    if source_group == "v9:zppsu":
        return "test"
    if source_group.startswith("v9:"):
        approved_train_groups = {
            "v9:car_detection_v15",
            "v9:truck_v4",
            "v9:roboflow_datasetv5_v2",
            "v9:ibt_footbridge",
        }
        if source_group not in approved_train_groups:
            raise GroupingError(f"unapproved V9 source group: {source_group}")
    return "train"


def _parse_names(text: str) -> list[str]:
    match = re.search(r"(?m)^names:\s*(\[[^\n]+\])\s*$", text)
    if not match:
        raise GroupingError("data.yaml names mapping is missing or unsupported")
    try:
        names = ast.literal_eval(match.group(1))
    except (SyntaxError, ValueError) as exc:
        raise GroupingError(f"data.yaml names mapping is invalid: {exc}") from exc
    if not isinstance(names, list) or not names or not all(isinstance(name, str) for name in names):
        raise GroupingError("data.yaml names must be a nonempty string list")
    if len(names) != len(set(names)):
        raise GroupingError("data.yaml contains duplicate class names")
    return names


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _label_counts(text: str, source_names: list[str], canonical_names: list[str]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split()
        try:
            class_id = int(parts[0])
        except (IndexError, ValueError) as exc:
            raise GroupingError(f"invalid label class at line {line_number}") from exc
        if not 0 <= class_id < len(source_names):
            raise GroupingError(f"label class {class_id} outside source schema at line {line_number}")
        name = source_names[class_id]
        if name not in canonical_names:
            raise GroupingError(f"noncanonical class {name!r}")
        counts[name] += 1
    return counts


def build_plan(source: Path, output: Path, canonical_names: list[str]) -> dict[str, object]:
    if output.exists():
        raise GroupingError(f"output already exists: {output}")
    archives = sorted(source.glob("*.zip"), key=lambda path: path.name.lower())
    if not archives:
        raise GroupingError(f"no ZIP archives found in {source}")
    if len(canonical_names) != len(set(canonical_names)):
        raise GroupingError("canonical class names are duplicated")

    rows: list[dict[str, str]] = []
    seen_hashes: dict[str, str] = {}
    group_images: Counter[str] = Counter()
    group_instances: dict[str, Counter[str]] = defaultdict(Counter)
    split_images: Counter[str] = Counter()
    split_instances: dict[str, Counter[str]] = defaultdict(Counter)
    archive_hashes: dict[str, str] = {}

    for archive in archives:
        archive_hashes[archive.name] = _sha256_file(archive)
        with zipfile.ZipFile(archive) as zf:
            yaml_members = [info for info in zf.infolist() if PurePosixPath(info.filename).name == "data.yaml"]
            if len(yaml_members) != 1:
                raise GroupingError(f"{archive.name}: expected exactly one data.yaml")
            source_names = _parse_names(zf.read(yaml_members[0]).decode("utf-8-sig"))
            unknown = sorted(set(source_names) - set(canonical_names))
            if unknown:
                raise GroupingError(f"{archive.name}: noncanonical classes: {unknown}")

            label_members = {
                PurePosixPath(info.filename).stem: info
                for info in zf.infolist()
                if "/labels/" in info.filename and PurePosixPath(info.filename).suffix.lower() == ".txt"
            }
            image_members = [
                info
                for info in zf.infolist()
                if "/images/" in info.filename and PurePosixPath(info.filename).suffix.lower() in IMAGE_SUFFIXES
            ]
            for image_info in image_members:
                stem = PurePosixPath(image_info.filename).stem
                label_info = label_members.get(stem)
                if label_info is None:
                    raise GroupingError(f"{archive.name}: missing label for {image_info.filename}")
                image_bytes = zf.read(image_info)
                image_hash = hashlib.sha256(image_bytes).hexdigest()
                label_text = zf.read(label_info).decode("utf-8-sig")
                counts = _label_counts(label_text, source_names, canonical_names)
                group, confidence, basis = infer_source_group(archive, image_info.filename)
                split = proposed_split(group)
                duplicate_of = seen_hashes.get(image_hash, "")
                if duplicate_of:
                    assigned_split = "excluded_duplicate"
                else:
                    assigned_split = split
                    seen_hashes[image_hash] = f"{archive.name}::{image_info.filename}"
                    group_images[group] += 1
                    group_instances[group].update(counts)
                    split_images[split] += 1
                    split_instances[split].update(counts)
                rows.append(
                    {
                        "archive": archive.name,
                        "image_member": image_info.filename,
                        "label_member": label_info.filename,
                        "image_sha256": image_hash,
                        "source_group": group,
                        "group_confidence": confidence,
                        "group_basis": basis,
                        "proposed_split": assigned_split,
                        "duplicate_of": duplicate_of,
                        "instances": str(sum(counts.values())),
                    }
                )
                split_images[assigned_split] += 0 if not duplicate_of else 1

    group_splits: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if row["proposed_split"] != "excluded_duplicate":
            group_splits[row["source_group"]].add(row["proposed_split"])
    leaking = {group: sorted(splits) for group, splits in group_splits.items() if len(splits) != 1}
    if leaking:
        raise GroupingError(f"source groups cross proposed splits: {leaking}")

    minimum_instances_per_class = 20
    missing_classes = {
        split: [name for name in canonical_names if split_instances[split][name] == 0]
        for split in ("train", "valid", "test")
    }
    underpowered_classes = {
        split: {
            name: split_instances[split][name]
            for name in canonical_names
            if split_instances[split][name] < minimum_instances_per_class
        }
        for split in ("train", "valid", "test")
    }
    total_kept = sum(split_images[split] for split in ("train", "valid", "test"))
    plan = {
        "status": (
            "proposed_split_underpowered_requires_owner_review"
            if underpowered_classes["valid"] or underpowered_classes["test"]
            else "proposed_split_requires_owner_review"
        ),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_directory": str(source.resolve()),
        "source_archive_sha256": archive_hashes,
        "canonical_names": canonical_names,
        "source_groups": len(group_images),
        "images_by_split": {
            "train": split_images["train"],
            "valid": split_images["valid"],
            "test": split_images["test"],
            "excluded_duplicate": split_images["excluded_duplicate"],
        },
        "split_percentages": {
            split: round((split_images[split] / total_kept * 100), 3) if total_kept else 0
            for split in ("train", "valid", "test")
        },
        "instances_by_split": {
            split: {name: split_instances[split][name] for name in canonical_names}
            for split in ("train", "valid", "test")
        },
        "missing_classes_by_split": missing_classes,
        "minimum_instances_per_class": minimum_instances_per_class,
        "underpowered_classes_by_split": underpowered_classes,
        "policy": {
            "valid": "whole v9:baliwasan source group",
            "test": "whole v9:zppsu source group",
            "train": "all other approved groups",
            "fallback": "unknown external provenance is conservatively grouped by entire archive",
        },
        "raw_archives_modified": False,
        "merge_performed": False,
        "training_authorized": False,
        "owner_review_required": True,
    }

    output.mkdir(parents=True)
    with (output / "source_group_ledger.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=LEDGER_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    with (output / "source_group_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = ("source_group", "proposed_split", "images", *canonical_names)
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for group in sorted(group_images):
            split = next(iter(group_splits[group]))
            writer.writerow(
                {
                    "source_group": group,
                    "proposed_split": split,
                    "images": group_images[group],
                    **{name: group_instances[group][name] for name in canonical_names},
                }
            )
    (output / "split_plan.json").write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--class-schema", type=Path, required=True)
    args = parser.parse_args()
    try:
        schema = json.loads(args.class_schema.read_text(encoding="utf-8"))
        canonical_names = schema["object_classes"]
        if not isinstance(canonical_names, list):
            raise GroupingError("class schema object_classes must be a list")
        plan = build_plan(args.source.resolve(), args.output.resolve(), canonical_names)
    except (GroupingError, OSError, UnicodeError, zipfile.BadZipFile, json.JSONDecodeError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
