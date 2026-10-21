"""Build a provisional V9 derivative without modifying raw ZIPs."""

from __future__ import annotations

import argparse, ast, csv, hashlib, json, re, shutil, sys, zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


class MergeError(RuntimeError):
    pass


def _parse_names(text: str) -> list[str]:
    match = re.search(r"(?m)^names:\s*(\[[^\n]+\])\s*$", text)
    if not match:
        raise MergeError("data.yaml names list is missing or unsupported")
    names = ast.literal_eval(match.group(1))
    if not isinstance(names, list) or not names or not all(isinstance(x, str) for x in names):
        raise MergeError("data.yaml names must be a nonempty string list")
    return names


def _number(value: float) -> str:
    return f"{value:.10f}".rstrip("0").rstrip(".")


def convert_label_line(line: str, source_names: list[str], canonical_names: list[str]) -> tuple[str, bool]:
    parts = line.split()
    if len(parts) < 5:
        raise MergeError("annotation row has too few fields")
    try:
        source_id, values = int(parts[0]), [float(x) for x in parts[1:]]
    except ValueError as exc:
        raise MergeError("annotation row contains a nonnumeric value") from exc
    if not 0 <= source_id < len(source_names):
        raise MergeError("annotation class is outside the source schema")
    name = source_names[source_id]
    if name not in canonical_names:
        raise MergeError(f"noncanonical class: {name}")
    if any(not 0 <= value <= 1 for value in values):
        raise MergeError("annotation coordinate is outside [0, 1]")
    polygon = len(values) != 4
    if polygon:
        if len(values) < 6 or len(values) % 2:
            raise MergeError("polygon annotation must contain coordinate pairs")
        xs, ys = values[0::2], values[1::2]
        left, right, top, bottom = min(xs), max(xs), min(ys), max(ys)
        values = [(left + right) / 2, (top + bottom) / 2, right - left, bottom - top]
    if values[2] <= 0 or values[3] <= 0:
        raise MergeError("annotation box has nonpositive dimensions")
    return " ".join([str(canonical_names.index(name)), *map(_number, values)]), polygon


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _prefix(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:48]


def _write_pair(root, split, prefix, image_name, image_data, label_text, source_names, canonical_names, seen):
    digest = _hash_bytes(image_data)
    if digest in seen:
        raise MergeError(f"exact duplicate output: {image_name} and {seen[digest]}")
    seen[digest] = f"{split}/{image_name}"
    suffix = PurePosixPath(image_name).suffix.lower()
    output_name = f"{prefix[:20]}__{digest[:20]}{suffix}"
    image_path = root / split / "images" / output_name
    label_path = root / split / "labels" / f"{Path(output_name).stem}.txt"
    if image_path.exists() or label_path.exists():
        raise MergeError(f"output filename collision: {output_name}")
    rows, counts, polygons = [], Counter(), 0
    for line in label_text.splitlines():
        if not line.strip():
            continue
        converted, polygon = convert_label_line(line, source_names, canonical_names)
        rows.append(converted)
        counts[canonical_names[int(converted.split()[0])]] += 1
        polygons += int(polygon)
    image_path.write_bytes(image_data)
    label_path.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")
    return counts, polygons, output_name


def build(source_dir, ledger_path, supplements, canonical_names, output):
    if output.exists() or output.with_name(output.name + ".building").exists():
        raise MergeError(f"output exists: {output}")
    with ledger_path.open(newline="", encoding="utf-8-sig") as handle:
        ledger = list(csv.DictReader(handle))
    temp = output.with_name(output.name + ".building")
    for split in ("train", "valid", "test"):
        (temp / split / "images").mkdir(parents=True)
        (temp / split / "labels").mkdir(parents=True)
    images, instances = Counter(), {s: Counter() for s in ("train", "valid", "test")}
    seen, source_rows, archive_hashes, archives, schemas = {}, [], {}, {}, {}
    polygons = 0
    try:
        for row in ledger:
            split = row["proposed_split"]
            if split == "excluded_duplicate":
                continue
            if split not in instances:
                raise MergeError(f"unsupported split: {split}")
            archive_name = row["archive"]
            archive_path = source_dir / archive_name
            if archive_name not in archives:
                archive_hashes[archive_name] = _hash_file(archive_path)
                zf = zipfile.ZipFile(archive_path)
                yamls = [n for n in zf.namelist() if PurePosixPath(n).name == "data.yaml"]
                if len(yamls) != 1:
                    raise MergeError(f"{archive_name}: expected one data.yaml")
                archives[archive_name] = zf
                schemas[archive_name] = _parse_names(zf.read(yamls[0]).decode("utf-8-sig"))
            zf = archives[archive_name]
            image_data = zf.read(row["image_member"])
            if _hash_bytes(image_data) != row["image_sha256"]:
                raise MergeError(f"ledger hash mismatch: {archive_name}::{row['image_member']}")
            counts, converted, output_name = _write_pair(
                temp, split, _prefix(Path(archive_name).stem), row["image_member"], image_data,
                zf.read(row["label_member"]).decode("utf-8-sig"), schemas[archive_name], canonical_names, seen,
            )
            images[split] += 1; instances[split].update(counts); polygons += converted
            source_rows.append({"output": f"{split}/{output_name}", "source_group": row["source_group"], "source": f"{archive_name}::{row['image_member']}"})

        supplement_hashes = {}
        for supplement in supplements:
            supplement_hashes[supplement.name] = _hash_file(supplement)
            with zipfile.ZipFile(supplement) as zf:
                source_names = _parse_names(zf.read("data.yaml").decode("utf-8-sig"))
                supplement_manifest = json.loads(zf.read("MANIFEST.json"))
                manifest_groups = {
                    item["image"]: item.get("source_group")
                    for rows in supplement_manifest.get("splits", {}).values()
                    for item in rows
                }
                for split in ("valid", "test"):
                    members = sorted(n for n in zf.namelist() if n.startswith(f"{split}/images/") and not n.endswith("/"))
                    for member in members:
                        label = member.replace("/images/", "/labels/").rsplit(".", 1)[0] + ".txt"
                        counts, converted, output_name = _write_pair(
                            temp, split, _prefix(supplement.stem), member, zf.read(member),
                            zf.read(label).decode("utf-8-sig"), source_names, canonical_names, seen,
                        )
                        images[split] += 1; instances[split].update(counts); polygons += converted
                        source_group = manifest_groups.get(PurePosixPath(member).name)
                        if not source_group:
                            source_group = f"unknown-provenance:{supplement.stem}:{_hash_bytes(zf.read(member))[:20]}"
                        source_rows.append({"output": f"{split}/{output_name}", "source_group": source_group, "source": f"{supplement.name}::{member}"})

        (temp / "data.yaml").write_text(
            "path: .\ntrain: train/images\nval: valid/images\ntest: test/images\n\nnc: 15\nnames: ["
            + ", ".join(repr(x) for x in canonical_names) + "]\n", encoding="utf-8",
        )
        reports = temp / "reports"; reports.mkdir()
        with (reports / "source_ledger.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=("output", "source_group", "source")); writer.writeheader(); writer.writerows(source_rows)
        result = {
            "dataset_version": output.name, "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "provisional_merged_derivative_not_training_authorized", "canonical_names": canonical_names,
            "raw_archives_modified": False, "training_authorized": False, "promotion_authorized": False,
            "source_archive_sha256": archive_hashes, "supplement_sha256": supplement_hashes,
            "images_by_split": {s: images[s] for s in instances},
            "instances_by_split": {s: {n: instances[s][n] for n in canonical_names} for s in instances},
            "polygon_rows_converted_to_boxes": polygons, "exact_duplicates_in_output": 0,
            "known_limitations": [
                "bus supplement lacks original-source provenance",
                "weak-class supplement remains pending full semantic QA and stronger provenance",
                "bicycle validation and nutshell held-out counts remain below the planning floor of 20",
                "final promotion requires genuinely unseen Gold v3",
            ],
        }
        (temp / "manifest.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        temp.rename(output)
        return result
    except Exception:
        if temp.exists(): shutil.rmtree(temp)
        raise
    finally:
        for zf in archives.values(): zf.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--supplement", type=Path, action="append", default=[])
    parser.add_argument("--class-schema", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        canonical = json.loads(args.class_schema.read_text(encoding="utf-8"))["object_classes"]
        result = build(
            args.source_dir.resolve(), args.ledger.resolve(), [p.resolve() for p in args.supplement],
            canonical, args.output.resolve(),
        )
    except (MergeError, OSError, UnicodeError, KeyError, ValueError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
