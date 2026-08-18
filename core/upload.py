"""Video upload validation and file handling."""

from __future__ import annotations

import os
import time
from pathlib import Path

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

import config


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
