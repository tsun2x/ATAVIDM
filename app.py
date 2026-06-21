"""TAVIDM - Traffic Violation Detection and Monitoring System
Frontend prototype only. All data is mocked for demonstration purposes.
"""

from datetime import datetime, timedelta
import random

from flask import Flask, render_template

app = Flask(__name__)
app.config["SECRET_KEY"] = "tavidm-prototype-dev-key"

# ---------------------------------------------------------------------------
# Mock Data
# ---------------------------------------------------------------------------

CAMERAS = [
    {
        "id": "cam-01",
        "name": "Inbound Normal Road",
        "location": "Inbound Lane — Normal Road Sector",
        "status": "online",
        "fps": 30,
        "feed_image": "cctv_inbound.svg",
    },
    {
        "id": "cam-02",
        "name": "Outbound Normal Road",
        "location": "Outbound Lane — Normal Road Sector",
        "status": "online",
        "fps": 30,
        "feed_image": "cctv_outbound.svg",
    },
]

VIOLATION_TYPES = [
    "Red Light Violation",
    "Speeding",
    "Wrong Lane",
    "No Helmet",
    "Illegal Parking",
    "Jaywalking",
    "No Seatbelt",
    "Illegal Turn",
]

PLATE_PREFIXES = ["ABC", "XYZ", "TAV", "PHL", "NCR", "MNL"]

STATUSES = ["Pending", "Verified", "Dismissed", "Escalated"]


def _random_plate():
    return f"{random.choice(PLATE_PREFIXES)} {random.randint(100, 999)} {chr(random.randint(65, 90))}{chr(random.randint(65, 90))}"


def generate_violations(count=48):
    violations = []
    base_time = datetime.now()
    for i in range(1, count + 1):
        vtype = random.choice(VIOLATION_TYPES)
        cam = random.choice(CAMERAS)
        ts = base_time - timedelta(hours=random.randint(0, 72), minutes=random.randint(0, 59))
        violations.append(
            {
                "id": f"VIO-{2025}{i:04d}",
                "type": vtype,
                "plate": _random_plate(),
                "camera_id": cam["id"],
                "camera_name": cam["name"],
                "location": cam["location"],
                "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "timestamp_iso": ts.isoformat(),
                "confidence": round(random.uniform(0.72, 0.99), 2),
                "status": random.choice(STATUSES),
                "severity": random.choice(["Low", "Medium", "High", "Critical"]),
                "evidence_image": f"evidence_{(i % 6) + 1}.svg",
                "notes": "Automated detection via TAVIDM AI pipeline (mock).",
            }
        )
    violations.sort(key=lambda v: v["timestamp_iso"], reverse=True)
    return violations


MOCK_VIOLATIONS = generate_violations()

DASHBOARD_STATS = {
    "total_today": 127,
    "total_week": 842,
    "active_cameras": sum(1 for c in CAMERAS if c["status"] == "online"),
    "total_cameras": len(CAMERAS),
    "pending_review": sum(1 for v in MOCK_VIOLATIONS if v["status"] == "Pending"),
    "avg_confidence": round(sum(v["confidence"] for v in MOCK_VIOLATIONS[:20]) / 20 * 100, 1),
    "trend_today": 12.4,
    "trend_week": -3.2,
}

_VIOLATION_COUNTS = [
    ("Red Light Violation", 342, 8.2, "#ef4444"),
    ("Speeding", 218, -2.1, "#f97316"),
    ("Wrong Lane", 156, 5.4, "#eab308"),
    ("No Helmet", 134, 11.0, "#8b5cf6"),
    ("Illegal Parking", 98, -1.5, "#06b6d4"),
]
_total_violation_count = sum(v[1] for v in _VIOLATION_COUNTS)
VIOLATION_SUMMARY = [
    {
        "type": t,
        "count": c,
        "change": ch,
        "color": col,
        "share": round(c / _total_violation_count * 100, 1),
    }
    for t, c, ch, col in _VIOLATION_COUNTS
]

ANALYTICS_DATA = {
    "daily_labels": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    "daily_values": [89, 112, 95, 127, 143, 78, 65],
    "monthly_labels": ["Jan", "Feb", "Mar", "Apr", "May", "Jun"],
    "monthly_values": [1840, 2105, 1987, 2340, 2567, 2210],
    "breakdown_labels": [s["type"] for s in VIOLATION_SUMMARY],
    "breakdown_values": [s["count"] for s in VIOLATION_SUMMARY],
    "kpi": {
        "total_violations": 1248,
        "detection_rate": 94.7,
        "avg_response_time": "4.2 min",
        "accuracy": 91.3,
    },
}

REPORT_HISTORY = [
    {"id": "RPT-001", "name": "Weekly Violation Summary", "type": "PDF", "date": "2025-06-20", "size": "2.4 MB", "status": "Ready"},
    {"id": "RPT-002", "name": "Monthly Analytics Export", "type": "Excel", "date": "2025-06-15", "size": "1.8 MB", "status": "Ready"},
    {"id": "RPT-003", "name": "Red Light Violations Q2", "type": "PDF", "date": "2025-06-10", "size": "3.1 MB", "status": "Ready"},
    {"id": "RPT-004", "name": "Camera Performance Report", "type": "Excel", "date": "2025-06-05", "size": "956 KB", "status": "Ready"},
    {"id": "RPT-005", "name": "Daily Snapshot", "type": "PDF", "date": "2025-06-01", "size": "1.2 MB", "status": "Expired"},
]

USERS = [
    {"id": 1, "name": "Admin User", "email": "admin@tavidm.local", "role": "Administrator", "status": "Active", "last_login": "2025-06-21 08:30"},
    {"id": 2, "name": "Juan Dela Cruz", "email": "juan.dc@tavidm.local", "role": "Operator", "status": "Active", "last_login": "2025-06-21 07:15"},
    {"id": 3, "name": "Maria Santos", "email": "maria.s@tavidm.local", "role": "Reviewer", "status": "Active", "last_login": "2025-06-20 16:45"},
    {"id": 4, "name": "Pedro Reyes", "email": "pedro.r@tavidm.local", "role": "Operator", "status": "Inactive", "last_login": "2025-06-10 09:00"},
]

SYSTEM_SETTINGS = {
    "site_name": "TAVIDM",
    "timezone": "Asia/Manila",
    "alert_sound": True,
    "auto_export": False,
    "retention_days": 90,
    "confidence_threshold": 75,
    "speed_threshold": 60,
    "red_light_sensitivity": 80,
    "helmet_detection": True,
    "night_mode_enhance": True,
}

DETECTION_BOXES = [
    {"label": "Vehicle", "x": 12, "y": 35, "w": 28, "h": 22, "confidence": 0.94},
    {"label": "Red Light", "x": 68, "y": 8, "w": 8, "h": 12, "confidence": 0.98},
    {"label": "Plate", "x": 18, "y": 52, "w": 12, "h": 5, "confidence": 0.87},
]


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
            {"endpoint": "live_monitor", "label": "Live Monitor", "icon": "bi-camera-video"},
            {"endpoint": "violations", "label": "Violations", "icon": "bi-exclamation-triangle"},
            {"endpoint": "analytics", "label": "Analytics", "icon": "bi-bar-chart-line"},
            {"endpoint": "reports", "label": "Reports", "icon": "bi-file-earmark-text"},
            {"endpoint": "settings", "label": "Settings", "icon": "bi-gear"},
        ],
    }


@app.route("/")
def dashboard():
    chart_data = {
        "hourly_labels": [f"{h:02d}:00" for h in range(6, 22, 2)],
        "hourly_values": [8, 15, 22, 18, 31, 28, 19, 12],
        "weekly_labels": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        "weekly_values": [112, 98, 134, 127, 156, 89, 76],
    }
    return render_template(
        "dashboard.html",
        stats=DASHBOARD_STATS,
        recent_violations=MOCK_VIOLATIONS[:8],
        violation_summary=VIOLATION_SUMMARY,
        chart_data=chart_data,
    )


@app.route("/live-monitor")
def live_monitor():
    return render_template(
        "live_monitor.html",
        cameras=CAMERAS,
        default_camera=CAMERAS[0],
        detection_boxes=DETECTION_BOXES,
    )


@app.route("/violations")
def violations():
    return render_template(
        "violations.html",
        violations=MOCK_VIOLATIONS,
        violation_types=VIOLATION_TYPES,
        cameras=CAMERAS,
        statuses=STATUSES,
    )


@app.route("/analytics")
def analytics():
    return render_template(
        "analytics.html",
        analytics=ANALYTICS_DATA,
        violation_summary=VIOLATION_SUMMARY,
    )


@app.route("/reports")
def reports():
    return render_template(
        "reports.html",
        report_history=REPORT_HISTORY,
        violation_types=VIOLATION_TYPES,
    )


@app.route("/settings")
def settings():
    return render_template(
        "settings.html",
        cameras=CAMERAS,
        users=USERS,
        settings=SYSTEM_SETTINGS,
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
