"""Gate 2 — Versioned scene annotation and RuleSceneContext projection."""

from __future__ import annotations

import json

import pytest

from core.scene_annotation import (
    SceneAnnotationError,
    dumps_scene_annotation,
    empty_v2_document,
    load_scene_annotation,
    merge_rule_scene_inputs,
    project_rule_scene_context,
)
from core.zone_config import dumps_zones, parse_zones_json


LEGACY = {
    "no_parking": [[10, 10], [50, 10], [50, 40], [10, 40]],
    "active_lane": [[100, 10], [200, 10], [200, 80], [100, 80]],
    "pedestrian_crossing": [],
    "truck_ban_zone": [],
    "loading_unloading": [[300, 10], [360, 10], [360, 60], [300, 60]],
    "restricted_lane": [],
}


def _v2_doc() -> dict:
    return {
        "schema_version": 2,
        "zones": [
            {
                "id": "z-park",
                "type": "no_parking",
                "points": [[10, 10], [50, 10], [50, 40], [10, 40]],
            }
        ],
        "lanes": [
            {
                "id": "lane-a",
                "type": "active_lane",
                "points": [[100, 10], [200, 10], [200, 80], [100, 80]],
            },
            {
                "id": "lane-b",
                "type": "active_lane",
                "points": [[220, 10], [320, 10], [320, 80], [220, 80]],
            },
        ],
        "flow_arrows": [
            {
                "id": "arrow-a",
                "type": "lane_flow",
                "points": [[120, 40], [180, 40]],
                "lane_ids": ["lane-a"],
            },
            {
                "id": "arrow-b",
                "type": "lane_flow",
                "points": [[300, 40], [240, 40]],
                "lane_ids": ["lane-b"],
            },
        ],
        "threshold_lines": [
            {
                "id": "th-1",
                "type": "no_entry_threshold",
                "points": [[50, 100], [150, 100]],
                "lane_ids": ["lane-a"],
            }
        ],
        "markings": [
            {
                "id": "mk-1",
                "type": "double_solid",
                "points": [[0, 50], [400, 50]],
                "lane_ids": ["lane-a", "lane-b"],
                "prohibited_from": "both",
            }
        ],
        "signs": [
            {
                "id": "sg-1",
                "type": "no_entry",
                "points": [[60, 20], [80, 20], [80, 40], [60, 40]],
                "lane_ids": ["lane-a"],
            }
        ],
        "activity_regions": [
            {
                "id": "act-1",
                "type": "passenger_activity",
                "points": [[300, 10], [360, 10], [360, 60], [300, 60]],
            }
        ],
        "custom_meta": {"camera": "baliwasan"},
    }


class TestLegacyCompatibility:
    def test_legacy_loads_unchanged_projection(self):
        scene = load_scene_annotation(LEGACY)
        assert scene.source_kind == "legacy"
        projected = scene.legacy_zones()
        assert set(projected.keys()) == {
            "no_parking",
            "active_lane",
            "pedestrian_crossing",
            "truck_ban_zone",
            "loading_unloading",
            "restricted_lane",
        }
        assert "no_loading" not in projected
        assert projected["loading_unloading"] == LEGACY["loading_unloading"]
        assert parse_zones_json(LEGACY)["active_lane"] == LEGACY["active_lane"]

    def test_legacy_round_trip_preserves_polygons(self):
        dumped = dumps_zones(LEGACY)
        reloaded = json.loads(dumped)
        assert reloaded["no_parking"] == LEGACY["no_parking"]
        assert reloaded["loading_unloading"] == LEGACY["loading_unloading"]
        assert "schema_version" not in reloaded


class TestV2Contract:
    def test_v2_round_trip_and_unknown_fields(self):
        doc = _v2_doc()
        scene = load_scene_annotation(doc)
        assert scene.is_v2()
        dumped = dumps_scene_annotation(scene)
        again = load_scene_annotation(dumped)
        storage = again.to_storage_dict()
        assert storage["schema_version"] == 2
        assert storage["custom_meta"] == {"camera": "baliwasan"}
        assert {o["id"] for o in storage["lanes"]} == {"lane-a", "lane-b"}
        assert storage["flow_arrows"][0]["id"] == "arrow-a"

    def test_dumps_zones_preserves_v2(self):
        dumped = dumps_zones(_v2_doc())
        data = json.loads(dumped)
        assert data["schema_version"] == 2
        assert len(data["lanes"]) == 2
        assert data["custom_meta"]["camera"] == "baliwasan"

    def test_stable_ids_preserved(self):
        scene = load_scene_annotation(_v2_doc())
        ids = {obj.id for obj in scene.all_objects()}
        assert "lane-a" in ids and "sg-1" in ids
        again = load_scene_annotation(dumps_scene_annotation(scene))
        assert {obj.id for obj in again.all_objects()} == ids

    def test_invalid_coordinates_rejected(self):
        doc = empty_v2_document()
        doc["lanes"] = [
            {
                "id": "bad",
                "type": "active_lane",
                "points": [[0, 0], [1, float("nan")], [1, 1]],
            }
        ]
        with pytest.raises(SceneAnnotationError):
            load_scene_annotation(doc)

    def test_duplicate_ids_rejected(self):
        doc = empty_v2_document()
        doc["lanes"] = [
            {
                "id": "same",
                "type": "active_lane",
                "points": [[0, 0], [10, 0], [10, 10], [0, 10]],
            },
            {
                "id": "same",
                "type": "active_lane",
                "points": [[20, 0], [30, 0], [30, 10], [20, 10]],
            },
        ]
        with pytest.raises(SceneAnnotationError, match="Duplicate"):
            load_scene_annotation(doc)

    def test_broken_lane_reference_rejected(self):
        doc = empty_v2_document()
        doc["flow_arrows"] = [
            {
                "id": "a1",
                "type": "lane_flow",
                "points": [[0, 0], [10, 0]],
                "lane_ids": ["missing"],
            }
        ]
        with pytest.raises(SceneAnnotationError, match="unknown lane_id"):
            load_scene_annotation(doc)

    def test_multiple_lanes_not_collapsed_for_identity(self):
        scene = load_scene_annotation(_v2_doc())
        ctx = project_rule_scene_context(scene)
        assert len(ctx.lanes) == 2
        assert {f.lane_id for f in ctx.lane_flows} == {"lane-a", "lane-b"}
        # Legacy silhouette may expose one active_lane polygon, but identity
        # remains on ctx.lanes.
        assert len(ctx.legacy_zones["active_lane"]) >= 3


class TestConflictingInputs:
    def test_conflicting_structured_and_legacy_rejected(self):
        scene = load_scene_annotation(_v2_doc()).to_rule_context()
        with pytest.raises(SceneAnnotationError, match="Conflicting"):
            merge_rule_scene_inputs(
                scene=scene,
                zones={
                    "no_parking": [[1, 1], [2, 1], [2, 2], [1, 2]],
                },
            )

    def test_conflicting_param_signs_rejected(self):
        scene = load_scene_annotation(_v2_doc()).to_rule_context()
        with pytest.raises(SceneAnnotationError, match="supported_signs"):
            merge_rule_scene_inputs(
                scene=scene,
                params={"supported_signs": [{"type": "no_entry"}]},
            )


class TestVideoLiveProjectionParity:
    def test_same_projection_helpers(self):
        raw = json.dumps(_v2_doc())
        video_scene = load_scene_annotation(raw).to_rule_context()
        live_scene = load_scene_annotation(raw).to_rule_context()
        assert video_scene.legacy_zones == live_scene.legacy_zones
        assert [lane.id for lane in video_scene.lanes] == [
            lane.id for lane in live_scene.lanes
        ]
        assert [f.degrees for f in video_scene.lane_flows] == [
            f.degrees for f in live_scene.lane_flows
        ]
        assert [s.type for s in video_scene.signs] == [
            s.type for s in live_scene.signs
        ]
