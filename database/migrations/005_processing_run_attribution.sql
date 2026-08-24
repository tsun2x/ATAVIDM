-- Migration 005: processing_run_id attribution for detections and dependents
-- Forward-only / additive. Applied idempotently by sqlite_adapter._apply_migration_005.
--
-- Legacy rows: processing_run_id IS NULL.
-- Current-result queries select the latest completed run's attributed rows.
-- If a video has only NULL-attributed rows (pre-migration), those rows are used
-- as the current result until a new attributed run completes.

-- ALTER TABLE detections ADD COLUMN processing_run_id INTEGER REFERENCES processing_runs(id);
-- ALTER TABLE review_queue ADD COLUMN processing_run_id INTEGER REFERENCES processing_runs(id);
-- ALTER TABLE violations ADD COLUMN processing_run_id INTEGER REFERENCES processing_runs(id);

-- CREATE INDEX IF NOT EXISTS idx_detections_run_id ON detections(processing_run_id);
-- CREATE INDEX IF NOT EXISTS idx_detections_video_run ON detections(video_id, processing_run_id);
-- CREATE INDEX IF NOT EXISTS idx_review_queue_run_id ON review_queue(processing_run_id);
-- CREATE INDEX IF NOT EXISTS idx_violations_run_id ON violations(processing_run_id);
