"""TAVIDM - Traffic and Vehicle Intelligence Detection and Monitoring.

Flask application: YOLOv8m + ByteTrack detection pipeline, rule-based
violation detection, manual review queue, analytics, and reporting.
Decision-support tool only — violations are confirmed by human reviewers.
"""

from __future__ import annotations

import inspect
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
from core.annotated_writer import (
    annotated_final_path,
    download_name_for_annotated,
    mime_for_annotated_path,
)
from core.detection_config import CANONICAL_VIOLATIONS, MODEL_FAMILY
from core.violation_config import (
    ViolationConfigError,
    load_enabled_violations,
    save_enabled_violations,
    validate_enabled_violations,
    violation_catalog_for_ui,
    violation_groups_for_ui,
)
from core.detector import resolve_weights_path
from core.frame_extract import FrameExtractError, extract_first_frame, frame_path_for_video
from core.live_stream import mjpeg_generator, stream_manager
from core.media_serve import MediaPathError, resolve_under_roots, safe_media_response
from core.processing_preview import preview_hub
from core.processing_progress import ProgressTracker
from core.upload import UploadError, ensure_upload_dir, format_file_size, save_video_file, validate_upload
from core.upload_analytics import build_upload_processing_analytics
from core.video_lifecycle import VideoLifecycleError, delete_video_permanently, remove_processing_results
from core.video_processor import ProcessVideoError, ProcessVideoResult, process_video
from database.sqlite_adapter import TemporalEvidenceNotReady
from core.recording_time import RecordingTimeError, normalize_recorded_at
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
    slug = vtype.lower().replace("/", " ").replace("-", " ")
    return "-".join(part for part in slug.split() if part)


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
    job = _processing_jobs.get(db_id, {})
    latest_attempt = (
        db.get_latest_processing_run(db_id)
        if hasattr(db, "get_latest_processing_run")
        else None
    )
    current_result = None
    if hasattr(db, "get_current_completed_result_run"):
        current_result = db.get_current_completed_result_run(db_id)
    elif hasattr(db, "get_latest_completed_processing_run"):
        current_result = db.get_latest_completed_processing_run(db_id)
    annotated_ready = bool(current_result and current_result.get("annotated_video_ready"))
    result_run_id = current_result["id"] if current_result else None
    latest_run_id = latest_attempt["id"] if latest_attempt else None
    return {
        "id": f"vid-db-{db_id}",
        "db_id": db_id,
        "name": row["filename"],
        "filename": row["filename"],
        "location": f"Uploaded · {condition.title()}",
        "condition": condition,
        "duration": _format_duration(row.get("duration_sec")),
        "duration_sec": row.get("duration_sec"),
        "processed": bool(row.get("processed")),
        "status": status,
        "has_annotation": bool(row.get("annotation_id")),
        "template_id": row.get("template_id"),
        "filepath": row["filepath"],
        "created_at": row.get("created_at"),
        "recorded_at": row.get("recorded_at"),
        "file_size_bytes": row.get("file_size_bytes"),
        "uploaded_by": row.get("uploaded_by"),
        "frame_url": f"/api/videos/{db_id}/frame",
        "source_video_url": f"/api/videos/{db_id}/media",
        "processing": job.get("state") == "processing",
        "annotated_video_ready": annotated_ready,
        "annotated_video_url": (
            f"/api/videos/{db_id}/annotated/{result_run_id}"
            if annotated_ready and result_run_id
            else None
        ),
        "latest_run_id": latest_run_id,
        "current_result_run_id": result_run_id,
        "latest_attempt_status": latest_attempt.get("status") if latest_attempt else None,
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
    vid = row["id"]
    policy_rows = []
    try:
        policy_rows = db.get_case_policy_records(vid)
    except Exception:
        policy_rows = []
    plate_row = None
    try:
        plate_row = db.get_plate_verification(vid)
    except Exception:
        plate_row = None
    from core.violation_policy import (
        legal_status_for,
        proposed_official_category_for,
        verified_official_category_for,
    )

    proposed = None
    verified = None
    legal_status = None
    contributing = []
    if policy_rows:
        contributing = [p.get("canonical_rule") for p in policy_rows if p.get("canonical_rule")]
        proposed = next((p.get("official_category") for p in policy_rows if p.get("official_category")), None)
        legal_status = next((p.get("legal_status") for p in policy_rows if p.get("legal_status")), None)
        verified_rows = [
            p for p in policy_rows
            if p.get("legal_status") == "verified" and p.get("official_category")
        ]
        verified = verified_rows[0]["official_category"] if verified_rows else None
    else:
        vtype = row.get("violation_type") or ""
        proposed = proposed_official_category_for(vtype)
        verified = verified_official_category_for(vtype)
        status = legal_status_for(vtype)
        legal_status = status.value if status else None
        contributing = [vtype] if vtype else []

    flag_only = legal_status == "flag_only"
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
        "plate_text": (plate_row or {}).get("accepted_plate_text") or row.get("plate_text") or None,
        "plate_status": (plate_row or {}).get("plate_status") or row.get("plate_status") or "not_attempted",
        "reason_log": row.get("reason_log") or "",
        "frame_number": row.get("frame_number"),
        "proposed_official_category": proposed,
        "verified_official_category": verified,
        "legal_status": legal_status,
        "flag_only": flag_only,
        "contributing_behaviors": contributing,
        "case_confirmed": bool(db.is_case_confirmed(vid)),
        "notice_printed": bool(db.is_notice_printed(vid)),
        "event_time": db.get_confirmed_event_time(vid),
        "printable_as_official": bool(
            db.is_case_confirmed(vid) and not flag_only and row.get("status") != "dismissed"
        ),
        "review_material_only": bool(flag_only or legal_status in ("unverified", "partially_verified")),
    }


def _review_to_ui(row: dict, videos: dict[int, dict]) -> dict:
    from core.violation_policy import (
        legal_status_for,
        proposed_official_category_for,
        verified_official_category_for,
    )

    vtype = row.get("violation_type") or ""
    status = legal_status_for(vtype)
    legal_status = status.value if status else None
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
        "proposed_official_category": proposed_official_category_for(vtype),
        "verified_official_category": verified_official_category_for(vtype),
        "legal_status": legal_status,
        "flag_only": legal_status == "flag_only",
        "processing_run_id": row.get("processing_run_id"),
        "episode_start_sec": row.get("episode_start_sec"),
        "episode_end_sec": row.get("episode_end_sec"),
        "timestamp_ocr_available": False,
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
        user_role=(auth.current_user() or {}).get("role", "viewer"),
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
        violation_groups=violation_groups_for_ui(),
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
    user = auth.current_user()
    db.insert_video_history_event(
        video_id,
        "annotation_saved",
        actor_user_id=user["id"] if user else None,
        detail={
            "save_mode": save_mode,
            "template_id": video.get("template_id"),
            "annotation_id": annotation_id,
        },
    )
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
        recorded_at = normalize_recorded_at(request.form.get("recorded_at"))
    except RecordingTimeError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

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

        user = auth.current_user()
        video_id = db.insert_video(
            filename=filename,
            filepath=filepath,
            recorded_at=recorded_at,
            condition=condition,
            status="uploaded",
            file_size_bytes=file_size or None,
            uploaded_by=user["id"] if user else None,
        )
        try:
            db.insert_video_history_event(
                video_id,
                "uploaded",
                actor_user_id=user["id"] if user else None,
                detail={
                    "filename": filename,
                    "file_size_bytes": file_size,
                    "condition": condition,
                    "recorded_at": recorded_at,
                },
            )
        except Exception:
            logger.exception("Failed to record upload history for video %s", video_id)

        frame_error = None
        try:
            frame_path = extract_first_frame(filepath, video_id)
            db.update_video(video_id, status="annotating")
            try:
                db.insert_video_history_event(
                    video_id,
                    "reference_frame_extracted",
                    actor_user_id=user["id"] if user else None,
                    detail={"frame_ready": True},
                )
            except Exception:
                logger.exception("Failed to record frame history for video %s", video_id)
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
            "recording_time_known": recorded_at is not None,
        })
    except UploadError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:
        logger.exception("Upload failed: %s", exc)
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


def _queue_position(video_id: int) -> int | None:
    with _queue_lock:
        if _running_video_id == video_id:
            return 0
        for idx, entry in enumerate(_process_queue, start=1):
            if entry[0] == video_id:
                return idx
    return None


def _fail_processing_run(run_id: int, exc: BaseException) -> None:
    progress = getattr(exc, "progress_snapshot", None)
    try:
        db.finish_processing_run(
            run_id,
            status="failed",
            error_message=str(exc),
            diagnostics_json=getattr(exc, "diagnostics_json", None),
            geometry_snapshot_json=getattr(exc, "geometry_snapshot_json", None),
            progress=progress,
        )
    except Exception:
        logger.exception("Failed to mark processing run %d as failed", run_id)


def _reset_video_ready(video_id: int) -> None:
    try:
        db.update_video(video_id, status="ready")
    except Exception:
        logger.exception("Failed to reset video %d to ready after pipeline error", video_id)


def _call_process_video(video_id, *, enabled_violations, processing_run_id, progress_tracker=None):
    """Invoke process_video, passing optional kwargs only when supported."""
    kwargs = {"enabled_violations": enabled_violations}
    try:
        params = inspect.signature(process_video).parameters
    except (TypeError, ValueError):
        params = {}
    if "processing_run_id" in params or any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
    ):
        kwargs["processing_run_id"] = processing_run_id
    if progress_tracker is not None and (
        "progress_tracker" in params
        or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
    ):
        kwargs["progress_tracker"] = progress_tracker

    def _persist_progress(cur: int, tot: int) -> None:
        # Throttled DB write via progress_tracker snapshot when available.
        job = _processing_jobs.get(video_id)
        tracker = job.get("tracker") if job else None
        if tracker is None:
            return
        snap = tracker.snapshot()
        # Persist every ~30 frames to keep status recoverable across refresh.
        if cur == 1 or cur % 30 == 0 or (tot and cur >= tot):
            try:
                db.update_processing_run_progress(processing_run_id, snap)
            except Exception:
                logger.exception("Failed to persist progress for run %s", processing_run_id)

    if "progress_callback" in params or any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
    ):
        kwargs["progress_callback"] = _persist_progress
    return process_video(video_id, **kwargs)


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
        try:
            db.start_processing_run(run_id)
        except Exception:
            logger.exception(
                "Failed to mark processing run %d as running; "
                "inference will proceed but the run row may stay 'queued'.",
                run_id,
            )
        job = _processing_jobs.get(video_id) or {}
        tracker = job.get("tracker") or ProgressTracker(
            run_id=run_id, video_id=video_id, viewer_mode=job.get("viewer_mode") or "background"
        )
        tracker.set_queued(False, position=0)
        tracker.set_stage("loading_model")
        _processing_jobs[video_id] = {
            "state": "processing",
            "error": None,
            "run_id": run_id,
            "tracker": tracker,
            "viewer_mode": job.get("viewer_mode") or "background",
        }
        pipeline_ok = False
        result = None
        try:
            try:
                result = _call_process_video(
                    video_id,
                    enabled_violations=enabled_violations,
                    processing_run_id=run_id,
                    progress_tracker=tracker,
                )
                if not isinstance(result, ProcessVideoResult):
                    result = ProcessVideoResult(events=list(result or []))
                pipeline_ok = True
                _processing_jobs[video_id] = {
                    "state": "done",
                    "error": None,
                    "run_id": run_id,
                    "tracker": tracker,
                    "viewer_mode": job.get("viewer_mode") or "background",
                    "result": result,
                }
            except ProcessVideoError as exc:
                tracker.set_error(str(exc))
                _processing_jobs[video_id] = {
                    "state": "error",
                    "error": str(exc),
                    "run_id": run_id,
                    "tracker": tracker,
                    "viewer_mode": job.get("viewer_mode") or "background",
                }
                _fail_processing_run(run_id, exc)
                _reset_video_ready(video_id)
                db.insert_video_history_event(
                    video_id,
                    "processing_failed",
                    run_id=run_id,
                    detail={"error": str(exc)},
                )
            except Exception as exc:
                tracker.set_error(str(exc))
                _processing_jobs[video_id] = {
                    "state": "error",
                    "error": str(exc),
                    "run_id": run_id,
                    "tracker": tracker,
                    "viewer_mode": job.get("viewer_mode") or "background",
                }
                _fail_processing_run(run_id, exc)
                _reset_video_ready(video_id)
                db.insert_video_history_event(
                    video_id,
                    "processing_failed",
                    run_id=run_id,
                    detail={"error": str(exc)},
                )

            if pipeline_ok and result is not None:
                progress = result.progress_snapshot or tracker.snapshot()
                progress.update(
                    {
                        "annotated_video_path": result.annotated_video_path,
                        "annotated_video_ready": result.annotated_video_ready,
                        "detection_records": result.detection_records,
                        "unique_tracks": result.unique_tracks,
                        "class_counts": result.class_counts,
                        "violation_candidates": result.violation_candidates,
                        "model_identifier": result.model_identifier,
                        "frames_processed": result.frames_processed,
                        "total_frames": result.total_frames,
                        "source_duration_sec": result.source_duration_sec,
                        "effective_output_fps": result.effective_output_fps,
                        "stage": "completed",
                    }
                )
                try:
                    db.finish_processing_run(
                        run_id,
                        status="completed",
                        diagnostics_json=result.diagnostics_json,
                        geometry_snapshot_json=result.geometry_snapshot_json,
                        progress=progress,
                    )
                    db.insert_video_history_event(
                        video_id,
                        "processing_completed",
                        run_id=run_id,
                        detail={
                            "detection_records": result.detection_records,
                            "violation_candidates": result.violation_candidates,
                            "annotated_video_ready": result.annotated_video_ready,
                            "model_identifier": result.model_identifier,
                        },
                    )
                except Exception:
                    logger.exception(
                        "Processing run %d row write failed after a successful pipeline; "
                        "video %d stays processed.",
                        run_id,
                        video_id,
                    )
                    try:
                        db.finish_processing_run(
                            run_id,
                            status="failed",
                            error_message="Failed to write processing run row after successful pipeline.",
                            diagnostics_json=result.diagnostics_json,
                            geometry_snapshot_json=result.geometry_snapshot_json,
                            progress=progress,
                        )
                    except Exception:
                        pass
        finally:
            preview_hub.clear(video_id)
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
        queued = slot_busy or position > 1 or _running_video_id is not None
        _queue_cv.notify_all()
    _ensure_worker()
    return queued


def _try_claim_enqueue(
    video_id: int,
    *,
    enabled: tuple[str, ...],
    viewer_mode: str,
    actor_user_id: int | None,
) -> tuple[str, int | None, bool | None, int | None]:
    """Atomically check duplicates and enqueue under the queue lock.

    Returns (status, run_id, queued, position) where status is
    'accepted' or 'already_busy'.
    """
    with _queue_cv:
        busy = (
            _processing_jobs.get(video_id, {}).get("state") == "processing"
            or _running_video_id == video_id
            or any(entry[0] == video_id for entry in _process_queue)
        )
        if busy:
            return "already_busy", None, None, None

        run_id = db.create_processing_run(
            video_id,
            json.dumps(list(enabled)),
            viewer_mode=viewer_mode,
        )
        tracker = ProgressTracker(run_id=run_id, video_id=video_id, viewer_mode=viewer_mode)
        tracker.set_queued(True, position=None)
        _processing_jobs[video_id] = {
            "state": "processing",
            "error": None,
            "run_id": run_id,
            "tracker": tracker,
            "viewer_mode": viewer_mode,
        }
        _process_queue.append((video_id, enabled, run_id))
        try:
            position = [entry[0] for entry in _process_queue].index(video_id) + 1
        except ValueError:
            position = 0
        slot_busy = any(
            vid != video_id and v.get("state") == "processing"
            for vid, v in _processing_jobs.items()
        )
        queued = slot_busy or position > 1 or _running_video_id is not None
        tracker.set_queued(queued, position=position)
        _queue_cv.notify_all()

    _ensure_worker()
    db.insert_video_history_event(
        video_id,
        "processing_queued",
        run_id=run_id,
        actor_user_id=actor_user_id,
        detail={
            "viewer_mode": viewer_mode,
            "enabled_violations": list(enabled),
            "queued": queued,
            "queue_position": position,
        },
    )
    return "accepted", run_id, queued, position


def _build_status_payload(video_id: int, row: dict) -> dict:
    job = _processing_jobs.get(video_id, {})
    tracker: ProgressTracker | None = job.get("tracker")
    run_id = job.get("run_id")
    latest = None
    if run_id:
        latest = db.get_processing_run(run_id)
    if latest is None:
        latest = db.get_latest_processing_run(video_id)
        if latest:
            run_id = latest["id"]

    current_result = None
    if hasattr(db, "get_current_completed_result_run"):
        current_result = db.get_current_completed_result_run(video_id)
    elif hasattr(db, "get_latest_completed_processing_run"):
        current_result = db.get_latest_completed_processing_run(video_id)

    # Annotated replay always follows the current completed result, never a
    # failed/queued/running later attempt.
    result_for_annotated = current_result
    annotated_ready = bool(result_for_annotated and result_for_annotated.get("annotated_video_ready"))
    annotated_url = (
        f"/api/videos/{video_id}/annotated/{result_for_annotated['id']}"
        if annotated_ready and result_for_annotated
        else None
    )

    if tracker is not None:
        payload = tracker.snapshot()
        payload["annotated_video_ready"] = annotated_ready or bool(payload.get("annotated_video_ready"))
        if annotated_url:
            payload["annotated_video_url"] = annotated_url
        elif not payload.get("annotated_video_url"):
            payload["annotated_video_url"] = None
        payload["current_result_run_id"] = current_result["id"] if current_result else None
        payload["latest_attempt_status"] = latest.get("status") if latest else None
    elif latest:
        class_counts = {}
        raw_cc = latest.get("class_counts_json")
        if raw_cc:
            try:
                class_counts = json.loads(raw_cc)
            except (TypeError, json.JSONDecodeError):
                class_counts = {}
        # When the latest attempt is not the current completed result, surface
        # attempt progress/error separately from completed-result annotations.
        attempt_is_current_completed = bool(
            current_result
            and latest
            and int(current_result["id"]) == int(latest["id"])
            and latest.get("status") == "completed"
        )
        if attempt_is_current_completed:
            display_counts = class_counts
            display_det = latest.get("detection_records") or 0
            display_tracks = latest.get("unique_tracks")
            display_cands = latest.get("violation_candidates") or 0
        elif current_result and latest.get("status") in ("failed", "queued", "running"):
            display_counts = {}
            raw_crc = current_result.get("class_counts_json")
            if raw_crc:
                try:
                    display_counts = json.loads(raw_crc)
                except (TypeError, json.JSONDecodeError):
                    display_counts = {}
            display_det = current_result.get("detection_records") or 0
            display_tracks = current_result.get("unique_tracks")
            display_cands = current_result.get("violation_candidates") or 0
        else:
            display_counts = class_counts
            display_det = latest.get("detection_records") or 0
            display_tracks = latest.get("unique_tracks")
            display_cands = latest.get("violation_candidates") or 0
        payload = {
            "run_id": latest.get("id"),
            "video_id": video_id,
            "state": job.get("state") or (
                "done" if latest.get("status") == "completed"
                else "error" if latest.get("status") == "failed"
                else "processing" if latest.get("status") in ("queued", "running")
                else None
            ),
            "stage": latest.get("stage") or (
                "completed" if latest.get("status") == "completed"
                else "failed" if latest.get("status") == "failed"
                else "queued" if latest.get("status") == "queued"
                else "processing"
            ),
            "queued": latest.get("status") == "queued",
            "queue_position": None,
            "frames_processed": latest.get("frames_processed") or 0,
            "total_frames": latest.get("total_frames"),
            "progress_percent": float(latest.get("progress_percent") or 0),
            "elapsed_sec": latest.get("elapsed_sec"),
            "processing_fps": latest.get("processing_fps"),
            "eta_sec": None,
            "detection_records": display_det,
            "unique_tracks": display_tracks,
            "class_counts": display_counts,
            "violation_candidates": display_cands,
            "annotated_video_ready": annotated_ready,
            "annotated_video_url": annotated_url,
            "error": latest.get("error_message") or job.get("error"),
            "model_identifier": latest.get("model_identifier"),
            "viewer_mode": latest.get("viewer_mode"),
            "source_duration_sec": latest.get("source_duration_sec"),
            "started_at": latest.get("started_at"),
            "finished_at": latest.get("finished_at"),
            "diagnostics": [],
            "current_result_run_id": current_result["id"] if current_result else None,
            "latest_attempt_status": latest.get("status"),
        }
    else:
        payload = {
            "run_id": None,
            "video_id": video_id,
            "state": job.get("state"),
            "stage": None,
            "queued": False,
            "queue_position": None,
            "frames_processed": 0,
            "total_frames": None,
            "progress_percent": 0.0,
            "elapsed_sec": None,
            "processing_fps": None,
            "eta_sec": None,
            "detection_records": 0,
            "unique_tracks": None,
            "class_counts": {},
            "violation_candidates": 0,
            "annotated_video_ready": annotated_ready,
            "annotated_video_url": annotated_url,
            "error": job.get("error"),
            "model_identifier": None,
            "viewer_mode": None,
            "source_duration_sec": row.get("duration_sec"),
            "started_at": None,
            "finished_at": None,
            "diagnostics": [],
            "current_result_run_id": current_result["id"] if current_result else None,
            "latest_attempt_status": None,
        }

    queued = _is_processing_or_queued(video_id)
    position = _queue_position(video_id)
    payload["queued"] = queued and (position is None or position > 0) and job.get("state") != "done"
    if position is not None:
        payload["queue_position"] = position
    payload["video_status"] = row.get("status")
    payload["job_state"] = job.get("state") or payload.get("state")
    payload["job_error"] = job.get("error") or payload.get("error")
    payload["success"] = True
    if payload.get("run_id") is None and run_id is not None:
        payload["run_id"] = run_id
    return payload


def _enqueue_video_job(
    video_id: int,
    *,
    enabled: tuple[str, ...],
    viewer_mode: str = "background",
    actor_user_id: int | None = None,
) -> tuple[int, bool]:
    """Create run + queue atomically. Raises ValueError if already busy."""
    status, run_id, queued, _position = _try_claim_enqueue(
        video_id,
        enabled=enabled,
        viewer_mode=viewer_mode,
        actor_user_id=actor_user_id,
    )
    if status != "accepted" or run_id is None:
        raise ValueError("Video is already queued or processing.")
    return run_id, bool(queued)


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

    payload = request.get_json(silent=True) or {}
    requested = payload.get("enabled_violations")
    viewer_mode = (payload.get("viewer_mode") or "background").strip().lower()
    if viewer_mode not in ("background", "watch_live"):
        viewer_mode = "background"
    try:
        if requested is None:
            enabled = load_enabled_violations()
        else:
            enabled = validate_enabled_violations(requested)
    except ViolationConfigError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    user = auth.current_user()
    try:
        run_id, queued = _enqueue_video_job(
            video_id,
            enabled=enabled,
            viewer_mode=viewer_mode,
            actor_user_id=user["id"] if user else None,
        )
    except ValueError:
        queued = video_id in [e[0] for e in _process_queue]
        return jsonify({
            "success": False,
            "error": "Video is already queued for processing." if queued
            else "Video is already being processed.",
            "queued": queued,
        }), 409

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
        "viewer_mode": viewer_mode,
        "queue_position": _queue_position(video_id),
        "preview_url": f"/api/videos/{video_id}/process-preview",
    })


@app.route("/api/videos/process-bulk", methods=["POST"])
@auth.role_required("enforcer")
def api_process_bulk():
    payload = request.get_json(silent=True) or {}
    video_ids = payload.get("video_ids") or []
    if not isinstance(video_ids, list) or not video_ids:
        return jsonify({"success": False, "error": "video_ids must be a non-empty list."}), 400
    requested = payload.get("enabled_violations")
    try:
        if requested is None:
            enabled = load_enabled_violations()
        else:
            enabled = validate_enabled_violations(requested)
    except ViolationConfigError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    user = auth.current_user()
    accepted = []
    rejected = []
    already_queued = []
    for raw_id in video_ids:
        try:
            vid = int(raw_id)
        except (TypeError, ValueError):
            rejected.append({"video_id": raw_id, "reason": "Invalid video id."})
            continue
        row = db.get_video(vid)
        if row is None:
            rejected.append({"video_id": vid, "reason": "Video not found."})
            continue
        if not row.get("annotation_id"):
            rejected.append({"video_id": vid, "reason": "Missing zone annotation."})
            continue
        if row.get("status") not in ("ready", "processed"):
            rejected.append({"video_id": vid, "reason": f"Status '{row.get('status')}' is not ready."})
            continue
        status, run_id, queued, position = _try_claim_enqueue(
            vid,
            enabled=enabled,
            viewer_mode="background",
            actor_user_id=user["id"] if user else None,
        )
        if status == "already_busy":
            already_queued.append({"video_id": vid, "reason": "Already queued or processing."})
            continue
        accepted.append({
            "video_id": vid,
            "run_id": run_id,
            "queued": queued,
            "queue_position": position,
        })

    return jsonify({
        "success": True,
        "accepted": accepted,
        "rejected": rejected,
        "already_queued": already_queued,
        "enabled_violations": list(enabled),
    })


@app.route("/api/videos/<int:video_id>/process-status", methods=["GET"])
@auth.login_required
def api_process_status(video_id: int):
    row = db.get_video(video_id)
    if row is None:
        return jsonify({"success": False, "error": "Video not found."}), 404
    return jsonify(_build_status_payload(video_id, row))


@app.route("/api/videos/<int:video_id>/process-preview")
@auth.login_required
def api_process_preview(video_id: int):
    """MJPEG of the latest annotated processing frame (same run — no extra inference)."""
    row = db.get_video(video_id)
    if row is None:
        return jsonify({"success": False, "error": "Video not found."}), 404

    def _active() -> bool:
        job = _processing_jobs.get(video_id, {})
        if job.get("state") == "processing":
            return True
        # Keep streaming briefly after completion so the UI can show last frame.
        return job.get("state") in ("done", "error") and preview_hub.latest(video_id) is not None

    return Response(
        preview_hub.mjpeg_generator(video_id, is_active=_active),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/api/videos/<int:video_id>/media")
@auth.login_required
def api_video_media(video_id: int):
    row = db.get_video(video_id)
    if row is None:
        return jsonify({"success": False, "error": "Video not found."}), 404
    return safe_media_response(
        row["filepath"],
        roots=[config.UPLOAD_FOLDER],
        request=request,
        download_name=row.get("filename") or f"video_{video_id}.mp4",
        as_attachment=False,
        mimetype="video/mp4",
    )


@app.route("/api/videos/<int:video_id>/annotated/<int:run_id>")
@auth.login_required
def api_annotated_video(video_id: int, run_id: int):
    row = db.get_video(video_id)
    if row is None:
        return jsonify({"success": False, "error": "Video not found."}), 404
    run = db.get_processing_run(run_id)
    if run is None or int(run.get("video_id") or -1) != video_id:
        return jsonify({"success": False, "error": "Annotated run not found."}), 404
    if not run.get("annotated_video_ready"):
        return jsonify({"success": False, "error": "Annotated video is not ready."}), 404
    path = run.get("annotated_video_path") or str(annotated_final_path(run_id))
    as_attachment = request.args.get("download") in ("1", "true", "yes")
    mime = mime_for_annotated_path(path)
    return safe_media_response(
        path,
        roots=[config.ANNOTATED_FOLDER],
        request=request,
        download_name=download_name_for_annotated(row.get("filename"), run_id, path),
        as_attachment=as_attachment,
        mimetype=mime,
    )


@app.route("/api/videos/<int:video_id>/history")
@auth.login_required
def api_video_history(video_id: int):
    row = db.get_video(video_id)
    if row is None:
        return jsonify({"success": False, "error": "Video not found."}), 404
    annotation = db.get_annotation_by_video(video_id)
    template = db.get_zone_template(row["template_id"]) if row.get("template_id") else None
    uploader = db.get_user(row["uploaded_by"]) if row.get("uploaded_by") else None
    runs = db.list_processing_runs(video_id)
    events = db.list_video_history_events(video_id)
    summary = db.detection_summary_for_video(video_id)
    current_run = None
    if hasattr(db, "get_current_completed_result_run"):
        current_run = db.get_current_completed_result_run(video_id)
    elif hasattr(db, "get_latest_completed_processing_run"):
        current_run = db.get_latest_completed_processing_run(video_id)
    latest_attempt = (
        db.get_latest_processing_run(video_id)
        if hasattr(db, "get_latest_processing_run")
        else None
    )
    enriched_runs = []
    for run in runs:
        enabled = []
        raw = run.get("enabled_violations_json")
        if raw:
            try:
                enabled = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                enabled = []
        enriched_runs.append({
            **run,
            "enabled_violations": enabled,
            "queued_at": run.get("queued_at"),
            "started_at": run.get("started_at"),
            "finished_at": run.get("finished_at"),
            "source_duration_sec": run.get("source_duration_sec"),
            "is_current_result": bool(
                current_run and int(current_run["id"]) == int(run["id"])
            ),
            "is_latest_attempt": bool(
                latest_attempt and int(latest_attempt["id"]) == int(run["id"])
            ),
        })
    return jsonify({
        "success": True,
        "video": _db_video_to_ui(row),
        "uploader": _user_to_ui(uploader) if uploader else None,
        "recorded_at": row.get("recorded_at"),
        "recording_time_known": bool(row.get("recorded_at")),
        "source_duration_sec": row.get("duration_sec"),
        "annotation": annotation,
        "template": _template_to_ui(template) if template else None,
        "processing_runs": enriched_runs,
        "history_events": events,
        "detection_summary": summary,
        "current_result_run_id": current_run["id"] if current_run else None,
        "latest_attempt_run_id": latest_attempt["id"] if latest_attempt else None,
        "latest_attempt_status": latest_attempt.get("status") if latest_attempt else None,
    })


@app.route("/api/videos/<int:video_id>/remove-results", methods=["POST"])
@auth.role_required("enforcer")
def api_remove_results(video_id: int):
    user = auth.current_user()
    try:
        result = remove_processing_results(
            video_id,
            is_busy=_is_processing_or_queued,
            actor_user_id=user["id"] if user else None,
        )
    except VideoLifecycleError as exc:
        return jsonify({"success": False, "error": str(exc)}), exc.status_code
    _processing_jobs.pop(video_id, None)
    preview_hub.clear(video_id)
    return jsonify(result)


@app.route("/api/videos/<int:video_id>", methods=["DELETE"])
@auth.role_required("admin")
def api_delete_video(video_id: int):
    payload = request.get_json(silent=True) or {}
    confirm = payload.get("confirm_filename")
    if not isinstance(confirm, str) or not confirm.strip():
        return jsonify({
            "success": False,
            "error": "Filename confirmation is required for permanent deletion.",
        }), 400
    user = auth.current_user()
    try:
        result = delete_video_permanently(
            video_id,
            is_busy=_is_processing_or_queued,
            actor_user_id=user["id"] if user else None,
            expected_filename=confirm,
        )
    except VideoLifecycleError as exc:
        return jsonify({"success": False, "error": str(exc)}), exc.status_code
    _processing_jobs.pop(video_id, None)
    preview_hub.clear(video_id)
    return jsonify(result)


@app.route("/api/upload-processing-analytics")
@auth.login_required
def api_upload_processing_analytics():
    period = request.args.get("period", "today")
    start = request.args.get("start")
    end = request.args.get("end")
    try:
        data = build_upload_processing_analytics(period, start=start, end=end)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    return jsonify({"success": True, "analytics": data})


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
@auth.login_required
def api_confirm_review(review_id: int):
    user = auth.current_user()
    try:
        from core.case_review_service import CaseReviewError, materialize_case_from_review

        # Actor always from session; service enforces can_confirm_case.
        result = materialize_case_from_review(db, review_id, user["id"])
        return jsonify({"success": True, **result})
    except TemporalEvidenceNotReady as exc:
        return jsonify({"success": False, "error": str(exc)}), 409
    except PermissionError as exc:
        return jsonify({"success": False, "error": str(exc)}), 403
    except (CaseReviewError, ValueError) as exc:
        return jsonify({"success": False, "error": str(exc)}), 404


@app.route("/api/review-queue/<int:review_id>/dismiss", methods=["POST"])
@auth.role_required("enforcer")
def api_dismiss_review(review_id: int):
    user = auth.current_user()
    if db.get_review_item(review_id) is None:
        return jsonify({"success": False, "error": "Review item not found."}), 404
    db.dismiss_review_item(review_id, reviewed_by=user["id"])
    return jsonify({"success": True})


@app.route("/api/cases/<int:violation_id>", methods=["GET"])
@auth.login_required
def api_get_case(violation_id: int):
    videos = _video_name_map()
    row = db.get_violation(violation_id)
    if row is None:
        return jsonify({"success": False, "error": "Case not found."}), 404
    ui = _violation_to_ui(row, videos)
    ui["policy_records"] = db.get_case_policy_records(violation_id)
    ui["plate_verification"] = db.get_plate_verification(violation_id)
    ui["actions"] = db.get_case_actions(violation_id)
    from core.recurrence_policy import (
        evaluate_recurrence_eligibility,
        load_active_recurrence_policy_context,
        summarize_recurrence,
    )

    policy_ctx = load_active_recurrence_policy_context(db)
    evaluation = evaluate_recurrence_eligibility(
        db,
        violation_id=violation_id,
        lookback_days=policy_ctx["lookback_days"],
        policy_version_id=policy_ctx["policy_version_id"],
        lookback_policy_active=policy_ctx["lookback_policy_active"],
    )
    ui["recurrence"] = summarize_recurrence(evaluation)
    return jsonify({"success": True, "case": ui})


@app.route("/api/cases/<int:violation_id>/plate", methods=["POST"])
@auth.role_required("enforcer")
def api_verify_plate(violation_id: int):
    user = auth.current_user()
    payload = request.get_json(silent=True) or {}
    # Never trust client-supplied reviewer id or verification flag.
    try:
        from core.case_review_service import CaseReviewError, verify_plate_identity

        result = verify_plate_identity(
            db,
            violation_id,
            user["id"],
            plate_status=str(payload.get("plate_status") or ""),
            accepted_plate_text=payload.get("accepted_plate_text"),
            candidate_ocr_raw=payload.get("candidate_ocr_raw"),
            candidate_reference=payload.get("candidate_reference"),
            evidence_crop_ref=payload.get("evidence_crop_ref"),
            ocr_confidence=payload.get("ocr_confidence"),
            review_id=payload.get("review_id"),
        )
        return jsonify({"success": True, **result})
    except PermissionError as exc:
        return jsonify({"success": False, "error": str(exc)}), 403
    except (CaseReviewError, ValueError) as exc:
        return jsonify({"success": False, "error": str(exc)}), 400


@app.route("/api/cases/<int:violation_id>/event-time", methods=["POST"])
@auth.role_required("enforcer")
def api_confirm_event_time(violation_id: int):
    user = auth.current_user()
    payload = request.get_json(silent=True) or {}
    try:
        from core.case_review_service import CaseReviewError, persist_event_time_review

        result = persist_event_time_review(
            db,
            violation_id,
            user["id"],
            user_entry_raw=payload.get("event_time") or payload.get("user_entry_raw"),
            user_entry_timezone=payload.get("timezone"),
            correction_raw=payload.get("correction"),
            video_relative_sec=payload.get("video_relative_sec"),
        )
        return jsonify({"success": True, **result})
    except PermissionError as exc:
        return jsonify({"success": False, "error": str(exc)}), 403
    except (CaseReviewError, ValueError) as exc:
        return jsonify({"success": False, "error": str(exc)}), 400


@app.route("/api/cases/<int:violation_id>/confirm-case", methods=["POST"])
@auth.role_required("enforcer")
def api_confirm_case(violation_id: int):
    user = auth.current_user()
    try:
        ok = db.confirm_case(violation_id, user["id"])
        return jsonify({"success": True, "case_confirmed": ok})
    except PermissionError as exc:
        return jsonify({"success": False, "error": str(exc)}), 403
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400


@app.route("/api/cases/<int:violation_id>/notice-printed", methods=["POST"])
@auth.role_required("enforcer")
def api_notice_printed(violation_id: int):
    user = auth.current_user()
    payload = request.get_json(silent=True) or {}
    try:
        ok = db.confirm_notice_printed(
            violation_id,
            user["id"],
            document_reference=payload.get("document_reference"),
        )
        return jsonify({"success": True, "notice_printed": ok})
    except PermissionError as exc:
        return jsonify({"success": False, "error": str(exc)}), 403
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400


@app.route("/api/cases/notice-printed/batch", methods=["POST"])
@auth.role_required("enforcer")
def api_notice_printed_batch():
    user = auth.current_user()
    payload = request.get_json(silent=True) or {}
    membership = payload.get("violation_ids") or []
    if not isinstance(membership, list) or not membership:
        return jsonify({"success": False, "error": "violation_ids required"}), 400
    result = db.record_print_batch(
        [int(v) for v in membership],
        user["id"],
        document_reference=payload.get("document_reference"),
    )
    return jsonify({"success": result["all_succeeded"], **result})


@app.route("/api/cases/<int:violation_id>/printable", methods=["GET"])
@auth.role_required("enforcer")
def api_printable_record(violation_id: int):
    """Return a printable review/citation record. Preview does not attest printing."""
    videos = _video_name_map()
    row = db.get_violation(violation_id)
    if row is None:
        return jsonify({"success": False, "error": "Case not found."}), 404
    ui = _violation_to_ui(row, videos)
    document_kind = "official_notice_draft" if ui["printable_as_official"] else "review_material"
    return jsonify(
        {
            "success": True,
            "document_kind": document_kind,
            "notice_printed": ui["notice_printed"],
            "attestation_required": True,
            "preview_is_not_printed": True,
            "record": ui,
        }
    )


@app.route("/api/policy/versions", methods=["GET"])
@auth.login_required
def api_list_policy_versions():
    active = db.get_active_legal_policy_version()
    return jsonify(
        {
            "success": True,
            "active": active,
            "offense_suggestions_enabled": db.offense_suggestions_enabled_for_active_policy(),
        }
    )


@app.route("/api/policy/versions", methods=["POST"])
@auth.role_required("admin")
def api_propose_policy_version():
    user = auth.current_user()
    payload = request.get_json(silent=True) or {}
    version = str(payload.get("version") or "").strip()
    if not version:
        return jsonify({"success": False, "error": "version required"}), 400
    try:
        vid = db.propose_legal_policy_version(
            version=version,
            created_by=user["id"],
            lookback_days=int(payload.get("lookback_days") or 365),
            schedule_json=payload.get("schedule_json") or {},
            detail_json=payload.get("detail_json") or {
                "offense_suggestions_enabled": False,
            },
        )
        return jsonify({"success": True, "policy_version_id": vid})
    except PermissionError as exc:
        return jsonify({"success": False, "error": str(exc)}), 403
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400


@app.route("/api/policy/versions/<int:version_id>/approve", methods=["POST"])
@auth.login_required
def api_approve_policy_version(version_id: int):
    user = auth.current_user()
    try:
        db.approve_legal_policy_version(version_id, approved_by=user["id"])
        return jsonify({"success": True})
    except PermissionError as exc:
        return jsonify({"success": False, "error": str(exc)}), 403
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400


@app.route("/api/policy/versions/<int:version_id>/reject", methods=["POST"])
@auth.login_required
def api_reject_policy_version(version_id: int):
    user = auth.current_user()
    payload = request.get_json(silent=True) or {}
    try:
        # Rejection uses propose authority or approve grant; enforce active user.
        if not (db.can_propose_policy(user["id"]) or db.can_approve_policy(user["id"])):
            raise PermissionError("Lacks policy rejection authority")
        db.reject_legal_policy_version(
            version_id, user["id"], reason=payload.get("reason")
        )
        return jsonify({"success": True})
    except PermissionError as exc:
        return jsonify({"success": False, "error": str(exc)}), 403
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400


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
            "violation_groups": violation_groups_for_ui(),
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
