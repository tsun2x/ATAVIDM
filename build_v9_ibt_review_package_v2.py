"""Build a provenance-preserving Roboflow package from two ZIPs and IBT videos."""
from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import re
import shutil
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

CLASSES = ["car", "van", "jeepney", "tricycle", "autorickshaw", "bus", "truck",
           "pickup_truck", "motorcycle", "bicycle", "person", "rider"]
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


class PackageError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dataset_rows(path: Path, target_map: dict[str, str], excluded: set[str]) -> list[dict]:
    archive_hash = sha256_file(path)
    rows = []
    with zipfile.ZipFile(path) as archive:
        yaml_names = [name for name in archive.namelist() if Path(name).name == "data.yaml"]
        if not yaml_names:
            raise PackageError(f"missing data.yaml: {path}")
        yaml_text = archive.read(yaml_names[0]).decode("utf-8-sig")
        match = re.search(r"(?m)^names:\s*(\[.*\])\s*$", yaml_text)
        if not match:
            raise PackageError(f"names must be an inline list: {path}")
        names = ast.literal_eval(match.group(1))
        normalized = {index: name.strip().lower() for index, name in enumerate(names)}
        target_ids = {index: target_map[name] for index, name in normalized.items() if name in target_map}
        excluded_ids = {index for index, name in normalized.items() if name in excluded}
        members = archive.namelist()
        for label_name in sorted(name for name in members if "/labels/" in name and name.endswith(".txt")):
            ids = []
            for line in archive.read(label_name).decode("utf-8-sig").splitlines():
                fields = line.split()
                if not fields:
                    continue
                if len(fields) < 5 or not fields[0].isdigit():
                    raise PackageError(f"invalid YOLO row: {label_name}")
                class_id = int(fields[0])
                if class_id not in normalized:
                    raise PackageError(f"class ID outside names roster: {label_name}")
                ids.append(class_id)
            if not ids or any(value in excluded_ids for value in ids):
                continue
            candidates = sorted({target_ids[value] for value in ids if value in target_ids})
            if not candidates:
                continue
            image_prefix = label_name.replace("/labels/", "/images/").rsplit(".", 1)[0]
            images = [name for name in members if name.rsplit(".", 1)[0] == image_prefix
                      and Path(name).suffix.lower() in IMAGE_SUFFIXES]
            if len(images) != 1:
                raise PackageError(f"image pairing failure: {label_name}")
            data = archive.read(images[0])
            if cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR) is None:
                raise PackageError(f"unreadable image: {images[0]}")
            rows.append({"bytes": data, "source_path": images[0],
                         "source_sha256": hashlib.sha256(data).hexdigest(),
                         "source_archive": str(path.resolve()),
                         "source_archive_sha256": archive_hash,
                         "source_split": images[0].split("/", 1)[0],
                         "candidate_classes": candidates})
    return rows


def spread(rows: list[dict], count: int) -> list[dict]:
    if count < 1 or len(rows) < count:
        raise PackageError(f"only {len(rows)} candidates available for quota {count}")
    return [rows[int(index)] for index in np.linspace(0, len(rows) - 1, count, dtype=int)]


def select_car(rows: list[dict], count: int) -> list[dict]:
    targets = ["bus", "pickup_truck", "truck", "van"]
    base, remainder = divmod(count, len(targets))
    chosen, used = [], set()
    for index, target in enumerate(targets):
        quota = base + (1 if index < remainder else 0)
        candidates = [row for row in rows if target in row["candidate_classes"]
                      and row["source_sha256"] not in used]
        for row in spread(candidates, quota):
            chosen.append(row)
            used.add(row["source_sha256"])
    if len(chosen) < count:
        for row in spread([row for row in rows if row["source_sha256"] not in used], count - len(chosen)):
            chosen.append(row)
            used.add(row["source_sha256"])
    return chosen


def video_rows(videos: list[Path], frames_per_video: int) -> list[dict]:
    rows = []
    for number, video in enumerate(videos, 1):
        capture = cv2.VideoCapture(str(video))
        if not capture.isOpened():
            raise PackageError(f"cannot open video: {video}")
        try:
            fps = float(capture.get(cv2.CAP_PROP_FPS))
            total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
            if not np.isfinite(fps) or fps <= 0 or total < frames_per_video:
                raise PackageError(f"invalid video metadata: {video}")
            indexes = np.linspace(int(fps), total - int(fps) - 1, frames_per_video, dtype=int)
            video_hash = sha256_file(video)
            for frame_index in indexes:
                capture.set(cv2.CAP_PROP_POS_FRAMES, int(frame_index))
                ok, frame = capture.read()
                if not ok:
                    raise PackageError(f"cannot read frame {frame_index}: {video}")
                ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
                if not ok:
                    raise PackageError(f"cannot encode frame {frame_index}: {video}")
                data = encoded.tobytes()
                rows.append({"bytes": data, "source_path": str(video.resolve()),
                             "source_sha256": hashlib.sha256(data).hexdigest(),
                             "source_video_sha256": video_hash,
                             "source_group": f"ibt_2026-09-12_clip_{number:02d}",
                             "frame_index": int(frame_index),
                             "timestamp_ms": round(int(frame_index) / fps * 1000),
                             "candidate_classes": []})
        finally:
            capture.release()
    return rows


def build_package(car_zip: Path, truck_zip: Path, videos: list[Path], output: Path,
                  car_count: int, truck_count: int, frames_per_video: int) -> dict:
    if output.exists():
        raise PackageError(f"refusing to overwrite existing output: {output}")
    if not videos:
        raise PackageError("no IBT videos found")
    car = dataset_rows(car_zip, {"bus": "bus", "pickup": "pickup_truck",
                                "truck": "truck", "van": "van"}, {"ambulance", "multicab"})
    truck = dataset_rows(truck_zip, {"truck": "truck"}, set())
    selected = [("car_detection_v15", row) for row in select_car(car, car_count)]
    selected += [("truck_v4", row) for row in spread(truck, truck_count)]
    selected += [("ibt_footbridge", row) for row in video_rows(videos, frames_per_video)]
    if len({row["source_sha256"] for _, row in selected}) != len(selected):
        raise PackageError("exact duplicate selected across package")

    upload, docs = output / "upload_images", output / "documentation_do_not_upload"
    upload.mkdir(parents=True)
    docs.mkdir()
    counts, items = Counter(), []
    for source, row in selected:
        counts[source] += 1
        filename = f"tavidm_v9_{source}_{counts[source]:04d}.jpg"
        image = cv2.imdecode(np.frombuffer(row.pop("bytes"), np.uint8), cv2.IMREAD_COLOR)
        if image is None or not cv2.imwrite(str(upload / filename), image, [cv2.IMWRITE_JPEG_QUALITY, 95]):
            raise PackageError(f"cannot write package image: {filename}")
        items.append({**row, "package_filename": filename, "sha256": sha256_file(upload / filename),
                      "source": source, "dataset_role": "development_only_not_gold",
                      "annotation_status": "not_started"})
    if len({row["sha256"] for row in items}) != len(items):
        raise PackageError("output encoding created exact duplicates")
    manifest = {"schema_version": "1.0.0", "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "dataset_role": "development_only_not_gold", "training_authorized": False,
                "promotion_authorized": False, "annotation_classes": CLASSES,
                "total_images": len(items), "source_counts": dict(counts), "items": items}
    (docs / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    fields = sorted({key for row in items for key in row})
    with (docs / "review_queue.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in items:
            writer.writerow({**row, "candidate_classes": ";".join(row["candidate_classes"])})
    (docs / "classes.txt").write_text("\n".join(CLASSES) + "\n", encoding="utf-8")
    (docs / "ANNOTATION_INSTRUCTIONS.md").write_text(
        "# TAVIDM V9 review instructions\n\nUpload only the image ZIP. Annotate every clearly "
        "visible compatible object, not only the candidate class. Use tight boxes. Map Sedan/SUV "
        "to car and Pickup to pickup_truck. Distinguish truck from pickup_truck and tricycle from "
        "integrated-body autorickshaw. Never guess; flag uncertainty for review. Export YOLOv8 and "
        "preserve the export unchanged. Final splits must be created locally by source group.\n",
        encoding="utf-8")
    zip_base = output / f"roboflow_upload_images_{len(items)}"
    shutil.make_archive(str(zip_base), "zip", upload)
    summary = {"total_images": len(items), "unique_sha256": len(items),
               "source_counts": dict(counts), "upload_zip": str(zip_base.with_suffix(".zip"))}
    (docs / "BUILD_SUMMARY.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--car-zip", type=Path, required=True)
    parser.add_argument("--truck-zip", type=Path, required=True)
    parser.add_argument("--video-dir", type=Path, required=True)
    parser.add_argument("--video-pattern", default="*.mp4")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--car-count", type=int, default=150)
    parser.add_argument("--truck-count", type=int, default=150)
    parser.add_argument("--frames-per-video", type=int, default=15)
    args = parser.parse_args()
    try:
        summary = build_package(args.car_zip, args.truck_zip,
                                sorted(args.video_dir.glob(args.video_pattern)), args.output,
                                args.car_count, args.truck_count, args.frames_per_video)
    except (OSError, ValueError, zipfile.BadZipFile, PackageError) as exc:
        print(f"ERROR: {exc}")
        return 2
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
