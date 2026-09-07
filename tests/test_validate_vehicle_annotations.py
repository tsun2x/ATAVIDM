import json
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_vehicle_annotations.py"
SCHEMA = Path(__file__).resolve().parents[1] / "config" / "training" / "class_schema.json"


def _manifest(path: Path, source_group: str = "cam01", digest: str = "a" * 64) -> None:
    path.write_text(json.dumps({
        "status": "selected_frames_pending_annotation",
        "source_group": source_group,
        "frames": [{
            "frame_index": 0,
            "timestamp_ms": 0,
            "relative_path": f"images/{source_group}__frame_00000000__ms_000000000000.jpg",
            "sha256": digest,
        }],
    }), encoding="utf-8")


def _export(path: Path, source_group: str = "cam01", state: str = "Review complete") -> None:
    name = f"{source_group}__frame_00000000__ms_000000000000.jpg"
    path.write_text(json.dumps([{
        "data": {"image_name": name, "source_group": source_group},
        "annotations": [{
            "was_cancelled": False,
            "result": [
                {"from_name": "vehicle", "to_name": "image", "type": "rectanglelabels", "value": {"x": 10, "y": 20, "width": 30, "height": 40, "rotation": 0, "rectanglelabels": ["pickup_truck"]}},
                {"from_name": "review_state", "to_name": "image", "type": "choices", "value": {"choices": [state]}},
            ],
        }],
    }]), encoding="utf-8")


def test_accepts_complete_review_and_writes_validated_ledger(tmp_path: Path) -> None:
    """Catches losing human-review state, class mapping, or source provenance."""
    manifest = tmp_path / "manifest.json"
    export = tmp_path / "export.json"
    output = tmp_path / "validated.json"
    _manifest(manifest)
    _export(export)

    result = subprocess.run([sys.executable, str(SCRIPT), "--export", str(export), "--manifest", str(manifest), "--schema", str(SCHEMA), "--output", str(output)], text=True, capture_output=True)

    assert result.returncode == 0, result.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "validated_for_dataset_build_review"
    assert report["source_groups"] == ["cam01"]
    assert report["reviewed_frames"] == 1
    assert report["class_counts"] == {"pickup_truck": 1}
    assert report["frames"][0]["boxes"][0]["class_id"] == 7
    assert report["training_authorized"] is False


def test_rejects_uncertain_review_state(tmp_path: Path) -> None:
    """Catches uncertain objects being silently converted into ground truth."""
    manifest = tmp_path / "manifest.json"
    export = tmp_path / "export.json"
    _manifest(manifest)
    _export(export, state="Contains uncertain vehicle")

    result = subprocess.run([sys.executable, str(SCRIPT), "--export", str(export), "--manifest", str(manifest), "--schema", str(SCHEMA), "--output", str(tmp_path / "validated.json")], text=True, capture_output=True)

    assert result.returncode != 0
    assert "unresolved uncertain vehicle" in result.stderr
    assert not (tmp_path / "validated.json").exists()


def test_rejects_duplicate_content_across_source_groups(tmp_path: Path) -> None:
    """Catches the same frame entering multiple video groups."""
    one = tmp_path / "one.json"
    two = tmp_path / "two.json"
    export = tmp_path / "export.json"
    _manifest(one, "cam01", "b" * 64)
    _manifest(two, "cam02", "b" * 64)
    _export(export, "cam01")

    result = subprocess.run([sys.executable, str(SCRIPT), "--export", str(export), "--manifest", str(one), "--manifest", str(two), "--schema", str(SCHEMA), "--output", str(tmp_path / "validated.json")], text=True, capture_output=True)

    assert result.returncode != 0
    assert "duplicate frame content" in result.stderr
