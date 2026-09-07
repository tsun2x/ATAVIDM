"""Tests for stable case identity and parking/obstruction grouping."""

from __future__ import annotations

import os
import tempfile

import pytest

from core.case_identity import (
    build_case_group,
    can_fuse_parking_obstruction,
    episodes_overlap,
    observation_from_review_row,
)
from core.case_review_service import CaseReviewError, materialize_case_from_review
from core.detection_config import (
    VIOLATION_COUNTERFLOW,
    VIOLATION_ILLEGAL_PARKING,
    VIOLATION_ILLEGAL_TERMINAL,
    VIOLATION_OBSTRUCTION,
)


@pytest.fixture
def test_db_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_tavidm.db")
        assert "database/tavidm.db" not in db_path.replace("\\", "/")
        os.environ["SQLITE_PATH"] = db_path
        os.environ["DATABASE_URL"] = db_path
        yield db_path


@pytest.fixture
def test_db(test_db_path):
    from database import sqlite_adapter

    sqlite_adapter.init_db(force=True)
    return sqlite_adapter


@pytest.fixture
def enforcer(test_db):
    return test_db.create_user("enf", "hash", role="enforcer", full_name="Enforcer")


@pytest.fixture
def admin(test_db):
    return test_db.create_user("adm0", "hash", role="admin", full_name="Admin")


@pytest.fixture
def video_id(test_db):
    return test_db.insert_video(
        filename="case.mp4", filepath="/tmp/case.mp4", status="ready"
    )


def _queue(
    test_db,
    video_id,
    *,
    vtype,
    track_id=7,
    run_id=1,
    ts=10.0,
    ep_start=8.0,
    ep_end=20.0,
    frame=300,
):
    return test_db.insert_review_queue(
        video_id=video_id,
        track_id=track_id,
        violation_type=vtype,
        confidence=0.9,
        frame_number=frame,
        timestamp_sec=ts,
        episode_start_sec=ep_start,
        episode_end_sec=ep_end,
        processing_run_id=run_id,
        evidence_path=f"/tmp/{vtype}.jpg",
        reason_log=f"detected {vtype}",
    )


class TestGroupingHelpers:
    def test_overlapping_parking_obstruction_fuses(self):
        a = observation_from_review_row(
            {
                "id": 1,
                "video_id": 1,
                "track_id": 2,
                "processing_run_id": 3,
                "violation_type": VIOLATION_ILLEGAL_PARKING,
                "timestamp_sec": 12.0,
                "episode_start_sec": 10.0,
                "episode_end_sec": 25.0,
            }
        )
        b = observation_from_review_row(
            {
                "id": 2,
                "video_id": 1,
                "track_id": 2,
                "processing_run_id": 3,
                "violation_type": VIOLATION_OBSTRUCTION,
                "timestamp_sec": 15.0,
                "episode_start_sec": 11.0,
                "episode_end_sec": 22.0,
            }
        )
        assert episodes_overlap(a, b)
        assert can_fuse_parking_obstruction(a, b)
        group = build_case_group(a, [b])
        assert group.is_fused_parking_obstruction
        assert group.primary_canonical_rule == VIOLATION_OBSTRUCTION
        assert group.proposed_official_category == "Obstruction of Traffic Flow"
        assert set(group.contributing_rules) == {
            VIOLATION_ILLEGAL_PARKING,
            VIOLATION_OBSTRUCTION,
        }

    def test_different_run_not_fused(self):
        a = observation_from_review_row(
            {
                "id": 1,
                "video_id": 1,
                "track_id": 2,
                "processing_run_id": 1,
                "violation_type": VIOLATION_ILLEGAL_PARKING,
                "timestamp_sec": 12.0,
                "episode_start_sec": 10.0,
                "episode_end_sec": 25.0,
            }
        )
        b = observation_from_review_row(
            {
                "id": 2,
                "video_id": 1,
                "track_id": 2,
                "processing_run_id": 2,
                "violation_type": VIOLATION_OBSTRUCTION,
                "timestamp_sec": 15.0,
                "episode_start_sec": 11.0,
                "episode_end_sec": 22.0,
            }
        )
        assert not can_fuse_parking_obstruction(a, b)
        group = build_case_group(a, [b])
        assert not group.is_fused_parking_obstruction
        assert len(group.observations) == 1

    def test_non_overlapping_episodes_not_fused(self):
        a = observation_from_review_row(
            {
                "id": 1,
                "video_id": 1,
                "track_id": 2,
                "processing_run_id": 1,
                "violation_type": VIOLATION_ILLEGAL_PARKING,
                "timestamp_sec": 5.0,
                "episode_start_sec": 0.0,
                "episode_end_sec": 8.0,
            }
        )
        b = observation_from_review_row(
            {
                "id": 2,
                "video_id": 1,
                "track_id": 2,
                "processing_run_id": 1,
                "violation_type": VIOLATION_OBSTRUCTION,
                "timestamp_sec": 40.0,
                "episode_start_sec": 30.0,
                "episode_end_sec": 50.0,
            }
        )
        assert not can_fuse_parking_obstruction(a, b)

    def test_standalone_parking_and_unrelated_types(self):
        a = observation_from_review_row(
            {
                "id": 1,
                "video_id": 1,
                "track_id": 2,
                "processing_run_id": 1,
                "violation_type": VIOLATION_ILLEGAL_PARKING,
                "timestamp_sec": 12.0,
                "episode_start_sec": 10.0,
                "episode_end_sec": 25.0,
            }
        )
        b = observation_from_review_row(
            {
                "id": 2,
                "video_id": 1,
                "track_id": 2,
                "processing_run_id": 1,
                "violation_type": VIOLATION_COUNTERFLOW,
                "timestamp_sec": 15.0,
                "episode_start_sec": 11.0,
                "episode_end_sec": 22.0,
            }
        )
        group = build_case_group(a, [b])
        assert not group.is_fused_parking_obstruction
        assert group.primary_canonical_rule == VIOLATION_ILLEGAL_PARKING


class TestMaterializeCase:
    def test_fused_case_one_violation_two_behaviors(
        self, test_db, enforcer, video_id
    ):
        park = _queue(test_db, video_id, vtype=VIOLATION_ILLEGAL_PARKING)
        obs = _queue(test_db, video_id, vtype=VIOLATION_OBSTRUCTION, ts=12.0)
        result = materialize_case_from_review(test_db, park, enforcer)
        assert result["fused"] is True
        assert result["reused_existing"] is False
        vid = result["violation_id"]
        assert set(result["contributing_rules"]) == {
            VIOLATION_ILLEGAL_PARKING,
            VIOLATION_OBSTRUCTION,
        }

        # Second confirm reuses the same case identity.
        result2 = materialize_case_from_review(test_db, obs, enforcer)
        assert result2["violation_id"] == vid
        assert result2["reused_existing"] is True

        with test_db.db_session() as conn:
            viols = conn.execute("SELECT id, violation_type FROM violations").fetchall()
            assert len(viols) == 1
            assert viols[0]["violation_type"] == VIOLATION_OBSTRUCTION
            reviews = conn.execute(
                "SELECT status FROM review_queue ORDER BY id"
            ).fetchall()
            assert all(r["status"] == "confirmed" for r in reviews)

        policies = test_db.get_case_policy_records(vid)
        rules = {p["canonical_rule"] for p in policies}
        assert rules == {VIOLATION_ILLEGAL_PARKING, VIOLATION_OBSTRUCTION}
        assert all(
            p["official_category"] == "Obstruction of Traffic Flow" for p in policies
        )

        # Stable review→violation links (no second identity).
        assert test_db.find_violation_linked_to_review(park) == vid
        assert test_db.find_violation_linked_to_review(obs) == vid

    def test_independent_events_remain_separate(
        self, test_db, enforcer, video_id
    ):
        a = _queue(test_db, video_id, vtype=VIOLATION_ILLEGAL_PARKING, track_id=1)
        b = _queue(
            test_db,
            video_id,
            vtype=VIOLATION_OBSTRUCTION,
            track_id=2,
            ts=12.0,
        )
        r1 = materialize_case_from_review(test_db, a, enforcer)
        r2 = materialize_case_from_review(test_db, b, enforcer)
        assert r1["violation_id"] != r2["violation_id"]
        assert r1["fused"] is False
        assert r2["fused"] is False

    def test_different_processing_runs_not_merged(
        self, test_db, enforcer, video_id
    ):
        a = _queue(test_db, video_id, vtype=VIOLATION_ILLEGAL_PARKING, run_id=10)
        b = _queue(
            test_db,
            video_id,
            vtype=VIOLATION_OBSTRUCTION,
            run_id=11,
            ts=12.0,
        )
        r1 = materialize_case_from_review(test_db, a, enforcer)
        r2 = materialize_case_from_review(test_db, b, enforcer)
        assert r1["violation_id"] != r2["violation_id"]

    def test_standalone_parking_preserved(self, test_db, enforcer, video_id):
        rid = _queue(test_db, video_id, vtype=VIOLATION_ILLEGAL_PARKING)
        result = materialize_case_from_review(test_db, rid, enforcer)
        assert result["fused"] is False
        viol = test_db.get_violation(result["violation_id"])
        assert viol["violation_type"] == VIOLATION_ILLEGAL_PARKING
        assert result["proposed_official_category"] == "Obstruction of Traffic Flow"
        assert result["verified_official_category"] is None
        policies = test_db.get_case_policy_records(result["violation_id"])
        assert {p["canonical_rule"] for p in policies} == {VIOLATION_ILLEGAL_PARKING}
        assert all(
            p["official_category"] == "Obstruction of Traffic Flow" for p in policies
        )

    def test_illegal_terminal_not_confused_with_fusion(
        self, test_db, enforcer, video_id
    ):
        park = _queue(test_db, video_id, vtype=VIOLATION_ILLEGAL_PARKING)
        term = _queue(
            test_db,
            video_id,
            vtype=VIOLATION_ILLEGAL_TERMINAL,
            ts=12.0,
        )
        r1 = materialize_case_from_review(test_db, park, enforcer)
        r2 = materialize_case_from_review(test_db, term, enforcer)
        assert r1["violation_id"] != r2["violation_id"]
        assert r1["fused"] is False

    def test_evidence_preserved_from_siblings(self, test_db, enforcer, video_id):
        park = test_db.insert_review_queue(
            video_id=video_id,
            track_id=7,
            violation_type=VIOLATION_ILLEGAL_PARKING,
            confidence=0.9,
            frame_number=300,
            timestamp_sec=10.0,
            episode_start_sec=8.0,
            episode_end_sec=20.0,
            processing_run_id=1,
            evidence_path="/tmp/park.jpg",
        )
        obs = test_db.insert_review_queue(
            video_id=video_id,
            track_id=7,
            violation_type=VIOLATION_OBSTRUCTION,
            confidence=0.91,
            frame_number=310,
            timestamp_sec=12.0,
            episode_start_sec=9.0,
            episode_end_sec=21.0,
            processing_run_id=1,
            vehicle_evidence_path="/tmp/vehicle.jpg",
            plate_evidence_path="/tmp/plate.jpg",
        )
        result = materialize_case_from_review(test_db, park, enforcer)
        # Attach obstruction evidence via second confirm path.
        materialize_case_from_review(test_db, obs, enforcer)
        viol = test_db.get_violation(result["violation_id"])
        assert viol["evidence_path"] == "/tmp/park.jpg"
        assert viol["vehicle_evidence_path"] == "/tmp/vehicle.jpg"
        assert viol["plate_evidence_path"] == "/tmp/plate.jpg"

    def test_repeated_confirm_idempotent(self, test_db, enforcer, video_id):
        rid = _queue(test_db, video_id, vtype=VIOLATION_OBSTRUCTION)
        r1 = materialize_case_from_review(test_db, rid, enforcer)
        r2 = materialize_case_from_review(test_db, rid, enforcer)
        assert r2["reused_existing"] is True
        assert r2["violation_id"] == r1["violation_id"]
        with test_db.db_session() as conn:
            count = conn.execute("SELECT COUNT(*) AS c FROM violations").fetchone()["c"]
            assert count == 1

    def test_distinct_episodes_same_track_remain_separate(
        self, test_db, enforcer, video_id
    ):
        p1 = _queue(
            test_db,
            video_id,
            vtype=VIOLATION_ILLEGAL_PARKING,
            ts=15.0,
            ep_start=10.0,
            ep_end=20.0,
            frame=450,
        )
        o1 = _queue(
            test_db,
            video_id,
            vtype=VIOLATION_OBSTRUCTION,
            ts=16.0,
            ep_start=11.0,
            ep_end=19.0,
            frame=480,
        )
        r1 = materialize_case_from_review(test_db, p1, enforcer)
        materialize_case_from_review(test_db, o1, enforcer)

        p2 = _queue(
            test_db,
            video_id,
            vtype=VIOLATION_ILLEGAL_PARKING,
            ts=105.0,
            ep_start=100.0,
            ep_end=110.0,
            frame=3150,
        )
        o2 = _queue(
            test_db,
            video_id,
            vtype=VIOLATION_OBSTRUCTION,
            ts=106.0,
            ep_start=101.0,
            ep_end=109.0,
            frame=3180,
        )
        r2 = materialize_case_from_review(test_db, p2, enforcer)
        r2b = materialize_case_from_review(test_db, o2, enforcer)
        assert r1["violation_id"] != r2["violation_id"]
        assert r2b["violation_id"] == r2["violation_id"]
        with test_db.db_session() as conn:
            count = conn.execute("SELECT COUNT(*) AS c FROM violations").fetchone()["c"]
            assert count == 2

    def test_late_obstruction_attaches_to_parking_case(
        self, test_db, enforcer, video_id
    ):
        park = _queue(test_db, video_id, vtype=VIOLATION_ILLEGAL_PARKING)
        first = materialize_case_from_review(test_db, park, enforcer)
        assert first["fused"] is False
        obs = _queue(
            test_db,
            video_id,
            vtype=VIOLATION_OBSTRUCTION,
            ts=12.0,
            ep_start=9.0,
            ep_end=21.0,
        )
        second = materialize_case_from_review(test_db, obs, enforcer)
        assert second["violation_id"] == first["violation_id"]
        assert second["reused_existing"] is True
        assert second["fused"] is True
        assert set(second["contributing_rules"]) == {
            VIOLATION_ILLEGAL_PARKING,
            VIOLATION_OBSTRUCTION,
        }
        viol = test_db.get_violation(first["violation_id"])
        assert viol["violation_type"] == VIOLATION_OBSTRUCTION
        policies = test_db.get_case_policy_records(first["violation_id"])
        assert {p["canonical_rule"] for p in policies} == {
            VIOLATION_ILLEGAL_PARKING,
            VIOLATION_OBSTRUCTION,
        }

    def test_late_parking_attaches_to_obstruction_case(
        self, test_db, enforcer, video_id
    ):
        obs = _queue(test_db, video_id, vtype=VIOLATION_OBSTRUCTION)
        first = materialize_case_from_review(test_db, obs, enforcer)
        park = _queue(
            test_db,
            video_id,
            vtype=VIOLATION_ILLEGAL_PARKING,
            ts=12.0,
            ep_start=9.0,
            ep_end=21.0,
        )
        second = materialize_case_from_review(test_db, park, enforcer)
        assert second["violation_id"] == first["violation_id"]
        assert second["fused"] is True

    def test_ambiguous_episode_data_not_auto_merged(self):
        a = observation_from_review_row(
            {
                "id": 1,
                "video_id": 1,
                "track_id": 2,
                "processing_run_id": 1,
                "violation_type": VIOLATION_ILLEGAL_PARKING,
                "timestamp_sec": None,
                "episode_start_sec": None,
                "episode_end_sec": None,
                "frame_number": 300,
            }
        )
        b = observation_from_review_row(
            {
                "id": 2,
                "video_id": 1,
                "track_id": 2,
                "processing_run_id": 1,
                "violation_type": VIOLATION_OBSTRUCTION,
                "timestamp_sec": None,
                "episode_start_sec": None,
                "episode_end_sec": None,
                "frame_number": 310,
            }
        )
        assert a.episode_identity_uncertain
        assert not episodes_overlap(a, b)
        assert not can_fuse_parking_obstruction(a, b)
        group = build_case_group(a, [b])
        assert not group.is_fused_parking_obstruction
        assert group.episode_identity_uncertain

    def test_dismissed_case_not_reused(self, test_db, enforcer, video_id):
        park = _queue(test_db, video_id, vtype=VIOLATION_ILLEGAL_PARKING)
        first = materialize_case_from_review(test_db, park, enforcer)
        test_db.update_violation_status(first["violation_id"], "dismissed")
        obs = _queue(
            test_db,
            video_id,
            vtype=VIOLATION_OBSTRUCTION,
            ts=12.0,
            ep_start=9.0,
            ep_end=21.0,
        )
        second = materialize_case_from_review(test_db, obs, enforcer)
        assert second["violation_id"] != first["violation_id"]

    def test_admin_can_materialize(self, test_db, video_id):
        admin = test_db.create_user("adm", "hash", role="admin")
        rid = _queue(test_db, video_id, vtype=VIOLATION_ILLEGAL_PARKING)
        result = materialize_case_from_review(test_db, rid, admin)
        assert result["violation_id"] > 0
        assert result["proposed_official_category"] == "Obstruction of Traffic Flow"
        assert test_db.get_review_item(rid)["status"] == "confirmed"

    def test_supervisor_with_confirm_case_can_materialize(self, test_db, video_id):
        admin = test_db.create_user("adm2", "hash", role="admin")
        sup = test_db.create_user("sup", "hash", role="viewer")
        test_db.assign_policy_permission(sup, "confirm_case", granted_by=admin)
        rid = _queue(test_db, video_id, vtype=VIOLATION_ILLEGAL_PARKING)
        result = materialize_case_from_review(test_db, rid, sup)
        assert result["violation_id"] > 0

    def test_snapshot_failure_retry_completes_standalone(
        self, test_db, enforcer, video_id, monkeypatch
    ):
        rid = _queue(test_db, video_id, vtype=VIOLATION_ILLEGAL_PARKING)
        real = test_db.create_case_policy_record
        calls = {"n": 0}

        def boom(*args, **kwargs):
            calls["n"] += 1
            raise RuntimeError("snap boom")

        monkeypatch.setattr(test_db, "create_case_policy_record", boom)
        with pytest.raises(RuntimeError, match="snap boom"):
            materialize_case_from_review(test_db, rid, enforcer)
        monkeypatch.setattr(test_db, "create_case_policy_record", real)

        result = materialize_case_from_review(test_db, rid, enforcer)
        assert result["reused_existing"] is True
        policies = test_db.get_case_policy_records(result["violation_id"])
        assert {p["canonical_rule"] for p in policies} == {VIOLATION_ILLEGAL_PARKING}
        assert test_db.find_violation_linked_to_review(rid) == result["violation_id"]

    def test_partial_fused_snapshot_retry_completes(
        self, test_db, enforcer, video_id, monkeypatch
    ):
        park = _queue(test_db, video_id, vtype=VIOLATION_ILLEGAL_PARKING)
        obs = _queue(
            test_db,
            video_id,
            vtype=VIOLATION_OBSTRUCTION,
            ts=12.0,
            ep_start=9.0,
            ep_end=21.0,
        )
        real = test_db.create_case_policy_record
        seen = {"n": 0}

        def fail_second(*args, **kwargs):
            seen["n"] += 1
            if seen["n"] >= 2:
                raise RuntimeError("second snap boom")
            return real(*args, **kwargs)

        monkeypatch.setattr(test_db, "create_case_policy_record", fail_second)
        with pytest.raises(RuntimeError, match="second snap boom"):
            materialize_case_from_review(test_db, park, enforcer)
        monkeypatch.setattr(test_db, "create_case_policy_record", real)

        result = materialize_case_from_review(test_db, park, enforcer)
        assert result["fused"] is True
        policies = test_db.get_case_policy_records(result["violation_id"])
        assert {p["canonical_rule"] for p in policies} == {
            VIOLATION_ILLEGAL_PARKING,
            VIOLATION_OBSTRUCTION,
        }
        # Idempotent second retry — no duplicate rows.
        again = materialize_case_from_review(test_db, park, enforcer)
        assert again["violation_id"] == result["violation_id"]
        policies2 = test_db.get_case_policy_records(result["violation_id"])
        assert len(policies2) == 2
        # Obstruction sibling can still attach/complete.
        materialize_case_from_review(test_db, obs, enforcer)
        assert test_db.find_violation_linked_to_review(obs) == result["violation_id"]

    def test_retry_preserves_original_policy_version(
        self, test_db, enforcer, video_id, admin, monkeypatch
    ):
        # Capture version at first attempt, then change active policy before retry.
        v1 = test_db.ensure_config_mapping_policy_version()
        rid = _queue(test_db, video_id, vtype=VIOLATION_ILLEGAL_PARKING)
        real = test_db.create_case_policy_record

        def boom(*args, **kwargs):
            raise RuntimeError("snap boom")

        monkeypatch.setattr(test_db, "create_case_policy_record", boom)
        with pytest.raises(RuntimeError):
            materialize_case_from_review(test_db, rid, enforcer)
        monkeypatch.setattr(test_db, "create_case_policy_record", real)

        sup = test_db.create_user("supv", "hash", role="viewer")
        test_db.assign_policy_permission(sup, "approve_policy", granted_by=admin)
        v2 = test_db.propose_legal_policy_version(
            version="after-fail",
            created_by=admin,
            lookback_days=90,
            detail_json={"offense_suggestions_enabled": False},
        )
        test_db.approve_legal_policy_version(v2, approved_by=sup)
        assert test_db.get_active_legal_policy_version()["id"] == v2

        result = materialize_case_from_review(test_db, rid, enforcer)
        policies = test_db.get_case_policy_records(result["violation_id"])
        assert len(policies) == 1
        assert int(policies[0]["policy_version_id"]) == int(v1)

    def test_unauthorized_retry_makes_no_changes(
        self, test_db, enforcer, video_id, monkeypatch
    ):
        rid = _queue(test_db, video_id, vtype=VIOLATION_ILLEGAL_PARKING)
        real = test_db.create_case_policy_record

        def boom(*args, **kwargs):
            raise RuntimeError("snap boom")

        monkeypatch.setattr(test_db, "create_case_policy_record", boom)
        with pytest.raises(RuntimeError):
            materialize_case_from_review(test_db, rid, enforcer)
        monkeypatch.setattr(test_db, "create_case_policy_record", real)

        viewer = test_db.create_user("badview", "hash", role="viewer")
        before = test_db.get_case_policy_records(
            test_db.find_violation_linked_to_review(rid)
        )
        with pytest.raises(PermissionError):
            materialize_case_from_review(test_db, rid, viewer)
        after = test_db.get_case_policy_records(
            test_db.find_violation_linked_to_review(rid)
        )
        assert before == after

    def test_intent_failure_rolls_back_initial_case(
        self, test_db, enforcer, video_id, monkeypatch
    ):
        """Intent insert failure must not leave case/link/confirm orphans."""
        test_db.ensure_config_mapping_policy_version()
        rid = _queue(test_db, video_id, vtype=VIOLATION_ILLEGAL_PARKING)
        real = test_db.create_case_with_materialization_intent

        def boom(*args, **kwargs):
            kwargs = dict(kwargs)
            kwargs["_fail_after"] = "intent"
            return real(*args, **kwargs)

        monkeypatch.setattr(test_db, "create_case_with_materialization_intent", boom)
        with pytest.raises(RuntimeError, match="intent boom"):
            materialize_case_from_review(test_db, rid, enforcer)

        assert test_db.get_review_item(rid)["status"] == "pending"
        assert test_db.find_violation_linked_to_review(rid) is None
        with test_db.db_session() as conn:
            actions = conn.execute(
                "SELECT COUNT(*) AS n FROM case_action_events WHERE review_id = ?",
                (rid,),
            ).fetchone()["n"]
            viols = conn.execute(
                "SELECT COUNT(*) AS n FROM violations WHERE video_id = ?",
                (video_id,),
            ).fetchone()["n"]
        assert actions == 0
        assert viols == 0
        assert test_db.get_review_item(rid)["status"] == "pending"

    def test_intent_rollback_then_policy_change_is_fresh_attempt(
        self, test_db, enforcer, video_id, admin, monkeypatch
    ):
        """Fully rolled-back attempt leaves no historical policy binding."""
        test_db.ensure_config_mapping_policy_version()
        rid = _queue(test_db, video_id, vtype=VIOLATION_ILLEGAL_PARKING)
        real = test_db.create_case_with_materialization_intent

        def boom(*args, **kwargs):
            kwargs = dict(kwargs)
            kwargs["_fail_after"] = "intent"
            return real(*args, **kwargs)

        monkeypatch.setattr(test_db, "create_case_with_materialization_intent", boom)
        with pytest.raises(RuntimeError, match="intent boom"):
            materialize_case_from_review(test_db, rid, enforcer)
        monkeypatch.setattr(test_db, "create_case_with_materialization_intent", real)

        sup = test_db.create_user("sup_intent", "hash", role="viewer")
        test_db.assign_policy_permission(sup, "approve_policy", granted_by=admin)
        v2 = test_db.propose_legal_policy_version(
            version="after-intent-rollback",
            created_by=admin,
            lookback_days=90,
            detail_json={"offense_suggestions_enabled": False},
        )
        test_db.approve_legal_policy_version(v2, approved_by=sup)
        assert test_db.get_active_legal_policy_version()["id"] == v2

        result = materialize_case_from_review(test_db, rid, enforcer)
        policies = test_db.get_case_policy_records(result["violation_id"])
        assert len(policies) == 1
        assert int(policies[0]["policy_version_id"]) == int(v2)
        # Exactly one case for this review — no duplicate from the rolled-back try.
        assert test_db.find_violation_linked_to_review(rid) == result["violation_id"]
        with test_db.db_session() as conn:
            n = conn.execute(
                "SELECT COUNT(*) AS n FROM violations WHERE video_id = ?",
                (video_id,),
            ).fetchone()["n"]
        assert n == 1

    def test_intent_committed_snapshot_fail_preserves_v1_across_policy_change(
        self, test_db, enforcer, video_id, admin, monkeypatch
    ):
        v1 = test_db.ensure_config_mapping_policy_version()
        rid = _queue(test_db, video_id, vtype=VIOLATION_ILLEGAL_PARKING)
        real_snap = test_db.create_case_policy_record

        def boom(*args, **kwargs):
            raise RuntimeError("snap boom")

        monkeypatch.setattr(test_db, "create_case_policy_record", boom)
        with pytest.raises(RuntimeError, match="snap boom"):
            materialize_case_from_review(test_db, rid, enforcer)
        monkeypatch.setattr(test_db, "create_case_policy_record", real_snap)

        import json as _json

        vid = test_db.find_violation_linked_to_review(rid)
        assert vid is not None
        intent_actions = []
        for a in test_db.get_case_actions(vid):
            try:
                detail = _json.loads(a.get("detail_json") or "{}")
            except Exception:
                continue
            if detail.get("materialization_intent"):
                intent_actions.append(a)
        assert intent_actions, "intent must be durable after case creation"

        sup = test_db.create_user("sup_v1keep", "hash", role="viewer")
        test_db.assign_policy_permission(sup, "approve_policy", granted_by=admin)
        v2 = test_db.propose_legal_policy_version(
            version="after-snap-fail",
            created_by=admin,
            lookback_days=60,
            detail_json={"offense_suggestions_enabled": False},
        )
        test_db.approve_legal_policy_version(v2, approved_by=sup)
        assert test_db.get_active_legal_policy_version()["id"] == v2

        result = materialize_case_from_review(test_db, rid, enforcer)
        assert result["violation_id"] == vid
        policies = test_db.get_case_policy_records(vid)
        assert len(policies) == 1
        assert int(policies[0]["policy_version_id"]) == int(v1)
        # Idempotent retry.
        again = materialize_case_from_review(test_db, rid, enforcer)
        assert again["violation_id"] == vid
        assert len(test_db.get_case_policy_records(vid)) == 1

    def test_historical_case_without_intent_or_snapshots_unresolved(
        self, test_db, enforcer, video_id, admin
    ):
        rid = _queue(test_db, video_id, vtype=VIOLATION_ILLEGAL_PARKING)
        vid = test_db.confirm_review_item(rid, enforcer)
        assert test_db.get_review_item(rid)["status"] == "confirmed"
        assert test_db.get_case_policy_records(vid) == []

        before_actions = test_db.get_case_actions(vid)
        before_status = test_db.get_violation(vid)["status"]

        # Activate a newer policy — must not be used as invented original context.
        sup = test_db.create_user("sup_hist", "hash", role="viewer")
        test_db.assign_policy_permission(sup, "approve_policy", granted_by=admin)
        v2 = test_db.propose_legal_policy_version(
            version="invent-guard",
            created_by=admin,
            lookback_days=30,
            detail_json={"offense_suggestions_enabled": False},
        )
        test_db.approve_legal_policy_version(v2, approved_by=sup)

        with pytest.raises(CaseReviewError, match="unresolved|missing original"):
            materialize_case_from_review(test_db, rid, enforcer)

        assert test_db.get_case_policy_records(vid) == []
        assert test_db.get_case_actions(vid) == before_actions
        assert test_db.get_violation(vid)["status"] == before_status
        assert test_db.find_violation_linked_to_review(rid) == vid

    def test_historical_snapshot_provenance_recovers_under_that_version(
        self, test_db, enforcer, video_id, admin
    ):
        v1 = test_db.ensure_config_mapping_policy_version()
        rid = _queue(test_db, video_id, vtype=VIOLATION_ILLEGAL_PARKING)
        vid = test_db.confirm_review_item(rid, enforcer)
        test_db.create_case_policy_record(
            violation_id=vid,
            policy_version_id=int(v1),
            canonical_rule=VIOLATION_ILLEGAL_PARKING,
            official_category="Illegal Parking",
            legal_status="unverified",
            behavior_details=["illegal_parking"],
        )

        sup = test_db.create_user("sup_snap", "hash", role="viewer")
        test_db.assign_policy_permission(sup, "approve_policy", granted_by=admin)
        v2 = test_db.propose_legal_policy_version(
            version="snap-prov-v2",
            created_by=admin,
            lookback_days=45,
            detail_json={"offense_suggestions_enabled": False},
        )
        test_db.approve_legal_policy_version(v2, approved_by=sup)

        result = materialize_case_from_review(test_db, rid, enforcer)
        assert result["violation_id"] == vid
        policies = test_db.get_case_policy_records(vid)
        assert {int(p["policy_version_id"]) for p in policies} == {int(v1)}

    def test_conflicting_historical_policy_context_unresolved(
        self, test_db, enforcer, video_id, admin
    ):
        v1 = test_db.ensure_config_mapping_policy_version()
        rid = _queue(test_db, video_id, vtype=VIOLATION_ILLEGAL_PARKING)
        vid = test_db.confirm_review_item(rid, enforcer)

        sup = test_db.create_user("sup_conf", "hash", role="viewer")
        test_db.assign_policy_permission(sup, "approve_policy", granted_by=admin)
        v2 = test_db.propose_legal_policy_version(
            version="conflict-v2",
            created_by=admin,
            lookback_days=20,
            detail_json={"offense_suggestions_enabled": False},
        )
        test_db.approve_legal_policy_version(v2, approved_by=sup)

        test_db.create_case_policy_record(
            violation_id=vid,
            policy_version_id=int(v1),
            canonical_rule=VIOLATION_ILLEGAL_PARKING,
            official_category="Illegal Parking",
            legal_status="unverified",
        )
        # Second row with different version (bypass unique via direct SQL).
        with test_db.db_session() as conn:
            conn.execute(
                """
                INSERT INTO case_policy_records
                    (violation_id, review_id, policy_version_id, canonical_rule,
                     official_category, legal_status, behavior_details_json, created_at)
                VALUES (?, NULL, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    vid,
                    int(v2),
                    VIOLATION_OBSTRUCTION,
                    "Obstruction",
                    "unverified",
                    "[]",
                ),
            )

        before = test_db.get_case_policy_records(vid)
        with pytest.raises(CaseReviewError, match="conflict"):
            materialize_case_from_review(test_db, rid, enforcer)
        assert test_db.get_case_policy_records(vid) == before

    def test_fused_intent_failure_rolls_back(
        self, test_db, enforcer, video_id, monkeypatch
    ):
        park = _queue(test_db, video_id, vtype=VIOLATION_ILLEGAL_PARKING)
        obs = _queue(
            test_db,
            video_id,
            vtype=VIOLATION_OBSTRUCTION,
            ts=12.0,
            ep_start=9.0,
            ep_end=21.0,
        )
        real = test_db.create_case_with_materialization_intent

        def boom(*args, **kwargs):
            kwargs = dict(kwargs)
            kwargs["_fail_after"] = "intent"
            return real(*args, **kwargs)

        monkeypatch.setattr(test_db, "create_case_with_materialization_intent", boom)
        with pytest.raises(RuntimeError, match="intent boom"):
            materialize_case_from_review(test_db, park, enforcer)

        assert test_db.get_review_item(park)["status"] == "pending"
        assert test_db.get_review_item(obs)["status"] == "pending"
        assert test_db.find_violation_linked_to_review(park) is None
        assert test_db.find_violation_linked_to_review(obs) is None
        with test_db.db_session() as conn:
            n = conn.execute(
                "SELECT COUNT(*) AS n FROM violations WHERE video_id = ?",
                (video_id,),
            ).fetchone()["n"]
        assert n == 0

    def test_fused_intent_then_snap_fail_preserves_v1(
        self, test_db, enforcer, video_id, admin, monkeypatch
    ):
        v1 = test_db.ensure_config_mapping_policy_version()
        park = _queue(test_db, video_id, vtype=VIOLATION_ILLEGAL_PARKING)
        obs = _queue(
            test_db,
            video_id,
            vtype=VIOLATION_OBSTRUCTION,
            ts=12.0,
            ep_start=9.0,
            ep_end=21.0,
        )
        real = test_db.create_case_policy_record

        def boom(*args, **kwargs):
            raise RuntimeError("fused snap boom")

        monkeypatch.setattr(test_db, "create_case_policy_record", boom)
        with pytest.raises(RuntimeError, match="fused snap boom"):
            materialize_case_from_review(test_db, park, enforcer)
        monkeypatch.setattr(test_db, "create_case_policy_record", real)

        vid = test_db.find_violation_linked_to_review(park)
        assert vid is not None

        sup = test_db.create_user("sup_fused", "hash", role="viewer")
        test_db.assign_policy_permission(sup, "approve_policy", granted_by=admin)
        v2 = test_db.propose_legal_policy_version(
            version="fused-after-fail",
            created_by=admin,
            lookback_days=55,
            detail_json={"offense_suggestions_enabled": False},
        )
        test_db.approve_legal_policy_version(v2, approved_by=sup)

        result = materialize_case_from_review(test_db, park, enforcer)
        assert result["violation_id"] == vid
        policies = test_db.get_case_policy_records(vid)
        assert {int(p["policy_version_id"]) for p in policies} == {int(v1)}
        materialize_case_from_review(test_db, obs, enforcer)
        assert test_db.find_violation_linked_to_review(obs) == vid
        again = materialize_case_from_review(test_db, park, enforcer)
        assert again["violation_id"] == vid

    def test_dismissed_incomplete_recovery_makes_no_changes(
        self, test_db, enforcer, video_id
    ):
        rid = _queue(test_db, video_id, vtype=VIOLATION_ILLEGAL_PARKING)
        vid = test_db.confirm_review_item(rid, enforcer)
        test_db.update_violation_status(vid, "dismissed")
        before_actions = test_db.get_case_actions(vid)
        before_policies = test_db.get_case_policy_records(vid)

        with pytest.raises(CaseReviewError):
            materialize_case_from_review(test_db, rid, enforcer)

        assert test_db.get_case_actions(vid) == before_actions
        assert test_db.get_case_policy_records(vid) == before_policies
        assert test_db.get_violation(vid)["status"] == "dismissed"
