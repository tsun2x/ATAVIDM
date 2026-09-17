"""Rebuild a review package by replacing reviewer-rejected Truck v4 images."""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from build_v9_ibt_review_package_v2 import PackageError, dataset_rows, sha256_file, spread


def rebuild(source: Path, truck_zip: Path, reject_names: list[str], output: Path) -> dict:
    if output.exists():
        raise PackageError(f"refusing to overwrite existing output: {output}")
    manifest_path = source / "documentation_do_not_upload" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = manifest["items"]
    by_name = {item["package_filename"]: item for item in items}
    rejects = set(reject_names)
    missing = rejects - by_name.keys()
    if missing:
        raise PackageError(f"unknown rejected package filenames: {sorted(missing)}")
    if any(by_name[name]["source"] != "truck_v4" for name in rejects):
        raise PackageError("this remediation accepts only Truck v4 package filenames")

    used_source_hashes = {item["source_sha256"] for item in items if item["package_filename"] not in rejects}
    rejected_source_hashes = {by_name[name]["source_sha256"] for name in rejects}
    candidates = dataset_rows(truck_zip, {"truck": "truck"}, set())
    available = [row for row in candidates
                 if row["source_sha256"] not in used_source_hashes | rejected_source_hashes]
    replacements = iter(spread(available, len(rejects)))

    upload = output / "upload_images"
    docs = output / "documentation_do_not_upload"
    upload.mkdir(parents=True)
    docs.mkdir()
    new_items, replacement_rows = [], []
    for old in items:
        name = old["package_filename"]
        target = upload / name
        if name not in rejects:
            shutil.copy2(source / "upload_images" / name, target)
            new_items.append({**old, "sha256": sha256_file(target)})
            continue
        row = next(replacements)
        image = cv2.imdecode(np.frombuffer(row.pop("bytes"), np.uint8), cv2.IMREAD_COLOR)
        if image is None or not cv2.imwrite(str(target), image, [cv2.IMWRITE_JPEG_QUALITY, 95]):
            raise PackageError(f"cannot write replacement: {target}")
        new = {**row, "package_filename": name, "sha256": sha256_file(target),
               "source": "truck_v4", "dataset_role": "development_only_not_gold",
               "annotation_status": "not_started"}
        new_items.append(new)
        replacement_rows.append({"package_filename": name,
                                 "rejected_source_sha256": old["source_sha256"],
                                 "replacement_source_sha256": new["source_sha256"],
                                 "replacement_source_path": new["source_path"]})
    if len({item["sha256"] for item in new_items}) != len(new_items):
        raise PackageError("rebuilt package contains exact duplicate images")
    manifest.update({"created_at_utc": datetime.now(timezone.utc).isoformat(),
                     "items": new_items, "total_images": len(new_items),
                     "qa_status": "replacement_candidates_pending_visual_review"})
    (docs / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    fields = sorted({key for item in new_items for key in item})
    with (docs / "review_queue.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in new_items:
            writer.writerow({**item, "candidate_classes": ";".join(item["candidate_classes"])})
    for filename in ("classes.txt", "ANNOTATION_INSTRUCTIONS.md"):
        shutil.copy2(source / "documentation_do_not_upload" / filename, docs / filename)
    (docs / "REPLACEMENT_SUMMARY.json").write_text(
        json.dumps({"replacements": replacement_rows}, indent=2), encoding="utf-8")
    zip_base = output / f"roboflow_upload_images_{len(new_items)}"
    shutil.make_archive(str(zip_base), "zip", upload)
    summary = {"total_images": len(new_items), "unique_sha256": len(new_items),
               "replacements": len(replacement_rows), "upload_zip": str(zip_base.with_suffix(".zip")),
               "qa_status": "replacement_candidates_pending_visual_review"}
    (docs / "BUILD_SUMMARY.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-package", type=Path, required=True)
    parser.add_argument("--truck-zip", type=Path, required=True)
    parser.add_argument("--reject-list", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        names = [line.strip() for line in args.reject_list.read_text(encoding="utf-8").splitlines()
                 if line.strip()]
        if not names or len(names) != len(set(names)):
            raise PackageError("reject list must contain unique filenames")
        result = rebuild(args.source_package, args.truck_zip, names, args.output)
    except (OSError, ValueError, zipfile.BadZipFile, PackageError) as exc:
        print(f"ERROR: {exc}")
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
