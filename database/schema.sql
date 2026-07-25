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
  bbox_h REAL
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
  reviewed_by INTEGER REFERENCES users(id)
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
  reviewed_at DATETIME
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
