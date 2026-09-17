"""Build an image-only, provenance-preserving V9 external review supplement."""
from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import math
import re
import shutil
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np


CLASSES = [
    "car", "van", "jeepney", "tricycle", "autorickshaw", "bus", "truck",
    "pickup_truck", "motorcycle", "bicycle", "person", "rider",
    "helmet_acceptable", "helmet_nut_shell", "side_mirror",
]
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


class SupplementError(RuntimeError):
    pass


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _dhash(data: bytes) -> int:
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise SupplementError("unreadable candidate image")
    resized = cv2.resize(image, (9, 8), interpolation=cv2.INTER_AREA)
    return sum(
        int(resized[row, col] > resized[row, col + 1]) << (row * 8 + col)
        for row in range(8) for col in range(8)
    )


def _dataset_rows(path: Path, required_label: str) -> list[dict]:
    archive_sha256 = _file_sha256(path)
    rows: list[dict] = []
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise SupplementError(f"archive CRC failure: {path}")
        yaml_infos = [item for item in archive.infolist() if Path(item.filename).name == "data.yaml"]
        if not yaml_infos:
            raise SupplementError(f"missing data.yaml: {path}")
        yaml_text = archive.read(yaml_infos[0]).decode("utf-8-sig")
        match = re.search(r"(?m)^names:\s*(\[.*\])\s*$", yaml_text)
        if not match:
            raise SupplementError(f"names must be an inline list: {path}")
        names = list(ast.literal_eval(match.group(1)))
        if required_label not in names:
            raise SupplementError(f"missing required class {required_label!r}: {path}")
        required_id = names.index(required_label)
        license_match = re.search(r"(?m)^\s*license:\s*(.+?)\s*$", yaml_text)
        license_name = license_match.group(1) if license_match else "unverified"
        members = archive.namelist()
        for label_name in sorted(name for name in members if "/labels/" in name and name.endswith(".txt")):
            source_labels: set[str] = set()
            contains_required = False
            for line_number, line in enumerate(
                archive.read(label_name).decode("utf-8-sig").splitlines(), 1
            ):
                fields = line.split()
                if not fields:
                    continue
                if len(fields) != 5:
                    raise SupplementError(
                        f"invalid YOLO detection row: {label_name}:{line_number}"
                    )
                try:
                    class_id = int(fields[0])
                    x, y, width, height = map(float, fields[1:])
                except ValueError as exc:
                    raise SupplementError(
                        f"invalid YOLO detection row: {label_name}:{line_number}"
                    ) from exc
                if not 0 <= class_id < len(names):
                    raise SupplementError(f"class ID outside names roster: {label_name}:{line_number}")
                values = (x, y, width, height)
                if (
                    not all(math.isfinite(value) for value in values)
                    or not (0 <= x <= 1 and 0 <= y <= 1 and 0 < width <= 1 and 0 < height <= 1)
                ):
                    raise SupplementError(
                        f"invalid YOLO detection row: {label_name}:{line_number}"
                    )
                source_labels.add(str(names[class_id]))
                contains_required |= class_id == required_id
            if not contains_required:
                continue
            image_prefix = label_name.replace("/labels/", "/images/").rsplit(".", 1)[0]
            image_names = [
                name for name in members
                if name.rsplit(".", 1)[0] == image_prefix
                and Path(name).suffix.lower() in IMAGE_SUFFIXES
            ]
            if len(image_names) != 1:
                raise SupplementError(f"image pairing failure: {label_name}")
            image_name = image_names[0]
            data = archive.read(image_name)
            rows.append({
                "bytes": data,
                "source_archive": str(path.resolve()),
                "source_archive_sha256": archive_sha256,
                "source_path": image_name,
                "source_split": image_name.split("/", 1)[0],
                "source_sha256": _sha256(data),
                "source_labels": sorted(source_labels),
                "source_license": license_name,
                "dhash": _dhash(data),
            })
    return rows


def _image_hashes(path: Path) -> set[str]:
    hashes: set[str] = set()
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise SupplementError(f"baseline archive CRC failure: {path}")
        for name in archive.namelist():
            if "/images/" in name and Path(name).suffix.lower() in IMAGE_SUFFIXES:
                hashes.add(_sha256(archive.read(name)))
    return hashes


def _select(
    rows: list[dict], count: int, used_hashes: set[str], used_dhashes: list[int],
    near_duplicate_distance: int,
) -> list[dict]:
    chosen: list[dict] = []
    for row in rows:
        if row["source_sha256"] in used_hashes:
            continue
        if near_duplicate_distance and any(
            (row["dhash"] ^ prior).bit_count() <= near_duplicate_distance
            for prior in used_dhashes
        ):
            continue
        chosen.append(row)
        used_hashes.add(row["source_sha256"])
        used_dhashes.append(row["dhash"])
        if len(chosen) == count:
            return chosen
    raise SupplementError(f"only {len(chosen)} unique candidates available for quota {count}")


def build(
    bicycle_zip: Path, vehicle_zip: Path, helmet_zip: Path, baseline_zip: Path,
    output: Path, bicycle_count: int, bus_count: int, van_count: int,
    helmet_count: int, near_duplicate_distance: int,
) -> dict:
    if output.exists() or output.with_name(output.name + ".partial").exists():
        raise SupplementError(f"refusing to overwrite existing output: {output}")
    if not 0 <= near_duplicate_distance <= 64:
        raise SupplementError("near-duplicate-distance must be between 0 and 64")
    quotas = {
        "bicycle": bicycle_count,
        "bus": bus_count,
        "van": van_count,
        "helmet_manual_review": helmet_count,
    }
    if any(value < 1 for value in quotas.values()):
        raise SupplementError("all selection counts must be positive")

    source_sets = {
        "bicycle": _dataset_rows(bicycle_zip, "bicycle"),
        "bus": _dataset_rows(vehicle_zip, "bus"),
        "van": _dataset_rows(vehicle_zip, "van"),
        "helmet_manual_review": _dataset_rows(helmet_zip, "invalid"),
    }
    used_hashes = _image_hashes(baseline_zip)
    baseline_hash_count = len(used_hashes)
    used_dhashes: list[int] = []
    selected: list[tuple[str, dict]] = []
    for target, count in quotas.items():
        selected.extend(
            (target, row) for row in _select(
                source_sets[target], count, used_hashes, used_dhashes,
                near_duplicate_distance,
            )
        )

    partial = output.with_name(output.name + ".partial")
    upload = partial / "upload_images"
    docs = partial / "documentation_do_not_upload"
    upload.mkdir(parents=True)
    docs.mkdir()
    counters: Counter[str] = Counter()
    items: list[dict] = []
    for target, row in selected:
        counters[target] += 1
        filename = f"tavidm_v9_external_{target}_{counters[target]:04d}.jpg"
        data = row.pop("bytes")
        (upload / filename).write_bytes(data)
        candidate_classes = [] if target == "helmet_manual_review" else [target]
        items.append({
            **{key: value for key, value in row.items() if key != "dhash"},
            "package_filename": filename,
            "package_sha256": _sha256(data),
            "selection_target": target,
            "candidate_classes": candidate_classes,
            "semantic_mapping_authorized": False,
            "requires_full_reannotation": True,
            "annotation_status": "not_started",
            "dataset_role": "development_only_not_gold",
        })

    manifest = {
        "schema_version": "1.0.0",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset_role": "development_only_not_gold",
        "training_authorized": False,
        "promotion_authorized": False,
        "baseline_archive": str(baseline_zip.resolve()),
        "baseline_unique_image_hashes": baseline_hash_count,
        "annotation_classes": CLASSES,
        "total_images": len(items),
        "selection_counts": dict(counters),
        "items": items,
    }
    (docs / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    fields = sorted({key for row in items for key in row})
    with (docs / "review_queue.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in items:
            writer.writerow({
                **row,
                "candidate_classes": ";".join(row["candidate_classes"]),
                "source_labels": ";".join(row["source_labels"]),
            })
    (docs / "classes.txt").write_text("\n".join(CLASSES) + "\n", encoding="utf-8")
    (docs / "ANNOTATION_INSTRUCTIONS.md").write_text(
        "# TAVIDM V9 external supplement review\n\n"
        "Upload only the image ZIP. Every image requires complete human re-annotation using "
        "the exact 15-class roster; source labels are hints only and are not uploaded. Draw "
        "tight rectangular boxes around every clearly visible target object. Never convert "
        "`invalid`, `Half-Faced`, or another external helmet label automatically to "
        "`helmet_nut_shell`; accept that class only after visual review confirms the exact "
        "observable form. Flag uncertainty rather than guessing. This package is development "
        "only, not Gold, and does not authorize training, promotion, or deployment.\n",
        encoding="utf-8",
    )
    zip_path = partial / f"roboflow_upload_images_{len(items)}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for image in sorted(upload.iterdir()):
            archive.write(image, image.name)
    summary = {
        "total_images": len(items),
        "unique_sha256": len({row["package_sha256"] for row in items}),
        "selection_counts": dict(counters),
        "baseline_exact_duplicates_excluded": len(used_hashes) - baseline_hash_count - len(items),
        "upload_zip": str(output / zip_path.name),
    }
    (docs / "BUILD_SUMMARY.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    partial.rename(output)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bicycle-zip", type=Path, required=True)
    parser.add_argument("--vehicle-zip", type=Path, required=True)
    parser.add_argument("--helmet-zip", type=Path, required=True)
    parser.add_argument("--baseline-zip", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bicycle-count", type=int, default=200)
    parser.add_argument("--bus-count", type=int, default=100)
    parser.add_argument("--van-count", type=int, default=100)
    parser.add_argument("--helmet-count", type=int, default=100)
    parser.add_argument("--near-duplicate-distance", type=int, default=4)
    args = parser.parse_args()
    try:
        result = build(
            args.bicycle_zip, args.vehicle_zip, args.helmet_zip, args.baseline_zip,
            args.output, args.bicycle_count, args.bus_count, args.van_count,
            args.helmet_count, args.near_duplicate_distance,
        )
    except (OSError, ValueError, zipfile.BadZipFile, SupplementError) as exc:
        print(f"ERROR: {exc}")
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
