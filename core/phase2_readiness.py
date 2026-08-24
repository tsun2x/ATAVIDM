"""Dataset evidence validation for the TAVIDM Phase 2 entry gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


FINAL_PURPOSES = {"final_train", "validation", "evaluation"}
REQUIRED_SPLIT_GROUPS = {"camera", "location", "source_video", "time"}


def load_registry(path: str | Path) -> dict[str, Any]:
    """Load and minimally validate a Phase 2 dataset registry."""
    registry = json.loads(Path(path).read_text(encoding="utf-8"))
    if registry.get("schema_version") != 1 or not isinstance(registry.get("datasets"), list):
        raise ValueError("Dataset registry must use schema_version 1 and contain a datasets list")
    return registry


def _issue(dataset_id: str, code: str, message: str) -> dict[str, str]:
    return {"dataset_id": dataset_id, "code": code, "message": message}


def evaluate_readiness(registry: dict[str, Any]) -> dict[str, Any]:
    """Return deterministic dataset-gate findings without changing any files."""
    issues: list[dict[str, str]] = []
    datasets = registry["datasets"]

    for dataset in datasets:
        dataset_id = str(dataset.get("id", "<missing-id>"))
        if dataset.get("status") == "rejected":
            continue
        purposes = set(dataset.get("purposes", []))

        if dataset.get("status") != "accepted":
            issues.append(_issue(dataset_id, "dataset_not_accepted", "Dataset has not been accepted"))
        if dataset.get("license", {}).get("status") != "verified":
            issues.append(_issue(dataset_id, "license_unverified", "Dataset license is not verified"))
        if dataset.get("acquisition", {}).get("status") != "downloaded":
            issues.append(_issue(dataset_id, "not_downloaded", "Dataset files are not downloaded"))
        if dataset.get("inspection", {}).get("status") != "passed":
            issues.append(_issue(dataset_id, "inspection_incomplete", "Representative sample inspection has not passed"))
        if dataset.get("annotations", {}).get("status") != "passed":
            issues.append(_issue(dataset_id, "annotations_incomplete", "Annotation quality control has not passed"))

        split = dataset.get("split", {})
        if split.get("status") != "passed" or not REQUIRED_SPLIT_GROUPS <= set(split.get("grouped_by", [])):
            issues.append(_issue(dataset_id, "split_not_leakage_safe", "Split lacks all required source grouping dimensions"))

        if dataset.get("domain") != "philippines" and purposes & FINAL_PURPOSES:
            issues.append(_issue(dataset_id, "foreign_final_domain", "Foreign-domain data cannot define final training or evaluation"))

        coverage = dataset.get("coverage", {})
        if purposes & FINAL_PURPOSES and not (
            coverage.get("philippine_signs") and coverage.get("philippine_markings")
        ):
            issues.append(_issue(dataset_id, "philippine_coverage_missing", "Final data lacks confirmed Philippine sign/marking coverage"))

    has_accepted = any(dataset.get("status") == "accepted" for dataset in datasets)
    if not has_accepted:
        issues.append(_issue("registry", "no_accepted_dataset", "No accepted dataset is available"))
    return {"ready": not issues and has_accepted, "dataset_count": len(datasets), "issues": issues}


def evaluate_entry_gate(registry: dict[str, Any], controls: dict[str, str]) -> dict[str, Any]:
    """Combine dataset evidence with the non-dataset Phase 2 entry controls."""
    report = evaluate_readiness(registry)
    issues = list(report["issues"])
    requirements = {
        "training_specification": ("frozen", "training_spec_not_frozen", "Training specification is not frozen"),
        "collection_permission": ("approved", "collection_permission_missing", "Local data collection permission is not approved"),
        "privacy_and_retention": ("approved", "privacy_policy_missing", "Privacy and retention procedure is not approved"),
        "gold_annotation_set": ("passed", "gold_set_incomplete", "Gold annotation set has not passed review"),
        "pilot_training": ("passed", "pilot_incomplete", "YOLOv8m pilot has not passed"),
        "acceptance_thresholds": ("recorded", "thresholds_missing", "Acceptance thresholds are not recorded"),
    }
    for field, (expected, code, message) in requirements.items():
        if controls.get(field) != expected:
            issues.append(_issue("project", code, message))

    return {"ready": not issues, "dataset_count": report["dataset_count"], "issues": issues}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the TAVIDM Phase 2 AI-training entry gate")
    parser.add_argument("--registry", default="dataset/registry/datasets.json")
    parser.add_argument("--controls", default="config/training/readiness_controls.json")
    parser.add_argument("--output", help="Optional path for the JSON report")
    args = parser.parse_args(argv)

    registry = load_registry(args.registry)
    controls = json.loads(Path(args.controls).read_text(encoding="utf-8"))
    report = evaluate_entry_gate(registry, controls)
    rendered = json.dumps(report, indent=2)
    print(rendered)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
