import numpy as np

from core.frame_annotate import TrajectoryOverlay, annotate_frame


def _vehicle(x, *, epoch=1, track_id=7):
    return {
        "bbox_x": x,
        "bbox_y": 10,
        "bbox_w": 10,
        "bbox_h": 10,
        "class_label": "car",
        "confidence": 0.9,
        "track_id": track_id,
        "track_identity_epoch": epoch,
    }


def test_reused_tracker_id_starts_a_new_visual_identity_epoch():
    frame = np.zeros((60, 100, 3), dtype=np.uint8)
    overlay = TrajectoryOverlay(trace_length=5)

    annotate_frame(frame, [_vehicle(10)], trajectory_overlay=overlay)
    same_identity = annotate_frame(
        frame, [_vehicle(30)], trajectory_overlay=overlay
    )
    assert np.any(same_identity[20, 25])

    new_identity = annotate_frame(
        frame, [_vehicle(70, epoch=2)], trajectory_overlay=overlay
    )
    assert not np.any(new_identity[20, 25])


def test_missing_or_invalid_track_metadata_does_not_draw_a_path():
    frame = np.zeros((60, 100, 3), dtype=np.uint8)
    overlay = TrajectoryOverlay(trace_length=5)
    detections = [
        {"bbox_x": 10, "bbox_y": 10, "bbox_w": 10, "bbox_h": 10},
        {"bbox_x": 20, "bbox_y": 10, "bbox_w": float("nan"), "bbox_h": 10, "track_id": 8},
    ]
    output = overlay.annotate(
        frame,
        detections,
    )
    assert np.array_equal(output, frame)


def test_live_monitor_draws_recent_track_path():
    from core.live_stream import LiveStreamWorker

    worker = LiveStreamWorker({"id": 31, "name": "Gate", "rtsp_url": "mock://camera"})
    frame = np.zeros((60, 100, 3), dtype=np.uint8)
    worker._annotate(frame, [_vehicle(10)])
    output = worker._annotate(frame, [_vehicle(30)])
    assert np.any(output[20, 25])
