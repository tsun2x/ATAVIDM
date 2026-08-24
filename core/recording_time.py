"""Recording datetime helpers — never invent from upload/processing wall clock."""

from __future__ import annotations

from datetime import datetime


class RecordingTimeError(ValueError):
    """Raised when a provided recorded_at value cannot be parsed."""


_ACCEPTED_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d",
)


def normalize_recorded_at(raw: str | None) -> str | None:
    """Normalize optional recording time to ``YYYY-MM-DD HH:MM:SS`` or None.

    - Missing / blank → ``None`` (SQL NULL). Never invent upload or filesystem time.
    - Non-empty invalid → ``RecordingTimeError`` (callers map to HTTP 400).
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    for fmt in _ACCEPTED_FORMATS:
        try:
            dt = datetime.strptime(text, fmt)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    raise RecordingTimeError(
        f"Invalid recorded_at '{raw}'. Expected YYYY-MM-DD HH:MM:SS (or date-only)."
    )
