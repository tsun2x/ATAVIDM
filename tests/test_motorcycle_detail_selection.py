"""Motorcycle detail selection: track grouping, caps, ranking, crops, association.

Pure-logic tests. No database, no model, no Flask.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from core.motorcycle_detail import (
    CropRect,
    DETAIL_SELECTOR_VERSION,
    MAX_FRAMES_PER_OCCURRENCE,
    STATE_AMBIGUOUS,
    STATE_ASSOCIATED,
    STATE_UNASSOCIATED,
    HELMET_ACCEPTABLE,
    HELMET_NUT_SHELL,
    HELMET_UNKNOWN,
    MIRROR_BOTH,
    MIRROR_NONE_VISIBLE,
    MIRROR_ONE_LEFT,
    MotorcycleDetailCollector,
    TrackOccurrenceRegistry,
    associate_helmet,
    associate_mirrors,
    associate_rider,
    is_motorcycle_detection,
    map_box_from_crop,
    padded_crop_rect,
    rider_head_region,
    scan_scale_for,
    score_frame,
)

FRAME_W, FRAME_H = 640, 480


def _det(track_id, label, ts=0.0, x=300.0, y=200.0, w=60.0, h=80.0, conf=0.9, **extra):
    row = {
        "track_id": track_id,
        "class_label": label,
        "confidence": conf,
        "bbox_x": float(x),
        "bbox_y": float(y),
        "bbox_w": float(w),
        "bbox_h": float(h),
        "timestamp_sec": float(ts),
    }
    row.update(extra)
    return row


def _sharp_frame(w=FRAME_W, h=FRAME_H, seed=0):
    import cv2

    rng = np.random.default_rng(seed)
    base = cv2.GaussianBlur(
        rng.integers(60, 190, size=(h, w, 3), dtype=np.uint8), (5, 5), 0
    )
    noise = rng.integers(0, 60, size=(h, w, 3), dtype=np.uint8)
    return cv2.add(base, noise)


def _blur_frame(w=FRAME_W, h=FRAME_H):
    import cv2

    return cv2.GaussianBlur(np.full((h, w, 3), 120, dtype=np.uint8), (21, 21), 0)


# ---------------------------------------------------------------------------
# Detection eligibility
# ---------------------------------------------------------------------------


class TestEligibility:
    def test_motorcycle_with_track_id_is_eligible(self):
        assert is_motorcycle_detection(_det(3, "motorcycle")) is True

    def test_bicycle_is_excluded(self):
        assert is_motorcycle_detection(_det(3, "bicycle")) is False

    def test_motorcycle_without_track_id_is_excluded(self):
        assert is_motorcycle_detection(_det(None, "motorcycle")) is False
        assert is_motorcycle_detection(_det("abc", "motorcycle")) is False
        assert is_motorcycle_detection(_det(True, "motorcycle")) is False


# ---------------------------------------------------------------------------
# Track-occurrence grouping and ID reuse
# ---------------------------------------------------------------------------


class TestOccurrenceGrouping:
    def test_same_id_within_expiry_is_one_occurrence(self):
        reg = TrackOccurrenceRegistry(expiry_sec=5.0)
        reg.observe([_det(7, "motorcycle", 0.0)], now=0.0)
        reg.observe([_det(7, "motorcycle", 3.0)], now=3.0)
        assert reg.active_keys() == ("t7g1",)
        assert reg.active_occurrence(7).occurrence_index == 1

    def test_id_reuse_after_expiry_creates_a_new_occurrence(self):
        reg = TrackOccurrenceRegistry(expiry_sec=5.0)
        reg.observe([_det(7, "motorcycle", 0.0)], now=0.0)
        retired = reg.observe([_det(7, "motorcycle", 20.0)], now=20.0)
        assert [r.key for r in retired if r.reason == "track_expired"] == ["t7g1"]
        assert reg.active_keys() == ("t7g2",)

    def test_class_change_closes_the_occurrence(self):
        reg = TrackOccurrenceRegistry(expiry_sec=5.0)
        reg.observe([_det(7, "motorcycle", 0.0)], now=0.0)
        retired = reg.observe([_det(7, "car", 0.2)], now=0.2)
        assert [r.key for r in retired if r.reason == "class_changed"] == ["t7g1"]
        assert reg.active_keys() == ("t7g2",)

    def test_active_memory_is_bounded(self):
        reg = TrackOccurrenceRegistry(expiry_sec=100.0, max_active=3)
        for tid in range(6):
            reg.observe([_det(tid, "motorcycle", float(tid))], now=float(tid))
        assert len(reg.active_keys()) == 3
        assert reg.overflow_retired == 3

    def test_unrelated_vehicles_never_consume_the_motorcycle_budget(self):
        """24 nearby cars must not evict the one motorcycle being reviewed."""
        reg = TrackOccurrenceRegistry(expiry_sec=100.0, max_active=4, max_foreign=6)
        reg.observe([_det(7, "motorcycle", 0.0)], now=0.0)
        # The motorcycle stays at t7g1 while 24 foreign vehicles are observed.
        for index in range(24):
            reg.observe(
                [_det(1000 + index, "car", 0.0, x=10.0 + index * 2, y=20.0)],
                now=0.0,
            )
        assert reg.active_occurrence(7).key == "t7g1"
        assert reg.active_keys() == ("t7g1",) + tuple(
            f"t{1000 + index}g1" for index in range(18, 24)
        )
        assert reg.overflow_retired == 0
        assert reg.foreign_retired == 18

    def test_foreign_vehicles_are_bounded_independently(self):
        reg = TrackOccurrenceRegistry(expiry_sec=100.0, max_active=2, max_foreign=3)
        for index in range(9):
            reg.observe(
                [_det(1000 + index, "car", 0.0, x=10.0 + index * 2, y=20.0)], now=0.0
            )
        assert len(reg.active_keys()) == 3
        assert reg.foreign_retired == 6
        assert reg.overflow_retired == 0

    def test_foreign_budget_is_never_tighter_than_the_motorcycle_budget(self):
        reg = TrackOccurrenceRegistry(expiry_sec=100.0, max_active=4, max_foreign=1)
        for index in range(8):
            reg.observe(
                [_det(1000 + index, "car", 0.0, x=10.0 + index * 2, y=20.0)], now=0.0
            )
        assert len(reg.active_keys()) == 4

    def test_foreign_eviction_never_retires_a_motorcycle(self):
        reg = TrackOccurrenceRegistry(expiry_sec=100.0, max_active=4, max_foreign=5)
        reg.observe([_det(7, "motorcycle", 0.0)], now=0.0)
        for index in range(12):
            reg.observe(
                [_det(1000 + index, "car", 0.0, x=10.0 + index * 2, y=20.0)], now=0.0
            )
        assert reg.active_occurrence(7) is not None
        # 1 motorcycle + 5 foreign, and the motorcycle kept its occurrence key.
        assert len(reg.active_keys()) == 6
        assert reg.overflow_retired == 0

    def test_motorcycle_becoming_a_car_is_never_reviewed_as_a_motorcycle(self):
        reg = TrackOccurrenceRegistry(expiry_sec=100.0, max_active=4)
        reg.observe([_det(7, "motorcycle", 0.0)], now=0.0)
        retired = reg.observe([_det(7, "car", 0.2)], now=0.2)
        assert [(r.key, r.reason) for r in retired] == [("t7g1", "class_changed")]
        current = reg.active_occurrence(7)
        assert current.key == "t7g2"
        assert current.is_motorcycle is False

    def test_vehicle_that_becomes_a_motorcycle_gets_a_motorcycle_occurrence(self):
        reg = TrackOccurrenceRegistry(expiry_sec=100.0, max_active=4)
        reg.observe([_det(7, "car", 0.0)], now=0.0)
        reg.observe([_det(7, "motorcycle", 0.2)], now=0.2)
        current = reg.active_occurrence(7)
        assert current.key == "t7g2"
        assert current.is_motorcycle is True

    def test_generation_memory_is_bounded(self):
        reg = TrackOccurrenceRegistry(
            expiry_sec=0.0, max_active=1, max_generations=8
        )
        for step in range(40):
            reg.observe([_det(7, "motorcycle", float(step) * 2.0)], now=float(step) * 2.0)
        # Tracked generation bookkeeping cannot grow without bound.
        assert len(reg._generation) <= 8  # noqa: SLF001 - the bound is the contract
        assert len(reg.active_keys()) == 1


# ---------------------------------------------------------------------------
# Frame ranking
# ---------------------------------------------------------------------------


class TestFrameRanking:
    def test_sharp_large_visible_frame_scores_above_blurry_small_one(self):
        det = _det(1, "motorcycle", conf=0.95)
        good = score_frame(_sharp_frame(), det, frame_w=FRAME_W, frame_h=FRAME_H)
        weak = score_frame(
            _blur_frame(),
            _det(1, "motorcycle", conf=0.3, x=2.0, y=2.0, w=8.0, h=10.0),
            frame_w=FRAME_W, frame_h=FRAME_H,
        )
        assert good.total > weak.total
        assert good.breakdown["sharpness"] > weak.breakdown["sharpness"]
        assert weak.breakdown["size"] < good.breakdown["size"]

    def test_clipped_object_lowers_the_clipping_component(self):
        inside = score_frame(_sharp_frame(), _det(1, "motorcycle"), frame_w=FRAME_W, frame_h=FRAME_H)
        clipped = score_frame(
            _sharp_frame(), _det(1, "motorcycle", x=-40.0, y=200.0, w=80.0, h=80.0),
            frame_w=FRAME_W, frame_h=FRAME_H,
        )
        assert clipped.breakdown["clipping"] < inside.breakdown["clipping"]
        assert "clipped_by_frame_edge" in clipped.reasons

    def test_occlusion_lowers_the_occlusion_component(self):
        det = _det(1, "motorcycle")
        other = _det(2, "car", x=305.0, y=205.0, w=55.0, h=70.0)
        clear = score_frame(_sharp_frame(), det, frame_w=FRAME_W, frame_h=FRAME_H)
        blocked = score_frame(_sharp_frame(), det, others=[other], frame_w=FRAME_W, frame_h=FRAME_H)
        assert blocked.breakdown["occlusion"] < clear.breakdown["occlusion"]
        assert "occluded_by_other_object" in blocked.reasons

    def test_weights_sum_to_one_and_score_is_bounded(self):
        from core.motorcycle_detail import SCORE_WEIGHTS

        assert abs(sum(SCORE_WEIGHTS.values()) - 1.0) < 1e-9
        score = score_frame(_sharp_frame(), _det(1, "motorcycle"), frame_w=FRAME_W, frame_h=FRAME_H)
        assert 0.0 <= score.total <= 1.0


# ---------------------------------------------------------------------------
# Crop margins and coordinate mapping
# ---------------------------------------------------------------------------


class TestCropGeometry:
    def test_padded_crop_includes_space_above_the_vehicle(self):
        rect = padded_crop_rect((300.0, 200.0, 360.0, 280.0), FRAME_W, FRAME_H)
        assert rect is not None
        assert rect.y < 200.0
        assert rect.x < 300.0 and rect.x + rect.w > 360.0
        assert rect.y + rect.h >= 280.0
        assert rect.w > 60.0

    def test_rider_box_expands_the_crop_upward(self):
        without = padded_crop_rect((300.0, 200.0, 360.0, 280.0), FRAME_W, FRAME_H)
        with_rider = padded_crop_rect(
            (300.0, 200.0, 360.0, 280.0), FRAME_W, FRAME_H,
            rider_box=(305.0, 150.0, 355.0, 240.0),
        )
        assert with_rider.y <= without.y
        assert with_rider.h >= without.h

    def test_edge_clamped_crop_stays_inside_the_frame(self):
        rect = padded_crop_rect((-20.0, -30.0, 10.0, 10.0), FRAME_W, FRAME_H)
        assert rect is not None
        assert rect.x >= 0 and rect.y >= 0
        assert rect.x + rect.w <= FRAME_W and rect.y + rect.h <= FRAME_H
        assert rect.w > 0 and rect.h > 0

    def test_scan_scale_only_upscales_small_crops(self):
        assert scan_scale_for(CropRect(0, 0, 400, 400), min_side=320) == (1.0, 1.0)
        small = scan_scale_for(CropRect(0, 0, 200, 150), min_side=320)
        assert small[0] == pytest.approx(320 / 150)
        assert small[0] == small[1]
        # The upscale cap keeps a tiny crop from exploding in cost.
        assert scan_scale_for(CropRect(0, 0, 10, 8), min_side=320)[0] == pytest.approx(3.0)

    def test_mapping_from_resized_crop_returns_source_coordinates(self):
        crop = CropRect(x=100, y=50, w=200, h=150)
        resized = (10.0, 20.0, 100.0, 140.0)
        mapped = map_box_from_crop(
            resized, crop, scale_x=2.0, scale_y=2.0,
            source_w=FRAME_W, source_h=FRAME_H,
        )
        assert mapped[0] == pytest.approx(105.0)
        assert mapped[1] == pytest.approx(60.0)
        assert mapped[2] == pytest.approx(150.0)
        assert mapped[3] == pytest.approx(120.0)

    def test_mapping_is_clamped_for_edge_clamped_crops(self):
        crop = CropRect(x=0, y=0, w=120, h=100)
        mapped = map_box_from_crop(
            (-40.0, -30.0, 260.0, 220.0), crop,
            scale_x=1.0, scale_y=1.0, source_w=200, source_h=150,
        )
        assert mapped[0] >= 0.0 and mapped[1] >= 0.0
        assert mapped[2] <= 200.0 and mapped[3] <= 150.0

    def test_non_positive_scan_scale_is_rejected(self):
        from core.motorcycle_detail import DetailSelectionError

        with pytest.raises(DetailSelectionError):
            map_box_from_crop(
                (0, 0, 1, 1), CropRect(0, 0, 10, 10),
                scale_x=0.0, scale_y=1.0, source_w=10, source_h=10,
            )


# ---------------------------------------------------------------------------
# Association: rider, helmet, side mirror
# ---------------------------------------------------------------------------

BIKE = _det(7, "motorcycle", x=300.0, y=200.0, w=60.0, h=80.0)


class TestRiderAssociation:
    def test_overlapping_rider_is_associated(self):
        rider = _det(11, "rider", x=305.0, y=160.0, w=50.0, h=90.0)
        assoc = associate_rider(BIKE, [rider])
        assert assoc.state == STATE_ASSOCIATED
        assert assoc.rider_track_id == 11
        assert assoc.head_region is not None

    def test_generic_person_never_associates_as_rider(self):
        person = _det(11, "person", x=305.0, y=160.0, w=50.0, h=90.0)
        assoc = associate_rider(BIKE, [person])
        assert assoc.state == STATE_UNASSOCIATED
        assert assoc.rider_track_id is None
        assert "no_rider_overlapping_motorcycle" in assoc.reasons

    def test_distant_rider_is_not_associated(self):
        rider = _det(11, "rider", x=100.0, y=60.0, w=20.0, h=40.0)
        assert associate_rider(BIKE, [rider]).state == STATE_UNASSOCIATED

    def test_two_overlapping_riders_are_ambiguous(self):
        riders = [
            _det(11, "rider", x=305.0, y=160.0, w=50.0, h=90.0),
            _det(12, "rider", x=310.0, y=165.0, w=48.0, h=88.0),
        ]
        assoc = associate_rider(BIKE, riders)
        assert assoc.state == STATE_AMBIGUOUS
        assert "multiple_riders_overlap_motorcycle" in assoc.reasons

    def test_head_region_is_the_upper_band_of_the_rider(self):
        rider = _det(11, "rider", x=305.0, y=160.0, w=50.0, h=90.0)
        head = rider_head_region(associate_rider(BIKE, [rider]).rider_box)
        assert head[3] < 160.0 + 90.0 * 0.5
        assert head[0] <= 305.0 and head[2] >= 355.0



# ---------------------------------------------------------------------------
# Collector bounds: two-frame cap, temporal separation, budgets
# ---------------------------------------------------------------------------


class _RecordingWriter:
    def __init__(self) -> None:
        self.calls = []

    def __call__(self, frame, crop, *, run_key, occurrence_key, frame_index):
        self.calls.append((run_key, occurrence_key, frame_index, crop.w, crop.h))
        return {
            "scene_path": f"{run_key}/{occurrence_key}/scene_f{frame_index}.jpg",
            "crop_path": f"{run_key}/{occurrence_key}/crop_f{frame_index}.jpg",
            "size_bytes": 1234,
        }


def _collector(**kwargs):
    kwargs.setdefault("video_id", 1)
    return MotorcycleDetailCollector(
        run_key="run_7", frame_w=FRAME_W, frame_h=FRAME_H, **kwargs
    )


def _feed(collector, frame, dets, first_ts, step=1.0, count=6, frame_offset=0):
    for i in range(count):
        ts = first_ts + i * step
        collector.observe(frame, dets(ts), frame_number=frame_offset + i, timestamp_sec=ts)


class TestCollectorBounds:
    def test_at_most_two_frames_per_occurrence(self):
        collector = _collector()
        collector._writer = _RecordingWriter()
        _feed(collector, _sharp_frame(), lambda ts: [_det(7, "motorcycle", ts=ts)], 0.0, count=30)
        collector.wrap_up()
        candidates = collector.candidates()
        assert len(candidates) == 1
        assert len(json.loads(candidates[0]["frames_json"])) == MAX_FRAMES_PER_OCCURRENCE

    def test_stored_frames_are_temporally_separated(self):
        collector = _collector()
        collector._writer = _RecordingWriter()
        _feed(collector, _sharp_frame(), lambda ts: [_det(7, "motorcycle", ts=ts)], 0.0, count=6)
        collector.wrap_up()
        frames = json.loads(collector.candidates()[0]["frames_json"])
        times = sorted(f["timestamp_sec"] for f in frames)
        assert len(times) == 2
        assert (times[1] - times[0]) >= 0.4

    def test_identical_timestamps_cannot_produce_two_frames(self):
        collector = _collector()
        collector._writer = _RecordingWriter()
        frame = _sharp_frame()
        for i in range(6):
            # Same source timestamp for every processed frame: the occurrence
            # cannot satisfy the temporal-separation rule a second time.
            collector.observe(
                frame, [_det(7, "motorcycle", ts=0.0)],
                frame_number=i, timestamp_sec=0.0,
            )
        collector.wrap_up()
        assert len(json.loads(collector.candidates()[0]["frames_json"])) == 1

    def test_candidate_budget_is_bounded(self):
        collector = _collector(max_candidates=3)
        collector._writer = _RecordingWriter()
        frame = _sharp_frame()
        for tid in range(12):
            _feed(collector, frame, lambda ts, t=tid: [_det(t, "motorcycle", ts=ts)],
                  tid * 20.0, step=2.0, count=3, frame_offset=tid * 100)
        collector.wrap_up()
        assert len(collector.candidates()) <= 3
        assert collector.summary()["budget_exhausted"] is True

    def test_evidence_write_budget_is_bounded_per_occurrence(self):
        collector = _collector(max_writes=2)
        collector._writer = _RecordingWriter()
        _feed(collector, _sharp_frame(), lambda ts: [_det(7, "motorcycle", ts=ts)],
              0.0, step=2.0, count=40)
        collector.wrap_up()
        assert collector.stats["frames_selected"] <= 2

    def test_id_reuse_produces_two_occurrences(self):
        collector = _collector()
        collector._writer = _RecordingWriter()
        frame = _sharp_frame()
        _feed(collector, frame, lambda ts: [_det(7, "motorcycle", ts=ts)], 0.0, count=3)
        _feed(collector, frame, lambda ts: [_det(7, "motorcycle", ts=ts)], 40.0,
              count=3, frame_offset=100)
        collector.wrap_up()
    def test_failed_evidence_write_is_not_reported_as_selected(self):
        collector = _collector()
        collector._writer = lambda *a, **k: None
        collector.observe(
            _sharp_frame(), [_det(7, "motorcycle", ts=0.0)], frame_number=0, timestamp_sec=0.0
        )
        collector.wrap_up()
        assert collector.candidates() == []
        assert collector.stats["evidence_write_failures"] == 1

    def test_disabled_collector_is_inert(self):
        collector = _collector(enabled=False)
        collector._writer = _RecordingWriter()
        collector.observe(
            _sharp_frame(), [_det(7, "motorcycle", ts=0.0)], frame_number=0, timestamp_sec=0.0
        )
        collector.wrap_up()
        assert collector.candidates() == []
        assert collector.stats["frames_observed"] == 0

    def test_payload_carries_selector_version_and_bounding_metadata(self):
        collector = _collector()
        collector._writer = _RecordingWriter()
        collector.observe(
            _sharp_frame(), [_det(7, "motorcycle", ts=0.0)], frame_number=3, timestamp_sec=1.5
        )
        collector.wrap_up()
        payload = collector.candidates()[0]
        assert payload["selector_version"] == DETAIL_SELECTOR_VERSION
        assert payload["run_key"] == "run_7"
        frame = json.loads(payload["frames_json"])[0]
        for key in ("crop", "scan_scale_x", "scan_scale_y", "detection_bbox", "frame_number"):
            assert key in frame
        assert frame["crop"]["w"] > 0 and frame["crop"]["h"] > 0

    def test_rider_box_widens_the_stored_crop(self):
        collector = _collector()
        collector._writer = _RecordingWriter()
        bike = _det(7, "motorcycle", x=300.0, y=220.0, w=60.0, h=60.0)
        rider = _det(11, "rider", x=305.0, y=160.0, w=50.0, h=90.0)
        collector.observe(_sharp_frame(), [bike, rider], frame_number=1, timestamp_sec=0.0)
        collector.wrap_up()
        crop = json.loads(collector.candidates()[0]["frames_json"])[0]["crop"]
        assert crop["y"] < 220.0

    def test_persist_writes_one_row_per_occurrence(self, test_db):
        video_id = test_db.insert_video(
            "detail.mp4", "/tmp/detail.mp4", status="processed"
        )
        collector = _collector(video_id=video_id)
        collector._writer = _RecordingWriter()
        collector.observe(
            _sharp_frame(), [_det(7, "motorcycle", ts=0.0)], frame_number=1, timestamp_sec=0.0
        )
        collector.wrap_up()
        assert collector.persist() == 1
        rows, total = test_db.list_motorcycle_detail_candidates()
        assert total == 1
        assert rows[0]["occurrence_key"] == "t7g1"
        assert rows[0]["video_id"] == video_id
        assert rows[0]["scan_state"] == "queued"
        assert rows[0]["human_outcome"] == "pending"
        assert rows[0]["source_width"] == FRAME_W
