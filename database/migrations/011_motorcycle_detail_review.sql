-- Migration 011: motorcycle detail-review candidates (SEPARATE from review_queue)
--
-- Additive only. No existing table, column, constraint, index, or API contract is
-- altered. This table stores motorcycle detail-review candidates produced by the
-- uploaded-video pipeline (track-occurrence grouping + a queued crop pass) and
-- their human review outcomes.
--
-- Hard boundaries enforced by the application:
--   * the crop pass can supply evidence or a review flag only;
--   * it can never declare, confirm, or create a violation case;
--   * generic `person` never substitutes for `rider`;
--   * a missing mirror detection is UNKNOWN, never proof of absence.
--
-- dedup_key encodes (processing run, track occurrence, selector version) so
-- retrying a scan updates the same candidate row instead of inserting another.
-- It uses TEXT because SQLite treats NULL as distinct in UNIQUE constraints,
-- which would defeat dedup for direct (run-less) processing calls.
--
-- scan_state    : queued -> scanning -> ready | failed
-- human_outcome : pending -> reviewed | dismissed | uncertain
--                 (there is deliberately no "confirmed" outcome here)

CREATE TABLE IF NOT EXISTS motorcycle_detail_candidates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  video_id INTEGER REFERENCES videos(id) ON DELETE CASCADE,
  processing_run_id INTEGER,
  run_key TEXT NOT NULL,
  track_id INTEGER NOT NULL,
  occurrence_index INTEGER NOT NULL DEFAULT 1,
  occurrence_key TEXT NOT NULL,
  selector_version TEXT NOT NULL,
  dedup_key TEXT NOT NULL,
  scan_state TEXT CHECK(scan_state IN ('queued','scanning','ready','failed')) NOT NULL DEFAULT 'queued',
  human_outcome TEXT CHECK(human_outcome IN ('pending','reviewed','dismissed','uncertain')) NOT NULL DEFAULT 'pending',
  frame_number INTEGER,
  timestamp_sec REAL,
  source_width INTEGER,
  source_height INTEGER,
  frame_count INTEGER NOT NULL DEFAULT 0,
  frames_json TEXT NOT NULL DEFAULT '[]',
  detection_bbox_json TEXT NOT NULL DEFAULT '{}',
  frame_score REAL,
  score_breakdown_json TEXT NOT NULL DEFAULT '{}',
  size_bytes INTEGER NOT NULL DEFAULT 0,
  scan_model TEXT,
  scan_class_map_json TEXT NOT NULL DEFAULT '[]',
  observations_json TEXT NOT NULL DEFAULT '{}',
  association_json TEXT NOT NULL DEFAULT '{}',
  uncertainty_json TEXT NOT NULL DEFAULT '[]',
  scan_attempts INTEGER NOT NULL DEFAULT 0,
  scan_error TEXT,
  scan_seconds REAL,
  claimed_at DATETIME,
  scanned_at DATETIME,
  reviewed_by INTEGER REFERENCES users(id),
  reviewed_at DATETIME,
  reviewer_notes TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_motorcycle_detail_dedup
  ON motorcycle_detail_candidates(dedup_key);
CREATE INDEX IF NOT EXISTS idx_motorcycle_detail_state
  ON motorcycle_detail_candidates(scan_state, human_outcome);
CREATE INDEX IF NOT EXISTS idx_motorcycle_detail_video
  ON motorcycle_detail_candidates(video_id);
CREATE INDEX IF NOT EXISTS idx_motorcycle_detail_run
  ON motorcycle_detail_candidates(processing_run_id);
