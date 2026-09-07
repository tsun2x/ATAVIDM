import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate_pickup_audit_package.py"


def _make_dataset(root: Path) -> None:
    selected = []
    for source, color in (("thai", "green"), ("mio", "yellow")):
        for index in range(2):
            name = f"{source}_{index}.jpg"
            image_path = root / "train" / "images" / name
            image_path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (200, 120), color).save(image_path)
            digest = hashlib.sha256(image_path.read_bytes()).hexdigest()
            label_path = root / "train" / "labels" / f"{source}_{index}.txt"
            label_path.parent.mkdir(parents=True, exist_ok=True)
            label_path.write_text("7 0.5 0.5 0.4 0.5\n0 0.2 0.2 0.1 0.1\n", encoding="utf-8")
            selected.append({"output_image": name, "source": source, "source_group": f"{source}-g", "image_sha256": digest, "manual_decision": "", "prescreen_suggestion": "manual_review", "prescreen_reasons": "", "pickup_instances": "1", "context_instances": "1", "max_pickup_area_pixels": "4800", "sharpness_score": "5", "selection_status": "selected_high_confidence", "selection_reason": "pass"})
    fields = list(selected[0])
    report = root / "reports" / "selected_candidates.csv"
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(selected)
    (root / "manifest.json").write_text(json.dumps({"status": "high_confidence_candidates_not_semantically_verified", "names": ["car", "van", "jeepney", "tricycle", "autorickshaw", "bus", "truck", "pickup_truck", "motorcycle", "bicycle"], "pickup_class_id": 7}), encoding="utf-8")


def test_generates_balanced_annotated_audit_and_blank_decision_ledger(tmp_path: Path) -> None:
    """Catches unbalanced sampling, missing rendered boxes, or pre-filled decisions."""
    dataset = tmp_path / "dataset"
    output = tmp_path / "audit"
    _make_dataset(dataset)

    result = subprocess.run([sys.executable, str(SCRIPT), "--dataset", str(dataset), "--output", str(output), "--per-source", "1", "--sheet-columns", "2", "--sheet-rows", "1"], text=True, capture_output=True)

    assert result.returncode == 0, result.stderr
    decisions = list(csv.DictReader((output / "audit_decisions.csv").open(encoding="utf-8")))
    assert len(decisions) == 2
    assert {row["source"] for row in decisions} == {"thai", "mio"}
    assert all(row["decision"] == "" and row["note"] == "" for row in decisions)
    assert {row["audit_id"] for row in decisions} == {"MIO-001", "THAI-001"}
    assert len(list((output / "annotated_images").glob("*.jpg"))) == 2
    sheet = Image.open(next((output / "contact_sheets").glob("*.jpg")))
    assert sheet.width > 400 and sheet.height > 120
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["sample_counts"] == {"mio": 1, "thai": 1}
    assert manifest["status"] == "pending_manual_semantic_review"
    assert manifest["allowed_decisions"] == ["keep", "reject", "uncertain"]


def test_fails_closed_when_selected_image_hash_has_changed(tmp_path: Path) -> None:
    """Catches rendering a different image than the selected provenance row names."""
    dataset = tmp_path / "dataset"
    output = tmp_path / "audit"
    _make_dataset(dataset)
    image = dataset / "train" / "images" / "thai_0.jpg"
    Image.new("RGB", (200, 120), "red").save(image)

    result = subprocess.run([sys.executable, str(SCRIPT), "--dataset", str(dataset), "--output", str(output), "--per-source", "1"], text=True, capture_output=True)

    assert result.returncode != 0
    assert "image hash mismatch" in result.stderr
    assert not output.exists()
