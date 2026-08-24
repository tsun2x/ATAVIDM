"""Upload and processing analytics with timezone-aware period boundaries."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from config import APP_TIMEZONE
from database import db


def _tz():
    """Application timezone for analytics period boundaries."""
    try:
        return ZoneInfo(APP_TIMEZONE)
    except Exception:
        # Windows installs may lack tzdata; Manila is UTC+8 year-round (no DST).
        if str(APP_TIMEZONE).upper() in ("ASIA/MANILA", "ASIA/SINGAPORE"):
            from datetime import timezone as _tzmod, timedelta
            return _tzmod(timedelta(hours=8))
        from datetime import timezone as _tzmod
        return _tzmod.utc


def period_bounds(
    period: str,
    *,
    start: str | None = None,
    end: str | None = None,
    now: datetime | None = None,
) -> tuple[datetime, datetime, str]:
    """Return [start, end) in UTC-naive wall times stored as app-local strings.

    SQLite stores naive DATETIME in application local time (APP_TIMEZONE).
    """
    tz = _tz()
    now_local = (now or datetime.now(tz=tz)).astimezone(tz)
    period = (period or "today").lower().strip()

    if period == "custom":
        if not start or not end:
            raise ValueError("Custom range requires start and end (YYYY-MM-DD).")
        start_dt = datetime.strptime(start[:10], "%Y-%m-%d").replace(tzinfo=tz)
        end_dt = datetime.strptime(end[:10], "%Y-%m-%d").replace(tzinfo=tz) + timedelta(days=1)
        if end_dt <= start_dt:
            raise ValueError("Custom range end must be after start.")
        grain = "day"
        return start_dt.replace(tzinfo=None), end_dt.replace(tzinfo=None), grain

    if period == "today":
        start_dt = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = start_dt + timedelta(days=1)
        grain = "day"
    elif period in ("week", "this_week"):
        start_dt = (now_local - timedelta(days=now_local.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        end_dt = start_dt + timedelta(days=7)
        grain = "day"
    elif period in ("month", "this_month"):
        start_dt = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if start_dt.month == 12:
            end_dt = start_dt.replace(year=start_dt.year + 1, month=1)
        else:
            end_dt = start_dt.replace(month=start_dt.month + 1)
        grain = "day"
    else:
        raise ValueError(f"Unknown period: {period}")

    return start_dt.replace(tzinfo=None), end_dt.replace(tzinfo=None), grain


def build_upload_processing_analytics(
    period: str = "today",
    *,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    start_dt, end_dt, grain = period_bounds(period, start=start, end=end)
    start_s = start_dt.strftime("%Y-%m-%d %H:%M:%S")
    end_s = end_dt.strftime("%Y-%m-%d %H:%M:%S")

    stats = db.upload_processing_analytics(start_s, end_s, grain=grain)
    stats["period"] = period
    stats["timezone"] = APP_TIMEZONE
    stats["start"] = start_s
    stats["end"] = end_s
    stats["grain"] = grain
    return stats
