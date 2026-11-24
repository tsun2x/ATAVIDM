"""Pickup review leads use existing main-detector tracks, never violation cases."""

from __future__ import annotations

import numpy as np
import json
import pytest


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


def _crop_person(track_id, pickup, x, y, w, h, *, source_vehicle_class=None):
    from core.pickup_person_scan import rider_geometry

    person = {
        **_det(track_id, "person", x, y, w, h),
        "source": "pickup_person_crop_submodel",
        "source_pickup_track_id": pickup["track_id"],
        "source_pickup_identity_epoch": pickup["track_identity_epoch"],
        "rider_geometry": rider_geometry(
            (float(x), float(y), float(w), float(h)),
            (pickup["bbox_x"], pickup["bbox_y"], pickup["bbox_w"], pickup["bbox_h"]),
        ),
    }
    if source_vehicle_class is not None:
        person["source_vehicle_class"] = source_vehicle_class
    return person


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
        pickup = _det(10, "pickup_truck", x, 80, 100, 80)
        tracked = [pickup, _crop_person(-1, pickup, x + 40, 50, 20, 40)]
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

    for index in range(4):
        x = 60 + index * 20
        pickup = _det(10, "pickup_truck", x, 80, 100, 80)
        collector.observe(
            frame, [pickup, _crop_person(-1, pickup, x + 32, 48, 24, 36)],
            frame_number=index + 1, timestamp_sec=index * 0.5,
        )
        if index < 3:
            assert collector.candidates() == []

    candidates = collector.candidates()
    assert len(candidates) == 1
    association = json.loads(candidates[0]["association_json"])
    assert association["state"] == "crop_person_scan_positive"
    assert association["evidence_source"] == "pickup_person_crop_submodel"
    assert association["motion"]["motion_state"] == "moving_with_pickup"
    assert association["rider_geometry"]["label"] == "elevated"
    assert association["crop_scan"]["minimum_persistence_sec"] == 1.1
    assert {item["geometry_label"] for item in association["observations"]} == {"elevated"}


def test_stationary_pickup_crop_rider_needs_two_seconds_and_is_tagged():
    """A parked pickup gives no shared motion, so its lead waits longer."""
    collector = _collector()
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    pickup = _det(10, "pickup_truck", 80, 80, 100, 80)
    person = _crop_person(-1, pickup, 112, 48, 24, 36)

    for index in range(5):
        collector.observe(
            frame, [pickup, person], frame_number=index + 1, timestamp_sec=index * 0.5,
        )
        if index < 4:
            assert collector.candidates() == []

    candidates = collector.candidates()
    assert len(candidates) == 1
    association = json.loads(candidates[0]["association_json"])
    assert association["motion"]["motion_state"] == "stationary"
    assert collector.summary()["crop_decision_counts"] == {"lead_created_stationary": 1}


def test_person_standing_still_behind_moving_pickup_is_rejected():
    """A person who does not move with the pickup is not a bed rider."""
    collector = _collector()
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    for index in range(5):
        x = 40 + index * 15
        pickup = _det(10, "pickup_truck", x, 80, 160, 80)
        collector.observe(
            frame, [pickup, _crop_person(-1, pickup, 112, 48, 24, 36)],
            frame_number=index + 1, timestamp_sec=index * 0.5,
        )

    assert collector.candidates() == []
    assert collector.summary()["crop_decision_counts"]["moved_independently"] >= 1


def test_moving_person_below_roofline_creates_lead_with_uncertain_bed_location():
    """The same 2D boxes can depict a low bed rider or a person in the cab."""
    collector = _collector()
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    for index in range(5):
        x = 20 + index * 15
        pickup = _det(10, "pickup_truck", x, 80, 100, 80)
        person = _crop_person(-1, pickup, x + 40, 88, 20, 24)
        assert person["rider_geometry"]["label"] == "ambiguous"
        collector.observe(
            frame, [pickup, person], frame_number=index + 1, timestamp_sec=index * 0.5,
        )

    candidate, = collector.candidates()
    association = json.loads(candidate["association_json"])
    assert association["state"] == "moving_with_pickup_bed_uncertain"
    assert association["rider_geometry"]["label"] == "ambiguous"
    assert association["cargo_region_visible"] is None


def test_main_detector_person_covered_by_crop_person_is_not_paired_twice():
    """The crop link wins; the same person does not also start a main-detector lead."""
    collector = _collector()
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    for index in range(5):
        x = 20 + index * 12
        pickup = _det(10, "pickup_truck", x, 80, 100, 80)
        crop_person = {
            **_crop_person(-1, pickup, x + 40, 95, 20, 45),
            "overlaps_primary_track_id": 20,
        }
        tracked = [pickup, _det(20, "person", x + 40, 95, 20, 45), crop_person]
        collector.observe(frame, tracked, frame_number=index * 10, timestamp_sec=index * 0.5)

    candidate, = collector.candidates()
    assert candidate["person_track_id"] == -1


def test_crop_person_cannot_start_a_main_detector_lead_for_another_pickup():
    collector = _collector()
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    for index in range(5):
        x = 20 + index * 15
        source = _det(10, "pickup_truck", x, 80, 100, 80)
        other = _det(11, "pickup_truck", x + 15, 80, 100, 80)
        person = _crop_person(-1, source, x + 35, 90, 20, 30)
        collector.observe(
            frame, [other, person], frame_number=index + 1, timestamp_sec=index * 0.5,
        )
    assert collector.candidates() == []


@pytest.mark.parametrize("case", ["parked", "crossing", "sparse", "contested", "car_fallback"])
def test_uncertain_bed_lead_requires_continuous_motion_and_clear_pickup(case):
    collector = _collector()
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    for index in range(5):
        x = 20 if case == "parked" else 20 + index * 15
        label = "car" if case == "car_fallback" else "pickup_truck"
        pickup = _det(10, label, x, 80, 100, 80)
        offset = 30 if case == "crossing" and index == 2 else 0
        person = _crop_person(-1, pickup, x + 30 + offset, 100, 20, 25)
        if case == "contested":
            person["pickup_ownership_contested"] = True
            person["overlaps_primary_track_id"] = 20
        tracked = [pickup, person]
        if case == "contested":
            tracked.append(_det(20, "person", x + 30, 100, 20, 25))
        collector.observe(
            frame, tracked, frame_number=index, timestamp_sec=index * (0.75 if case == "sparse" else 0.5),
        )
    assert collector.candidates() == []


def test_evidence_uses_matching_frame_and_boxes_from_the_qualified_passage():
    written = []

    def writer(frame, rect, **_kwargs):
        written.append((int(frame[0, 0, 0]), rect))
        return {"scene_path": "scene.jpg", "crop_path": "crop.jpg", "size_bytes": 12}

    collector = _collector(writer=writer)
    for epoch in (1, 2):
        for index, confidence in enumerate((0.97, 0.92, 0.88, 0.90)):
            x = 20 + index * 16
            pickup = _det(10, "pickup_truck", x, 80, 100, 80, epoch=epoch)
            pickup["confidence"] = 0.74 + index * 0.03
            person = _crop_person(-1, pickup, x + 30, 100, 20, 25)
            person["confidence"] = confidence
            frame = np.full((240, 320, 3), 60 * epoch + index, dtype=np.uint8)
            collector.observe(
                frame, [pickup, person], frame_number=(epoch - 1) * 10 + index,
                timestamp_sec=(epoch - 1) * 5 + index * 0.5,
            )

    first, second = collector.candidates()
    assert [marker for marker, _ in written] == [60, 120]
    for item, number, (_, rect) in zip((first, second), (0, 10), written):
        evidence, = json.loads(item["frames_json"])
        assert evidence["frame_number"] == item["frame_number"] == number
        assert evidence["pickup_bbox"] == {"x": 20, "y": 80, "w": 100, "h": 80}
        assert evidence["person_bbox"] == {"x": 50, "y": 100, "w": 20, "h": 25}
        assert evidence["pickup_detector_score"] == 0.74
        assert evidence["person_detector_score"] == 0.97
        assert (rect.x, rect.y, rect.w, rect.h) == (5, 68, 130, 104)


def test_failed_evidence_does_not_consume_pickup_passage():
    writes = []

    def writer(*_args, **_kwargs):
        writes.append(True)
        if len(writes) == 1:
            return None
        return {"scene_path": "scene.jpg", "crop_path": "crop.jpg", "size_bytes": 12}

    collector = _collector(writer=writer)
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    for index in range(6):
        x = 20 + index * 16
        pickup = _det(10, "pickup_truck", x, 80, 100, 80)
        person = _crop_person(-1, pickup, x + 30, 100, 20, 25)
        collector.observe(frame, [pickup, person], frame_number=index, timestamp_sec=index * 0.5)
        if index == 3:
            assert collector.candidates() == []
    assert len(collector.candidates()) == 1
    assert len(writes) == 2
    assert collector.summary()["crop_decision_counts"]["evidence_write_failed"] == 1


def test_crop_scan_hits_for_one_pickup_do_not_create_duplicate_review_leads():
    """Changing synthetic person IDs do not duplicate one pickup's review item."""
    collector = _collector()
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    pickup = _det(10, "pickup_truck", 80, 80, 100, 80)

    scanned_person = _crop_person(
        -1, pickup, 112, 48, 24, 36, source_vehicle_class="pickup_truck"
    )
    for index in range(5):
        collector.observe(
            frame, [pickup, scanned_person], frame_number=index + 1,
            timestamp_sec=index * 0.5,
        )

    # A later person-ID replacement for the same pickup must not create a
    # second row after the first persistent crop hit was reviewed.
    replacement = _crop_person(
        -2, pickup, 112, 48, 24, 36, source_vehicle_class="pickup_truck"
    )
    for index in range(5, 10):
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
    scanned_person = _crop_person(-1, car, 112, 48, 24, 36, source_vehicle_class="car")

    for index in range(5):
        collector.observe(
            frame, [car, scanned_person], frame_number=index + 1,
            timestamp_sec=index * 0.5,
        )
        if index < 4:
            assert collector.candidates() == []

    assert len(collector.candidates()) == 1
    association = json.loads(collector.candidates()[0]["association_json"])
    assert association["vehicle_class_at_scan"] == "car"


def test_unresolved_vehicle_track_still_links_crop_scan_person():
    """Tracker output carries resolved_track_class=None before class settles."""
    collector = _collector()
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    pickup = {**_det(10, "pickup_truck", 80, 80, 100, 80), "resolved_track_class": None}
    scanned_person = _crop_person(
        -1, pickup, 112, 48, 24, 36, source_vehicle_class="pickup_truck"
    )
    for index in range(5):
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
    pickup_sizes = [(80, 64), (90, 72), (110, 88), (100, 80), (100, 80)]
    frame_markers = [20, 40, 80, 120, 160]
    for index, ((width, height), marker) in enumerate(zip(pickup_sizes, frame_markers)):
        frame = np.full((240, 320, 3), marker, dtype=np.uint8)
        pickup = _det(10, "pickup_truck", 80, 80, width, height)
        person = _crop_person(-1, pickup, 112, 48, 24, 36)
        collector.observe(
            frame, [pickup, person], frame_number=index + 1,
            timestamp_sec=index * 0.5,
        )

    assert len(collector.candidates()) == 1
    assert written[0][0] == 80
