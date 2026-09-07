"""Read-only comparison of two YOLO checkpoints on pickup-truck validation boxes."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image
from ultralytics import YOLO


EXPECTED_NAMES = [
    "car", "van", "jeepney", "tricycle", "autorickshaw",
    "bus", "truck", "pickup_truck", "motorcycle", "bicycle",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--predecessor", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split", choices=("valid", "test"), default="valid")
    parser.add_argument("--device", default="0")
    return parser.parse_args()


def image_for_label(label: Path, images_dir: Path) -> Path:
    for suffix in (".jpg", ".jpeg", ".png", ".bmp", ".webp"):
        candidate = images_dir / f"{label.stem}{suffix}"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"No image for {label}")


def load_pickup_ground_truth(dataset: Path, evaluation_split: str) -> tuple[list[dict], dict]:
    records = []
    split_stats: dict[str, dict] = {}
    for split in ("train", evaluation_split):
        labels_dir = dataset / split / "labels"
        images_dir = dataset / split / "images"
        boxes = []
        source_counts = Counter()
        image_count = 0
        for label in sorted(labels_dir.glob("*.txt")):
            pickup_rows = []
            for line in label.read_text(encoding="utf-8").splitlines():
                fields = line.split()
                if len(fields) == 5 and int(fields[0]) == 7:
                    pickup_rows.append(tuple(map(float, fields[1:])))
            if not pickup_rows:
                continue
            image = image_for_label(label, images_dir)
            width, height = Image.open(image).size
            image_count += 1
            stem = label.stem.lower()
            if "mio" in stem:
                source = "mio_tcd"
            elif "thai" in stem:
                source = "thai"
            elif "truck_pickup" in stem or "truck-pickup" in stem:
                source = "truck_pickup"
            elif "vehicle_detection" in stem:
                source = "vehicle_detection_ph"
            else:
                source = stem.split("__", 1)[0] if "__" in stem else "base_or_unknown"
            source_counts[source] += len(pickup_rows)
            for xc, yc, wn, hn in pickup_rows:
                boxes.append({
                    "area_fraction": wn * hn,
                    "width_px": wn * width,
                    "height_px": hn * height,
                    "aspect_ratio": wn / hn,
                })
                if split == evaluation_split:
                    records.append({
                        "image": str(image),
                        "image_name": image.name,
                        "gt_xyxy": [
                            (xc - wn / 2) * width,
                            (yc - hn / 2) * height,
                            (xc + wn / 2) * width,
                            (yc + hn / 2) * height,
                        ],
                    })
        def percentile(values: list[float], q: float) -> float | None:
            if not values:
                return None
            ordered = sorted(values)
            return ordered[round((len(ordered) - 1) * q)]
        split_stats[split] = {
            "pickup_images": image_count,
            "pickup_instances": len(boxes),
            "sources": dict(source_counts),
            "geometry": {
                key: {"p10": percentile([b[key] for b in boxes], .1),
                      "median": percentile([b[key] for b in boxes], .5),
                      "p90": percentile([b[key] for b in boxes], .9)}
                for key in ("area_fraction", "width_px", "height_px", "aspect_ratio")
            },
        }
    return records, split_stats


def iou(a: list[float], b: list[float]) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    aa = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    bb = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    return inter / (aa + bb - inter) if aa + bb - inter else 0.0


def compare_checkpoint(weight: Path, records: list[dict], device: str) -> tuple[dict, list[dict]]:
    model = YOLO(str(weight))
    names = [model.names[i] for i in range(len(model.names))]
    if names != EXPECTED_NAMES:
        raise RuntimeError(f"Unexpected model class names: {names}")
    by_image = defaultdict(list)
    for idx, record in enumerate(records):
        by_image[record["image"]].append(idx)
    results = model.predict(
        source=list(by_image), conf=0.001, iou=0.7, imgsz=640,
        max_det=300, device=device, verbose=False, stream=False,
    )
    rows = [None] * len(records)
    confusion = Counter()
    pickup_at_50 = 0
    pickup_at_25 = 0
    any_at_50 = 0
    for result in results:
        image_key = str(Path(result.path))
        predictions = []
        if result.boxes is not None:
            for xyxy, cls, conf in zip(result.boxes.xyxy.cpu().tolist(), result.boxes.cls.cpu().tolist(), result.boxes.conf.cpu().tolist()):
                predictions.append((xyxy, int(cls), float(conf)))
        for idx in by_image[image_key]:
            gt = records[idx]["gt_xyxy"]
            best_any = max(predictions, key=lambda p: iou(gt, p[0]), default=None)
            pickup_predictions = [p for p in predictions if p[1] == 7]
            best_pickup = max(pickup_predictions, key=lambda p: iou(gt, p[0]), default=None)
            any_iou = iou(gt, best_any[0]) if best_any else 0.0
            pickup_iou = iou(gt, best_pickup[0]) if best_pickup else 0.0
            any_cls = EXPECTED_NAMES[best_any[1]] if best_any else "no_prediction"
            if any_iou >= 0.5:
                any_at_50 += 1
                confusion[any_cls] += 1
            else:
                confusion["no_overlap_iou50"] += 1
            if best_pickup and pickup_iou >= 0.5:
                pickup_at_50 += 1
                if best_pickup[2] >= 0.25:
                    pickup_at_25 += 1
            rows[idx] = {
                "image_name": records[idx]["image_name"],
                "best_any_class": any_cls,
                "best_any_iou": round(any_iou, 6),
                "best_any_conf": round(best_any[2], 6) if best_any else None,
                "best_pickup_iou": round(pickup_iou, 6),
                "best_pickup_conf": round(best_pickup[2], 6) if best_pickup else None,
            }
    total = len(records)
    summary = {
        "weight": str(weight),
        "validation_pickup_instances": total,
        "pickup_overlap_iou50_at_conf_0_001": pickup_at_50,
        "pickup_overlap_iou50_at_conf_0_25": pickup_at_25,
        "any_class_overlap_iou50": any_at_50,
        "best_overlap_class_at_iou50": dict(confusion),
    }
    return summary, rows


def main() -> None:
    args = parse_args()
    records, split_stats = load_pickup_ground_truth(args.dataset, args.split)
    predecessor, predecessor_rows = compare_checkpoint(args.predecessor, records, args.device)
    candidate, candidate_rows = compare_checkpoint(args.candidate, records, args.device)
    report = {
        "dataset": str(args.dataset),
        "evaluation_split": args.split,
        "split_stats": split_stats,
        "predecessor": predecessor,
        "candidate": candidate,
        "per_instance": [
            {"ground_truth": records[i], "predecessor": predecessor_rows[i], "candidate": candidate_rows[i]}
            for i in range(len(records))
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "per_instance"}, indent=2))


if __name__ == "__main__":
    main()
