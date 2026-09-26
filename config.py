"""Application configuration for TAVIDM."""

import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env", override=False)
except ImportError:  # environment variables can still be supplied by the host
    pass

UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", str(BASE_DIR / "dataset" / "raw"))
FRAMES_FOLDER = os.environ.get("FRAMES_FOLDER", str(BASE_DIR / "dataset" / "frames"))
# Per-run annotated replay MP4s (never served as unauthenticated static files).
ANNOTATED_FOLDER = os.environ.get(
    "ANNOTATED_FOLDER", str(BASE_DIR / "dataset" / "annotated")
)
# Evidence and reports are private files served only through authenticated routes.
EVIDENCE_FOLDER = os.environ.get("EVIDENCE_FOLDER", str(BASE_DIR / "dataset" / "evidence"))
# Generated PDF/Excel reports.
REPORTS_FOLDER = os.environ.get("REPORTS_FOLDER", str(BASE_DIR / "dataset" / "reports"))
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "3072"))
MAX_CONTENT_LENGTH = MAX_UPLOAD_MB * 1024 * 1024
ALLOWED_VIDEO_EXTENSIONS = {"mp4"}
# Period boundaries for upload/processing analytics (never use recording time).
APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "Asia/Manila")

DB_BACKEND = os.environ.get("DB_BACKEND", "sqlite").strip().lower() or "sqlite"
# DATABASE_URL is backend-agnostic. For sqlite, a local file path is also accepted.
DATABASE_URL = os.environ.get("DATABASE_URL", os.environ.get("SQLITE_PATH", "database/tavidm.db"))
# Backward-compatible alias for existing scripts/docs.
SQLITE_PATH = DATABASE_URL
_environment = os.environ.get("TAVIDM_ENV", "development").strip().lower()
_configured_secret = os.environ.get("FLASK_SECRET_KEY", "").strip()
if _environment in {"production", "prod"} and not _configured_secret:
    raise RuntimeError("FLASK_SECRET_KEY must be configured when TAVIDM_ENV=production.")
FLASK_SECRET_KEY = _configured_secret or secrets.token_hex(32)
SESSION_COOKIE_SECURE = _environment in {"production", "prod"}
