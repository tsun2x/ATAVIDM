"""Owner-approved corrections: grouping, settings groups, admin case-review."""

from __future__ import annotations

import os
import tempfile

import pytest

from core.case_review_service import (
    materialize_case_from_review,
    persist_event_time_review,
    verify_plate_identity,
)
from core.detection_config import (
    VIOLATION_COUNTERFLOW,
    VIOLATION_DISREGARDING_SIGN,
    VIOLATION_ILLEGAL_PARKING,
    VIOLATION_NO_SIDE_MIRROR,
    VIOLATION_OBSTRUCTION,
    VIOLATION_PAVEMENT_MARKINGS,
    VIOLATION_TRUCK_BAN,
)
from core.plate_processing import HUMAN_PLATE_STATUS_VERIFIED_READABLE
from core.violation_config import (
    expand_group_selection_to_canonical,
    save_enabled_violations,
    violation_groups_for_ui,
)
from core.violation_policy import (
    legal_status_for,
    proposed_official_category_for,
    reset_policy_cache,
    verified_official_category_for,
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
    reset_policy_cache()
    return sqlite_adapter


@pytest.fixture
def enforcer(test_db):
    return test_db.create_user("enf", "hash", role="enforcer")


@pytest.fixture
def admin(test_db):
    return test_db.create_user("adm", "hash", role="admin")


@pytest.fixture
def viewer(test_db):
    return test_db.create_user("view", "hash", role="viewer")


@pytest.fixture
def video_id(test_db):
    return test_db.insert_video("g.mp4", "/tmp/g.mp4", status="ready")


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
):
    return test_db.insert_review_queue(
        video_id=video_id,
        track_id=track_id,
        violation_type=vtype,
        confidence=0.9,
        frame_number=300,
        timestamp_sec=ts,
        episode_start_sec=ep_start,
        episode_end_sec=ep_end,
        processing_run_id=run_id,
        evidence_path=f"/tmp/{vtype}.jpg",
        reason_log=f"detected {vtype}",
    )


class TestObstructionTrafficFlowGrouping:
    def test_parking_alone_grouped_category(self, test_db, enforcer, video_id):
        rid = _queue(test_db, video_id, vtype=VIOLATION_ILLEGAL_PARKING)
        result = materialize_case_from_review(test_db, rid, enforcer)
        assert result["proposed_official_category"] == "Obstruction of Traffic Flow"
        assert result["verified_official_category"] is None
        assert result["fused"] is False
        assert VIOLATION_ILLEGAL_PARKING in result["contributing_rules"]
        viol = test_db.get_violation(result["violation_id"])
        assert viol["violation_type"] == VIOLATION_ILLEGAL_PARKING

    def test_obstruction_alone_grouped_category(self, test_db, enforcer, video_id):
        rid = _queue(test_db, video_id, vtype=VIOLATION_OBSTRUCTION)
        result = materialize_case_from_review(test_db, rid, enforcer)
        assert result["proposed_official_category"] == "Obstruction of Traffic Flow"
        assert result["verified_official_category"] is None
        assert VIOLATION_OBSTRUCTION in result["contributing_rules"]

    def test_overlapping_both_one_case(self, test_db, enforcer, video_id):
        park = _queue(test_db, video_id, vtype=VIOLATION_ILLEGAL_PARKING)
        obs = _queue(
            test_db,
            video_id,
            vtype=VIOLATION_OBSTRUCTION,
            ts=12.0,
            ep_start=9.0,
            ep_end=21.0,
        )
        result = materialize_case_from_review(test_db, park, enforcer)
        assert result["fused"] is True
        assert set(result["contributing_rules"]) == {
            VIOLATION_ILLEGAL_PARKING,
            VIOLATION_OBSTRUCTION,
        }
        materialize_case_from_review(test_db, obs, enforcer)
        assert test_db.find_violation_linked_to_review(obs) == result["violation_id"]
        policies = test_db.get_case_policy_records(result["violation_id"])
        assert {p["canonical_rule"] for p in policies} == {
            VIOLATION_ILLEGAL_PARKING,
            VIOLATION_OBSTRUCTION,
        }

    def test_late_observation_fuses_separate_episodes_do_not(
        self, test_db, enforcer, video_id
    ):
        park = _queue(test_db, video_id, vtype=VIOLATION_ILLEGAL_PARKING)
        first = materialize_case_from_review(test_db, park, enforcer)
        late = _queue(
            test_db,
            video_id,
            vtype=VIOLATION_OBSTRUCTION,
            ts=12.0,
            ep_start=9.0,
            ep_end=21.0,
        )
        second = materialize_case_from_review(test_db, late, enforcer)
        assert second["violation_id"] == first["violation_id"]

        other = _queue(
            test_db,
            video_id,
            vtype=VIOLATION_ILLEGAL_PARKING,
            track_id=7,
            run_id=1,
            ts=80.0,
            ep_start=70.0,
            ep_end=90.0,
        )
        distinct = materialize_case_from_review(test_db, other, enforcer)
        assert distinct["violation_id"] != first["violation_id"]

    def test_dismissed_not_reused(self, test_db, enforcer, video_id):
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

    def test_mappings_remain_unverified(self):
        reset_policy_cache()
        assert legal_status_for(VIOLATION_ILLEGAL_PARKING).value == "unverified"
        assert legal_status_for(VIOLATION_OBSTRUCTION).value == "unverified"
        assert verified_official_category_for(VIOLATION_ILLEGAL_PARKING) is None
        assert proposed_official_category_for(VIOLATION_ILLEGAL_PARKING) == (
            "Obstruction of Traffic Flow"
        )


class TestGroupedSettings:
    def test_obstruction_group_controls_both_rules(self, monkeypatch):
        reset_policy_cache()
        store: dict[str, str] = {}
        monkeypatch.setattr(
            "core.violation_config.db.get_setting", lambda key: store.get(key)
        )
        monkeypatch.setattr(
            "core.violation_config.db.set_settings",
            lambda values: store.update(values),
        )
        save_enabled_violations([VIOLATION_ILLEGAL_PARKING, VIOLATION_OBSTRUCTION])
        groups = violation_groups_for_ui()
        obst = next(g for g in groups if g["group_id"] == "Obstruction of Traffic Flow")
        assert set(obst["canonical_rules"]) == {
            VIOLATION_ILLEGAL_PARKING,
            VIOLATION_OBSTRUCTION,
        }
        assert obst["enabled_state"] == "on"
        assert obst["pending_legal_verification"] is True

        signals = next(
            g for g in groups if g["group_id"] == "Disregarding Traffic Signals"
        )
        assert set(signals["canonical_rules"]) >= {
            VIOLATION_COUNTERFLOW,
            VIOLATION_DISREGARDING_SIGN,
            VIOLATION_PAVEMENT_MARKINGS,
        }

        accessories = next(
            g for g in groups if g["group_id"] == "Incomplete Accessories"
        )
        assert VIOLATION_NO_SIDE_MIRROR in accessories["canonical_rules"]
        assert accessories["legal_status"] == "flag_only"

        truck = next(g for g in groups if g["group_id"] == "Violation of Truck Ban")
        assert VIOLATION_TRUCK_BAN in truck["canonical_rules"]

    def test_group_toggle_expansion_preserves_canonicals(self):
        reset_policy_cache()
        expanded = expand_group_selection_to_canonical(
            group_states={"Obstruction of Traffic Flow": "on"},
            prior_enabled=[],
        )
        assert set(expanded) == {VIOLATION_ILLEGAL_PARKING, VIOLATION_OBSTRUCTION}

        off = expand_group_selection_to_canonical(
            group_states={"Obstruction of Traffic Flow": "off"},
            prior_enabled=[VIOLATION_ILLEGAL_PARKING, VIOLATION_OBSTRUCTION],
        )
        assert off == ()

    def test_legacy_mixed_state_preserved(self, monkeypatch):
        reset_policy_cache()
        store: dict[str, str] = {}
        monkeypatch.setattr(
            "core.violation_config.db.get_setting", lambda key: store.get(key)
        )
        monkeypatch.setattr(
            "core.violation_config.db.set_settings",
            lambda values: store.update(values),
        )
        save_enabled_violations([VIOLATION_ILLEGAL_PARKING])
        groups = violation_groups_for_ui()
        obst = next(g for g in groups if g["group_id"] == "Obstruction of Traffic Flow")
        assert obst["enabled_state"] == "mixed"
        assert obst["mixed"] is True

        preserved = expand_group_selection_to_canonical(
            group_states={"Obstruction of Traffic Flow": "mixed"},
            prior_enabled=[VIOLATION_ILLEGAL_PARKING],
        )
        assert preserved == (VIOLATION_ILLEGAL_PARKING,)

    def test_save_reload_preserves_selection(self, monkeypatch):
        reset_policy_cache()
        store: dict[str, str] = {}
        monkeypatch.setattr(
            "core.violation_config.db.get_setting", lambda key: store.get(key)
        )
        monkeypatch.setattr(
            "core.violation_config.db.set_settings",
            lambda values: store.update(values),
        )
        from core.violation_config import load_enabled_violations

        chosen = expand_group_selection_to_canonical(
            group_states={
                "Obstruction of Traffic Flow": "on",
                "Disregarding Traffic Signals": "off",
            },
            prior_enabled=[],
        )
        save_enabled_violations(list(chosen))
        assert set(load_enabled_violations()) == {
            VIOLATION_ILLEGAL_PARKING,
            VIOLATION_OBSTRUCTION,
        }
        reloaded = violation_groups_for_ui()
        obst = next(
            g for g in reloaded if g["group_id"] == "Obstruction of Traffic Flow"
        )
        assert obst["enabled_state"] == "on"

    def test_settings_html_uses_group_toggles(self):
        html = open("templates/settings.html", encoding="utf-8").read()
        assert "violation-group-toggle" in html
        assert "violation_groups" in html
        assert "Pending legal verification" in html
        js = open("static/js/settings.js", encoding="utf-8").read()
        assert "violation-group-toggle" in js
        switch_js = open("static/js/violation_switch.js", encoding="utf-8").read()
        assert "isMixedSwitch" in switch_js or 'enabledState === "mixed"' in switch_js
        assert '"mixed"' in switch_js


class TestAdminCaseReviewAuthority:
    def test_active_admin_full_case_review_path(self, test_db, admin, video_id):
        rid = _queue(test_db, video_id, vtype=VIOLATION_OBSTRUCTION)
        mat = materialize_case_from_review(test_db, rid, admin)
        vid = mat["violation_id"]
        plate = verify_plate_identity(
            test_db,
            vid,
            admin,
            plate_status=HUMAN_PLATE_STATUS_VERIFIED_READABLE,
            accepted_plate_text="ADM001",
        )
        assert plate["verified_by"] == admin
        evt = persist_event_time_review(
            test_db,
            vid,
            admin,
            user_entry_raw="2026-05-01T09:15:00+08:00",
        )
        assert evt["persisted"] is True
        assert test_db.confirm_case(vid, admin) is True
        assert test_db.confirm_notice_printed(vid, admin) is True
        assert test_db.is_notice_printed(vid) is True

    def test_inactive_admin_denied_no_writes(self, test_db, admin, video_id):
        test_db.update_user(admin, is_active=0)
        rid = _queue(test_db, video_id, vtype=VIOLATION_ILLEGAL_PARKING)
        before = test_db.get_review_item(rid)["status"]
        with pytest.raises(PermissionError):
            materialize_case_from_review(test_db, rid, admin)
        assert test_db.get_review_item(rid)["status"] == before
        assert test_db.can_confirm_case(admin) is False
        assert test_db.can_approve_policy(admin) is False

    def test_viewer_denied(self, test_db, viewer, video_id):
        rid = _queue(test_db, video_id, vtype=VIOLATION_ILLEGAL_PARKING)
        with pytest.raises(PermissionError):
            materialize_case_from_review(test_db, rid, viewer)

    def test_admin_cannot_approve_legal_policy(self, test_db, admin):
        assert test_db.can_approve_policy(admin) is False
        assert test_db.can_propose_policy(admin) is True
        pid = test_db.propose_legal_policy_version(
            version="admin-no-approve", created_by=admin
        )
        with pytest.raises(PermissionError):
            test_db.approve_legal_policy_version(pid, approved_by=admin)

    def test_preview_does_not_mark_printed_print_before_confirm_fails(
        self, test_db, admin, video_id
    ):
        rid = _queue(test_db, video_id, vtype=VIOLATION_OBSTRUCTION)
        vid = materialize_case_from_review(test_db, rid, admin)["violation_id"]
        assert test_db.is_notice_printed(vid) is False
        with pytest.raises(ValueError, match="Case must be confirmed"):
            test_db.confirm_notice_printed(vid, admin)
        assert test_db.is_notice_printed(vid) is False
        test_db.confirm_case(vid, admin)
        assert test_db.confirm_notice_printed(vid, admin) is True
        actions = [
            a
            for a in test_db.get_case_actions(vid)
            if a["action_type"] == "notice_printed"
        ]
        assert int(actions[0]["actor_user_id"]) == int(admin)
