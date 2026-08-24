-- TAVIDM SQLite schema

-- Roles (manuscript Ch3): admin = System Administrator,
-- enforcer = Traffic Enforcement Officer, viewer = Guest Viewer.
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  full_name TEXT,
  role TEXT CHECK(role IN ('admin','enforcer','viewer')) DEFAULT 'enforcer',
  is_active BOOLEAN DEFAULT 1,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS zone_templates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  template_name TEXT NOT NULL,
  description TEXT,
  zones_json TEXT NOT NULL DEFAULT '{}',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  last_used_at DATETIME,
  usage_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS videos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  filename TEXT NOT NULL,
  filepath TEXT NOT NULL,
  duration_sec REAL,
  recorded_at DATETIME,
  condition TEXT CHECK(condition IN ('morning','peak','nighttime')),
  processed BOOLEAN DEFAULT 0,
  status TEXT CHECK(status IN ('uploaded','annotating','ready','processing','processed')) DEFAULT 'uploaded',
  annotation_id INTEGER,
  template_id INTEGER REFERENCES zone_templates(id),
  file_size_bytes INTEGER,
  uploaded_by INTEGER REFERENCES users(id),
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS annotations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  video_id INTEGER NOT NULL UNIQUE REFERENCES videos(id) ON DELETE CASCADE,
  zones_json TEXT NOT NULL DEFAULT '{}',
  reference_frame_path TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS detections (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  video_id INTEGER REFERENCES videos(id),
  frame_number INTEGER,
  timestamp_sec REAL,
  track_id INTEGER,
  class_label TEXT,
  confidence REAL,
  bbox_x REAL,
  bbox_y REAL,
  bbox_w REAL,
  bbox_h REAL,
  processing_run_id INTEGER
);

CREATE TABLE IF NOT EXISTS violations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  video_id INTEGER REFERENCES videos(id),
  track_id INTEGER,
  violation_type TEXT NOT NULL,
  vehicle_class TEXT,
  confidence REAL NOT NULL,
  frame_number INTEGER,
  timestamp_sec REAL,
  detected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  evidence_path TEXT,
  reason_log TEXT,
  status TEXT CHECK(status IN ('confirmed','dismissed','pending')) DEFAULT 'confirmed',
  reviewed_by INTEGER REFERENCES users(id),
  vehicle_evidence_path TEXT,
  plate_evidence_path TEXT,
  plate_text TEXT,
  plate_status TEXT CHECK(plate_status IN ('not_attempted','unreadable','recognized')) DEFAULT 'not_attempted',
  detection_confidence REAL,
  violation_confidence REAL,
  evidence_sufficiency REAL,
  evidence_clip_path TEXT,
  evidence_sequence_dir TEXT,
  evidence_pre_sec REAL,
  evidence_post_sec REAL,
  episode_start_sec REAL,
  episode_end_sec REAL,
  contributing_factors_json TEXT,
  unavailable_factors_json TEXT,
  processing_run_id INTEGER
);

CREATE TABLE IF NOT EXISTS review_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  video_id INTEGER REFERENCES videos(id),
  track_id INTEGER,
  violation_type TEXT,
  vehicle_class TEXT,
  confidence REAL,
  frame_number INTEGER,
  timestamp_sec REAL,
  evidence_path TEXT,
  reason_log TEXT,
  status TEXT CHECK(status IN ('pending','confirmed','dismissed')) DEFAULT 'pending',
  queued_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  reviewed_by INTEGER REFERENCES users(id),
  reviewed_at DATETIME,
  vehicle_evidence_path TEXT,
  plate_evidence_path TEXT,
  plate_text TEXT,
  plate_status TEXT CHECK(plate_status IN ('not_attempted','unreadable','recognized')) DEFAULT 'not_attempted',
  detection_confidence REAL,
  violation_confidence REAL,
  evidence_sufficiency REAL,
  evidence_clip_path TEXT,
  evidence_sequence_dir TEXT,
  evidence_pre_sec REAL,
  evidence_post_sec REAL,
  episode_start_sec REAL,
  episode_end_sec REAL,
  contributing_factors_json TEXT,
  unavailable_factors_json TEXT,
  processing_run_id INTEGER
);

-- RTSP-supported live CCTV camera streams (manuscript Ch1 Scope, Ch3 Data Source).
CREATE TABLE IF NOT EXISTS cameras (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  location TEXT,
  rtsp_url TEXT NOT NULL,
  zones_json TEXT NOT NULL DEFAULT '{}',
  is_active BOOLEAN DEFAULT 1,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- System Configuration Module: traffic rule parameters, detection settings.
CREATE TABLE IF NOT EXISTS system_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Generated report history (Reporting Module).
CREATE TABLE IF NOT EXISTS reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  report_format TEXT CHECK(report_format IN ('pdf','excel')) NOT NULL,
  filters_json TEXT NOT NULL DEFAULT '{}',
  file_path TEXT NOT NULL,
  generated_by INTEGER REFERENCES users(id),
  generated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status);
CREATE INDEX IF NOT EXISTS idx_videos_template_id ON videos(template_id);
CREATE INDEX IF NOT EXISTS idx_annotations_video_id ON annotations(video_id);
CREATE INDEX IF NOT EXISTS idx_zone_templates_name ON zone_templates(template_name);
CREATE INDEX IF NOT EXISTS idx_violations_video_id ON violations(video_id);
CREATE INDEX IF NOT EXISTS idx_violations_status ON violations(status);
CREATE INDEX IF NOT EXISTS idx_violations_detected_at ON violations(detected_at);
CREATE INDEX IF NOT EXISTS idx_review_queue_status ON review_queue(status);
CREATE INDEX IF NOT EXISTS idx_detections_video_frame ON detections(video_id, frame_number);
CREATE INDEX IF NOT EXISTS idx_reports_generated_at ON reports(generated_at);

-- Per-run processing metadata: a run freezes the violation snapshot it used.
CREATE TABLE IF NOT EXISTS processing_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  video_id INTEGER REFERENCES videos(id) ON DELETE CASCADE,
  started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  finished_at DATETIME,
  status TEXT CHECK(status IN ('queued','running','completed','failed')) DEFAULT 'queued',
  enabled_violations_json TEXT NOT NULL DEFAULT '[]',
  error_message TEXT,
  diagnostics_json TEXT,
  geometry_snapshot_json TEXT,
  stage TEXT,
  viewer_mode TEXT DEFAULT 'background',
  queued_at DATETIME,
  frames_processed INTEGER DEFAULT 0,
  total_frames INTEGER,
  progress_percent REAL DEFAULT 0,
  elapsed_sec REAL,
  processing_fps REAL,
  detection_records INTEGER DEFAULT 0,
  unique_tracks INTEGER,
  class_counts_json TEXT DEFAULT '{}',
  violation_candidates INTEGER DEFAULT 0,
  annotated_video_path TEXT,
  annotated_video_ready INTEGER DEFAULT 0,
  model_identifier TEXT,
  source_duration_sec REAL,
  results_removed_at DATETIME,
  effective_output_fps REAL,
  results_run_scoped INTEGER NOT NULL DEFAULT 0,
  results_scope_explicit INTEGER NOT NULL DEFAULT 0,
  results_scope_origin TEXT
);

CREATE INDEX IF NOT EXISTS idx_processing_runs_video_id ON processing_runs(video_id);
CREATE INDEX IF NOT EXISTS idx_processing_runs_status ON processing_runs(status);
CREATE INDEX IF NOT EXISTS idx_detections_run_id ON detections(processing_run_id);
CREATE INDEX IF NOT EXISTS idx_detections_video_run ON detections(video_id, processing_run_id);
CREATE INDEX IF NOT EXISTS idx_review_queue_run_id ON review_queue(processing_run_id);
CREATE INDEX IF NOT EXISTS idx_violations_run_id ON violations(processing_run_id);

-- Durable upload/processing history events (survives reloads; cascades with video).
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

-- Minimal audit retained after permanent video deletion.
CREATE TABLE IF NOT EXISTS system_audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_type TEXT NOT NULL,
  detail_json TEXT NOT NULL DEFAULT '{}',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
