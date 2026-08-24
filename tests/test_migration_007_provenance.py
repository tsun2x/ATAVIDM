"""Correction pass 3: migration 007 provenance + legacy false-scope repair."""

from __future__ import annotations

import sqlite3

from core.upload_analytics import build_upload_processing_analytics


def _point_adapter_at(db_path, monkeypatch) -> None:
    path = str(db_path)
    monkeypatch.setenv("SQLITE_PATH", path)
    monkeypatch.setenv("DATABASE_URL", path)


def _insert_null_detections(
    conn: sqlite3.Connection, video_id: int, n: int, *, label: str = "car"
) -> None:
    for i in range(n):
        conn.execute(
            """
            INSERT INTO detections (
              video_id, frame_number, timestamp_sec, track_id, class_label, confidence,
              bbox_x, bbox_y, bbox_w, bbox_h, processing_run_id
            ) VALUES (?, ?, 0.0, ?, ?, 0.9, 0, 0, 1, 1, NULL)
            """,
            (video_id, i, i + 1, label),
        )


def _minimal_legacy_schema(conn: sqlite3.Connection, *, with_diag_geom: bool = True) -> None:
    diag_cols = ""
    if with_diag_geom:
        diag_cols = ", diagnostics_json TEXT, geometry_snapshot_json TEXT"
    conn.executescript(
        f"""
        CREATE TABLE videos (
          id INTEGER PRIMARY KEY,
          filename TEXT,
          filepath TEXT,
          duration_sec REAL,
          recorded_at DATETIME,
          condition TEXT,
          status TEXT,
          file_size_bytes INTEGER,
          uploaded_by INTEGER,
          created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE detections (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          video_id INTEGER,
          frame_number INTEGER,
          timestamp_sec REAL,
          track_id INTEGER,
          class_label TEXT,
          confidence REAL,
          bbox_x REAL, bbox_y REAL, bbox_w REAL, bbox_h REAL,
          processing_run_id INTEGER
        );
        CREATE TABLE review_queue (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          video_id INTEGER,
          status TEXT DEFAULT 'pending',
          processing_run_id INTEGER
        );
        CREATE TABLE violations (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          video_id INTEGER,
          status TEXT DEFAULT 'pending',
          processing_run_id INTEGER
        );
        CREATE TABLE processing_runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          video_id INTEGER,
          status TEXT,
          enabled_violations_json TEXT NOT NULL DEFAULT '[]',
          started_at DATETIME,
          finished_at DATETIME,
          viewer_mode TEXT DEFAULT 'background',
          queued_at DATETIME,
          stage TEXT,
          error_message TEXT,
          detection_records INTEGER DEFAULT 0,
          unique_tracks INTEGER,
          class_counts_json TEXT DEFAULT '{{}}',
          violation_candidates INTEGER DEFAULT 0,
          annotated_video_path TEXT,
          annotated_video_ready INTEGER DEFAULT 0
          {diag_cols}
        );
        """
    )


class TestMigration007LegacySafety:
    def test_false_positive_metadata_backfill(self, tmp_path, monkeypatch):
        """Diagnostics/geometry/stage must not hide NULL-attributed legacy detections."""
        from database import sqlite_adapter as sa

        db_path = tmp_path / "fp_meta.db"
        _point_adapter_at(db_path, monkeypatch)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _minimal_legacy_schema(conn)
        conn.execute(
            "INSERT INTO videos (id, filename, filepath, status) "
            "VALUES (1,'v.mp4','/v','processed')"
        )
        conn.execute(
            """
            INSERT INTO processing_runs (
              id, video_id, status, queued_at, stage,
              diagnostics_json, geometry_snapshot_json
            ) VALUES (1, 1, 'completed', '2026-08-20 10:00:00', 'completed',
                      '{"ok":1}', '{"z":1}')
            """
        )
        _insert_null_detections(conn, 1, 5, label="car")
        conn.commit()
        conn.close()

        with sqlite3.connect(db_path) as c:
            c.row_factory = sqlite3.Row
            sa._apply_migration_006(c)
            sa._apply_migration_007(c)
            c.commit()
            run = c.execute("SELECT * FROM processing_runs WHERE id=1").fetchone()
            assert int(run["results_run_scoped"] or 0) == 0
            assert int(run["results_scope_explicit"] or 0) == 0
            assert c.execute("SELECT COUNT(*) FROM detections").fetchone()[0] == 5

        summary = sa.detection_summary_for_video(1)
        assert summary["detection_records"] == 5
        assert summary["class_counts"] == {"car": 5}
        assert summary["processing_run_id"] is None
        assert summary["result_scope"] == "legacy"

    def test_already_misclassified_database_repair(self, tmp_path, monkeypatch):
        from database import sqlite_adapter as sa

        db_path = tmp_path / "misclass.db"
        _point_adapter_at(db_path, monkeypatch)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _minimal_legacy_schema(conn)
        conn.execute(
            "ALTER TABLE processing_runs "
            "ADD COLUMN results_run_scoped INTEGER NOT NULL DEFAULT 0"
        )
        conn.execute(
            "INSERT INTO videos (id, filename, filepath, status) "
            "VALUES (1,'m.mp4','/m','processed')"
        )
        conn.execute(
            """
            INSERT INTO processing_runs (
              id, video_id, status, queued_at, stage,
              diagnostics_json, geometry_snapshot_json, results_run_scoped
            ) VALUES (1, 1, 'completed', '2026-08-20 10:00:00', 'completed',
                      '{}', '{}', 1)
            """
        )
        _insert_null_detections(conn, 1, 3, label="truck")
        conn.commit()
        conn.close()

        with sqlite3.connect(db_path) as c:
            c.row_factory = sqlite3.Row
            sa._apply_migration_007(c)
            c.commit()
            run = c.execute("SELECT * FROM processing_runs WHERE id=1").fetchone()
            assert int(run["results_run_scoped"] or 0) == 0
            assert int(run["results_scope_explicit"] or 0) == 0
            assert c.execute("SELECT COUNT(*) FROM detections").fetchone()[0] == 3

        summary = sa.detection_summary_for_video(1)
        assert summary["detection_records"] == 3
        assert summary["class_counts"] == {"truck": 3}
        assert summary["result_scope"] == "legacy"
        assert summary["processing_run_id"] is None

    def test_attributed_historical_run_remains_scoped(self, tmp_path, monkeypatch):
        from database import sqlite_adapter as sa

        db_path = tmp_path / "attr.db"
        _point_adapter_at(db_path, monkeypatch)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _minimal_legacy_schema(conn, with_diag_geom=False)
        conn.execute(
            "INSERT INTO videos (id, filename, filepath, status) "
            "VALUES (1,'a.mp4','/a','processed')"
        )
        conn.execute(
            "INSERT INTO processing_runs (id, video_id, status) VALUES (1, 1, 'completed')"
        )
        conn.execute(
            """
            INSERT INTO detections (
              video_id, frame_number, timestamp_sec, track_id, class_label, confidence,
              bbox_x, bbox_y, bbox_w, bbox_h, processing_run_id
            ) VALUES (1, 0, 0.0, 1, 'bus', 0.8, 0, 0, 1, 1, 1)
            """
        )
        conn.execute(
            "INSERT INTO review_queue (video_id, status, processing_run_id) "
            "VALUES (1, 'pending', 1)"
        )
        conn.commit()
        conn.close()

        with sqlite3.connect(db_path) as c:
            c.row_factory = sqlite3.Row
            sa._apply_migration_006(c)
            sa._apply_migration_007(c)
            c.commit()
            run = c.execute("SELECT * FROM processing_runs WHERE id=1").fetchone()
            assert int(run["results_run_scoped"] or 0) == 1
            assert int(run["results_scope_explicit"] or 0) == 1
            assert run["results_scope_origin"] == "attributed_children"
            det = c.execute(
                "SELECT processing_run_id, class_label FROM detections"
            ).fetchone()
            assert det[0] == 1 and det[1] == "bus"

        summary = sa.detection_summary_for_video(1)
        assert summary["result_scope"] == "run"
        assert summary["processing_run_id"] == 1
        assert summary["detection_records"] == 1
        assert summary["class_counts"] == {"bus": 1}

    def test_explicit_new_zero_result_run_supersedes_legacy(self, test_db):
        from database import sqlite_adapter as sa

        vid = test_db.insert_video(filename="z.mp4", filepath="/tmp/z.mp4", status="ready")
        for i in range(4):
            test_db.insert_detection(
                video_id=vid,
                frame_number=i,
                timestamp_sec=float(i),
                track_id=i + 1,
                class_label="car",
                confidence=0.9,
                bbox_x=1,
                bbox_y=1,
                bbox_w=2,
                bbox_h=2,
                processing_run_id=None,
            )
        before = test_db.detection_summary_for_video(vid)
        assert before["detection_records"] == 4
        assert before["result_scope"] == "legacy"

        run_id = test_db.create_processing_run(vid, "[]")
        run = test_db.get_processing_run(run_id)
        assert int(run["results_run_scoped"] or 0) == 1
        assert int(run["results_scope_explicit"] or 0) == 1
        assert run["results_scope_origin"] == "create"
        test_db.finish_processing_run(
            run_id,
            status="completed",
            progress={
                "detection_records": 0,
                "unique_tracks": 0,
                "class_counts": {},
                "violation_candidates": 0,
            },
        )
        after = test_db.detection_summary_for_video(vid)
        assert after["detection_records"] == 0
        assert after["class_counts"] == {}
        assert after["result_scope"] == "run"
        assert after["processing_run_id"] == run_id
        assert len(test_db.get_detections_by_video(vid, current_only=False)) == 4

        with sa.db_session() as conn:
            sa._apply_migration_006(conn)
            sa._apply_migration_007(conn)
        run2 = test_db.get_processing_run(run_id)
        assert int(run2["results_run_scoped"] or 0) == 1
        assert int(run2["results_scope_explicit"] or 0) == 1
        assert test_db.detection_summary_for_video(vid)["detection_records"] == 0

    def test_migrations_idempotent_mixed_population(self, tmp_path, monkeypatch):
        from database import sqlite_adapter as sa

        db_path = tmp_path / "idem.db"
        _point_adapter_at(db_path, monkeypatch)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _minimal_legacy_schema(conn)
        conn.execute(
            "INSERT INTO videos (id, filename, filepath, status) "
            "VALUES (1,'i.mp4','/i','processed')"
        )
        conn.execute(
            """
            INSERT INTO processing_runs (
              id, video_id, status, queued_at, stage,
              diagnostics_json, geometry_snapshot_json
            ) VALUES (1, 1, 'completed', '2026-08-01', 'completed', '{}', '{}')
            """
        )
        _insert_null_detections(conn, 1, 2, label="car")
        conn.execute(
            "INSERT INTO videos (id, filename, filepath, status) "
            "VALUES (2,'j.mp4','/j','processed')"
        )
        conn.execute(
            "INSERT INTO processing_runs (id, video_id, status) VALUES (2, 2, 'completed')"
        )
        conn.execute(
            """
            INSERT INTO detections (
              video_id, frame_number, timestamp_sec, track_id, class_label, confidence,
              bbox_x, bbox_y, bbox_w, bbox_h, processing_run_id
            ) VALUES (2, 0, 0.0, 1, 'van', 0.7, 0, 0, 1, 1, 2)
            """
        )
        conn.commit()
        conn.close()

        for _ in range(3):
            with sqlite3.connect(db_path) as c:
                c.row_factory = sqlite3.Row
                sa._apply_migration_006(c)
                sa._apply_migration_007(c)
                c.commit()

        with sqlite3.connect(db_path) as c:
            c.row_factory = sqlite3.Row
            cols = [r[1] for r in c.execute("PRAGMA table_info(processing_runs)").fetchall()]
            assert cols.count("results_run_scoped") == 1
            assert cols.count("results_scope_explicit") == 1
            assert cols.count("results_scope_origin") == 1
            r1 = c.execute("SELECT * FROM processing_runs WHERE id=1").fetchone()
            r2 = c.execute("SELECT * FROM processing_runs WHERE id=2").fetchone()
            assert int(r1["results_run_scoped"] or 0) == 0
            assert int(r1["results_scope_explicit"] or 0) == 0
            assert int(r2["results_run_scoped"] or 0) == 1
            assert int(r2["results_scope_explicit"] or 0) == 1
            assert c.execute("SELECT COUNT(*) FROM detections").fetchone()[0] == 3

        zero_vid = sa.insert_video(filename="zero.mp4", filepath="/z", status="ready")
        zero_run = sa.create_processing_run(zero_vid, "[]")
        sa.finish_processing_run(
            zero_run,
            status="completed",
            progress={
                "detection_records": 0,
                "class_counts": {},
                "violation_candidates": 0,
            },
        )
        with sqlite3.connect(db_path) as c:
            c.row_factory = sqlite3.Row
            sa._apply_migration_006(c)
            sa._apply_migration_007(c)
            c.commit()
            zr = c.execute(
                "SELECT results_run_scoped, results_scope_explicit "
                "FROM processing_runs WHERE id=?",
                (zero_run,),
            ).fetchone()
            assert int(zr[0]) == 1 and int(zr[1]) == 1
        assert sa.detection_summary_for_video(zero_vid)["detection_records"] == 0

    def test_analytics_legacy_then_zero_agree(self, test_db):
        vid = test_db.insert_video(filename="an.mp4", filepath="/tmp/an.mp4", status="ready")
        test_db.insert_detection(
            video_id=vid,
            frame_number=0,
            timestamp_sec=0.0,
            track_id=1,
            class_label="motorcycle",
            confidence=0.9,
            bbox_x=1,
            bbox_y=1,
            bbox_w=2,
            bbox_h=2,
            processing_run_id=None,
        )
        stats = build_upload_processing_analytics("today")
        assert stats["detection_records"] >= 1
        assert stats["class_counts"].get("motorcycle", 0) >= 1

        run_id = test_db.create_processing_run(vid, "[]")
        test_db.finish_processing_run(
            run_id,
            status="completed",
            progress={
                "detection_records": 0,
                "class_counts": {},
                "violation_candidates": 0,
            },
        )
        summary = test_db.detection_summary_for_video(vid)
        assert summary["detection_records"] == 0
        assert summary["class_counts"] == {}
        assert summary["violation_candidates"] == 0
        assert summary["result_scope"] == "run"
        stats2 = build_upload_processing_analytics("today")
        assert stats2["detection_records"] == 0
        assert stats2["class_counts"] == {}
        assert stats2["violation_candidates"] == 0

    def test_video6_equivalent_large_null_legacy_visible(self, tmp_path, monkeypatch):
        """Same ownership conditions as Video 6 with a smaller generated row count."""
        from database import sqlite_adapter as sa

        db_path = tmp_path / "v6eq.db"
        _point_adapter_at(db_path, monkeypatch)
        n = 200
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _minimal_legacy_schema(conn)
        conn.execute(
            "INSERT INTO videos (id, filename, filepath, status) "
            "VALUES (6,'hwy.mp4','/h','processed')"
        )
        conn.execute(
            "ALTER TABLE processing_runs "
            "ADD COLUMN results_run_scoped INTEGER NOT NULL DEFAULT 0"
        )
        conn.execute(
            """
            INSERT INTO processing_runs (
              id, video_id, status, diagnostics_json, geometry_snapshot_json,
              results_run_scoped
            ) VALUES (1, 6, 'completed', '{"frames":1}', '{"zones":[]}', 1)
            """
        )
        _insert_null_detections(conn, 6, n, label="car")
        conn.commit()
        conn.close()

        with sqlite3.connect(db_path) as c:
            c.row_factory = sqlite3.Row
            sa._apply_migration_007(c)
            c.commit()
            run = c.execute("SELECT * FROM processing_runs WHERE id=1").fetchone()
            assert int(run["results_run_scoped"] or 0) == 0
            assert int(run["results_scope_explicit"] or 0) == 0
            null_n = c.execute(
                "SELECT COUNT(*) FROM detections "
                "WHERE video_id=6 AND processing_run_id IS NULL"
            ).fetchone()[0]
            attr_n = c.execute(
                "SELECT COUNT(*) FROM detections "
                "WHERE video_id=6 AND processing_run_id IS NOT NULL"
            ).fetchone()[0]
            assert null_n == n and attr_n == 0

        summary = sa.detection_summary_for_video(6)
        assert summary["detection_records"] == n
        assert summary["class_counts"] == {"car": n}
        assert summary["result_scope"] == "legacy"
        assert summary["processing_run_id"] is None

        run2 = sa.create_processing_run(6, "[]")
        sa.finish_processing_run(
            run2,
            status="completed",
            progress={
                "detection_records": 0,
                "class_counts": {},
                "violation_candidates": 0,
            },
        )
        after = sa.detection_summary_for_video(6)
        assert after["detection_records"] == 0
        assert after["class_counts"] == {}
        assert after["result_scope"] == "run"
        assert after["processing_run_id"] == run2
        with sqlite3.connect(db_path) as c:
            assert (
                c.execute("SELECT COUNT(*) FROM detections WHERE video_id=6").fetchone()[0]
                == n
            )
            sa._apply_migration_006(c)
            sa._apply_migration_007(c)
            c.commit()
            r2 = c.execute(
                "SELECT results_run_scoped, results_scope_explicit "
                "FROM processing_runs WHERE id=?",
                (run2,),
            ).fetchone()
            assert int(r2[0]) == 1 and int(r2[1]) == 1
        assert sa.detection_summary_for_video(6)["detection_records"] == 0

    def test_missing_optional_child_tables_safe(self, tmp_path):
        from database import sqlite_adapter as sa

        db_path = tmp_path / "sparse.db"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE videos (
              id INTEGER PRIMARY KEY, filename TEXT, filepath TEXT, status TEXT
            );
            CREATE TABLE processing_runs (
              id INTEGER PRIMARY KEY, video_id INTEGER, status TEXT,
              diagnostics_json TEXT, geometry_snapshot_json TEXT
            );
            INSERT INTO videos VALUES (1, 's.mp4', '/s', 'ready');
            INSERT INTO processing_runs (
              id, video_id, status, diagnostics_json, geometry_snapshot_json
            ) VALUES (1, 1, 'completed', '{}', '{}');
            """
        )
        conn.commit()
        sa._apply_migration_006(conn)
        sa._apply_migration_007(conn)
        conn.commit()
        run = conn.execute("SELECT * FROM processing_runs WHERE id=1").fetchone()
        assert int(run["results_run_scoped"] or 0) == 0
        assert int(run["results_scope_explicit"] or 0) == 0
        conn.close()
