import json
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "train_vehicle_ph_v5_pickup.py"


def test_preflight_reports_fresh_configuration_without_creating_run(tmp_path: Path) -> None:
    data = tmp_path / "dataset" / "data.yaml"; data.parent.mkdir(); data.write_text("names: {}\n")
    checkpoint = tmp_path / "best.pt"; checkpoint.write_bytes(b"checkpoint")
    result = subprocess.run([sys.executable, str(SCRIPT), "--root", str(tmp_path), "--data", str(data), "--initial-checkpoint", str(checkpoint), "--preflight-only"], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["mode"] == "fresh"
    assert payload["batch"] == 6
    assert payload["epochs"] == 60
    assert not (tmp_path / "runs" / "vehicle_ph_v5_pickup_yolov8m").exists()


def test_resume_fails_closed_without_last_checkpoint(tmp_path: Path) -> None:
    result = subprocess.run([sys.executable, str(SCRIPT), "--root", str(tmp_path), "--resume", "--preflight-only"], text=True, capture_output=True)
    assert result.returncode != 0
    assert "Resume checkpoint not found" in result.stderr
