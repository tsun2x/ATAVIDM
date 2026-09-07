"""Build a small, clear, diverse, training-only pickup candidate supplement."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageStat


class SelectionError(RuntimeError):
    """A fail-closed ledger, schema, or provenance error."""


AUDIT_FIELDS = (
    "output_image", "source", "source_group", "image_sha256", "manual_decision",
    "prescreen_suggestion", "prescreen_reasons", "pickup_instances", "context_instances",
    "max_pickup_area_pixels", "sharpness_score", "selection_status", "selection_reason",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_csv(path: Path, required: set[str]) -> list[dict[str, str]]:
    if not path.is_file():
        raise SelectionError(f"required CSV missing: {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise SelectionError(f"{path.name} missing columns: {sorted(missing)}")
        return list(reader)


def _index_unique(rows: list[dict[str, str]], key: str, label: str) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        value = row[key]
        if not value or value in result:
            raise SelectionError(f"blank or duplicate {key} in {label}: {value!r}")
        result[value] = row
    return result


def _read_boxes(path: Path, class_count: int, pickup_class_id: int) -> tuple[list[tuple[int, float, float, float, float]], int, int]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise SelectionError(f"accepted label unreadable: {path}: {exc}") from exc
    boxes: list[tuple[int, float, float, float, float]] = []
    for line in lines:
        parts = line.split()
        if len(parts) != 5:
            raise SelectionError(f"accepted label malformed: {path}")
        try:
            class_id = int(parts[0])
            x, y, width, height = (float(value) for value in parts[1:])
        except ValueError as exc:
            raise SelectionError(f"accepted label malformed: {path}") from exc
        if not 0 <= class_id < class_count:
            raise SelectionError(f"accepted label class out of range: {path}")
        boxes.append((class_id, x, y, width, height))
    pickup_count = sum(class_id == pickup_class_id for class_id, *_ in boxes)
    return boxes, pickup_count, len(boxes) - pickup_count


def _sharpness(image: Image.Image) -> float:
    gray = image.convert("L")
    width = min(256, gray.width)
    height = min(256, gray.height)
    pixels = list(gray.resize((width, height)).get_flattened_data())
    if len(pixels) < 2:
        return 0.0
    return sum(abs(left - right) for left, right in zip(pixels, pixels[1:])) / (len(pixels) - 1)


def _dhash(image: Image.Image) -> int:
    pixels = list(image.convert("L").resize((9, 8)).get_flattened_data())
    value = 0
    for row in range(8):
        offset = row * 9
        for column in range(8):
            value = (value << 1) | (pixels[offset + column] > pixels[offset + column + 1])
    return value


def _write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def build(
    clean_dataset: Path,
    prescreen_path: Path,
    output: Path,
    per_source: int,
    border_margin: float,
    near_duplicate_distance: int,
    max_per_group: int,
) -> dict[str, object]:
    if output.exists():
        raise SelectionError(f"output already exists; choose a new version: {output}")
    if per_source < 1 or max_per_group < 1:
        raise SelectionError("per-source and max-per-group must be positive")
    if not 0 <= border_margin < 0.5 or not 0 <= near_duplicate_distance <= 64:
        raise SelectionError("invalid border margin or near-duplicate distance")

    manifest_path = clean_dataset / "manifest.json"
    try:
        clean_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        names = clean_manifest["names"]
        pickup_class_id = int(clean_manifest["pickup_class_id"])
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise SelectionError(f"invalid clean manifest: {exc}") from exc
    if clean_manifest.get("status") != "provisional_not_approved_for_training":
        raise SelectionError("clean dataset has unexpected status")
    if not isinstance(names, list) or pickup_class_id >= len(names) or names[pickup_class_id] != "pickup_truck":
        raise SelectionError("clean dataset pickup schema mismatch")

    cleaning_rows = _read_csv(clean_dataset / "reports" / "cleaning_audit.csv", {
        "output_image", "source", "source_group", "image_sha256", "manual_decision",
        "final_status", "pickup_instances", "context_instances",
    })
    accepted = [row for row in cleaning_rows if row["final_status"] in {"accepted_manual_keep", "accepted_provisional"}]
    queue = _index_unique(_read_csv(clean_dataset / "reports" / "review_queue_snapshot.csv", {
        "output_image", "source", "source_group", "image_sha256", "pickup_instances", "context_instances",
    }), "output_image", "review queue")
    prescreen = _index_unique(_read_csv(prescreen_path, {
        "output_image", "source", "image_sha256", "suggestion", "reasons",
    }), "output_image", "pre-screen report")

    candidates: list[dict[str, object]] = []
    audit_by_name: dict[str, dict[str, str]] = {}
    for row in accepted:
        name = row["output_image"]
        if Path(name).name != name:
            raise SelectionError(f"unsafe candidate filename: {name!r}")
        queue_row = queue.get(name)
        screen_row = prescreen.get(name)
        if queue_row is None or screen_row is None:
            raise SelectionError(f"accepted candidate missing from source ledger: {name}")
        if not (row["source"] == queue_row["source"] == screen_row["source"]):
            raise SelectionError(f"source mismatch across ledgers: {name}")
        if not (row["image_sha256"].lower() == queue_row["image_sha256"].lower() == screen_row["image_sha256"].lower()):
            raise SelectionError(f"hash mismatch across ledgers: {name}")
        image_path = clean_dataset / "train" / "images" / name
        label_path = clean_dataset / "train" / "labels" / f"{Path(name).stem}.txt"
        if not image_path.is_file() or _sha256(image_path) != row["image_sha256"].lower():
            raise SelectionError(f"accepted image missing or hash mismatch: {name}")
        boxes, pickup_count, context_count = _read_boxes(label_path, len(names), pickup_class_id)
        manual_keep = row["manual_decision"] == "keep" or row["final_status"] == "accepted_manual_keep"
        base = {
            "output_image": name,
            "source": row["source"],
            "source_group": row["source_group"],
            "image_sha256": row["image_sha256"],
            "manual_decision": "keep" if manual_keep else row["manual_decision"],
            "prescreen_suggestion": screen_row["suggestion"],
            "prescreen_reasons": screen_row["reasons"],
            "pickup_instances": str(pickup_count),
            "context_instances": str(context_count),
            "max_pickup_area_pixels": "",
            "sharpness_score": "",
            "selection_status": "",
            "selection_reason": "",
        }
        if pickup_count != int(queue_row["pickup_instances"]) or context_count != int(queue_row["context_instances"]):
            base["selection_status"] = "excluded_annotation_count_mismatch"
            base["selection_reason"] = f"actual={pickup_count}/{context_count};queue={queue_row['pickup_instances']}/{queue_row['context_instances']}"
            audit_by_name[name] = base
            continue
        if not manual_keep and screen_row["suggestion"] != "manual_review":
            base["selection_status"] = "excluded_prescreen_quality"
            base["selection_reason"] = screen_row["reasons"] or screen_row["suggestion"]
            audit_by_name[name] = base
            continue
        pickup_boxes = [box for box in boxes if box[0] == pickup_class_id]
        if not manual_keep and any(
            x - width / 2 <= border_margin or x + width / 2 >= 1 - border_margin
            or y - height / 2 <= border_margin or y + height / 2 >= 1 - border_margin
            for _, x, y, width, height in pickup_boxes
        ):
            base["selection_status"] = "excluded_border_truncated"
            base["selection_reason"] = f"pickup_within_{border_margin:.4f}_of_border"
            audit_by_name[name] = base
            continue
        try:
            with Image.open(image_path) as opened:
                image = opened.convert("RGB")
        except OSError as exc:
            raise SelectionError(f"accepted image unreadable: {name}: {exc}") from exc
        max_area = max(width * image.width * height * image.height for _, _, _, width, height in pickup_boxes)
        sharpness = _sharpness(image)
        dhash = _dhash(image)
        base["max_pickup_area_pixels"] = f"{max_area:.2f}"
        base["sharpness_score"] = f"{sharpness:.4f}"
        candidates.append({"row": row, "audit": base, "image": image_path, "label": label_path, "dhash": dhash, "manual": manual_keep, "area": max_area, "sharpness": sharpness})

    selected: list[dict[str, object]] = []
    candidates_by_source: dict[str, list[dict[str, object]]] = defaultdict(list)
    for candidate in candidates:
        candidates_by_source[str(candidate["row"]["source"])].append(candidate)  # type: ignore[index]
    for source, source_candidates in sorted(candidates_by_source.items()):
        group_count = len({str(candidate["row"]["source_group"]) for candidate in source_candidates})  # type: ignore[index]
        ranked = sorted(source_candidates, key=lambda candidate: (
            not bool(candidate["manual"]), -float(candidate["area"]), -float(candidate["sharpness"]), str(candidate["row"]["image_sha256"]),  # type: ignore[index]
        ))
        chosen_hashes: list[int] = []
        chosen_groups: Counter[str] = Counter()
        chosen = 0
        for candidate in ranked:
            audit = candidate["audit"]
            group = str(candidate["row"]["source_group"])  # type: ignore[index]
            if chosen >= per_source:
                audit["selection_status"] = "excluded_source_cap"  # type: ignore[index]
                audit["selection_reason"] = f"source_cap={per_source}"  # type: ignore[index]
            elif group_count > 1 and chosen_groups[group] >= max_per_group and not candidate["manual"]:
                audit["selection_status"] = "excluded_group_cap"  # type: ignore[index]
                audit["selection_reason"] = f"group_cap={max_per_group}"  # type: ignore[index]
            elif near_duplicate_distance > 0 and not candidate["manual"] and any((int(candidate["dhash"]) ^ prior).bit_count() <= near_duplicate_distance for prior in chosen_hashes):
                audit["selection_status"] = "excluded_near_duplicate"  # type: ignore[index]
                audit["selection_reason"] = f"dhash_distance<={near_duplicate_distance}"  # type: ignore[index]
            else:
                audit["selection_status"] = "selected_manual_keep" if candidate["manual"] else "selected_high_confidence"  # type: ignore[index]
                audit["selection_reason"] = "manual_keep_override" if candidate["manual"] else "structural+prescreen+border+diversity_pass"  # type: ignore[index]
                selected.append(candidate)
                chosen_hashes.append(int(candidate["dhash"]))
                chosen_groups[group] += 1
                chosen += 1
            audit_by_name[str(candidate["row"]["output_image"])] = audit  # type: ignore[index]

    temp = output.with_name(output.name + ".building")
    if temp.exists():
        raise SelectionError(f"temporary output already exists: {temp}")
    try:
        (temp / "train" / "images").mkdir(parents=True)
        (temp / "train" / "labels").mkdir(parents=True)
        (temp / "reports").mkdir(parents=True)
        for candidate in selected:
            shutil.copy2(candidate["image"], temp / "train" / "images" / Path(candidate["image"]).name)
            shutil.copy2(candidate["label"], temp / "train" / "labels" / Path(candidate["label"]).name)
        audit_rows = [audit_by_name[name] for name in sorted(audit_by_name)]
        _write_csv(temp / "reports" / "selection_audit.csv", AUDIT_FIELDS, audit_rows)
        selected_rows = [row for row in audit_rows if row["selection_status"].startswith("selected_")]
        excluded_rows = [row for row in audit_rows if row["selection_status"].startswith("excluded_")]
        _write_csv(temp / "reports" / "selected_candidates.csv", AUDIT_FIELDS, selected_rows)
        _write_csv(temp / "reports" / "excluded_candidates.csv", AUDIT_FIELDS, excluded_rows)
        count_rows = [
            {"source": source, "selection_status": status, "count": str(count)}
            for (source, status), count in sorted(Counter((row["source"], row["selection_status"]) for row in audit_rows).items())
        ]
        _write_csv(temp / "reports" / "counts_by_source_and_status.csv", ("source", "selection_status", "count"), count_rows)
        data_yaml = "path: .\ntrain: train/images\nval: null\ntest: null\nnames:\n" + "".join(f"  {index}: {name}\n" for index, name in enumerate(names))
        (temp / "data.yaml").write_text(data_yaml, encoding="utf-8")
        selected_by_source = dict(sorted(Counter(row["source"] for row in selected_rows).items()))
        status_counts = dict(sorted(Counter(row["selection_status"] for row in audit_rows).items()))
        result: dict[str, object] = {
            "dataset_version": output.name,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "high_confidence_candidates_not_semantically_verified",
            "training_only": True,
            "merge_authorized": False,
            "training_authorized": False,
            "policy": "Clear structurally valid pickup candidates selected conservatively; manual keeps preserved; contextual annotations retained; semantic visual verification still required.",
            "source_clean_dataset": str(clean_dataset),
            "source_clean_manifest_sha256": _sha256(manifest_path),
            "source_prescreen_report": str(prescreen_path),
            "source_prescreen_sha256": _sha256(prescreen_path),
            "names": names,
            "pickup_class_id": pickup_class_id,
            "selection_parameters": {"per_source": per_source, "border_margin": border_margin, "near_duplicate_distance": near_duplicate_distance, "max_per_group": max_per_group},
            "selected_by_source": selected_by_source,
            "status_counts": status_counts,
            "reports": ["reports/selection_audit.csv", "reports/selected_candidates.csv", "reports/excluded_candidates.csv", "reports/counts_by_source_and_status.csv"],
        }
        (temp / "manifest.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temp.rename(output)
        return result
    except Exception:
        if temp.exists():
            shutil.rmtree(temp)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clean-dataset", type=Path, required=True)
    parser.add_argument("--prescreen", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--per-source", type=int, default=800)
    parser.add_argument("--border-margin", type=float, default=0.005)
    parser.add_argument("--near-duplicate-distance", type=int, default=6)
    parser.add_argument("--max-per-group", type=int, default=50)
    args = parser.parse_args()
    try:
        result = build(args.clean_dataset.resolve(), args.prescreen.resolve(), args.output.resolve(), args.per_source, args.border_margin, args.near_duplicate_distance, args.max_per_group)
    except (SelectionError, OSError, csv.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
