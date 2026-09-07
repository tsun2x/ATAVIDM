"""Record an owner-reviewed pickup semantic sample without authorizing merge/training."""

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


class FinalizationError(RuntimeError):
    """A fail-closed review-ledger or provenance error."""


IDENTITY_FIELDS = ("audit_id", "output_image", "source", "image_sha256")
DECISION_FIELDS = (*IDENTITY_FIELDS, "decision", "note")
OUTPUT_FIELDS = (*IDENTITY_FIELDS, "decision", "note", "reviewed_by", "reviewed_at")
ALLOWED_DECISIONS = {"keep", "reject", "uncertain"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_csv(path: Path, required: tuple[str, ...]) -> list[dict[str, str]]:
    if not path.is_file():
        raise FinalizationError(f"required audit file missing: {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = set(required) - set(reader.fieldnames or ())
        if missing:
            raise FinalizationError(f"{path.name} missing columns: {sorted(missing)}")
        return list(reader)


def _identities(rows: list[dict[str, str]], label: str) -> dict[str, tuple[str, str, str, str]]:
    result = {}
    for row in rows:
        audit_id = row["audit_id"]
        identity = tuple(row[field] for field in IDENTITY_FIELDS)
        if not audit_id or audit_id in result:
            raise FinalizationError(f"blank or duplicate audit_id in {label}: {audit_id!r}")
        result[audit_id] = identity
    return result


def finalize(audit: Path, output: Path, decision: str, reviewed_by: str) -> dict[str, object]:
    if output.exists():
        raise FinalizationError(f"output already exists; choose a new reviewed version: {output}")
    if decision not in ALLOWED_DECISIONS:
        raise FinalizationError(f"decision must be one of {sorted(ALLOWED_DECISIONS)}")
    if not reviewed_by.strip():
        raise FinalizationError("reviewed-by must be non-empty")
    manifest_path = audit / "manifest.json"
    try:
        source_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FinalizationError(f"invalid audit manifest: {exc}") from exc
    if source_manifest.get("status") != "pending_manual_semantic_review":
        raise FinalizationError("source audit is not pending manual semantic review")
    if source_manifest.get("merge_authorized") is not False or source_manifest.get("training_authorized") is not False:
        raise FinalizationError("source audit authorization flags are unsafe")

    decisions_path = audit / "audit_decisions.csv"
    sample_path = audit / "audit_sample.csv"
    decision_rows = _read_csv(decisions_path, DECISION_FIELDS)
    sample_rows = _read_csv(sample_path, IDENTITY_FIELDS)
    if _identities(decision_rows, "decision ledger") != _identities(sample_rows, "sample index"):
        raise FinalizationError("identity mismatch between decision ledger and sample index")
    if not decision_rows:
        raise FinalizationError("audit contains no decision rows")
    if any(row["decision"].strip() or row["note"].strip() for row in decision_rows):
        raise FinalizationError("source decision ledger is not blank; refusing bulk overwrite")

    reviewed_at = datetime.now(timezone.utc).isoformat()
    reviewed_rows = [
        {
            **{field: row[field] for field in IDENTITY_FIELDS},
            "decision": decision,
            "note": "Owner confirmed all displayed audit samples are acceptable." if decision == "keep" else "",
            "reviewed_by": reviewed_by.strip(),
            "reviewed_at": reviewed_at,
        }
        for row in decision_rows
    ]
    temp = output.with_name(output.name + ".building")
    if temp.exists():
        raise FinalizationError(f"temporary output already exists: {temp}")
    try:
        temp.mkdir(parents=True)
        reviewed_path = temp / "audit_decisions_owner_reviewed.csv"
        with reviewed_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
            writer.writeheader()
            writer.writerows(reviewed_rows)
        decision_counts = dict(sorted(Counter(row["decision"] for row in reviewed_rows).items()))
        source_counts = dict(sorted(Counter(row["source"] for row in reviewed_rows).items()))
        result: dict[str, object] = {
            "review_version": output.name,
            "created_at_utc": reviewed_at,
            "status": "owner_semantic_sample_audit_passed" if decision_counts == {"keep": len(reviewed_rows)} else "owner_semantic_sample_audit_completed_with_exceptions",
            "scope": "Sample audit outcome only; does not semantically verify every supplement image.",
            "reviewed_by": reviewed_by.strip(),
            "source_audit": str(audit),
            "source_audit_manifest_sha256": _sha256(manifest_path),
            "source_blank_decisions_sha256": _sha256(decisions_path),
            "source_sample_index_sha256": _sha256(sample_path),
            "reviewed_decisions_sha256": _sha256(reviewed_path),
            "decision_counts": decision_counts,
            "counts_by_source": source_counts,
            "merge_authorized": False,
            "training_authorized": False,
            "next_gate": "Explicit owner approval is required before any merge; training remains a separate approval.",
        }
        (temp / "approval_manifest.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (temp / "README.md").write_text(
            "# Owner-reviewed pickup audit\n\nThis folder records the project owner's semantic decisions for the 100-image audit sample. It preserves the original blank audit package and does not authorize merging or training.\n",
            encoding="utf-8",
        )
        temp.rename(output)
        return result
    except Exception:
        if temp.exists():
            shutil.rmtree(temp)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--decision", choices=sorted(ALLOWED_DECISIONS), required=True)
    parser.add_argument("--reviewed-by", required=True)
    args = parser.parse_args()
    try:
        result = finalize(args.audit.resolve(), args.output.resolve(), args.decision, args.reviewed_by)
    except (FinalizationError, OSError, csv.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
