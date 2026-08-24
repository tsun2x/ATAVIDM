"""Safe remove-results and permanent-delete for uploaded videos."""

from __future__ import annotations

import logging
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from core.annotated_writer import run_artifact_dir
from core.frame_extract import frame_path_for_video
from core.media_serve import MediaPathError, resolve_under_roots
from database import db

logger = logging.getLogger(__name__)


class VideoLifecycleError(Exception):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _roots() -> dict[str, list[Path]]:
    # Read from config module at call time so tests can monkeypatch paths.
    import config as _cfg

    return {
        "upload": [Path(_cfg.UPLOAD_FOLDER).resolve()],
        "frames": [Path(_cfg.FRAMES_FOLDER).resolve()],
        "evidence": [Path(_cfg.EVIDENCE_FOLDER).resolve()],
        "annotated": [Path(_cfg.ANNOTATED_FOLDER).resolve()],
        "quarantine": [Path(_cfg.ANNOTATED_FOLDER).resolve() / "_quarantine"],
    }


def _all_media_roots() -> list[Path]:
    roots = _roots()
    return roots["upload"] + roots["frames"] + roots["evidence"] + roots["annotated"]


def _quarantine_root() -> Path:
    q = _roots()["quarantine"][0]
    q.mkdir(parents=True, exist_ok=True)
    return q


def _safe_resolve(path: Path | None, roots: list[Path], *, allow_directory: bool = False) -> Path | None:
    if path is None:
        return None
    try:
        candidate = Path(path)
        allow_dir = allow_directory or (candidate.exists() and candidate.is_dir())
        return resolve_under_roots(
            path,
            roots,
            must_exist=True,
            allow_directory=allow_dir,
        )
    except MediaPathError:
        logger.warning("Refusing path outside roots: %s", path)
        return None


def _safe_unlink(path: Path | None, roots: list[Path]) -> None:
    resolved = _safe_resolve(path, roots)
    if resolved is None:
        return
    try:
        if resolved.is_file():
            resolved.unlink()
        elif resolved.is_dir():
            shutil.rmtree(resolved)
    except OSError:
        logger.exception("Failed to delete %s", resolved)


def _quarantine_path(resolved: Path, token: str) -> Path:
    """Move a resolved path into quarantine (same-volume rename preferred)."""
    qroot = _quarantine_root()
    dest = qroot / f"{token}_{resolved.name}"
    n = 0
    while dest.exists():
        n += 1
        dest = qroot / f"{token}_{n}_{resolved.name}"
    # shutil.move uses rename on same volume and copy+delete across volumes.
    shutil.move(str(resolved), str(dest))
    return dest


def _restore_quarantined(quarantined: list[tuple[Path, Path]]) -> None:
    for original, qp in reversed(quarantined):
        try:
            if qp.exists() and not original.exists():
                original.parent.mkdir(parents=True, exist_ok=True)
                qp.rename(original)
        except OSError:
            logger.exception("Failed to restore %s", original)


def _collect_evidence_paths(video_id: int) -> list[Path]:
    paths: list[Path] = []
    for item in db.list_review_items_for_video(video_id, status=None):
        for key in (
            "evidence_path",
            "vehicle_evidence_path",
            "plate_evidence_path",
            "evidence_clip_path",
            "evidence_sequence_dir",
        ):
            val = item.get(key)
            if val:
                paths.append(Path(val))
    for item in db.list_violation_rows_for_video(video_id):
        for key in (
            "evidence_path",
            "vehicle_evidence_path",
            "plate_evidence_path",
            "evidence_clip_path",
            "evidence_sequence_dir",
        ):
            val = item.get(key)
            if val:
                paths.append(Path(val))
    return paths


def _normalize_confirmation(value: str | None) -> str:
    if value is None:
        return ""
    return str(value).strip()


def require_filename_confirmation(video: dict[str, Any], expected_filename: str | None) -> None:
    """Validate nonempty exact filename confirmation. Raises VideoLifecycleError."""
    confirm = _normalize_confirmation(expected_filename)
    stored = str(video.get("filename") or "")
    if not confirm:
        raise VideoLifecycleError(
            "Filename confirmation is required for permanent deletion.",
            status_code=400,
        )
    if confirm != stored:
        raise VideoLifecycleError(
            "Filename confirmation does not match.",
            status_code=400,
        )


def has_protected_dependencies(video_id: int) -> tuple[bool, str]:
    confirmed = db.count_confirmed_violations_for_video(video_id)
    if confirmed:
        return True, (
            f"Cannot proceed: {confirmed} confirmed violation(s) depend on this video."
        )
    reports = db.count_reports_referencing_video(video_id)
    if reports:
        return True, (
            f"Cannot proceed: {reports} report(s) reference this video."
        )
    return False, ""


def remove_processing_results(
    video_id: int,
    *,
    is_busy: Callable[[int], bool],
    actor_user_id: int | None = None,
) -> dict[str, Any]:
    """Keep source + annotation; remove generated artifacts and unconfirmed results."""
    video = db.get_video(video_id)
    if video is None:
        raise VideoLifecycleError("Video not found.", status_code=404)
    if is_busy(video_id):
        raise VideoLifecycleError(
            "Cannot remove results while the video is queued or processing.",
            status_code=409,
        )
    blocked, reason = has_protected_dependencies(video_id)
    if blocked:
        raise VideoLifecycleError(reason, status_code=409)

    roots = _roots()
    evidence_roots = roots["evidence"]
    annotated_roots = roots["annotated"]

    pending = db.list_review_items_for_video(video_id, status="pending")
    for item in pending:
        for key in (
            "evidence_path",
            "vehicle_evidence_path",
            "plate_evidence_path",
            "evidence_clip_path",
            "evidence_sequence_dir",
        ):
            val = item.get(key)
            if val:
                _safe_unlink(Path(val), evidence_roots)

    db.delete_unconfirmed_results_for_video(video_id)

    for run in db.list_processing_runs(video_id):
        _safe_unlink(run_artifact_dir(run["id"]), annotated_roots)
        db.clear_processing_run_artifact(run["id"])

    import config as _cfg
    _safe_unlink(Path(_cfg.EVIDENCE_FOLDER) / f"video_{video_id}", evidence_roots)

    db.update_video(video_id, processed=0, status="ready")
    db.insert_video_history_event(
        video_id,
        "results_removed",
        detail={"message": "Processing results removed; source and annotation retained."},
        actor_user_id=actor_user_id,
    )
    return {"success": True, "video_id": video_id, "status": "ready"}


def delete_video_permanently(
    video_id: int,
    *,
    is_busy: Callable[[int], bool],
    actor_user_id: int | None = None,
    expected_filename: str | None = None,
) -> dict[str, Any]:
    """Permanently delete a video and eligible dependents. Admin-gated by caller.

    Failure-safe sequence:
    1. Validate confirmation and preconditions (no FS/DB mutations yet).
    2. Quarantine (rename) all referenced files under approved media roots.
    3. Cascade-delete DB rows + insert durable audit in one transaction.
    4. Purge quarantine on success; restore quarantined paths if DB/audit fails.
    5. Purge failure after commit is a cleanup warning (deletion remains committed).
    """
    video = db.get_video(video_id)
    if video is None:
        raise VideoLifecycleError("Video not found.", status_code=404)
    if is_busy(video_id):
        raise VideoLifecycleError(
            "Cannot delete while the video is queued or processing.",
            status_code=409,
        )
    require_filename_confirmation(video, expected_filename)
    blocked, reason = has_protected_dependencies(video_id)
    if blocked:
        raise VideoLifecycleError(reason, status_code=409)

    roots = _roots()
    upload_roots = roots["upload"]
    frame_roots = roots["frames"]
    evidence_roots = roots["evidence"]
    annotated_roots = roots["annotated"]
    media_roots = _all_media_roots()

    filepath = video.get("filepath")
    frame = frame_path_for_video(video_id)
    annotation = db.get_annotation_by_video(video_id)
    ref_frame = annotation.get("reference_frame_path") if annotation else None

    # Collect every path we will remove (must stay under approved roots).
    candidates: list[tuple[Path, list[Path]]] = []
    for run in db.list_processing_runs(video_id):
        candidates.append((run_artifact_dir(run["id"]), annotated_roots))
    for p in _collect_evidence_paths(video_id):
        candidates.append((p, evidence_roots))
    import config as _cfg
    candidates.append((Path(_cfg.EVIDENCE_FOLDER) / f"video_{video_id}", evidence_roots))
    if filepath:
        candidates.append((Path(filepath), upload_roots))
    candidates.append((Path(frame), frame_roots))
    if ref_frame:
        candidates.append((Path(ref_frame), frame_roots + upload_roots))

    token = f"v{video_id}_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    quarantined: list[tuple[Path, Path]] = []  # (original, quarantine)

    try:
        for path, allowed in candidates:
            resolved = _safe_resolve(path, allowed, allow_directory=True)
            if resolved is None:
                continue
            # Cross-check against the union of media roots.
            try:
                resolve_under_roots(resolved, media_roots, must_exist=True, allow_directory=True)
            except MediaPathError:
                raise VideoLifecycleError(
                    "Refusing to delete a path outside approved media roots.",
                    status_code=400,
                )
            try:
                qpath = _quarantine_path(resolved, token)
            except OSError as exc:
                # Restore anything already moved, then abort — DB untouched.
                _restore_quarantined(quarantined)
                raise VideoLifecycleError(
                    f"Filesystem quarantine failed: {exc}",
                    status_code=500,
                ) from exc
            quarantined.append((resolved, qpath))

        try:
            db.delete_video_cascade_with_audit(
                video_id,
                event_type="video_deleted",
                detail={
                    "video_id": video_id,
                    "filename": video.get("filename"),
                    "actor_user_id": actor_user_id,
                },
            )
        except Exception as exc:
            # Restore every quarantined file so DB + FS stay consistent.
            _restore_quarantined(quarantined)
            raise VideoLifecycleError(
                f"Database deletion failed; files restored: {exc}",
                status_code=500,
            ) from exc

        # DB committed — purge quarantine. Failure here is cleanup-only.
        cleanup_warnings: list[str] = []
        for _original, qp in quarantined:
            try:
                if qp.is_file():
                    qp.unlink()
                elif qp.is_dir():
                    shutil.rmtree(qp)
            except OSError as exc:
                logger.exception("Failed to purge quarantine artifact %s", qp)
                cleanup_warnings.append(f"{qp.name}: {exc}")

    except VideoLifecycleError:
        raise
    except OSError as exc:
        _restore_quarantined(quarantined)
        raise VideoLifecycleError(
            f"Filesystem operation failed: {exc}",
            status_code=500,
        ) from exc

    result: dict[str, Any] = {
        "success": True,
        "deleted_video_id": video_id,
        "filename": video.get("filename"),
        "database_committed": True,
    }
    if cleanup_warnings:
        result["cleanup_warning"] = (
            "Database deletion committed, but quarantine purge failed for some "
            "artifacts. Stranded files remain under annotated/_quarantine/ "
            f"with token prefix {token}."
        )
        result["cleanup_failures"] = cleanup_warnings
        result["quarantine_token"] = token
    return result
