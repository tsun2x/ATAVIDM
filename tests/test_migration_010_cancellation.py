import sqlite3

from database import sqlite_adapter


def test_migration_010_preserves_runs_and_allows_cancelled(tmp_path):
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE videos (id INTEGER PRIMARY KEY);
        INSERT INTO videos(id) VALUES (1);
        CREATE TABLE processing_runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          video_id INTEGER REFERENCES videos(id) ON DELETE CASCADE,
          started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
          finished_at DATETIME,
          status TEXT CHECK(status IN ('queued','running','completed','failed')) DEFAULT 'queued',
          enabled_violations_json TEXT NOT NULL DEFAULT '[]',
          error_message TEXT
        );
        INSERT INTO processing_runs(video_id, status) VALUES (1, 'completed');
        """
    )
    sqlite_adapter._apply_migration_010(conn)
    conn.execute("UPDATE processing_runs SET status='cancelled' WHERE id=1")
    row = conn.execute("SELECT status, crossing_counts_json FROM processing_runs WHERE id=1").fetchone()
    assert row == ("cancelled", "{}")
    conn.close()
