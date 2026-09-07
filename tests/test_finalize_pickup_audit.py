import csv
import json
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "finalize_pickup_audit.py"
FIELDS = ["audit_id", "output_image", "source", "image_sha256", "decision", "note"]


def _audit(root: Path, changed_sample: bool = False) -> None:
    rows = [
        {"audit_id": "MIO-001", "output_image": "mio.jpg", "source": "mio", "image_sha256": "a" * 64, "decision": "", "note": ""},
        {"audit_id": "THAI-001", "output_image": "thai.jpg", "source": "thai", "image_sha256": "b" * 64, "decision": "", "note": ""},
    ]
    root.mkdir(parents=True)
    with (root / "audit_decisions.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader(); writer.writerows(rows)
    with (root / "audit_sample.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = ["audit_id", "output_image", "source", "image_sha256", "annotated_image"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({**{key: row[key] for key in fields if key != "annotated_image"}, "image_sha256": ("c" * 64 if changed_sample and row["audit_id"] == "MIO-001" else row["image_sha256"]), "annotated_image": row["audit_id"] + ".jpg"})
    (root / "manifest.json").write_text(json.dumps({"status": "pending_manual_semantic_review", "sample_counts": {"mio": 1, "thai": 1}, "merge_authorized": False, "training_authorized": False}), encoding="utf-8")


def test_records_owner_keeps_without_authorizing_merge_or_training(tmp_path: Path) -> None:
    """Catches losing owner decisions or escalating audit approval into merge/training authority."""
    audit = tmp_path / "audit"
    output = tmp_path / "reviewed"
    _audit(audit)

    result = subprocess.run([sys.executable, str(SCRIPT), "--audit", str(audit), "--output", str(output), "--decision", "keep", "--reviewed-by", "project_owner"], text=True, capture_output=True)

    assert result.returncode == 0, result.stderr
    decisions = list(csv.DictReader((output / "audit_decisions_owner_reviewed.csv").open(encoding="utf-8")))
    assert [row["decision"] for row in decisions] == ["keep", "keep"]
    assert all(row["reviewed_by"] == "project_owner" and row["reviewed_at"] for row in decisions)
    manifest = json.loads((output / "approval_manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "owner_semantic_sample_audit_passed"
    assert manifest["decision_counts"] == {"keep": 2}
    assert manifest["merge_authorized"] is False
    assert manifest["training_authorized"] is False
    assert (audit / "audit_decisions.csv").read_text(encoding="utf-8").endswith(",,\n")


def test_fails_closed_when_decision_and_sample_identities_disagree(tmp_path: Path) -> None:
    """Catches approving decisions that are not bound to the rendered sample identities."""
    audit = tmp_path / "audit"
    output = tmp_path / "reviewed"
    _audit(audit, changed_sample=True)

    result = subprocess.run([sys.executable, str(SCRIPT), "--audit", str(audit), "--output", str(output), "--decision", "keep", "--reviewed-by", "project_owner"], text=True, capture_output=True)

    assert result.returncode != 0
    assert "identity mismatch" in result.stderr
    assert not output.exists()
