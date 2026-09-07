"""Tests for plate + event-time review persistence service layer."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

import numpy as np
import pytest

from core.case_review_service import (
    CaseReviewError,
    persist_event_time_review,
    process_plate_for_evidence,
    verify_plate_identity,
)
from core.detection_config import VIOLATION_OBSTRUCTION
from core.plate_processing import (
    HUMAN_PLATE_STATUS_NOT_VISIBLE,
    HUMAN_PLATE_STATUS_UNCLEAR,
    HUMAN_PLATE_STATUS_VERIFIED_READABLE,
    PlateProcessingOutcome,
    PlateProcessor,
)


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
def inactive_enforcer(test_db):
    uid = test_db.create_user("oldenf", "hash", role="enforcer", full_name="Old")
    test_db.update_user(uid, is_active=0)
    return uid


@pytest.fixture
def violation_id(test_db, enforcer):
    vid = test_db.insert_video("v.mp4", "/tmp/v.mp4", status="ready")
    rid = test_db.insert_review_queue(
        video_id=vid,
        track_id=1,
        violation_type=VIOLATION_OBSTRUCTION,
        confidence=0.9,
        frame_number=10,
        timestamp_sec=1.0,
        evidence_path="/tmp/e.jpg",
    )
    return test_db.confirm_review_item(rid, enforcer)


class FakeAlpr:
    def predict(self, frame):
        class Det:
            label = "plate"
            confidence = 0.9
            bounding_box = type("B", (), {"x1": 1, "y1": 2, "x2": 3, "y2": 4})()

        class Ocr:
            text = "ABC123"
            confidence = 0.88

        class Res:
            detection = Det()
            ocr = Ocr()

        return [Res()]


class TestPlateService:
    def test_missing_backend_unavailable(self):
        out = process_plate_for_evidence(None, np.zeros((8, 8), dtype=np.uint8))
        assert out["outcome"] == PlateProcessingOutcome.PROCESSING_UNAVAILABLE.value
        assert out["candidates"] == []

    def test_injected_fake_backend(self):
        proc = PlateProcessor(FakeAlpr())
        out = process_plate_for_evidence(
            proc, np.zeros((16, 16, 3), dtype=np.uint8), evidence_ref="e1"
        )
        assert out["outcome"] == PlateProcessingOutcome.CANDIDATE_FOUND.value
        assert out["candidates"][0]["ocr_raw"] == "ABC123"

    def test_verify_plate_authorized(self, test_db, enforcer, violation_id):
        result = verify_plate_identity(
            test_db,
            violation_id,
            enforcer,
            plate_status=HUMAN_PLATE_STATUS_VERIFIED_READABLE,
            accepted_plate_text="ABC123",
            candidate_ocr_raw="ABC123",
            candidate_reference="cand-1",
            evidence_crop_ref="/tmp/plate.jpg",
        )
        assert result["accepted_plate_text"] == "ABC123"
        row = test_db.get_plate_verification(violation_id)
        assert row["plate_status"] == HUMAN_PLATE_STATUS_VERIFIED_READABLE
        assert row["verified_by"] == enforcer
        actions = test_db.get_case_actions(violation_id)
        assert any(a["action_type"] == "plate_verified" for a in actions)

    def test_admin_can_verify_by_role(self, test_db, admin, violation_id):
        result = verify_plate_identity(
            test_db,
            violation_id,
            admin,
            plate_status=HUMAN_PLATE_STATUS_VERIFIED_READABLE,
            accepted_plate_text="ABC123",
        )
        assert result["verified_by"] == admin
        assert test_db.get_plate_verification(violation_id)["accepted_plate_text"] == "ABC123"

    def test_inactive_enforcer_rejected(
        self, test_db, inactive_enforcer, violation_id
    ):
        with pytest.raises(PermissionError):
            verify_plate_identity(
                test_db,
                violation_id,
                inactive_enforcer,
                plate_status=HUMAN_PLATE_STATUS_VERIFIED_READABLE,
                accepted_plate_text="ABC123",
            )

    def test_unclear_and_absent_retain_record_without_identity(
        self, test_db, enforcer, violation_id
    ):
        verify_plate_identity(
            test_db,
            violation_id,
            enforcer,
            plate_status=HUMAN_PLATE_STATUS_UNCLEAR,
            accepted_plate_text="SHOULD_CLEAR",
        )
        row = test_db.get_plate_verification(violation_id)
        assert row["plate_status"] == HUMAN_PLATE_STATUS_UNCLEAR
        assert row["accepted_plate_text"] is None

        verify_plate_identity(
            test_db,
            violation_id,
            enforcer,
            plate_status=HUMAN_PLATE_STATUS_NOT_VISIBLE,
        )
        row = test_db.get_plate_verification(violation_id)
        assert row["plate_status"] == HUMAN_PLATE_STATUS_NOT_VISIBLE
        assert row["accepted_plate_text"] is None
        viol = test_db.get_violation(violation_id)
        assert viol["evidence_path"] == "/tmp/e.jpg"

    def test_partial_plate_rejected(self, test_db, enforcer, violation_id):
        with pytest.raises(ValueError):
            verify_plate_identity(
                test_db,
                violation_id,
                enforcer,
                plate_status=HUMAN_PLATE_STATUS_VERIFIED_READABLE,
                accepted_plate_text="AB*123",
            )

    def test_dismissed_case_blocked(self, test_db, enforcer, violation_id):
        test_db.update_violation_status(violation_id, "dismissed")
        with pytest.raises(ValueError):
            verify_plate_identity(
                test_db,
                violation_id,
                enforcer,
                plate_status=HUMAN_PLATE_STATUS_VERIFIED_READABLE,
                accepted_plate_text="ABC123",
            )


class TestEventTimeService:
    def test_user_entry_confirmation(self, test_db, enforcer, violation_id):
        result = persist_event_time_review(
            test_db,
            violation_id,
            enforcer,
            user_entry_raw="2026-01-15T10:30:00+08:00",
            video_relative_sec=12.5,
        )
        assert result["persisted"] is True
        assert "2026-01-15" in result["effective_confirmed"]
        assert test_db.get_confirmed_event_time(violation_id) is not None

    def test_correction_preserves_previous(self, test_db, enforcer, violation_id):
        first = persist_event_time_review(
            test_db,
            violation_id,
            enforcer,
            user_entry_raw="2026-01-15T10:30:00+08:00",
        )
        second = persist_event_time_review(
            test_db,
            violation_id,
            enforcer,
            user_entry_raw="2026-01-15T10:30:00+08:00",
            correction_raw="2026-01-15T11:00:00+08:00",
        )
        assert second["persisted"] is True
        assert second["detail"]["previous_confirmed"] is not None
        assert "11:00:00" in second["effective_confirmed"]
        assert first["effective_confirmed"] != second["effective_confirmed"]

    def test_reconfirm_without_correction_keeps_latest(
        self, test_db, enforcer, violation_id
    ):
        from core.event_time import from_user_entry, confirm_event_time

        prior = from_user_entry(raw_text="2026-01-15T10:30:00+08:00")
        confirmed = confirm_event_time(
            prior,
            confirmed_by="1",
            confirmed_at=datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc),
        )
        corrected = confirm_event_time(
            confirmed,
            confirmed_by="1",
            confirmed_at=datetime(2026, 1, 15, 12, 5, tzinfo=timezone.utc),
            corrected_instant=from_user_entry(
                raw_text="2026-01-15T11:00:00+08:00"
            ).candidate,
        )
        # Reconfirm without new correction retains corrected value.
        again = persist_event_time_review(
            test_db,
            violation_id,
            enforcer,
            prior_result=corrected,
        )
        assert "11:00:00" in again["effective_confirmed"]

    def test_inactive_user_rejected(self, test_db, inactive_enforcer, violation_id):
        with pytest.raises(PermissionError):
            persist_event_time_review(
                test_db,
                violation_id,
                inactive_enforcer,
                user_entry_raw="2026-01-15T10:30:00+08:00",
            )

    def test_admin_can_confirm_event_time_by_role(self, test_db, admin, violation_id):
        result = persist_event_time_review(
            test_db,
            violation_id,
            admin,
            user_entry_raw="2026-01-15T10:30:00+08:00",
        )
        assert result["persisted"] is True
        assert result["confirmed_by"] == admin

    def test_timestamp_ocr_marked_unavailable(self, test_db, enforcer, violation_id):
        result = persist_event_time_review(
            test_db,
            violation_id,
            enforcer,
            user_entry_raw="2026-01-15T10:30:00+08:00",
        )
        assert result["detail"]["timestamp_ocr_available"] is False

    def test_correction_only_after_persisted_review(
        self, test_db, enforcer, violation_id
    ):
        first = persist_event_time_review(
            test_db,
            violation_id,
            enforcer,
            user_entry_raw="2026-01-15T10:30:00+08:00",
            video_relative_sec=12.5,
        )
        assert first["persisted"] is True
        second = persist_event_time_review(
            test_db,
            violation_id,
            enforcer,
            correction_raw="2026-01-15T11:00:00+08:00",
        )
        assert second["persisted"] is True
        assert "11:00:00" in second["effective_confirmed"]
        assert second["detail"]["previous_confirmed"] is not None
        assert "10:30:00" in second["detail"]["previous_confirmed"]
        assert second["detail"]["original_confirmed_by"] == str(enforcer)
        assert second["detail"]["correction_by"] == str(enforcer)

    def test_correction_by_different_authorized_user(
        self, test_db, enforcer, violation_id
    ):
        other = test_db.create_user("enf2", "hash", role="enforcer")
        persist_event_time_review(
            test_db,
            violation_id,
            enforcer,
            user_entry_raw="2026-01-15T10:30:00+08:00",
        )
        second = persist_event_time_review(
            test_db,
            violation_id,
            other,
            correction_raw="2026-01-15T11:15:00+08:00",
        )
        assert second["detail"]["original_confirmed_by"] == str(enforcer)
        assert second["detail"]["correction_by"] == str(other)
        assert "11:15:00" in second["effective_confirmed"]

    def test_reconfirm_after_correction_keeps_corrected_value(
        self, test_db, enforcer, violation_id
    ):
        persist_event_time_review(
            test_db,
            violation_id,
            enforcer,
            user_entry_raw="2026-01-15T10:30:00+08:00",
        )
        persist_event_time_review(
            test_db,
            violation_id,
            enforcer,
            correction_raw="2026-01-15T11:00:00+08:00",
        )
        again = persist_event_time_review(
            test_db,
            violation_id,
            enforcer,
        )
        assert "11:00:00" in again["effective_confirmed"]

    def test_invalid_correction_preserves_prior(self, test_db, enforcer, violation_id):
        persist_event_time_review(
            test_db,
            violation_id,
            enforcer,
            user_entry_raw="2026-01-15T10:30:00+08:00",
        )
        with pytest.raises(CaseReviewError):
            persist_event_time_review(
                test_db,
                violation_id,
                enforcer,
                correction_raw="not-a-timestamp",
            )
        assert "10:30:00" in (test_db.get_confirmed_event_time(violation_id) or "")

    def test_correction_without_prior_treated_as_new_entry(
        self, test_db, enforcer, violation_id
    ):
        result = persist_event_time_review(
            test_db,
            violation_id,
            enforcer,
            correction_raw="2026-01-15T09:00:00+08:00",
        )
        assert result["persisted"] is True
        assert "09:00:00" in result["effective_confirmed"]
        assert result["detail"].get("previous_confirmed") in (None, )


class TestOriginalEventTimeCandidate:
    def test_original_candidate_survives_two_corrections(
        self, test_db, enforcer, violation_id
    ):
        first = persist_event_time_review(
            test_db,
            violation_id,
            enforcer,
            user_entry_raw="2026-01-15T10:00:00+08:00",
        )
        assert "10:00:00" in first["detail"]["original_candidate"]

        second = persist_event_time_review(
            test_db,
            violation_id,
            enforcer,
            correction_raw="2026-01-15T11:00:00+08:00",
        )
        assert "10:00:00" in second["detail"]["original_candidate"]
        assert "10:00:00" in (second["detail"]["previous_confirmed"] or "")
        assert "11:00:00" in second["effective_confirmed"]

        third = persist_event_time_review(
            test_db,
            violation_id,
            enforcer,
            correction_raw="2026-01-15T12:00:00+08:00",
        )
        assert "10:00:00" in third["detail"]["original_candidate"]
        assert "10:00:00" in third["detail"]["candidate"]
        assert "11:00:00" in (third["detail"]["previous_confirmed"] or "")
        assert "12:00:00" in third["effective_confirmed"]

        again = persist_event_time_review(test_db, violation_id, enforcer)
        assert "12:00:00" in again["effective_confirmed"]
        assert "10:00:00" in again["detail"]["original_candidate"]

    def test_different_reviewer_correction_metadata(
        self, test_db, enforcer, violation_id
    ):
        other = test_db.create_user("enf3", "hash", role="enforcer")
        persist_event_time_review(
            test_db,
            violation_id,
            enforcer,
            user_entry_raw="2026-01-15T10:00:00+08:00",
        )
        second = persist_event_time_review(
            test_db,
            violation_id,
            other,
            correction_raw="2026-01-15T11:00:00+08:00",
        )
        assert second["detail"]["original_confirmed_by"] == str(enforcer)
        assert second["detail"]["correction_by"] == str(other)
        assert "10:00:00" in second["detail"]["original_candidate"]

    def test_missing_original_candidate_not_fabricated(
        self, test_db, enforcer, violation_id
    ):
        test_db.record_event_time(
            violation_id,
            "2026-01-15T10:00:00+08:00",
            "user_confirmation",
            confirmed_by=enforcer,
            original_metadata='{"effective_confirmed":"2026-01-15T10:00:00+08:00"}',
        )
        corr = persist_event_time_review(
            test_db,
            violation_id,
            enforcer,
            correction_raw="2026-01-15T11:00:00+08:00",
        )
        assert corr["detail"].get("original_candidate") in (None,)
        assert "11:00:00" in corr["effective_confirmed"]


class TestPlateAtomicity:
    def test_audit_failure_rolls_back_plate_and_legacy(
        self, test_db, enforcer, violation_id, monkeypatch
    ):
        verify_plate_identity(
            test_db,
            violation_id,
            enforcer,
            plate_status=HUMAN_PLATE_STATUS_VERIFIED_READABLE,
            accepted_plate_text="OLD111",
        )
        real_apply = test_db.apply_plate_verification

        def flaky_apply(*args, **kwargs):
            kwargs = dict(kwargs)
            kwargs["_fail_after"] = "audit"
            return real_apply(*args, **kwargs)

        monkeypatch.setattr(test_db, "apply_plate_verification", flaky_apply)
        with pytest.raises(RuntimeError, match="audit boom"):
            verify_plate_identity(
                test_db,
                violation_id,
                enforcer,
                plate_status=HUMAN_PLATE_STATUS_VERIFIED_READABLE,
                accepted_plate_text="NEW222",
            )
        pv = test_db.get_plate_verification(violation_id)
        viol = test_db.get_violation(violation_id)
        assert pv["accepted_plate_text"] == "OLD111"
        assert viol["plate_text"] == "OLD111"
        assert viol["plate_status"] == "recognized"

    def test_legacy_failure_rolls_back_verification(
        self, test_db, enforcer, violation_id, monkeypatch
    ):
        verify_plate_identity(
            test_db,
            violation_id,
            enforcer,
            plate_status=HUMAN_PLATE_STATUS_VERIFIED_READABLE,
            accepted_plate_text="KEEPME",
        )
        real_apply = test_db.apply_plate_verification

        def flaky_apply(*args, **kwargs):
            kwargs = dict(kwargs)
            kwargs["_fail_after"] = "legacy"
            return real_apply(*args, **kwargs)

        monkeypatch.setattr(test_db, "apply_plate_verification", flaky_apply)
        with pytest.raises(RuntimeError, match="legacy boom"):
            verify_plate_identity(
                test_db,
                violation_id,
                enforcer,
                plate_status=HUMAN_PLATE_STATUS_VERIFIED_READABLE,
                accepted_plate_text="SHOULDNOT",
            )
        pv = test_db.get_plate_verification(violation_id)
        assert pv["accepted_plate_text"] == "KEEPME"

    def test_verification_failure_leaves_unchanged(
        self, test_db, enforcer, violation_id, monkeypatch
    ):
        real_apply = test_db.apply_plate_verification

        def flaky_apply(*args, **kwargs):
            kwargs = dict(kwargs)
            kwargs["_fail_after"] = "verification"
            return real_apply(*args, **kwargs)

        monkeypatch.setattr(test_db, "apply_plate_verification", flaky_apply)
        with pytest.raises(RuntimeError, match="verification boom"):
            verify_plate_identity(
                test_db,
                violation_id,
                enforcer,
                plate_status=HUMAN_PLATE_STATUS_VERIFIED_READABLE,
                accepted_plate_text="NOWRITE",
            )
        assert test_db.get_plate_verification(violation_id) is None
        viol = test_db.get_violation(violation_id)
        assert viol["plate_status"] == "not_attempted"

    def test_verified_to_unclear_clears_current_identity(
        self, test_db, enforcer, violation_id
    ):
        verify_plate_identity(
            test_db,
            violation_id,
            enforcer,
            plate_status=HUMAN_PLATE_STATUS_VERIFIED_READABLE,
            accepted_plate_text="ABC123",
        )
        result = verify_plate_identity(
            test_db,
            violation_id,
            enforcer,
            plate_status=HUMAN_PLATE_STATUS_UNCLEAR,
        )
        assert result["accepted_plate_text"] is None
        pv = test_db.get_plate_verification(violation_id)
        assert pv["plate_status"] == HUMAN_PLATE_STATUS_UNCLEAR
        assert pv["accepted_plate_text"] is None
        viol = test_db.get_violation(violation_id)
        assert viol["plate_text"] is None
        assert viol["plate_status"] == "unreadable"
        actions = test_db.get_case_actions(violation_id)
        plate_actions = [a for a in actions if a["action_type"] == "plate_verified"]
        assert len(plate_actions) >= 2

    def test_unauthorized_plate_leaves_tables_unchanged(
        self, test_db, violation_id
    ):
        viewer = test_db.create_user("plate_viewer", "hash", role="viewer")
        with pytest.raises(PermissionError):
            verify_plate_identity(
                test_db,
                violation_id,
                viewer,
                plate_status=HUMAN_PLATE_STATUS_VERIFIED_READABLE,
                accepted_plate_text="NOPE",
            )
        assert test_db.get_plate_verification(violation_id) is None
        viol = test_db.get_violation(violation_id)
        assert viol["plate_status"] == "not_attempted"
