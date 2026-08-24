"""Incremental annotated video writer with temp → finalize semantics.

Produces browser-playable output using codecs already available via OpenCV's
bundled FFmpeg build. Prefer VP8/VP9 WebM (Chrome/Edge native). Never publish
AVI relabeled as MP4. Never publish partial/failed output.

Dependency decision: do NOT install FFmpeg CLI, OpenH264 DLL, or new Python
packages. Use OpenCV-bundled encoders only; WebM/VP8 is the primary path.
"""

from __future__ import annotations

import logging
import mimetypes
import os
from pathlib import Path
from typing import Any

import cv2

import config

logger = logging.getLogger(__name__)


class AnnotatedWriterError(RuntimeError):
    """Raised when the annotated output cannot be created or finalized."""


# Codec preference: browser-native WebM first. H.264 only if WebM unavailable
# AND the writer actually opens (OpenH264 may be missing on Windows).
_CODEC_CANDIDATES: tuple[tuple[str, str, str, str], ...] = (
    # fourcc, temp name, final name, mime
    ("VP80", "annotated.partial.webm", "annotated.webm", "video/webm"),
    ("VP90", "annotated.partial.webm", "annotated.webm", "video/webm"),
    ("avc1", "annotated.partial.mp4", "annotated.mp4", "video/mp4"),
)


def run_artifact_dir(run_id: int | str) -> Path:
    return Path(config.ANNOTATED_FOLDER) / f"run_{run_id}"


def annotated_final_path(run_id: int | str) -> Path:
    """Best-effort final path; prefer existing artifact if already published."""
    out_dir = run_artifact_dir(run_id)
    for name in ("annotated.webm", "annotated.mp4"):
        candidate = out_dir / name
        if candidate.is_file():
            return candidate
    return out_dir / "annotated.webm"


def annotated_temp_path(run_id: int | str) -> Path:
    return run_artifact_dir(run_id) / "annotated.partial.webm"


def mime_for_annotated_path(path: str | Path) -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".webm":
        return "video/webm"
    if suffix == ".mp4":
        return "video/mp4"
    guessed = mimetypes.guess_type(str(path))[0]
    return guessed or "application/octet-stream"


def download_name_for_annotated(
    source_filename: str | None,
    run_id: int | str,
    path: str | Path,
) -> str:
    stem = Path(source_filename or "video").stem
    ext = Path(path).suffix.lower() or ".webm"
    return f"{stem}_annotated_run{run_id}{ext}"


def _looks_like_webm(path: Path) -> bool:
    try:
        return path.read_bytes()[:4] == b"\x1a\x45\xdf\xa3"
    except OSError:
        return False


def _looks_like_mp4(path: Path) -> bool:
    try:
        header = path.read_bytes()[:64]
    except OSError:
        return False
    return b"ftyp" in header


class AnnotatedVideoWriter:
    """Write annotated frames incrementally; publish only after successful finalize."""

    def __init__(
        self,
        run_id: int | str,
        *,
        width: int,
        height: int,
        fps: float,
    ) -> None:
        if width <= 0 or height <= 0:
            raise AnnotatedWriterError(f"Invalid frame size {width}x{height}")
        self.run_id = run_id
        self.width = int(width)
        self.height = int(height)
        self.fps = float(fps) if fps and fps > 0 else 1.0
        self.frames_written = 0
        self._finalized = False
        self._aborted = False
        self._writer: cv2.VideoWriter | None = None
        self.codec: str | None = None
        self.mime_type: str = "video/webm"
        self.container: str = "webm"

        out_dir = run_artifact_dir(run_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        self.temp_path = annotated_temp_path(run_id)
        self.final_path = out_dir / "annotated.webm"

        for stale in out_dir.glob("annotated.partial.*"):
            try:
                stale.unlink()
            except OSError:
                pass

        last_error = None
        for codec, temp_name, final_name, mime in _CODEC_CANDIDATES:
            candidate = out_dir / temp_name
            if candidate.exists():
                try:
                    candidate.unlink()
                except OSError:
                    pass
            fourcc = cv2.VideoWriter_fourcc(*codec)
            writer = cv2.VideoWriter(
                str(candidate),
                fourcc,
                self.fps,
                (self.width, self.height),
            )
            if writer.isOpened():
                self.temp_path = candidate
                self.final_path = out_dir / final_name
                self._writer = writer
                self.codec = codec
                self.mime_type = mime
                self.container = Path(final_name).suffix.lstrip(".")
                break
            writer.release()
            last_error = codec
            try:
                if candidate.exists():
                    candidate.unlink()
            except OSError:
                pass

        if self._writer is None:
            raise AnnotatedWriterError(
                f"Failed to open browser-compatible VideoWriter for run {run_id} "
                f"({self.width}x{self.height} @ {self.fps:.3f} fps; "
                f"tried {[c[0] for c in _CODEC_CANDIDATES]}). "
                "Install OpenH264 only with owner approval if H.264 MP4 is required."
                + (f" last={last_error}" if last_error else "")
            )
        logger.info(
            "Annotated writer opened run=%s codec=%s container=%s mime=%s",
            run_id,
            self.codec,
            self.container,
            self.mime_type,
        )

    def write(self, frame: Any) -> None:
        if self._writer is None or self._aborted or self._finalized:
            raise AnnotatedWriterError("Writer is not open")
        if frame is None:
            raise AnnotatedWriterError("Cannot write empty frame")
        h, w = frame.shape[:2]
        if w != self.width or h != self.height:
            frame = cv2.resize(frame, (self.width, self.height))
        self._writer.write(frame)
        self.frames_written += 1

    def finalize(self) -> Path:
        """Close the writer and atomically publish the final annotated file."""
        if self._finalized:
            return self.final_path
        if self._aborted:
            raise AnnotatedWriterError("Cannot finalize an aborted writer")
        if self._writer is None:
            raise AnnotatedWriterError("Writer never opened")
        if self.frames_written <= 0:
            self.abort()
            raise AnnotatedWriterError("No frames written; annotated artifact not published")

        self._writer.release()
        self._writer = None

        if not self.temp_path.is_file() or self.temp_path.stat().st_size <= 0:
            self.abort()
            raise AnnotatedWriterError("Annotated temp output is empty")

        suffix = self.temp_path.suffix.lower()
        if suffix == ".webm" and not _looks_like_webm(self.temp_path):
            self.abort()
            raise AnnotatedWriterError("WebM header mismatch; refusing to publish")
        if suffix == ".mp4" and not _looks_like_mp4(self.temp_path):
            self.abort()
            raise AnnotatedWriterError("MP4 header mismatch; refusing to publish")
        if suffix in (".avi", ".mkv"):
            self.abort()
            raise AnnotatedWriterError(
                f"{suffix} annotated output is not accepted; container must match MIME"
            )

        probe = cv2.VideoCapture(str(self.temp_path))
        try:
            if not probe.isOpened():
                raise AnnotatedWriterError("Annotated output cannot be reopened")
            ok, _ = probe.read()
            if not ok:
                raise AnnotatedWriterError("Annotated output has no readable frames")
            probe_fps = float(probe.get(cv2.CAP_PROP_FPS) or 0.0)
        finally:
            probe.release()

        if self.final_path.exists():
            try:
                self.final_path.unlink()
            except OSError as exc:
                raise AnnotatedWriterError(f"Cannot replace existing artifact: {exc}") from exc
        os.replace(str(self.temp_path), str(self.final_path))
        self._finalized = True
        logger.info(
            "Annotated finalized run=%s path=%s frames=%s fps=%.3f probe_fps=%.3f",
            self.run_id,
            self.final_path,
            self.frames_written,
            self.fps,
            probe_fps,
        )
        return self.final_path

    def abort(self) -> None:
        """Release resources and remove unfinished artifacts for this run only."""
        self._aborted = True
        if self._writer is not None:
            try:
                self._writer.release()
            except Exception:
                logger.exception("Error releasing annotated VideoWriter for run %s", self.run_id)
            self._writer = None
        try:
            if self.temp_path.exists():
                self.temp_path.unlink()
        except OSError:
            logger.exception("Failed to remove partial annotated file %s", self.temp_path)

    def close(self) -> None:
        if self._finalized:
            return
        if not self._aborted:
            self.abort()
