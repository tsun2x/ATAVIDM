"""TAVIDM - Traffic Violation Detection and Monitoring System
Frontend prototype with mock data. Decision-support tool only.
"""

from datetime import datetime, timedelta
import json
import os
import random
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_from_directory

import config
from core.frame_extract import FrameExtractError, extract_first_frame, frame_path_for_video
from core.upload import UploadError, ensure_upload_dir, format_file_size, save_video_file, validate_upload
from core.zone_config import (
    dumps_zones,
    parse_zones_json,
    zones_complete,
    zones_for_api,
)
from database import db

app = Flask(__name__)
app.config["SECRET_KEY"] = config.FLASK_SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = config.MAX_CONTENT_LENGTH

ensure_upload_dir()
db.init_db()

# ---------------------------------------------------------------------------
# Mock Data — mirrors static/js/mock-data.js (demo seed when DB is empty)
# ---------------------------------------------------------------------------

VIOLATION_TYPES = [
    "Illegal Parking",
    "Counterflowing",
    "Obstruction",
    "Illegal Loading/Unloading",
    "Blocking Pedestrian Crossing",
    "Truck Ban",
    "Reckless Driving",
    "No Helmet Violation",
    "Motorcycle Overloading",
]

STATUSES = ["confirmed", "dismissed", "pending"]
CONDITIONS = ["morning", "peak", "nighttime"]

SEED_VIDEOS = [
    {
        "id": "vid-01",
        "name": "Morning Traffic — Footbridge Cam A",
        "filename": "morning_traffic.mp4",
        "location": "Normal Road Sector — Northbound",
        "condition": "morning",
        "duration": "12:34",
        "processed": True,
        "thumbnail": "cctv_inbound.svg",
        "is_uploaded": False,
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
        "is_uploaded": False,
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
        "is_uploaded": False,
    },
]


def _vtype_slug(vtype):
    return vtype.lower().replace(" ", "-").replace("/", "-")


def _format_duration(duration_sec: float | None) -> str:
    if not duration_sec:
        return "—"
    total = int(duration_sec)
    minutes, seconds = divmod(total, 60)
    return f"{minutes}:{seconds:02d}"


def _db_video_to_ui(row: dict) -> dict:
    condition = row.get("condition") or "peak"
    status = row.get("status") or ("processed" if row.get("processed") else "uploaded")
    db_id = row["id"]
    has_annotation = bool(row.get("annotation_id"))
    return {
        "id": f"vid-db-{db_id}",
        "db_id": db_id,
        "name": row["filename"],
        "filename": row["filename"],
        "location": f"Uploaded · {condition.title()}",
        "condition": condition,
        "duration": _format_duration(row.get("duration_sec")),
        "processed": bool(row.get("processed")),
        "status": status,
        "has_annotation": has_annotation,
        "template_id": row.get("template_id"),
        "thumbnail": "cctv_feed.svg",
        "filepath": row["filepath"],
        "is_uploaded": True,
        "created_at": row.get("created_at"),
        "frame_url": f"/api/videos/{db_id}/frame",
    }


def _template_to_ui(row: dict) -> dict:
    return {
        "id": row["id"],
        "template_name": row["template_name"],
        "description": row.get("description") or "",
        "zones_json": row.get("zones_json") or "{}",
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "last_used_at": row.get("last_used_at"),
        "usage_count": row.get("usage_count") or 0,
        "video_count": db.count_videos_for_template(row["id"]),
    }


def get_videos_for_ui() -> list[dict]:
    uploaded = [_db_video_to_ui(row) for row in db.list_videos()]
    if uploaded:
        return uploaded + [v for v in SEED_VIDEOS if not any(u["filename"] == v["filename"] for u in uploaded)]
    return list(SEED_VIDEOS)


VIDEOS = get_videos_for_ui()


def generate_violations(count=48):
    violations = []
    base_time = datetime.now()
    videos = get_videos_for_ui()
    for i in range(1, count + 1):
        vtype = VIOLATION_TYPES[i % len(VIOLATION_TYPES)]
        video = videos[i % len(videos)]
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
    "videos_processed": sum(1 for v in get_videos_for_ui() if v.get("processed")),
    "total_videos": len(get_videos_for_ui()),
    "avg_confidence": 81.4,
    "trend_today": 6.2,
}

VIOLATION_SUMMARY = [
    {"type": "Counterflowing", "count": 42, "change": 5.1, "color": "#e63946", "share": 22.3},
    {"type": "Illegal Parking", "count": 38, "change": -2.4, "color": "#f59e0b", "share": 20.2},
    {"type": "Obstruction", "count": 28, "change": 3.8, "color": "#f97316", "share": 14.9},
    {"type": "Truck Ban", "count": 24, "change": 1.2, "color": "#8b5cf6", "share": 12.8},
    {"type": "No Helmet Violation", "count": 21, "change": 4.5, "color": "#06b6d4", "share": 11.2},
    {"type": "Motorcycle Overloading", "count": 16, "change": 2.1, "color": "#ec4899", "share": 8.5},
]

ANALYTICS_DATA = {
    "daily_labels": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    "daily_values": [28, 35, 31, 42, 48, 22, 18],
    "monthly_labels": ["Jan", "Feb", "Mar", "Apr", "May", "Jun"],
    "monthly_values": [312, 348, 329, 401, 438, 389],
    "breakdown_labels": VIOLATION_TYPES,
    "breakdown_values": [38, 42, 28, 19, 15, 12, 11, 21, 16],
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
    "truck_ban_start": "06:00",
    "truck_ban_end": "09:00",
    "frame_skip": 2,
    "evidence_retention_days": 90,
    "fps": 30,
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
        "max_upload_mb": config.MAX_UPLOAD_MB,
    }


@app.errorhandler(413)
def request_entity_too_large(_error):
    return jsonify({
        "success": False,
        "error": f"File exceeds maximum upload size of {config.MAX_UPLOAD_MB} MB.",
    }), 413


@app.route("/api/videos", methods=["GET"])
def api_list_videos():
    return jsonify({"success": True, "videos": get_videos_for_ui()})


@app.route("/api/zone-types", methods=["GET"])
def api_zone_types():
    return jsonify({"success": True, "zone_types": zones_for_api()})


@app.route("/api/zone-templates", methods=["GET"])
def api_list_zone_templates():
    templates = [_template_to_ui(row) for row in db.list_zone_templates()]
    return jsonify({"success": True, "templates": templates})


@app.route("/api/zone-templates", methods=["POST"])
def api_create_zone_template():
    payload = request.get_json(silent=True) or {}
    name = (payload.get("template_name") or "").strip()
    if not name:
        return jsonify({"success": False, "error": "Template name is required."}), 400
    try:
        zones_json = dumps_zones(payload.get("zones_json") or payload.get("zones") or {})
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    template_id = db.create_zone_template(
        template_name=name,
        zones_json=zones_json,
        description=(payload.get("description") or "").strip() or None,
    )
    row = db.get_zone_template(template_id)
    return jsonify({"success": True, "template": _template_to_ui(row)})


@app.route("/api/zone-templates/<int:template_id>", methods=["GET"])
def api_get_zone_template(template_id: int):
    row = db.get_zone_template(template_id)
    if row is None:
        return jsonify({"success": False, "error": "Template not found."}), 404
    return jsonify({"success": True, "template": _template_to_ui(row)})


@app.route("/api/zone-templates/<int:template_id>", methods=["PUT"])
def api_update_zone_template(template_id: int):
    row = db.get_zone_template(template_id)
    if row is None:
        return jsonify({"success": False, "error": "Template not found."}), 404

    payload = request.get_json(silent=True) or {}
    zones_json = None
    if "zones_json" in payload or "zones" in payload:
        try:
            zones_json = dumps_zones(payload.get("zones_json") or payload.get("zones") or {})
        except ValueError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400

    name = payload.get("template_name")
    if name is not None:
        name = name.strip()
        if not name:
            return jsonify({"success": False, "error": "Template name cannot be empty."}), 400

    db.update_zone_template(
        template_id,
        template_name=name,
        description=payload.get("description"),
        zones_json=zones_json,
    )
    updated = db.get_zone_template(template_id)
    return jsonify({"success": True, "template": _template_to_ui(updated)})


@app.route("/api/zone-templates/<int:template_id>", methods=["DELETE"])
def api_delete_zone_template(template_id: int):
    row = db.get_zone_template(template_id)
    if row is None:
        return jsonify({"success": False, "error": "Template not found."}), 404
    db.delete_zone_template(template_id)
    return jsonify({"success": True})


@app.route("/api/zone-templates/<int:template_id>/duplicate", methods=["POST"])
def api_duplicate_zone_template(template_id: int):
    payload = request.get_json(silent=True) or {}
    new_name = (payload.get("template_name") or "").strip()
    if not new_name:
        source = db.get_zone_template(template_id)
        if source is None:
            return jsonify({"success": False, "error": "Template not found."}), 404
        new_name = f"{source['template_name']} (Copy)"
    try:
        new_id = db.duplicate_zone_template(template_id, new_name)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    row = db.get_zone_template(new_id)
    return jsonify({"success": True, "template": _template_to_ui(row)})


@app.route("/api/videos/<int:video_id>/frame")
def api_video_frame(video_id: int):
    row = db.get_video(video_id)
    if row is None:
        return jsonify({"success": False, "error": "Video not found."}), 404

    frame_path = frame_path_for_video(video_id)
    if not frame_path.is_file():
        annotation = db.get_annotation_by_video(video_id)
        ref = annotation.get("reference_frame_path") if annotation else None
        if ref and Path(ref).is_file():
            frame_path = Path(ref)
        else:
            try:
                extract_first_frame(row["filepath"], video_id)
            except FrameExtractError as exc:
                return jsonify({"success": False, "error": str(exc)}), 500

    return send_from_directory(
        frame_path.parent,
        frame_path.name,
        mimetype="image/jpeg",
    )


@app.route("/api/videos/<int:video_id>/annotation", methods=["GET"])
def api_get_annotation(video_id: int):
    row = db.get_video(video_id)
    if row is None:
        return jsonify({"success": False, "error": "Video not found."}), 404
    annotation = db.get_annotation_by_video(video_id)
    if annotation is None:
        return jsonify({"success": True, "annotation": None})
    return jsonify({"success": True, "annotation": annotation})


@app.route("/api/videos/<int:video_id>/annotation", methods=["POST", "PUT"])
def api_save_annotation(video_id: int):
    row = db.get_video(video_id)
    if row is None:
        return jsonify({"success": False, "error": "Video not found."}), 404

    payload = request.get_json(silent=True) or {}
    save_mode = payload.get("save_mode", "video_only")
    source_template_id = payload.get("source_template_id")

    try:
        zones = parse_zones_json(payload.get("zones_json") or payload.get("zones") or {})
        zones_json = dumps_zones(zones)
    except (ValueError, json.JSONDecodeError) as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    if not zones_complete(zones):
        return jsonify({
            "success": False,
            "error": "All required zones must have at least 3 points.",
        }), 400

    frame_path = frame_path_for_video(video_id)
    ref_path = str(frame_path) if frame_path.is_file() else None
    annotation_id = db.upsert_annotation(video_id, zones_json, ref_path)

    template_id = row.get("template_id")
    template_row = None

    if save_mode == "new_template":
        name = (payload.get("template_name") or "").strip()
        if not name:
            return jsonify({"success": False, "error": "Template name is required."}), 400
        template_id = db.create_zone_template(
            template_name=name,
            zones_json=zones_json,
            description=(payload.get("template_description") or "").strip() or None,
        )
        db.update_video(video_id, template_id=template_id)
        template_row = db.get_zone_template(template_id)
    elif save_mode == "update_template":
        update_id = payload.get("template_id") or source_template_id or template_id
        if not update_id:
            return jsonify({"success": False, "error": "No template selected to update."}), 400
        db.update_zone_template(int(update_id), zones_json=zones_json)
        db.record_template_usage(int(update_id))
        db.update_video(video_id, template_id=int(update_id))
        template_row = db.get_zone_template(int(update_id))
    else:
        if source_template_id:
            db.record_template_usage(int(source_template_id))
            db.update_video(video_id, template_id=int(source_template_id))
        else:
            db.update_video(video_id, clear_template=True)

    annotation = db.get_annotation(annotation_id)
    video = _db_video_to_ui(db.get_video(video_id))
    response = {
        "success": True,
        "message": "Annotation saved successfully.",
        "annotation": annotation,
        "video": video,
    }
    if template_row:
        response["template"] = _template_to_ui(template_row)
    return jsonify(response)


@app.route("/api/upload-video", methods=["POST"])
def api_upload_video():
    file = request.files.get("video")
    condition = request.form.get("condition", "peak")
    if condition not in CONDITIONS:
        condition = "peak"

    try:
        validate_upload(file, request.content_length)
        filename, filepath = save_video_file(file)
        file_size = 0
        if file.content_length:
            file_size = file.content_length
        else:
            try:
                file_size = os.path.getsize(filepath)
            except OSError:
                file_size = 0

        video_id = db.insert_video(
            filename=filename,
            filepath=filepath,
            recorded_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            condition=condition,
            status="uploaded",
        )

        frame_error = None
        try:
            frame_path = extract_first_frame(filepath, video_id)
            db.update_video(video_id, status="annotating")
        except FrameExtractError as exc:
            frame_path = None
            frame_error = str(exc)

        row = db.get_video(video_id)
        video = _db_video_to_ui(row)
        templates = [_template_to_ui(t) for t in db.list_zone_templates()]

        return jsonify({
            "success": True,
            "message": "Video uploaded. Configure zone annotations to continue.",
            "video": video,
            "size": format_file_size(file_size),
            "frame_url": f"/api/videos/{video_id}/frame",
            "frame_ready": frame_path is not None,
            "frame_error": frame_error,
            "templates": templates,
            "zone_types": zones_for_api(),
            "requires_annotation": True,
        })
    except UploadError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception:
        return jsonify({"success": False, "error": "Upload failed. Please try again."}), 500


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
    videos = get_videos_for_ui()
    default_video = videos[0] if videos else SEED_VIDEOS[0]
    return render_template(
        "live_monitor.html",
        videos=videos,
        default_video=default_video,
        detection_boxes=DETECTION_BOXES,
        conditions=CONDITIONS,
        zone_types=zones_for_api(),
        zone_templates=[_template_to_ui(t) for t in db.list_zone_templates()],
    )


@app.route("/violations")
def violations():
    return render_template(
        "violations.html",
        violations=MOCK_VIOLATIONS,
        violation_types=VIOLATION_TYPES,
        videos=get_videos_for_ui(),
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
        videos=get_videos_for_ui(),
        conditions=CONDITIONS,
    )


@app.route("/settings")
def settings():
    return render_template(
        "settings.html",
        users=USERS,
        settings=SYSTEM_SETTINGS,
        zone_types=zones_for_api(),
        zone_templates=[_template_to_ui(t) for t in db.list_zone_templates()],
    )


@app.route("/review-queue")
def review_queue():
    return render_template(
        "review_queue.html",
        review_items=REVIEW_QUEUE,
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
