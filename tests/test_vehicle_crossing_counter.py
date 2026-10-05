from types import SimpleNamespace

import pytest

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


def _second_line():
    return SimpleNamespace(id="count-2", type="counting_line", points=((80.0, 0.0), (80.0, 100.0)), metadata={})


def test_repeated_frame_observations_of_one_track_are_not_passages():
    counter = VehicleCrossingCounter([_line()], hysteresis_px=2)
    for _ in range(30):
        counter.update([_det(3, 30, label="car")])
    assert counter.total == 0
    assert counter.by_class == {}


def test_one_track_crossing_one_line_back_and_forth_counts_once():
    counter = VehicleCrossingCounter([_line()], hysteresis_px=2)
    for x in (30, 70, 30, 70, 30):
        counter.update([_det(5, x, label="car")])
    assert counter.total == 1
    assert counter.by_class == {"car": 1}


def test_one_track_crossing_two_lines_is_two_passages():
    counter = VehicleCrossingCounter([_line(), _second_line()], hysteresis_px=2)
    for x in (30, 60, 95):
        counter.update([_det(9, x, label="bus")])
    assert counter.total == 2
    assert counter.by_class == {"bus": 2}
    assert dict(counter.by_line) == {"count-1": 1, "count-2": 1}


@pytest.mark.parametrize("label", ["person", "rider", "helmet_acceptable", "helmet_nut_shell", "side_mirror", "no_helmet"])
def test_non_vehicle_labels_never_become_vehicle_passages(label):
    counter = VehicleCrossingCounter([_line()], hysteresis_px=2)
    for x in (30, 70):
        counter.update([_det(11, x, label=label)])
    assert counter.total == 0
    assert counter.by_class == {}
    assert counter.non_vehicle_observations[label] == 2


def test_valid_zero_with_a_counting_line_differs_from_unavailable():
    counter = VehicleCrossingCounter([_line()], hysteresis_px=2)
    counter.update([_det(1, 30, label="car")])
    assert counter.available is True
    assert counter.total == 0


def test_crossing_infinite_extension_outside_segment_does_not_count():
    counter = VehicleCrossingCounter([_line()], hysteresis_px=2)
    counter.update([{"track_id": 4, "class_label": "car", "bbox_x": 30, "bbox_y": 180, "bbox_w": 10, "bbox_h": 10}])
    counter.update([{"track_id": 4, "class_label": "car", "bbox_x": 60, "bbox_y": 180, "bbox_w": 10, "bbox_h": 10}])
    assert counter.total == 0
