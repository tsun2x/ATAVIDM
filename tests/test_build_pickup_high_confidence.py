import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_pickup_high_confidence.py"
AUDIT_FIELDS = ["output_image", "source", "source_split", "source_group", "image_sha256", "manual_decision", "final_status", "reasons", "pickup_instances", "context_instances"]


def _csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _candidate(root: Path, name: str, source: str, color: tuple[int, int, int], label: str, manual: str = "") -> dict[str, str]:
    image = root / "train" / "images" / name
    image.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (320, 240), color).save(image)
    label_path = root / "train" / "labels" / f"{Path(name).stem}.txt"
    label_path.parent.mkdir(parents=True, exist_ok=True)
    label_path.write_text(label, encoding="utf-8")
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    return {"output_image": name, "source": source, "source_split": "train", "source_group": f"{source}-g", "image_sha256": digest, "manual_decision": manual, "final_status": "accepted_manual_keep" if manual == "keep" else "accepted_provisional", "reasons": "", "pickup_instances": "1", "context_instances": "1" if "\n0 " in f"\n{label}" else "0"}


def test_builds_balanced_clear_training_only_supplement_and_preserves_context(tmp_path: Path) -> None:
    """Catches selecting pre-screen rejects, truncated boxes, or dropping context boxes."""
    clean = tmp_path / "clean"
    rows = [
        _candidate(clean, "thai_clear.jpg", "thai", (220, 20, 20), "7 0.5 0.5 0.4 0.4\n0 0.2 0.2 0.1 0.1\n"),
        _candidate(clean, "thai_border.jpg", "thai", (20, 220, 20), "7 0.05 0.5 0.1 0.3\n"),
        _candidate(clean, "thai_manual.jpg", "thai", (20, 20, 220), "7 0.5 0.5 0.3 0.3\n", "keep"),
        _candidate(clean, "mio_clear.jpg", "mio", (180, 180, 20), "7 0.5 0.5 0.35 0.35\n"),
        _candidate(clean, "mio_reject.jpg", "mio", (20, 180, 180), "7 0.5 0.5 0.35 0.35\n"),
    ]
    _csv(clean / "reports" / "cleaning_audit.csv", AUDIT_FIELDS, rows)
    queue_fields = ["output_image", "source", "source_split", "source_member", "source_group", "image_sha256", "pickup_instances", "context_instances", "visual_review_status"]
    _csv(clean / "reports" / "review_queue_snapshot.csv", queue_fields, [
        {"output_image": row["output_image"], "source": row["source"], "source_split": "train", "source_member": row["output_image"], "source_group": row["source_group"], "image_sha256": row["image_sha256"], "pickup_instances": row["pickup_instances"], "context_instances": row["context_instances"], "visual_review_status": "pending"}
        for row in rows
    ])
    (clean / "manifest.json").write_text(json.dumps({"names": ["car", "van", "jeepney", "tricycle", "autorickshaw", "bus", "truck", "pickup_truck", "motorcycle", "bicycle"], "pickup_class_id": 7, "status": "provisional_not_approved_for_training"}), encoding="utf-8")
    prescreen_fields = ["output_image", "source", "image_sha256", "suggestion", "reasons"]
    _csv(tmp_path / "prescreen.csv", prescreen_fields, [
        {"output_image": row["output_image"], "source": row["source"], "image_sha256": row["image_sha256"], "suggestion": "suggest_reject" if row["output_image"] in {"mio_reject.jpg", "thai_manual.jpg"} else "manual_review", "reasons": "blur_suspect" if row["output_image"] in {"mio_reject.jpg", "thai_manual.jpg"} else ""}
        for row in rows
    ])
    output = tmp_path / "high_confidence"

    result = subprocess.run([sys.executable, str(SCRIPT), "--clean-dataset", str(clean), "--prescreen", str(tmp_path / "prescreen.csv"), "--output", str(output), "--per-source", "2", "--near-duplicate-distance", "0"], text=True, capture_output=True)

    assert result.returncode == 0, result.stderr
    selected = {path.name for path in (output / "train" / "images").iterdir()}
    assert selected == {"thai_clear.jpg", "thai_manual.jpg", "mio_clear.jpg"}
    assert (output / "train" / "labels" / "thai_clear.txt").read_text(encoding="utf-8") == "7 0.5 0.5 0.4 0.4\n0 0.2 0.2 0.1 0.1\n"
    assert not (output / "valid").exists()
    assert not (output / "test").exists()
    report = {row["output_image"]: row for row in csv.DictReader((output / "reports" / "selection_audit.csv").open(encoding="utf-8"))}
    assert report["thai_border.jpg"]["selection_status"] == "excluded_border_truncated"
    assert report["mio_reject.jpg"]["selection_status"] == "excluded_prescreen_quality"
    assert report["thai_manual.jpg"]["selection_status"] == "selected_manual_keep"
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "high_confidence_candidates_not_semantically_verified"
    assert manifest["training_only"] is True
    assert manifest["selected_by_source"] == {"mio": 1, "thai": 2}


def test_excludes_candidate_when_queue_annotation_counts_do_not_match_labels(tmp_path: Path) -> None:
    """Catches trusting stale queue counts instead of rejecting that candidate."""
    clean = tmp_path / "clean"
    row = _candidate(clean, "sample.jpg", "thai", (200, 20, 20), "7 0.5 0.5 0.4 0.4\n")
    _csv(clean / "reports" / "cleaning_audit.csv", AUDIT_FIELDS, [row])
    queue_fields = ["output_image", "source", "source_split", "source_member", "source_group", "image_sha256", "pickup_instances", "context_instances", "visual_review_status"]
    _csv(clean / "reports" / "review_queue_snapshot.csv", queue_fields, [{"output_image": "sample.jpg", "source": "thai", "source_split": "train", "source_member": "sample.jpg", "source_group": "g", "image_sha256": row["image_sha256"], "pickup_instances": "2", "context_instances": "0", "visual_review_status": "pending"}])
    (clean / "manifest.json").write_text(json.dumps({"names": ["car", "van", "jeepney", "tricycle", "autorickshaw", "bus", "truck", "pickup_truck", "motorcycle", "bicycle"], "pickup_class_id": 7, "status": "provisional_not_approved_for_training"}), encoding="utf-8")
    _csv(tmp_path / "prescreen.csv", ["output_image", "source", "image_sha256", "suggestion", "reasons"], [{"output_image": "sample.jpg", "source": "thai", "image_sha256": row["image_sha256"], "suggestion": "manual_review", "reasons": ""}])

    output = tmp_path / "output"
    result = subprocess.run([sys.executable, str(SCRIPT), "--clean-dataset", str(clean), "--prescreen", str(tmp_path / "prescreen.csv"), "--output", str(output)], text=True, capture_output=True)

    assert result.returncode == 0, result.stderr
    audit = list(csv.DictReader((output / "reports" / "selection_audit.csv").open(encoding="utf-8")))
    assert audit[0]["selection_status"] == "excluded_annotation_count_mismatch"
    assert not any((output / "train" / "images").iterdir())
