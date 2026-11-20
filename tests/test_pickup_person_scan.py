"""Pickup crop person submodel keeps detections local to review/visual evidence."""

import numpy as np


class _Box:
    def __init__(self, xyxy, cls=0, conf=0.9):
        self.xyxy = np.asarray([xyxy], dtype=float)
        self.cls = np.asarray([cls], dtype=float)
        self.conf = np.asarray([conf], dtype=float)


class _Result:
    def __init__(self, boxes):
        self.boxes = boxes


def test_pickup_crop_scan_maps_person_box_back_to_frame_and_keeps_stable_track():
    """A lost primary-person box should be recovered inside each pickup crop."""
    from core.pickup_person_scan import PickupPersonCropScanner

    class Model:
        names = {0: "person", 1: "car"}

        def __init__(self):
            self.inputs = None

        def predict(self, source, **kwargs):
            self.inputs = source
            # The first result is a person and an unrelated class. Only the
            # person result may be returned from the crop pass.
            return [
                _Result([_Box([30, 40, 50, 100]), _Box([5, 5, 80, 80], cls=1)])
                for _crop in source
            ]

    model = Model()
    scanner = PickupPersonCropScanner(enabled=True, model_loader=lambda _path: model)
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    pickup = {
        "track_id": 7,
        "track_identity_epoch": 2,
        "class_label": "pickup_truck",
        "bbox_x": 100,
        "bbox_y": 80,
        "bbox_w": 100,
        "bbox_h": 80,
    }

    first = scanner.scan(frame, [pickup], timestamp_sec=1.0)
    second = scanner.scan(frame, [pickup], timestamp_sec=1.5)

    assert len(model.inputs) == 1
    # Crop extends above the truck box to retain riders visible above the bed.
    assert model.inputs[0].shape[:2] == (128, 116)
    assert first[0]["class_label"] == "person"
    assert first[0]["bbox_x"] == 122
    assert first[0]["bbox_y"] == 76
    assert first[0]["bbox_w"] == 20
    assert first[0]["bbox_h"] == 60
    assert first[0]["track_id"] == second[0]["track_id"]
    assert first[0]["source"] == "pickup_person_crop_submodel"


def test_pickup_crop_scan_skips_non_pickups_and_disabled_scanner():
    from core.pickup_person_scan import PickupPersonCropScanner

    class Model:
        names = {0: "person"}

        def predict(self, *_args, **_kwargs):
            raise AssertionError("should not infer without pickup crops")

    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    truck = {"track_id": 1, "class_label": "truck", "bbox_x": 0,
             "bbox_y": 0, "bbox_w": 50, "bbox_h": 30}
    scanner = PickupPersonCropScanner(enabled=True, model_loader=lambda _path: Model())
    assert scanner.scan(frame, [truck], timestamp_sec=0.0) == []

    disabled = PickupPersonCropScanner(enabled=False, model_loader=lambda _path: Model())
    assert disabled.scan(frame, [], timestamp_sec=0.0) == []


def test_car_fallback_accepts_person_over_vehicle_and_rejects_adjacent_person():
    """A pickup labeled as car can be scanned without adopting nearby people."""
    from core.pickup_person_scan import PickupPersonCropScanner

    class Model:
        names = {0: "person"}

        def predict(self, source, **kwargs):
            assert len(source) == 1
            return [_Result([
                _Box([30, 5, 50, 40]),
                _Box([111, 5, 116, 40]),
            ])]

    scanner = PickupPersonCropScanner(enabled=True, model_loader=lambda _path: Model())
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    car = {
        "track_id": 12,
        "track_identity_epoch": 1,
        "class_label": "car",
        "bbox_x": 100,
        "bbox_y": 80,
        "bbox_w": 100,
        "bbox_h": 80,
    }

    detections = scanner.scan(frame, [car], timestamp_sec=1.0)

    assert len(detections) == 1
    assert detections[0]["source_vehicle_class"] == "car"
    assert detections[0]["bbox_x"] == 122
    assert detections[0]["bbox_y"] == 41
