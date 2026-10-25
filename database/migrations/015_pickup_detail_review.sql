-- Migration 015: independent pickup/person evidence-review leads.
-- Additive only: no existing table or API contract is modified.
-- These rows describe review leads, not canonical violations or review_queue cases.
CREATE TABLE IF NOT EXISTS pickup_detail_candidates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  video_id INTEGER REFERENCES videos(id) ON DELETE CASCADE,
  processing_run_id INTEGER,
  run_key TEXT NOT NULL,
  pickup_track_id INTEGER NOT NULL,
  person_track_id INTEGER NOT NULL,
  pickup_identity_epoch INTEGER NOT NULL DEFAULT 0,
  person_identity_epoch INTEGER NOT NULL DEFAULT 0,
  occurrence_key TEXT NOT NULL,
  selector_version TEXT NOT NULL,
  dedup_key TEXT NOT NULL UNIQUE,
  human_outcome TEXT CHECK(human_outcome IN ('pending','reviewed','dismissed','uncertain')) NOT NULL DEFAULT 'pending',
  frame_number INTEGER,
  timestamp_sec REAL,
  episode_start_sec REAL,
  source_width INTEGER,
  source_height INTEGER,
  frame_count INTEGER NOT NULL DEFAULT 0,
  frames_json TEXT NOT NULL DEFAULT '[]',
  association_json TEXT NOT NULL DEFAULT '{}',
  size_bytes INTEGER NOT NULL DEFAULT 0,
  reviewed_by INTEGER REFERENCES users(id),
  reviewed_at DATETIME,
  reviewer_notes TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_pickup_detail_outcome
  ON pickup_detail_candidates(human_outcome, id);
CREATE INDEX IF NOT EXISTS idx_pickup_detail_video
  ON pickup_detail_candidates(video_id);
CREATE INDEX IF NOT EXISTS idx_pickup_detail_run
  ON pickup_detail_candidates(processing_run_id);
