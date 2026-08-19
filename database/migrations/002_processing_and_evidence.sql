-- Migration 002: Processing queue + richer violation evidence
--
-- Addresses adviser deployment feedback:
--   1) Persist upload file sizes for realistic large-file handling.
--   2) Record per-run processing metadata so a run uses a frozen violation
--      snapshot, independent of later global setting changes.
--   3) Extend evidence with a vehicle crop and explicit plate-related fields
--      (structure only — no OCR/ALPR; plate_status tracks intent).
--
-- All changes are additive. Existing rows keep their evidence_path; the new
-- plate_* columns default to NULL / 'not_attempted'.

-- 2.1  Upload file size (additive on videos).
ALTER TABLE videos ADD COLUMN file_size_bytes INTEGER;

-- 2.2  Per-run processing metadata (Option A: dedicated table).
CREATE TABLE IF NOT EXISTS processing_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  video_id INTEGER REFERENCES videos(id) ON DELETE CASCADE,
  started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  finished_at DATETIME,
  status TEXT CHECK(status IN ('queued','running','completed','failed')) DEFAULT 'queued',
  enabled_violations_json TEXT NOT NULL DEFAULT '[]',
  error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_processing_runs_video_id ON processing_runs(video_id);
CREATE INDEX IF NOT EXISTS idx_processing_runs_status ON processing_runs(status);

-- 2.3  Evidence extension on review_queue.
ALTER TABLE review_queue ADD COLUMN vehicle_evidence_path TEXT;
ALTER TABLE review_queue ADD COLUMN plate_evidence_path TEXT;
ALTER TABLE review_queue ADD COLUMN plate_text TEXT;
ALTER TABLE review_queue ADD COLUMN plate_status TEXT CHECK(plate_status IN ('not_attempted','unreadable','recognized')) DEFAULT 'not_attempted';

-- 2.4  Evidence extension on violations.
ALTER TABLE violations ADD COLUMN vehicle_evidence_path TEXT;
ALTER TABLE violations ADD COLUMN plate_evidence_path TEXT;
ALTER TABLE violations ADD COLUMN plate_text TEXT;
ALTER TABLE violations ADD COLUMN plate_status TEXT CHECK(plate_status IN ('not_attempted','unreadable','recognized')) DEFAULT 'not_attempted';
