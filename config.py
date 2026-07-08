"""Application configuration for TAVIDM."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", str(BASE_DIR / "dataset" / "raw"))
FRAMES_FOLDER = os.environ.get("FRAMES_FOLDER", str(BASE_DIR / "dataset" / "frames"))
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "500"))
MAX_CONTENT_LENGTH = MAX_UPLOAD_MB * 1024 * 1024
ALLOWED_VIDEO_EXTENSIONS = {"mp4"}

DB_BACKEND = os.environ.get("DB_BACKEND", "sqlite").strip().lower() or "sqlite"
# DATABASE_URL is backend-agnostic. For sqlite, a local file path is also accepted.
DATABASE_URL = os.environ.get("DATABASE_URL", os.environ.get("SQLITE_PATH", "database/tavidm.db"))
# Backward-compatible alias for existing scripts/docs.
SQLITE_PATH = DATABASE_URL
FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "tavidm-prototype-dev-key")
