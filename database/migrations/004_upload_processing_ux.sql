-- Migration 004: uploaded-video processing UX
-- Additive columns for run progress, annotated artifacts, history, and upload actor.
-- Applied idempotently by sqlite_adapter._apply_migration_004.

-- videos: who uploaded (nullable for legacy rows)
-- ALTER TABLE videos ADD COLUMN uploaded_by INTEGER REFERENCES users(id);

-- processing_runs: rich progress + artifact metadata
-- ALTER TABLE processing_runs ADD COLUMN stage TEXT;
-- ALTER TABLE processing_runs ADD COLUMN viewer_mode TEXT DEFAULT 'background';
-- ALTER TABLE processing_runs ADD COLUMN queued_at DATETIME;
-- ALTER TABLE processing_runs ADD COLUMN frames_processed INTEGER DEFAULT 0;
-- ALTER TABLE processing_runs ADD COLUMN total_frames INTEGER;
-- ALTER TABLE processing_runs ADD COLUMN progress_percent REAL DEFAULT 0;
-- ALTER TABLE processing_runs ADD COLUMN elapsed_sec REAL;
-- ALTER TABLE processing_runs ADD COLUMN processing_fps REAL;
-- ALTER TABLE processing_runs ADD COLUMN detection_records INTEGER DEFAULT 0;
-- ALTER TABLE processing_runs ADD COLUMN unique_tracks INTEGER;
-- ALTER TABLE processing_runs ADD COLUMN class_counts_json TEXT DEFAULT '{}';
-- ALTER TABLE processing_runs ADD COLUMN violation_candidates INTEGER DEFAULT 0;
-- ALTER TABLE processing_runs ADD COLUMN annotated_video_path TEXT;
-- ALTER TABLE processing_runs ADD COLUMN annotated_video_ready INTEGER DEFAULT 0;
-- ALTER TABLE processing_runs ADD COLUMN model_identifier TEXT;
-- ALTER TABLE processing_runs ADD COLUMN source_duration_sec REAL;
-- ALTER TABLE processing_runs ADD COLUMN results_removed_at DATETIME;
-- ALTER TABLE processing_runs ADD COLUMN effective_output_fps REAL;

-- Durable audit/history events for upload + processing lifecycle
CREATE TABLE IF NOT EXISTS video_history_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  run_id INTEGER REFERENCES processing_runs(id) ON DELETE SET NULL,
  event_type TEXT NOT NULL,
  detail_json TEXT NOT NULL DEFAULT '{}',
  actor_user_id INTEGER REFERENCES users(id),
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_video_history_video_id ON video_history_events(video_id);
CREATE INDEX IF NOT EXISTS idx_video_history_created_at ON video_history_events(created_at);
