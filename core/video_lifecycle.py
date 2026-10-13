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


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _safe_unlink_preserving(path: Path | None, roots: list[Path], protected: set[Path]) -> None:
    """Remove a tree without deleting a referenced path inside or around it."""
    resolved = _safe_resolve(path, roots, allow_directory=True)
    if resolved is None:
        return
    if any(_paths_overlap(resolved, kept) for kept in protected):
        if resolved.is_dir() and not any(kept == resolved or kept in resolved.parents for kept in protected):
            try:
                for child in resolved.iterdir():
                    _safe_unlink_preserving(child, roots, protected)
                if not any(kept == resolved or resolved in kept.parents for kept in protected):
                    resolved.rmdir()
            except OSError:
                logger.exception("Failed to selectively delete %s", resolved)
        return
    _safe_unlink(resolved, roots)


def _external_evidence_paths(video_id: int, roots: list[Path]) -> set[Path]:
    """Take one bounded reference snapshot for a lifecycle operation."""
    from database import sqlite_adapter

    columns = (
        "evidence_path", "vehicle_evidence_path", "plate_evidence_path",
        "evidence_clip_path", "evidence_sequence_dir",
    )
    paths: set[Path] = set()
    with sqlite_adapter.db_session() as conn:
        for table in ("review_queue", "violations"):
            rows = conn.execute(
                f"SELECT {', '.join(columns)} FROM {table} WHERE video_id != ?",
                (int(video_id),),
            ).fetchall()
            for row in rows:
                for column, value in zip(columns, row):
                    if value:
                        candidate = _safe_resolve(Path(value), roots, allow_directory=True)
                        if candidate is not None:
                            paths.add(candidate)
                        if column == "evidence_path":
                            raw = _raw_scene_sidecar(Path(value), roots)
                            if raw is not None:
                                paths.add(raw)
    return paths


def _evidence_referenced_elsewhere(
    path: Path, video_id: int, shared_paths: set[Path] | None = None
) -> bool:
    """Keep a media path if another video's DB record still points to it."""
    resolved_path = path.resolve()
    references = shared_paths if shared_paths is not None else _external_evidence_paths(
        video_id, _roots()["evidence"]
    )
    return any(_paths_overlap(resolved_path, candidate) for candidate in references)


def _retained_evidence_paths(video_id: int, roots: list[Path]) -> set[Path]:
    """Paths still referenced by confirmed records that lifecycle edits retain."""
    retained: set[Path] = set()
    for item in db.list_review_items_for_video(video_id, status=None):
        if item.get("status") != "confirmed":
            continue
        retained.update(_stored_media_references(item, roots))
    for item in db.list_violation_rows_for_video(video_id):
        if item.get("status") != "confirmed":
            continue
        retained.update(_stored_media_references(item, roots))
    return retained


def _raw_scene_sidecar(path: Path, roots: list[Path]) -> Path | None:
    annotated = _safe_resolve(path, roots)
    if annotated is not None:
        return _safe_resolve(annotated.with_name("raw_" + annotated.name), roots)
    # Legacy DB rows can outlive the annotated sidecar while the raw sibling
    # remains. Resolve the derived name under the configured root before use.
    candidate = Path(path)
    return _safe_resolve(candidate.with_name("raw_" + candidate.name), roots)


def _stored_media_references(item: dict[str, Any], roots: list[Path]) -> set[Path]:
    """Expand a retained row's stored media paths to every owned scene file."""
    references: set[Path] = set()
    for key in (
        "evidence_path", "vehicle_evidence_path", "plate_evidence_path",
        "evidence_clip_path", "evidence_sequence_dir",
    ):
        value = item.get(key)
        if not value:
            continue
        resolved = _safe_resolve(Path(value), roots, allow_directory=True)
        if resolved is not None:
            references.add(resolved)
        if key == "evidence_path":
            raw = _raw_scene_sidecar(Path(value), roots)
            if raw is not None:
                references.add(raw)
    return references


def _remove_evidence_reference(
    path: Path | None,
    roots: list[Path],
    *,
    video_id: int,
    retained: set[Path] | None = None,
    paired_raw: bool = False,
    shared_paths: set[Path] | None = None,
) -> None:
    resolved = _safe_resolve(path, roots)
    if resolved is None:
        if paired_raw:
            raw = _raw_scene_sidecar(Path(path), roots)
            protected = retained or set()
            if raw is not None and not any(_paths_overlap(raw, item) for item in protected) and not _evidence_referenced_elsewhere(
                raw, video_id, shared_paths
            ):
                _safe_unlink(raw, roots)
        return
    protected = retained or set()
    if any(_paths_overlap(resolved, item) for item in protected) or _evidence_referenced_elsewhere(
        resolved, video_id, shared_paths
    ):
        return
    raw = _raw_scene_sidecar(resolved, roots) if paired_raw else None
    _safe_unlink(resolved, roots)
    if raw is not None and not any(_paths_overlap(raw, item) for item in protected) and not _evidence_referenced_elsewhere(
        raw, video_id, shared_paths
    ):
        _safe_unlink(raw, roots)


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


def _restore_quarantined(quarantined: list[tuple[Path, Path]]) -> list[str]:
    failures: list[str] = []
    for original, qp in reversed(quarantined):
        try:
            if not qp.exists():
                if not original.exists():
                    failures.append(f"{original} is missing from both original and quarantine paths")
                continue
            if original.exists():
                failures.append(f"{original} already exists; quarantined copy remains at {qp}")
                continue
            original.parent.mkdir(parents=True, exist_ok=True)
            # Use the same cross-volume-capable primitive used for quarantine.
            shutil.move(str(qp), str(original))
        except OSError as exc:
            logger.exception("Failed to restore %s from %s", original, qp)
            failures.append(f"{original}: {exc}; recovery copy is at {qp}")
    return failures


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
                path = Path(val)
                paths.append(path)
                if key == "evidence_path":
                    raw = _raw_scene_sidecar(path, _roots()["evidence"])
                    if raw is not None:
                        paths.append(raw)
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
                path = Path(val)
                paths.append(path)
                if key == "evidence_path":
                    raw = _raw_scene_sidecar(path, _roots()["evidence"])
                    if raw is not None:
                        paths.append(raw)
    return paths


def _normalize_confirmation(value: str | None) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _detail_evidence_dirs(video_id: int) -> list[Path]:
    """Run-scoped motorcycle detail-evidence directories for a video.

    Includes directories derived from stored candidate rows and from this
    video's processing runs, so a partially written run is covered too.
    """
    import config as _cfg

    root = Path(_cfg.EVIDENCE_FOLDER) / "detail"
    keys: set[str] = set()
    try:
        keys.update(db.list_motorcycle_detail_run_keys_for_video(video_id))
    except Exception:
        logger.exception("Failed to list detail run keys for video %s", video_id)
    for run in db.list_processing_runs(video_id):
        keys.add(f"run_{run['id']}")
    return [root / key for key in sorted(keys)]


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

    removable_reviews = [
        item
        for item in db.list_review_items_for_video(video_id, status=None)
        if item.get("status") in {"pending", "dismissed"}
    ]
    retained = _retained_evidence_paths(video_id, evidence_roots)
    shared_paths = _external_evidence_paths(video_id, evidence_roots)
    for item in removable_reviews:
        for key in (
            "evidence_path",
            "vehicle_evidence_path",
            "plate_evidence_path",
            "evidence_clip_path",
            "evidence_sequence_dir",
        ):
            val = item.get(key)
            if val:
                _remove_evidence_reference(
                    Path(val), evidence_roots, video_id=video_id,
                    retained=retained, paired_raw=key == "evidence_path",
                    shared_paths=shared_paths,
                )

    detail_dirs = _detail_evidence_dirs(video_id)
    db.delete_unconfirmed_results_for_video(video_id)
    protected_evidence = retained | shared_paths
    for detail_dir in detail_dirs:
        _safe_unlink_preserving(detail_dir, evidence_roots, protected_evidence)

    for run in db.list_processing_runs(video_id):
        _safe_unlink(run_artifact_dir(run["id"]), annotated_roots)
        db.clear_processing_run_artifact(run["id"])

    import config as _cfg
    _safe_unlink_preserving(
        Path(_cfg.EVIDENCE_FOLDER) / f"video_{video_id}", evidence_roots, protected_evidence
    )

    db.update_video(video_id, processed=0, status="ready")
    db.insert_video_history_event(
        video_id,
        "results_removed",
        detail={"message": "Processing results removed; source and annotation retained."},
        actor_user_id=actor_user_id,
    )
    return {"success": True, "video_id": video_id, "status": "ready"}


def invalidate_processing_results_for_annotation_change(
    video_id: int,
    *,
    is_busy: Callable[[int], bool],
    actor_user_id: int | None = None,
) -> dict[str, Any]:
    """Retire stale generated results after scene geometry changes.

    Confirmed violations are retained as historical, reviewed records. Pending
    and dismissed machine candidates, detections, and annotated run artifacts
    are no longer valid for the changed annotation and are removed.
    """
    video = db.get_video(video_id)
    if video is None:
        raise VideoLifecycleError("Video not found.", status_code=404)
    if is_busy(video_id):
        raise VideoLifecycleError(
            "Cannot change annotation while the video is queued or processing.",
            status_code=409,
        )

    evidence_roots = _roots()["evidence"]
    annotated_roots = _roots()["annotated"]
    retained = _retained_evidence_paths(video_id, evidence_roots)
    shared_paths = _external_evidence_paths(video_id, evidence_roots)
    for item in db.list_review_items_for_video(video_id, status=None):
        if item.get("status") == "confirmed":
            continue
        for key in (
            "evidence_path",
            "vehicle_evidence_path",
            "plate_evidence_path",
            "evidence_clip_path",
            "evidence_sequence_dir",
        ):
            value = item.get(key)
            if value:
                _remove_evidence_reference(
                    Path(value), evidence_roots, video_id=video_id,
                    retained=retained, paired_raw=key == "evidence_path",
                    shared_paths=shared_paths,
                )

    detail_dirs = _detail_evidence_dirs(video_id)
    db.delete_unconfirmed_results_for_video(video_id)
    for run in db.list_processing_runs(video_id):
        _safe_unlink(run_artifact_dir(run["id"]), annotated_roots)
        db.clear_processing_run_artifact(run["id"])
    protected_evidence = retained | shared_paths
    for detail_dir in detail_dirs:
        _safe_unlink_preserving(detail_dir, evidence_roots, protected_evidence)

    db.update_video(video_id, processed=0, status="ready")
    db.insert_video_history_event(
        video_id,
        "results_invalidated_by_annotation_change",
        detail={
            "message": "Prior machine results retired because the scene annotation changed.",
            "confirmed_violations_preserved": db.count_confirmed_violations_for_video(video_id),
        },
        actor_user_id=actor_user_id,
    )
    return {"success": True, "video_id": video_id, "status": "ready"}


def delete_video_permanently(
    video_id: int,
    *,
    is_busy: Callable[[int], bool],
    actor_user_id: int | None = None,
    expected_filename: str | None = None,
    allow_confirmed_dependents: bool = False,
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
    confirmed = db.count_confirmed_violations_for_video(video_id)
    if confirmed and not allow_confirmed_dependents:
        raise VideoLifecycleError(
            f"Cannot proceed: {confirmed} confirmed violation(s) depend on this video. "
            "Explicit confirmation to delete the dependent violations is required.",
            status_code=409,
        )
    reports = db.count_reports_referencing_video(video_id)
    if reports:
        raise VideoLifecycleError(
            f"Cannot proceed: {reports} report(s) reference this video.",
            status_code=409,
        )

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
    for detail_dir in _detail_evidence_dirs(video_id):
        candidates.append((detail_dir, evidence_roots))
    import config as _cfg
    candidates.append((Path(_cfg.EVIDENCE_FOLDER) / f"video_{video_id}", evidence_roots))
    if filepath:
        candidates.append((Path(filepath), upload_roots))
    candidates.append((Path(frame), frame_roots))
    if ref_frame:
        candidates.append((Path(ref_frame), frame_roots + upload_roots))

    token = f"v{video_id}_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    quarantined: list[tuple[Path, Path]] = []  # (original, quarantine)

    # Resolve and validate every deletion target and preflight ownership before
    # the first move. Keep the per-target ownership recheck below to avoid
    # deleting a path that another video's record references during preflight.
    prepared: list[tuple[Path, list[Path], bool]] = []
    prepared_paths: list[Path] = []
    shared_evidence_snapshot = _external_evidence_paths(video_id, evidence_roots)
    try:
        for path, allowed in candidates:
            resolved = _safe_resolve(path, allowed, allow_directory=True)
            if resolved is None:
                continue
            if any(parent == resolved or parent in resolved.parents for parent in prepared_paths):
                continue
            try:
                resolve_under_roots(resolved, media_roots, must_exist=True, allow_directory=True)
            except MediaPathError as exc:
                raise VideoLifecycleError(
                    "Refusing to delete a path outside approved media roots.", status_code=400
                ) from exc
            is_evidence = any(
                resolved == root or root in resolved.parents for root in evidence_roots
            )
            if is_evidence and any(
                _paths_overlap(resolved, reference) for reference in shared_evidence_snapshot
            ):
                continue
            prepared.append((resolved, allowed, is_evidence))
            prepared_paths.append(resolved)
    except VideoLifecycleError:
        raise
    except Exception as exc:
        raise VideoLifecycleError(
            f"Could not verify deletion ownership; no files were moved: {exc}",
            status_code=500,
        ) from exc

    try:
        for resolved, _allowed, is_evidence in prepared:
            # A previously moved parent directory owns any nested candidate.
            if not resolved.exists():
                continue
            if is_evidence and _evidence_referenced_elsewhere(resolved, video_id):
                continue
            qpath = _quarantine_path(resolved, token)
            quarantined.append((resolved, qpath))
        db.delete_video_cascade_with_audit(
            video_id,
            event_type="video_deleted",
            detail={
                "video_id": video_id,
                "filename": video.get("filename"),
                "actor_user_id": actor_user_id,
                "confirmed_violations_deleted": confirmed,
                "confirmed_dependents_override": bool(
                    confirmed and allow_confirmed_dependents
                ),
            },
        )
    except Exception as exc:
        # Any failure before the cascade call returns is pre-commit from this
        # service's contract. Restore moved files and report incomplete recovery.
        restore_failures = _restore_quarantined(quarantined)
        if restore_failures:
            logger.error(
                "Video %s deletion did not commit; restore failures: %s",
                video_id,
                "; ".join(restore_failures),
            )
            recovery = (
                f"{len(restore_failures)} file(s) could not be restored; recovery "
                f"token {token} remains under annotated/_quarantine/"
            )
        else:
            recovery = "all quarantined files were restored"
        status_code = exc.status_code if isinstance(exc, VideoLifecycleError) else 500
        raise VideoLifecycleError(
            f"Video deletion was not committed; {recovery}. {exc}",
            status_code=status_code,
        ) from exc

    # The cascade/audit transaction returned successfully: later purge failures
    # are cleanup warnings and must never be described as a rollback.
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

    result: dict[str, Any] = {
        "success": True,
        "deleted_video_id": video_id,
        "filename": video.get("filename"),
        "database_committed": True,
        "deleted_confirmed_violations": confirmed,
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

