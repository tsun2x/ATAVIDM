"""Pickup review leads use existing main-detector tracks, never violation cases."""

from __future__ import annotations

import numpy as np
import json


def _det(track_id, label, x, y, w, h, *, epoch=1):
    return {
        "track_id": track_id,
        "track_identity_epoch": epoch,
        "class_label": label,
        "confidence": 0.9,
        "bbox_x": float(x),
        "bbox_y": float(y),
        "bbox_w": float(w),
        "bbox_h": float(h),
    }


def _collector(writer=None):
    from core.pickup_detail_review import PickupDetailCollector

    return PickupDetailCollector(
        video_id=3,
        run_key="run_7",
        frame_w=320,
        frame_h=240,
        processing_run_id=7,
        writer=writer or (lambda *args, **kwargs: {
            "scene_path": "scene.jpg", "crop_path": "crop.jpg", "size_bytes": 12
        }),
    )


def test_main_detector_tracks_emit_review_lead_after_persistent_shared_motion():
    """A person inside the pickup box and sharing its path creates only a review lead."""
    collector = _collector()
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    for index in range(5):
        x = 20 + index * 12
        tracked = [
            _det(10, "pickup_truck", x, 80, 100, 80),
            _det(20, "person", x + 40, 95, 20, 45),
        ]
        collector.observe(frame, tracked, frame_number=index * 10, timestamp_sec=index * 0.5)

    candidates = collector.candidates()
    assert len(candidates) == 1
    item = candidates[0]
    assert item["pickup_track_id"] == 10
    assert item["person_track_id"] == 20
    association = json.loads(item["association_json"])
    assert association["evidence_source"] == "main_detector_and_bytetrack"
    assert association["cargo_region_visible"] is None
    assert association["lower_body_visible"] is None


def test_crossing_person_and_non_pickup_do_not_create_review_leads():
    """Nearby crossings and other vehicle labels must not enter the pickup queue."""
    collector = _collector()
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    for index in range(6):
        x = 20 + index * 12
        tracked = [
            _det(10, "truck", x, 80, 100, 80),
            _det(20, "person", 40 + (index % 2) * 20, 40 + index * 4, 18, 35),
        ]
        collector.observe(frame, tracked, frame_number=index * 10, timestamp_sec=index * 0.5)
    assert collector.candidates() == []


def test_person_leaving_pickup_box_resets_the_candidate_episode():
    collector = _collector()
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    for index in range(9):
        x = 20 + index * 12
        person_x = x + (150 if index == 3 else 40)
        tracked = [
            _det(10, "pickup_truck", x, 80, 100, 80),
            _det(20, "person", person_x, 95, 20, 45),
        ]
        collector.observe(frame, tracked, frame_number=index * 10, timestamp_sec=index * 0.5)
        if index < 8:
            assert collector.candidates() == []
    assert len(collector.candidates()) == 1


def test_person_reentering_same_pickup_passage_does_not_duplicate_lead():
    collector = _collector()
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    for index in range(12):
        x = 20 + index * 12
        person_x = x + (150 if index == 5 else 40)
        tracked = [
            _det(10, "pickup_truck", x, 80, 100, 80),
            _det(20, "person", person_x, 95, 20, 45),
        ]
        collector.observe(frame, tracked, frame_number=index * 10, timestamp_sec=index * 0.5)
    candidates = collector.candidates()
    assert len(candidates) == 1


def test_crop_submodel_person_above_vehicle_box_can_create_review_lead():
    """The pickup crop pass can see riders whose body center is above the truck box."""
    collector = _collector()
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    for index in range(5):
        x = 20 + index * 12
        tracked = [
            _det(10, "pickup_truck", x, 80, 100, 80),
            {
                **_det(-1, "person", x + 40, 50, 20, 40),
                "source": "pickup_person_crop_submodel",
                "source_pickup_track_id": 10,
                "source_pickup_identity_epoch": 1,
            },
        ]
        collector.observe(frame, tracked, frame_number=index * 10, timestamp_sec=index * 0.5)

    candidates = collector.candidates()
    assert len(candidates) == 1
    assert json.loads(candidates[0]["association_json"])["evidence_source"] == (
        "pickup_person_crop_submodel"
    )


def test_crop_submodel_requires_1_1_seconds_before_review():
    """A one-frame pickup/person association cannot create a review lead."""
    collector = _collector()
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    pickup = _det(10, "pickup_truck", 80, 80, 100, 80)
    scanned_person = {
        **_det(-1, "person", 112, 48, 24, 36),
        "source": "pickup_person_crop_submodel",
        "source_pickup_track_id": 10,
        "source_pickup_identity_epoch": 1,
    }

    for index in range(3):
        collector.observe(
            frame, [pickup, scanned_person], frame_number=index + 1,
            timestamp_sec=index * 0.5,
        )
        assert collector.candidates() == []

    collector.observe(
        frame, [pickup, scanned_person], frame_number=4, timestamp_sec=1.5
    )
    candidates = collector.candidates()
    assert len(candidates) == 1
    association = json.loads(candidates[0]["association_json"])
    assert association["state"] == "crop_person_scan_positive"
    assert association["evidence_source"] == "pickup_person_crop_submodel"
    assert association["motion"] == {}
    assert association["crop_scan"]["minimum_persistence_sec"] == 1.1


def test_crop_scan_hits_for_one_pickup_do_not_create_duplicate_review_leads():
    """Changing synthetic person IDs do not duplicate one pickup's review item."""
    collector = _collector()
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    pickup = _det(10, "pickup_truck", 80, 80, 100, 80)

    for index in range(4):
        scanned_person = {
            **_det(-1, "person", 112, 48, 24, 36),
            "source": "pickup_person_crop_submodel",
            "source_pickup_track_id": 10,
            "source_pickup_identity_epoch": 1,
            "source_vehicle_class": "pickup_truck",
        }
        collector.observe(
            frame, [pickup, scanned_person], frame_number=index + 1,
            timestamp_sec=index * 0.5,
        )

    # A later person-ID replacement for the same pickup must not create a
    # second row after the first persistent crop hit was reviewed.
    replacement = {
        **_det(-2, "person", 112, 48, 24, 36),
        "source": "pickup_person_crop_submodel",
        "source_pickup_track_id": 10,
        "source_pickup_identity_epoch": 1,
        "source_vehicle_class": "pickup_truck",
    }
    for index in range(4, 8):
        collector.observe(
            frame, [pickup, replacement], frame_number=index + 1,
            timestamp_sec=index * 0.5,
        )

    assert len(collector.candidates()) == 1


def test_crop_scan_car_fallback_requires_persistent_person_hit():
    """A car-labeled candidate routes only after a sustained crop-person hit."""
    collector = _collector()
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    car = _det(12, "car", 80, 80, 100, 80)
    scanned_person = {
        **_det(-1, "person", 112, 48, 24, 36),
        "source": "pickup_person_crop_submodel",
        "source_pickup_track_id": 12,
        "source_pickup_identity_epoch": 1,
        "source_vehicle_class": "car",
    }

    for index in range(4):
        collector.observe(
            frame, [car, scanned_person], frame_number=index + 1,
            timestamp_sec=index * 0.5,
        )
        if index < 3:
            assert collector.candidates() == []

    assert len(collector.candidates()) == 1
    association = json.loads(collector.candidates()[0]["association_json"])
    assert association["vehicle_class_at_scan"] == "car"


def test_unresolved_vehicle_track_still_links_crop_scan_person():
    """Tracker output carries resolved_track_class=None before class settles."""
    collector = _collector()
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    pickup = {**_det(10, "pickup_truck", 80, 80, 100, 80), "resolved_track_class": None}
    scanned_person = {
        **_det(-1, "person", 112, 48, 24, 36),
        "source": "pickup_person_crop_submodel",
        "source_pickup_track_id": 10,
        "source_pickup_identity_epoch": 1,
        "source_vehicle_class": "pickup_truck",
    }
    for index in range(4):
        collector.observe(
            frame, [pickup, scanned_person], frame_number=index + 1,
            timestamp_sec=index * 0.5,
        )

    candidates = collector.candidates()
    assert len(candidates) == 1
    assert candidates[0]["pickup_track_id"] == 10
    assert json.loads(candidates[0]["association_json"])["vehicle_class_at_scan"] == "pickup_truck"


def test_crop_review_evidence_uses_largest_vehicle_box_during_persistence():
    """Review evidence selects the largest pickup crop seen before confirmation."""
    written = []

    def writer(frame, rect, **_kwargs):
        written.append((int(frame[0, 0, 0]), rect))
        return {"scene_path": "scene.jpg", "crop_path": "crop.jpg", "size_bytes": 12}

    collector = _collector(writer=writer)
    pickup_sizes = [(80, 64), (90, 72), (110, 88), (100, 80)]
    frame_markers = [20, 40, 80, 120]
    for index, ((width, height), marker) in enumerate(zip(pickup_sizes, frame_markers)):
        frame = np.full((240, 320, 3), marker, dtype=np.uint8)
        pickup = _det(10, "pickup_truck", 80, 80, width, height)
        person = {
            **_det(-1, "person", 112, 48, 24, 36),
            "source": "pickup_person_crop_submodel",
            "source_pickup_track_id": 10,
            "source_pickup_identity_epoch": 1,
        }
        collector.observe(
            frame, [pickup, person], frame_number=index + 1,
            timestamp_sec=index * 0.5,
        )

    assert len(collector.candidates()) == 1
    assert written[0][0] == 80
