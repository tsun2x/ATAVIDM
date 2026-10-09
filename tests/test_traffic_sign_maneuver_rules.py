import pytest

from core.scene_annotation import (
    IdentifiedSceneObject,
    LaneFlow,
    RuleSceneContext,
    SceneAnnotation,
    load_scene_annotation,
    project_rule_scene_context,
)
from core.tracker import CentroidObservation, TrackHistorySnapshot, TrackHistoryView
from core.violation_engine import (
    RuleEngineState,
    check_disregarding_traffic_sign,
    evaluate_detection_rules,
)


def _rule_scene(sign_type, *, include_flow=True, include_threshold=True):
    sign = IdentifiedSceneObject(
        id="sign-1",
        type=sign_type,
        points=((10, 10), (30, 10), (30, 30), (10, 30)),
        lane_ids=("lane-1",),
    )
    threshold = IdentifiedSceneObject(
        id="decision-line-1",
        type="threshold",
        points=((20, 100), (180, 100)),
        lane_ids=("lane-1",),
    )
    return RuleSceneContext(
        legacy_zones={},
        lanes=(),
        lane_flows=(LaneFlow("lane-1", 270, (0, -1)),) if include_flow else (),
        signs=(sign,),
        markings=(),
        threshold_lines=(threshold,) if include_threshold else (),
        activity_regions=(),
    )


def _history(points):
    observations = tuple(
        CentroidObservation(float(i), float(x), float(y))
        for i, (x, y) in enumerate(points)
    )
    snapshot = TrackHistorySnapshot(
        track_id=7,
        class_label="car",
        observations=observations,
        last_seen=float(len(points) - 1),
        stationary_since=None,
    )
    return TrackHistoryView({7: snapshot}, now_sec=float(len(points) - 1))


def _evaluate(sign_type, points, *, include_flow=True, include_threshold=True):
    detection = {
        "class_label": "car",
        "track_id": 7,
        "bbox_x": points[-1][0] - 15,
        "bbox_y": points[-1][1] - 15,
        "bbox_w": 30,
        "bbox_h": 30,
        "confidence": 0.9,
        "timestamp_sec": float(len(points) - 1),
    }
    return check_disregarding_traffic_sign(
        [detection],
        RuleEngineState(),
        frame_number=len(points),
        params={
            "_rule_scene": _rule_scene(
                sign_type,
                include_flow=include_flow,
                include_threshold=include_threshold,
            ),
            "_track_history": _history(points),
            "min_direction_px": 20,
        },
    )


def test_no_left_turn_candidate_requires_observed_left_turn_across_configured_line():
    left_turn = [
        (100, 160), (100, 130), (100, 90), (100, 75), (80, 70), (50, 70)
    ]
    right_turn = [
        (100, 160), (100, 130), (100, 90), (100, 75), (120, 70), (150, 70)
    ]

    events = _evaluate("no_left_turn", left_turn)

    assert len(events) == 1
    assert events[0].outcome == "review"
    assert "No Left Turn" in events[0].reason_log
    assert _evaluate("no_left_turn", right_turn) == []


def test_no_right_turn_and_no_u_turn_candidates_are_review_only():
    right_turn = [
        (100, 160), (100, 130), (100, 90), (100, 75), (120, 70), (150, 70)
    ]
    u_turn = [
        (100, 160), (100, 130), (100, 90), (100, 75), (100, 100), (100, 140)
    ]

    right_events = _evaluate("no_right_turn", right_turn)
    u_turn_events = _evaluate("no_u_turn", u_turn)

    assert len(right_events) == 1
    assert right_events[0].outcome == "review"
    assert "No Right Turn" in right_events[0].reason_log
    assert len(u_turn_events) == 1
    assert u_turn_events[0].outcome == "review"
    assert "No U-turn" in u_turn_events[0].reason_log


def test_turn_signs_fail_closed_without_lane_flow_or_threshold_geometry():
    left_turn = [
        (100, 160), (100, 130), (100, 90), (100, 75), (80, 70), (50, 70)
    ]

    assert _evaluate("no_left_turn", left_turn, include_flow=False) == []
    assert _evaluate("no_left_turn", left_turn, include_threshold=False) == []


def test_turn_candidate_does_not_use_a_stale_threshold_crossing():
    stale_crossing_then_right_turn = [
        (100, 160), (100, 130), (100, 90), (100, 75),
        (130, 35), (180, 5), (230, -25)
    ]

    assert _evaluate("no_right_turn", stale_crossing_then_right_turn) == []


def test_turn_crossing_uses_vehicle_bottom_center_against_road_geometry():
    # The box center stays above the line, but its bottom-center crosses it.
    center_points = [(100, 95), (100, 90), (100, 75), (80, 70), (50, 70)]

    events = _evaluate("no_left_turn", center_points)

    assert len(events) == 1
    assert events[0].outcome == "review"


def test_turn_candidate_runs_through_the_video_rule_entrypoint():
    points = [
        (100, 160), (100, 130), (100, 90), (100, 75), (80, 70), (50, 70)
    ]
    scene = SceneAnnotation(
        schema_version=2,
        source_kind="v2",
        lanes=[IdentifiedSceneObject(
            id="lane-1", type="active_lane",
            points=((50, 40), (150, 40), (150, 200), (50, 200)),
        )],
        flow_arrows=[IdentifiedSceneObject(
            id="flow-1", type="lane_flow",
            points=((100, 180), (100, 100)), lane_ids=("lane-1",),
        )],
        threshold_lines=[IdentifiedSceneObject(
            id="decision-line-1", type="threshold",
            points=((20, 100), (180, 100)), lane_ids=("lane-1",),
        )],
        signs=[IdentifiedSceneObject(
            id="sign-1", type="no_left_turn",
            points=((10, 10), (30, 10), (30, 30), (10, 30)),
            lane_ids=("lane-1",),
        )],
    )
    detection = {
        "class_label": "car", "track_id": 7,
        "bbox_x": 35, "bbox_y": 55, "bbox_w": 30, "bbox_h": 30,
        "confidence": 0.9, "timestamp_sec": float(len(points) - 1),
    }

    events = evaluate_detection_rules(
        [detection],
        RuleEngineState(),
        frame_number=len(points),
        params={"min_direction_px": 20},
        enabled_violations=("Disregarding Traffic Sign",),
        now_sec=float(len(points) - 1),
        scene=project_rule_scene_context(scene),
        history=_history(points),
    )

    assert len(events) == 1
    assert events[0].outcome == "review"
    assert "No Left Turn" in events[0].reason_log


def test_no_entry_uses_saved_prohibited_side_on_threshold_through_main_entrypoint():
    """A saved side must control the entry direction; current metadata-only lookup misses it."""
    scene = SceneAnnotation(
        schema_version=2,
        source_kind="v2",
        threshold_lines=[IdentifiedSceneObject(
            id="entry-line", type="no_entry_threshold",
            points=((20, 100), (180, 100)), lane_ids=("lane-1",),
            prohibited_from="left",
        )],
        signs=[IdentifiedSceneObject(
            id="no-entry", type="no_entry",
            points=((10, 10), (30, 10), (30, 30), (10, 30)),
            lane_ids=("lane-1",),
        )],
    )
    points = [(100, 160), (100, 80)]
    event = {
        "class_label": "car", "track_id": 7,
        "bbox_x": 85, "bbox_y": 65, "bbox_w": 30, "bbox_h": 30,
        "confidence": 0.9, "timestamp_sec": 1.0,
    }

    candidates = evaluate_detection_rules(
        [event], RuleEngineState(), frame_number=2,
        params={"min_direction_px": 20},
        enabled_violations=("Disregarding Traffic Sign",),
        now_sec=1.0, scene=project_rule_scene_context(scene),
        history=_history(points),
    )

    assert len(candidates) == 1
    assert candidates[0].outcome == "review"
    assert candidates[0].violation_type == "Disregarding Traffic Sign"


def test_no_entry_saved_right_side_and_allowed_reverse_direction():
    def run(points, side):
        scene = SceneAnnotation(
            schema_version=2,
            source_kind="v2",
            threshold_lines=[IdentifiedSceneObject(
                id="entry-line", type="no_entry_threshold",
                points=((20, 100), (180, 100)), lane_ids=("lane-1",),
                prohibited_from=side,
            )],
            signs=[IdentifiedSceneObject(
                id="no-entry", type="no_entry",
                points=((10, 10), (30, 10), (30, 30), (10, 30)),
                lane_ids=("lane-1",),
            )],
        )
        x, y = points[-1]
        detection = {
            "class_label": "car", "track_id": 7,
            "bbox_x": x - 15, "bbox_y": y - 15,
            "bbox_w": 30, "bbox_h": 30,
            "confidence": 0.9, "timestamp_sec": 1.0,
        }
        return evaluate_detection_rules(
            [detection], RuleEngineState(), frame_number=2,
            params={"min_direction_px": 20},
            enabled_violations=("Disregarding Traffic Sign",),
            now_sec=1.0, scene=project_rule_scene_context(scene),
            history=_history(points),
        )

    # From above to below enters from the right side of the oriented line.
    assert len(run([(100, 40), (100, 120)], "right")) == 1
    # The same geometric crossing from the opposite side is allowed.
    assert run([(100, 120), (100, 40)], "right") == []


def test_no_entry_preserves_explicit_prohibited_vector_behavior():
    scene = SceneAnnotation(
        schema_version=2,
        source_kind="v2",
        threshold_lines=[IdentifiedSceneObject(
            id="entry-line", type="no_entry_threshold",
            points=((20, 100), (180, 100)), lane_ids=("lane-1",),
            metadata={"prohibited_vector": (0, -1)},
        )],
        signs=[IdentifiedSceneObject(
            id="no-entry", type="no_entry",
            points=((10, 10), (30, 10), (30, 30), (10, 30)),
            lane_ids=("lane-1",),
        )],
    )
    detection = {
        "class_label": "car", "track_id": 7, "bbox_x": 85,
        "bbox_y": 65, "bbox_w": 30, "bbox_h": 30,
        "confidence": 0.9, "timestamp_sec": 1.0,
    }

    events = evaluate_detection_rules(
        [detection], RuleEngineState(), frame_number=2,
        params={"min_direction_px": 20},
        enabled_violations=("Disregarding Traffic Sign",),
        now_sec=1.0, scene=project_rule_scene_context(scene),
        history=_history([(100, 160), (100, 80)]),
    )

    assert len(events) == 1
    assert events[0].outcome == "review"


def test_no_entry_without_lane_associated_direction_fails_closed_with_diagnostic():
    scene = SceneAnnotation(
        schema_version=2,
        source_kind="v2",
        threshold_lines=[IdentifiedSceneObject(
            id="entry-line", type="no_entry_threshold",
            points=((20, 100), (180, 100)), lane_ids=("lane-2",),
        )],
        signs=[IdentifiedSceneObject(
            id="no-entry", type="no_entry",
            points=((10, 10), (30, 10), (30, 30), (10, 30)),
            lane_ids=("lane-1",),
        )],
    )
    state = RuleEngineState()
    detection = {
        "class_label": "car", "track_id": 7, "bbox_x": 85,
        "bbox_y": 65, "bbox_w": 30, "bbox_h": 30,
        "confidence": 0.9, "timestamp_sec": 1.0,
    }

    candidates = evaluate_detection_rules(
        [detection], state, frame_number=2,
        params={"min_direction_px": 20},
        enabled_violations=("Disregarding Traffic Sign",),
        now_sec=1.0, scene=project_rule_scene_context(scene),
        history=_history([(100, 160), (100, 80)]),
    )

    assert candidates == []
    assert any("no-entry sign" in note and "decision threshold" in note for note in state.diagnostics)


def test_counting_line_does_not_make_no_entry_threshold_ambiguous():
    scene = SceneAnnotation(
        schema_version=2,
        source_kind="v2",
        threshold_lines=[
            IdentifiedSceneObject(
                id="entry-line", type="no_entry_threshold",
                points=((20, 100), (180, 100)), lane_ids=("lane-1",),
                prohibited_from="left",
            ),
            IdentifiedSceneObject(
                id="count-line", type="counting_line",
                points=((20, 80), (180, 80)), lane_ids=("lane-1",),
            ),
        ],
        signs=[IdentifiedSceneObject(
            id="no-entry", type="no_entry",
            points=((10, 10), (30, 10), (30, 30), (10, 30)),
            lane_ids=("lane-1",),
        )],
    )
    detection = {
        "class_label": "car", "track_id": 7,
        "bbox_x": 85, "bbox_y": 65, "bbox_w": 30, "bbox_h": 30,
        "confidence": 0.9, "timestamp_sec": 1.0,
    }

    candidates = evaluate_detection_rules(
        [detection], RuleEngineState(), frame_number=2,
        params={"min_direction_px": 20},
        enabled_violations=("Disregarding Traffic Sign",),
        now_sec=1.0, scene=project_rule_scene_context(scene),
        history=_history([(100, 160), (100, 80)]),
    )

    assert len(candidates) == 1
    assert candidates[0].outcome == "review"


def test_counting_line_alone_does_not_create_no_entry_candidate():
    scene = SceneAnnotation(
        schema_version=2,
        source_kind="v2",
        threshold_lines=[IdentifiedSceneObject(
            id="count-line", type="counting_line",
            points=((20, 100), (180, 100)), lane_ids=("lane-1",),
        )],
        signs=[IdentifiedSceneObject(
            id="no-entry", type="no_entry",
            points=((10, 10), (30, 10), (30, 30), (10, 30)),
            lane_ids=("lane-1",),
        )],
    )
    state = RuleEngineState()
    detection = {
        "class_label": "car", "track_id": 7,
        "bbox_x": 85, "bbox_y": 65, "bbox_w": 30, "bbox_h": 30,
        "confidence": 0.9, "timestamp_sec": 1.0,
    }

    candidates = evaluate_detection_rules(
        [detection], state, frame_number=2,
        params={"min_direction_px": 20},
        enabled_violations=("Disregarding Traffic Sign",),
        now_sec=1.0, scene=project_rule_scene_context(scene),
        history=_history([(100, 160), (100, 80)]),
    )

    assert candidates == []
    assert any("decision threshold" in note for note in state.diagnostics)


@pytest.mark.parametrize("bad_vector", [
    {"x": 0, "y": -1},  # objects are not a supported serialized vector shape
    3,
    [0],
    ["north", 1],
    [float("inf"), 0],
    [0, 0],
])
def test_malformed_saved_no_entry_vector_fails_closed_at_scene_rule_boundary(bad_vector):
    document = {
        "schema_version": 2,
        "threshold_lines": [{
            "id": "entry-line", "type": "no_entry_threshold",
            "points": [[20, 100], [180, 100]], "lane_ids": ["lane-1"],
            "prohibited_vector": bad_vector,
        }],
        "lanes": [{
            "id": "lane-1", "type": "active_lane",
            "points": [[40, 40], [160, 40], [160, 200], [40, 200]],
        }],
        "signs": [{
            "id": "no-entry", "type": "no_entry",
            "points": [[10, 10], [30, 10], [30, 30], [10, 30]],
            "lane_ids": ["lane-1"],
        }],
        "flow_arrows": [{
            "id": "flow", "type": "lane_flow",
            "points": [[100, 180], [100, 100]], "lane_ids": ["lane-1"],
        }],
    }
    scene = load_scene_annotation(document)
    state = RuleEngineState()
    detection = {
        "class_label": "car", "track_id": 7,
        "bbox_x": 85, "bbox_y": 65, "bbox_w": 30, "bbox_h": 30,
        "confidence": 0.9, "timestamp_sec": 1.0,
    }

    candidates = evaluate_detection_rules(
        [detection], state, frame_number=2,
        params={"min_direction_px": 20},
        enabled_violations=("Disregarding Traffic Sign",),
        now_sec=1.0, scene=project_rule_scene_context(scene),
        history=_history([(100, 160), (100, 80)]),
    )

    assert candidates == []
    assert any("unambiguous prohibited direction" in note for note in state.diagnostics)


def test_repeated_rule_evaluations_retain_one_missing_threshold_diagnostic():
    scene = SceneAnnotation(
        schema_version=2,
        source_kind="v2",
        threshold_lines=[IdentifiedSceneObject(
            id="count-line", type="counting_line",
            points=((20, 100), (180, 100)), lane_ids=("lane-1",),
        )],
        signs=[IdentifiedSceneObject(
            id="no-entry", type="no_entry",
            points=((10, 10), (30, 10), (30, 30), (10, 30)),
            lane_ids=("lane-1",),
        )],
    )
    state = RuleEngineState()
    detection = {
        "class_label": "car", "track_id": 7,
        "bbox_x": 85, "bbox_y": 65, "bbox_w": 30, "bbox_h": 30,
        "confidence": 0.9, "timestamp_sec": 1.0,
    }

    for frame in range(1000):
        evaluate_detection_rules(
            [detection], state, frame_number=frame,
            params={"min_direction_px": 20},
            enabled_violations=("Disregarding Traffic Sign",),
            now_sec=1.0, scene=project_rule_scene_context(scene),
            history=_history([(100, 160), (100, 80)]),
        )

    assert len(state.diagnostics) == 1
    assert "counting line only" in state.diagnostics[0]


def _evaluate_no_overtaking(first_path, second_path):
    sign = IdentifiedSceneObject(
        id="no-pass-sign", type="no_overtaking",
        points=((10, 10), (30, 10), (30, 30), (10, 30)),
        lane_ids=("lane-1",),
    )
    lane = IdentifiedSceneObject(
        id="lane-1", type="active_lane",
        points=((40, 40), (140, 40), (140, 190), (40, 190)),
    )
    scene = RuleSceneContext(
        legacy_zones={},
        lanes=(lane,),
        lane_flows=(LaneFlow("lane-1", 270, (0, -1)),),
        signs=(sign,),
        markings=(),
        threshold_lines=(),
        activity_regions=(),
    )
    snapshots = {}
    detections = []
    for track_id, path in ((7, first_path), (8, second_path)):
        observations = tuple(
            CentroidObservation(float(i), float(x), float(y))
            for i, (x, y) in enumerate(path)
        )
        snapshots[track_id] = TrackHistorySnapshot(
            track_id=track_id,
            class_label="car",
            observations=observations,
            last_seen=float(len(path) - 1),
            stationary_since=None,
        )
        x, y = path[-1]
        detections.append({
            "class_label": "car", "track_id": track_id,
            "bbox_x": x - 10, "bbox_y": y - 15,
            "bbox_w": 20, "bbox_h": 30,
            "confidence": 0.9, "timestamp_sec": float(len(path) - 1),
        })
    events = check_disregarding_traffic_sign(
        detections,
        RuleEngineState(),
        frame_number=len(first_path),
        params={
            "_rule_scene": scene,
            "_track_history": TrackHistoryView(
                snapshots, now_sec=float(len(first_path) - 1)
            ),
            "min_direction_px": 20,
        },
    )
    return events


def test_no_overtaking_candidate_requires_two_tracks_to_swap_order_in_signed_lane():
    overtaking = [(80, 140), (80, 110), (80, 80)]
    overtaken = [(100, 135), (100, 120), (100, 110)]
    no_pass = [(80, 140), (80, 130), (80, 120)]

    events = _evaluate_no_overtaking(overtaking, overtaken)

    assert len(events) == 1
    assert events[0].track_id == 7
    assert events[0].outcome == "review"
    assert "passed" in events[0].reason_log
    assert _evaluate_no_overtaking(no_pass, overtaken) == []


def test_no_overtaking_uses_a_single_observed_behind_to_ahead_transition():
    overtaking = [(80, 140), (80, 100)]
    overtaken = [(100, 130), (100, 105)]

    events = _evaluate_no_overtaking(overtaking, overtaken)

    assert len(events) == 1
    assert events[0].track_id == 7
    assert events[0].outcome == "review"
