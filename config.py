"""Application configuration for TAVIDM."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", str(BASE_DIR / "dataset" / "raw"))
FRAMES_FOLDER = os.environ.get("FRAMES_FOLDER", str(BASE_DIR / "dataset" / "frames"))
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "500"))
MAX_CONTENT_LENGTH = MAX_UPLOAD_MB * 1024 * 1024
ALLOWED_VIDEO_EXTENSIONS = {"mp4"}

SQLITE_PATH = os.environ.get("SQLITE_PATH", "database/tavidm.db")
FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "tavidm-prototype-dev-key")
