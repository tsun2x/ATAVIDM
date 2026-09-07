-- TAVIDM Migration 008: Legal-policy persistence layer
--
-- Additive only. Preserves all existing tables, columns, constraints, and
-- historical records. This migration is idempotent: re-running it on a
-- database that already has these tables will not fail or duplicate data.
--
-- New tables:
--   legal_policy_versions     — versioned legal-policy + mapping source register
--   legal_behavior_mappings   — per-canonical-rule behavior → category → provision
--   case_policy_records       — which policy version applied to which case
--   plate_verifications       — OCR candidates, human verification, provenance
--   case_action_events        — audit trail of officer actions (confirm, print)
--   recurrence_reviews        — recurrence match snapshots at review time
--   policy_permission_assignments — explicit supervisory capability assignments
--
-- Intended row identity for case_policy_records:
--   UNIQUE(violation_id, canonical_rule) so multiple contributing behaviors
--   (e.g. Illegal Parking + Obstruction) can be retained on one case.
--
-- Policy-level audit events (propose/approve/reject) use NULL violation_id.
--
-- This file must stay in sync with _apply_migration_008_inline in
-- sqlite_adapter.py (the inline fallback DDL). Existing databases created
-- from an earlier 008 variant are repaired by migration 009.

-- ---------------------------------------------------------------------------
-- legal_policy_versions: versioned source-backed legal-policy register
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS legal_policy_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version TEXT NOT NULL,
    status TEXT CHECK(status IN ('proposed','approved','rejected')) NOT NULL DEFAULT 'proposed',
    lookback_days INTEGER NOT NULL DEFAULT 365,
    schedule_json TEXT NOT NULL DEFAULT '{}',
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_by INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    approved_by INTEGER,
    approved_at DATETIME,
    rejected_by INTEGER,
    rejected_at DATETIME,
    UNIQUE(version)
);
CREATE INDEX IF NOT EXISTS idx_legal_policy_versions_status ON legal_policy_versions(status);
CREATE INDEX IF NOT EXISTS idx_legal_policy_versions_created_at ON legal_policy_versions(created_at);

-- ---------------------------------------------------------------------------
-- legal_behavior_mappings: per-canonical-rule behavior → category → provision
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS legal_behavior_mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_version_id INTEGER NOT NULL REFERENCES legal_policy_versions(id) ON DELETE CASCADE,
    canonical_rule TEXT NOT NULL,
    official_category TEXT,
    legal_status TEXT CHECK(legal_status IN ('verified','partially_verified','unverified','flag_only')) NOT NULL DEFAULT 'unverified',
    verified_elements_json TEXT NOT NULL DEFAULT '[]',
    unresolved_elements_json TEXT NOT NULL DEFAULT '[]',
    behavior_details_json TEXT NOT NULL DEFAULT '[]',
    provision_reference TEXT,
    penalty_schedule_json TEXT,
    source_url TEXT,
    source_document_id TEXT,
    mapping_version TEXT,
    is_grouped_with_json TEXT NOT NULL DEFAULT '[]',
    notes TEXT,
    UNIQUE(policy_version_id, canonical_rule)
);
CREATE INDEX IF NOT EXISTS idx_legal_mappings_version ON legal_behavior_mappings(policy_version_id);
CREATE INDEX IF NOT EXISTS idx_legal_mappings_rule ON legal_behavior_mappings(canonical_rule);

-- ---------------------------------------------------------------------------
-- case_policy_records: which policy version + mapping applied to which case
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS case_policy_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    violation_id INTEGER NOT NULL REFERENCES violations(id) ON DELETE CASCADE,
    review_id INTEGER REFERENCES review_queue(id) ON DELETE SET NULL,
    policy_version_id INTEGER NOT NULL REFERENCES legal_policy_versions(id),
    canonical_rule TEXT NOT NULL,
    official_category TEXT,
    legal_status TEXT CHECK(legal_status IN ('verified','partially_verified','unverified','flag_only')),
    behavior_details_json TEXT NOT NULL DEFAULT '[]',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(violation_id, canonical_rule)
);
CREATE INDEX IF NOT EXISTS idx_case_policy_violation ON case_policy_records(violation_id);
CREATE INDEX IF NOT EXISTS idx_case_policy_review ON case_policy_records(review_id);
CREATE INDEX IF NOT EXISTS idx_case_policy_version ON case_policy_records(policy_version_id);

-- ---------------------------------------------------------------------------
-- plate_verifications: OCR candidates and human-verified plate text
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS plate_verifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    violation_id INTEGER NOT NULL REFERENCES violations(id) ON DELETE CASCADE,
    review_id INTEGER REFERENCES review_queue(id) ON DELETE SET NULL,
    ocr_raw TEXT,
    accepted_plate_text TEXT,
    plate_status TEXT CHECK(plate_status IN (
        'not_attempted','processing_failed','unclear','not_visible',
        'candidate_awaiting_verification','verified_readable','migrated_unverified'
    )) NOT NULL DEFAULT 'not_attempted',
    alpr_model TEXT,
    alpr_version TEXT,
    ocr_confidence REAL,
    processing_diagnostics_json TEXT NOT NULL DEFAULT '{}',
    verified_by INTEGER REFERENCES users(id),
    verified_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(violation_id)
);
CREATE INDEX IF NOT EXISTS idx_plate_verifications_violation ON plate_verifications(violation_id);
CREATE INDEX IF NOT EXISTS idx_plate_verifications_status ON plate_verifications(plate_status);

-- ---------------------------------------------------------------------------
-- case_action_events: durable audit trail of officer actions
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS case_action_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    violation_id INTEGER REFERENCES violations(id) ON DELETE CASCADE,
    review_id INTEGER REFERENCES review_queue(id) ON DELETE SET NULL,
    action_type TEXT NOT NULL CHECK(action_type IN (
        'review_confirmed','case_confirmed','notice_printed','notice_printer_attested',
        'plate_verified','policy_proposed','policy_approved','policy_rejected',
        'recurrence_evaluated','event_time_confirmed'
    )),
    detail_json TEXT NOT NULL DEFAULT '{}',
    actor_user_id INTEGER REFERENCES users(id),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_case_actions_violation ON case_action_events(violation_id);
CREATE INDEX IF NOT EXISTS idx_case_actions_actor ON case_action_events(actor_user_id);
CREATE INDEX IF NOT EXISTS idx_case_actions_created_at ON case_action_events(created_at);

-- ---------------------------------------------------------------------------
-- recurrence_reviews: recurrence match snapshot at review time
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS recurrence_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    violation_id INTEGER NOT NULL REFERENCES violations(id) ON DELETE CASCADE,
    policy_version_id INTEGER NOT NULL REFERENCES legal_policy_versions(id),
    lookback_days INTEGER NOT NULL DEFAULT 365,
    matched_violation_ids_json TEXT NOT NULL DEFAULT '[]',
    eligible_match_ids_json TEXT NOT NULL DEFAULT '[]',
    suggested_recurrence_count INTEGER NOT NULL DEFAULT 0,
    evaluation_time DATETIME DEFAULT CURRENT_TIMESTAMP,
    evaluated_by INTEGER REFERENCES users(id),
    detail_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(violation_id, policy_version_id)
);
CREATE INDEX IF NOT EXISTS idx_recurrence_reviews_violation ON recurrence_reviews(violation_id);
CREATE INDEX IF NOT EXISTS idx_recurrence_reviews_version ON recurrence_reviews(policy_version_id);

-- ---------------------------------------------------------------------------
-- policy_permission_assignments: explicit supervisory capability assignments
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS policy_permission_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    permission TEXT NOT NULL CHECK(permission IN (
        'confirm_case','attest_print','propose_policy','approve_policy',
        'verify_plate','confirm_event_time'
    )),
    granted_by INTEGER REFERENCES users(id),
    granted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    revoked_at DATETIME,
    reason TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_permission_unique_active
    ON policy_permission_assignments(user_id, permission)
    WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_permission_user ON policy_permission_assignments(user_id);
CREATE INDEX IF NOT EXISTS idx_permission_perm ON policy_permission_assignments(permission);
