"""Gate 4 — Annotation editor v2 behavioral tests.

Static JavaScript source-string checks are not sufficient. This module
executes SceneObjectEditor operations via Node and verifies Flask
save/reload of schema_version 2 documents.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_scene_annotation_gate2 import _v2_doc

ZONE_EDITOR_JS = Path("static/js/zone_editor.js")
NODE_TEST = Path("tests/js/test_scene_object_editor_gate4.cjs")


def test_zone_editor_exposes_v2_scene_helpers():
    js = ZONE_EDITOR_JS.read_text(encoding="utf-8")
    assert "createSceneObjectEditor" in js
    assert "SceneObjectEditor" in js
    assert "orientationHint" in js
    assert "prohibited_from" in js
    assert "schema_version === 2" in js
    assert "flow_arrows" in js
    assert "activity_regions" in js
    assert "threshold_lines" in js
    assert "getSceneDocument" in js
    assert "setObjectKind" in js


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required for Gate 4 behavioral UI tests")
def test_scene_object_editor_operations_and_reload():
    result = subprocess.run(
        ["node", str(NODE_TEST)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "PASS" in result.stdout


def test_annotation_api_preserves_v2_ids_on_save_and_reload(enforcer_client, test_db):
    vid = test_db.insert_video(
        filename="gate4.mp4",
        filepath="/tmp/gate4.mp4",
        status="annotating",
    )
    doc = _v2_doc()
    resp = enforcer_client.post(
        f"/api/videos/{vid}/annotation",
        json={"zones": doc, "save_mode": "video_only"},
        content_type="application/json",
    )
    body = resp.get_json()
    assert resp.status_code == 200, body
    assert body["success"] is True

    got = enforcer_client.get(f"/api/videos/{vid}/annotation")
    payload = got.get_json()
    stored = json.loads(payload["annotation"]["zones_json"])
    assert stored["schema_version"] == 2
    assert {item["id"] for item in stored["lanes"]} == {"lane-a", "lane-b"}
    assert stored["flow_arrows"][0]["id"] == "arrow-a"
    assert stored["signs"][0]["id"] == "sg-1"
    assert stored["activity_regions"][0]["type"] == "passenger_activity"
    assert stored["markings"][0]["prohibited_from"] == "both"
    assert "no_loading" not in stored
    # Must remain a v2 document, not a flattened six-key polygon map.
    assert isinstance(stored["zones"][0], dict)
    assert stored["zones"][0]["id"] == "z-park"
    assert "schema_version" in stored
