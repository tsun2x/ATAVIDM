"""Fail-closed contracts for Philippine pickup video-group gold-set planning."""

from pathlib import Path

import pytest

from scripts.build_pickup_video_gold import (
    GoldSetError,
    audit_pickup_video_groups,
    audit_validated_group_assignments,
)


def _write_pickup_frame(root: Path, split: str, video: str, frame: int) -> None:
    images = root / split / "images"
    labels = root / split / "labels"
    images.mkdir(parents=True, exist_ok=True)
    labels.mkdir(parents=True, exist_ok=True)
    stem = f"vehicle_ph_v3_clean__ph20_{video}_mp4-{frame:04d}_jpg.rf.deadbeef"
    (images / f"{stem}.jpg").write_bytes(b"image")
    (labels / f"{stem}.txt").write_text("7 0.5 0.5 0.2 0.2\n", encoding="utf-8")


def test_audit_rejects_three_video_groups_as_too_narrow(tmp_path: Path) -> None:
    """Catches a builder that accepts the current three-video benchmark as broad."""
    _write_pickup_frame(tmp_path, "train", "Mandaluyong", 1)
    _write_pickup_frame(tmp_path, "valid", "EDSA-Orense-2", 1)
    _write_pickup_frame(tmp_path, "test", "EDSA-Orense-1", 1)

    with pytest.raises(GoldSetError, match="at least 4 independent Philippine pickup video groups"):
        audit_pickup_video_groups(tmp_path)


def test_audit_accepts_two_training_and_two_held_out_groups(tmp_path: Path) -> None:
    """Catches grouping by frame rather than by original source video."""
    for video in ("Mandaluyong", "Quezon-Ave"):
        _write_pickup_frame(tmp_path, "train", video, 1)
        _write_pickup_frame(tmp_path, "train", video, 2)
    _write_pickup_frame(tmp_path, "valid", "EDSA-Orense-2", 1)
    _write_pickup_frame(tmp_path, "test", "EDSA-Orense-1", 1)

    report = audit_pickup_video_groups(tmp_path)

    assert report["status"] == "ready_for_manual_gold_review"
    assert report["group_counts"] == {"train": 2, "valid": 1, "test": 1}
    assert report["groups"]["train"] == ["Mandaluyong", "Quezon-Ave"]


def test_validated_assignment_requires_pickups_in_each_split() -> None:
    """Catches a four-group split whose held-out data cannot evaluate pickups."""
    validated = {
        "status": "validated_for_dataset_build_review",
        "class_order": ["car", "van", "jeepney", "tricycle", "autorickshaw", "bus", "truck", "pickup_truck", "motorcycle", "bicycle"],
        "source_groups": ["train-a", "train-b", "valid-a", "test-a"],
        "frames": [
            {"source_group": group, "boxes": ([{"class_name": "pickup_truck"}] if group != "test-a" else [{"class_name": "car"}])}
            for group in ("train-a", "train-b", "valid-a", "test-a")
        ],
    }
    assignments = {"train-a": "train", "train-b": "train", "valid-a": "valid", "test-a": "test"}

    with pytest.raises(GoldSetError, match="pickup examples in every split"):
        audit_validated_group_assignments(validated, assignments)


def test_validated_assignment_accepts_two_train_one_valid_one_test() -> None:
    """Catches partial or image-level assignments bypassing the whole-group gate."""
    groups = ("train-a", "train-b", "valid-a", "test-a")
    validated = {
        "status": "validated_for_dataset_build_review",
        "class_order": ["car", "van", "jeepney", "tricycle", "autorickshaw", "bus", "truck", "pickup_truck", "motorcycle", "bicycle"],
        "source_groups": list(groups),
        "frames": [{"source_group": group, "boxes": [{"class_name": "pickup_truck"}]} for group in groups],
    }
    assignments = {"train-a": "train", "train-b": "train", "valid-a": "valid", "test-a": "test"}

    report = audit_validated_group_assignments(validated, assignments)

    assert report["status"] == "ready_for_v6_dataset_build_review"
    assert report["group_counts"] == {"train": 2, "valid": 1, "test": 1}
