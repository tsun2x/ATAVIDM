"""Guard same-event preferred-frame ranking against attractive off-event frames."""

from core.temporal_evidence import select_preferred_evidence_frame
from core.temporal_evidence import TemporalEvidenceBuffer
from core.temporal_evidence import active_sustained_event_keys
import json
from pathlib import Path
import numpy as np
import cv2


def test_selector_prefers_useful_in_event_frame_and_keeps_ties_deterministic():
    # Regression: selecting only the highest detector confidence can choose a
    # blurred or context-free frame, and a sharp preroll frame is not event proof.
    candidates = [
        {"frame_number": 42, "timestamp_sec": 8.0, "track_id": 7,
         "sharpness": 10, "area_fraction": 0.02, "fully_visible": False,
         "detection_confidence": 0.99},
        {"frame_number": 43, "timestamp_sec": 8.1, "track_id": 7,
         "sharpness": 240, "area_fraction": 0.12, "fully_visible": True,
         "detection_confidence": 0.78},
        {"frame_number": 41, "timestamp_sec": 6.0, "track_id": 7,
         "sharpness": 900, "area_fraction": 0.30, "fully_visible": True,
         "detection_confidence": 1.0},
    ]
    selected, reason = select_preferred_evidence_frame(
        candidates, track_id=7, episode_start_sec=7.5, episode_end_sec=8.5,
        confirmed_at_sec=8.0,
    )
    assert selected["frame_number"] == 43
    assert "sharpness" in reason and "event window" in reason

    tied = [dict(candidates[1], frame_number=45), dict(candidates[1], frame_number=44)]
    selected, _ = select_preferred_evidence_frame(
        tied, track_id=7, episode_start_sec=7.5, episode_end_sec=8.5,
        confirmed_at_sec=8.0,
    )
    assert selected["frame_number"] == 44


def test_selector_returns_no_frame_without_matching_track_or_event_bounds():
    selected, reason = select_preferred_evidence_frame(
        [{"frame_number": 3, "timestamp_sec": 4.0, "track_id": 8,
          "sharpness": 500, "area_fraction": 0.2, "fully_visible": True,
          "detection_confidence": 0.99}],
        track_id=7, episode_start_sec=3.0, episode_end_sec=5.0,
        confirmed_at_sec=4.0,
    )
    assert selected is None
    assert "unavailable" in reason.lower()


def test_finalizer_writes_preferred_frame_sidecar_without_replacing_trigger_evidence(
    tmp_path, monkeypatch
):
    # Regression: temporal selection may remain only an in-memory suggestion or
    # overwrite the event proof; it must be a separate durable sidecar.
    import core.temporal_evidence as temporal

    monkeypatch.setattr(temporal, "EVIDENCE_FOLDER", tmp_path / "evidence")
    buffer = TemporalEvidenceBuffer(source_key="video_4", fps=1, run_id=31)
    blurred = np.zeros((64, 64, 3), dtype=np.uint8)
    detailed = np.indices((64, 64)).sum(axis=0).astype(np.uint8) % 2 * 255
    detailed = np.repeat(detailed[:, :, None], 3, axis=2)
    buffer.push(blurred, 10, 1.0)
    episode = buffer.begin_episode(
        violation_type="Counterflow", track_id=7, confirmed_at_sec=1.0,
        episode_start_sec=1.0, review_id=2, track_identity_epoch=3,
    )
    detection1 = {"track_id": 7, "track_identity_epoch": 3, "bbox_x": 8, "bbox_y": 8, "bbox_w": 40, "bbox_h": 40, "confidence": .99}
    buffer.set_frame_annotations(10, [detection1], {}, None, {7}, preferred_event_keys={("Counterflow", 7)})
    buffer.push(detailed, 11, 1.5)
    detection2 = {"track_id": 7, "track_identity_epoch": 3, "bbox_x": 8, "bbox_y": 8, "bbox_w": 40, "bbox_h": 40, "confidence": .78}
    # Production rule evaluation has already emitted once; overlay IDs are empty.
    buffer.set_frame_annotations(11, [detection2], {}, None, set(), preferred_event_keys={("Counterflow", 7)})
    buffer.end_episode("Counterflow", 7, 1.5)
    finalized = buffer.finalize_episode("Counterflow", 7, now_sec=1.5, write_clip=False)

    sequence_dir = Path(temporal.EVIDENCE_FOLDER) / "video_4" / "run_31" / Path(finalized.sequence_dir).name
    preferred = sequence_dir / "preferred_scene.jpg"
    original = sequence_dir / "frame_0000_n000010.jpg"
    metadata = json.loads((sequence_dir / "preferred_scene.json").read_text(encoding="utf-8"))
    identity = json.loads((sequence_dir / "event_identity.json").read_text(encoding="utf-8"))
    assert finalized is episode and finalized.finalized
    assert preferred.is_file() and original.is_file()
    assert metadata["frame_number"] == 11
    assert metadata["track_identity_epoch"] == 3
    assert identity == {
        "version": 1,
        "video_id": 4,
        "processing_run_id": 31,
        "review_id": 2,
        "track_id": 7,
        "violation_type": "Counterflow",
        "track_identity_epoch": 3,
    }
    assert "sharpness" in metadata["selection_reason"]
    assert cv2.imread(str(preferred)).shape == (64, 64, 3)


def test_production_deduplicated_episode_can_select_later_frame_without_overlay_id(
    tmp_path, monkeypatch
):
    from core.violation_engine import RuleEngineState
    import core.temporal_evidence as temporal

    monkeypatch.setattr(temporal, "EVIDENCE_FOLDER", tmp_path / "evidence")
    state = RuleEngineState()
    state.mark_fired("Illegal Parking", 7, 1.0)
    tracker = state.tracker_for("illegal_parking_park", 7)
    assert tracker.update(True, 1.0, threshold_sec=0.0)
    assert temporal.active_sustained_event_keys(state, 1.0) == {("Illegal Parking", 7)}

    buffer = TemporalEvidenceBuffer(source_key="video_4", fps=1, run_id=32)
    blurry = np.zeros((64, 64, 3), dtype=np.uint8)
    detailed = np.indices((64, 64)).sum(axis=0).astype(np.uint8) % 2 * 255
    detailed = np.repeat(detailed[:, :, None], 3, axis=2)
    buffer.push(blurry, 20, 1.0)
    episode = buffer.begin_episode(
        violation_type="Illegal Parking", track_id=7, confirmed_at_sec=1.0,
        episode_start_sec=1.0, review_id=3, track_identity_epoch=5,
    )
    trigger = {"track_id": 7, "track_identity_epoch": 5,
               "bbox_x": 8, "bbox_y": 8, "bbox_w": 40, "bbox_h": 40, "confidence": .99}
    buffer.set_frame_annotations(20, [trigger], {}, None, {7}, preferred_event_keys={("Illegal Parking", 7)})

    # The episode remains true, but _emit deduplication yields no overlay violation ID.
    assert state.already_fired("Illegal Parking", 7)
    assert tracker.update(True, 1.5, threshold_sec=0.0)
    active = temporal.active_sustained_event_keys(state, 1.5)
    assert active == {("Illegal Parking", 7)}
    buffer.push(detailed, 21, 1.5)
    later = {"track_id": 7, "track_identity_epoch": 5,
             "bbox_x": 8, "bbox_y": 8, "bbox_w": 40, "bbox_h": 40, "confidence": .75}
    buffer.set_frame_annotations(21, [later], {}, None, set(), preferred_event_keys=active)

    # Same tracker ID from a recycled identity epoch cannot compete.
    buffer.push(detailed, 22, 1.75)
    recycled = {"track_id": 7, "track_identity_epoch": 6,
                "bbox_x": 8, "bbox_y": 8, "bbox_w": 40, "bbox_h": 40, "confidence": 1.0}
    buffer.set_frame_annotations(22, [recycled], {}, None, set(), preferred_event_keys={("Illegal Parking", 7)})
    buffer.end_episode("Illegal Parking", 7, 2.0)
    finalized = buffer.finalize_episode("Illegal Parking", 7, now_sec=2.0, write_clip=False)
    sequence_dir = Path(temporal.EVIDENCE_FOLDER) / "video_4" / "run_32" / Path(finalized.sequence_dir).name
    metadata = json.loads((sequence_dir / "preferred_scene.json").read_text(encoding="utf-8"))
    assert finalized is episode
    assert metadata["frame_number"] == 21


def test_instantaneous_rule_has_no_later_preferred_candidates():
    from core.violation_engine import RuleEngineState

    state = RuleEngineState()
    state.mark_fired("Failure to Follow Road/Pavement Markings", 7, 1.0)
    tracker = state.tracker_for("pavement_markings", 7)
    assert tracker.update(True, 1.0, threshold_sec=0.0)
    assert active_sustained_event_keys(state, 1.0) == set()
