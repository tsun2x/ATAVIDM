"""Regression tests for violation-engine remediation (2026-08)."""

from __future__ import annotations

import json
from datetime import time as dtime

import numpy as np
import pytest

from core.detection_config import (
    CANONICAL_VIOLATIONS,
    DEFAULT_ENABLED_VIOLATIONS,
    ENABLED_VIOLATIONS_SETTING_KEY,
    IMPLEMENTED_VIOLATIONS,
    PARTIAL_VIOLATIONS,
    PLANNED_VIOLATIONS,
    VIOLATION_COUNTERFLOW,
    VIOLATION_ILLEGAL_PARKING,
    VIOLATION_ILLEGAL_TERMINAL,
    VIOLATION_NO_HELMET,
    VIOLATION_NO_SIDE_MIRROR,
    VIOLATION_OBSTRUCTION,
    VIOLATION_SUBSTANDARD_HELMET,
    VIOLATION_TRUCK_BAN,
)
from core.geometry_profile import GeometryMode, build_geometry_profile
from core.model_capability import (
    assess_rule_capability,
    classes_satisfy_rule,
    missing_classes_for_rule,
)
from core.rule_confidence import score_violation
from core.temporal_evidence import TemporalEvidenceBuffer
from core.tracker import TRACK_EXPIRY_SEC
from core.violation_config import load_enabled_violations, save_enabled_violations
from core.violation_engine import RuleEngineState, evaluate_detection_rules
from core.zone_membership import (
    POLICY_STATIONARY,
    MembershipHysteresis,
    evaluate_zone_membership,
    road_contact_footprint,
)


def _vehicle(**kwargs):
    base = {
        "class_label": "car",
        "track_id": 1,
        "bbox_x": 100.0,
        "bbox_y": 80.0,
        "bbox_w": 40.0,
        "bbox_h": 30.0,
        "confidence": 0.95,
        "timestamp_sec": 0.0,
        "speed_px_per_sec": 0.0,
        "direction_degrees": 90.0,
    }
    base.update(kwargs)
    return base


class TestCanonicalRoster:
    def test_exactly_twelve_unique_names(self):
        assert len(CANONICAL_VIOLATIONS) == 12
        assert len(set(CANONICAL_VIOLATIONS)) == 12

    def test_parking_terminal_separate(self):
        assert VIOLATION_ILLEGAL_PARKING in CANONICAL_VIOLATIONS
        assert VIOLATION_ILLEGAL_TERMINAL in CANONICAL_VIOLATIONS
        assert VIOLATION_ILLEGAL_PARKING != VIOLATION_ILLEGAL_TERMINAL
        assert "Illegal Parking / Illegal Terminal" not in CANONICAL_VIOLATIONS

    def test_status_partitions_cover_all(self):
        covered = set(IMPLEMENTED_VIOLATIONS) | set(PARTIAL_VIOLATIONS) | set(PLANNED_VIOLATIONS)
        assert covered == set(CANONICAL_VIOLATIONS)


class TestDualConfidence:
    def test_detection_not_copied_as_violation(self):
        score = score_violation(
            detection_confidence=0.99,
            persistence_ratio=0.2,
            geometry_stability=0.2,
            association_quality=0.2,
            contextual_availability=0.2,
        )
        assert score.detection_confidence == pytest.approx(0.99)
        assert score.violation_confidence < 0.99
        assert "detection_reliability" in score.contributing_factors
        assert score.violation_confidence != score.detection_confidence

    def test_event_exposes_both(self):
        zone = [[50, 50], [250, 50], [250, 200], [50, 200]]
        state = RuleEngineState()
        all_events = []
        for t in (0.0, 0.5, 1.0, 1.5, 11.0):
            det = _vehicle(timestamp_sec=t, speed_px_per_sec=0.0, confidence=0.99)
            all_events.extend(
                evaluate_detection_rules(
                    [det],
                    state,
                    frame_number=int(t * 10),
                    zones={"active_lane": zone},
                    params={"obstruction_dwell_sec": 1.0, "stationary_px": 8.0},
                    enabled_violations=(VIOLATION_OBSTRUCTION,),
                    model_classes=("car", "truck", "motorcycle", "person"),
                )
            )
        assert all_events
        ev = all_events[0]
        assert ev.detection_confidence == pytest.approx(0.99)
        assert ev.violation_confidence == ev.confidence
        assert "detection_reliability" in ev.contributing_factors
        assert ev.contributing_factors


class TestTrackStateExpiry:
    def test_track_reuse_does_not_inherit_fired_state(self):
        """Required regression: track 7 fires, expires, reappears — no stale fire."""
        zone = [[50, 50], [250, 50], [250, 200], [50, 200]]
        clock = {"t": 0.0}
        state = RuleEngineState(track_expiry_sec=TRACK_EXPIRY_SEC, clock=lambda: clock["t"])
        params = {"obstruction_dwell_sec": 0.5, "stationary_px": 8.0}

        # Satisfy rule at time 0..1
        for t in (0.0, 0.6, 1.2):
            clock["t"] = t
            ev = evaluate_detection_rules(
                [_vehicle(track_id=7, timestamp_sec=t, speed_px_per_sec=0.0)],
                state,
                frame_number=int(t * 10),
                zones={"active_lane": zone},
                params=params,
                enabled_violations=(VIOLATION_OBSTRUCTION,),
                model_classes=("car",),
            )
        assert any(e.track_id == 7 for e in ev) or state.already_fired(VIOLATION_OBSTRUCTION, 7)

        # No observations until after expiry
        clock["t"] = 1.2 + TRACK_EXPIRY_SEC + 1.0
        state.prune_expired(clock["t"])
        assert not state.already_fired(VIOLATION_OBSTRUCTION, 7)
        assert ("obstruction", 7) not in state.persistence

        # Track ID 7 reappears at time 100 — must not fire immediately from stale state
        clock["t"] = 100.0
        immediate = evaluate_detection_rules(
            [_vehicle(track_id=7, timestamp_sec=100.0, speed_px_per_sec=0.0)],
            state,
            frame_number=1000,
            zones={"active_lane": zone},
            params=params,
            enabled_violations=(VIOLATION_OBSTRUCTION,),
            model_classes=("car",),
        )
        assert immediate == []


class TestZoneMembership:
    def test_fully_inside(self):
        zone = [[0, 0], [200, 0], [200, 200], [0, 200]]
        det = _vehicle(bbox_x=50, bbox_y=50, bbox_w=40, bbox_h=40)
        r = evaluate_zone_membership(det, zone, policy=POLICY_STATIONARY)
        assert r.state.value == "INSIDE"
        assert r.overlap_ratio >= 0.4

    def test_fully_outside(self):
        zone = [[0, 0], [50, 0], [50, 50], [0, 50]]
        det = _vehicle(bbox_x=200, bbox_y=200, bbox_w=40, bbox_h=40)
        r = evaluate_zone_membership(det, zone)
        assert r.state.value == "OUTSIDE"
        assert r.overlap_ratio < 0.05

    def test_partial_overlap(self):
        zone = [[0, 0], [120, 0], [120, 200], [0, 200]]
        det = _vehicle(bbox_x=80, bbox_y=50, bbox_w=80, bbox_h=60)
        r = evaluate_zone_membership(det, zone)
        assert 0.05 < r.overlap_ratio < 1.0

    def test_bottom_center_outside_but_footprint_inside(self):
        # Zone covers lower footprint area; bottom-center is just outside.
        zone = [[90, 100], [150, 100], [150, 130], [90, 130]]
        det = _vehicle(bbox_x=100, bbox_y=80, bbox_w=40, bbox_h=40)
        # bottom center at (120, 120) — inside; shift zone so bottom center out
        zone = [[90, 95], [150, 95], [150, 115], [90, 115]]
        # bottom center y=120 is outside zone top=95..115; footprint still overlaps
        r = evaluate_zone_membership(det, zone, policy=POLICY_STATIONARY)
        assert r.anchor_inside is False
        assert r.overlap_ratio > 0.0

    def test_bottom_center_inside_negligible_overlap(self):
        # Tiny zone around bottom center only — footprint mostly outside.
        det = _vehicle(bbox_x=100, bbox_y=50, bbox_w=80, bbox_h=100)
        # bottom center ≈ (140, 150)
        zone = [[138, 148], [142, 148], [142, 152], [138, 152]]
        r = evaluate_zone_membership(det, zone)
        assert r.anchor_inside is True
        assert r.overlap_ratio < 0.35
        # Must not classify INSIDE merely because anchor is inside.
        assert r.state.value in ("OUTSIDE", "BOUNDARY_UNKNOWN")

    def test_boundary_jitter_hysteresis(self):
        zone = [[0, 0], [130, 0], [130, 200], [0, 200]]
        hyst = MembershipHysteresis()
        det = _vehicle(bbox_x=90, bbox_y=50, bbox_w=50, bbox_h=40)
        states = []
        for _ in range(8):
            # Nudge bbox across boundary
            det["bbox_x"] = 90 + (2 if len(states) % 2 == 0 else -2)
            r = evaluate_zone_membership(
                det,
                zone,
                policy=POLICY_STATIONARY,
                hysteresis=hyst,
                hysteresis_key=("test", 1),
            )
            states.append(r.state.value)
        # Hysteresis should not flip every frame
        flips = sum(1 for a, b in zip(states, states[1:]) if a != b)
        assert flips <= 3

    def test_resolution_normalized_footprint(self):
        det_hd = _vehicle(bbox_x=100, bbox_y=100, bbox_w=40, bbox_h=40)
        fp = road_contact_footprint(det_hd)
        assert len(fp) == 4
        assert fp[0][1] > float(det_hd["bbox_y"])


class TestGeometryProfile:
    def test_equivalent_normalized_at_different_resolutions(self):
        p1 = build_geometry_profile(1920, 1080, rule_params={"stationary_px": 8.0, "min_direction_px": 40.0})
        p2 = build_geometry_profile(1280, 720, rule_params={"stationary_px": 8.0, "min_direction_px": 40.0})
        assert p1.mode == GeometryMode.NORMALIZED
        assert p2.mode == GeometryMode.NORMALIZED
        assert p1.stationary_norm_per_sec == pytest.approx(p2.stationary_norm_per_sec)
        assert p1.min_direction_norm == pytest.approx(p2.min_direction_norm)
        # Pixel thresholds scale with frame diagonal
        assert p1.stationary_px_per_sec() != pytest.approx(p2.stationary_px_per_sec())
        ratio = p1.diagonal_px / p2.diagonal_px
        assert p1.stationary_px_per_sec() / p2.stationary_px_per_sec() == pytest.approx(ratio)

    def test_no_physical_without_calibration(self):
        p = build_geometry_profile(640, 480)
        assert not p.can_report_physical()
        assert "Physical" in p.diagnostic or "normalized" in p.diagnostic.lower()


class TestModelCapabilityGate:
    def test_acceptable_helmet_not_no_helmet(self):
        state = RuleEngineState()
        tracked = [
            _vehicle(
                class_label="motorcycle",
                track_id=1,
                bbox_x=100,
                bbox_y=100,
                bbox_w=50,
                bbox_h=40,
                timestamp_sec=0.0,
            ),
            _vehicle(
                class_label="person",
                track_id=2,
                bbox_x=110,
                bbox_y=90,
                bbox_w=30,
                bbox_h=40,
                confidence=0.9,
                timestamp_sec=0.0,
            ),
            _vehicle(
                class_label="helmet_acceptable",
                track_id=3,
                bbox_x=115,
                bbox_y=85,
                bbox_w=20,
                bbox_h=15,
                timestamp_sec=0.0,
            ),
        ]
        classes = (
            "motorcycle",
            "person",
            "helmet_acceptable",
            "helmet_nut_shell",
        )
        for t in (0.0, 1.0, 2.0):
            for d in tracked:
                d["timestamp_sec"] = t
            ev = evaluate_detection_rules(
                tracked,
                state,
                frame_number=int(t * 10),
                zones={},
                enabled_violations=(VIOLATION_NO_HELMET, VIOLATION_SUBSTANDARD_HELMET),
                model_classes=classes,
            )
        assert all(e.violation_type != VIOLATION_NO_HELMET for e in ev)

    def test_missing_helmet_classes_disable_no_helmet(self):
        coco = ("person", "bicycle", "car", "motorcycle", "bus", "truck")
        assert not classes_satisfy_rule(VIOLATION_NO_HELMET, coco)
        cap = assess_rule_capability(VIOLATION_NO_HELMET, coco)
        assert cap.automatic_evaluation is False
        assert any("helmet" in m for m in cap.missing_prerequisites)

        state = RuleEngineState()
        state.capability[VIOLATION_NO_HELMET] = cap
        tracked = [
            _vehicle(class_label="motorcycle", track_id=1, timestamp_sec=0),
            _vehicle(class_label="person", track_id=2, bbox_x=110, bbox_y=90, bbox_w=30, bbox_h=40, timestamp_sec=0),
        ]
        for t in (0.0, 2.0):
            for d in tracked:
                d["timestamp_sec"] = t
            ev = evaluate_detection_rules(
                tracked,
                state,
                frame_number=1,
                enabled_violations=(VIOLATION_NO_HELMET,),
                model_classes=coco,
            )
        assert ev == []

    def test_missing_side_mirror_class_no_events(self):
        classes = ("car", "motorcycle", "person")
        assert missing_classes_for_rule(VIOLATION_NO_SIDE_MIRROR, classes)
        state = RuleEngineState()
        state.capability[VIOLATION_NO_SIDE_MIRROR] = assess_rule_capability(
            VIOLATION_NO_SIDE_MIRROR, classes
        )
        ev = evaluate_detection_rules(
            [_vehicle(timestamp_sec=0, confidence=0.95, bbox_w=80, bbox_h=60)],
            state,
            frame_number=1,
            enabled_violations=(VIOLATION_NO_SIDE_MIRROR,),
            model_classes=classes,
        )
        assert ev == []

    def test_pickup_truck_distinct_from_truck(self):
        zone = [[0, 0], [300, 0], [300, 300], [0, 300]]
        state = RuleEngineState()
        pickup = _vehicle(class_label="pickup_truck", track_id=9, timestamp_sec=0.0)
        for t in (0.0, 1.0, 2.0):
            pickup["timestamp_sec"] = t
            ev = evaluate_detection_rules(
                [pickup],
                state,
                frame_number=int(t * 10),
                zones={"truck_ban_zone": zone},
                params={"truck_ban_start": "00:00", "truck_ban_end": "23:59"},
                now_time=dtime(8, 0),
                recording_time_known=True,
                enabled_violations=(VIOLATION_TRUCK_BAN,),
                model_classes=("truck", "pickup_truck", "car"),
            )
        assert all(e.violation_type != VIOLATION_TRUCK_BAN for e in ev)


class TestTruckBanRecordingTime:
    def test_unknown_recording_time_fail_closed(self):
        zone = [[0, 0], [300, 0], [300, 300], [0, 300]]
        state = RuleEngineState()
        truck = _vehicle(class_label="truck", track_id=1, timestamp_sec=0.0)
        all_events = []
        for t in (0.0, 1.0, 2.0):
            truck["timestamp_sec"] = t
            all_events.extend(
                evaluate_detection_rules(
                    [truck],
                    state,
                    frame_number=1,
                    zones={"truck_ban_zone": zone},
                    params={"truck_ban_start": "00:00", "truck_ban_end": "23:59"},
                    now_time=None,
                    recording_time_known=False,
                    enabled_violations=(VIOLATION_TRUCK_BAN,),
                    model_classes=("truck",),
                )
            )
        assert all_events == []
        cap = state.capability.get(VIOLATION_TRUCK_BAN)
        assert cap is not None
        assert cap.automatic_evaluation is False
        assert any("recording_datetime" in p for p in cap.missing_prerequisites)


class TestIllegalParkingTerminalSeparate:
    def test_stop_alone_does_not_prove_terminal(self):
        zone = [[0, 0], [300, 0], [300, 300], [0, 300]]
        state = RuleEngineState()
        jeep = _vehicle(class_label="jeepney", track_id=1, speed_px_per_sec=0.0)
        for t in (0.0, 5.0, 10.0):
            jeep["timestamp_sec"] = t
            ev = evaluate_detection_rules(
                [jeep],
                state,
                frame_number=int(t),
                zones={"loading_unloading": zone},
                params={"loading_dwell_sec": 1.0, "stationary_px": 8.0},
                enabled_violations=(VIOLATION_ILLEGAL_TERMINAL,),
                model_classes=("jeepney", "car"),
            )
        # Without passenger-activity evidence → UNKNOWN → no fire
        assert ev == []

    def test_parking_does_not_emit_fused_name(self):
        zone = [[0, 0], [300, 0], [300, 300], [0, 300]]
        state = RuleEngineState()
        car = _vehicle(speed_px_per_sec=0.0)
        for t in (0.0, 15.0, 35.0):
            car["timestamp_sec"] = t
            ev = evaluate_detection_rules(
                [car],
                state,
                frame_number=int(t),
                zones={"no_parking": zone},
                params={"parking_dwell_sec": 1.0, "stationary_px": 8.0},
                enabled_violations=(VIOLATION_ILLEGAL_PARKING,),
                model_classes=("car",),
            )
        for e in ev:
            assert e.violation_type == VIOLATION_ILLEGAL_PARKING
            assert "/" not in e.violation_type or "Nut-Shell" in e.violation_type


class TestEnabledEmptyList:
    def test_explicit_empty_list_stays_disabled(self, monkeypatch):
        store: dict[str, str] = {}

        monkeypatch.setattr(
            "core.violation_config.db.get_setting",
            lambda key: store.get(key),
        )
        monkeypatch.setattr(
            "core.violation_config.db.set_settings",
            lambda values: store.update(values),
        )
        saved = save_enabled_violations([])
        assert saved == ()
        assert json.loads(store[ENABLED_VIOLATIONS_SETTING_KEY]) == []
        loaded = load_enabled_violations()
        assert loaded == ()
        assert loaded != DEFAULT_ENABLED_VIOLATIONS


class TestTemporalEvidence:
    def test_ring_buffer_finalize(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.temporal_evidence.EVIDENCE_FOLDER", str(tmp_path / "evidence"))
        buf = TemporalEvidenceBuffer(source_key="video_test", fps=5.0, max_frames=40)
        for i in range(20):
            frame = np.zeros((48, 64, 3), dtype=np.uint8)
            frame[:] = (i * 10) % 255
            buf.push(frame, frame_number=i, timestamp_sec=i / 5.0)
        buf.begin_episode(
            violation_type=VIOLATION_OBSTRUCTION,
            track_id=1,
            confirmed_at_sec=2.0,
            episode_start_sec=1.0,
        )
        buf.end_episode(VIOLATION_OBSTRUCTION, 1, episode_end_sec=2.5)
        ep = buf.finalize_episode(VIOLATION_OBSTRUCTION, 1, now_sec=3.0)
        assert ep is not None
        assert ep.finalized
        assert ep.sequence_dir is not None
        assert ep.clip_path is not None or ep.sequence_dir is not None

    def test_rejects_traversal(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.temporal_evidence.EVIDENCE_FOLDER", str(tmp_path / "evidence"))
        buf = TemporalEvidenceBuffer(source_key="../etc/passwd", fps=5.0)
        assert ".." not in buf.source_key
        assert "/" not in buf.source_key
