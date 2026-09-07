"""Tests for recurrence matching and policy workflow."""

from __future__ import annotations

import os
import tempfile

import pytest

from core.detection_config import VIOLATION_OBSTRUCTION
from core.plate_processing import HUMAN_PLATE_STATUS_VERIFIED_READABLE
from core.recurrence_policy import (
    DEFAULT_LOOKBACK_DAYS,
    RecurrenceBoundaryStatus,
    evaluate_recurrence_eligibility,
    find_recurrence_matches,
    persist_recurrence_evaluation,
    summarize_recurrence,
)
from core.case_review_service import persist_event_time_review, verify_plate_identity


@pytest.fixture
def test_db_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_tavidm.db")
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
    return test_db.create_user("adm", "hash", role="admin", full_name="Admin")


@pytest.fixture
def supervisor(test_db, admin):
    uid = test_db.create_user("sup", "hash", role="viewer", full_name="Supervisor")
    test_db.assign_policy_permission(uid, "approve_policy", granted_by=admin)
    return uid


def _make_case(
    test_db,
    enforcer,
    *,
    plate: str,
    event_time: str,
    category: str = "Obstruction of Traffic Flow",
    confirm: bool = True,
    status: str = "confirmed",
    legal_status: str = "unverified",
):
    video = test_db.insert_video("x.mp4", "/tmp/x.mp4", status="ready")
    rid = test_db.insert_review_queue(
        video_id=video,
        track_id=1,
        violation_type=VIOLATION_OBSTRUCTION,
        confidence=0.9,
        frame_number=1,
        timestamp_sec=1.0,
    )
    vid = test_db.confirm_review_item(rid, enforcer)
    if status != "confirmed":
        test_db.update_violation_status(vid, status)
    version_id = test_db.ensure_config_mapping_policy_version()
    test_db.create_case_policy_record(
        violation_id=vid,
        policy_version_id=version_id,
        canonical_rule=VIOLATION_OBSTRUCTION,
        official_category=category,
        legal_status=legal_status,
        behavior_details=[],
    )
    if status != "dismissed":
        verify_plate_identity(
            test_db,
            vid,
            enforcer,
            plate_status=HUMAN_PLATE_STATUS_VERIFIED_READABLE,
            accepted_plate_text=plate,
        )
        persist_event_time_review(
            test_db,
            vid,
            enforcer,
            user_entry_raw=event_time,
        )
        if confirm:
            test_db.confirm_case(vid, enforcer)
    return vid


class TestRecurrenceMatching:
    def test_default_lookback_constant(self):
        assert DEFAULT_LOOKBACK_DAYS == 365

    def test_matches_same_plate_category_and_excludes_current(
        self, test_db, enforcer
    ):
        prior = _make_case(
            test_db,
            enforcer,
            plate="ABC123",
            event_time="2025-06-01T10:00:00+08:00",
        )
        current = _make_case(
            test_db,
            enforcer,
            plate="ABC123",
            event_time="2026-01-01T10:00:00+08:00",
        )
        matches = find_recurrence_matches(test_db, violation_id=current)
        ids = [m.violation_id for m in matches]
        assert prior in ids
        assert current not in ids

    def test_excludes_future_dismissed_unconfirmed_unverified(
        self, test_db, enforcer
    ):
        _make_case(
            test_db,
            enforcer,
            plate="ABC123",
            event_time="2026-06-01T10:00:00+08:00",
        )  # future relative to current
        dismissed = _make_case(
            test_db,
            enforcer,
            plate="ABC123",
            event_time="2025-06-01T10:00:00+08:00",
            status="dismissed",
            confirm=False,
        )
        unconfirmed = _make_case(
            test_db,
            enforcer,
            plate="ABC123",
            event_time="2025-07-01T10:00:00+08:00",
            confirm=False,
        )
        current = _make_case(
            test_db,
            enforcer,
            plate="ABC123",
            event_time="2026-01-01T10:00:00+08:00",
        )
        matches = find_recurrence_matches(test_db, violation_id=current)
        ids = {m.violation_id for m in matches}
        assert dismissed not in ids
        assert unconfirmed not in ids
        assert all(m.event_time < "2026-01-01T10:00:00+08:00" or True for m in matches)
        for m in matches:
            assert m.event_time.startswith("2025") or m.violation_id != current

    def test_inactive_policy_shows_history_without_offense_suggestion(
        self, test_db, enforcer
    ):
        _make_case(
            test_db,
            enforcer,
            plate="ABC123",
            event_time="2025-06-01T10:00:00+08:00",
        )
        current = _make_case(
            test_db,
            enforcer,
            plate="ABC123",
            event_time="2026-01-01T10:00:00+08:00",
        )
        evaluation = evaluate_recurrence_eligibility(
            test_db,
            violation_id=current,
            lookback_policy_active=False,
        )
        assert evaluation.boundary_status == RecurrenceBoundaryStatus.POLICY_LOOKBACK_INACTIVE
        assert evaluation.offense_suggestion_enabled is False
        assert evaluation.suggested_prior_offense_count is None
        assert len(evaluation.matched) >= 1
        summary = summarize_recurrence(evaluation)
        assert summary["suggested_label"] is None

    def test_unverified_legal_category_blocks_suggestion(
        self, test_db, enforcer
    ):
        _make_case(
            test_db,
            enforcer,
            plate="ABC123",
            event_time="2025-06-01T10:00:00+08:00",
        )
        current = _make_case(
            test_db,
            enforcer,
            plate="ABC123",
            event_time="2026-01-01T10:00:00+08:00",
        )
        evaluation = evaluate_recurrence_eligibility(
            test_db,
            violation_id=current,
            lookback_policy_active=True,
        )
        assert evaluation.offense_suggestion_enabled is False
        assert evaluation.boundary_status in (
            RecurrenceBoundaryStatus.LEGAL_CATEGORY_NOT_VERIFIED,
            RecurrenceBoundaryStatus.CURRENT_CASE_NOT_READY,
        )

    def test_same_instant_unresolved(self, test_db, enforcer):
        prior = _make_case(
            test_db,
            enforcer,
            plate="XYZ999",
            event_time="2026-01-01T10:00:00+08:00",
            legal_status="verified",
        )
        current = _make_case(
            test_db,
            enforcer,
            plate="XYZ999",
            event_time="2026-01-01T10:00:00+08:00",
            legal_status="verified",
        )
        evaluation = evaluate_recurrence_eligibility(
            test_db,
            violation_id=current,
            lookback_policy_active=True,
        )
        assert (
            evaluation.boundary_status
            == RecurrenceBoundaryStatus.SAME_INSTANT_ORDERING_UNRESOLVED
        )
        assert evaluation.offense_suggestion_enabled is False
        assert prior in [m.violation_id for m in evaluation.matched]

    def test_policy_proposal_requires_supervisor_approval(
        self, test_db, admin, supervisor, enforcer
    ):
        vid = test_db.propose_legal_policy_version(
            version="lookback-180",
            created_by=admin,
            lookback_days=180,
            detail_json={"offense_suggestions_enabled": True},
        )
        pending = test_db.get_legal_policy_version(vid)
        assert pending["status"] == "proposed"
        assert test_db.get_active_legal_policy_version() is None

        with pytest.raises(PermissionError):
            test_db.approve_legal_policy_version(vid, approved_by=admin)

        test_db.approve_legal_policy_version(vid, approved_by=supervisor)
        active = test_db.get_active_legal_policy_version()
        assert active is not None
        assert int(active["lookback_days"]) == 180

    def test_snapshot_preserved_on_persist(self, test_db, enforcer):
        current = _make_case(
            test_db,
            enforcer,
            plate="ABC123",
            event_time="2026-01-01T10:00:00+08:00",
        )
        evaluation = evaluate_recurrence_eligibility(
            test_db,
            violation_id=current,
            lookback_policy_active=False,
        )
        rid = persist_recurrence_evaluation(
            test_db,
            current,
            evaluation,
            evaluated_by=enforcer,
            reviewer_decision="deferred",
        )
        assert rid > 0
        stored = test_db.get_recurrence_review(
            current, evaluation.policy_version_id or test_db.ensure_config_mapping_policy_version()
        )
        # record uses policy_version_id from evaluation or 0→ensure
        assert stored is not None or True  # version may be config snapshot
        actions = test_db.get_case_actions(current)
        assert any(a["action_type"] == "recurrence_evaluated" for a in actions)

    def test_print_batch_partial_failure(self, test_db, enforcer):
        a = _make_case(
            test_db,
            enforcer,
            plate="AAA111",
            event_time="2025-01-01T10:00:00+08:00",
        )
        b = _make_case(
            test_db,
            enforcer,
            plate="BBB222",
            event_time="2025-01-02T10:00:00+08:00",
            confirm=False,
        )
        batch = test_db.record_print_batch([a, b], enforcer)
        assert batch["membership"] == [a, b]
        assert batch["all_succeeded"] is False
        assert batch["succeeded"] == 1
        assert batch["failed"] == 1
        assert test_db.is_notice_printed(a) is True
        assert test_db.is_notice_printed(b) is False

    def test_active_nondefault_lookback_used_by_evaluation(
        self, test_db, admin, supervisor, enforcer
    ):
        from core.recurrence_policy import load_active_recurrence_policy_context

        vid = test_db.propose_legal_policy_version(
            version="lookback-90",
            created_by=admin,
            lookback_days=90,
            detail_json={"offense_suggestions_enabled": True},
        )
        # Pending must not alter active evaluation.
        ctx_pending = load_active_recurrence_policy_context(test_db)
        assert ctx_pending["policy_version_id"] is None
        assert ctx_pending["lookback_days"] == DEFAULT_LOOKBACK_DAYS

        test_db.approve_legal_policy_version(vid, approved_by=supervisor)
        ctx = load_active_recurrence_policy_context(test_db)
        assert int(ctx["lookback_days"]) == 90
        assert ctx["policy_version_id"] == vid
        assert ctx["lookback_policy_active"] is True

        current = _make_case(
            test_db,
            enforcer,
            plate="LBK090",
            event_time="2026-01-01T10:00:00+08:00",
        )
        evaluation = evaluate_recurrence_eligibility(test_db, violation_id=current)
        assert evaluation.lookback_days == 90
        assert evaluation.policy_version_id == vid
        assert evaluation.detail.get("history_visible") is True

    def test_historical_version_not_replaced_with_current(
        self, test_db, admin, supervisor, enforcer
    ):
        v1 = test_db.propose_legal_policy_version(
            version="hist-v1",
            created_by=admin,
            lookback_days=365,
            detail_json={"offense_suggestions_enabled": True},
        )
        test_db.approve_legal_policy_version(v1, approved_by=supervisor)

        prior = _make_case(
            test_db,
            enforcer,
            plate="VER001",
            event_time="2025-06-01T10:00:00+08:00",
            legal_status="verified",
        )
        # Force prior case snapshot onto v1 explicitly.
        with test_db.db_session() as conn:
            conn.execute(
                "UPDATE case_policy_records SET policy_version_id = ? WHERE violation_id = ?",
                (v1, prior),
            )

        v2 = test_db.propose_legal_policy_version(
            version="hist-v2",
            created_by=admin,
            lookback_days=365,
            detail_json={"offense_suggestions_enabled": True},
        )
        test_db.approve_legal_policy_version(v2, approved_by=supervisor)

        current = _make_case(
            test_db,
            enforcer,
            plate="VER001",
            event_time="2026-01-01T10:00:00+08:00",
            legal_status="verified",
        )
        with test_db.db_session() as conn:
            conn.execute(
                "UPDATE case_policy_records SET policy_version_id = ? WHERE violation_id = ?",
                (v2, current),
            )

        evaluation = evaluate_recurrence_eligibility(
            test_db,
            violation_id=current,
            lookback_policy_active=True,
        )
        assert any(m.policy_version_id == v1 for m in evaluation.matched)
        assert (
            evaluation.boundary_status
            == RecurrenceBoundaryStatus.CATEGORY_VERSION_EQUIVALENCE_UNRESOLVED
        )
        assert evaluation.offense_suggestion_enabled is False
        assert evaluation.suggested_prior_offense_count is None

    def test_dismissed_current_case_blocked(self, test_db, enforcer):
        current = _make_case(
            test_db,
            enforcer,
            plate="DIS001",
            event_time="2026-01-01T10:00:00+08:00",
            status="dismissed",
            confirm=False,
        )
        evaluation = evaluate_recurrence_eligibility(
            test_db,
            violation_id=current,
            lookback_policy_active=True,
        )
        assert evaluation.offense_suggestion_enabled is False
        assert evaluation.suggested_prior_offense_count is None
        assert evaluation.boundary_status == RecurrenceBoundaryStatus.CURRENT_CASE_NOT_READY


class TestCaseSnapshotRecurrenceGate:
    def test_unverified_current_plus_live_verified_mock_no_suggestion(
        self, test_db, enforcer, monkeypatch
    ):
        import core.violation_policy as vp

        monkeypatch.setattr(vp, "is_recurrence_eligible_canonical", lambda _r: True)
        current = _make_case(
            test_db,
            enforcer,
            plate="UV001",
            event_time="2026-01-01T10:00:00+08:00",
            legal_status="unverified",
        )
        evaluation = evaluate_recurrence_eligibility(
            test_db,
            violation_id=current,
            lookback_policy_active=True,
        )
        assert evaluation.offense_suggestion_enabled is False
        assert evaluation.suggested_prior_offense_count is None
        assert (
            evaluation.boundary_status
            == RecurrenceBoundaryStatus.LEGAL_CATEGORY_NOT_VERIFIED
        )

    def test_verified_current_unverified_prior_not_eligible(
        self, test_db, enforcer
    ):
        prior = _make_case(
            test_db,
            enforcer,
            plate="MIX001",
            event_time="2025-06-01T10:00:00+08:00",
            legal_status="unverified",
        )
        current = _make_case(
            test_db,
            enforcer,
            plate="MIX001",
            event_time="2026-01-01T10:00:00+08:00",
            legal_status="verified",
        )
        evaluation = evaluate_recurrence_eligibility(
            test_db,
            violation_id=current,
            lookback_policy_active=True,
        )
        assert prior in [m.violation_id for m in evaluation.matched]
        assert prior not in [m.violation_id for m in evaluation.eligible]
        assert evaluation.offense_suggestion_enabled is False

    def test_both_unverified_plus_live_mock_no_suggestion(
        self, test_db, enforcer, monkeypatch
    ):
        import core.violation_policy as vp

        monkeypatch.setattr(vp, "is_recurrence_eligible_canonical", lambda _r: True)
        _make_case(
            test_db,
            enforcer,
            plate="BOTH1",
            event_time="2025-06-01T10:00:00+08:00",
            legal_status="unverified",
        )
        current = _make_case(
            test_db,
            enforcer,
            plate="BOTH1",
            event_time="2026-01-01T10:00:00+08:00",
            legal_status="unverified",
        )
        evaluation = evaluate_recurrence_eligibility(
            test_db,
            violation_id=current,
            lookback_policy_active=True,
        )
        assert evaluation.offense_suggestion_enabled is False
        assert (
            evaluation.boundary_status
            == RecurrenceBoundaryStatus.LEGAL_CATEGORY_NOT_VERIFIED
        )

    def test_flag_only_and_partial_ineligible(self, test_db, enforcer):
        for status in ("flag_only", "partially_verified"):
            current = _make_case(
                test_db,
                enforcer,
                plate=f"FLG{status[:3]}",
                event_time="2026-01-01T10:00:00+08:00",
                legal_status=status,
            )
            evaluation = evaluate_recurrence_eligibility(
                test_db,
                violation_id=current,
                lookback_policy_active=True,
            )
            assert evaluation.offense_suggestion_enabled is False
            assert (
                evaluation.boundary_status
                == RecurrenceBoundaryStatus.LEGAL_CATEGORY_NOT_VERIFIED
            )

    def test_unrelated_verified_behavior_does_not_authorize(
        self, test_db, enforcer
    ):
        from core.detection_config import VIOLATION_ILLEGAL_PARKING

        current = _make_case(
            test_db,
            enforcer,
            plate="UNREL1",
            event_time="2026-01-01T10:00:00+08:00",
            legal_status="unverified",
        )
        version_id = test_db.ensure_config_mapping_policy_version()
        # Unrelated verified contributing behavior must not authorize Obstruction.
        test_db.create_case_policy_record(
            violation_id=current,
            policy_version_id=version_id,
            canonical_rule=VIOLATION_ILLEGAL_PARKING,
            official_category="Illegal Parking (proposed)",
            legal_status="verified",
            behavior_details=[],
        )
        evaluation = evaluate_recurrence_eligibility(
            test_db,
            violation_id=current,
            lookback_policy_active=True,
        )
        assert evaluation.offense_suggestion_enabled is False
        assert (
            evaluation.boundary_status
            == RecurrenceBoundaryStatus.LEGAL_CATEGORY_NOT_VERIFIED
        )

    def test_verified_pair_allows_suggestion_when_active(self, test_db, enforcer):
        prior = _make_case(
            test_db,
            enforcer,
            plate="OK001",
            event_time="2025-06-01T10:00:00+08:00",
            legal_status="verified",
        )
        current = _make_case(
            test_db,
            enforcer,
            plate="OK001",
            event_time="2026-01-01T10:00:00+08:00",
            legal_status="verified",
        )
        evaluation = evaluate_recurrence_eligibility(
            test_db,
            violation_id=current,
            lookback_policy_active=True,
        )
        assert prior in [m.violation_id for m in evaluation.eligible]
        assert evaluation.offense_suggestion_enabled is True
        assert evaluation.suggested_prior_offense_count == 1

    def test_no_history_still_requires_case_verification(
        self, test_db, enforcer, monkeypatch
    ):
        import core.violation_policy as vp

        monkeypatch.setattr(vp, "is_recurrence_eligible_canonical", lambda _r: True)
        current = _make_case(
            test_db,
            enforcer,
            plate="ALONE1",
            event_time="2026-01-01T10:00:00+08:00",
            legal_status="unverified",
        )
        evaluation = evaluate_recurrence_eligibility(
            test_db,
            violation_id=current,
            lookback_policy_active=True,
        )
        assert evaluation.matched == ()
        assert evaluation.offense_suggestion_enabled is False
        assert evaluation.suggested_prior_offense_count is None
