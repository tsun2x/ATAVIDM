-- Additive append-only review decisions.
-- Does not alter review_queue, violations, or case_action_events.

CREATE TABLE IF NOT EXISTS review_decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  review_id INTEGER NOT NULL REFERENCES review_queue(id),
  decision TEXT NOT NULL CHECK(decision IN (
    'confirm_proposed',
    'correct_canonical',
    'no_violation',
    'insufficient_evidence'
  )),
  original_violation_type TEXT NOT NULL,
  selected_canonical_rule TEXT,
  reason TEXT NOT NULL,
  reviewer_user_id INTEGER NOT NULL REFERENCES users(id),
  decided_at TEXT NOT NULL,
  evidence_refs_json TEXT NOT NULL DEFAULT '{}',
  policy_refs_json TEXT NOT NULL DEFAULT '{}',
  idempotency_key TEXT NOT NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  CHECK (
    (
      decision IN ('confirm_proposed', 'correct_canonical')
      AND selected_canonical_rule IS NOT NULL
    )
    OR (
      decision IN ('no_violation', 'insufficient_evidence')
      AND selected_canonical_rule IS NULL
    )
  )
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_review_decisions_one_per_review
  ON review_decisions(review_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_review_decisions_idempotency
  ON review_decisions(review_id, idempotency_key);
CREATE INDEX IF NOT EXISTS idx_review_decisions_decision
  ON review_decisions(decision, id);

CREATE TRIGGER IF NOT EXISTS review_decisions_no_update
BEFORE UPDATE ON review_decisions
BEGIN
  SELECT RAISE(ABORT, 'review_decisions is append-only');
END;

CREATE TRIGGER IF NOT EXISTS review_decisions_no_delete
BEFORE DELETE ON review_decisions
BEGIN
  SELECT RAISE(ABORT, 'review_decisions is append-only');
END;
