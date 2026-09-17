import json
import subprocess
import sys
import zipfile
from pathlib import Path

import cv2
import numpy as np


def _jpg(value: int) -> bytes:
    image = np.full((48, 64, 3), value, dtype=np.uint8)
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    return encoded.tobytes()


def _dataset(path: Path, names: list[str], class_ids: list[list[int]], base: int) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("data.yaml", f"names: {names!r}\n")
        for index, ids in enumerate(class_ids):
            stem = f"item_{index:03d}"
            archive.writestr(f"train/images/{stem}.jpg", _jpg(base + index * 15))
            archive.writestr(
                f"train/labels/{stem}.txt",
                "".join(f"{class_id} 0.5 0.5 0.3 0.3\n" for class_id in ids),
            )


def test_builds_image_only_unmapped_review_supplement(tmp_path: Path) -> None:
    """Catches source labels leaking into Roboflow or unsafe helmet auto-mapping."""
    bicycle = tmp_path / "bicycle.zip"
    vehicle = tmp_path / "vehicle.zip"
    helmet = tmp_path / "helmet.zip"
    baseline = tmp_path / "baseline.zip"
    _dataset(bicycle, ["bicycle"], [[0], [0]], 20)
    _dataset(vehicle, ["bus", "van"], [[0], [1]], 80)
    _dataset(helmet, ["invalid"], [[0]], 150)
    _dataset(baseline, ["car"], [[0]], 230)

    output = tmp_path / "supplement"
    script = Path(__file__).resolve().parents[1] / "build_v9_external_review_supplement.py"
    result = subprocess.run(
        [sys.executable, str(script), "--bicycle-zip", str(bicycle),
         "--vehicle-zip", str(vehicle), "--helmet-zip", str(helmet),
         "--baseline-zip", str(baseline), "--output", str(output),
         "--bicycle-count", "2", "--bus-count", "1", "--van-count", "1",
         "--helmet-count", "1", "--near-duplicate-distance", "0"],
        text=True, capture_output=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads(result.stdout)
    assert summary["total_images"] == 5
    assert summary["selection_counts"] == {
        "bicycle": 2, "bus": 1, "van": 1, "helmet_manual_review": 1
    }
    manifest = json.loads(
        (output / "documentation_do_not_upload" / "manifest.json").read_text()
    )
    helmet_row = next(x for x in manifest["items"] if x["selection_target"] == "helmet_manual_review")
    assert helmet_row["candidate_classes"] == []
    assert helmet_row["source_labels"] == ["invalid"]
    assert helmet_row["semantic_mapping_authorized"] is False
    assert manifest["training_authorized"] is False
    with zipfile.ZipFile(output / "roboflow_upload_images_5.zip") as archive:
        assert len(archive.namelist()) == 5
        assert all(name.lower().endswith(".jpg") for name in archive.namelist())
        assert not any("label" in name.lower() or name.endswith(".txt") for name in archive.namelist())


def test_rejects_polygon_rows_in_source_annotations(tmp_path: Path) -> None:
    """Catches incompatible segmentation rows silently entering candidate selection."""
    bicycle = tmp_path / "bicycle.zip"
    vehicle = tmp_path / "vehicle.zip"
    helmet = tmp_path / "helmet.zip"
    baseline = tmp_path / "baseline.zip"
    _dataset(bicycle, ["bicycle"], [[0]], 20)
    _dataset(vehicle, ["bus", "van"], [[0], [1]], 80)
    _dataset(helmet, ["invalid"], [[0]], 150)
    _dataset(baseline, ["car"], [[0]], 230)
    with zipfile.ZipFile(bicycle, "a") as archive:
        archive.writestr("train/labels/item_000.txt", "0 0.1 0.1 0.9 0.1 0.9 0.9\n")

    script = Path(__file__).resolve().parents[1] / "build_v9_external_review_supplement.py"
    result = subprocess.run(
        [sys.executable, str(script), "--bicycle-zip", str(bicycle),
         "--vehicle-zip", str(vehicle), "--helmet-zip", str(helmet),
         "--baseline-zip", str(baseline), "--output", str(tmp_path / "out"),
         "--bicycle-count", "1", "--bus-count", "1", "--van-count", "1",
         "--helmet-count", "1", "--near-duplicate-distance", "0"],
        text=True, capture_output=True,
    )

    assert result.returncode == 2
    assert "invalid YOLO detection row" in result.stdout
