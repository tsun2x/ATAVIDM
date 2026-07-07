"""TAVIDM - Traffic Violation Detection and Monitoring System
Frontend prototype with mock data. Decision-support tool only.
"""

from datetime import datetime, timedelta
import random

from flask import Flask, render_template

app = Flask(__name__)
app.config["SECRET_KEY"] = "tavidm-prototype-dev-key"

# ---------------------------------------------------------------------------
# Mock Data — mirrors static/js/mock-data.js
# ---------------------------------------------------------------------------

VIOLATION_TYPES = [
    "Illegal Parking",
    "Counterflowing",
    "Obstruction",
    "Illegal Loading/Unloading",
    "Blocking Pedestrian Crossing",
    "Truck Ban",
    "Speeding",
    "Reckless Driving",
]

STATUSES = ["confirmed", "dismissed", "pending"]
CONDITIONS = ["morning", "peak", "nighttime"]

VIDEOS = [
    {
        "id": "vid-01",
        "name": "Morning Traffic — Footbridge Cam A",
        "filename": "morning_traffic.mp4",
        "location": "Normal Road Sector — Northbound",
        "condition": "morning",
        "duration": "12:34",
        "processed": True,
        "thumbnail": "cctv_inbound.svg",
    },
    {
        "id": "vid-02",
        "name": "Peak Hour — Footbridge Cam B",
        "filename": "peak_traffic.mp4",
        "location": "Normal Road Sector — Southbound",
        "condition": "peak",
        "duration": "18:02",
        "processed": True,
        "thumbnail": "cctv_outbound.svg",
    },
    {
        "id": "vid-03",
        "name": "Nighttime — Footbridge Cam A",
        "filename": "night_traffic.mp4",
        "location": "Normal Road Sector — Northbound",
        "condition": "nighttime",
        "duration": "09:47",
        "processed": False,
        "thumbnail": "cctv_feed.svg",
    },
]


def _vtype_slug(vtype):
    return vtype.lower().replace(" ", "-").replace("/", "-")


def generate_violations(count=48):
    violations = []
    base_time = datetime.now()
    for i in range(1, count + 1):
        vtype = VIOLATION_TYPES[i % len(VIOLATION_TYPES)]
        video = VIDEOS[i % len(VIDEOS)]
        ts = base_time - timedelta(hours=i, minutes=random.randint(0, 59))
        confidence = round(random.uniform(0.58, 0.96), 2)
        status = "pending" if confidence < 0.75 else random.choice(["confirmed", "confirmed", "dismissed"])
        violations.append(
            {
                "id": f"VIO-2026{i:04d}",
                "type": vtype,
                "type_slug": _vtype_slug(vtype),
                "video_id": video["id"],
                "video_name": video["name"],
                "track_id": random.randint(1, 20),
                "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "timestamp_iso": ts.isoformat(),
                "confidence": confidence,
                "status": status,
                "condition": video["condition"],
                "evidence_image": f"evidence_{(i % 6) + 1}.svg",
                "reason_log": "Rule-based detection via YOLOv8 + ByteTrack pipeline (mock).",
                "frame_number": random.randint(100, 18000),
            }
        )
    violations.sort(key=lambda v: v["timestamp_iso"], reverse=True)
    return violations


MOCK_VIOLATIONS = generate_violations()

REVIEW_QUEUE = [
    {
        "id": f"RQ-{i:04d}",
        "violation_id": v["id"],
        "video_id": v["video_id"],
        "video_name": v["video_name"],
        "track_id": v["track_id"],
        "violation_type": v["type"],
        "type_slug": v["type_slug"],
        "confidence": v["confidence"],
        "frame_number": v["frame_number"],
        "evidence_image": v["evidence_image"],
        "reason_log": v["reason_log"],
        "queued_at": v["timestamp"],
        "status": "pending",
    }
    for i, v in enumerate([v for v in MOCK_VIOLATIONS if v["status"] == "pending"][:12], 1)
]

DASHBOARD_STATS = {
    "total_today": 34,
    "counterflow": 8,
    "illegal_parking": 11,
    "review_queue": len(REVIEW_QUEUE),
    "videos_processed": 2,
    "total_videos": len(VIDEOS),
    "avg_confidence": 81.4,
    "trend_today": 6.2,
}

VIOLATION_SUMMARY = [
    {"type": "Counterflowing", "count": 42, "change": 5.1, "color": "#e63946", "share": 22.3},
    {"type": "Illegal Parking", "count": 38, "change": -2.4, "color": "#f59e0b", "share": 20.2},
    {"type": "Obstruction", "count": 28, "change": 3.8, "color": "#f97316", "share": 14.9},
    {"type": "Speeding", "count": 24, "change": 1.2, "color": "#f43f5e", "share": 12.8},
    {"type": "Illegal Loading/Unloading", "count": 19, "change": -1.0, "color": "#22c55e", "share": 10.1},
]

ANALYTICS_DATA = {
    "daily_labels": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    "daily_values": [28, 35, 31, 42, 48, 22, 18],
    "monthly_labels": ["Jan", "Feb", "Mar", "Apr", "May", "Jun"],
    "monthly_values": [312, 348, 329, 401, 438, 389],
    "breakdown_labels": VIOLATION_TYPES,
    "breakdown_values": [38, 42, 28, 19, 15, 12, 24, 11],
    "condition_labels": ["Morning", "Peak", "Nighttime"],
    "condition_values": [142, 198, 89],
    "confidence_labels": ["≥85%", "65–84%", "<65%"],
    "confidence_values": [112, 58, 19],
    "kpi": {
        "total_week": 224,
        "peak_hour": "16:00",
        "most_common": "Counterflowing",
        "accuracy": 87.6,
    },
}

REPORT_HISTORY = [
    {"id": "RPT-001", "name": "Weekly Violation Summary", "type": "PDF", "date": "2026-06-20", "size": "2.4 MB", "status": "Ready", "filters": "Jun 14–20 · All types"},
    {"id": "RPT-002", "name": "Counterflowing Analysis", "type": "Excel", "date": "2026-06-15", "size": "1.8 MB", "status": "Ready", "filters": "Jun 1–15 · Counterflowing"},
    {"id": "RPT-003", "name": "Peak Hour Violations", "type": "PDF", "date": "2026-06-10", "size": "3.1 MB", "status": "Ready", "filters": "Peak condition"},
    {"id": "RPT-004", "name": "Review Queue Export", "type": "Excel", "date": "2026-06-05", "size": "956 KB", "status": "Ready", "filters": "Pending items"},
]

USERS = [
    {"id": 1, "username": "admin", "name": "Admin User", "role": "admin", "created_at": "2026-01-15"},
    {"id": 2, "username": "enforcer1", "name": "Juan Dela Cruz", "role": "enforcer", "created_at": "2026-02-03"},
    {"id": 3, "username": "enforcer2", "name": "Maria Santos", "role": "enforcer", "created_at": "2026-02-10"},
]

SYSTEM_SETTINGS = {
    "confidence_threshold": 60,
    "review_threshold": 75,
    "speed_limit": 60,
    "truck_ban_start": "06:00",
    "truck_ban_end": "09:00",
    "frame_skip": 2,
    "evidence_retention_days": 90,
    "fps": 30,
    "calibration_ppm": 12.5,
    "zones_json": '{\n  "no_parking": [[100,200],[300,200],[300,400],[100,400]],\n  "active_lane": [[400,150],[800,150],[800,450],[400,450]]\n}',
}

DETECTION_BOXES = [
    {"label": "car", "track_id": 3, "x": 12, "y": 35, "w": 28, "h": 22, "confidence": 0.91},
    {"label": "jeepney", "track_id": 7, "x": 48, "y": 28, "w": 22, "h": 18, "confidence": 0.87},
    {"label": "motorcycle", "track_id": 12, "x": 68, "y": 42, "w": 10, "h": 14, "confidence": 0.84},
]

SYSTEM_STATUS = {
    "pipeline": "Ready",
    "model": "YOLOv8n (mock)",
    "tracker": "ByteTrack",
    "db": "SQLite",
    "last_processed": "peak_traffic.mp4",
}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.context_processor
def inject_globals():
    return {
        "app_name": "TAVIDM",
        "app_full_name": "Traffic Violation Detection and Monitoring System",
        "current_year": datetime.now().year,
        "nav_items": [
            {"endpoint": "dashboard", "label": "Dashboard", "icon": "bi-speedometer2"},
            {"endpoint": "live_monitor", "label": "Live Monitor", "icon": "bi-camera-video", "badge": "live"},
            {"endpoint": "violations", "label": "Violations", "icon": "bi-exclamation-triangle", "badge_count": len(MOCK_VIOLATIONS)},
            {"endpoint": "analytics", "label": "Analytics", "icon": "bi-bar-chart-line"},
            {"endpoint": "reports", "label": "Reports", "icon": "bi-file-earmark-text"},
            {"endpoint": "settings", "label": "Settings", "icon": "bi-gear"},
        ],
        "review_queue_count": len(REVIEW_QUEUE),
    }


@app.route("/")
def dashboard():
    chart_data = {
        "hourly_labels": ["06:00", "08:00", "10:00", "12:00", "14:00", "16:00", "18:00", "20:00"],
        "hourly_values": [2, 5, 8, 6, 11, 9, 7, 4],
        "distribution_labels": [s["type"] for s in VIOLATION_SUMMARY],
        "distribution_values": [s["count"] for s in VIOLATION_SUMMARY],
    }
    return render_template(
        "dashboard.html",
        stats=DASHBOARD_STATS,
        recent_violations=MOCK_VIOLATIONS[:8],
        violation_summary=VIOLATION_SUMMARY,
        chart_data=chart_data,
        system_status=SYSTEM_STATUS,
    )


@app.route("/live-monitor")
def live_monitor():
    return render_template(
        "live_monitor.html",
        videos=VIDEOS,
        default_video=VIDEOS[0],
        detection_boxes=DETECTION_BOXES,
    )


@app.route("/violations")
def violations():
    return render_template(
        "violations.html",
        violations=MOCK_VIOLATIONS,
        violation_types=VIOLATION_TYPES,
        videos=VIDEOS,
        statuses=STATUSES,
    )


@app.route("/analytics")
def analytics():
    return render_template(
        "analytics.html",
        analytics=ANALYTICS_DATA,
        violation_summary=VIOLATION_SUMMARY,
        conditions=CONDITIONS,
    )


@app.route("/reports")
def reports():
    return render_template(
        "reports.html",
        report_history=REPORT_HISTORY,
        violation_types=VIOLATION_TYPES,
        videos=VIDEOS,
        conditions=CONDITIONS,
    )


@app.route("/settings")
def settings():
    return render_template(
        "settings.html",
        videos=VIDEOS,
        users=USERS,
        settings=SYSTEM_SETTINGS,
    )


@app.route("/review-queue")
def review_queue():
    return render_template(
        "review_queue.html",
        review_items=REVIEW_QUEUE,
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
