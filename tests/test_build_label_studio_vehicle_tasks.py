import json
import subprocess
import sys
from pathlib import Path

from PIL import Image


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_label_studio_vehicle_tasks.py"
SCHEMA = Path(__file__).resolve().parents[1] / "config" / "training" / "class_schema.json"


def _dataset(root: Path, class_id: int = 7) -> None:
    images = root / "valid" / "images"
    labels = root / "valid" / "labels"
    images.mkdir(parents=True)
    labels.mkdir(parents=True)
    stem = "vehicle_ph_v3_clean__ph20_EDSA-Orense-2_mp4-0027_jpg.rf.abc"
    Image.new("RGB", (200, 100), "white").save(images / f"{stem}.jpg")
    (labels / f"{stem}.txt").write_text(f"{class_id} 0.5 0.5 0.4 0.6\n", encoding="utf-8")


def test_builds_prediction_only_tasks_with_source_provenance(tmp_path: Path) -> None:
    """Catches accepted annotations, lost video identity, or a second class roster."""
    dataset = tmp_path / "dataset"
    output = tmp_path / "tasks.json"
    _dataset(dataset)

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--dataset", str(dataset), "--schema", str(SCHEMA), "--output", str(output)],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    tasks = json.loads(output.read_text(encoding="utf-8"))
    assert len(tasks) == 1
    task = tasks[0]
    assert "annotations" not in task
    assert task["data"]["source_group"] == "EDSA-Orense-2"
    assert task["data"]["split"] == "valid"
    assert task["data"]["review_required"] is True
    prediction = task["predictions"][0]
    assert prediction["model_version"] == "existing-yolo-labels-unverified"
    assert prediction["result"][0]["value"] == {
        "x": 30.0,
        "y": 20.0,
        "width": 40.0,
        "height": 60.0,
        "rotation": 0,
        "rectanglelabels": ["pickup_truck"],
    }
    assert task["data"]["class_order"] == [
        "car", "van", "jeepney", "tricycle", "autorickshaw",
        "bus", "truck", "pickup_truck", "motorcycle", "bicycle",
    ]


def test_rejects_out_of_range_yolo_class(tmp_path: Path) -> None:
    """Catches silently importing labels that violate the canonical roster."""
    dataset = tmp_path / "dataset"
    _dataset(dataset, class_id=10)

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--dataset", str(dataset), "--schema", str(SCHEMA), "--output", str(tmp_path / "tasks.json")],
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "class id out of range" in result.stderr
    assert not (tmp_path / "tasks.json").exists()


def test_attributed_only_skips_unattributable_sources_explicitly(tmp_path: Path) -> None:
    """Catches accidental inclusion of foreign/static images in the Philippine audit."""
    dataset = tmp_path / "dataset"
    _dataset(dataset)
    images = dataset / "train" / "images"
    labels = dataset / "train" / "labels"
    images.mkdir(parents=True)
    labels.mkdir(parents=True)
    Image.new("RGB", (100, 100), "black").save(images / "mio__0001.jpg")
    (labels / "mio__0001.txt").write_text("7 0.5 0.5 0.2 0.2\n", encoding="utf-8")
    output = tmp_path / "tasks.json"

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--dataset", str(dataset), "--schema", str(SCHEMA), "--output", str(output), "--attributed-only"],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    tasks = json.loads(output.read_text(encoding="utf-8"))
    assert len(tasks) == 1
    report = json.loads(result.stdout)
    assert report["skipped_unattributable"] == 1
