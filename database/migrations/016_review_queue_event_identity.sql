-- Speeds the idempotent lookup for a repeated event when an uploaded video is
-- reprocessed. This is intentionally non-unique: existing historical queue
-- duplicates are retained and the write path reuses a matching row going forward.
CREATE INDEX IF NOT EXISTS idx_review_queue_event_identity
  ON review_queue(video_id, track_id, violation_type, frame_number, timestamp_sec);
