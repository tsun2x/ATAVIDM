from types import SimpleNamespace

from core.vehicle_crossing_counter import VehicleCrossingCounter


def _line():
    return SimpleNamespace(id="count-1", type="counting_line", points=((50.0, 0.0), (50.0, 100.0)), metadata={})


def _det(track_id, x, label="jeepney"):
    return {"track_id": track_id, "class_label": label, "bbox_x": x - 5, "bbox_y": 20, "bbox_w": 10, "bbox_h": 20}


def test_counts_one_vehicle_once_after_complete_line_transition():
    counter = VehicleCrossingCounter([_line()], hysteresis_px=2)
    for x in (35, 42, 49, 51, 58, 65):
        counter.update([_det(7, x)])
    assert counter.total == 1
    assert counter.by_class == {"jeepney": 1}


def test_jitter_near_line_and_new_track_on_destination_side_do_not_count():
    counter = VehicleCrossingCounter([_line()], hysteresis_px=3)
    for x in (48, 51, 49, 52, 48):
        counter.update([_det(7, x)])
    counter.update([_det(8, 70)])
    assert counter.total == 0


def test_no_counting_line_reports_unavailable():
    counter = VehicleCrossingCounter([])
    counter.update([_det(1, 40), _det(1, 60)])
    assert counter.available is False
    assert counter.total is None
    assert counter.by_class == {}


def test_crossing_infinite_extension_outside_segment_does_not_count():
    counter = VehicleCrossingCounter([_line()], hysteresis_px=2)
    counter.update([{"track_id": 4, "class_label": "car", "bbox_x": 30, "bbox_y": 180, "bbox_w": 10, "bbox_h": 10}])
    counter.update([{"track_id": 4, "class_label": "car", "bbox_x": 60, "bbox_y": 180, "bbox_w": 10, "bbox_h": 10}])
    assert counter.total == 0
