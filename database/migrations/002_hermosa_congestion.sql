-- Migration 002: Hermosa Connect congestion + hotspot data model
-- Mirrors the tables added to schema.sql. Safe to re-run (IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS congestion_observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  camera_id INTEGER REFERENCES cameras(id) ON DELETE CASCADE,
  location TEXT,
  obs_time DATETIME NOT NULL,
  vehicle_count INTEGER,
  moving_count INTEGER,
  stationary_count INTEGER,
  avg_speed_kmh REAL,
  density_score REAL,
  source TEXT DEFAULT 'demo'
);

CREATE TABLE IF NOT EXISTS congestion_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  camera_id INTEGER REFERENCES cameras(id) ON DELETE CASCADE,
  location TEXT,
  severity TEXT CHECK(severity IN ('low','moderate','heavy','severe')) NOT NULL,
  state TEXT CHECK(state IN ('building','congested','improving','cleared')) NOT NULL,
  started_at DATETIME NOT NULL,
  ended_at DATETIME,
  duration_minutes REAL,
  peak_vehicle_count INTEGER,
  avg_density_score REAL
);

CREATE TABLE IF NOT EXISTS hotspots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  location TEXT NOT NULL,
  camera_id INTEGER REFERENCES cameras(id) ON DELETE SET NULL,
  frequency_score REAL,
  avg_duration_minutes REAL,
  peak_severity TEXT,
  rank INTEGER,
  computed_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_congestion_obs_camera_time ON congestion_observations(camera_id, obs_time);
CREATE INDEX IF NOT EXISTS idx_congestion_events_camera ON congestion_events(camera_id);
CREATE INDEX IF NOT EXISTS idx_hotspots_rank ON hotspots(rank);