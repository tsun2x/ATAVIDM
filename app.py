"""TAVIDM - Traffic and Vehicle Intelligence Detection and Monitoring.

Flask application: YOLOv8m + ByteTrack detection pipeline, rule-based
violation detection, manual review queue, analytics, and reporting.
Decision-support tool only — violations are confirmed by human reviewers.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path

from flask import (
    Flask,
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    url_for,
)

logger = logging.getLogger(__name__)

import config
from core import analytics as analytics_core
from core import auth
from core import reports as reports_core
from core.detection_config import CANONICAL_VIOLATIONS, MODEL_FAMILY
from core.violation_config import (
    ViolationConfigError,
    load_enabled_violations,
    save_enabled_violations,
    validate_enabled_violations,
    violation_catalog_for_ui,
)
from core.detector import resolve_weights_path
from core.frame_extract import FrameExtractError, extract_first_frame, frame_path_for_video
from core.live_stream import mjpeg_generator, stream_manager
from core.upload import UploadError, ensure_upload_dir, format_file_size, save_video_file, validate_upload
from core.video_processor import process_video
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
auth.ensure_default_admin()
# Reset any video left in 'processing' by a previous unclean shutdown so the
# sequential queue can pick it up cleanly on the next process request.
try:
    db.recover_orphaned_processing()
except Exception:  # pragma: no cover - defensive; never block startup
    pass

CONDITIONS = ["morning", "peak", "nighttime"]
STATUSES = ["confirmed", "dismissed", "pending"]

# video_id -> {"state": "processing"|"done"|"error", "error": str|None}
_processing_jobs: dict[int, dict] = {}

# Sequential processing queue: at most ONE process_video thread at a time
# (RTX 3050 6GB — a single YOLOv8m + ByteTrack inference job). Each queue
# entry carries the validated violation snapshot + its processing_runs id so a
# run uses the exact list submitted at enqueue time.
# Entry shape: (video_id, enabled_violations_tuple, run_id)
_process_queue: list[tuple[int, tuple[str, ...], int]] = []
_queue_lock = threading.Lock()
_queue_cv = threading.Condition(_queue_lock)
# id of the video currently being processed by the single worker (None if idle).
# Distinct from "in _process_queue": the worker pops before running, so a
# waiting job can be the only queue item while another video occupies the slot.
_running_video_id: int | None = None
_worker_shutdown = threading.Event()
_worker_thread: threading.Thread | None = None
_worker_started = False
_worker_start_lock = threading.Lock()


# ---------------------------------------------------------------------------
# UI mapping helpers
# ---------------------------------------------------------------------------

def _vtype_slug(vtype: str) -> str:
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
        "has_annotation": bool(row.get("annotation_id")),
        "template_id": row.get("template_id"),
        "filepath": row["filepath"],
        "created_at": row.get("created_at"),
        "file_size_bytes": row.get("file_size_bytes"),
        "frame_url": f"/api/videos/{db_id}/frame",
        "processing": _processing_jobs.get(db_id, {}).get("state") == "processing",
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


def _video_name_map() -> dict[int, dict]:
    return {row["id"]: row for row in db.list_videos()}


def _source_label(video_id: int | None, videos: dict[int, dict]) -> str:
    if video_id is None:
        return "Live Camera"
    video = videos.get(video_id)
    return video["filename"] if video else f"Video #{video_id}"


def _evidence_url(evidence_path: str | None) -> str | None:
    if not evidence_path:
        return None
    return "/" + evidence_path.lstrip("/")


def _violation_to_ui(row: dict, videos: dict[int, dict]) -> dict:
    video = videos.get(row.get("video_id"))
    detected = row.get("detected_at") or ""
    return {
        "id": f"VIO-{row['id']:06d}",
        "db_id": row["id"],
        "type": row["violation_type"],
        "type_slug": _vtype_slug(row["violation_type"]),
        "video_id": row.get("video_id"),
        "video_name": _source_label(row.get("video_id"), videos),
        "track_id": row.get("track_id"),
        "timestamp": detected,
        "timestamp_iso": detected.replace(" ", "T"),
        "confidence": row.get("confidence") or 0,
        "status": row.get("status"),
        "condition": (video or {}).get("condition") or "—",
        "vehicle_class": row.get("vehicle_class") or "—",
        "evidence_url": _evidence_url(row.get("evidence_path")),
        "vehicle_evidence_url": _evidence_url(row.get("vehicle_evidence_path")),
        "plate_text": row.get("plate_text") or None,
        "plate_status": row.get("plate_status") or "not_attempted",
        "reason_log": row.get("reason_log") or "",
        "frame_number": row.get("frame_number"),
    }


def _review_to_ui(row: dict, videos: dict[int, dict]) -> dict:
    return {
        "id": row["id"],
        "display_id": f"RQ-{row['id']:05d}",
        "video_id": row.get("video_id"),
        "video_name": _source_label(row.get("video_id"), videos),
        "track_id": row.get("track_id"),
        "violation_type": row.get("violation_type"),
        "type_slug": _vtype_slug(row.get("violation_type") or ""),
        "confidence": row.get("confidence") or 0,
        "frame_number": row.get("frame_number"),
        "vehicle_class": row.get("vehicle_class") or "—",
        "evidence_url": _evidence_url(row.get("evidence_path")),
        "vehicle_evidence_url": _evidence_url(row.get("vehicle_evidence_path")),
        "plate_text": row.get("plate_text") or None,
        "plate_status": row.get("plate_status") or "not_attempted",
        "reason_log": row.get("reason_log") or "",
        "queued_at": row.get("queued_at"),
        "status": row.get("status"),
    }


def _report_to_ui(row: dict) -> dict:
    filters = {}
    try:
        filters = json.loads(row.get("filters_json") or "{}")
    except json.JSONDecodeError:
        pass
    filter_parts = [str(v) for v in filters.values() if v]
    return {
        "id": row["id"],
        "name": row["title"],
        "type": "PDF" if row["report_format"] == "pdf" else "Excel",
        "date": row.get("generated_at"),
        "filters": " · ".join(filter_parts) if filter_parts else "All violations",
        "generated_by": row.get("generated_by_username") or "—",
        "download_url": f"/api/reports/{row['id']}/download",
    }


def _user_to_ui(row: dict) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "name": row.get("full_name") or row["username"],
        "role": row["role"],
        "role_label": auth.ROLE_LABELS.get(row["role"], row["role"]),
        "is_active": bool(row.get("is_active", 1)),
        "created_at": row.get("created_at"),
    }


def _camera_to_ui(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "location": row.get("location") or "",
        "rtsp_url": row["rtsp_url"],
        "zones_json": row.get("zones_json") or "{}",
        "is_active": bool(row.get("is_active", 1)),
        "created_at": row.get("created_at"),
        "stream_url": f"/api/cameras/{row['id']}/stream",
        "stream_status": stream_manager.status(row["id"]),
    }


def _system_status() -> dict:
    weights, is_custom = resolve_weights_path()
    videos = db.list_videos()
    last_processed = next((v["filename"] for v in videos if v.get("processed")), "—")
    return {
        "pipeline": "Ready",
        "model": f"{MODEL_FAMILY} ({'custom' if is_custom else 'COCO pretrained'})",
        "tracker": "ByteTrack",
        "db": db.active_backend().upper(),
        "last_processed": last_processed,
    }


# ---------------------------------------------------------------------------
# Context + error handlers
# ---------------------------------------------------------------------------

@app.context_processor
def inject_globals():
    user = auth.current_user()
    role = user["role"] if user else None
    nav_items = [
        {"endpoint": "dashboard", "label": "Dashboard", "icon": "bi-speedometer2"},
        {"endpoint": "live_monitor", "label": "Live Monitor", "icon": "bi-camera-video", "badge": "live"},
        {"endpoint": "violations", "label": "Violations", "icon": "bi-exclamation-triangle"},
        {"endpoint": "analytics", "label": "Analytics", "icon": "bi-bar-chart-line"},
    ]
    if role in ("admin", "enforcer"):
        nav_items.append({"endpoint": "reports", "label": "Reports", "icon": "bi-file-earmark-text"})
    if role == "admin":
        nav_items.append({"endpoint": "settings", "label": "Settings", "icon": "bi-gear"})
    return {
        "app_name": "TAVIDM",
        "app_full_name": "Traffic and Vehicle Intelligence Detection and Monitoring",
        "current_year": datetime.now().year,
        "nav_items": nav_items,
        "current_user": user,
        "review_queue_count": db.count_review_pending() if user else 0,
        "max_upload_mb": config.MAX_UPLOAD_MB,
    }


@app.errorhandler(413)
def request_entity_too_large(_error):
    return jsonify({
        "success": False,
        "error": f"File exceeds maximum upload size of {config.MAX_UPLOAD_MB} MB.",
    }), 413


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        user = auth.authenticate(username, password)
        if user is None:
            flash("Invalid username or password.", "danger")
            return render_template("login.html"), 401
        auth.login_user(user)
        next_url = request.args.get("next") or url_for("dashboard")
        return redirect(next_url)
    if auth.current_user():
        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    auth.logout_user()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.route("/")
@auth.login_required
def dashboard():
    stats = analytics_core.dashboard_stats()
    summary = analytics_core.violation_summary()
    hourly_labels, hourly_values = analytics_core.hourly_chart_today()
    videos = _video_name_map()
    recent_rows, _total = db.list_violations(page=1, per_page=8, sort="newest")
    chart_data = {
        "hourly_labels": hourly_labels,
        "hourly_values": hourly_values,
        "distribution_labels": [s["type"] for s in summary],
        "distribution_values": [s["count"] for s in summary],
    }
    return render_template(
        "dashboard.html",
        stats=stats,
        recent_violations=[_violation_to_ui(r, videos) for r in recent_rows],
        violation_summary=summary,
        chart_data=chart_data,
        system_status=_system_status(),
    )


@app.route("/live-monitor")
@auth.login_required
def live_monitor():
    videos = [_db_video_to_ui(row) for row in db.list_videos()]
    cameras = [_camera_to_ui(row) for row in db.list_cameras()]
    return render_template(
        "live_monitor.html",
        videos=videos,
        cameras=cameras,
        conditions=CONDITIONS,
        zone_types=zones_for_api(),
        zone_templates=[_template_to_ui(t) for t in db.list_zone_templates()],
        violation_catalog=violation_catalog_for_ui(),
        enabled_violations=list(load_enabled_violations()),
        max_upload_mb=config.MAX_UPLOAD_MB,
    )


@app.route("/violations")
@auth.login_required
def violations():
    videos = _video_name_map()
    rows, _total = db.list_violations(page=1, per_page=1000, sort="newest")
    return render_template(
        "violations.html",
        violations=[_violation_to_ui(r, videos) for r in rows],
        violation_types=list(CANONICAL_VIOLATIONS),
        videos=[_db_video_to_ui(row) for row in db.list_videos()],
        statuses=STATUSES,
    )


@app.route("/analytics")
@auth.login_required
def analytics():
    return render_template(
        "analytics.html",
        analytics=analytics_core.analytics_data(),
        violation_summary=analytics_core.violation_summary(),
    )


@app.route("/reports")
@auth.role_required("enforcer")
def reports():
    return render_template(
        "reports.html",
        report_history=[_report_to_ui(r) for r in db.list_reports()],
        violation_types=list(CANONICAL_VIOLATIONS),
        videos=[_db_video_to_ui(row) for row in db.list_videos()],
        statuses=STATUSES,
    )


@app.route("/settings")
@auth.role_required("admin")
def settings():
    from core.video_processor import load_rule_parameters

    return render_template(
        "settings.html",
        users=[_user_to_ui(u) for u in db.list_users()],
        settings=load_rule_parameters(),
        violation_catalog=violation_catalog_for_ui(),
        enabled_violations=list(load_enabled_violations()),
        cameras=[_camera_to_ui(c) for c in db.list_cameras()],
        roles=auth.ROLE_LABELS,
        zone_types=zones_for_api(),
        zone_templates=[_template_to_ui(t) for t in db.list_zone_templates()],
    )


@app.route("/review-queue")
@auth.role_required("enforcer")
def review_queue():
    videos = _video_name_map()
    rows, _total = db.list_review_queue(status="pending", page=1, per_page=200)
    return render_template(
        "review_queue.html",
        review_items=[_review_to_ui(r, videos) for r in rows],
    )


# ---------------------------------------------------------------------------
# Video + annotation APIs
# ---------------------------------------------------------------------------

@app.route("/api/videos", methods=["GET"])
@auth.login_required
def api_list_videos():
    return jsonify({"success": True, "videos": [_db_video_to_ui(row) for row in db.list_videos()]})


@app.route("/api/zone-types", methods=["GET"])
@auth.login_required
def api_zone_types():
    return jsonify({"success": True, "zone_types": zones_for_api()})


@app.route("/api/zone-templates", methods=["GET"])
@auth.login_required
def api_list_zone_templates():
    templates = [_template_to_ui(row) for row in db.list_zone_templates()]
    return jsonify({"success": True, "templates": templates})


@app.route("/api/zone-templates", methods=["POST"])
@auth.role_required("enforcer")
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
@auth.login_required
def api_get_zone_template(template_id: int):
    row = db.get_zone_template(template_id)
    if row is None:
        return jsonify({"success": False, "error": "Template not found."}), 404
    return jsonify({"success": True, "template": _template_to_ui(row)})


@app.route("/api/zone-templates/<int:template_id>", methods=["PUT"])
@auth.role_required("enforcer")
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
@auth.role_required("enforcer")
def api_delete_zone_template(template_id: int):
    row = db.get_zone_template(template_id)
    if row is None:
        return jsonify({"success": False, "error": "Template not found."}), 404
    db.delete_zone_template(template_id)
    return jsonify({"success": True})


@app.route("/api/zone-templates/<int:template_id>/duplicate", methods=["POST"])
@auth.role_required("enforcer")
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
@auth.login_required
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
@auth.login_required
def api_get_annotation(video_id: int):
    row = db.get_video(video_id)
    if row is None:
        return jsonify({"success": False, "error": "Video not found."}), 404
    annotation = db.get_annotation_by_video(video_id)
    if annotation is None:
        return jsonify({"success": True, "annotation": None})
    return jsonify({"success": True, "annotation": annotation})


@app.route("/api/videos/<int:video_id>/annotation", methods=["POST", "PUT"])
@auth.role_required("enforcer")
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
            "error": "Draw at least one zone; each drawn zone needs at least 3 points.",
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
@auth.role_required("enforcer")
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
            file_size_bytes=file_size or None,
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


# ---------------------------------------------------------------------------
# Detection pipeline APIs
# ---------------------------------------------------------------------------

def _is_processing_or_queued(video_id: int) -> bool:
    """True if this video is currently running or waiting in the queue."""
    if _processing_jobs.get(video_id, {}).get("state") == "processing":
        return True
    with _queue_lock:
        if _running_video_id == video_id:
            return True
        return any(entry[0] == video_id for entry in _process_queue)


def _processing_worker() -> None:
    """Single worker: drain the queue one job at a time.

    Exactly one inference job (YOLOv8m + ByteTrack) runs at any moment
    because this is the only thread that ever calls ``process_video``. The
    worker stays alive across jobs; tests may stop it via
    ``stop_processing_worker()`` so SQLite file locks are released.
    """
    global _running_video_id
    while not _worker_shutdown.is_set():
        with _queue_cv:
            while not _process_queue and not _worker_shutdown.is_set():
                _queue_cv.wait(timeout=0.5)
            if _worker_shutdown.is_set():
                _running_video_id = None
                return
            if not _process_queue:
                continue
            video_id, enabled_violations, run_id = _process_queue.pop(0)
            _running_video_id = video_id
        # Transition this run from 'queued' to 'running' now that it occupies
        # the single inference slot (status persists into processing_runs).
        try:
            db.start_processing_run(run_id)
        except Exception:
            logger.exception(
                "Failed to mark processing run %d as running; "
                "inference will proceed but the run row may stay 'queued'.",
                run_id,
            )
        _processing_jobs[video_id] = {"state": "processing", "error": None}
        try:
            process_video(video_id, enabled_violations=enabled_violations)
            _processing_jobs[video_id] = {"state": "done", "error": None}
            db.finish_processing_run(run_id, status="completed")
        except Exception as exc:  # surface pipeline failures to the UI
            _processing_jobs[video_id] = {"state": "error", "error": str(exc)}
            try:
                db.finish_processing_run(run_id, status="failed", error_message=str(exc))
            except Exception:
                pass
            try:
                db.update_video(video_id, status="ready")
            except Exception:
                pass
        finally:
            with _queue_cv:
                if _running_video_id == video_id:
                    _running_video_id = None


def _ensure_worker() -> None:
    """Start the sequential worker if it is not already alive."""
    global _worker_started, _worker_thread
    with _worker_start_lock:
        if _worker_thread is not None and _worker_thread.is_alive():
            return
        _worker_shutdown.clear()
        _worker_thread = threading.Thread(
            target=_processing_worker,
            daemon=True,
            name="tavidm-process-worker",
        )
        _worker_thread.start()
        _worker_started = True


def stop_processing_worker(timeout: float = 3.0) -> None:
    """Stop the background worker and join it.

    Production never needs this. Tests call it so the worker releases any
    SQLite handle before the temporary database directory is deleted.
    """
    global _worker_started, _worker_thread, _running_video_id
    _worker_shutdown.set()
    with _queue_cv:
        _queue_cv.notify_all()
    thread = _worker_thread
    if thread is not None and thread.is_alive() and threading.current_thread() is not thread:
        thread.join(timeout=timeout)
    with _worker_start_lock:
        _worker_started = False
        _worker_thread = None
    _running_video_id = None


def _enqueue_processing(video_id: int, enabled_violations: tuple[str, ...], run_id: int) -> bool:
    """Append a job. Returns True if it must wait behind another video."""
    with _queue_cv:
        _process_queue.append((video_id, enabled_violations, run_id))
        try:
            position = [entry[0] for entry in _process_queue].index(video_id) + 1
        except ValueError:
            position = 0
        slot_busy = any(
            vid != video_id and v.get("state") == "processing"
            for vid, v in _processing_jobs.items()
        )
        queued = slot_busy or position > 1
        _queue_cv.notify_all()
    _ensure_worker()
    return queued


@app.route("/api/videos/<int:video_id>/process", methods=["POST"])
@auth.role_required("enforcer")
def api_process_video(video_id: int):
    row = db.get_video(video_id)
    if row is None:
        return jsonify({"success": False, "error": "Video not found."}), 404
    if _is_processing_or_queued(video_id):
        queued = video_id in [e[0] for e in _process_queue]
        return jsonify({
            "success": False,
            "error": "Video is already queued for processing." if queued
            else "Video is already being processed.",
            "queued": queued,
        }), 409
    if not row.get("annotation_id"):
        return jsonify({
            "success": False,
            "error": "Annotate traffic zones before processing this video.",
        }), 400

    # Accept an explicit violation list for this run; default to the current
    # global enabled set. Validate against the canonical/toggleable registry.
    payload = request.get_json(silent=True) or {}
    requested = payload.get("enabled_violations")
    try:
        if requested is None:
            enabled = load_enabled_violations()
        else:
            enabled = validate_enabled_violations(requested)
    except ViolationConfigError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    import json as _json
    run_id = db.create_processing_run(video_id, _json.dumps(list(enabled)))

    _processing_jobs[video_id] = {"state": "processing", "error": None}
    queued = _enqueue_processing(video_id, enabled, run_id)

    if queued:
        message = "Processing queued. Runs sequentially after the current job."
    else:
        message = "Processing started."
    return jsonify({
        "success": True,
        "message": message,
        "queued": queued,
        "enabled_violations": list(enabled),
        "run_id": run_id,
    })


@app.route("/api/videos/<int:video_id>/process-status", methods=["GET"])
@auth.login_required
def api_process_status(video_id: int):
    row = db.get_video(video_id)
    if row is None:
        return jsonify({"success": False, "error": "Video not found."}), 404
    job = _processing_jobs.get(video_id, {})
    return jsonify({
        "success": True,
        "video_status": row.get("status"),
        "job_state": job.get("state"),
        "job_error": job.get("error"),
        "queued": _is_processing_or_queued(video_id),
    })


# ---------------------------------------------------------------------------
# Review queue APIs
# ---------------------------------------------------------------------------

@app.route("/api/review-queue", methods=["GET"])
@auth.login_required
def api_review_queue():
    status = request.args.get("status", "pending")
    videos = _video_name_map()
    rows, total = db.list_review_queue(status=status, page=1, per_page=200)
    return jsonify({
        "success": True,
        "items": [_review_to_ui(r, videos) for r in rows],
        "total": total,
    })


@app.route("/api/review-queue/<int:review_id>/confirm", methods=["POST"])
@auth.role_required("enforcer")
def api_confirm_review(review_id: int):
    user = auth.current_user()
    try:
        violation_id = db.confirm_review_item(review_id, reviewed_by=user["id"])
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 404
    return jsonify({"success": True, "violation_id": violation_id})


@app.route("/api/review-queue/<int:review_id>/dismiss", methods=["POST"])
@auth.role_required("enforcer")
def api_dismiss_review(review_id: int):
    user = auth.current_user()
    if db.get_review_item(review_id) is None:
        return jsonify({"success": False, "error": "Review item not found."}), 404
    db.dismiss_review_item(review_id, reviewed_by=user["id"])
    return jsonify({"success": True})


# ---------------------------------------------------------------------------
# Report APIs
# ---------------------------------------------------------------------------

@app.route("/api/reports", methods=["GET"])
@auth.role_required("enforcer")
def api_list_reports():
    return jsonify({"success": True, "reports": [_report_to_ui(r) for r in db.list_reports()]})


@app.route("/api/reports", methods=["POST"])
@auth.role_required("enforcer")
def api_generate_report():
    payload = request.get_json(silent=True) or {}
    report_format = (payload.get("format") or "pdf").lower()
    filters = {
        key: payload.get(key)
        for key in ("date_from", "date_to", "violation_type", "status", "video_id")
        if payload.get(key)
    }
    user = auth.current_user()
    try:
        row = reports_core.generate_report(
            report_format=report_format,
            filters=filters,
            title=(payload.get("title") or "").strip() or None,
            generated_by=user["id"],
        )
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    return jsonify({"success": True, "report": _report_to_ui(row)})


@app.route("/api/reports/<int:report_id>/download", methods=["GET"])
@auth.role_required("enforcer")
def api_download_report(report_id: int):
    row = db.get_report(report_id)
    if row is None:
        return jsonify({"success": False, "error": "Report not found."}), 404
    file_path = Path(config.BASE_DIR) / row["file_path"]
    if not file_path.is_file():
        return jsonify({"success": False, "error": "Report file is missing."}), 404
    return send_file(file_path, as_attachment=True)


# ---------------------------------------------------------------------------
# Settings + user management APIs (admin)
# ---------------------------------------------------------------------------

@app.route("/api/settings", methods=["GET"])
@auth.role_required("admin")
def api_get_settings():
    from core.video_processor import load_rule_parameters

    return jsonify(
        {
            "success": True,
            "settings": load_rule_parameters(),
            "enabled_violations": list(load_enabled_violations()),
            "violation_catalog": violation_catalog_for_ui(),
        }
    )


@app.route("/api/settings", methods=["POST"])
@auth.role_required("admin")
def api_save_settings():
    from core.detection_config import DEFAULT_RULE_PARAMETERS

    payload = request.get_json(silent=True) or {}
    values = {key: payload[key] for key in DEFAULT_RULE_PARAMETERS if key in payload}
    if "enabled_violations" in payload:
        raw = payload.get("enabled_violations")
        if not isinstance(raw, list):
            return jsonify({"success": False, "error": "enabled_violations must be a list."}), 400
        try:
            enabled = save_enabled_violations(raw)
        except ViolationConfigError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
    else:
        enabled = load_enabled_violations()

    if not values and "enabled_violations" not in payload:
        return jsonify({"success": False, "error": "No recognized settings provided."}), 400
    if values:
        db.set_settings(values)
    return jsonify(
        {
            "success": True,
            "message": "Settings saved.",
            "enabled_violations": list(enabled),
        }
    )


@app.route("/api/users", methods=["GET"])
@auth.role_required("admin")
def api_list_users():
    return jsonify({"success": True, "users": [_user_to_ui(u) for u in db.list_users()]})


@app.route("/api/users", methods=["POST"])
@auth.role_required("admin")
def api_create_user():
    payload = request.get_json(silent=True) or {}
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    role = payload.get("role") or "enforcer"
    if not username or not password:
        return jsonify({"success": False, "error": "Username and password are required."}), 400
    if role not in auth.ROLES:
        return jsonify({"success": False, "error": f"Role must be one of: {', '.join(auth.ROLES)}."}), 400
    if db.get_user_by_username(username):
        return jsonify({"success": False, "error": "Username already exists."}), 400
    user_id = db.create_user(
        username,
        auth.hash_password(password),
        role=role,
        full_name=(payload.get("full_name") or "").strip() or None,
    )
    return jsonify({"success": True, "user": _user_to_ui(db.get_user(user_id))})


@app.route("/api/users/<int:user_id>", methods=["PUT"])
@auth.role_required("admin")
def api_update_user(user_id: int):
    if db.get_user(user_id) is None:
        return jsonify({"success": False, "error": "User not found."}), 404
    payload = request.get_json(silent=True) or {}
    role = payload.get("role")
    if role is not None and role not in auth.ROLES:
        return jsonify({"success": False, "error": f"Role must be one of: {', '.join(auth.ROLES)}."}), 400
    password = payload.get("password")
    db.update_user(
        user_id,
        full_name=payload.get("full_name"),
        role=role,
        password_hash=auth.hash_password(password) if password else None,
        is_active=payload.get("is_active"),
    )
    return jsonify({"success": True, "user": _user_to_ui(db.get_user(user_id))})


# ---------------------------------------------------------------------------
# Camera (RTSP live stream) APIs
# ---------------------------------------------------------------------------

@app.route("/api/cameras", methods=["GET"])
@auth.login_required
def api_list_cameras():
    return jsonify({"success": True, "cameras": [_camera_to_ui(c) for c in db.list_cameras()]})


@app.route("/api/cameras", methods=["POST"])
@auth.role_required("admin")
def api_create_camera():
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    rtsp_url = (payload.get("rtsp_url") or "").strip()
    if not name or not rtsp_url:
        return jsonify({"success": False, "error": "Camera name and RTSP URL are required."}), 400
    try:
        zones_json = dumps_zones(payload.get("zones_json") or {})
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    camera_id = db.create_camera(
        name=name,
        rtsp_url=rtsp_url,
        location=(payload.get("location") or "").strip() or None,
        zones_json=zones_json,
    )
    return jsonify({"success": True, "camera": _camera_to_ui(db.get_camera(camera_id))})


@app.route("/api/cameras/<int:camera_id>", methods=["PUT"])
@auth.role_required("admin")
def api_update_camera(camera_id: int):
    if db.get_camera(camera_id) is None:
        return jsonify({"success": False, "error": "Camera not found."}), 404
    payload = request.get_json(silent=True) or {}
    zones_json = None
    if "zones_json" in payload:
        try:
            zones_json = dumps_zones(payload.get("zones_json") or {})
        except ValueError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
    db.update_camera(
        camera_id,
        name=payload.get("name"),
        location=payload.get("location"),
        rtsp_url=payload.get("rtsp_url"),
        zones_json=zones_json,
        is_active=payload.get("is_active"),
    )
    stream_manager.stop(camera_id)  # restart picks up the new configuration
    return jsonify({"success": True, "camera": _camera_to_ui(db.get_camera(camera_id))})


@app.route("/api/cameras/<int:camera_id>", methods=["DELETE"])
@auth.role_required("admin")
def api_delete_camera(camera_id: int):
    if db.get_camera(camera_id) is None:
        return jsonify({"success": False, "error": "Camera not found."}), 404
    stream_manager.stop(camera_id)
    db.delete_camera(camera_id)
    return jsonify({"success": True})


@app.route("/api/cameras/<int:camera_id>/stream")
@auth.login_required
def api_camera_stream(camera_id: int):
    try:
        worker = stream_manager.start(camera_id)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 404
    return Response(
        mjpeg_generator(worker),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/api/cameras/<int:camera_id>/stream-status")
@auth.login_required
def api_camera_stream_status(camera_id: int):
    return jsonify({"success": True, **stream_manager.status(camera_id)})


@app.route("/api/cameras/<int:camera_id>/stop", methods=["POST"])
@auth.role_required("enforcer")
def api_camera_stop(camera_id: int):
    stream_manager.stop(camera_id)
    return jsonify({"success": True})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
