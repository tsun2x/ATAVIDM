"""Matched YOLOv8n/YOLOv8s motorcycle-detail comparison tooling.

The default mode validates explicit local paths and the dataset manifest only.
Actual model loading and evaluation require ``--evaluate``. This script never
reads application settings, changes the designated checkpoint, downloads
weights, trains a model, or promotes a candidate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Sequence


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
MAX_TIMING_IMAGES = 100


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_dataset_manifest(path: Path) -> dict[str, Any]:
    """Validate a local Ultralytics dataset manifest without loading a model."""
    import yaml

    if not path.is_file():
        raise ValueError(f"Dataset manifest not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Dataset manifest must be a YAML mapping.")
    if data.get("download"):
        raise ValueError("Dataset auto-download directives are not allowed.")
    names = data.get("names")
    if isinstance(names, dict):
        try:
            keys = sorted(names, key=lambda key: int(key))
            if [int(key) for key in keys] != list(range(len(keys))):
                raise ValueError
            names = [names[key] for key in keys]
        except (TypeError, ValueError):
            raise ValueError("Dataset class IDs must be contiguous and ordered.") from None
    if not isinstance(names, (list, tuple)):
        raise ValueError("Dataset manifest must declare an ordered class list.")
    class_names = tuple(str(name).strip().lower() for name in names)
    if len(class_names) not in (3, 4) or len(set(class_names)) != len(class_names):
        raise ValueError("Dataset must match a supported three- or four-class detail contract.")
    expected_four = class_names[:3] == (
        "helmet_nut_shell", "helmet_acceptable", "side_mirror"
    ) and class_names[3:] == ("no_helmet",)
    expected_three = class_names == (
        "helmet_nut_shell", "helmet_acceptable", "side_mirror"
    )
    if not (expected_three or expected_four):
        raise ValueError("Dataset class order does not match a motorcycle-detail contract.")

    root_value = data.get("path")
    root = Path(root_value) if root_value else path.parent
    if not root.is_absolute():
        root = (path.parent / root).resolve()
    test_value = data.get("test")
    if not test_value:
        raise ValueError("Dataset manifest must define an untouched test split.")
    test_values = test_value if isinstance(test_value, list) else [test_value]
    test_paths = []
    for value in test_values:
        candidate = Path(str(value))
        if not candidate.is_absolute():
            candidate = (root / candidate).resolve()
        if not candidate.exists():
            raise ValueError(f"Test split path does not exist: {candidate}")
        test_paths.append(candidate)
    images = []
    for candidate in test_paths:
        if candidate.is_dir():
            images.extend(p for p in candidate.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)
        elif candidate.is_file() and candidate.suffix.lower() in IMAGE_SUFFIXES:
            images.append(candidate)
    images = sorted(set(images))
    instance_counts = {name: 0 for name in class_names}
    missing_label_files = 0
    for image_path in images:
        parts = list(image_path.parts)
        image_folder = next(
            (index for index in range(len(parts) - 1, -1, -1) if parts[index].lower() == "images"),
            None,
        )
        if image_folder is None:
            missing_label_files += 1
            continue
        label_parts = parts[:]
        label_parts[image_folder] = "labels"
        label_path = Path(*label_parts).with_suffix(".txt")
        if not label_path.is_file():
            missing_label_files += 1
            continue
        for line in label_path.read_text(encoding="utf-8").splitlines():
            tokens = line.split()
            if not tokens:
                continue
            try:
                class_id = int(tokens[0])
            except ValueError as exc:
                raise ValueError(f"Malformed test label: {label_path}") from exc
            if not 0 <= class_id < len(class_names):
                raise ValueError(f"Test label class ID is outside the manifest roster: {label_path}")
            instance_counts[class_names[class_id]] += 1
    if missing_label_files:
        raise ValueError(
            f"Test split is missing {missing_label_files} label files; empty negative labels must be explicit."
        )
    return {
        "manifest_path": str(path.resolve()),
        "manifest_sha256": sha256_file(path),
        "root": str(root),
        "class_names": list(class_names),
        "contract": "md-detail-4c-v1" if expected_four else "md-detail-3c-v1",
        "test_paths": [str(p) for p in test_paths],
        "test_image_count": len(images),
        "test_class_instance_counts": instance_counts,
        "test_label_files_missing": missing_label_files,
        "test_images": [str(p) for p in images],
    }


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    index = min(len(ordered) - 1, max(0, math.ceil(percentile * len(ordered)) - 1))
    return round(ordered[index], 4)


def _cuda_sync(torch: Any, device: str) -> None:
    if str(device).startswith("cuda") and torch.cuda.is_available():
        torch.cuda.synchronize(device)


def _memory_stats(torch: Any, device: str) -> dict[str, float | None]:
    if not torch.cuda.is_available() or not str(device).startswith("cuda"):
        return {"peak_allocated_mb": None, "peak_reserved_mb": None}
    return {
        "peak_allocated_mb": round(torch.cuda.max_memory_allocated(device) / 1048576, 2),
        "peak_reserved_mb": round(torch.cuda.max_memory_reserved(device) / 1048576, 2),
    }


def _metrics_by_class(metrics: Any, class_names: Sequence[str]) -> dict[str, Any]:
    box = getattr(metrics, "box", None)
    per_class = {
        name: {"precision": None, "recall": None, "ap50": None, "ap50_95": None}
        for name in class_names
    }
    if box is None:
        return {
            "available": False,
            "aggregate": {"precision": None, "recall": None, "ap50": None, "ap50_95": None},
            "per_class": per_class,
        }
    raw_indices = getattr(box, "ap_class_index", None)
    indices = list(raw_indices) if raw_indices is not None else []
    precisions = getattr(box, "p", [])
    recalls = getattr(box, "r", [])

    def values(raw: Any) -> list[Any]:
        if raw is None:
            return []
        try:
            return list(raw)
        except TypeError:
            return [raw]

    def finite_float(raw: Any) -> float | None:
        try:
            value = float(raw)
        except (TypeError, ValueError, OverflowError):
            return None
        return value if math.isfinite(value) else None

    ap50_values = values(getattr(box, "ap50", []))
    ap_values = values(getattr(box, "ap", []))
    precision_values = values(precisions)
    recall_values = values(recalls)
    represented = False
    for position, class_id in enumerate(indices):
        try:
            class_id = int(class_id)
        except (TypeError, ValueError, OverflowError):
            continue
        if not 0 <= class_id < len(class_names):
            continue
        row = {
            "precision": finite_float(precision_values[position]) if position < len(precision_values) else None,
            "recall": finite_float(recall_values[position]) if position < len(recall_values) else None,
            "ap50": finite_float(ap50_values[position]) if position < len(ap50_values) else None,
            "ap50_95": finite_float(ap_values[position]) if position < len(ap_values) else None,
        }
        represented = represented or any(value is not None for value in row.values())
        per_class[class_names[class_id]] = row
    if not represented:
        return {
            "available": False,
            "aggregate": {"precision": None, "recall": None, "ap50": None, "ap50_95": None},
            "per_class": per_class,
        }
    aggregate = {
        "precision": finite_float(getattr(box, "mp", None)),
        "recall": finite_float(getattr(box, "mr", None)),
        "ap50": finite_float(getattr(box, "map50", None)),
        "ap50_95": finite_float(getattr(box, "map", None)),
    }
    return {
        "available": True,
        "aggregate": aggregate,
        "per_class": per_class,
    }


def _measure_one(
    label: str,
    checkpoint: Path,
    data_yaml: Path,
    dataset: dict[str, Any],
    output_root: Path,
    *,
    device: str,
    image_size: int,
    confidence: float,
    iou: float,
    batch: int,
    max_timing_images: int,
) -> dict[str, Any]:
    import cv2
    import torch
    from ultralytics import YOLO

    from core.motorcycle_detail_contract import check_detail_task, resolve_detail_contract, detail_architecture_hint

    if not checkpoint.is_file():
        raise ValueError(f"{label} checkpoint does not exist: {checkpoint}")
    load_start = time.perf_counter()
    model = YOLO(str(checkpoint.resolve()))
    load_seconds = time.perf_counter() - load_start
    task_error = check_detail_task(getattr(model, "task", None))
    contract = resolve_detail_contract(getattr(model, "names", None))
    if task_error or not contract.ok:
        raise ValueError(f"{label} checkpoint contract rejected: {task_error or contract.reason}")
    if tuple(contract.class_names) != tuple(dataset["class_names"]):
        raise ValueError(f"{label} checkpoint class order differs from the dataset.")
    arch = detail_architecture_hint(model)
    expected_arch = "yolov8n" if label == "nano" else "yolov8s"
    if arch and arch != expected_arch:
        raise ValueError(f"{label} checkpoint architecture metadata reports {arch}.")

    image_paths = dataset["test_images"][:max_timing_images]
    if not image_paths:
        raise ValueError("Test split contains no readable image paths.")
    images = []
    for path in image_paths:
        image = cv2.imread(path)
        if image is not None:
            images.append((path, image))
    if not images:
        raise ValueError("No timing images could be decoded from the test split.")

    predict_args = {"imgsz": image_size, "conf": confidence, "iou": iou, "verbose": False}
    if device:
        predict_args["device"] = device
    _cuda_sync(torch, device)
    cold_started = time.perf_counter()
    model.predict(source=images[0][1], **predict_args)
    _cuda_sync(torch, device)
    cold_first_predict_ms = round((time.perf_counter() - cold_started) * 1000.0, 4)
    model.predict(source=images[0][1], **predict_args)  # warm-up, excluded from latency samples
    if torch.cuda.is_available() and str(device).startswith("cuda"):
        torch.cuda.reset_peak_memory_stats(device)

    latencies = []
    for _path, image in images:
        _cuda_sync(torch, device)
        started = time.perf_counter()
        model.predict(source=image, **predict_args)
        _cuda_sync(torch, device)
        latencies.append((time.perf_counter() - started) * 1000.0)

    project = output_root / "validation_runs" / f"{label}_{uuid.uuid4().hex[:8]}"
    run_start = time.perf_counter()
    metrics = model.val(
        data=str(data_yaml.resolve()), split="test", imgsz=image_size,
        conf=confidence, iou=iou, batch=batch, device=device,
        workers=0, plots=False, save_json=False, verbose=False,
        project=str(project), name="results", exist_ok=False,
    )
    _cuda_sync(torch, device)
    val_seconds = time.perf_counter() - run_start
    memory = _memory_stats(torch, device)
    validation_metrics = _metrics_by_class(metrics, contract.class_names)
    if not validation_metrics["available"]:
        raise ValueError(f"{label} checkpoint validation returned no per-class metrics.")
    for class_name, values in validation_metrics["per_class"].items():
        values["test_instances"] = int(dataset["test_class_instance_counts"].get(class_name, 0))
    result = {
        "label": label,
        "checkpoint": str(checkpoint.resolve()),
        "sha256": sha256_file(checkpoint),
        "architecture_hint": arch or "unverified",
        "contract": contract.version,
        "class_names": list(contract.class_names),
        "cold_load_seconds": round(load_seconds, 4),
        "cold_first_predict_ms": cold_first_predict_ms,
        "test_validation_seconds": round(val_seconds, 4),
        "test_image_count": dataset["test_image_count"],
        "timing_sample_count": len(latencies),
        "warm_latency_ms": {
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
        },
        "validation_metrics": validation_metrics,
        "gpu_memory": memory,
        "validation_output": str(project / "results"),
    }
    del model
    if torch.cuda.is_available() and str(device).startswith("cuda"):
        _cuda_sync(torch, device)
        torch.cuda.empty_cache()
    return result


def compare_models(
    nano_path: Path,
    small_path: Path,
    data_yaml: Path,
    output_path: Path,
    *,
    device: str = "cpu",
    image_size: int = 640,
    confidence: float = 0.001,
    iou: float = 0.7,
    batch: int = 1,
    max_timing_images: int = 100,
) -> dict[str, Any]:
    """Evaluate explicit n/s checkpoints sequentially on the same test split."""
    if nano_path.resolve() == small_path.resolve():
        raise ValueError("Nano and small checkpoints must be different files.")
    if not 1 <= max_timing_images <= MAX_TIMING_IMAGES:
        raise ValueError("Timing image count must be between 1 and 100.")
    dataset = load_dataset_manifest(data_yaml)
    if not nano_path.is_file() or not small_path.is_file():
        raise ValueError("Both explicit local checkpoint files must exist; no substitutes are downloaded.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    nano = _measure_one(
        "nano", nano_path, data_yaml, dataset, output_path.parent,
        device=device, image_size=image_size, confidence=confidence, iou=iou,
        batch=batch, max_timing_images=max_timing_images,
    )
    small = _measure_one(
        "small", small_path, data_yaml, dataset, output_path.parent,
        device=device, image_size=image_size, confidence=confidence, iou=iou,
        batch=batch, max_timing_images=max_timing_images,
    )
    result = {
        "mode": "motorcycle_detail_matched_comparison",
        "dataset": {key: value for key, value in dataset.items() if key != "test_images"},
        "settings": {
            "device": device,
            "image_size": image_size,
            "confidence": confidence,
            "iou": iou,
            "batch": batch,
            "max_timing_images": max_timing_images,
            "timing_scope": "warm per-image predict; input image decode excluded",
            "validation_scope": "entire explicit test split; do not use it for model selection",
        },
        "models": {"nano": nano, "small": small},
        "runtime": _runtime_info(device),
        "limitations": [
            "These measurements do not establish field violation accuracy.",
            "Per-class metrics depend on labelled test split coverage; report each class denominator.",
            "Concurrent primary-pipeline impact is not measured by this sequential isolated comparison.",
        ],
    }
    output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def _runtime_info(device: str) -> dict[str, Any]:
    import torch
    import ultralytics

    info = {
        "device_requested": device,
        "torch_version": str(torch.__version__),
        "ultralytics_version": str(getattr(ultralytics, "__version__", "unknown")),
        "cuda_runtime": getattr(torch.version, "cuda", None),
        "gpu_name": None,
    }
    if torch.cuda.is_available() and str(device).startswith("cuda"):
        try:
            info["gpu_name"] = torch.cuda.get_device_name(device)
        except Exception:
            info["gpu_name"] = "unavailable"
    return info


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nano", required=True, type=Path, help="Explicit local YOLOv8n checkpoint")
    parser.add_argument("--small", required=True, type=Path, help="Explicit local YOLOv8s checkpoint")
    parser.add_argument("--data", required=True, type=Path, help="Audited dataset YAML with untouched test split")
    parser.add_argument("--output", required=True, type=Path, help="JSON comparison output")
    parser.add_argument("--evaluate", action="store_true", help="Load models and evaluate; default validates paths only")
    parser.add_argument("--device", default="cpu", help="Explicit evaluation device, default cpu")
    parser.add_argument("--image-size", type=int, default=640)
    parser.add_argument("--confidence", type=float, default=0.001)
    parser.add_argument("--iou", type=float, default=0.7)
    parser.add_argument("--batch", type=int, default=1)
    parser.add_argument("--max-timing-images", type=int, default=100)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        dataset = load_dataset_manifest(args.data)
        for label, path in (("nano", args.nano), ("small", args.small)):
            if not path.is_file():
                raise ValueError(f"{label} checkpoint does not exist: {path}")
        if not args.evaluate:
            result = {
                "mode": "metadata_only_dry_run",
                "dataset": {key: value for key, value in dataset.items() if key != "test_images"},
                "checkpoints": {
                    "nano": {"path": str(args.nano.resolve()), "sha256": sha256_file(args.nano)},
                    "small": {"path": str(args.small.resolve()), "sha256": sha256_file(args.small)},
                },
                "class_map_validation": "not performed without loading checkpoints",
                "inference_performed": False,
            }
            print(json.dumps(result, indent=2))
            return 0
        result = compare_models(
            args.nano, args.small, args.data, args.output,
            device=args.device, image_size=args.image_size,
            confidence=args.confidence, iou=args.iou, batch=args.batch,
            max_timing_images=args.max_timing_images,
        )
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError) as exc:
        print(f"motorcycle comparison failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
