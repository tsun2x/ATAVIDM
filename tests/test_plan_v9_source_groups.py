from pathlib import Path
import csv
import json
import zipfile

import pytest

from scripts.plan_v9_source_groups import GroupingError, build_plan, infer_source_group, proposed_split


def test_v9_known_sources_are_grouped_by_controlled_source_prefix() -> None:
    # Defect caught: individual numbered frames could be split independently and leak one source.
    archive = Path("TAVIDM V9 Development Review.v3-v9-development-review-raw-20260923.yolov8.zip")

    assert infer_source_group(
        archive,
        "train/images/tavidm_v9_baliwasan_0141_jpg.rf.c4050a6b3a5f974df459de1a842180e0.jpg",
    ) == ("v9:baliwasan", "high", "controlled_v9_filename_prefix")
    assert infer_source_group(
        archive,
        "train/images/tavidm_v9_truck_v4_0050_jpg.rf.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg",
    ) == ("v9:truck_v4", "high", "controlled_v9_filename_prefix")


def test_cctv_frames_from_one_xvr_recording_stay_in_one_group() -> None:
    # Defect caught: frame numbers from one video could be mistaken for independent sources.
    archive = Path("TAVIDM staging helmet.v1-staging-helmet-raw-20260923.yolov8.zip")

    assert infer_source_group(
        archive,
        "train/images/XVR_ch9_main_20221004170008_20221004180008_5Mins_mp4-625_jpg.rf.abc.jpg",
    ) == (
        "cctv:XVR_ch9_main_20221004170008_20221004180008_5Mins",
        "high",
        "video_frame_filename",
    )


def test_unknown_external_provenance_falls_back_to_whole_archive_group() -> None:
    # Defect caught: weak filename guesses could split an unproven external source across partitions.
    archive = Path("TAVIDM Staging Bicycle.v1-staging-bicycle-raw-20260923.yolov8.zip")

    group, confidence, basis = infer_source_group(
        archive,
        "train/images/bicycle-153-_jpg.rf.8552f80e7446afffa6cb3888e8bd134e.jpg",
    )

    assert group == "archive:tavidm-staging-bicycle"
    assert confidence == "conservative"
    assert basis == "archive_fallback_missing_source_manifest"


@pytest.mark.parametrize(
    ("group", "expected"),
    [
        ("v9:baliwasan", "valid"),
        ("v9:zppsu", "test"),
        ("v9:ibt_footbridge", "train"),
        ("archive:tavidm-staging-bicycle", "train"),
        ("cctv:XVR_ch9_main_20221004170008_20221004180008_5Mins", "train"),
    ],
)
def test_proposed_split_keeps_whole_groups_together(group: str, expected: str) -> None:
    assert proposed_split(group) == expected


def test_unknown_v9_group_fails_closed_instead_of_silently_entering_train() -> None:
    with pytest.raises(GroupingError, match="unapproved V9 source group"):
        proposed_split("v9:new_source")


def test_build_plan_writes_group_ledger_and_excludes_exact_duplicate(tmp_path: Path) -> None:
    # Defect caught: duplicate pixels could survive under different Roboflow filenames.
    source = tmp_path / "source"
    source.mkdir()
    archive = source / "TAVIDM V9 Development Review.v3-v9-development-review-raw-20260923.yolov8.zip"
    yaml = "names: ['car', 'person']\n"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("data.yaml", yaml)
        zf.writestr("train/images/tavidm_v9_baliwasan_0001_jpg.rf.aaa.jpg", b"same-image")
        zf.writestr("train/labels/tavidm_v9_baliwasan_0001_jpg.rf.aaa.txt", "0 0.5 0.5 0.2 0.2\n")
        zf.writestr("train/images/tavidm_v9_baliwasan_0002_jpg.rf.bbb.jpg", b"same-image")
        zf.writestr("train/labels/tavidm_v9_baliwasan_0002_jpg.rf.bbb.txt", "0 0.5 0.5 0.2 0.2\n")
        zf.writestr("train/images/tavidm_v9_zppsu_0001_jpg.rf.ccc.jpg", b"different-image")
        zf.writestr("train/labels/tavidm_v9_zppsu_0001_jpg.rf.ccc.txt", "1 0.5 0.5 0.2 0.2\n")

    output = tmp_path / "plan"
    result = build_plan(source, output, ["car", "person"])

    assert result["status"] == "proposed_split_underpowered_requires_owner_review"
    assert result["images_by_split"] == {"train": 0, "valid": 1, "test": 1, "excluded_duplicate": 1}
    assert result["minimum_instances_per_class"] == 20
    assert result["underpowered_classes_by_split"]["valid"] == {"car": 1, "person": 0}
    assert result["underpowered_classes_by_split"]["test"] == {"car": 0, "person": 1}
    with (output / "source_group_ledger.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["proposed_split"] for row in rows] == ["valid", "excluded_duplicate", "test"]
    assert rows[0]["source_group"] == rows[1]["source_group"] == "v9:baliwasan"
    assert json.loads((output / "split_plan.json").read_text(encoding="utf-8"))["merge_performed"] is False


def test_build_plan_refuses_to_overwrite_existing_output(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    output = tmp_path / "plan"
    output.mkdir()

    with pytest.raises(GroupingError, match="output already exists"):
        build_plan(source, output, ["car"])
