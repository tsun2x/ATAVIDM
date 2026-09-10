"""Integration remediation §§1–5 regression tests.

Exercises realistic seams: Detector.class_names, upload route + DB,
process_video lifecycle, worker finish_processing_run, and side-mirror
fail-closed evaluation.
"""

from __future__ import annotations

import io
import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

from core.detection_config import (
    NEEDS_DECISION_VIOLATIONS,
    PARTIAL_VIOLATIONS,
    VIOLATION_NO_SIDE_MIRROR,
    VIOLATION_OBSTRUCTION,
    VIOLATION_TRUCK_BAN,
    violation_execution_status,
)
from core.detector import Detector
from core.model_capability import (
    assess_enabled_rules,
    assess_rule_capability,
    classes_satisfy_rule,
    extract_model_class_names,
    normalize_class_set,
)
from core.recording_time import RecordingTimeError, normalize_recorded_at
from core.temporal_evidence import TemporalEvidenceBuffer
from core.violation_config import violation_catalog_for_ui
from core.violation_engine import RuleEngineState, evaluate_detection_rules
from core.video_processor import ProcessVideoError, ProcessVideoResult, process_video


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _vehicle(**kwargs) -> dict[str, Any]:
    base = {
        "track_id": 1,
        "class_label": "car",
        "confidence": 0.95,
        "bbox_x": 100.0,
        "bbox_y": 80.0,
        "bbox_w": 120.0,
        "bbox_h": 80.0,
        "timestamp_sec": 0.0,
        "speed_px": 0.0,
        "is_stationary": True,
    }
    base.update(kwargs)
    return base


def _fake_detector_with_names(names: dict[int, str]) -> Detector:
    det = Detector.__new__(Detector)
    det._weights = "test-weights"
    det.is_custom = False
    det._model = type("FakeModel", (), {"names": names})()
    det.load = lambda: None  # type: ignore[method-assign]
    return det


def _write_tiny_mp4(path: Path, *, frames: int = 30, fps: float = 10.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (64, 48),
    )
    assert writer.isOpened(), "VideoWriter failed to open"
    try:
        for i in range(frames):
            frame = np.zeros((48, 64, 3), dtype=np.uint8)
            frame[:, :] = (i * 7) % 255
            writer.write(frame)
    finally:
        writer.release()


# ---------------------------------------------------------------------------
# §1 — Model capability via Detector.class_names
# ---------------------------------------------------------------------------

class TestModelCapabilityDetectorClassNames:
    def test_class_names_normalized_and_rules_gated(self):
        det = _fake_detector_with_names({0: "Car", 1: "Motorcycle", 2: "person"})
        raw = det.class_names
        assert raw == {"Car", "Motorcycle", "person"}
        extracted = extract_model_class_names(det)
        assert set(extracted) == {"car", "motorcycle", "person"}
        assert normalize_class_set(extracted) == {"car", "motorcycle", "person"}

        # Helmet / side-mirror rules must stay disabled without attribute classes.
        caps = assess_enabled_rules(
            (VIOLATION_NO_SIDE_MIRROR, "No Helmet", VIOLATION_TRUCK_BAN),
            extracted,
        )
        by_name = {c.rule_name: c for c in caps}
        assert by_name[VIOLATION_NO_SIDE_MIRROR].automatic_evaluation is False
        assert by_name["No Helmet"].automatic_evaluation is False
        # Truck ban only needs truck class — still disabled here.
        assert by_name[VIOLATION_TRUCK_BAN].automatic_evaluation is False

    def test_rules_enabled_when_required_classes_present(self):
        det = _fake_detector_with_names(
            {0: "car", 1: "truck", 2: "side_mirror", 3: "motorcycle", 4: "rider", 5: "helmet"}
        )
        names = extract_model_class_names(det)
        assert classes_satisfy_rule(VIOLATION_NO_SIDE_MIRROR, names)
        assert classes_satisfy_rule(VIOLATION_TRUCK_BAN, names)
        assert classes_satisfy_rule("No Helmet", names)
        caps = assess_enabled_rules(
            (VIOLATION_NO_SIDE_MIRROR, VIOLATION_TRUCK_BAN, "No Helmet"),
            names,
        )
        assert all(c.automatic_evaluation for c in caps)

    def test_no_helmet_not_enabled_when_person_substitutes_for_rider(self):
        det = _fake_detector_with_names(
            {0: "motorcycle", 1: "person", 2: "helmet", 3: "car"}
        )
        names = extract_model_class_names(det)
        assert "person" in names
        assert "rider" not in names
        assert not classes_satisfy_rule("No Helmet", names)
        cap = assess_rule_capability("No Helmet", names)
        assert cap.automatic_evaluation is False
        assert any("rider" in item for item in cap.missing_prerequisites)

    def test_malformed_and_unknown_rosters_fail_closed(self):
        empty = _fake_detector_with_names({})
        assert extract_model_class_names(empty) == ()
        cap = assess_rule_capability(VIOLATION_NO_SIDE_MIRROR, ())
        assert cap.automatic_evaluation is False

        weird = _fake_detector_with_names({0: "???", 1: "", 2: "  "})
        names = extract_model_class_names(weird)
        assert not classes_satisfy_rule(VIOLATION_NO_SIDE_MIRROR, names)
        assert not classes_satisfy_rule(VIOLATION_TRUCK_BAN, names)

        nonnumeric = _fake_detector_with_names({"front": "car", "side": "truck"})
        extracted = extract_model_class_names(nonnumeric)
        assert extracted == ()
        assert nonnumeric.class_names == set()

        class Boom:
            def __str__(self) -> str:
                raise RuntimeError("cannot stringify")

        exploding = _fake_detector_with_names({"x": Boom()})
        assert exploding.class_names == set()
        assert extract_model_class_names(exploding) == ()


# ---------------------------------------------------------------------------
# §5 — Side-mirror fail-closed
# ---------------------------------------------------------------------------

class TestSideMirrorFailClosed:
    def _run_nondetection(self, *, confidence: float, bbox_w: float, bbox_h: float, frames: int = 12):
        state = RuleEngineState()
        classes = ("car", "motorcycle", "side_mirror", "person")
        state.capability[VIOLATION_NO_SIDE_MIRROR] = assess_rule_capability(
            VIOLATION_NO_SIDE_MIRROR, classes
        )
        all_events = []
        veh = _vehicle(confidence=confidence, bbox_w=bbox_w, bbox_h=bbox_h)
        for t in range(frames):
            veh["timestamp_sec"] = float(t)
            ev = evaluate_detection_rules(
                [veh],
                state,
                frame_number=t,
                enabled_violations=(VIOLATION_NO_SIDE_MIRROR,),
                model_classes=classes,
            )
            all_events.extend(ev)
        return state, all_events

    def test_repeated_nondetection_remains_unknown(self):
        state, events = self._run_nondetection(confidence=0.99, bbox_w=200, bbox_h=150)
        assert events == []
        ctx = state.contextual.get(("side_mirror", 1), {})
        assert ctx.get("state", "UNKNOWN") == "UNKNOWN"
        assert ("no_side_mirror", 1) not in state.persistence

    def test_large_bbox_does_not_prove_visibility(self):
        state, events = self._run_nondetection(confidence=0.5, bbox_w=400, bbox_h=300)
        assert events == []
        assert state.contextual[("side_mirror", 1)]["state"] == "UNKNOWN"

    def test_high_confidence_does_not_prove_visibility(self):
        state, events = self._run_nondetection(confidence=0.99, bbox_w=40, bbox_h=40)
        assert events == []
        assert state.contextual[("side_mirror", 1)]["state"] == "UNKNOWN"

    def test_cropped_occluded_blurred_orientation_unknown_stay_unknown(self):
        # These flags are informational; without affirmative absence evidence
        # the rule must stay UNKNOWN and emit nothing.
        state = RuleEngineState()
        classes = ("car", "side_mirror")
        state.capability[VIOLATION_NO_SIDE_MIRROR] = assess_rule_capability(
            VIOLATION_NO_SIDE_MIRROR, classes
        )
        for flag in ("cropped", "occluded", "blurred", "orientation_unknown"):
            veh = _vehicle(
                confidence=0.99,
                bbox_w=200,
                bbox_h=150,
                mirror_view=flag,
                timestamp_sec=0.0,
            )
            for t in range(8):
                veh["timestamp_sec"] = float(t)
                ev = evaluate_detection_rules(
                    [veh],
                    state,
                    frame_number=t,
                    enabled_violations=(VIOLATION_NO_SIDE_MIRROR,),
                    model_classes=classes,
                )
                assert ev == []
            assert state.contextual[("side_mirror", 1)]["state"] == "UNKNOWN"

    def test_nondetection_no_persistence_timer_no_review(self):
        state, events = self._run_nondetection(confidence=0.95, bbox_w=200, bbox_h=150, frames=20)
        assert events == []
        assert ("no_side_mirror", 1) not in state.persistence
        assert state.fired == {}

    def test_present_mirror_still_recorded(self):
        state = RuleEngineState()
        classes = ("car", "side_mirror")
        state.capability[VIOLATION_NO_SIDE_MIRROR] = assess_rule_capability(
            VIOLATION_NO_SIDE_MIRROR, classes
        )
        veh = _vehicle(bbox_x=100, bbox_y=80, bbox_w=120, bbox_h=80, confidence=0.9)
        mirror = {
            "track_id": 99,
            "class_label": "side_mirror",
            "confidence": 0.8,
            "bbox_x": 110.0,
            "bbox_y": 85.0,
            "bbox_w": 20.0,
            "bbox_h": 15.0,
            "timestamp_sec": 0.0,
        }
        for t in range(3):
            veh["timestamp_sec"] = float(t)
            mirror["timestamp_sec"] = float(t)
            ev = evaluate_detection_rules(
                [veh, mirror],
                state,
                frame_number=t,
                enabled_violations=(VIOLATION_NO_SIDE_MIRROR,),
                model_classes=classes,
            )
            assert ev == []
        assert state.contextual[("side_mirror", 1)]["state"] == "PRESENT"

    def test_two_associated_sides_record_both_and_emit_nothing(self):
        state = RuleEngineState()
        classes = ("car", "side_mirror")
        state.capability[VIOLATION_NO_SIDE_MIRROR] = assess_rule_capability(
            VIOLATION_NO_SIDE_MIRROR, classes
        )
        veh = _vehicle(bbox_x=100, bbox_y=80, bbox_w=120, bbox_h=80, confidence=0.9)
        left = {
            "track_id": 98,
            "class_label": "side_mirror",
            "confidence": 0.8,
            "bbox_x": 105.0,
            "bbox_y": 85.0,
            "bbox_w": 16.0,
            "bbox_h": 12.0,
            "timestamp_sec": 0.0,
        }
        right = {
            "track_id": 99,
            "class_label": "side_mirror",
            "confidence": 0.8,
            "bbox_x": 190.0,
            "bbox_y": 85.0,
            "bbox_w": 16.0,
            "bbox_h": 12.0,
            "timestamp_sec": 0.0,
        }
        for t in range(3):
            veh["timestamp_sec"] = float(t)
            left["timestamp_sec"] = float(t)
            right["timestamp_sec"] = float(t)
            ev = evaluate_detection_rules(
                [veh, left, right],
                state,
                frame_number=t,
                enabled_violations=(VIOLATION_NO_SIDE_MIRROR,),
                model_classes=classes,
            )
            assert ev == []
        assert state.contextual[("side_mirror", 1)]["state"] == "PRESENT_BOTH"

    def test_one_mirror_never_proves_both_present(self):
        state = RuleEngineState()
        classes = ("car", "side_mirror")
        state.capability[VIOLATION_NO_SIDE_MIRROR] = assess_rule_capability(
            VIOLATION_NO_SIDE_MIRROR, classes
        )
        veh = _vehicle(
            bbox_x=100,
            bbox_y=80,
            bbox_w=120,
            bbox_h=80,
            confidence=0.9,
            mirror_roi_visibility="both_visible",
        )
        mirror = {
            "track_id": 99,
            "class_label": "side_mirror",
            "confidence": 0.8,
            "bbox_x": 110.0,
            "bbox_y": 85.0,
            "bbox_w": 20.0,
            "bbox_h": 15.0,
            "timestamp_sec": 0.0,
        }
        evaluate_detection_rules(
            [veh, mirror],
            state,
            frame_number=0,
            enabled_violations=(VIOLATION_NO_SIDE_MIRROR,),
            model_classes=classes,
        )
        assert state.contextual[("side_mirror", 1)]["state"] == "PRESENT"
        assert state.contextual[("side_mirror", 1)]["state"] != "PRESENT_BOTH"

    def test_one_mirror_with_visibility_can_emit_review(self):
        state = RuleEngineState()
        classes = ("car", "side_mirror")
        state.capability[VIOLATION_NO_SIDE_MIRROR] = assess_rule_capability(
            VIOLATION_NO_SIDE_MIRROR, classes
        )
        veh = _vehicle(
            bbox_x=100,
            bbox_y=80,
            bbox_w=120,
            bbox_h=80,
            confidence=0.9,
            mirror_roi_visibility="both_visible",
        )
        mirror = {
            "track_id": 99,
            "class_label": "side_mirror",
            "confidence": 0.8,
            "bbox_x": 110.0,
            "bbox_y": 85.0,
            "bbox_w": 20.0,
            "bbox_h": 15.0,
            "timestamp_sec": 0.0,
        }
        events = []
        for t in range(5):
            veh["timestamp_sec"] = float(t)
            mirror["timestamp_sec"] = float(t)
            events.extend(
                evaluate_detection_rules(
                    [veh, mirror],
                    state,
                    frame_number=t,
                    enabled_violations=(VIOLATION_NO_SIDE_MIRROR,),
                    model_classes=classes,
                )
            )
        assert state.contextual[("side_mirror", 1)]["state"] == "PRESENT"
        assert any(
            e.violation_type == VIOLATION_NO_SIDE_MIRROR and e.outcome == "review"
            for e in events
        )

    def test_rule_status_partial_and_needs_decision(self, test_db):
        assert VIOLATION_NO_SIDE_MIRROR in PARTIAL_VIOLATIONS
        assert VIOLATION_NO_SIDE_MIRROR in NEEDS_DECISION_VIOLATIONS
        assert violation_execution_status(VIOLATION_NO_SIDE_MIRROR) == "partial"
        # Catalog surface (UI / API metadata)
        entry = next(c for c in violation_catalog_for_ui() if c["name"] == VIOLATION_NO_SIDE_MIRROR)
        assert entry["partial"] is True
        assert entry["needs_decision"] is True
        assert entry["status"] == "partial"


# ---------------------------------------------------------------------------
# §3 — recorded_at must not invent upload time
# ---------------------------------------------------------------------------

class TestRecordedAtNormalization:
    def test_blank_is_null(self):
        assert normalize_recorded_at(None) is None
        assert normalize_recorded_at("") is None
        assert normalize_recorded_at("   ") is None

    def test_valid_formats(self):
        assert normalize_recorded_at("2026-08-20 14:30:00") == "2026-08-20 14:30:00"
        assert normalize_recorded_at("2026-08-20T14:30:00") == "2026-08-20 14:30:00"
        assert normalize_recorded_at("2026-08-20") == "2026-08-20 00:00:00"

    def test_invalid_raises(self):
        with pytest.raises(RecordingTimeError):
            normalize_recorded_at("not-a-date")
        with pytest.raises(RecordingTimeError):
            normalize_recorded_at("2026/08/20")


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("werkzeug") is None,
    reason="werkzeug not available",
)
class TestUploadRecordedAtRoute:
    def test_omitted_recorded_at_is_null_not_upload_time(
        self, enforcer_client, test_db, tmp_path, monkeypatch
    ):
        import app as app_module
        from core import upload as upload_mod

        monkeypatch.setattr(upload_mod, "check_disk_space", lambda *a, **k: None)
        monkeypatch.setattr(upload_mod.config, "UPLOAD_FOLDER", str(tmp_path))
        monkeypatch.setattr(
            app_module,
            "extract_first_frame",
            lambda *a, **k: str(tmp_path / "frame.jpg"),
        )

        before = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        data = b"\x00\x00fake-mp4-bytes"
        resp = enforcer_client.post(
            "/api/upload-video",
            data={
                "video": (io.BytesIO(data), "clip.mp4"),
                "condition": "peak",
            },
            content_type="multipart/form-data",
        )
        body = resp.get_json()
        assert resp.status_code == 200, body
        assert body["success"] is True
        assert body["recording_time_known"] is False
        vid = body["video"]["db_id"]
        row = test_db.get_video(vid)
        assert row is not None
        assert row["recorded_at"] is None
        # Must not equal "now" inventively (upload time).
        assert row["recorded_at"] != before

    def test_valid_recorded_at_persisted(self, enforcer_client, test_db, tmp_path, monkeypatch):
        import app as app_module
        from core import upload as upload_mod

        monkeypatch.setattr(upload_mod, "check_disk_space", lambda *a, **k: None)
        monkeypatch.setattr(upload_mod.config, "UPLOAD_FOLDER", str(tmp_path))
        monkeypatch.setattr(
            app_module,
            "extract_first_frame",
            lambda *a, **k: str(tmp_path / "frame.jpg"),
        )
        resp = enforcer_client.post(
            "/api/upload-video",
            data={
                "video": (io.BytesIO(b"\x00mp4"), "clip.mp4"),
                "condition": "peak",
                "recorded_at": "2026-01-15 08:30:00",
            },
            content_type="multipart/form-data",
        )
        body = resp.get_json()
        assert resp.status_code == 200, body
        assert body["recording_time_known"] is True
        row = test_db.get_video(body["video"]["db_id"])
        assert row is not None
        assert row["recorded_at"] == "2026-01-15 08:30:00"

    def test_invalid_recorded_at_http_400(self, enforcer_client, tmp_path, monkeypatch):
        from core import upload as upload_mod

        monkeypatch.setattr(upload_mod, "check_disk_space", lambda *a, **k: None)
        monkeypatch.setattr(upload_mod.config, "UPLOAD_FOLDER", str(tmp_path))
        resp = enforcer_client.post(
            "/api/upload-video",
            data={
                "video": (io.BytesIO(b"\x00mp4"), "clip.mp4"),
                "condition": "peak",
                "recorded_at": "yesterday",
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400
        assert resp.get_json()["success"] is False


# ---------------------------------------------------------------------------
# §2 — Temporal evidence ↔ review row (process_video seam)
# ---------------------------------------------------------------------------

class TestTemporalEvidenceProcessVideoLifecycle:
    def test_long_episode_spill_survives_ring_rotation(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.temporal_evidence.EVIDENCE_FOLDER", str(tmp_path / "evidence"))
        buf = TemporalEvidenceBuffer(source_key="video_spill", fps=5.0, max_frames=8)
        for i in range(5):
            buf.push(np.zeros((32, 32, 3), dtype=np.uint8), i, i / 5.0)
        ep = buf.begin_episode(
            violation_type=VIOLATION_OBSTRUCTION,
            track_id=1,
            confirmed_at_sec=0.8,
            episode_start_sec=0.0,
            review_id=42,
        )
        # Continue far past ring capacity while episode stays open.
        for i in range(5, 40):
            buf.push(np.zeros((32, 32, 3), dtype=np.uint8), i, i / 5.0)
        assert len(ep.spill) > len(buf._frames)
        assert ep.spill[0].frame_number == 0 or ep.spill[0].timestamp_sec <= 0.8
        buf.end_episode(VIOLATION_OBSTRUCTION, 1, episode_end_sec=7.0)
        # Advance past post-roll
        for i in range(40, 60):
            finalized = buf.push(np.zeros((32, 32, 3), dtype=np.uint8), i, i / 5.0)
            if finalized:
                assert finalized[0].review_id == 42
                assert finalized[0].sequence_dir is not None
                break
        else:
            finalized = buf.finalize_all(12.0)
            assert finalized and finalized[0].review_id == 42

    def test_process_video_writes_clip_back_to_review_row(
        self, test_db, tmp_path, monkeypatch
    ):
        from core.violation_engine import ViolationEvent
        from core.rule_confidence import score_from_persistence
        from core.detection_config import VIOLATION_PERSISTENCE_SEC

        video_path = tmp_path / "scene.mp4"
        _write_tiny_mp4(video_path, frames=50, fps=10.0)
        monkeypatch.setattr("core.temporal_evidence.EVIDENCE_FOLDER", str(tmp_path / "evidence"))
        monkeypatch.setattr("core.evidence.EVIDENCE_FOLDER", str(tmp_path / "evidence"))

        vid = test_db.insert_video(
            filename="scene.mp4",
            filepath=str(video_path),
            status="ready",
            recorded_at=None,
        )
        test_db.upsert_annotation(vid, json.dumps({"active_lane": [[0, 0], [64, 0], [64, 48], [0, 48]]}))

        class StubDetector:
            def load(self):
                return None

            @property
            def class_names(self):
                return {"car"}

            def track_frame(self, frame, conf=0.6, timestamp_sec=0.0):
                return [
                    {
                        "track_id": 7,
                        "class_label": "car",
                        "confidence": 0.9,
                        "bbox_x": 10.0,
                        "bbox_y": 10.0,
                        "bbox_w": 30.0,
                        "bbox_h": 20.0,
                        "timestamp_sec": timestamp_sec,
                    }
                ]

        fired = {"done": False}

        def fake_evaluate(*args, **kwargs):
            if fired["done"]:
                return []
            fired["done"] = True
            score = score_from_persistence(
                detection_confidence=0.9,
                elapsed_sec=VIOLATION_PERSISTENCE_SEC,
                required_sec=VIOLATION_PERSISTENCE_SEC,
            )
            return [
                ViolationEvent(
                    violation_type=VIOLATION_OBSTRUCTION,
                    track_id=7,
                    confidence=score.violation_confidence,
                    frame_number=0,
                    timestamp_sec=0.0,
                    reason_log="test obstruction",
                    vehicle_class="car",
                    detection_confidence=0.9,
                    violation_confidence=score.violation_confidence,
                    evidence_sufficiency=score.evidence_sufficiency,
                    contributing_factors=dict(score.contributing_factors),
                    unavailable_factors=tuple(score.unavailable_factors),
                )
            ]

        monkeypatch.setattr("core.video_processor.Detector", StubDetector)
        monkeypatch.setattr("core.video_processor.evaluate_detection_rules", fake_evaluate)
        monkeypatch.setattr(
            "core.video_processor.load_rule_parameters",
            lambda: {"frame_skip": 1, "confidence_threshold": 0.5},
        )

        result = process_video(vid, enabled_violations=(VIOLATION_OBSTRUCTION,))
        assert isinstance(result, ProcessVideoResult)
        assert result.diagnostics_json is not None
        assert result.geometry_snapshot_json is not None
        assert len(result.events) == 1

        reviews, total = test_db.list_review_queue(status="pending", page=1, per_page=50)
        assert total == 1
        item = test_db.get_review_item(reviews[0]["id"])
        assert item is not None
        assert item.get("evidence_sequence_dir") or item.get("evidence_clip_path")
        assert item.get("episode_end_sec") is not None
        assert item.get("evidence_pre_sec") == 6.0
        assert item.get("evidence_post_sec") == 3.0


# ---------------------------------------------------------------------------
# §4 — Worker persists diagnostics / geometry
# ---------------------------------------------------------------------------

class TestWorkerPersistsDiagnostics:
    def test_completed_run_stores_diagnostics(self, enforcer_client, test_db, monkeypatch):
        import app as app_module

        def fake_process(video_id, enabled_violations=None, **kwargs):
            return ProcessVideoResult(
                events=[],
                diagnostics_json=json.dumps({"model_classes": ["car"], "notes": ["ok"]}),
                geometry_snapshot_json=json.dumps({"mode": "normalized", "frame_w": 64}),
            )

        monkeypatch.setattr(app_module, "process_video", fake_process)
        zones = json.dumps({"z": [[0, 0], [1, 0], [1, 1], [0, 1]]})
        vid = test_db.insert_video(filename="c.mp4", filepath="/tmp/c.mp4", status="ready")
        test_db.upsert_annotation(vid, zones)

        resp = enforcer_client.post(
            f"/api/videos/{vid}/process",
            json={"enabled_violations": []},
            content_type="application/json",
        )
        assert resp.status_code == 200, resp.get_json()
        # Wait for worker
        deadline = time.time() + 5.0
        run = None
        while time.time() < deadline:
            runs = test_db.list_processing_runs(vid)
            if runs and runs[0]["status"] in ("completed", "failed"):
                run = runs[0]
                break
            time.sleep(0.05)
        assert run is not None
        assert run["status"] == "completed"
        assert run["diagnostics_json"]
        assert "car" in run["diagnostics_json"]
        assert run["geometry_snapshot_json"]
        assert "normalized" in run["geometry_snapshot_json"]

    def test_failed_run_preserves_partial_diagnostics(self, enforcer_client, test_db, monkeypatch):
        import app as app_module

        def fake_process(video_id, enabled_violations=None, **kwargs):
            raise ProcessVideoError(
                "boom",
                diagnostics_json=json.dumps({"notes": ["partial"]}),
                geometry_snapshot_json=json.dumps({"mode": "legacy_fallback"}),
            )

        monkeypatch.setattr(app_module, "process_video", fake_process)
        zones = json.dumps({"z": [[0, 0], [1, 0], [1, 1], [0, 1]]})
        vid = test_db.insert_video(filename="c.mp4", filepath="/tmp/fail.mp4", status="ready")
        test_db.upsert_annotation(vid, zones)

        resp = enforcer_client.post(
            f"/api/videos/{vid}/process",
            json={"enabled_violations": []},
            content_type="application/json",
        )
        assert resp.status_code == 200
        deadline = time.time() + 5.0
        run = None
        while time.time() < deadline:
            runs = test_db.list_processing_runs(vid)
            if runs and runs[0]["status"] in ("completed", "failed"):
                run = runs[0]
                break
            time.sleep(0.05)
        assert run is not None
        assert run["status"] == "failed"
        assert run["error_message"] == "boom"
        assert "partial" in (run["diagnostics_json"] or "")
        assert "legacy_fallback" in (run["geometry_snapshot_json"] or "")
