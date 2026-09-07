import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "clean_pickup_candidates.py"


def _image(path: Path, color: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 48), color).save(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_cleaner_preserves_manual_decisions_and_rejects_only_structural_errors(tmp_path: Path) -> None:
    """Catches a cleaner that overrides review decisions or accepts malformed pairs."""
    candidates = tmp_path / "candidates"
    benchmark = tmp_path / "benchmark"
    output = tmp_path / "pickup_clean_v1"
    images = candidates / "train" / "images"
    labels = candidates / "train" / "labels"
    labels.mkdir(parents=True)

    hashes = {
        "thai_keep.jpg": _image(images / "thai_keep.jpg", "red"),
        "thai_uncertain.jpg": _image(images / "thai_uncertain.jpg", "blue"),
        "mio_valid.jpg": _image(images / "mio_valid.jpg", "green"),
        "mio_bad.jpg": _image(images / "mio_bad.jpg", "yellow"),
    }
    (labels / "thai_keep.txt").write_text("7 0.5 0.5 0.4 0.4\n", encoding="utf-8")
    (labels / "thai_uncertain.txt").write_text("7 0.5 0.5 0.4 0.4\n", encoding="utf-8")
    (labels / "mio_valid.txt").write_text("7 0.5 0.5 0.4 0.4\n0 0.2 0.2 0.1 0.1\n", encoding="utf-8")
    (labels / "mio_bad.txt").write_text("7 1.2 0.5 0.4 0.4\n", encoding="utf-8")

    queue_fields = ["output_image", "source", "source_split", "source_member", "source_group", "image_sha256", "pickup_instances", "context_instances", "visual_review_status"]
    _write_csv(candidates / "review_queue.csv", queue_fields, [
        {"output_image": name, "source": source, "source_split": "train", "source_member": name, "source_group": group, "image_sha256": hashes[name], "pickup_instances": "1", "context_instances": "0", "visual_review_status": "pending"}
        for name, source, group in [
            ("thai_keep.jpg", "thai", "thai-g1"),
            ("thai_uncertain.jpg", "thai", "thai-g2"),
            ("mio_valid.jpg", "mio", "mio-g1"),
            ("mio_bad.jpg", "mio", "mio-g2"),
        ]
    ])
    decision_fields = ["output_image", "source", "image_sha256", "decision", "reviewed_at", "note"]
    _write_csv(candidates / "review_decisions.csv", decision_fields, [
        {"output_image": "thai_keep.jpg", "source": "thai", "image_sha256": hashes["thai_keep.jpg"], "decision": "keep", "reviewed_at": "2026-08-31T13:00:00+08:00", "note": "manual"},
        {"output_image": "thai_uncertain.jpg", "source": "thai", "image_sha256": hashes["thai_uncertain.jpg"], "decision": "uncertain", "reviewed_at": "2026-08-31T13:01:00+08:00", "note": "manual"},
    ])
    (candidates / "manifest.json").write_text(json.dumps({"names": ["car", "van", "jeepney", "tricycle", "autorickshaw", "bus", "truck", "pickup_truck", "motorcycle", "bicycle"], "pickup_class_id": 7}), encoding="utf-8")

    for split, color in (("valid", "purple"), ("test", "orange")):
        _image(benchmark / split / "images" / f"{split}.jpg", color)
        label = benchmark / split / "labels" / f"{split}.txt"
        label.parent.mkdir(parents=True, exist_ok=True)
        label.write_text("7 0.5 0.5 0.4 0.4\n", encoding="utf-8")
    (benchmark / "data.yaml").write_text("names: [car, van, jeepney, tricycle, autorickshaw, bus, truck, pickup_truck, motorcycle, bicycle]\n", encoding="utf-8")

    result = subprocess.run([
        sys.executable, str(SCRIPT), "--candidates", str(candidates),
        "--benchmark", str(benchmark), "--output", str(output),
        "--audit-per-source", "1",
    ], text=True, capture_output=True)

    assert result.returncode == 0, result.stderr
    assert (output / "train" / "images" / "thai_keep.jpg").exists()
    assert (output / "train" / "images" / "mio_valid.jpg").exists()
    assert not (output / "train" / "images" / "thai_uncertain.jpg").exists()
    assert not (output / "train" / "images" / "mio_bad.jpg").exists()
    assert (output / "valid" / "images" / "valid.jpg").read_bytes() == (benchmark / "valid" / "images" / "valid.jpg").read_bytes()
    assert (output / "test" / "labels" / "test.txt").read_bytes() == (benchmark / "test" / "labels" / "test.txt").read_bytes()

    audit = {row["output_image"]: row for row in csv.DictReader((output / "reports" / "cleaning_audit.csv").open(encoding="utf-8"))}
    assert audit["thai_keep.jpg"]["final_status"] == "accepted_manual_keep"
    assert audit["thai_uncertain.jpg"]["final_status"] == "excluded_manual_uncertain"
    assert audit["mio_valid.jpg"]["final_status"] == "accepted_provisional"
    assert audit["mio_bad.jpg"]["final_status"] == "culled_structural_error"
    assert audit["mio_bad.jpg"]["reasons"] == "label_coordinate_out_of_range"

    sample = list(csv.DictReader((output / "reports" / "source_balanced_audit_sample.csv").open(encoding="utf-8")))
    assert {row["source"] for row in sample} == {"thai", "mio"}
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "provisional_not_approved_for_training"
    assert manifest["counts"]["accepted_manual_keep"] == 1
    assert manifest["counts"]["accepted_provisional"] == 1
    assert manifest["counts"]["culled_structural_error"] == 1
    assert manifest["counts"]["excluded_manual_uncertain"] == 1


def test_cleaner_fails_closed_on_stale_manual_decision_hash(tmp_path: Path) -> None:
    """Catches applying a decision to different image bytes with the same filename."""
    candidates = tmp_path / "candidates"
    benchmark = tmp_path / "benchmark"
    output = tmp_path / "output"
    digest = _image(candidates / "train" / "images" / "sample.jpg", "red")
    label = candidates / "train" / "labels" / "sample.txt"
    label.parent.mkdir(parents=True)
    label.write_text("7 0.5 0.5 0.4 0.4\n", encoding="utf-8")
    _write_csv(candidates / "review_queue.csv", ["output_image", "source", "source_split", "source_member", "source_group", "image_sha256", "pickup_instances", "context_instances", "visual_review_status"], [{"output_image": "sample.jpg", "source": "thai", "source_split": "train", "source_member": "sample.jpg", "source_group": "g", "image_sha256": digest, "pickup_instances": "1", "context_instances": "0", "visual_review_status": "pending"}])
    _write_csv(candidates / "review_decisions.csv", ["output_image", "source", "image_sha256", "decision", "reviewed_at", "note"], [{"output_image": "sample.jpg", "source": "thai", "image_sha256": "0" * 64, "decision": "keep", "reviewed_at": "now", "note": ""}])
    (candidates / "manifest.json").write_text(json.dumps({"names": ["car", "van", "jeepney", "tricycle", "autorickshaw", "bus", "truck", "pickup_truck", "motorcycle", "bicycle"], "pickup_class_id": 7}), encoding="utf-8")
    for split in ("valid", "test"):
        (benchmark / split / "images").mkdir(parents=True)
        (benchmark / split / "labels").mkdir(parents=True)
    (benchmark / "data.yaml").write_text("names: []\n", encoding="utf-8")

    result = subprocess.run([sys.executable, str(SCRIPT), "--candidates", str(candidates), "--benchmark", str(benchmark), "--output", str(output)], text=True, capture_output=True)

    assert result.returncode != 0
    assert "manual decision hash mismatch" in result.stderr
    assert not output.exists()
