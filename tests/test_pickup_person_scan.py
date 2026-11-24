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


def test_flickering_pickup_resolved_as_car_is_promoted_to_per_frame_scan():
    """Raw pickup frames route a car-resolved or unresolved track as a pickup.

    Mirrors IMG_7440 track 204: the tracker settled it as car while far away
    and its pickup label never held for the motion window, so the rider was
    scanned only by the throttled car fallback.
    """
    from core.pickup_person_scan import PickupPersonCropScanner

    class Model:
        names = {0: "person"}

        def __init__(self):
            self.calls = 0

        def predict(self, source, **kwargs):
            self.calls += 1
            return [_Result([_Box([45, 40, 60, 90])]) for _crop in source]

    # Unresolved tracks fall back to the raw label, so their pickup frames
    # before promotion (indices 1 and 3) are scanned too.
    for resolved, expected_calls in (("car", 4), (None, 6)):
        model = Model()
        scanner = PickupPersonCropScanner(enabled=True, model_loader=lambda _path: model)
        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        labels = ["car", "pickup_truck", "car", "pickup_truck", "pickup_truck", "car", "car"]
        outputs = []
        for index, label in enumerate(labels):
            vehicle = {
                "track_id": 204,
                "track_identity_epoch": 1,
                "class_label": label,
                "resolved_track_class": resolved,
                "bbox_x": 100,
                "bbox_y": 80,
                "bbox_w": 100,
                "bbox_h": 80,
            }
            outputs.append(scanner.scan(frame, [vehicle], timestamp_sec=index * 0.1))

        # Third raw pickup frame (index 4) promotes the track; it then stays a
        # pickup for the rest of the identity epoch despite later car frames.
        assert all(item[0]["source_vehicle_class"] == "pickup_truck" for item in outputs[4:])
        assert model.calls == expected_calls
        assert len({item[0]["track_id"] for item in outputs[4:]}) == 1


def test_pickup_scan_rejects_person_centered_past_vehicle_side():
    """A pedestrian beside a parked pickup is not adopted as a bed rider."""
    from core.pickup_person_scan import PickupPersonCropScanner

    class Model:
        names = {0: "person"}

        def predict(self, source, **kwargs):
            # Crop left is 92; centers map to frame x 202 (outside) and 150.
            return [_Result([_Box([105, 30, 115, 90]), _Box([53, 30, 63, 90])])]

    scanner = PickupPersonCropScanner(enabled=True, model_loader=lambda _path: Model())
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    pickup = {
        "track_id": 7, "track_identity_epoch": 1, "class_label": "pickup_truck",
        "resolved_track_class": "pickup_truck",
        "bbox_x": 100, "bbox_y": 80, "bbox_w": 100, "bbox_h": 80,
    }
    detections = scanner.scan(frame, [pickup], timestamp_sec=0.0)

    assert len(detections) == 1
    assert detections[0]["bbox_x"] == 145


def test_person_track_survives_box_jitter_and_vehicle_class_flapping():
    """The same crop-person identity remains stable across small box jitter."""
    from core.pickup_person_scan import PickupPersonCropScanner

    class Model:
        names = {0: "person"}

        def __init__(self):
            self.results = iter([
                _Result([_Box([30, 0, 35, 30])]),
                _Result([_Box([35, 0, 40, 30])]),
            ])

        def predict(self, *_args, **_kwargs):
            return [next(self.results)]

    scanner = PickupPersonCropScanner(enabled=True, model_loader=lambda _path: Model())
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    vehicle = {
        "track_id": 12,
        "track_identity_epoch": 1,
        "class_label": "pickup_truck",
        "bbox_x": 100,
        "bbox_y": 80,
        "bbox_w": 100,
        "bbox_h": 80,
    }
    first = scanner.scan(frame, [vehicle], timestamp_sec=0.0)
    vehicle["class_label"] = "car"
    second = scanner.scan(frame, [vehicle], timestamp_sec=0.5)

    assert len(first) == len(second) == 1
    assert first[0]["track_id"] == second[0]["track_id"]


def test_rider_geometry_matches_stored_img_7440_cases():
    """Boxes from Runs #53/#54: the bed rider is elevated; lot pedestrians are not."""
    from core.pickup_person_scan import rider_geometry

    # Run #54, pickup track 204 at 38.4 s.
    assert rider_geometry((1231, 229, 35, 49), (1184, 269, 166, 162))["label"] == "elevated"
    # Run #53 track 7: legs visible down to the pickup's lower edge.
    assert rider_geometry((678, 254, 10, 32), (482, 223, 194, 78))["label"] == "ground_level"
    # Run #53 track 146: a distant pedestrian entirely above the pickup box.
    assert rider_geometry((127, 282, 22, 18), (149, 336, 274, 116))["label"] == "detached_above"
    # A cab occupant whose head stays below the roofline.
    assert rider_geometry((140, 88, 20, 24), (100, 80, 100, 80))["label"] == "ambiguous"


def test_scan_logs_filter_decisions_and_summary_counters():
    """Rejected and sub-threshold boxes are logged for missed-rider diagnosis."""
    from core.pickup_person_scan import SCANNER_VERSION, PickupPersonCropScanner

    class Model:
        names = {0: "person"}

        def predict(self, source, **kwargs):
            assert kwargs["conf"] == 0.10
            return [_Result([
                _Box([38, 34, 58, 114]),             # standing on the ground
                _Box([60, 10, 75, 40], conf=0.12),   # below person confidence
                _Box([30, 5, 50, 40]),               # rider above the bed
            ])]

    scanner = PickupPersonCropScanner(enabled=True, model_loader=lambda _path: Model())
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    pickup = {
        "track_id": 204, "track_identity_epoch": 1, "class_label": "pickup_truck",
        "resolved_track_class": "pickup_truck",
        "bbox_x": 100, "bbox_y": 80, "bbox_w": 100, "bbox_h": 80,
    }
    detections = scanner.scan(frame, [pickup], timestamp_sec=38.4, frame_number=1152)

    assert len(detections) == 1
    assert detections[0]["bbox_x"] == 122
    assert detections[0]["rider_geometry"]["label"] == "elevated"
    rows = scanner.scan_log_rows()
    assert len(rows) == 1
    assert rows[0]["frame"] == 1152
    assert rows[0]["pickup_track_id"] == 204
    assert rows[0]["scan_reason"] == "resolved_track_class"
    assert [p["decision"] for p in rows[0]["persons"]] == [
        "ground_level", "below_confidence", "kept",
    ]
    assert rows[0]["persons"][2]["track_id"] == detections[0]["track_id"]
    summary = scanner.summary()
    assert summary["version"] == SCANNER_VERSION
    assert summary["counters"]["decision_kept"] == 1
    assert summary["pickups"][0]["raw_person_boxes"] == 3
    assert summary["pickups"][0]["kept_person_boxes"] == 1


def test_person_seen_in_overlapping_pickup_crops_links_to_one_pickup():
    """One person is never attached to two pickups whose crops overlap."""
    from core.pickup_person_scan import PickupPersonCropScanner

    class Model:
        names = {0: "person"}

        def predict(self, source, **kwargs):
            # Both crops contain the same frame box (160, 50, 20, 40).
            return [_Result([_Box([68, 14, 88, 54])]), _Result([_Box([18, 9, 38, 49])])]

    scanner = PickupPersonCropScanner(enabled=True, model_loader=lambda _path: Model())
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    pickups = [
        {"track_id": 1, "track_identity_epoch": 1, "class_label": "pickup_truck",
         "resolved_track_class": "pickup_truck",
         "bbox_x": 100, "bbox_y": 80, "bbox_w": 100, "bbox_h": 80},
        {"track_id": 2, "track_identity_epoch": 1, "class_label": "pickup_truck",
         "resolved_track_class": "pickup_truck",
         "bbox_x": 150, "bbox_y": 85, "bbox_w": 100, "bbox_h": 80},
    ]
    detections = scanner.scan(frame, pickups, timestamp_sec=0.0)

    assert len(detections) == 1
    assert detections[0]["source_pickup_track_id"] == 1
    assert detections[0]["pickup_ownership_contested"] is True
    decisions = {
        row["pickup_track_id"]: row["persons"][0]["decision"]
        for row in scanner.scan_log_rows()
    }
    assert decisions == {1: "kept", 2: "assigned_to_other_pickup"}


def test_scan_log_is_written_as_json_lines_in_the_run_evidence_folder(tmp_path, monkeypatch):
    import json

    import core.evidence as evidence

    monkeypatch.setattr(evidence, "EVIDENCE_FOLDER", str(tmp_path))
    assert evidence.save_pickup_detail_scan_log("run_54", []) is None
    stored = evidence.save_pickup_detail_scan_log(
        "run_54", [{"t": 38.4, "pickup_track_id": 204}, {"t": 38.5, "pickup_track_id": 204}]
    )

    log_file = tmp_path / "pickup_detail" / "run_54" / "scan_log.jsonl"
    assert stored is not None and log_file.is_file()
    lines = log_file.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["t"] for line in lines] == [38.4, 38.5]


def test_crop_person_overlapping_main_detector_person_is_kept_and_annotated():
    from core.pickup_person_scan import PickupPersonCropScanner

    class Model:
        names = {0: "person"}

        def predict(self, source, **kwargs):
            return [_Result([_Box([68, 14, 88, 54])])]

    scanner = PickupPersonCropScanner(enabled=True, model_loader=lambda _path: Model())
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    tracked = [
        {"track_id": 1, "track_identity_epoch": 1, "class_label": "pickup_truck",
         "resolved_track_class": "pickup_truck",
         "bbox_x": 100, "bbox_y": 80, "bbox_w": 100, "bbox_h": 80},
        {"track_id": 55, "track_identity_epoch": 1, "class_label": "person",
         "bbox_x": 160, "bbox_y": 50, "bbox_w": 20, "bbox_h": 40},
    ]
    detections = scanner.scan(frame, tracked, timestamp_sec=0.0)

    assert len(detections) == 1
    assert detections[0]["overlaps_primary_track_id"] == 55
    assert detections[0]["source_pickup_track_id"] == 1
