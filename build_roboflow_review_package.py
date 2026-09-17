"""Create a provenance-preserving 500-image Roboflow review pilot."""
from __future__ import annotations

import csv, hashlib, json, shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(r"D:\tavidm\dataset\review_packages\roboflow_v9_pilot_500_20260910")
BALI = Path(r"D:\tavidm-annotation-workspace\cctv_trafwmsu_20260903")
ZPPSU = Path(r"D:\tavidm-annotation-workspace\zppsu_2026-09-08_annotation_candidates_v1")
WEAK = Path(r"D:\yolo zip tavidm datasets\local_training\vehicle_v1_supplement_weak_v1_source")
CLASSES = ["car", "van", "jeepney", "tricycle", "autorickshaw", "bus", "truck",
           "pickup_truck", "motorcycle", "bicycle", "person", "rider"]
WEAK_MAP = {1: "bus", 3: "jeepney", 5: "truck"}


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def cctv_groups(root: Path, source: str) -> dict[str, list[dict]]:
    result = {}
    for mp in sorted(root.glob("*/manifest.json")):
        m = json.loads(mp.read_text(encoding="utf-8"))
        group = m["source_group"]
        result[group] = []
        for frame in m["frames"]:
            image = mp.parent / frame["relative_path"]
            result[group].append({
                "source": source, "source_group": group, "source_path": str(image),
                "source_sha256": frame["sha256"], "source_video": m.get("source_video"),
                "source_video_sha256": m.get("source_video_sha256"),
                "frame_index": frame.get("frame_index"), "timestamp_ms": frame.get("timestamp_ms"),
                "candidate_classes": [], "license": "local_academic_cctv_restricted",
                "selection_reason": "location_time_traffic_diversity"})
    return result


def round_robin(groups: dict[str, list[dict]], needed: int) -> list[dict]:
    selected, positions = [], defaultdict(int)
    while len(selected) < needed:
        added = False
        for group in sorted(groups):
            if positions[group] < len(groups[group]):
                selected.append(groups[group][positions[group]])
                positions[group] += 1
                added = True
                if len(selected) == needed:
                    break
        if not added:
            raise RuntimeError(f"Only {len(selected)} of {needed} frames available")
    return selected


def weak_rows() -> list[dict]:
    rows = []
    for split in ("train", "valid", "test"):
        for label in sorted((WEAK / split / "labels").glob("*.txt")):
            ids = {int(x.split()[0]) for x in label.read_text().splitlines() if x.strip()}
            names = sorted({WEAK_MAP[x] for x in ids if x in WEAK_MAP})
            if not names:
                continue
            images = list((WEAK / split / "images").glob(label.stem + ".*"))
            if len(images) != 1:
                raise RuntimeError(f"Image pairing failure: {label}")
            image = images[0]
            rows.append({
                "source": "roboflow_datasetv5_v2", "source_group": "roboflow_datasetv5_v2_" + split,
                "source_path": str(image), "source_sha256": digest(image), "source_video": None,
                "source_video_sha256": None, "frame_index": None, "timestamp_ms": None,
                "candidate_classes": names, "license": "CC_BY_4.0",
                "selection_reason": "weak_class_candidate_human_review_required"})
    return rows


def select_weak() -> list[dict]:
    rows, chosen, used = weak_rows(), [], set()
    for name, target in (("bus", 34), ("jeepney", 33), ("truck", 33)):
        for row in rows:
            if name not in row["candidate_classes"] or row["source_sha256"] in used:
                continue
            chosen.append(row); used.add(row["source_sha256"])
            if sum(name in x["candidate_classes"] for x in chosen) >= target:
                break
        if sum(name in x["candidate_classes"] for x in chosen) < target:
            raise RuntimeError(f"Insufficient unique {name} candidates")
    for row in rows:
        if len(chosen) == 100:
            break
        if row["source_sha256"] not in used:
            chosen.append(row)
            used.add(row["source_sha256"])
    if len(chosen) != 100:
        raise RuntimeError("Insufficient total unique weak-class candidates")
    return chosen


def main() -> None:
    if OUT.exists():
        raise FileExistsError(OUT)
    rows = round_robin(cctv_groups(BALI, "baliwasan"), 200)
    rows += round_robin(cctv_groups(ZPPSU, "zppsu"), 200)
    rows += select_weak()
    hashes = {x["source_sha256"] for x in rows}
    if len(rows) != 500 or len(hashes) != 500:
        raise RuntimeError("Package count or exact-hash uniqueness check failed")

    upload = OUT / "upload_images"
    docs = OUT / "documentation_do_not_upload"
    upload.mkdir(parents=True)
    docs.mkdir()
    counts, output_rows = defaultdict(int), []
    for row in rows:
        counts[row["source"]] += 1
        ext = Path(row["source_path"]).suffix.lower()
        name = f"tavidm_v9_{row['source']}_{counts[row['source']]:04d}{ext}"
        target = upload / name
        shutil.copy2(row["source_path"], target)
        if digest(target) != row["source_sha256"]:
            raise RuntimeError(f"Copy hash mismatch: {target}")
        output_rows.append({**row, "package_filename": name, "annotation_status": "not_started"})

    manifest = {
        "schema_version": "1.0.0",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset_role": "development_only_not_gold",
        "training_authorized": False,
        "promotion_authorized": False,
        "total_images": 500,
        "source_counts": dict(counts),
        "annotation_classes": CLASSES,
        "side_mirror_policy": "separate_close_view_package",
        "items": output_rows,
    }
    (docs / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    fields = list(output_rows[0])
    with (docs / "review_queue.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in output_rows:
            writer.writerow({**row, "candidate_classes": ";".join(row["candidate_classes"])})
    (docs / "classes.txt").write_text("\n".join(CLASSES) + "\n", encoding="utf-8")
    instructions = """# TAVIDM V9 Roboflow review instructions

Upload only `upload_images` or `roboflow_upload_images_500.zip`. Keep this documentation folder locally.

## Exact classes
car, van, jeepney, tricycle, autorickshaw, bus, truck, pickup_truck, motorcycle,
bicycle, person, rider

## Labeling rules
- Box every clearly visible target object, not only the weak class that caused selection.
- Use tight boxes around the visible object; exclude large shadows and background.
- `car` includes SUVs and crossovers.
- `van` is a visible body type; do not infer operating status.
- `pickup_truck` has an open or separately defined cargo bed; do not call it `truck`.
- `tricycle` is a motorcycle with sidecar; `autorickshaw` has an integrated body.
- Use `person` for pedestrians or standing people and `rider` for an active rider.
- Do not label side mirrors in this general CCTV batch. They need close-view images.
- If a class is unclear, comment and flag it for review; never guess.
- Review crowded scenes and every weak-class label with a second teammate.

Export the completed project as YOLOv8 and preserve the export ZIP unchanged.
Do not let Roboflow randomly define the final train/valid/test split; grouping by source
video/location must be performed locally first.
"""
    (docs / "ANNOTATION_INSTRUCTIONS.md").write_text(instructions, encoding="utf-8")
    shutil.copy2(WEAK / "README.roboflow.txt", docs / "WEAK_SOURCE_LICENSE_AND_PROVENANCE.txt")
    shutil.make_archive(str(OUT / "roboflow_upload_images_500"), "zip", upload)
    summary = {
        "total_images": 500,
        "unique_sha256": 500,
        "source_counts": dict(counts),
        "upload_zip": str(OUT / "roboflow_upload_images_500.zip"),
    }
    (docs / "BUILD_SUMMARY.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
