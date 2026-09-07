"""Tests for Stage B: Legal-policy persistence layer (migration 008/009)."""

from __future__ import annotations

import os
import tempfile

import pytest


@pytest.fixture
def test_db_path() -> str:
    """Create a temporary SQLite database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_tavidm.db")
        # Guard: never resolve to the operational default path.
        assert "database/tavidm.db" not in db_path.replace("\\", "/")
        assert os.path.basename(db_path) == "test_tavidm.db"
        os.environ["SQLITE_PATH"] = db_path
        os.environ["DATABASE_URL"] = db_path
        yield db_path


@pytest.fixture
def test_db(test_db_path: str):
    """Initialize a test database with schema."""
    from database import sqlite_adapter

    sqlite_adapter.init_db(force=True)
    assert sqlite_adapter.get_db_path() == test_db_path
    return sqlite_adapter


@pytest.fixture
def sample_video_id(test_db) -> int:
    return test_db.insert_video(
        filename="policy_test.mp4",
        filepath="/tmp/policy_test.mp4",
        status="ready",
    )


@pytest.fixture
def admin_user(test_db):
    return test_db.create_user(
        "admin_test", "hash", role="admin", full_name="Test Admin"
    )


@pytest.fixture
def enforcer_user(test_db):
    return test_db.create_user(
        "enforcer_test", "hash", role="enforcer", full_name="Test Enforcer"
    )


@pytest.fixture
def viewer_user(test_db):
    return test_db.create_user(
        "viewer_test", "hash", role="viewer", full_name="Test Viewer"
    )


@pytest.fixture
def supervisor_user(test_db, admin_user):
    """CTEU supervisor via explicit approve_policy grant (not admin role)."""
    uid = test_db.create_user(
        "supervisor_test", "hash", role="viewer", full_name="CTEU Supervisor"
    )
    test_db.assign_policy_permission(
        uid, "approve_policy", granted_by=admin_user, reason="test fixture"
    )
    return uid


def _count_actions(test_db, violation_id: int | None, action_type: str) -> int:
    with test_db.db_session() as conn:
        if violation_id is None:
            rows = conn.execute(
                "SELECT COUNT(*) AS c FROM case_action_events WHERE action_type = ?",
                (action_type,),
            ).fetchone()
        else:
            rows = conn.execute(
                """
                SELECT COUNT(*) AS c FROM case_action_events
                WHERE violation_id = ? AND action_type = ?
                """,
                (violation_id, action_type),
            ).fetchone()
        return int(rows["c"])


# ---------------------------------------------------------------------------
# Migration 008/009: table creation and parity
# ---------------------------------------------------------------------------


class TestMigration008Tables:
    REQUIRED_TABLES = [
        "legal_policy_versions",
        "legal_behavior_mappings",
        "case_policy_records",
        "plate_verifications",
        "case_action_events",
        "recurrence_reviews",
        "policy_permission_assignments",
    ]

    def test_all_tables_created(self, test_db):
        tables = test_db.list_tables()
        for required in self.REQUIRED_TABLES:
            assert required in tables, f"Missing table: {required}"

    def test_migration_idempotent(self, test_db_path):
        from database import sqlite_adapter

        sqlite_adapter.init_db(force=True)
        sqlite_adapter.init_db(force=True)

    def test_fresh_schema_column_parity(self, test_db):
        cols = set(test_db.get_table_columns("legal_behavior_mappings"))
        assert "source_document_id" in cols
        conn = test_db.get_connection()
        try:
            assert test_db._case_policy_has_multi_behavior_unique(conn)
            assert test_db._case_actions_violation_id_nullable(conn)
            assert test_db._column_exists(
                conn, "legal_behavior_mappings", "source_document_id"
            )
        finally:
            test_db.close_connection(conn)

    def test_legal_policy_versions_schema(self, test_db):
        cols = test_db.get_table_columns("legal_policy_versions")
        expected = {
            "id", "version", "status", "lookback_days", "schedule_json",
            "detail_json", "created_by", "created_at", "approved_by",
            "approved_at", "rejected_by", "rejected_at",
        }
        assert expected.issubset(set(cols))

    def test_legal_behavior_mappings_schema(self, test_db):
        cols = test_db.get_table_columns("legal_behavior_mappings")
        expected = {
            "id", "policy_version_id", "canonical_rule", "official_category",
            "legal_status", "verified_elements_json", "unresolved_elements_json",
            "behavior_details_json", "provision_reference", "penalty_schedule_json",
            "source_url", "source_document_id", "mapping_version",
            "is_grouped_with_json", "notes",
        }
        assert expected.issubset(set(cols))

    def test_case_policy_records_schema(self, test_db):
        cols = test_db.get_table_columns("case_policy_records")
        expected = {
            "id", "violation_id", "review_id", "policy_version_id",
            "canonical_rule", "official_category", "legal_status",
            "behavior_details_json", "created_at",
        }
        assert expected.issubset(set(cols))

    def test_plate_verifications_schema(self, test_db):
        cols = test_db.get_table_columns("plate_verifications")
        expected = {
            "id", "violation_id", "review_id", "ocr_raw", "accepted_plate_text",
            "plate_status", "alpr_model", "alpr_version", "ocr_confidence",
            "processing_diagnostics_json", "verified_by", "verified_at", "created_at",
        }
        assert expected.issubset(set(cols))

    def test_case_action_events_schema(self, test_db):
        cols = test_db.get_table_columns("case_action_events")
        expected = {
            "id", "violation_id", "review_id", "action_type", "detail_json",
            "actor_user_id", "created_at",
        }
        assert expected.issubset(set(cols))

    def test_recurrence_reviews_schema(self, test_db):
        cols = test_db.get_table_columns("recurrence_reviews")
        expected = {
            "id", "violation_id", "policy_version_id", "lookback_days",
            "matched_violation_ids_json", "eligible_match_ids_json",
            "suggested_recurrence_count", "evaluation_time", "evaluated_by",
            "detail_json",
        }
        assert expected.issubset(set(cols))

    def test_policy_permission_assignments_schema(self, test_db):
        cols = test_db.get_table_columns("policy_permission_assignments")
        expected = {
            "id", "user_id", "permission", "granted_by", "granted_at",
            "revoked_at", "reason",
        }
        assert expected.issubset(set(cols))


class TestMigration009Upgrades:
    """Upgrade temporary fixtures that mimic conflicting 008 variants."""

    def test_upgrade_from_unique_violation_only_variant(self, test_db_path):
        from database import sqlite_adapter

        # Start from a complete fresh install, then reshape tables to the
        # conflicting 008 draft so migration 009 can be exercised safely.
        sqlite_adapter.init_db(force=True)
        admin_id = sqlite_adapter.create_user("a", "h", role="admin")
        video_id = sqlite_adapter.insert_video(
            filename="u.mp4", filepath="/tmp/u.mp4", status="ready"
        )
        viol_id = sqlite_adapter.insert_violation(
            video_id=video_id,
            track_id=1,
            violation_type="Illegal Parking",
            confidence=0.9,
            frame_number=1,
            timestamp_sec=1.0,
        )

        with sqlite_adapter.db_session() as conn:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.executescript(
                """
                DROP TABLE IF EXISTS case_policy_records;
                DROP TABLE IF EXISTS case_action_events;
                CREATE TABLE case_policy_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    violation_id INTEGER NOT NULL,
                    review_id INTEGER,
                    policy_version_id INTEGER NOT NULL,
                    canonical_rule TEXT NOT NULL,
                    official_category TEXT,
                    legal_status TEXT,
                    behavior_details_json TEXT NOT NULL DEFAULT '[]',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(violation_id)
                );
                CREATE TABLE case_action_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    violation_id INTEGER NOT NULL,
                    review_id INTEGER,
                    action_type TEXT NOT NULL,
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    actor_user_id INTEGER,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            # Remove source_document_id by rebuilding legal_behavior_mappings.
            conn.executescript(
                """
                CREATE TABLE legal_behavior_mappings_old (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    policy_version_id INTEGER NOT NULL,
                    canonical_rule TEXT NOT NULL,
                    official_category TEXT,
                    legal_status TEXT DEFAULT 'unverified',
                    verified_elements_json TEXT NOT NULL DEFAULT '[]',
                    unresolved_elements_json TEXT NOT NULL DEFAULT '[]',
                    behavior_details_json TEXT NOT NULL DEFAULT '[]',
                    provision_reference TEXT,
                    penalty_schedule_json TEXT,
                    source_url TEXT,
                    mapping_version TEXT,
                    is_grouped_with_json TEXT NOT NULL DEFAULT '[]',
                    notes TEXT,
                    UNIQUE(policy_version_id, canonical_rule)
                );
                INSERT INTO legal_behavior_mappings_old (
                    id, policy_version_id, canonical_rule, official_category,
                    legal_status, verified_elements_json, unresolved_elements_json,
                    behavior_details_json, provision_reference, penalty_schedule_json,
                    source_url, mapping_version, is_grouped_with_json, notes
                )
                SELECT id, policy_version_id, canonical_rule, official_category,
                       legal_status, verified_elements_json, unresolved_elements_json,
                       behavior_details_json, provision_reference, penalty_schedule_json,
                       source_url, mapping_version, is_grouped_with_json, notes
                FROM legal_behavior_mappings;
                DROP TABLE legal_behavior_mappings;
                ALTER TABLE legal_behavior_mappings_old RENAME TO legal_behavior_mappings;
                """
            )
            cur = conn.execute(
                "INSERT INTO legal_policy_versions (version, status, created_by) "
                "VALUES ('legacy-1', 'proposed', ?)",
                (admin_id,),
            )
            policy_id = int(cur.lastrowid)
            conn.execute(
                """
                INSERT INTO case_policy_records
                    (violation_id, policy_version_id, canonical_rule, legal_status)
                VALUES (?, ?, 'Illegal Parking', 'unverified')
                """,
                (viol_id, policy_id),
            )
            conn.execute(
                """
                INSERT INTO case_action_events
                    (violation_id, action_type, detail_json, actor_user_id)
                VALUES (0, 'policy_proposed', '{}', ?)
                """,
                (admin_id,),
            )
            conn.execute("PRAGMA foreign_keys = ON")

        with sqlite_adapter.db_session() as conn:
            sqlite_adapter._apply_migration_009(conn)

        conn = sqlite_adapter.get_connection()
        try:
            assert sqlite_adapter._column_exists(
                conn, "legal_behavior_mappings", "source_document_id"
            )
            assert sqlite_adapter._case_policy_has_multi_behavior_unique(conn)
            assert sqlite_adapter._case_actions_violation_id_nullable(conn)
            preserved = conn.execute(
                "SELECT canonical_rule FROM case_policy_records WHERE violation_id = ?",
                (viol_id,),
            ).fetchone()
            assert preserved["canonical_rule"] == "Illegal Parking"
            policy_evt = conn.execute(
                "SELECT violation_id FROM case_action_events "
                "WHERE action_type = 'policy_proposed'"
            ).fetchone()
            assert policy_evt["violation_id"] is None
        finally:
            sqlite_adapter.close_connection(conn)

    def test_upgrade_preserves_rows_on_reapply(
        self, test_db, admin_user, sample_video_id, supervisor_user
    ):
        vid = test_db.propose_legal_policy_version(
            version="preserve-1", created_by=admin_user
        )
        test_db.approve_legal_policy_version(vid, approved_by=supervisor_user)
        violation_id = test_db.insert_violation(
            video_id=sample_video_id,
            track_id=1,
            violation_type="Illegal Parking",
            confidence=0.95,
            frame_number=1,
            timestamp_sec=1.0,
        )
        test_db.create_case_policy_record(
            violation_id, vid, "Illegal Parking", legal_status="unverified"
        )
        test_db.create_case_policy_record(
            violation_id, vid, "Obstruction", legal_status="unverified"
        )
        before = _count_actions(test_db, None, "policy_proposed")
        with test_db.db_session() as conn:
            test_db._apply_migration_008(conn)
            test_db._apply_migration_009(conn)
        after = _count_actions(test_db, None, "policy_proposed")
        assert after == before
        with test_db.db_session() as conn:
            rows = conn.execute(
                "SELECT canonical_rule FROM case_policy_records WHERE violation_id = ? "
                "ORDER BY canonical_rule",
                (violation_id,),
            ).fetchall()
        assert [r["canonical_rule"] for r in rows] == ["Illegal Parking", "Obstruction"]


# ---------------------------------------------------------------------------
# Legal policy version lifecycle
# ---------------------------------------------------------------------------


class TestLegalPolicyVersionLifecycle:
    def test_propose_creates_version_with_proposed_status(self, test_db, admin_user):
        vid = test_db.propose_legal_policy_version(
            version="1.0.0",
            created_by=admin_user,
            lookback_days=365,
            schedule_json={"fine": 1000},
            detail_json={"source": "test"},
        )
        assert vid > 0
        version = test_db.get_legal_policy_version(vid)
        assert version is not None
        assert version["status"] == "proposed"
        assert version["version"] == "1.0.0"
        assert version["lookback_days"] == 365

    def test_admin_alone_cannot_approve(self, test_db, admin_user):
        vid = test_db.propose_legal_policy_version(
            version="1.0.0", created_by=admin_user
        )
        audit_before = _count_actions(test_db, None, "policy_approved")
        with pytest.raises(PermissionError):
            test_db.approve_legal_policy_version(vid, approved_by=admin_user)
        version = test_db.get_legal_policy_version(vid)
        assert version["status"] == "proposed"
        assert version["approved_by"] is None
        assert test_db.get_active_legal_policy_version() is None
        assert _count_actions(test_db, None, "policy_approved") == audit_before

    def test_explicit_supervisor_can_approve(
        self, test_db, admin_user, supervisor_user
    ):
        vid = test_db.propose_legal_policy_version(
            version="1.0.0", created_by=admin_user
        )
        test_db.approve_legal_policy_version(vid, approved_by=supervisor_user)
        version = test_db.get_legal_policy_version(vid)
        assert version["status"] == "approved"
        assert version["approved_by"] == supervisor_user
        assert version["created_by"] == admin_user
        assert version["approved_at"] is not None
        active = test_db.get_active_legal_policy_version()
        assert active is not None
        assert active["id"] == vid

    def test_enforcer_cannot_approve_without_grant(
        self, test_db, admin_user, enforcer_user
    ):
        vid = test_db.propose_legal_policy_version(
            version="1.0.0", created_by=admin_user
        )
        with pytest.raises(PermissionError):
            test_db.approve_legal_policy_version(vid, approved_by=enforcer_user)
        assert test_db.get_legal_policy_version(vid)["status"] == "proposed"

    def test_reject_transitions_status(self, test_db, admin_user):
        vid = test_db.propose_legal_policy_version(
            version="1.0.0", created_by=admin_user
        )
        test_db.reject_legal_policy_version(vid, rejected_by=admin_user, reason="bad")
        version = test_db.get_legal_policy_version(vid)
        assert version["status"] == "rejected"
        assert version["rejected_by"] == admin_user
        assert version["rejected_at"] is not None

    def test_unique_version_constraint(self, test_db, admin_user):
        test_db.propose_legal_policy_version(version="1.0.0", created_by=admin_user)
        with pytest.raises(Exception):
            test_db.propose_legal_policy_version(
                version="1.0.0", created_by=admin_user
            )


class TestMappingPersistence:
    def test_record_policy_mapping_with_source_document(
        self, test_db, admin_user, supervisor_user
    ):
        vid = test_db.propose_legal_policy_version(
            version="map-1", created_by=admin_user
        )
        test_db.approve_legal_policy_version(vid, approved_by=supervisor_user)
        test_db.record_policy_mapping(
            vid,
            [
                {
                    "canonical_rule": "Illegal Parking",
                    "official_category": "Obstruction of Traffic Flow",
                    "legal_status": "unverified",
                    "source_document_id": "co944-poster",
                    "behavior_details": ["dwell in no-parking"],
                    "is_grouped_with": ["Obstruction"],
                },
                {
                    "canonical_rule": "Obstruction",
                    "official_category": "Obstruction of Traffic Flow",
                    "legal_status": "unverified",
                    "source_document_id": "co944-poster",
                    "behavior_details": ["dwell in active lane"],
                    "is_grouped_with": ["Illegal Parking"],
                },
            ],
        )
        with test_db.db_session() as conn:
            rows = conn.execute(
                "SELECT canonical_rule, source_document_id FROM legal_behavior_mappings "
                "WHERE policy_version_id = ? ORDER BY canonical_rule",
                (vid,),
            ).fetchall()
        assert len(rows) == 2
        assert rows[0]["canonical_rule"] == "Illegal Parking"
        assert rows[0]["source_document_id"] == "co944-poster"
        assert rows[1]["canonical_rule"] == "Obstruction"

    def test_multiple_case_behavior_records(
        self, test_db, admin_user, supervisor_user, sample_video_id
    ):
        policy_id = test_db.propose_legal_policy_version(
            version="case-behaviors", created_by=admin_user
        )
        test_db.approve_legal_policy_version(policy_id, approved_by=supervisor_user)
        violation_id = test_db.insert_violation(
            video_id=sample_video_id,
            track_id=1,
            violation_type="Illegal Parking",
            confidence=0.9,
            frame_number=1,
            timestamp_sec=1.0,
        )
        test_db.create_case_policy_record(
            violation_id, policy_id, "Illegal Parking", legal_status="unverified"
        )
        test_db.create_case_policy_record(
            violation_id, policy_id, "Obstruction", legal_status="unverified"
        )
        # Duplicate of same pair ignored
        test_db.create_case_policy_record(
            violation_id, policy_id, "Illegal Parking", legal_status="unverified"
        )
        with test_db.db_session() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS c FROM case_policy_records WHERE violation_id = ?",
                (violation_id,),
            ).fetchone()["c"]
        assert count == 2


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


class TestPermissions:
    def test_assign_and_check_permission(self, test_db, admin_user, enforcer_user):
        test_db.assign_policy_permission(
            enforcer_user, "confirm_case", granted_by=admin_user
        )
        assert test_db.user_has_permission(enforcer_user, "confirm_case") is True

    def test_revoke_permission(self, test_db, admin_user, enforcer_user):
        test_db.assign_policy_permission(
            enforcer_user, "confirm_case", granted_by=admin_user
        )
        test_db.revoke_policy_permission(enforcer_user, "confirm_case")
        assert test_db.user_has_permission(enforcer_user, "confirm_case") is False

    def test_get_user_permissions(self, test_db, admin_user, enforcer_user):
        test_db.assign_policy_permission(
            enforcer_user, "confirm_case", granted_by=admin_user
        )
        test_db.assign_policy_permission(
            enforcer_user, "verify_plate", granted_by=admin_user
        )
        perms = test_db.get_user_permissions(enforcer_user)
        assert "confirm_case" in perms
        assert "verify_plate" in perms

    def test_role_based_access_enforcer(self, test_db, enforcer_user):
        assert test_db.can_confirm_case(enforcer_user) is True
        assert test_db.can_attest_print(enforcer_user) is True

    def test_admin_can_attest_but_not_approve_by_role(self, test_db, admin_user):
        assert test_db.can_approve_policy(admin_user) is False
        assert test_db.can_attest_print(admin_user) is True
        assert test_db.can_confirm_case(admin_user) is True
        assert test_db.can_propose_policy(admin_user) is True
        assert test_db.can_verify_plate(admin_user) is True
        assert test_db.can_confirm_event_time(admin_user) is True

    def test_role_based_access_viewer(self, test_db, viewer_user):
        assert test_db.can_confirm_case(viewer_user) is False
        assert test_db.can_attest_print(viewer_user) is False

    def test_nonexistent_user_has_no_permissions(self, test_db):
        assert test_db.can_confirm_case(9999) is False
        assert test_db.user_has_permission(9999, "confirm_case") is False


# ---------------------------------------------------------------------------
# Case confirmation and notice printing
# ---------------------------------------------------------------------------


class TestCaseConfirmation:
    @pytest.fixture
    def violation_id(self, test_db, sample_video_id):
        return test_db.insert_violation(
            video_id=sample_video_id,
            track_id=1,
            violation_type="Illegal Parking",
            confidence=0.95,
            frame_number=100,
            timestamp_sec=10.0,
        )

    def test_confirm_case_records_event(self, test_db, enforcer_user, violation_id):
        result = test_db.confirm_case(violation_id, enforcer_user)
        assert result is True
        actions = test_db.get_case_actions(violation_id)
        assert any(a["action_type"] == "case_confirmed" for a in actions)

    def test_confirm_case_requires_permission(self, test_db, viewer_user, violation_id):
        with pytest.raises(PermissionError):
            test_db.confirm_case(violation_id, viewer_user)

    def test_legacy_status_confirmed_not_enough_for_print(
        self, test_db, enforcer_user, violation_id
    ):
        with test_db.db_session() as conn:
            conn.execute(
                "UPDATE violations SET status = 'confirmed' WHERE id = ?",
                (violation_id,),
            )
        with pytest.raises(ValueError, match="Case must be confirmed"):
            test_db.confirm_notice_printed(violation_id, enforcer_user)
        with pytest.raises(ValueError, match="Case must be confirmed"):
            test_db.confirm_notice_printer_attested(violation_id, enforcer_user)

    def test_confirm_notice_requires_case_confirmed_first(
        self, test_db, enforcer_user, violation_id
    ):
        with pytest.raises(ValueError, match="Case must be confirmed"):
            test_db.confirm_notice_printed(violation_id, enforcer_user)

    def test_printer_attested_requires_case_confirmed(
        self, test_db, enforcer_user, violation_id
    ):
        with pytest.raises(ValueError, match="Case must be confirmed"):
            test_db.confirm_notice_printer_attested(violation_id, enforcer_user)

    def test_admin_can_attest_print(self, test_db, admin_user, enforcer_user, violation_id):
        test_db.confirm_case(violation_id, enforcer_user)
        assert test_db.confirm_notice_printed(violation_id, admin_user) is True
        assert test_db.is_notice_printed(violation_id) is True
        actions = test_db.get_case_actions(violation_id)
        printed = [a for a in actions if a["action_type"] == "notice_printed"]
        assert printed
        assert int(printed[0]["actor_user_id"]) == int(admin_user)

    def test_viewer_cannot_attest_print(
        self, test_db, viewer_user, enforcer_user, violation_id
    ):
        test_db.confirm_case(violation_id, enforcer_user)
        with pytest.raises(PermissionError):
            test_db.confirm_notice_printed(violation_id, viewer_user)

    def test_different_case_and_print_confirmers(
        self, test_db, admin_user, enforcer_user, violation_id
    ):
        printer = test_db.create_user(
            "printer2", "hash", role="enforcer", full_name="Printer Two"
        )
        test_db.confirm_case(violation_id, enforcer_user)
        test_db.confirm_notice_printed(violation_id, printer)
        actions = test_db.get_case_actions(violation_id)
        case_evt = next(a for a in actions if a["action_type"] == "case_confirmed")
        print_evt = next(a for a in actions if a["action_type"] == "notice_printed")
        assert case_evt["actor_user_id"] == enforcer_user
        assert print_evt["actor_user_id"] == printer
        assert case_evt["created_at"]
        assert print_evt["created_at"]

    def test_print_retry_is_idempotent(self, test_db, enforcer_user, violation_id):
        test_db.confirm_case(violation_id, enforcer_user)
        assert test_db.confirm_notice_printed(violation_id, enforcer_user) is True
        assert test_db.confirm_notice_printed(violation_id, enforcer_user) is True
        assert _count_actions(test_db, violation_id, "notice_printed") == 1

    def test_failed_print_leaves_no_partial_audit(
        self, test_db, enforcer_user, violation_id
    ):
        before = _count_actions(test_db, violation_id, "notice_printed")
        with pytest.raises(ValueError):
            test_db.confirm_notice_printed(violation_id, enforcer_user)
        assert _count_actions(test_db, violation_id, "notice_printed") == before
        assert test_db.is_notice_printed(violation_id) is False

    def test_confirm_notice_records_event(
        self, test_db, enforcer_user, violation_id
    ):
        test_db.confirm_case(violation_id, enforcer_user)
        result = test_db.confirm_notice_printed(violation_id, enforcer_user)
        assert result is True
        actions = test_db.get_case_actions(violation_id)
        assert any(a["action_type"] == "notice_printed" for a in actions)

    def test_is_case_confirmed(self, test_db, enforcer_user, violation_id):
        assert test_db.is_case_confirmed(violation_id) is False
        test_db.confirm_case(violation_id, enforcer_user)
        assert test_db.is_case_confirmed(violation_id) is True

    def test_is_notice_printed(self, test_db, enforcer_user, violation_id):
        assert test_db.is_notice_printed(violation_id) is False
        test_db.confirm_case(violation_id, enforcer_user)
        test_db.confirm_notice_printed(violation_id, enforcer_user)
        assert test_db.is_notice_printed(violation_id) is True


# ---------------------------------------------------------------------------
# Inactive-user authority rejection
# ---------------------------------------------------------------------------


class TestInactiveUserAuthority:
    """Inactive accounts must lose enforcement/policy authority immediately."""

    @pytest.fixture
    def violation_id(self, test_db, sample_video_id):
        return test_db.insert_violation(
            video_id=sample_video_id,
            track_id=1,
            violation_type="Illegal Parking",
            confidence=0.95,
            frame_number=100,
            timestamp_sec=10.0,
        )

    def test_inactive_enforcer_cannot_confirm_or_print(
        self, test_db, enforcer_user, violation_id
    ):
        test_db.update_user(enforcer_user, is_active=False)
        assert test_db.can_confirm_case(enforcer_user) is False
        assert test_db.can_attest_print(enforcer_user) is False

        before_confirm = _count_actions(test_db, violation_id, "case_confirmed")
        before_print = _count_actions(test_db, violation_id, "notice_printed")
        before_attested = _count_actions(
            test_db, violation_id, "notice_printer_attested"
        )

        with pytest.raises(PermissionError):
            test_db.confirm_case(violation_id, enforcer_user)
        with pytest.raises(PermissionError):
            test_db.confirm_notice_printed(violation_id, enforcer_user)
        with pytest.raises(PermissionError):
            test_db.confirm_notice_printer_attested(violation_id, enforcer_user)

        assert _count_actions(test_db, violation_id, "case_confirmed") == before_confirm
        assert _count_actions(test_db, violation_id, "notice_printed") == before_print
        assert (
            _count_actions(test_db, violation_id, "notice_printer_attested")
            == before_attested
        )
        with test_db.db_session() as conn:
            status = conn.execute(
                "SELECT status FROM violations WHERE id = ?", (violation_id,)
            ).fetchone()["status"]
        assert status != "dismissed"

    def test_inactive_admin_cannot_propose_policy(self, test_db, admin_user):
        test_db.update_user(admin_user, is_active=False)
        assert test_db.can_propose_policy(admin_user) is False
        before_versions = 0
        with test_db.db_session() as conn:
            before_versions = conn.execute(
                "SELECT COUNT(*) AS c FROM legal_policy_versions"
            ).fetchone()["c"]
            before_audit = conn.execute(
                "SELECT COUNT(*) AS c FROM case_action_events WHERE action_type = ?",
                ("policy_proposed",),
            ).fetchone()["c"]

        with pytest.raises(PermissionError):
            test_db.propose_legal_policy_version(
                version="inactive-admin-block", created_by=admin_user
            )

        with test_db.db_session() as conn:
            after_versions = conn.execute(
                "SELECT COUNT(*) AS c FROM legal_policy_versions"
            ).fetchone()["c"]
            after_audit = conn.execute(
                "SELECT COUNT(*) AS c FROM case_action_events WHERE action_type = ?",
                ("policy_proposed",),
            ).fetchone()["c"]
        assert after_versions == before_versions
        assert after_audit == before_audit

    def test_inactive_explicit_supervisor_cannot_approve(
        self, test_db, admin_user, supervisor_user
    ):
        policy_id = test_db.propose_legal_policy_version(
            version="inactive-supervisor-block", created_by=admin_user
        )
        test_db.update_user(supervisor_user, is_active=False)
        assert test_db.can_approve_policy(supervisor_user) is False

        with test_db.db_session() as conn:
            before_status = conn.execute(
                "SELECT status FROM legal_policy_versions WHERE id = ?",
                (policy_id,),
            ).fetchone()["status"]
            before_audit = conn.execute(
                "SELECT COUNT(*) AS c FROM case_action_events WHERE action_type = ?",
                ("policy_approved",),
            ).fetchone()["c"]
        assert before_status == "proposed"

        with pytest.raises(PermissionError):
            test_db.approve_legal_policy_version(policy_id, approved_by=supervisor_user)

        with test_db.db_session() as conn:
            after_status = conn.execute(
                "SELECT status FROM legal_policy_versions WHERE id = ?",
                (policy_id,),
            ).fetchone()["status"]
            after_audit = conn.execute(
                "SELECT COUNT(*) AS c FROM case_action_events WHERE action_type = ?",
                ("policy_approved",),
            ).fetchone()["c"]
        assert after_status == "proposed"
        assert after_audit == before_audit

    def test_explicit_grant_does_not_override_inactivity(
        self, test_db, admin_user, viewer_user, violation_id
    ):
        test_db.assign_policy_permission(
            viewer_user, "confirm_case", granted_by=admin_user
        )
        test_db.assign_policy_permission(
            viewer_user, "attest_print", granted_by=admin_user
        )
        test_db.assign_policy_permission(
            viewer_user, "approve_policy", granted_by=admin_user
        )
        test_db.assign_policy_permission(
            viewer_user, "propose_policy", granted_by=admin_user
        )
        # Active + grants works for capability helpers.
        assert test_db.can_confirm_case(viewer_user) is True
        assert test_db.can_attest_print(viewer_user) is True
        assert test_db.can_approve_policy(viewer_user) is True
        assert test_db.can_propose_policy(viewer_user) is True

        test_db.update_user(viewer_user, is_active=False)
        # Stored grants remain, but authority is denied.
        assert "confirm_case" in test_db.get_user_permissions(viewer_user)
        assert "attest_print" in test_db.get_user_permissions(viewer_user)
        assert "approve_policy" in test_db.get_user_permissions(viewer_user)
        assert "propose_policy" in test_db.get_user_permissions(viewer_user)
        assert test_db.can_confirm_case(viewer_user) is False
        assert test_db.can_attest_print(viewer_user) is False
        assert test_db.can_approve_policy(viewer_user) is False
        assert test_db.can_propose_policy(viewer_user) is False

        before = _count_actions(test_db, violation_id, "case_confirmed")
        with pytest.raises(PermissionError):
            test_db.confirm_case(violation_id, viewer_user)
        assert _count_actions(test_db, violation_id, "case_confirmed") == before

    def test_active_authorized_users_retain_permissions(
        self, test_db, admin_user, enforcer_user, supervisor_user, violation_id
    ):
        assert test_db.can_confirm_case(enforcer_user) is True
        assert test_db.can_attest_print(enforcer_user) is True
        assert test_db.can_propose_policy(admin_user) is True
        assert test_db.can_approve_policy(supervisor_user) is True
        # Active admin may perform case-review actions; still cannot approve policy.
        assert test_db.can_confirm_case(admin_user) is True
        assert test_db.can_attest_print(admin_user) is True
        assert test_db.can_approve_policy(admin_user) is False

        assert test_db.confirm_case(violation_id, enforcer_user) is True
        printer = test_db.create_user(
            "active_printer", "hash", role="enforcer", full_name="Active Printer"
        )
        assert test_db.confirm_notice_printed(violation_id, printer) is True
        assert test_db.confirm_notice_printer_attested(violation_id, printer) is True

        policy_id = test_db.propose_legal_policy_version(
            version="active-policy-ok", created_by=admin_user
        )
        test_db.approve_legal_policy_version(policy_id, approved_by=supervisor_user)
        with test_db.db_session() as conn:
            status = conn.execute(
                "SELECT status FROM legal_policy_versions WHERE id = ?",
                (policy_id,),
            ).fetchone()["status"]
        assert status == "approved"


# ---------------------------------------------------------------------------
# Dismissed-case confirmation / printing block
# ---------------------------------------------------------------------------


class TestDismissedCaseBlockedActions:
    """Dismissed cases cannot gain new confirm/print events."""

    @pytest.fixture
    def violation_id(self, test_db, sample_video_id):
        return test_db.insert_violation(
            video_id=sample_video_id,
            track_id=1,
            violation_type="Illegal Parking",
            confidence=0.95,
            frame_number=100,
            timestamp_sec=10.0,
        )

    def test_dismissed_case_without_prior_confirm_cannot_confirm_or_print(
        self, test_db, enforcer_user, violation_id
    ):
        test_db.update_violation_status(violation_id, "dismissed", reviewed_by=enforcer_user)
        before_confirm = _count_actions(test_db, violation_id, "case_confirmed")
        before_print = _count_actions(test_db, violation_id, "notice_printed")
        before_attested = _count_actions(
            test_db, violation_id, "notice_printer_attested"
        )

        with pytest.raises(ValueError, match="dismissed"):
            test_db.confirm_case(violation_id, enforcer_user)
        with pytest.raises(ValueError, match="dismissed"):
            test_db.confirm_notice_printed(violation_id, enforcer_user)
        with pytest.raises(ValueError, match="dismissed"):
            test_db.confirm_notice_printer_attested(violation_id, enforcer_user)

        assert _count_actions(test_db, violation_id, "case_confirmed") == before_confirm
        assert _count_actions(test_db, violation_id, "notice_printed") == before_print
        assert (
            _count_actions(test_db, violation_id, "notice_printer_attested")
            == before_attested
        )
        with test_db.db_session() as conn:
            status = conn.execute(
                "SELECT status FROM violations WHERE id = ?", (violation_id,)
            ).fetchone()["status"]
        assert status == "dismissed"

    def test_confirmed_then_dismissed_cannot_confirm_or_print_again(
        self, test_db, enforcer_user, violation_id
    ):
        test_db.confirm_case(violation_id, enforcer_user)
        historical = test_db.get_case_actions(violation_id)
        assert any(a["action_type"] == "case_confirmed" for a in historical)
        test_db.update_violation_status(violation_id, "dismissed", reviewed_by=enforcer_user)

        before_confirm = _count_actions(test_db, violation_id, "case_confirmed")
        before_print = _count_actions(test_db, violation_id, "notice_printed")
        before_attested = _count_actions(
            test_db, violation_id, "notice_printer_attested"
        )

        with pytest.raises(ValueError, match="dismissed"):
            test_db.confirm_case(violation_id, enforcer_user)
        with pytest.raises(ValueError, match="dismissed"):
            test_db.confirm_notice_printed(violation_id, enforcer_user)
        with pytest.raises(ValueError, match="dismissed"):
            test_db.confirm_notice_printer_attested(violation_id, enforcer_user)

        assert _count_actions(test_db, violation_id, "case_confirmed") == before_confirm
        assert _count_actions(test_db, violation_id, "notice_printed") == before_print
        assert (
            _count_actions(test_db, violation_id, "notice_printer_attested")
            == before_attested
        )
        # Historical confirmation event preserved; case remains dismissed.
        actions = test_db.get_case_actions(violation_id)
        assert any(a["action_type"] == "case_confirmed" for a in actions)
        with test_db.db_session() as conn:
            status = conn.execute(
                "SELECT status FROM violations WHERE id = ?", (violation_id,)
            ).fetchone()["status"]
        assert status == "dismissed"

    def test_printed_then_dismissed_does_not_return_print_success(
        self, test_db, enforcer_user, violation_id
    ):
        test_db.confirm_case(violation_id, enforcer_user)
        assert test_db.confirm_notice_printed(violation_id, enforcer_user) is True
        assert test_db.confirm_notice_printer_attested(violation_id, enforcer_user) is True
        historical = test_db.get_case_actions(violation_id)
        assert any(a["action_type"] == "notice_printed" for a in historical)
        assert any(a["action_type"] == "notice_printer_attested" for a in historical)

        test_db.update_violation_status(violation_id, "dismissed", reviewed_by=enforcer_user)
        before_print = _count_actions(test_db, violation_id, "notice_printed")
        before_attested = _count_actions(
            test_db, violation_id, "notice_printer_attested"
        )

        # Must not take the idempotent success path after dismissal.
        with pytest.raises(ValueError, match="dismissed"):
            test_db.confirm_notice_printed(violation_id, enforcer_user)
        with pytest.raises(ValueError, match="dismissed"):
            test_db.confirm_notice_printer_attested(violation_id, enforcer_user)

        assert _count_actions(test_db, violation_id, "notice_printed") == before_print
        assert (
            _count_actions(test_db, violation_id, "notice_printer_attested")
            == before_attested
        )
        actions = test_db.get_case_actions(violation_id)
        assert any(a["action_type"] == "case_confirmed" for a in actions)
        assert any(a["action_type"] == "notice_printed" for a in actions)
        assert any(a["action_type"] == "notice_printer_attested" for a in actions)
        with test_db.db_session() as conn:
            status = conn.execute(
                "SELECT status FROM violations WHERE id = ?", (violation_id,)
            ).fetchone()["status"]
        assert status == "dismissed"


# ---------------------------------------------------------------------------
# Plate verification / recurrence persistence stubs
# ---------------------------------------------------------------------------


class TestPlateVerification:
    @pytest.fixture
    def violation_id(self, test_db, sample_video_id):
        return test_db.insert_violation(
            video_id=sample_video_id,
            track_id=1,
            violation_type="Illegal Parking",
            confidence=0.95,
            frame_number=100,
            timestamp_sec=10.0,
        )

    def test_record_plate_verification(self, test_db, enforcer_user, violation_id):
        test_db.record_plate_verification(
            violation_id=violation_id,
            ocr_raw="ABC123",
            accepted_plate_text="ABC 123",
            plate_status="verified_readable",
            alpr_model="fast-alpr",
            alpr_version="1.0",
            ocr_confidence=0.92,
            verified_by=enforcer_user,
        )
        pv = test_db.get_plate_verification(violation_id)
        assert pv is not None
        assert pv["accepted_plate_text"] == "ABC 123"
        assert pv["plate_status"] == "verified_readable"
        assert pv["verified_by"] == enforcer_user

    def test_get_plate_verification_none_when_not_set(self, test_db, violation_id):
        assert test_db.get_plate_verification(violation_id) is None


class TestRecurrenceReview:
    @pytest.fixture
    def policy_version(self, test_db, admin_user):
        return test_db.propose_legal_policy_version(
            version="1.0.0", created_by=admin_user
        )

    @pytest.fixture
    def violation_id(self, test_db, sample_video_id):
        return test_db.insert_violation(
            video_id=sample_video_id,
            track_id=1,
            violation_type="Illegal Parking",
            confidence=0.95,
            frame_number=100,
            timestamp_sec=10.0,
        )

    def test_record_recurrence_review(
        self, test_db, enforcer_user, violation_id, policy_version
    ):
        test_db.record_recurrence_review(
            violation_id=violation_id,
            policy_version_id=policy_version,
            lookback_days=365,
            matched_violation_ids=[101, 102],
            eligible_match_ids=[101, 102, 103],
            suggested_recurrence_count=3,
            evaluated_by=enforcer_user,
        )
        rv = test_db.get_recurrence_review(violation_id, policy_version)
        assert rv is not None
        assert rv["suggested_recurrence_count"] == 3
        assert rv["evaluated_by"] == enforcer_user

    def test_get_recurrence_review_none_when_not_set(
        self, test_db, violation_id, policy_version
    ):
        assert test_db.get_recurrence_review(violation_id, policy_version) is None
