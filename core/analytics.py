"""Analytics aggregations from the centralized database (manuscript Ch3,
Analytics and Visualization Module: totals, trends, hotspots, breakdowns)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from core.detection_config import (
    CANONICAL_VIOLATIONS,
    VIOLATION_COUNTERFLOW,
    VIOLATION_ILLEGAL_PARKING,
    canonicalize_violation,
)
from database import db

_TYPE_COLORS = (
    "#e63946", "#f59e0b", "#f97316", "#8b5cf6", "#06b6d4",
    "#ec4899", "#22c55e", "#3b82f6", "#eab308", "#64748b",
    "#14b8a6", "#a855f7",
)


def type_color(index: int) -> str:
    return _TYPE_COLORS[index % len(_TYPE_COLORS)]


def _canonical_counts_from_rows(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        canon = canonicalize_violation(row["violation_type"])
        counts[canon] = counts.get(canon, 0) + row["count"]
    return counts


def _canonical_counts_from_map(type_counts: dict[str, int]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for viol_type, count in type_counts.items():
        canon = canonicalize_violation(viol_type)
        counts[canon] = counts.get(canon, 0) + count
    return counts


def violation_summary() -> list[dict[str, Any]]:
    """Per-type counts with share of total (non-dismissed violations)."""
    rows = db.count_violations_by_type()
    canonical = _canonical_counts_from_rows(rows)
    ordered = sorted(canonical.items(), key=lambda item: item[1], reverse=True)
    total = sum(count for _, count in ordered) or 1
    return [
        {
            "type": viol_type,
            "count": count,
            "color": type_color(i),
            "share": round(count * 100 / total, 1),
        }
        for i, (viol_type, count) in enumerate(ordered)
    ]


def _avg_confidence() -> float:
    with db.db_session() as conn:
        row = conn.execute(
            "SELECT AVG(confidence) AS avg_conf FROM violations WHERE status != 'dismissed'"
        ).fetchone()
        return round((row["avg_conf"] or 0) * 100, 1)


def dashboard_stats() -> dict[str, Any]:
    videos = db.list_videos()
    today = datetime.now().date()
    today_by_type = _canonical_counts_from_map(db.count_violations_by_type_on(today.isoformat()))
    return {
        "total_today": db.count_violations_today(),
        "total_all": db.count_all_violations(),
        "counterflow": today_by_type.get(VIOLATION_COUNTERFLOW, 0),
        "illegal_parking": today_by_type.get(VIOLATION_ILLEGAL_PARKING, 0),
        "review_queue": db.count_review_pending(),
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

    breakdown_raw = db.count_violations_by_type()
    breakdown_map = _canonical_counts_from_rows(breakdown_raw)
    breakdown = [
        {"violation_type": viol_type, "count": count}
        for viol_type, count in sorted(breakdown_map.items(), key=lambda x: x[1], reverse=True)
    ]
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
        "violation_types": list(CANONICAL_VIOLATIONS),
    }
