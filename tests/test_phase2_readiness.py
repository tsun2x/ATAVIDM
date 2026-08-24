"""Behavioral tests for the Phase 2 dataset readiness gate."""

import json

from core.phase2_readiness import evaluate_entry_gate, evaluate_readiness, load_registry


def _write_registry(tmp_path, datasets):
    path = tmp_path / "datasets.json"
    path.write_text(json.dumps({"schema_version": 1, "datasets": datasets}), encoding="utf-8")
    return path


def _ready_dataset(dataset_id="local-ph-cctv"):
    return {
        "id": dataset_id,
        "name": "Authorized Philippine CCTV",
        "status": "accepted",
        "domain": "philippines",
        "purposes": ["final_train", "validation", "evaluation"],
        "official_url": "https://example.invalid/local-record",
        "license": {"status": "verified", "name": "Project permission"},
        "acquisition": {"status": "downloaded", "checksum_manifest": "checksums.sha256"},
        "inspection": {"status": "passed", "sample_count": 100},
        "annotations": {"status": "passed", "format": "yolo", "class_counts_recorded": True},
        "split": {"status": "passed", "grouped_by": ["camera", "location", "source_video", "time"]},
        "coverage": {"philippine_signs": True, "philippine_markings": True},
    }


def test_foreign_sign_dataset_cannot_be_used_for_final_evaluation(tmp_path):
    """Catches accepting foreign road conventions as authoritative evaluation data."""
    dataset = _ready_dataset("foreign-signs")
    dataset["domain"] = "foreign"
    dataset["purposes"] = ["evaluation"]
    registry = load_registry(_write_registry(tmp_path, [dataset]))

    report = evaluate_readiness(registry)

    assert report["ready"] is False
    assert any(issue["code"] == "foreign_final_domain" for issue in report["issues"])


def test_candidate_links_do_not_pass_the_entry_gate(tmp_path):
    """Catches treating an uninspected link as a training-ready dataset."""
    candidate = _ready_dataset("candidate")
    candidate["status"] = "candidate"
    candidate["license"]["status"] = "unverified"
    candidate["acquisition"]["status"] = "link_only"
    candidate["inspection"]["status"] = "pending"
    registry = load_registry(_write_registry(tmp_path, [candidate]))

    report = evaluate_readiness(registry)

    assert report["ready"] is False
    codes = {issue["code"] for issue in report["issues"]}
    assert {"dataset_not_accepted", "license_unverified", "not_downloaded", "inspection_incomplete"} <= codes


def test_complete_local_dataset_passes_dataset_gate(tmp_path):
    """Catches a gate that stays closed after all required evidence is present."""
    registry = load_registry(_write_registry(tmp_path, [_ready_dataset()]))

    report = evaluate_readiness(registry)

    assert report == {"ready": True, "dataset_count": 1, "issues": []}


def test_phase2_entry_gate_requires_governance_gold_set_and_pilot(tmp_path):
    """Catches starting full training after data checks but before project controls pass."""
    registry = load_registry(_write_registry(tmp_path, [_ready_dataset()]))
    controls = {
        "training_specification": "frozen",
        "collection_permission": "pending",
        "privacy_and_retention": "pending",
        "gold_annotation_set": "pending",
        "pilot_training": "pending",
        "acceptance_thresholds": "pending",
    }

    report = evaluate_entry_gate(registry, controls)

    assert report["ready"] is False
    codes = {issue["code"] for issue in report["issues"]}
    assert {
        "collection_permission_missing",
        "privacy_policy_missing",
        "gold_set_incomplete",
        "pilot_incomplete",
        "thresholds_missing",
    } <= codes


def test_phase2_entry_gate_passes_only_when_all_controls_pass(tmp_path):
    """Catches ignoring completed project controls or silently adding extra blockers."""
    registry = load_registry(_write_registry(tmp_path, [_ready_dataset()]))
    controls = {
        "training_specification": "frozen",
        "collection_permission": "approved",
        "privacy_and_retention": "approved",
        "gold_annotation_set": "passed",
        "pilot_training": "passed",
        "acceptance_thresholds": "recorded",
    }

    report = evaluate_entry_gate(registry, controls)

    assert report["ready"] is True
    assert report["issues"] == []


def test_rejected_dataset_is_retained_for_audit_without_blocking_gate(tmp_path):
    """Catches forcing teams to delete rejected-source audit history to pass the gate."""
    rejected = _ready_dataset("irrelevant-media")
    rejected["status"] = "rejected"
    rejected["inspection"]["status"] = "failed"
    registry = load_registry(_write_registry(tmp_path, [rejected, _ready_dataset()]))

    report = evaluate_readiness(registry)

    assert report["ready"] is True
    assert report["dataset_count"] == 2
    assert report["issues"] == []


def test_entry_gate_cannot_pass_without_an_accepted_dataset(tmp_path):
    """Catches a project-controls-only pass when the registry has no usable data."""
    registry = load_registry(_write_registry(tmp_path, []))
    controls = {
        "training_specification": "frozen",
        "collection_permission": "approved",
        "privacy_and_retention": "approved",
        "gold_annotation_set": "passed",
        "pilot_training": "passed",
        "acceptance_thresholds": "recorded",
    }

    report = evaluate_entry_gate(registry, controls)

    assert report["ready"] is False
    assert any(issue["code"] == "no_accepted_dataset" for issue in report["issues"])
