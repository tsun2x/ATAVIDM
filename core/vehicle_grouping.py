"""Strict, evidence-backed vehicle event grouping for the violations view."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _identity_for_event(
    row: dict[str, Any],
    evidence_root: Path,
    linked_reviews: list[dict[str, Any]],
) -> tuple[int, int, int, int] | None:
    raw_dir = row.get("evidence_sequence_dir")
    if not isinstance(raw_dir, str) or not raw_dir.strip():
        return None
    project_root = Path(__file__).resolve().parents[1]
    candidate = Path(raw_dir)
    if not candidate.is_absolute():
        candidate = project_root / candidate
    try:
        base = evidence_root.resolve(strict=True)
        directory = candidate.resolve(strict=True)
        directory.relative_to(base)
        manifest_path = (directory / "event_identity.json").resolve(strict=True)
        manifest_path.relative_to(base)

        def strict_int(value: Any) -> int | None:
            if type(value) is int:
                return value if 0 <= value <= 2**63 - 1 else None
            return None

        def reject_nonfinite(token: str) -> None:
            raise ValueError(f"Invalid JSON numeric constant: {token}")

        payload = json.loads(
            manifest_path.read_text(encoding="utf-8"), parse_constant=reject_nonfinite
        )
        if not isinstance(payload, dict):
            return None
        row_identity = tuple(strict_int(row.get(key)) for key in (
            "video_id", "processing_run_id", "track_id"
        ))
        observed_identity = tuple(strict_int(payload.get(key)) for key in (
            "video_id", "processing_run_id", "track_id"
        ))
        epoch = strict_int(payload.get("track_identity_epoch"))
        version = strict_int(payload.get("version"))
        source_review_id = strict_int(payload.get("review_id"))
        if any(value is None for value in (
            *row_identity, *observed_identity, epoch, version, source_review_id
        )):
            return None
        expected = (*row_identity, epoch)
        observed = (*observed_identity, epoch)
        if version != 1 or not isinstance(payload.get("violation_type"), str) or expected != observed:
            return None

        case_id = strict_int(row.get("id"))
        final_type = row.get("violation_type")
        source = next(
            (
                linked for linked in linked_reviews
                if linked.get("case_id") == case_id
                and linked.get("source_review_id") == source_review_id
            ),
            None,
        )
        if source is None or source.get("status") != "confirmed":
            return None
        source_identity = tuple(strict_int(source.get(key)) for key in (
            "video_id", "processing_run_id", "track_id"
        ))
        if source_identity != row_identity or source.get("violation_type") != payload.get("violation_type"):
            return None

        # The persisted case and its linked source review must both retain the
        # exact sequence that produced this manifest.
        case_sequence = row.get("evidence_sequence_dir")
        source_sequence = source.get("evidence_sequence_dir")
        if not isinstance(case_sequence, str) or not isinstance(source_sequence, str):
            return None
        case_sequence_path = Path(case_sequence)
        source_sequence_path = Path(source_sequence)
        if not case_sequence_path.is_absolute():
            case_sequence_path = project_root / case_sequence_path
        if not source_sequence_path.is_absolute():
            source_sequence_path = project_root / source_sequence_path
        if (
            case_sequence_path.resolve(strict=True) != manifest_path.parent
            or source_sequence_path.resolve(strict=True) != manifest_path.parent
        ):
            return None

        if final_type != source.get("violation_type"):
            correction_is_valid = (
                source.get("decision") == "correct_canonical"
                and source.get("original_violation_type") == payload.get("violation_type")
                and source.get("selected_canonical_rule") == final_type
            )
            if not correction_is_valid and not _supported_fusion_preserves_identity(
                row=row,
                source=source,
                payload=payload,
                linked_reviews=linked_reviews,
                evidence_root=base,
                row_identity=row_identity,
                epoch=epoch,
                strict_int=strict_int,
            ):
                return None
        return expected
    except (OSError, ValueError, TypeError, KeyError, OverflowError, json.JSONDecodeError):
        return None


def _supported_fusion_preserves_identity(
    *,
    row: dict[str, Any],
    source: dict[str, Any],
    payload: dict[str, Any],
    linked_reviews: list[dict[str, Any]],
    evidence_root: Path,
    row_identity: tuple[int, int, int],
    epoch: int,
    strict_int,
) -> bool:
    """Prove the stored type change came from the existing case-fusion contract."""
    from core.case_identity import build_case_group, observation_from_review_row
    from core.violation_policy import is_parking_obstruction_fusion

    final_type = row.get("violation_type")
    source_type = payload.get("violation_type")
    policy_rules = source.get("case_policy_rules")
    if (
        not isinstance(policy_rules, list)
        or not is_parking_obstruction_fusion(policy_rules)
    ):
        return False

    source_id = strict_int(source.get("source_review_id"))
    if source_id is None:
        return False

    def reject_nonfinite(token: str) -> None:
        raise ValueError(f"Invalid JSON numeric constant: {token}")

    source_observation = observation_from_review_row({**source, "id": source_id})
    for candidate in linked_reviews:
        candidate_id = strict_int(candidate.get("source_review_id"))
        if (
            candidate_id is None
            or candidate_id == source_id
            or candidate.get("status") != "confirmed"
            or candidate.get("case_policy_rules") != policy_rules
        ):
            continue
        candidate_type = candidate.get("violation_type")
        if candidate_type == source_type or not isinstance(candidate_type, str):
            continue
        if tuple(strict_int(candidate.get(key)) for key in (
            "video_id", "processing_run_id", "track_id"
        )) != row_identity:
            continue

        # Both source reviews must carry producer manifests for the same
        # source/run/track/epoch; a matching tracker ID alone is insufficient.
        sequence_dir = candidate.get("evidence_sequence_dir")
        if not isinstance(sequence_dir, str) or not sequence_dir.strip():
            continue
        candidate_directory = Path(sequence_dir)
        if not candidate_directory.is_absolute():
            candidate_directory = Path(__file__).resolve().parents[1] / candidate_directory
        try:
            candidate_directory = candidate_directory.resolve(strict=True)
            candidate_directory.relative_to(evidence_root)
            candidate_manifest_path = (candidate_directory / "event_identity.json").resolve(strict=True)
            candidate_manifest_path.relative_to(evidence_root)
            candidate_manifest = json.loads(
                candidate_manifest_path.read_text(encoding="utf-8"),
                parse_constant=reject_nonfinite,
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        if not isinstance(candidate_manifest, dict):
            continue
        candidate_identity = tuple(strict_int(candidate_manifest.get(key)) for key in (
            "video_id", "processing_run_id", "track_id"
        ))
        if (
            strict_int(candidate_manifest.get("version")) != 1
            or strict_int(candidate_manifest.get("review_id")) != candidate_id
            or strict_int(candidate_manifest.get("track_identity_epoch")) != epoch
            or candidate_identity != row_identity
            or candidate_manifest.get("violation_type") != candidate_type
        ):
            continue

        candidate_observation = observation_from_review_row(
            {**candidate, "id": candidate_id}
        )
        fusion = build_case_group(source_observation, [candidate_observation])
        if (
            fusion.is_fused_parking_obstruction
            and fusion.primary_canonical_rule == final_type
            and set(fusion.contributing_rules) == set(policy_rules)
        ):
            return True
    return False


def group_vehicle_events(
    rows: list[dict[str, Any]],
    *,
    evidence_root: Path,
    provenance_by_case: dict[int, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Group proven same-vehicle events; preserve every unproven event alone."""
    groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    for index, row in enumerate(rows):
        try:
            case_id = int(row["id"])
        except (KeyError, TypeError, ValueError, OverflowError):
            case_id = -1
        identity = _identity_for_event(
            row, Path(evidence_root), (provenance_by_case or {}).get(case_id, [])
        )
        if identity is None:
            key = ("event", row.get("id"), index)
            verified = False
        else:
            key = ("vehicle", *identity)
            verified = True
        group = groups.setdefault(key, {
            "group_key": ":".join(str(part) for part in key),
            "vehicle_identity_verified": verified,
            "video_id": row.get("video_id"),
            "processing_run_id": row.get("processing_run_id"),
            "track_id": row.get("track_id"),
            "events": [],
        })
        group["events"].append(row)
    result = list(groups.values())
    for group in result:
        types = list(dict.fromkeys(str(event.get("violation_type") or "") for event in group["events"]))
        group["violation_types"] = types
        group["event_count"] = len(group["events"])
        group["vehicle_label"] = (
            str(group["events"][0].get("vehicle_class") or "Vehicle")
            if group["vehicle_identity_verified"] else "Identity unavailable"
        )
    return result
