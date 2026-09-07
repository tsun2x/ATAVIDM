"""Audit source-video coverage before building a Philippine pickup gold set.

This command is deliberately read-only. It blocks dataset construction until
the available labels contain enough independent Philippine pickup videos to
keep representative pickup footage in training and held-out evaluation.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


class GoldSetError(RuntimeError):
    """Raised when available source groups cannot support a valid gold set."""


def audit_validated_group_assignments(validated: dict, assignments: dict[str, str]) -> dict:
    """Audit whole-source assignments from a validated Label Studio export."""
    if validated.get("status") != "validated_for_dataset_build_review":
        raise GoldSetError("annotation report has not passed validation")
    class_order = validated.get("class_order")
    if not isinstance(class_order, list) or len(class_order) <= 7 or class_order[7] != "pickup_truck":
        raise GoldSetError("validated report does not use the canonical pickup class id")
    source_groups = validated.get("source_groups")
    if not isinstance(source_groups, list) or len(source_groups) != len(set(source_groups)):
        raise GoldSetError("validated source groups are missing or duplicated")
    if set(assignments) != set(source_groups):
        raise GoldSetError("assignments must name every validated source group exactly once")
    allowed = {"train", "valid", "test"}
    if any(split not in allowed for split in assignments.values()):
        raise GoldSetError("assignment split must be train, valid, or test")
    groups = {split: sorted(group for group, assigned in assignments.items() if assigned == split) for split in ("train", "valid", "test")}
    if len(groups["train"]) < 2 or len(groups["valid"]) < 1 or len(groups["test"]) < 1:
        raise GoldSetError(f"assignments require 2 train, 1 valid, and 1 test source groups: {groups}")
    pickup_counts = {split: 0 for split in groups}
    for frame in validated.get("frames", []):
        group = frame.get("source_group")
        if group not in assignments:
            raise GoldSetError(f"frame references an unassigned source group: {group!r}")
        pickup_counts[assignments[group]] += sum(box.get("class_name") == "pickup_truck" for box in frame.get("boxes", []))
    if any(count < 1 for count in pickup_counts.values()):
        raise GoldSetError(f"pickup examples in every split are required: {pickup_counts}")
    return {
        "status": "ready_for_v6_dataset_build_review",
        "groups": groups,
        "group_counts": {split: len(values) for split, values in groups.items()},
        "pickup_instances": pickup_counts,
        "training_authorized": False,
        "next_gate": "Build a derived dataset with preserved source provenance, then obtain separate training approval.",
    }


PH_VIDEO_PATTERN = re.compile(
    r"(?:^|__)ph\d+_(?P<video>.+?)_mp4-\d+_jpg(?:\.rf\..+)?$",
    re.IGNORECASE,
)


def _pickup_video_groups(dataset: Path, split: str) -> list[str]:
    label_dir = dataset / split / "labels"
    if not label_dir.is_dir():
        raise GoldSetError(f"missing label directory: {label_dir}")
    groups: set[str] = set()
    for label_path in label_dir.glob("*.txt"):
        if not any(line.split(maxsplit=1)[0] == "7" for line in label_path.read_text(encoding="utf-8").splitlines() if line.strip()):
            continue
        match = PH_VIDEO_PATTERN.search(label_path.stem)
        if match:
            groups.add(match.group("video"))
    return sorted(groups)


def audit_pickup_video_groups(dataset: Path) -> dict:
    """Return the video-group plan or fail if it cannot support broad evaluation."""
    dataset = Path(dataset).resolve()
    groups = {split: _pickup_video_groups(dataset, split) for split in ("train", "valid", "test")}
    all_groups = set().union(*map(set, groups.values()))
    overlaps = {
        f"{left}_{right}": sorted(set(groups[left]) & set(groups[right]))
        for left, right in (("train", "valid"), ("train", "test"), ("valid", "test"))
    }
    if any(overlaps.values()):
        raise GoldSetError(f"pickup source-video leakage detected: {overlaps}")
    if len(all_groups) < 4:
        raise GoldSetError(
            "at least 4 independent Philippine pickup video groups are required "
            f"(2 train, 1 valid, 1 test); found {len(all_groups)}: {sorted(all_groups)}"
        )
    if len(groups["train"]) < 2 or not groups["valid"] or not groups["test"]:
        raise GoldSetError(f"pickup groups must include 2 train, 1 valid, and 1 test groups: {groups}")
    return {
        "status": "ready_for_manual_gold_review",
        "dataset": str(dataset),
        "pickup_class_id": 7,
        "groups": groups,
        "group_counts": {split: len(values) for split, values in groups.items()},
        "leakage": overlaps,
        "next_gate": "Manually verify every held-out pickup box and car/van hard negative before training.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        report = audit_pickup_video_groups(args.dataset)
    except GoldSetError as exc:
        print(f"BLOCKED: {exc}")
        return 2
    payload = json.dumps(report, indent=2, sort_keys=True)
    if args.report:
        args.report.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
