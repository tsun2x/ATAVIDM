"""Generate a deterministic, annotated, source-balanced pickup audit package."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


class AuditError(RuntimeError):
    """A fail-closed audit input or provenance error."""


SELECTED_REQUIRED = {"output_image", "source", "image_sha256", "selection_status"}
DECISION_FIELDS = ("audit_id", "output_image", "source", "image_sha256", "decision", "note")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_selected(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise AuditError(f"selected-candidates report missing: {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = SELECTED_REQUIRED - set(reader.fieldnames or ())
        if missing:
            raise AuditError(f"selected-candidates report missing columns: {sorted(missing)}")
        rows = list(reader)
    seen: set[str] = set()
    for row in rows:
        name = row["output_image"]
        if not name or Path(name).name != name or name in seen:
            raise AuditError(f"unsafe or duplicate selected filename: {name!r}")
        if not row["selection_status"].startswith("selected_"):
            raise AuditError(f"non-selected row in selected-candidates report: {name}")
        seen.add(name)
    return rows


def _read_boxes(path: Path, class_count: int) -> list[tuple[int, float, float, float, float]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise AuditError(f"label unreadable: {path}: {exc}") from exc
    boxes = []
    for line in lines:
        parts = line.split()
        if len(parts) != 5:
            raise AuditError(f"label malformed: {path}")
        try:
            class_id = int(parts[0])
            x, y, width, height = (float(value) for value in parts[1:])
        except ValueError as exc:
            raise AuditError(f"label malformed: {path}") from exc
        if not 0 <= class_id < class_count:
            raise AuditError(f"label class out of range: {path}")
        if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < width <= 1 and 0 < height <= 1):
            raise AuditError(f"label coordinates invalid: {path}")
        boxes.append((class_id, x, y, width, height))
    return boxes


def _annotate(image_path: Path, boxes: list[tuple[int, float, float, float, float]], names: list[str], pickup_class_id: int, audit_id: str) -> Image.Image:
    try:
        with Image.open(image_path) as opened:
            source = opened.convert("RGB")
    except OSError as exc:
        raise AuditError(f"image unreadable: {image_path}: {exc}") from exc
    header_height = max(30, source.height // 20)
    canvas = Image.new("RGB", (source.width, source.height + header_height), "#111111")
    canvas.paste(source, (0, header_height))
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    draw.text((8, 8), f"{audit_id} | RED=pickup_truck | BLUE=context", fill="white", font=font)
    line_width = max(2, min(source.width, source.height) // 250)
    for class_id, x, y, width, height in boxes:
        left = max(0, int((x - width / 2) * source.width))
        right = min(source.width - 1, int((x + width / 2) * source.width))
        top = max(0, int((y - height / 2) * source.height)) + header_height
        bottom = min(source.height - 1, int((y + height / 2) * source.height)) + header_height
        color = "#ff2020" if class_id == pickup_class_id else "#2080ff"
        draw.rectangle((left, top, right, bottom), outline=color, width=line_width)
        label = names[class_id]
        label_box = draw.textbbox((left, top), label, font=font)
        text_width = label_box[2] - label_box[0]
        text_height = label_box[3] - label_box[1]
        label_top = max(header_height, top - text_height - 4)
        draw.rectangle((left, label_top, left + text_width + 6, label_top + text_height + 4), fill=color)
        draw.text((left + 3, label_top + 2), label, fill="white", font=font)
    return canvas


def _make_sheets(annotated: list[tuple[str, Path]], destination: Path, columns: int, rows: int) -> list[Path]:
    destination.mkdir(parents=True, exist_ok=True)
    cell_width, cell_height = 320, 230
    capacity = columns * rows
    results = []
    for page_index, start in enumerate(range(0, len(annotated), capacity), start=1):
        page_rows = annotated[start:start + capacity]
        sheet = Image.new("RGB", (columns * cell_width, rows * cell_height), "#303030")
        draw = ImageDraw.Draw(sheet)
        font = ImageFont.load_default()
        for offset, (audit_id, path) in enumerate(page_rows):
            with Image.open(path) as opened:
                thumbnail = ImageOps.contain(opened.convert("RGB"), (cell_width - 8, cell_height - 24))
            column = offset % columns
            row = offset // columns
            x = column * cell_width + (cell_width - thumbnail.width) // 2
            y = row * cell_height + 20 + (cell_height - 20 - thumbnail.height) // 2
            sheet.paste(thumbnail, (x, y))
            draw.text((column * cell_width + 6, row * cell_height + 4), audit_id, fill="white", font=font)
        path = destination / f"contact_sheet_{page_index:02d}.jpg"
        sheet.save(path, quality=92, subsampling=0)
        results.append(path)
    return results


def build(dataset: Path, output: Path, per_source: int, sheet_columns: int, sheet_rows: int) -> dict[str, object]:
    if output.exists():
        raise AuditError(f"output already exists; choose a new version: {output}")
    if per_source < 1 or sheet_columns < 1 or sheet_rows < 1:
        raise AuditError("sample and sheet dimensions must be positive")
    manifest_path = dataset / "manifest.json"
    try:
        source_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        names = source_manifest["names"]
        pickup_class_id = int(source_manifest["pickup_class_id"])
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise AuditError(f"invalid source manifest: {exc}") from exc
    if source_manifest.get("status") != "high_confidence_candidates_not_semantically_verified":
        raise AuditError("source dataset has unexpected status")
    if not isinstance(names, list) or pickup_class_id >= len(names) or names[pickup_class_id] != "pickup_truck":
        raise AuditError("source pickup schema mismatch")

    selected_path = dataset / "reports" / "selected_candidates.csv"
    selected = _read_selected(selected_path)
    by_source: dict[str, list[dict[str, str]]] = {}
    for row in selected:
        image_path = dataset / "train" / "images" / row["output_image"]
        if not image_path.is_file() or _sha256(image_path) != row["image_sha256"].lower():
            raise AuditError(f"image hash mismatch: {row['output_image']}")
        label_path = dataset / "train" / "labels" / f"{Path(row['output_image']).stem}.txt"
        if not label_path.is_file():
            raise AuditError(f"label missing: {row['output_image']}")
        by_source.setdefault(row["source"], []).append(row)
    required_sources = {"thai", "mio"}
    if set(by_source) != required_sources:
        raise AuditError(f"expected exactly Thai and MIO sources, found: {sorted(by_source)}")
    if any(len(by_source[source]) < per_source for source in required_sources):
        raise AuditError("not enough selected candidates for balanced audit")

    sample: list[dict[str, str]] = []
    for source in sorted(required_sources):
        ranked = sorted(by_source[source], key=lambda row: hashlib.sha256(("pickup-semantic-audit-v1|" + row["image_sha256"]).encode()).hexdigest())
        for index, row in enumerate(ranked[:per_source], start=1):
            sample.append({**row, "audit_id": f"{source.upper()}-{index:03d}"})
    sample.sort(key=lambda row: row["audit_id"])

    temp = output.with_name(output.name + ".building")
    if temp.exists():
        raise AuditError(f"temporary output already exists: {temp}")
    try:
        annotated_dir = temp / "annotated_images"
        annotated_dir.mkdir(parents=True)
        rendered: list[tuple[str, Path]] = []
        decision_rows: list[dict[str, str]] = []
        sample_rows: list[dict[str, str]] = []
        for row in sample:
            name = row["output_image"]
            label_path = dataset / "train" / "labels" / f"{Path(name).stem}.txt"
            boxes = _read_boxes(label_path, len(names))
            if not any(box[0] == pickup_class_id for box in boxes):
                raise AuditError(f"sample has no pickup annotation: {name}")
            annotated = _annotate(dataset / "train" / "images" / name, boxes, names, pickup_class_id, row["audit_id"])
            annotated_path = annotated_dir / f"{row['audit_id']}__{name}"
            annotated.save(annotated_path, quality=94, subsampling=0)
            rendered.append((row["audit_id"], annotated_path))
            decision_rows.append({"audit_id": row["audit_id"], "output_image": name, "source": row["source"], "image_sha256": row["image_sha256"], "decision": "", "note": ""})
            sample_rows.append({"audit_id": row["audit_id"], "output_image": name, "source": row["source"], "image_sha256": row["image_sha256"], "annotated_image": annotated_path.name})
        sheets = _make_sheets(rendered, temp / "contact_sheets", sheet_columns, sheet_rows)
        with (temp / "audit_decisions.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=DECISION_FIELDS)
            writer.writeheader()
            writer.writerows(decision_rows)
        with (temp / "audit_sample.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=("audit_id", "output_image", "source", "image_sha256", "annotated_image"))
            writer.writeheader()
            writer.writerows(sample_rows)
        (temp / "README.md").write_text(
            "# Pickup semantic audit\n\nReview the contact sheets or annotated images. Red boxes are `pickup_truck`; blue boxes are contextual classes.\n\nIn `audit_decisions.csv`, enter only `keep`, `reject`, or `uncertain` in the `decision` column. Use `note` for a brief reason. Do not change identity or hash columns. This package does not authorize merging or training.\n",
            encoding="utf-8",
        )
        result: dict[str, object] = {
            "audit_version": output.name,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "pending_manual_semantic_review",
            "source_dataset": str(dataset),
            "source_manifest_sha256": _sha256(manifest_path),
            "source_selected_report_sha256": _sha256(selected_path),
            "sampling": "deterministic SHA-256 rank with pickup-semantic-audit-v1 salt",
            "per_source": per_source,
            "sample_counts": dict(sorted(Counter(row["source"] for row in sample).items())),
            "allowed_decisions": ["keep", "reject", "uncertain"],
            "contact_sheets": [path.relative_to(temp).as_posix() for path in sheets],
            "annotated_images": len(rendered),
            "merge_authorized": False,
            "training_authorized": False,
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
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--per-source", type=int, default=50)
    parser.add_argument("--sheet-columns", type=int, default=4)
    parser.add_argument("--sheet-rows", type=int, default=5)
    args = parser.parse_args()
    try:
        result = build(args.dataset.resolve(), args.output.resolve(), args.per_source, args.sheet_columns, args.sheet_rows)
    except (AuditError, OSError, csv.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
