"""Send an officer-selected pickup lead to the ordinary pending review queue."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import config
from core.detection_config import VIOLATION_CARGO_PASSENGERS, YOLO_CLASS_PICKUP_TRUCK
from core.media_serve import MediaPathError, resolve_under_roots

PICKUP_HANDOFF_MARKER = "[pickup_detail_handoff]"
SENDABLE_OUTCOMES = frozenset({"pending", "reviewed"})


class PickupHandoffConflict(ValueError):
    """The lead changed or does not have reviewable source evidence."""


class PickupHandoffNotFound(LookupError):
    """The pickup lead is missing or outside the reviewer's scope."""


def _evidence_file(stored: Any, root: Path, *, relative_to: Path) -> Path | None:
    if not isinstance(stored, str) or not stored:
        return None
    try:
        path = resolve_under_roots(relative_to / stored, [root])
        return path if path.stat().st_size > 0 else None
    except (MediaPathError, OSError, RuntimeError):
        return None


def _snapshot(lead: dict[str, Any], notes: str | None) -> tuple[dict[str, Any], Path, Path]:
    try:
        frames = json.loads(lead.get("frames_json") or "[]")
        association = json.loads(lead.get("association_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        frames, association = [], {}
    frame = next((
        item for item in frames
        if isinstance(item, dict) and item.get("frame_number") == lead["frame_number"]
    ), {}) if isinstance(frames, list) else {}
    base = Path(config.BASE_DIR)
    root = base / config.EVIDENCE_FOLDER / "pickup_detail"
    scene = _evidence_file(frame.get("scene_path"), root, relative_to=base)
    crop = _evidence_file(frame.get("crop_path"), root, relative_to=base)
    if not scene or not crop or not isinstance(association, dict) or not association.get("evidence_source"):
        raise PickupHandoffConflict("This lead needs a source scene, context crop, and association evidence.")
    provenance = {key: lead[key] for key in (
        "video_id", "processing_run_id", "run_key", "occurrence_key", "selector_version",
        "pickup_track_id", "person_track_id", "pickup_identity_epoch", "person_identity_epoch",
        "frame_number", "timestamp_sec", "episode_start_sec", "source_width", "source_height",
    )}
    provenance.update({
        "candidate_id": lead["id"],
        "reviewer_notes": notes,
        "association": association,
        "frame": {key: frame[key] for key in ("index", "crop", "pickup_bbox", "person_bbox") if key in frame},
    })
    return provenance, scene, crop


def send_pickup_to_review(
    adapter: Any, candidate_id: int, reviewer_id: int, *,
    expected_outcome: str, expected_processing_run_id: int, notes: str | None,
) -> dict[str, Any]:
    """Atomically record one handoff per uploaded-video pickup occurrence."""
    with adapter.review_write_transaction():
        reviewer = adapter.get_user(reviewer_id)
        if not reviewer or not reviewer["is_active"] or reviewer["role"] not in ("admin", "enforcer"):
            raise PermissionError("Your account cannot send pickup evidence to Violation Review.")
        lead = adapter.get_pickup_detail_candidate(candidate_id)
        if lead is None:
            raise PickupHandoffNotFound("Pickup candidate not found.")
        if lead["video_id"] is None:
            raise PickupHandoffConflict("This lead needs an uploaded source video before it can be sent.")
        video = adapter.get_video(lead["video_id"])
        if not video or (reviewer["role"] != "admin" and video["uploaded_by"] != reviewer_id):
            raise PickupHandoffNotFound("Pickup candidate not found.")

        handoff = adapter.list_pickup_detail_handoffs([candidate_id]).get(candidate_id)
        if handoff:
            return {**handoff, "outcome": lead["human_outcome"], "idempotent": True}
        if lead["human_outcome"] not in SENDABLE_OUTCOMES:
            raise PickupHandoffConflict("Only pending or reviewed pickup leads can be sent.")
        if lead["human_outcome"] != expected_outcome or lead["processing_run_id"] != expected_processing_run_id:
            raise PickupHandoffConflict("This lead changed. Reload it and check the evidence before sending.")

        run = adapter.get_processing_run(expected_processing_run_id)
        if not run or run["video_id"] != video["id"] or run["status"] != "completed" or run.get("results_removed_at"):
            raise PickupHandoffConflict("This lead needs evidence from a completed processing run.")
        if not _evidence_file(video.get("filepath"), Path(config.UPLOAD_FOLDER), relative_to=Path.cwd()):
            raise PickupHandoffConflict("The uploaded source video is unavailable.")

        reviewer_notes = (notes.strip() or None) if notes is not None else lead["reviewer_notes"]
        provenance, scene, crop = _snapshot(lead, reviewer_notes)
        review_id = adapter.insert_review_queue(
            video_id=video["id"],
            processing_run_id=run["id"],
            track_id=lead["pickup_track_id"],
            violation_type=VIOLATION_CARGO_PASSENGERS,
            vehicle_class=YOLO_CLASS_PICKUP_TRUCK,
            # The legacy case column is NOT NULL. The rule score stays absent.
            confidence=0.0,
            frame_number=lead["frame_number"],
            timestamp_sec=lead["timestamp_sec"],
            episode_start_sec=lead["episode_start_sec"],
            episode_end_sec=lead["timestamp_sec"],
            evidence_path=str(scene),
            vehicle_evidence_path=str(crop),
            reason_log=f"{PICKUP_HANDOFF_MARKER} Evidence sent from Pickup Review for a violation decision.",
            contributing_factors_json=json.dumps({"pickup_review": provenance}),
        )
        if not adapter.record_pickup_detail_review(
            candidate_id=candidate_id,
            outcome="reviewed",
            expected_outcome=expected_outcome,
            reviewed_by=reviewer_id,
            notes=reviewer_notes,
        ):
            raise PickupHandoffConflict("This lead changed. Reload it before sending.")
        adapter.insert_video_history_event(
            video["id"],
            "pickup_detail_sent_to_review",
            run_id=run["id"],
            actor_user_id=reviewer_id,
            detail={"candidate_id": candidate_id, "occurrence_key": lead["occurrence_key"], "review_id": review_id},
        )
        return {"review_id": review_id, "review_status": "pending", "outcome": "reviewed", "idempotent": False}
