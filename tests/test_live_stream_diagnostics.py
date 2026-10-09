import json

from core.detection_config import VIOLATION_NO_SIDE_MIRROR, VIOLATION_TRUCK_BAN
from core.live_stream import (
    LiveStreamWorker,
    StreamManager,
    _consume_live_diagnostics,
    live_capability_diagnostics,
    live_review_evidence_fields,
)
from core.violation_engine import RuleEngineState, ViolationEvent, evaluate_detection_rules


def test_live_unknown_prerequisites_are_exposed_as_actionable_diagnostics():
    zone = [[0, 0], [300, 0], [300, 300], [0, 300]]
    state = RuleEngineState()
    truck = {
        "class_label": "truck",
        "track_id": 1,
        "bbox_x": 100,
        "bbox_y": 100,
        "bbox_w": 40,
        "bbox_h": 30,
        "confidence": 0.9,
        "timestamp_sec": 1.0,
    }

    events = evaluate_detection_rules(
        [truck],
        state,
        frame_number=1,
        zones={"truck_ban_zone": zone},
        params={"_live_mode": True, "truck_ban_start": "00:00", "truck_ban_end": "23:59"},
        enabled_violations=(VIOLATION_TRUCK_BAN, VIOLATION_NO_SIDE_MIRROR),
        model_classes=("truck", "side_mirror"),
        now_sec=1.0,
    )
    diagnostics = live_capability_diagnostics(state.capability)

    assert events == []
    assert len(diagnostics) == 2
    assert any("recording_datetime" in note for note in diagnostics)
    assert any("both mirror mounting areas" in note for note in diagnostics)

    manager = StreamManager()
    worker = LiveStreamWorker({"id": 99, "zones_json": "{}"})
    worker.diagnostics = diagnostics
    manager._workers[99] = worker
    assert manager.status(99)["diagnostics"] == diagnostics


def test_live_review_confidence_factors_persist_without_video_provenance(test_db):
    event = ViolationEvent(
        violation_type=VIOLATION_TRUCK_BAN,
        track_id=4,
        confidence=0.73,
        frame_number=12,
        timestamp_sec=5.5,
        reason_log="review candidate",
        detection_confidence=0.91,
        violation_confidence=0.73,
        evidence_sufficiency=0.66,
        contributing_factors={"persistence": 0.8, "context": 0.5},
        unavailable_factors=("capture_time",),
    )
    review_id = test_db.insert_review_queue(
        video_id=None,
        track_id=event.track_id,
        violation_type=event.violation_type,
        confidence=event.confidence,
        frame_number=event.frame_number,
        **live_review_evidence_fields(event),
    )
    row = test_db.get_review_item(review_id)

    assert row["video_id"] is None
    assert row["detection_confidence"] == 0.91
    assert row["violation_confidence"] == 0.73
    assert row["evidence_sufficiency"] == 0.66
    assert json.loads(row["contributing_factors_json"]) == {
        "persistence": 0.8,
        "context": 0.5,
    }
    assert json.loads(row["unavailable_factors_json"]) == ["capture_time"]


def test_rule_diagnostics_are_bounded_unique_and_incremental():
    diagnostics = RuleEngineState().diagnostics
    for _ in range(2000):
        diagnostics.append("saved no-entry threshold is unavailable")
    assert diagnostics == ["saved no-entry threshold is unavailable"]
    first_batch, revision = diagnostics.since(0)
    assert first_batch == ["saved no-entry threshold is unavailable"]
    assert diagnostics.since(revision) == ([], revision)

    diagnostics.append("a second actionable rule note")
    assert diagnostics.since(revision) == (["a second actionable rule note"], revision + 1)


def test_rule_diagnostic_retention_is_bounded_when_messages_change():
    diagnostics = RuleEngineState().diagnostics
    for index in range(2000):
        diagnostics.append(f"threshold {index} is unavailable")
    assert len(diagnostics) == diagnostics.MAX_ITEMS
    assert diagnostics[0] == "threshold 1488 is unavailable"
    assert diagnostics[-1] == "threshold 1999 is unavailable"


def test_live_consumer_only_appends_new_rule_diagnostics_per_worker():
    first = RuleEngineState()
    second = RuleEngineState()
    output_one, output_two = [], []
    seen_one, seen_two = set(), set()

    first.diagnostics.append("first rule note")
    revision = _consume_live_diagnostics(output_one, seen_one, first, 0)
    revision = _consume_live_diagnostics(output_one, seen_one, first, revision)
    assert output_one == ["first rule note"]

    first.diagnostics.append("second rule note")
    revision = _consume_live_diagnostics(output_one, seen_one, first, revision)
    assert output_one == ["first rule note", "second rule note"]

    second.diagnostics.append("first rule note")
    _consume_live_diagnostics(output_two, seen_two, second, 0)
    assert output_two == ["first rule note"]
