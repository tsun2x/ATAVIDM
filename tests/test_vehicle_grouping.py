import json
from pathlib import Path

import pytest

from core.vehicle_grouping import group_vehicle_events


def _event(root: Path, event_id: int, *, video=1, run=10, track=7, epoch=2, vtype="Illegal Parking"):
    directory = root / f"event-{event_id}"
    directory.mkdir(parents=True)
    identity = {
        "version": 1,
        "video_id": video,
        "processing_run_id": run,
        "review_id": event_id,
        "track_id": track,
        "violation_type": vtype,
        "track_identity_epoch": epoch,
    }
    (directory / "event_identity.json").write_text(json.dumps(identity), encoding="utf-8")
    return {
        "id": event_id,
        "video_id": video,
        "processing_run_id": run,
        "track_id": track,
        "violation_type": vtype,
        "evidence_sequence_dir": str(directory),
        "frame_number": event_id,
    }


def _provenance_for(rows, *, correction_for=()):
    result = {}
    corrections = set(correction_for)
    for row in rows:
        directory = row.get("evidence_sequence_dir")
        if not directory:
            continue
        try:
            manifest = json.loads(
                (Path(directory) / "event_identity.json").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            continue
        if "review_id" not in manifest or not isinstance(manifest.get("video_id"), int):
            continue
        result[row["id"]] = [{
            "case_id": row["id"],
            "source_review_id": manifest["review_id"],
            "video_id": manifest.get("video_id"),
            "processing_run_id": manifest.get("processing_run_id"),
            "track_id": manifest.get("track_id"),
            "violation_type": manifest["violation_type"],
            "status": "confirmed",
            "evidence_sequence_dir": directory,
            "decision": "correct_canonical" if row["id"] in corrections else None,
            "original_violation_type": manifest["violation_type"] if row["id"] in corrections else None,
            "selected_canonical_rule": row.get("violation_type") if row["id"] in corrections else None,
        }]
    return result


def _production_review_with_manifest(
    test_db, monkeypatch, tmp_path, *, video_id, violation_type, run_id, track_id=7,
    timestamp_sec=4.0, episode_start_sec=1.0, episode_end_sec=8.0,
):
    """Finalize evidence through TemporalEvidenceBuffer and store its review row."""
    import numpy as np
    import config
    import core.temporal_evidence as temporal

    monkeypatch.setattr(temporal, "EVIDENCE_FOLDER", tmp_path)
    monkeypatch.setattr(config, "EVIDENCE_FOLDER", str(tmp_path))
    review_id = test_db.insert_review_queue(
        video_id=video_id,
        track_id=track_id,
        violation_type=violation_type,
        confidence=.8,
        frame_number=10,
        timestamp_sec=timestamp_sec,
        processing_run_id=run_id,
        evidence_pre_sec=1.0,
        episode_start_sec=episode_start_sec,
        episode_end_sec=episode_end_sec,
    )
    buffer = temporal.TemporalEvidenceBuffer(
        source_key=f"video_{video_id}", fps=1.0, run_id=run_id
    )
    frame = np.zeros((32, 32, 3), dtype=np.uint8)
    buffer.push(frame, 10, 1.0, finalize_due=False)
    buffer.begin_episode(
        violation_type=violation_type,
        track_id=track_id,
        confirmed_at_sec=timestamp_sec,
        episode_start_sec=1.0,
        review_id=review_id,
        track_identity_epoch=2,
    )
    buffer.end_episode(violation_type, track_id, episode_end_sec)
    episode = buffer.finalize_episode(
        violation_type, track_id, now_sec=episode_end_sec, write_clip=False
    )
    assert episode is not None and episode.sequence_dir
    test_db.update_review_temporal_evidence(
        review_id, evidence_sequence_dir=episode.sequence_dir
    )
    return review_id


def test_real_parking_obstruction_fusion_preserves_grouping_identity(
    test_db, enforcer_client, monkeypatch, tmp_path
):
    """A legitimate fused case keeps identity from its retained Parking manifest."""
    from core.case_review_service import materialize_case_from_review

    video_id = test_db.insert_video("parking-obstruction.mp4", str(tmp_path / "source.mp4"), status="ready")
    run_id = test_db.create_processing_run(video_id, "[]")
    reviewer_id = test_db.get_user_by_username("enforcer_test")["id"]
    parking_review = _production_review_with_manifest(
        test_db, monkeypatch, tmp_path, video_id=video_id,
        violation_type="Illegal Parking", run_id=run_id, track_id=24,
        timestamp_sec=10.0, episode_start_sec=8.0, episode_end_sec=16.0,
    )
    initial = materialize_case_from_review(test_db, parking_review, reviewer_id)
    case_id = initial["violation_id"]

    before_fusion = enforcer_client.get("/api/violations/vehicle-groups").get_json()
    before_event = next(
        event for group in before_fusion["groups"] for event in group["events"]
        if event["db_id"] == case_id
    )
    assert before_event["type"] == "Illegal Parking"
    assert next(group for group in before_fusion["groups"] if case_id in {
        event["db_id"] for event in group["events"]
    })["vehicle_identity_verified"] is True

    # A label mutation by itself must not authorize the same identity change.
    test_db.update_violation_canonical_type(case_id, "Obstruction")
    forged = enforcer_client.get("/api/violations/vehicle-groups").get_json()
    forged_group = next(
        group for group in forged["groups"]
        if any(event["db_id"] == case_id for event in group["events"])
    )
    assert forged_group["vehicle_identity_verified"] is False
    test_db.update_violation_canonical_type(case_id, "Illegal Parking")

    obstruction_review = _production_review_with_manifest(
        test_db, monkeypatch, tmp_path, video_id=video_id,
        violation_type="Obstruction", run_id=run_id, track_id=24,
        timestamp_sec=13.0, episode_start_sec=12.0, episode_end_sec=20.0,
    )
    fused = materialize_case_from_review(test_db, obstruction_review, reviewer_id)

    assert fused["violation_id"] == case_id
    assert fused["fused"] is True
    case = test_db.get_violation(case_id)
    assert case["violation_type"] == "Obstruction"
    assert {
        record["canonical_rule"]
        for record in test_db.get_case_policy_records(case_id)
    } == {"Illegal Parking", "Obstruction"}
    case_links = test_db.list_vehicle_grouping_provenance([case_id])[case_id]
    assert {link["source_review_id"] for link in case_links} == {
        parking_review, obstruction_review
    }
    assert all(link["case_policy_rules"] == ["Illegal Parking", "Obstruction"] for link in case_links)
    manifest_path = Path(case["evidence_sequence_dir"]) / "event_identity.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["review_id"] == parking_review
    assert manifest["violation_type"] == "Illegal Parking"

    response = enforcer_client.get("/api/violations/vehicle-groups")
    payload = response.get_json()
    fused_group = next(
        group for group in payload["groups"]
        if any(event["db_id"] == case_id for event in group["events"])
    )
    assert fused_group["vehicle_identity_verified"] is True
    assert any(
        event["db_id"] == case_id and event["type"] == "Obstruction"
        for event in fused_group["events"]
    )

    # A distinct, non-overlapping event for this tracker epoch stays a second
    # case row while sharing the same proven vehicle occurrence group.
    later_review = _production_review_with_manifest(
        test_db, monkeypatch, tmp_path, video_id=video_id,
        violation_type="Counterflow", run_id=run_id, track_id=24,
        timestamp_sec=28.0, episode_start_sec=26.0, episode_end_sec=31.0,
    )
    later_case = materialize_case_from_review(test_db, later_review, reviewer_id)
    assert later_case["violation_id"] != case_id
    grouped_after_later_event = enforcer_client.get(
        "/api/violations/vehicle-groups"
    ).get_json()
    same_occurrence = next(
        group for group in grouped_after_later_event["groups"]
        if any(event["db_id"] == case_id for event in group["events"])
    )
    assert same_occurrence["vehicle_identity_verified"] is True
    assert {event["db_id"] for event in same_occurrence["events"]} == {
        case_id, later_case["violation_id"]
    }
    assert {event["type"] for event in same_occurrence["events"]} == {
        "Obstruction", "Counterflow"
    }

    def fused_case_group():
        current = enforcer_client.get("/api/violations/vehicle-groups").get_json()
        return next(
            group for group in current["groups"]
            if any(event["db_id"] == case_id for event in group["events"])
        )

    # Same tracker key with a different occurrence epoch is not fusion proof.
    obstruction_manifest_path = (
        Path(test_db.get_review_item(obstruction_review)["evidence_sequence_dir"])
        / "event_identity.json"
    )
    obstruction_manifest = json.loads(
        obstruction_manifest_path.read_text(encoding="utf-8")
    )
    obstruction_manifest_path.write_text(
        json.dumps({**obstruction_manifest, "track_identity_epoch": 9}),
        encoding="utf-8",
    )
    assert fused_case_group()["vehicle_identity_verified"] is False
    obstruction_manifest_path.write_text(
        json.dumps(obstruction_manifest), encoding="utf-8"
    )

    # Persisted episode bounds must still satisfy the case-identity contract.
    with test_db.db_session() as conn:
        conn.execute(
            "UPDATE review_queue SET timestamp_sec = 28, episode_start_sec = 26, episode_end_sec = 31 WHERE id = ?",
            (obstruction_review,),
        )
    assert fused_case_group()["vehicle_identity_verified"] is False
    with test_db.db_session() as conn:
        conn.execute(
            "UPDATE review_queue SET timestamp_sec = 13, episode_start_sec = 12, episode_end_sec = 20 WHERE id = ?",
            (obstruction_review,),
        )

    # A linked but unrelated review cannot substitute for the fusion member.
    test_db.link_review_to_case(later_review, case_id)
    with test_db.db_session() as conn:
        conn.execute(
            "DELETE FROM case_action_events WHERE violation_id = ? AND review_id = ? AND action_type = ?",
            (case_id, obstruction_review, test_db.ACTION_REVIEW_CONFIRMED),
        )
    assert fused_case_group()["vehicle_identity_verified"] is False

    # Removing either durable rule snapshot removes the fusion proof.
    with test_db.db_session() as conn:
        conn.execute(
            "DELETE FROM case_policy_records WHERE violation_id = ? AND canonical_rule = ?",
            (case_id, "Illegal Parking"),
        )
    assert fused_case_group()["vehicle_identity_verified"] is False


@pytest.mark.parametrize("scenario", ["obstruction_first", "both_pending"])
def test_fusion_identity_remains_proven_for_reverse_and_initial_materialization(
    scenario, test_db, enforcer_client, monkeypatch, tmp_path
):
    """The supported fusion contract stays stable across observation arrival order."""
    from core.case_review_service import materialize_case_from_review

    video_id = test_db.insert_video(
        f"{scenario}.mp4", str(tmp_path / f"{scenario}.mp4"), status="ready"
    )
    run_id = test_db.create_processing_run(video_id, "[]")
    reviewer_id = test_db.get_user_by_username("enforcer_test")["id"]
    if scenario == "obstruction_first":
        obstruction = _production_review_with_manifest(
            test_db, monkeypatch, tmp_path, video_id=video_id,
            violation_type="Obstruction", run_id=run_id, track_id=35,
            timestamp_sec=10.0, episode_start_sec=8.0, episode_end_sec=16.0,
        )
        first = materialize_case_from_review(test_db, obstruction, reviewer_id)
        parking = _production_review_with_manifest(
            test_db, monkeypatch, tmp_path, video_id=video_id,
            violation_type="Illegal Parking", run_id=run_id, track_id=35,
            timestamp_sec=13.0, episode_start_sec=12.0, episode_end_sec=20.0,
        )
        final = materialize_case_from_review(test_db, parking, reviewer_id)
    else:
        parking = _production_review_with_manifest(
            test_db, monkeypatch, tmp_path, video_id=video_id,
            violation_type="Illegal Parking", run_id=run_id, track_id=35,
            timestamp_sec=10.0, episode_start_sec=8.0, episode_end_sec=16.0,
        )
        obstruction = _production_review_with_manifest(
            test_db, monkeypatch, tmp_path, video_id=video_id,
            violation_type="Obstruction", run_id=run_id, track_id=35,
            timestamp_sec=13.0, episode_start_sec=12.0, episode_end_sec=20.0,
        )
        first = materialize_case_from_review(test_db, parking, reviewer_id)
        final = first

    assert final["violation_id"] == first["violation_id"]
    assert final["fused"] is True
    response = enforcer_client.get("/api/violations/vehicle-groups")
    payload = response.get_json()
    case_group = next(
        group for group in payload["groups"]
        if any(event["db_id"] == first["violation_id"] for event in group["events"])
    )
    assert case_group["vehicle_identity_verified"] is True
    case_event = next(
        event for event in case_group["events"]
        if event["db_id"] == first["violation_id"]
    )
    assert case_event["type"] == "Obstruction"
    assert {
        link["source_review_id"]
        for link in test_db.list_vehicle_grouping_provenance([first["violation_id"]])[first["violation_id"]]
    } == {parking, obstruction}


def test_group_api_resolves_actual_confirmed_links_when_case_ids_differ(
    test_db, enforcer_client, monkeypatch, tmp_path
):
    """The manifest names review rows; direct comparison to case IDs drops identity."""
    video_id = test_db.insert_video("group.mp4", str(tmp_path / "group.mp4"), status="ready")
    run_id = test_db.create_processing_run(video_id, "[]")
    reviewer_id = test_db.get_user_by_username("enforcer_test")["id"]
    for index in range(2):
        test_db.insert_violation(
            video_id=video_id, track_id=40 + index, violation_type="Illegal Parking",
            confidence=.5, frame_number=index, timestamp_sec=float(index),
            status="confirmed",
        )
    case_ids = []
    for violation_type in ("Illegal Parking", "Obstruction"):
        review_id = _production_review_with_manifest(
            test_db, monkeypatch, tmp_path, video_id=video_id,
            violation_type=violation_type, run_id=run_id,
        )
        case_ids.append(test_db.confirm_review_item(review_id, reviewer_id))
    assert all(case_id != review_id for case_id, review_id in zip(case_ids, (1, 2)))

    response = enforcer_client.get("/api/violations/vehicle-groups")

    payload = response.get_json()
    proven = [group for group in payload["groups"] if group["vehicle_identity_verified"]]
    assert response.status_code == 200
    assert len(proven) == 1
    assert {event["type"] for event in proven[0]["events"]} == {
        "Illegal Parking", "Obstruction"
    }


def test_group_api_keeps_identity_after_real_canonical_correction(
    test_db, enforcer_client, monkeypatch, tmp_path
):
    """An authorized decision can change the case label without changing source identity."""
    from core.review_decision_service import DECISION_CORRECT, apply_review_decision

    video_id = test_db.insert_video("corrected.mp4", str(tmp_path / "corrected.mp4"), status="ready")
    run_id = test_db.create_processing_run(video_id, "[]")
    reviewer_id = test_db.get_user_by_username("enforcer_test")["id"]
    review_id = _production_review_with_manifest(
        test_db, monkeypatch, tmp_path, video_id=video_id,
        violation_type="Counterflow", run_id=run_id,
    )
    result = apply_review_decision(
        test_db, review_id, reviewer_id, decision=DECISION_CORRECT,
        selected_canonical_rule="Obstruction",
        reason="The authorized reviewer selected the obstructed-lane rule.",
        idempotency_key="group-correction",
    )

    response = enforcer_client.get("/api/violations/vehicle-groups")

    payload = response.get_json()
    corrected = next(group for group in payload["groups"] if group["vehicle_identity_verified"])
    assert corrected["events"][0]["db_id"] == result["violation_id"]
    assert corrected["events"][0]["type"] == "Obstruction"


def test_grouping_provenance_supports_multiple_reviews_materialized_to_one_case(
    test_db, enforcer_client, monkeypatch, tmp_path
):
    video_id = test_db.insert_video("fused.mp4", str(tmp_path / "fused.mp4"), status="ready")
    run_id = test_db.create_processing_run(video_id, "[]")
    reviewer_id = test_db.get_user_by_username("enforcer_test")["id"]
    primary = _production_review_with_manifest(
        test_db, monkeypatch, tmp_path, video_id=video_id,
        violation_type="Counterflow", run_id=run_id,
    )
    contributing = _production_review_with_manifest(
        test_db, monkeypatch, tmp_path, video_id=video_id,
        violation_type="Obstruction", run_id=run_id,
    )
    policy_id = test_db.ensure_config_mapping_policy_version()
    case_id = test_db.create_case_with_materialization_intent(
        primary, reviewer_id,
        policy_version_id=policy_id,
        contributing_rules=["Counterflow", "Obstruction"],
        review_ids=[primary, contributing],
        additional_link_review_ids=[contributing],
    )

    response = enforcer_client.get("/api/violations/vehicle-groups")
    payload = response.get_json()
    case_links = test_db.list_vehicle_grouping_provenance([case_id])[case_id]
    grouped_case = next(
        group for group in payload["groups"]
        if group["vehicle_identity_verified"]
        and group["events"][0]["db_id"] == case_id
    )

    assert {link["source_review_id"] for link in case_links} == {primary, contributing}
    assert grouped_case["events"][0]["type"] == "Counterflow"


def test_unlinked_review_manifest_and_unrecorded_type_change_fail_closed(tmp_path):
    row = _event(tmp_path, 8, vtype="Counterflow")
    provenance = _provenance_for([row])
    payload_path = Path(row["evidence_sequence_dir"]) / "event_identity.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["review_id"] = 999
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    unlinked = group_vehicle_events(
        [row], evidence_root=tmp_path, provenance_by_case=provenance
    )
    assert unlinked[0]["vehicle_identity_verified"] is False

    row = _event(tmp_path, 9, vtype="Counterflow")
    row["violation_type"] = "Obstruction"
    forged_correction = group_vehicle_events(
        [row], evidence_root=tmp_path, provenance_by_case=_provenance_for([row])
    )
    assert forged_correction[0]["vehicle_identity_verified"] is False
def test_groups_only_events_with_same_source_run_track_and_identity_epoch(tmp_path):
    first = _event(tmp_path, 1)
    second = _event(tmp_path, 2, vtype="Obstruction")
    reused = _event(tmp_path, 3, epoch=3)
    other_run = _event(tmp_path, 4, run=11)
    other_video = _event(tmp_path, 5, video=2)

    rows = [first, second, reused, other_run, other_video]
    groups = group_vehicle_events(
        rows, evidence_root=tmp_path, provenance_by_case=_provenance_for(rows)
    )

    assert len(groups) == 4
    paired = next(group for group in groups if len(group["events"]) == 2)
    assert [event["id"] for event in paired["events"]] == [1, 2]
    assert paired["vehicle_identity_verified"] is True


def test_missing_mismatched_or_unsafe_provenance_stays_individual(tmp_path):
    good = _event(tmp_path, 1)
    missing = {**good, "id": 2, "evidence_sequence_dir": ""}
    mismatched = _event(tmp_path, 3, track=8)
    mismatched["track_id"] = 7
    unsafe = {**good, "id": 4, "evidence_sequence_dir": str(tmp_path.parent)}

    rows = [good, missing, mismatched, unsafe]
    groups = group_vehicle_events(
        rows, evidence_root=tmp_path, provenance_by_case=_provenance_for(rows)
    )

    assert all(len(group["events"]) == 1 for group in groups)
    assert sum(group["vehicle_identity_verified"] for group in groups) == 1


@pytest.mark.parametrize("field,value", [
    ("video_id", True), ("processing_run_id", 1.5), ("track_id", None),
    ("track_identity_epoch", "2"), ("version", float("inf")),
    ("review_id", -1), ("video_id", 2**63),
])
def test_malformed_identity_values_fail_closed(tmp_path, field, value):
    row = _event(tmp_path, 1)
    manifest = Path(row["evidence_sequence_dir"]) / "event_identity.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload[field] = value
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    groups = group_vehicle_events(
        [row], evidence_root=tmp_path, provenance_by_case=_provenance_for([row])
    )

    assert len(groups) == 1
    assert groups[0]["vehicle_identity_verified"] is False


@pytest.mark.parametrize("raw", [
    '{"version":1,"video_id":NaN}',
    '{"version":1,"video_id":1e309}',
])
def test_nonfinite_json_identity_values_fail_closed(tmp_path, raw):
    row = _event(tmp_path, 1)
    manifest = Path(row["evidence_sequence_dir"]) / "event_identity.json"
    manifest.write_text(raw, encoding="utf-8")

    groups = group_vehicle_events(
        [row], evidence_root=tmp_path, provenance_by_case=_provenance_for([row])
    )

    assert groups[0]["vehicle_identity_verified"] is False


def test_manifest_symlink_outside_evidence_root_fails_closed(tmp_path):
    import os

    row = _event(tmp_path, 1)
    manifest = Path(row["evidence_sequence_dir"]) / "event_identity.json"
    outside = tmp_path.parent / f"{tmp_path.name}-identity.json"
    outside.write_text(manifest.read_text(encoding="utf-8"), encoding="utf-8")
    manifest.unlink()
    try:
        os.symlink(outside, manifest)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable on this Windows test host")

    groups = group_vehicle_events(
        [row], evidence_root=tmp_path, provenance_by_case=_provenance_for([row])
    )

    assert groups[0]["vehicle_identity_verified"] is False


def test_unassociated_events_remain_separate_without_unique_event_ids(tmp_path):
    first = {"id": None, "video_id": 1, "evidence_sequence_dir": None}
    second = {"id": None, "video_id": 1, "evidence_sequence_dir": None}

    groups = group_vehicle_events([first, second], evidence_root=tmp_path)

    assert len(groups) == 2
    assert all(group["vehicle_identity_verified"] is False for group in groups)


def test_api_paginates_groups_after_grouping_filtered_events(enforcer_client, monkeypatch, tmp_path):
    import app as flask_app

    first = _event(tmp_path, 1)
    second = _event(tmp_path, 2)
    third = _event(tmp_path, 3, track=8)
    first["detected_at"] = "2026-01-01 08:00:00"
    second["detected_at"] = "2026-01-01 08:05:00"
    third["detected_at"] = "2026-01-01 08:10:00"
    calls = {}

    def list_events(*, filters, page, per_page, sort):
        calls.update(filters=filters, page=page, per_page=per_page, sort=sort)
        return [third, second, first], 3

    monkeypatch.setattr(flask_app.db, "list_violations", list_events)
    monkeypatch.setattr(
        flask_app.db, "list_vehicle_grouping_provenance",
        lambda _ids: _provenance_for([third, second, first]),
    )
    monkeypatch.setattr(flask_app, "_video_name_map", lambda: {})
    monkeypatch.setattr(flask_app, "_case_actions_for_user", lambda _actor: {})
    monkeypatch.setattr(flask_app, "_violation_to_ui", lambda row, _videos: row)
    monkeypatch.setattr(flask_app.config, "EVIDENCE_FOLDER", str(tmp_path))

    response = enforcer_client.get(
        "/api/violations/vehicle-groups?per_page=1&page=2&status=confirmed"
    )

    payload = response.get_json()
    assert response.status_code == 200
    assert calls["filters"] == {"status": "confirmed"}
    assert calls["page"] == 1 and calls["per_page"] == 101
    assert payload["event_total"] == 3 and payload["total"] == 2
    assert [event["id"] for event in payload["groups"][0]["events"]] == [2, 1]


def test_group_api_refuses_partial_grouping_above_bound(enforcer_client, monkeypatch):
    import app as flask_app

    monkeypatch.setattr(
        flask_app.db, "list_violations",
        lambda **_kwargs: ([], 101),
    )
    monkeypatch.setattr(flask_app.db, "list_vehicle_grouping_provenance", lambda _ids: {})
    response = enforcer_client.get("/api/violations/vehicle-groups")

    assert response.status_code == 413
    assert response.get_json()["event_total"] == 101
    assert "Narrow the filters" in response.get_json()["error"]




def test_group_api_counts_proven_vehicles_and_unassociated_events_separately(
    enforcer_client, monkeypatch, tmp_path
):
    import app as flask_app

    proven = _event(tmp_path, 1)
    unassociated = {**proven, "id": 2, "evidence_sequence_dir": None}
    monkeypatch.setattr(flask_app.db, "list_violations", lambda **_kwargs: ([proven, unassociated], 2))
    monkeypatch.setattr(
        flask_app.db, "list_vehicle_grouping_provenance",
        lambda _ids: _provenance_for([proven, unassociated]),
    )
    monkeypatch.setattr(flask_app, "_video_name_map", lambda: {})
    monkeypatch.setattr(flask_app, "_case_actions_for_user", lambda _actor: {})
    monkeypatch.setattr(flask_app, "_violation_to_ui", lambda row, _videos: row)
    monkeypatch.setattr(flask_app.config, "EVIDENCE_FOLDER", str(tmp_path))

    response = enforcer_client.get("/api/violations/vehicle-groups")

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["total"] == payload["group_total"] == 2
    assert payload["verified_vehicle_total"] == 1
    assert payload["unassociated_event_total"] == 1
    assert payload["event_total"] == 2
