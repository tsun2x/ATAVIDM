import hashlib
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "select_cctv_frames.py"


def _video(path: Path) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 5.0, (64, 48))
    assert writer.isOpened()
    frames = [
        np.full((48, 64, 3), 10, dtype=np.uint8),
        np.full((48, 64, 3), 10, dtype=np.uint8),
        np.full((48, 64, 3), 90, dtype=np.uint8),
        np.full((48, 64, 3), 90, dtype=np.uint8),
        np.full((48, 64, 3), 180, dtype=np.uint8),
    ]
    for frame in frames:
        writer.write(frame)
    writer.release()


def test_extracts_attributable_deduplicated_frames_and_manifest(tmp_path: Path) -> None:
    """Catches loss of provenance, duplicate retention, or source mutation."""
    source = tmp_path / "camera.avi"
    output = tmp_path / "selected"
    _video(source)
    before = hashlib.sha256(source.read_bytes()).hexdigest()

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--video", str(source), "--source-group", "barangay-a-cam01-session01", "--output", str(output), "--every-frames", "1", "--near-duplicate-distance", "0"],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_group"] == "barangay-a-cam01-session01"
    assert manifest["source_video_sha256"] == before
    assert manifest["sampled_frames"] == 5
    assert manifest["selected_frames"] == 3
    assert manifest["duplicates_rejected"] == 2
    files = sorted((output / "images").glob("*.jpg"))
    assert [path.name for path in files] == [
        "barangay-a-cam01-session01__frame_00000000__ms_000000000000.jpg",
        "barangay-a-cam01-session01__frame_00000002__ms_000000000400.jpg",
        "barangay-a-cam01-session01__frame_00000004__ms_000000000800.jpg",
    ]
    assert all(row["sha256"] == hashlib.sha256((output / row["relative_path"]).read_bytes()).hexdigest() for row in manifest["frames"])


def test_refuses_to_overwrite_existing_output(tmp_path: Path) -> None:
    """Catches a rerun replacing a previous frame-selection package."""
    source = tmp_path / "camera.avi"
    output = tmp_path / "selected"
    _video(source)
    output.mkdir()

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--video", str(source), "--source-group", "cam01", "--output", str(output)],
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "refusing to overwrite" in result.stderr
