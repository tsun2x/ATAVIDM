-- Migration 003: Dual confidence + temporal evidence metadata
-- Additive / non-destructive. Legacy ``confidence`` remains and maps to
-- violation_confidence for compatibility (see docs).

ALTER TABLE violations ADD COLUMN detection_confidence REAL;
ALTER TABLE violations ADD COLUMN violation_confidence REAL;
ALTER TABLE violations ADD COLUMN evidence_sufficiency REAL;
ALTER TABLE violations ADD COLUMN evidence_clip_path TEXT;
ALTER TABLE violations ADD COLUMN evidence_sequence_dir TEXT;
ALTER TABLE violations ADD COLUMN evidence_pre_sec REAL;
ALTER TABLE violations ADD COLUMN evidence_post_sec REAL;
ALTER TABLE violations ADD COLUMN episode_start_sec REAL;
ALTER TABLE violations ADD COLUMN episode_end_sec REAL;
ALTER TABLE violations ADD COLUMN contributing_factors_json TEXT;
ALTER TABLE violations ADD COLUMN unavailable_factors_json TEXT;

ALTER TABLE review_queue ADD COLUMN detection_confidence REAL;
ALTER TABLE review_queue ADD COLUMN violation_confidence REAL;
ALTER TABLE review_queue ADD COLUMN evidence_sufficiency REAL;
ALTER TABLE review_queue ADD COLUMN evidence_clip_path TEXT;
ALTER TABLE review_queue ADD COLUMN evidence_sequence_dir TEXT;
ALTER TABLE review_queue ADD COLUMN evidence_pre_sec REAL;
ALTER TABLE review_queue ADD COLUMN evidence_post_sec REAL;
ALTER TABLE review_queue ADD COLUMN episode_start_sec REAL;
ALTER TABLE review_queue ADD COLUMN episode_end_sec REAL;
ALTER TABLE review_queue ADD COLUMN contributing_factors_json TEXT;
ALTER TABLE review_queue ADD COLUMN unavailable_factors_json TEXT;

ALTER TABLE processing_runs ADD COLUMN diagnostics_json TEXT;
ALTER TABLE processing_runs ADD COLUMN geometry_snapshot_json TEXT;
