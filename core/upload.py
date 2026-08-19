"""Video upload validation and file handling."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

import config

# Headroom reserved on top of the file size so the upload + extraction have room
# to complete without filling the disk. Documented in README / .env.example.
# Read at call time (not import time) so it can be tuned per environment/test.
def _disk_headroom_bytes() -> int:
    return int(os.environ.get("UPLOAD_DISK_HEADROOM_MB", "500")) * 1024 * 1024


class UploadError(Exception):
    """Raised when an upload fails validation or saving."""


def allowed_file(filename: str) -> bool:
    if not filename or "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in config.ALLOWED_VIDEO_EXTENSIONS


def validate_upload(file: FileStorage, content_length: int | None) -> None:
    if file is None or not file.filename:
        raise UploadError("No file selected.")

    if not allowed_file(file.filename):
        raise UploadError("Only MP4 video files are allowed.")

    if content_length is not None and content_length > config.MAX_CONTENT_LENGTH:
        max_mb = config.MAX_UPLOAD_MB
        raise UploadError(f"File exceeds maximum upload size of {max_mb} MB.")


def check_disk_space(required_bytes: int, path: str | None = None) -> None:
    """Raise UploadError if there isn't enough free space on the target volume.

    Checks ``UPLOAD_FOLDER`` (or an explicit ``path``); the caller should pass
    the expected file size plus headroom so the upload has room to complete.
    """
    target = Path(path) if path else Path(config.UPLOAD_FOLDER)
    try:
        target.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(target)
    except OSError as exc:  # pragma: no cover - filesystem edge cases
        raise UploadError(f"Could not check disk space: {exc}")

    needed = required_bytes + _disk_headroom_bytes()
    if usage.free < needed:
        free_mb = usage.free / (1024 * 1024)
        raise UploadError(
            f"Not enough free disk space. Need about {format_file_size(needed)} "
            f"(including {format_file_size(_disk_headroom_bytes())} headroom), "
            f"but only {free_mb:.0f} MB is available."
        )


def ensure_upload_dir() -> Path:
    upload_dir = Path(config.UPLOAD_FOLDER)
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir


def save_video_file(file: FileStorage) -> tuple[str, str]:
    """Save uploaded file; returns (secure_filename, absolute_filepath)."""
    upload_dir = ensure_upload_dir()
    original = secure_filename(file.filename)
    if not original:
        raise UploadError("Invalid filename.")

    # Guard disk space before writing. The browser may not report a size, so
    # fall back to the configured maximum when content_length is missing.
    expected_size = file.content_length or config.MAX_CONTENT_LENGTH
    check_disk_space(expected_size, path=str(upload_dir))

    stem = Path(original).stem
    suffix = Path(original).suffix.lower()
    candidate = original
    dest = upload_dir / candidate
    counter = 1
    while dest.exists():
        candidate = f"{stem}_{int(time.time())}_{counter}{suffix}"
        dest = upload_dir / candidate
        counter += 1

    file.save(dest)
    return candidate, str(dest.resolve())


def format_file_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes / (1024 * 1024):.1f} MB"
