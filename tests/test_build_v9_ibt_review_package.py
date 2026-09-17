import json
import subprocess
import sys
import zipfile
from pathlib import Path

import cv2
import numpy as np

def _image_bytes(value: int) -> bytes:
    image = np.full((48, 64, 3), value, dtype=np.uint8)
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    return encoded.tobytes()


def _dataset(path: Path, names: list[str], labels: list[list[int]], base: int) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("data.yaml", f"names: {names!r}\n")
        for index, ids in enumerate(labels):
            stem = f"item_{index:03d}"
            archive.writestr(f"train/images/{stem}.jpg", _image_bytes(base + index * 10))
            archive.writestr(
                f"train/labels/{stem}.txt",
                "".join(f"{class_id} 0.5 0.5 0.4 0.4\n" for class_id in ids),
            )


def _video(path: Path, base: int) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 5.0, (64, 48))
    assert writer.isOpened()
    for index in range(25):
        writer.write(np.full((48, 64, 3), base + index, dtype=np.uint8))
    writer.release()


def test_builds_exact_image_only_package_with_provenance(tmp_path: Path) -> None:
    """Catches wrong quotas, label leakage into upload ZIP, or missing source provenance."""
    car_zip = tmp_path / "car.zip"
    truck_zip = tmp_path / "truck.zip"
    _dataset(car_zip, ["Bus", "Pickup", "Truck", "Van"], [[0], [1], [2], [3]], 20)
    _dataset(truck_zip, ["truck"], [[0], [0]], 180)
    videos = []
    for index in range(2):
        video = tmp_path / f"clip_{index}.avi"
        _video(video, 80 + index * 40)
        videos.append(video)

    output = tmp_path / "package"
    script = Path(__file__).resolve().parents[1] / "build_v9_ibt_review_package_v2.py"
    result = subprocess.run(
        [sys.executable, str(script), "--car-zip", str(car_zip), "--truck-zip", str(truck_zip),
         "--video-dir", str(tmp_path), "--video-pattern", "clip_*.avi", "--output", str(output),
         "--car-count", "4", "--truck-count", "2", "--frames-per-video", "2"],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["total_images"] == 10
    assert summary["source_counts"] == {"car_detection_v15": 4, "truck_v4": 2, "ibt_footbridge": 4}
    manifest = json.loads((output / "documentation_do_not_upload" / "manifest.json").read_text())
    assert len(manifest["items"]) == 10
    assert len({item["sha256"] for item in manifest["items"]}) == 10
    assert all(item["dataset_role"] == "development_only_not_gold" for item in manifest["items"])
    with zipfile.ZipFile(output / "roboflow_upload_images_10.zip") as archive:
        assert len(archive.namelist()) == 10
        assert all(name.lower().endswith(".jpg") for name in archive.namelist())


def test_rebuild_replaces_rejected_image_and_preserves_quota(tmp_path: Path) -> None:
    """Catches rejected images surviving a rebuild or replacement shrinking the package."""
    car_zip, truck_zip = tmp_path / "car.zip", tmp_path / "truck.zip"
    _dataset(car_zip, ["Bus", "Pickup", "Truck", "Van"], [[0], [1], [2], [3]], 20)
    _dataset(truck_zip, ["truck"], [[0], [0], [0]], 160)
    video = tmp_path / "clip.avi"
    _video(video, 90)
    first = tmp_path / "first"
    builder = Path(__file__).resolve().parents[1] / "build_v9_ibt_review_package_v2.py"
    build = subprocess.run(
        [sys.executable, str(builder), "--car-zip", str(car_zip), "--truck-zip", str(truck_zip),
         "--video-dir", str(tmp_path), "--video-pattern", "clip.avi", "--output", str(first),
         "--car-count", "4", "--truck-count", "2", "--frames-per-video", "2"], capture_output=True, text=True)
    assert build.returncode == 0, build.stdout + build.stderr
    rejected = "tavidm_v9_truck_v4_0001.jpg"
    reject_file = tmp_path / "reject.txt"
    reject_file.write_text(rejected + "\n")
    second = tmp_path / "second"
    remediation = Path(__file__).resolve().parents[1] / "rebuild_v9_review_replacements.py"
    result = subprocess.run(
        [sys.executable, str(remediation), "--source-package", str(first), "--truck-zip", str(truck_zip),
         "--reject-list", str(reject_file), "--output", str(second)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    old = json.loads((first / "documentation_do_not_upload" / "manifest.json").read_text())
    new = json.loads((second / "documentation_do_not_upload" / "manifest.json").read_text())
    old_hash = next(item["source_sha256"] for item in old["items"] if item["package_filename"] == rejected)
    assert len(new["items"]) == len(old["items"])
    assert old_hash not in {item["source_sha256"] for item in new["items"]}
    assert sum(item["source"] == "truck_v4" for item in new["items"]) == 2
