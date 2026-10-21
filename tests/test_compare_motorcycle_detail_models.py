"""Offline contract checks for matched motorcycle-detail comparisons."""

from __future__ import annotations

import pytest
import numpy as np
from types import SimpleNamespace

from scripts.compare_motorcycle_detail_models import (
    _metrics_by_class,
    compare_models,
    load_dataset_manifest,
    _percentile,
)


def test_manifest_counts_test_instances_and_keeps_three_class_contract(tmp_path):
    root = tmp_path / "dataset"
    image_dir = root / "test" / "images"
    label_dir = root / "test" / "labels"
    image_dir.mkdir(parents=True)
    label_dir.mkdir(parents=True)
    (image_dir / "frame-1.jpg").write_bytes(b"synthetic image")
    (label_dir / "frame-1.txt").write_text(
        "0 0.5 0.5 0.2 0.2\n2 0.4 0.4 0.1 0.1\n", encoding="utf-8"
    )
    manifest = tmp_path / "data.yaml"
    manifest.write_text(
        "path: dataset\ntest: test/images\n"
        "names: [helmet_nut_shell, helmet_acceptable, side_mirror]\n",
        encoding="utf-8",
    )

    result = load_dataset_manifest(manifest)

    assert result["contract"] == "md-detail-3c-v1"
    assert result["test_image_count"] == 1
    assert result["test_class_instance_counts"] == {
        "helmet_nut_shell": 1,
        "helmet_acceptable": 0,
        "side_mirror": 1,
    }
    assert result["test_label_files_missing"] == 0


def test_manifest_refuses_unordered_contract_and_download_directive(tmp_path):
    manifest = tmp_path / "data.yaml"
    manifest.write_text(
        "path: .\ntest: test/images\ndownload: https://example.invalid/data.zip\n"
        "names: [side_mirror, helmet_nut_shell, helmet_acceptable]\n",
        encoding="utf-8",
    )
    (tmp_path / "test" / "images").mkdir(parents=True)
    with pytest.raises(ValueError, match="auto-download"):
        load_dataset_manifest(manifest)

    manifest.write_text(
        "path: .\ntest: test/images\n"
        "names: [side_mirror, helmet_nut_shell, helmet_acceptable]\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="class order"):
        load_dataset_manifest(manifest)


def test_percentiles_are_deterministic_and_empty_samples_are_unmeasured():
    assert _percentile([1.0, 10.0, 2.0, 3.0], 0.5) == 2.0
    assert _percentile([1.0, 10.0, 2.0, 3.0], 0.95) == 10.0
    assert _percentile([], 0.95) is None


def test_metric_arrays_use_scalar_ap50_95_values_and_preserve_aggregates():
    box = SimpleNamespace(
        ap_class_index=np.array([0, 1, 2]),
        p=np.array([0.11, 0.22, 0.33]),
        r=np.array([0.44, 0.55, 0.66]),
        ap50=np.array([0.71, 0.82, 0.93]),
        ap=np.array([0.61, 0.72, 0.83]),
        mp=0.22,
        mr=0.55,
        map50=0.82,
        map=0.72,
    )

    result = _metrics_by_class(SimpleNamespace(box=box), ["a", "b", "c"])

    assert result["available"] is True
    assert result["aggregate"] == {
        "precision": 0.22, "recall": 0.55, "ap50": 0.82, "ap50_95": 0.72
    }
    assert result["per_class"]["b"] == {
        "precision": 0.22, "recall": 0.55, "ap50": 0.82, "ap50_95": 0.72
    }


def test_sparse_metric_indices_map_all_metric_vectors_by_represented_position():
    box = SimpleNamespace(
        ap_class_index=np.array([1, 2]),
        p=np.array([0.17, 0.29]),
        r=np.array([0.38, 0.49]),
        ap50=np.array([0.58, 0.69]),
        ap=np.array([0.48, 0.59]),
        mp=0.23,
        mr=0.435,
        map50=0.635,
        map=0.535,
    )

    result = _metrics_by_class(SimpleNamespace(box=box), ["absent", "one", "two"])

    assert result["per_class"] == {
        "absent": {"precision": None, "recall": None, "ap50": None, "ap50_95": None},
        "one": {"precision": 0.17, "recall": 0.38, "ap50": 0.58, "ap50_95": 0.48},
        "two": {"precision": 0.29, "recall": 0.49, "ap50": 0.69, "ap50_95": 0.59},
    }


def test_empty_or_unsupported_metrics_are_explicitly_unavailable():
    empty = _metrics_by_class(SimpleNamespace(box=SimpleNamespace(
        ap_class_index=[], p=[], r=[], ap50=[], ap=[], mp=0, mr=0, map50=0, map=0,
    )), ["a", "b"])
    unsupported = _metrics_by_class(SimpleNamespace(), ["a", "b"])

    assert empty["available"] is False
    assert empty["aggregate"] == {
        "precision": None, "recall": None, "ap50": None, "ap50_95": None
    }
    assert all(value is None for row in empty["per_class"].values() for value in row.values())
    assert unsupported["available"] is False
    assert all(value is None for row in unsupported["per_class"].values() for value in row.values())


def test_mocked_comparison_writes_numeric_metrics_to_json_without_inference(tmp_path, monkeypatch):
    import json
    import scripts.compare_motorcycle_detail_models as comparison

    nano = tmp_path / "nano.pt"
    small = tmp_path / "small.pt"
    data = tmp_path / "data.yaml"
    output = tmp_path / "comparison.json"
    nano.write_bytes(b"fake nano checkpoint")
    small.write_bytes(b"fake small checkpoint")
    data.write_text("unused", encoding="utf-8")
    dataset = {
        "manifest_path": str(data), "manifest_sha256": "manifest", "root": str(tmp_path),
        "class_names": ["helmet_nut_shell", "helmet_acceptable", "side_mirror"],
        "contract": "md-detail-3c-v1", "test_paths": [], "test_image_count": 1,
        "test_class_instance_counts": {"helmet_nut_shell": 1, "helmet_acceptable": 1, "side_mirror": 0},
        "test_label_files_missing": 0, "test_images": ["synthetic.jpg"],
    }
    box = SimpleNamespace(
        ap_class_index=np.array([0, 1]), p=np.array([0.8, 0.7]), r=np.array([0.6, 0.5]),
        ap50=np.array([0.9, 0.8]), ap=np.array([0.7, 0.6]),
        mp=0.75, mr=0.55, map50=0.85, map=0.65,
    )
    metrics = _metrics_by_class(SimpleNamespace(box=box), dataset["class_names"])
    monkeypatch.setattr(comparison, "load_dataset_manifest", lambda _path: dataset)
    monkeypatch.setattr(comparison, "_runtime_info", lambda _device: {"device_requested": "cpu"})
    monkeypatch.setattr(comparison, "_measure_one", lambda label, *_args, **_kwargs: {
        "label": label, "validation_metrics": metrics,
    })

    result = compare_models(nano, small, data, output)

    saved = json.loads(output.read_text(encoding="utf-8"))
    assert result["models"]["nano"]["validation_metrics"]["per_class"]["helmet_nut_shell"]["ap50"] == 0.9
    assert saved["models"]["small"]["validation_metrics"]["per_class"]["helmet_acceptable"]["ap50_95"] == 0.6
    assert saved["models"]["nano"]["validation_metrics"]["available"] is True
