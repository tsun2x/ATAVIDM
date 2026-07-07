-- TAVIDM SQLite schema

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT CHECK(role IN ('admin','enforcer')) DEFAULT 'enforcer',
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
  confidence REAL,
  frame_number INTEGER,
  evidence_path TEXT,
  reason_log TEXT,
  status TEXT CHECK(status IN ('pending','confirmed','dismissed')) DEFAULT 'pending',
  queued_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  reviewed_by INTEGER REFERENCES users(id),
  reviewed_at DATETIME
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
