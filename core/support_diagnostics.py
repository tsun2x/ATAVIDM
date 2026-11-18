"""Small, privacy-conscious support export; no request data or free-text logs."""
from collections import deque
from datetime import datetime, timezone
from threading import Lock

_events = deque(maxlen=500)
_lock = Lock()

# Endpoint names are code-defined, never user-supplied paths or query strings.
_TRACKED = frozenset({
    "api_upload_video", "api_save_annotation", "api_process_video",
    "api_process_bulk", "api_cancel_processing",
    "api_confirm_review", "api_review_decision", "api_confirm_case",
    "api_camera_stream", "api_camera_stream_status", "api_camera_stop",
    "reports", "api_report_video_preview", "pickup_detail_review",
    "api_pickup_detail_list", "motorcycle_detail_review",
    "api_motorcycle_detail_list", "api_motorcycle_detail_scan_now",
})


def record_request(endpoint, status, view_args=None):
    if endpoint not in _TRACKED:
        return
    event = {"time_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "action": endpoint, "http_status": int(status)}
    video_id = (view_args or {}).get("video_id")
    if isinstance(video_id, int) and not isinstance(video_id, bool) and video_id > 0:
        event["video_id"] = video_id
    review_id = (view_args or {}).get("review_id")
    if isinstance(review_id, int) and not isinstance(review_id, bool) and review_id > 0:
        event["review_id"] = review_id
    with _lock:
        _events.append(event)


def recent_activity():
    with _lock:
        return list(_events)
