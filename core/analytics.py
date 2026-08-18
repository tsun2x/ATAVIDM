"""Analytics aggregations from the centralized database (manuscript Ch3,
Analytics and Visualization Module: totals, trends, hotspots, breakdowns)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from core.detection_config import IMPLEMENTED_VIOLATIONS
from database import db

_TYPE_COLORS = (
    "#e63946", "#f59e0b", "#f97316", "#8b5cf6", "#06b6d4",
    "#ec4899", "#22c55e", "#3b82f6", "#eab308", "#64748b",
)


def type_color(index: int) -> str:
    return _TYPE_COLORS[index % len(_TYPE_COLORS)]


def violation_summary() -> list[dict[str, Any]]:
    """Per-type counts with share of total (non-dismissed violations)."""
    rows = db.count_violations_by_type()
    total = sum(row["count"] for row in rows) or 1
    yesterday = (datetime.now().date() - timedelta(days=1)).isoformat()
    yesterday_by_type = db.count_violations_by_type_on(yesterday)
    summary = []
    for i, row in enumerate(rows):
        today_count = row["count"]
        yest_count = yesterday_by_type.get(row["violation_type"], 0)
        change = (
            round((today_count - yest_count) / yest_count * 100, 1)
            if yest_count > 0
            else 0.0
        )
        summary.append(
            {
                "type": row["violation_type"],
                "count": today_count,
                "color": type_color(i),
                "share": round(today_count * 100 / total, 1),
                "change": change,
            }
        )
    return summary


def _avg_confidence() -> float:
    with db.db_session() as conn:
        row = conn.execute(
            "SELECT AVG(confidence) AS avg_conf FROM violations WHERE status != 'dismissed'"
        ).fetchone()
        return round((row["avg_conf"] or 0) * 100, 1)


def dashboard_stats() -> dict[str, Any]:
    videos = db.list_videos()
    today = datetime.now().date()
    today_by_type = db.count_violations_by_type_on(today.isoformat())
    # trend_today / trend_week: % change vs previous day / previous 7 days.
    # Restores consistency with the existing dashboard.html (pre-existing edit).
    per_day = db.violations_per_day(14)
    counts = [row["count"] for row in per_day]  # ascending by day
    today_count = counts[-1] if counts else 0
    prev_day = counts[-2] if len(counts) >= 2 else 0
    last7 = sum(counts[-7:]) if counts else 0
    prev7 = sum(counts[-14:-7]) if len(counts) >= 14 else 0
    trend_today = round((today_count - prev_day) / prev_day * 100, 1) if prev_day > 0 else 0.0
    trend_week = round((last7 - prev7) / prev7 * 100, 1) if prev7 > 0 else 0.0
    cameras = db.list_cameras()
    return {
        "total_today": db.count_violations_today(),
        "total_week": last7,
        "total_all": db.count_all_violations(),
        "trend_today": trend_today,
        "trend_week": trend_week,
        "active_cameras": sum(1 for c in cameras if c.get("is_active")),
        "total_cameras": len(cameras),
        "counterflow": today_by_type.get("Counterflow Driving", 0),
        "illegal_parking": today_by_type.get("Illegal Parking", 0),
        "review_queue": db.count_review_pending(),
        "pending_review": db.count_review_pending(),
        "videos_processed": sum(1 for v in videos if v.get("processed")),
        "total_videos": len(videos),
        "avg_confidence": _avg_confidence(),
    }


def hourly_chart_today() -> tuple[list[str], list[int]]:
    today = datetime.now().date().isoformat()
    rows = {row["hour"]: row["count"] for row in db.violations_by_hour(today)}
    labels = [f"{h:02d}:00" for h in range(24)]
    values = [rows.get(h, 0) for h in range(24)]
    return labels, values


def _daily_series(days: int = 7) -> tuple[list[str], list[int]]:
    rows = {row["day"]: row["count"] for row in db.violations_per_day(days)}
    labels: list[str] = []
    values: list[int] = []
    today = datetime.now().date()
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        labels.append(day.strftime("%a %m/%d"))
        values.append(rows.get(day.isoformat(), 0))
    return labels, values


def _hourly_series() -> tuple[list[str], list[int]]:
    rows = {row["hour"]: row["count"] for row in db.violations_by_hour()}
    labels = [f"{h:02d}:00" for h in range(24)]
    values = [rows.get(h, 0) for h in range(24)]
    return labels, values


def analytics_data() -> dict[str, Any]:
    daily_labels, daily_values = _daily_series(7)
    monthly_labels, monthly_values = _daily_series(30)
    hourly_labels, hourly_values = _hourly_series()

    breakdown = db.count_violations_by_type()
    vehicle_rows = db.violations_by_vehicle_class()

    # Confidence bands follow the manuscript's manual-review policy thresholds.
    with db.db_session() as conn:
        band_row = conn.execute(
            """
            SELECT
              SUM(CASE WHEN confidence >= 0.95 THEN 1 ELSE 0 END) AS auto_q,
              SUM(CASE WHEN confidence >= 0.80 AND confidence < 0.95 THEN 1 ELSE 0 END) AS careful,
              SUM(CASE WHEN confidence < 0.80 THEN 1 ELSE 0 END) AS low
            FROM violations WHERE status != 'dismissed'
            """
        ).fetchone()

    total_week = sum(daily_values)
    peak_hour = hourly_labels[hourly_values.index(max(hourly_values))] if any(hourly_values) else "—"
    most_common = breakdown[0]["violation_type"] if breakdown else "—"

    return {
        "daily_labels": daily_labels,
        "daily_values": daily_values,
        "monthly_labels": monthly_labels,
        "monthly_values": monthly_values,
        "hourly_labels": hourly_labels,
        "hourly_values": hourly_values,
        "breakdown_labels": [row["violation_type"] for row in breakdown],
        "breakdown_values": [row["count"] for row in breakdown],
        "vehicle_labels": [row["vehicle_class"] for row in vehicle_rows],
        "vehicle_values": [row["count"] for row in vehicle_rows],
        "confidence_labels": ["≥95% (auto-queued)", "80–94% (careful review)", "<80% (monitoring)"],
        "confidence_values": [band_row["auto_q"] or 0, band_row["careful"] or 0, band_row["low"] or 0],
        "kpi": {
            "total_week": total_week,
            "peak_hour": peak_hour,
            "most_common": most_common,
            "avg_confidence": _avg_confidence(),
        },
        "violation_types": list(IMPLEMENTED_VIOLATIONS),
    }


def congestion_summary() -> dict[str, Any]:
    """Hermosa Connect: aggregate congestion analytics for dashboards/reports.

    Returns counts + hotspot top list. Safe when no congestion data exists.
    """
    events = db.list_congestion_events(limit=500)
    hotspots = db.list_hotspots(limit=10)
    severe = sum(1 for e in events if e.get("severity") in ("heavy", "severe"))
    return {
        "event_count": len(events),
        "severe_count": severe,
        "hotspot_count": len(hotspots),
        "top_hotspots": [
            {"rank": h.get("rank"), "location": h.get("location"),
             "frequency": h.get("frequency_score")}
            for h in hotspots
        ],
    }
