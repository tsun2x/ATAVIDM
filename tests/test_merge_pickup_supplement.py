import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "merge_pickup_supplement.py"
NAMES = ["car", "van", "jeepney", "tricycle", "autorickshaw", "bus", "truck", "pickup_truck", "motorcycle", "bicycle"]


def _pair(root: Path, split: str, name: str, color: str) -> str:
    image = root / split / "images" / name
    image.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (80, 60), color).save(image)
    label = root / split / "labels" / f"{Path(name).stem}.txt"
    label.parent.mkdir(parents=True, exist_ok=True)
    label.write_text("7 0.5 0.5 0.4 0.4\n0 0.2 0.2 0.1 0.1\n", encoding="utf-8")
    return hashlib.sha256(image.read_bytes()).hexdigest()


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    base, supplement, approval = tmp_path / "base", tmp_path / "supplement", tmp_path / "approval"
    _pair(base, "train", "base_train.jpg", "red")
    duplicate_hash = _pair(base, "valid", "base_valid.jpg", "green")
    _pair(base, "test", "base_test.jpg", "blue")
    base.joinpath("data.yaml").write_text("path: .\ntrain: train/images\nval: valid/images\ntest: test/images\nnames:\n" + "".join(f"  {i}: {name}\n" for i, name in enumerate(NAMES)), encoding="utf-8")
    unique_hash = _pair(supplement, "train", "unique.jpg", "yellow")
    duplicate = supplement / "train" / "images" / "duplicate.jpg"
    duplicate.write_bytes((base / "valid" / "images" / "base_valid.jpg").read_bytes())
    (supplement / "train" / "labels" / "duplicate.txt").write_text("7 0.5 0.5 0.4 0.4\n", encoding="utf-8")
    (supplement / "manifest.json").write_text(json.dumps({"status": "high_confidence_candidates_not_semantically_verified", "training_only": True, "names": NAMES, "pickup_class_id": 7}), encoding="utf-8")
    fields = ["output_image", "source", "image_sha256"]
    supplement.joinpath("reports").mkdir()
    with (supplement / "reports" / "selected_candidates.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows([
            {"output_image": "unique.jpg", "source": "mio", "image_sha256": unique_hash},
            {"output_image": "duplicate.jpg", "source": "thai", "image_sha256": duplicate_hash},
        ])
    approval.mkdir()
    (approval / "approval_manifest.json").write_text(json.dumps({"status": "owner_semantic_sample_audit_passed", "decision_counts": {"keep": 2}, "merge_authorized": False, "training_authorized": False}), encoding="utf-8")
    return base, supplement, approval


def test_merges_unique_supplement_into_train_and_preserves_evaluation_splits(tmp_path: Path) -> None:
    """Catches evaluation mutation or cross-split duplicate inclusion."""
    base, supplement, approval = _fixture(tmp_path)
    output = tmp_path / "merged"
    valid_before = (base / "valid" / "images" / "base_valid.jpg").read_bytes()
    test_before = (base / "test" / "labels" / "base_test.txt").read_bytes()

    result = subprocess.run([sys.executable, str(SCRIPT), "--base", str(base), "--supplement", str(supplement), "--approval", str(approval), "--output", str(output), "--owner-approved"], text=True, capture_output=True)

    assert result.returncode == 0, result.stderr
    assert (output / "train" / "images" / "pickup_hc_v1__unique.jpg").exists()
    assert not (output / "train" / "images" / "pickup_hc_v1__duplicate.jpg").exists()
    assert (output / "valid" / "images" / "base_valid.jpg").read_bytes() == valid_before
    assert (output / "test" / "labels" / "base_test.txt").read_bytes() == test_before
    audit = {row["output_image"]: row for row in csv.DictReader((output / "reports" / "pickup_merge_audit.csv").open(encoding="utf-8"))}
    assert audit["unique.jpg"]["merge_status"] == "merged_train_only"
    assert audit["duplicate.jpg"]["merge_status"] == "excluded_exact_duplicate_valid"
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "merged_dataset_ready_for_training_review"
    assert manifest["training_authorized"] is False
    assert manifest["supplement_merged"] == 1


def test_refuses_merge_without_explicit_owner_approval_flag(tmp_path: Path) -> None:
    """Catches treating audit completion alone as merge authorization."""
    base, supplement, approval = _fixture(tmp_path)
    output = tmp_path / "merged"
    result = subprocess.run([sys.executable, str(SCRIPT), "--base", str(base), "--supplement", str(supplement), "--approval", str(approval), "--output", str(output)], text=True, capture_output=True)
    assert result.returncode != 0
    assert not output.exists()
