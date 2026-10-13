"""Correction pass 2: run-scoped zero-result authority + atomic delete/audit."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import app as app_module
from core.detection_config import VIOLATION_ILLEGAL_PARKING
from core.upload_analytics import build_upload_processing_analytics
from core.video_lifecycle import (
    VideoLifecycleError,
    delete_video_permanently,
    invalidate_processing_results_for_annotation_change,
    remove_processing_results,
)


@pytest.fixture(autouse=True)
def _reset_processing_state():
    app_module.stop_processing_worker()
    app_module._process_queue.clear()
    app_module._processing_jobs.clear()
    app_module._running_video_id = None
    yield
    app_module.stop_processing_worker()
    app_module._process_queue.clear()
    app_module._processing_jobs.clear()
    app_module._running_video_id = None


def _legacy_detection(db, video_id: int, *, class_label: str = "car") -> int:
    return db.insert_detection(
        video_id=video_id,
        frame_number=0,
        timestamp_sec=0.0,
        track_id=1,
        class_label=class_label,
        confidence=0.9,
        bbox_x=1,
        bbox_y=1,
        bbox_w=10,
        bbox_h=10,
        processing_run_id=None,
    )


def _ready_paths(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "UPLOAD_FOLDER", str(tmp_path))
    monkeypatch.setattr(config, "FRAMES_FOLDER", str(tmp_path / "frames"))
    monkeypatch.setattr(config, "EVIDENCE_FOLDER", str(tmp_path / "ev"))
    monkeypatch.setattr(config, "ANNOTATED_FOLDER", str(tmp_path / "ann"))
    (tmp_path / "frames").mkdir(exist_ok=True)
    (tmp_path / "ev").mkdir(exist_ok=True)
    (tmp_path / "ann").mkdir(exist_ok=True)


def _seed_video_with_source(db, tmp_path, name: str = "clip.mp4") -> tuple[int, Path]:
    path = tmp_path / name
    path.write_bytes(b"fake-video")
    vid = db.insert_video(filename=name, filepath=str(path), status="ready")
    return vid, path


class TestZeroResultRunScopedAuthority:
    def test_legacy_then_zero_result_run_is_empty(self, test_db):
        vid = test_db.insert_video(filename="z.mp4", filepath="/tmp/z.mp4", status="ready")
        _legacy_detection(test_db, vid)
        test_db.insert_review_queue(
            video_id=vid,
            track_id=1,
            violation_type=VIOLATION_ILLEGAL_PARKING,
            confidence=0.8,
            frame_number=1,
            processing_run_id=None,
        )

        run_id = test_db.create_processing_run(vid, "[]")
        run = test_db.get_processing_run(run_id)
        assert int(run["results_run_scoped"] or 0) == 1
        assert int(run.get("results_scope_explicit") or 0) == 1
        assert run.get("results_scope_origin") == "create"
        test_db.finish_processing_run(
            run_id,
            status="completed",
            progress={
                "detection_records": 0,
                "unique_tracks": 0,
                "class_counts": {},
                "violation_candidates": 0,
                "annotated_video_ready": False,
            },
        )

        summary = test_db.detection_summary_for_video(vid)
        assert summary["detection_records"] == 0
        assert summary["unique_tracks"] == 0
        assert summary["class_counts"] == {}
        assert summary["violation_candidates"] == 0
        assert summary["processing_run_id"] == run_id
        assert summary["result_scope"] == "run"

        assert test_db.get_detections_by_video(vid, current_only=True) == []

    def test_legacy_review_not_owned_by_zero_run(self, test_db):
        vid = test_db.insert_video(filename="r.mp4", filepath="/tmp/r.mp4", status="ready")
        _legacy_detection(test_db, vid)
        test_db.insert_review_queue(
            video_id=vid,
            track_id=9,
            violation_type=VIOLATION_ILLEGAL_PARKING,
            confidence=0.7,
            frame_number=2,
            processing_run_id=None,
        )
        run_id = test_db.create_processing_run(vid, "[]")
        test_db.finish_processing_run(
            run_id,
            status="completed",
            progress={"detection_records": 0, "violation_candidates": 0, "class_counts": {}},
        )
        summary = test_db.detection_summary_for_video(vid)
        assert summary["violation_candidates"] == 0
        assert summary["processing_run_id"] == run_id

    def test_legacy_only_still_shows_null_rows(self, test_db):
        vid = test_db.insert_video(filename="leg.mp4", filepath="/tmp/leg.mp4", status="ready")
        _legacy_detection(test_db, vid, class_label="truck")
        summary = test_db.detection_summary_for_video(vid)
        assert summary["detection_records"] == 1
        assert summary["class_counts"] == {"truck": 1}
        assert summary["processing_run_id"] is None
        assert summary["result_scope"] == "legacy"
        rows = test_db.get_detections_by_video(vid, current_only=True)
        assert len(rows) == 1
        assert rows[0]["processing_run_id"] is None

    def test_analytics_zero_run_class_counts_agree(self, test_db):
        vid = test_db.insert_video(filename="a.mp4", filepath="/tmp/a.mp4", status="ready")
        _legacy_detection(test_db, vid)
        run_id = test_db.create_processing_run(vid, "[]")
        test_db.finish_processing_run(
            run_id,
            status="completed",
            progress={"detection_records": 0, "violation_candidates": 0, "class_counts": {}},
        )
        stats = build_upload_processing_analytics("today")
        assert stats["unique_videos_uploaded"] == 1
        assert stats["total_processing_runs"] >= 1
        assert stats["detection_records"] == 0
        assert stats["class_counts"] == {}
        assert stats["violation_candidates"] == 0

    def test_migration_006_idempotent_preserves_rows(self, tmp_path):
        from database import sqlite_adapter as sa

        db_path = tmp_path / "m006.db"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE videos (
              id INTEGER PRIMARY KEY, filename TEXT, filepath TEXT, status TEXT,
              created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE detections (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              video_id INTEGER, frame_number INTEGER, timestamp_sec REAL,
              track_id INTEGER, class_label TEXT, confidence REAL,
              bbox_x REAL, bbox_y REAL, bbox_w REAL, bbox_h REAL,
              processing_run_id INTEGER
            );
            CREATE TABLE review_queue (
              id INTEGER PRIMARY KEY, video_id INTEGER, status TEXT, processing_run_id INTEGER
            );
            CREATE TABLE violations (
              id INTEGER PRIMARY KEY, video_id INTEGER, status TEXT, processing_run_id INTEGER
            );
            CREATE TABLE processing_runs (
              id INTEGER PRIMARY KEY, video_id INTEGER, status TEXT,
              queued_at DATETIME, stage TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO videos (filename, filepath, status) VALUES (?,?,?)",
            ("a.mp4", "/a", "ready"),
        )
        conn.execute(
            "INSERT INTO processing_runs (id, video_id, status, queued_at, stage) "
            "VALUES (1, 1, 'completed', '2026-08-25 01:00:00', 'completed')"
        )
        conn.execute(
            "INSERT INTO detections (video_id, frame_number, timestamp_sec, track_id, "
            "class_label, confidence, bbox_x, bbox_y, bbox_w, bbox_h, processing_run_id) "
            "VALUES (1,0,0,1,'car',0.9,0,0,1,1,NULL)"
        )
        conn.commit()
        sa._apply_migration_006(conn)
        conn.commit()
        cols = [r[1] for r in conn.execute("PRAGMA table_info(processing_runs)").fetchall()]
        assert "results_run_scoped" in cols
        scoped = conn.execute(
            "SELECT results_run_scoped FROM processing_runs WHERE id = 1"
        ).fetchone()[0]
        # Metadata alone must not mark the run scoped (legacy NULL rows stay current).
        assert int(scoped) == 0
        det = conn.execute("SELECT processing_run_id, class_label FROM detections").fetchone()
        assert det[0] is None and det[1] == "car"
        sa._apply_migration_006(conn)
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM detections").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM processing_runs").fetchone()[0] == 1
        conn.close()


class TestAttemptVsCompletedResult:
    def test_failed_second_run_keeps_annotated_replay(self, test_db, tmp_path, monkeypatch):
        _ready_paths(tmp_path, monkeypatch)
        vid, _path = _seed_video_with_source(test_db, tmp_path, "ann.mp4")
        run1 = test_db.create_processing_run(vid, "[]")
        art = tmp_path / "ann" / f"run_{run1}" / "annotated.webm"
        art.parent.mkdir(parents=True)
        art.write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 32)
        test_db.finish_processing_run(
            run1,
            status="completed",
            progress={
                "detection_records": 3,
                "class_counts": {"car": 3},
                "annotated_video_path": str(art),
                "annotated_video_ready": True,
            },
        )
        test_db.insert_detection(
            video_id=vid,
            frame_number=1,
            timestamp_sec=0.1,
            track_id=1,
            class_label="car",
            confidence=0.9,
            bbox_x=1,
            bbox_y=1,
            bbox_w=5,
            bbox_h=5,
            processing_run_id=run1,
        )

        run2 = test_db.create_processing_run(vid, "[]")
        test_db.finish_processing_run(run2, status="failed", error_message="boom")

        row = test_db.get_video(vid)
        ui = app_module._db_video_to_ui(row)
        assert ui["annotated_video_ready"] is True
        assert ui["annotated_video_url"] == f"/api/videos/{vid}/annotated/{run1}"
        assert ui["latest_run_id"] == run2
        assert ui["current_result_run_id"] == run1
        assert ui["latest_attempt_status"] == "failed"

        status = app_module._build_status_payload(vid, row)
        assert status["latest_attempt_status"] == "failed"
        assert status["error"] == "boom"
        assert status["run_id"] == run2
        assert status["current_result_run_id"] == run1
        assert status["annotated_video_ready"] is True
        assert status["annotated_video_url"] == f"/api/videos/{vid}/annotated/{run1}"
        assert status["detection_records"] == 3

        summary = test_db.detection_summary_for_video(vid)
        assert summary["processing_run_id"] == run1
        assert summary["detection_records"] == 1

    def test_queued_second_run_keeps_annotated_replay(self, test_db, tmp_path, monkeypatch):
        _ready_paths(tmp_path, monkeypatch)
        vid, _path = _seed_video_with_source(test_db, tmp_path, "q.mp4")
        run1 = test_db.create_processing_run(vid, "[]")
        art = tmp_path / "ann" / f"run_{run1}" / "annotated.webm"
        art.parent.mkdir(parents=True)
        art.write_bytes(b"webm")
        test_db.finish_processing_run(
            run1,
            status="completed",
            progress={
                "annotated_video_path": str(art),
                "annotated_video_ready": True,
                "detection_records": 1,
            },
        )
        run2 = test_db.create_processing_run(vid, "[]")
        assert test_db.get_processing_run(run2)["status"] == "queued"

        ui = app_module._db_video_to_ui(test_db.get_video(vid))
        assert ui["annotated_video_ready"] is True
        assert ui["annotated_video_url"].endswith(f"/{run1}")
        assert ui["latest_run_id"] == run2
        assert ui["current_result_run_id"] == run1

        status = app_module._build_status_payload(vid, test_db.get_video(vid))
        assert status["annotated_video_ready"] is True
        assert status["current_result_run_id"] == run1
        assert status["latest_attempt_status"] == "queued"


class TestAtomicDeleteWithAudit:
    @pytest.mark.parametrize(
        "owner_scope", ["same_video", "other_video", "missing_annotated"]
    )
    def test_remove_results_preserves_raw_sidecar_referenced_by_confirmed_review(
        self, owner_scope, test_db, tmp_path, monkeypatch
    ):
        """A confirmed review owns its raw scene sibling even if another row is removable."""
        _ready_paths(tmp_path, monkeypatch)
        vid, _path = _seed_video_with_source(test_db, tmp_path, "raw-protected.mp4")
        scene_dir = tmp_path / "ev" / f"video_{vid}" / "run_33"
        scene_dir.mkdir(parents=True)
        annotated = scene_dir / "counterflow_f000010_t007.jpg"
        raw = scene_dir / f"raw_{annotated.name}"
        if owner_scope != "missing_annotated":
            annotated.write_bytes(b"annotated-evidence")
        raw.write_bytes(b"raw-evidence")
        owner_video_id = vid
        if owner_scope == "other_video":
            owner_video_id, _ = _seed_video_with_source(test_db, tmp_path, "raw-owner.mp4")
        test_db.insert_review_queue(
            video_id=owner_video_id, track_id=7, violation_type="Counterflow", confidence=.8,
            frame_number=10, evidence_path=str(annotated), status="confirmed",
        )
        test_db.insert_review_queue(
            video_id=vid, track_id=8, violation_type="Counterflow", confidence=.8,
            frame_number=11, evidence_path=str(annotated), status="pending",
        )

        remove_processing_results(vid, is_busy=lambda _v: False)

        if owner_scope != "missing_annotated":
            assert annotated.read_bytes() == b"annotated-evidence"
        assert raw.read_bytes() == b"raw-evidence"

    def test_reference_read_failure_after_quarantine_restores_all_files(
        self, test_db, tmp_path, monkeypatch
    ):
        """A DB reference-query error must not strand a prior quarantine move."""
        from core import video_lifecycle as vl

        _ready_paths(tmp_path, monkeypatch)
        vid, source = _seed_video_with_source(test_db, tmp_path, "reference-failure.mp4")
        run = test_db.create_processing_run(vid, "[]")
        art = tmp_path / "ann" / f"run_{run}"
        art.mkdir(parents=True)
        annotated_video = art / "annotated.webm"
        annotated_video.write_bytes(b"preserve-me")
        scene = tmp_path / "ev" / f"video_{vid}_run_41" / "scene.jpg"
        scene.parent.mkdir(parents=True)
        scene.write_bytes(b"scene")
        test_db.insert_review_queue(
            video_id=vid, track_id=7, violation_type="Counterflow", confidence=.8,
            frame_number=10, evidence_path=str(scene), status="pending",
        )

        quarantine_moves = {"count": 0}
        real_quarantine = vl._quarantine_path

        def track_successful_move(path, token):
            result = real_quarantine(path, token)
            quarantine_moves["count"] += 1
            return result

        def fail_reference_read(_path, _video_id):
            if quarantine_moves["count"] > 0:
                raise sqlite3.OperationalError("injected reference query failure")
            return False

        monkeypatch.setattr(vl, "_quarantine_path", track_successful_move)
        monkeypatch.setattr(vl, "_evidence_referenced_elsewhere", fail_reference_read)
        with pytest.raises(VideoLifecycleError, match="reference"):
            delete_video_permanently(
                vid, is_busy=lambda _v: False, expected_filename="reference-failure.mp4"
            )

        assert quarantine_moves["count"] == 1
        assert test_db.get_video(vid) is not None
        assert source.is_file()
        assert annotated_video.is_file() and annotated_video.read_bytes() == b"preserve-me"
        assert scene.is_file()
        qroot = tmp_path / "ann" / "_quarantine"
        assert not qroot.exists() or list(qroot.iterdir()) == []

    @pytest.mark.parametrize("cleanup", [remove_processing_results, invalidate_processing_results_for_annotation_change])
    def test_recursive_detail_cleanup_keeps_shared_sequence_child(
        self, cleanup, test_db, tmp_path, monkeypatch
    ):
        _ready_paths(tmp_path, monkeypatch)
        vid, _path = _seed_video_with_source(test_db, tmp_path, "recursive-cleanup.mp4")
        other, _other_path = _seed_video_with_source(test_db, tmp_path, "recursive-shared.mp4")
        run_id = test_db.create_processing_run(vid, "[]")
        detail_dir = tmp_path / "ev" / "detail" / f"run_{run_id}"
        shared_sequence = detail_dir / "shared-sequence"
        shared_sequence.mkdir(parents=True)
        shared_child = shared_sequence / "frame.jpg"
        removable_child = detail_dir / "stale-crop.jpg"
        shared_child.write_bytes(b"shared")
        removable_child.write_bytes(b"stale")
        test_db.insert_review_queue(
            video_id=other, track_id=9, violation_type="Counterflow", confidence=.8,
            frame_number=3, evidence_sequence_dir=str(shared_sequence), status="pending",
        )

        cleanup(vid, is_busy=lambda _v: False)

        assert shared_child.is_file()
        assert not removable_child.exists()

    def test_audit_failure_restores_everything(self, test_db, tmp_path, monkeypatch):
        from core import video_lifecycle as vl

        _ready_paths(tmp_path, monkeypatch)
        vid, path = _seed_video_with_source(test_db, tmp_path, "auditfail.mp4")
        run = test_db.create_processing_run(vid, "[]")
        art = tmp_path / "ann" / f"run_{run}" / "annotated.webm"
        art.parent.mkdir(parents=True)
        art.write_bytes(b"keep")
        ev = tmp_path / "ev" / f"video_{vid}" / "shot.jpg"
        ev.parent.mkdir(parents=True)
        ev.write_bytes(b"ev")
        test_db.finish_processing_run(
            run,
            status="completed",
            progress={"annotated_video_path": str(art), "annotated_video_ready": True},
        )
        test_db.insert_detection(
            video_id=vid,
            frame_number=0,
            timestamp_sec=0.0,
            track_id=1,
            class_label="car",
            confidence=0.9,
            bbox_x=1,
            bbox_y=1,
            bbox_w=2,
            bbox_h=2,
            processing_run_id=run,
        )

        def boom(video_id, *, event_type, detail):
            raise RuntimeError("forced audit failure")

        monkeypatch.setattr(vl.db, "delete_video_cascade_with_audit", boom)
        with pytest.raises(VideoLifecycleError):
            delete_video_permanently(
                vid, is_busy=lambda _v: False, expected_filename="auditfail.mp4"
            )

        assert test_db.get_video(vid) is not None
        assert test_db.get_processing_run(run) is not None
        assert len(test_db.get_detections_by_video(vid)) == 1
        assert path.exists() and path.read_bytes() == b"fake-video"
        assert art.exists() and art.read_bytes() == b"keep"
        assert ev.exists()
        qroot = tmp_path / "ann" / "_quarantine"
        stranded = list(qroot.glob("*")) if qroot.exists() else []
        assert stranded == []

    def test_audit_failure_inside_transaction_rolls_back(self, test_db, tmp_path, monkeypatch):
        """Cascade + audit share one transaction; failure before commit rolls back."""
        from core import video_lifecycle as vl
        from database import sqlite_adapter as sa

        _ready_paths(tmp_path, monkeypatch)
        vid, path = _seed_video_with_source(test_db, tmp_path, "txfail.mp4")
        run = test_db.create_processing_run(vid, "[]")
        art = tmp_path / "ann" / f"run_{run}"
        art.mkdir(parents=True)
        (art / "annotated.webm").write_bytes(b"x")

        def fail_after_cascade(video_id, *, event_type, detail):
            with sa.db_session() as conn:
                sa._cascade_delete_video_rows(conn, video_id)
                raise RuntimeError("audit insert failed")

        monkeypatch.setattr(vl.db, "delete_video_cascade_with_audit", fail_after_cascade)
        with pytest.raises(VideoLifecycleError):
            delete_video_permanently(
                vid, is_busy=lambda _v: False, expected_filename="txfail.mp4"
            )
        assert test_db.get_video(vid) is not None
        assert test_db.list_processing_runs(vid)
        assert path.exists()
        assert (art / "annotated.webm").exists()
        qroot = tmp_path / "ann" / "_quarantine"
        assert not qroot.exists() or list(qroot.glob("*")) == []
        assert sa.list_system_audit_events(event_type="video_deleted") == []

    def test_first_quarantine_failure_leaves_db_untouched(self, test_db, tmp_path, monkeypatch):
        from core import video_lifecycle as vl

        _ready_paths(tmp_path, monkeypatch)
        vid, path = _seed_video_with_source(test_db, tmp_path, "firstfs.mp4")
        run = test_db.create_processing_run(vid, "[]")
        art = tmp_path / "ann" / f"run_{run}"
        art.mkdir(parents=True)
        (art / "annotated.webm").write_bytes(b"a")

        calls = {"n": 0}
        real_move = vl.shutil.move

        def fail_first(src, dst):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("first move failed")
            return real_move(src, dst)

        monkeypatch.setattr(vl.shutil, "move", fail_first)
        with pytest.raises(VideoLifecycleError):
            delete_video_permanently(
                vid, is_busy=lambda _v: False, expected_filename="firstfs.mp4"
            )
        assert test_db.get_video(vid) is not None
        assert path.exists()
        assert (art / "annotated.webm").exists()

    def test_later_quarantine_failure_restores_earlier_moves(
        self, test_db, tmp_path, monkeypatch
    ):
        from core import video_lifecycle as vl

        _ready_paths(tmp_path, monkeypatch)
        vid, path = _seed_video_with_source(test_db, tmp_path, "laterfs.mp4")
        run = test_db.create_processing_run(vid, "[]")
        art_dir = tmp_path / "ann" / f"run_{run}"
        art_dir.mkdir(parents=True)
        (art_dir / "annotated.webm").write_bytes(b"ann")
        ev_dir = tmp_path / "ev" / f"video_{vid}"
        ev_dir.mkdir(parents=True)
        (ev_dir / "frame.jpg").write_bytes(b"ev")

        real_move = vl.shutil.move
        moves = {"n": 0}

        def fail_second(src, dst):
            moves["n"] += 1
            if moves["n"] == 2:
                raise OSError("second move failed")
            return real_move(src, dst)

        monkeypatch.setattr(vl.shutil, "move", fail_second)
        with pytest.raises(VideoLifecycleError):
            delete_video_permanently(
                vid, is_busy=lambda _v: False, expected_filename="laterfs.mp4"
            )
        assert test_db.get_video(vid) is not None
        assert path.exists()
        assert (art_dir / "annotated.webm").exists()
        assert (ev_dir / "frame.jpg").exists()
        qroot = tmp_path / "ann" / "_quarantine"
        stranded = list(qroot.glob("*")) if qroot.exists() else []
        assert stranded == []

    def test_successful_delete_writes_audit_and_clears_quarantine(
        self, test_db, tmp_path, monkeypatch
    ):
        _ready_paths(tmp_path, monkeypatch)
        vid, path = _seed_video_with_source(test_db, tmp_path, "okdel.mp4")
        run = test_db.create_processing_run(vid, "[]")
        art = tmp_path / "ann" / f"run_{run}"
        art.mkdir(parents=True)
        (art / "annotated.webm").write_bytes(b"gone")

        result = delete_video_permanently(
            vid, is_busy=lambda _v: False, expected_filename="okdel.mp4"
        )
        assert result["success"] is True
        assert result.get("database_committed") is True
        assert test_db.get_video(vid) is None
        assert not path.exists()
        assert not art.exists()
        audits = test_db.list_system_audit_events(event_type="video_deleted")
        assert len(audits) == 1
        detail = json.loads(audits[0]["detail_json"])
        assert detail["video_id"] == vid
        assert detail["filename"] == "okdel.mp4"
        qroot = tmp_path / "ann" / "_quarantine"
        assert not qroot.exists() or list(qroot.glob("*")) == []

    def test_permanent_delete_quarantines_raw_scene_sidecar_with_snapshot(
        self, test_db, tmp_path, monkeypatch
    ):
        _ready_paths(tmp_path, monkeypatch)
        vid, _path = _seed_video_with_source(test_db, tmp_path, "raw-delete.mp4")
        directory = tmp_path / "ev" / f"video_{vid}_run_41"
        directory.mkdir(parents=True)
        annotated = directory / "counterflow_f000010_t007.jpg"
        raw = directory / f"raw_{annotated.name}"
        annotated.write_bytes(b"annotated")
        raw.write_bytes(b"raw")
        test_db.insert_review_queue(
            video_id=vid, track_id=7, violation_type="Counterflow", confidence=.8,
            frame_number=10, evidence_path=str(annotated), status="pending",
        )

        result = delete_video_permanently(
            vid, is_busy=lambda _v: False, expected_filename="raw-delete.mp4"
        )

        assert result["success"] is True
        assert not annotated.exists()
        assert not raw.exists()


    def test_remove_results_deletes_paired_scene_files_but_keeps_another_source(
        self, test_db, tmp_path, monkeypatch
    ):
        _ready_paths(tmp_path, monkeypatch)
        vid, _path = _seed_video_with_source(test_db, tmp_path, "remove-raw.mp4")
        other, _other_path = _seed_video_with_source(test_db, tmp_path, "other.mp4")
        event_dir = tmp_path / "ev" / f"video_{vid}_run_17"
        event_dir.mkdir(parents=True)
        annotated = event_dir / "illegal_parking_f000020_t003.jpg"
        raw = event_dir / f"raw_{annotated.name}"
        annotated.write_bytes(b"annotated")
        raw.write_bytes(b"raw")
        other_dir = tmp_path / "ev" / f"video_{other}_run_18"
        other_dir.mkdir(parents=True)
        other_file = other_dir / "keep.jpg"
        other_file.write_bytes(b"other")
        test_db.insert_review_queue(
            video_id=vid, track_id=3, violation_type="Illegal Parking", confidence=.7,
            frame_number=20, evidence_path=str(annotated), status="pending",
        )
        test_db.insert_review_queue(
            video_id=other, track_id=4, violation_type="Illegal Parking", confidence=.7,
            frame_number=20, evidence_path=str(other_file), status="pending",
        )

        remove_processing_results(vid, is_busy=lambda _v: False)

        assert not annotated.exists()
        assert not raw.exists()
        assert other_file.is_file()

    def test_remove_results_also_deletes_dismissed_artifacts(self, test_db, tmp_path, monkeypatch):
        _ready_paths(tmp_path, monkeypatch)
        vid, _path = _seed_video_with_source(test_db, tmp_path, "dismissed-artifact.mp4")
        event_dir = tmp_path / "ev" / f"video_{vid}_run_27"
        event_dir.mkdir(parents=True)
        scene = event_dir / "scene.jpg"
        raw = event_dir / "raw_scene.jpg"
        scene.write_bytes(b"annotated")
        raw.write_bytes(b"raw")
        test_db.insert_review_queue(
            video_id=vid, track_id=7, violation_type="Counterflow", confidence=.8,
            frame_number=10, evidence_path=str(scene), status="dismissed",
        )

        remove_processing_results(vid, is_busy=lambda _v: False)

        assert test_db.list_review_items_for_video(vid, status="dismissed") == []
        assert not scene.exists()
        assert not raw.exists()

    def test_annotation_invalidation_preserves_sidecar_shared_with_confirmed_case(
        self, test_db, tmp_path, monkeypatch
    ):
        _ready_paths(tmp_path, monkeypatch)
        vid, _path = _seed_video_with_source(test_db, tmp_path, "shared-raw.mp4")
        directory = tmp_path / "ev" / f"video_{vid}_run_19"
        directory.mkdir(parents=True)
        annotated = directory / "obstruction_f000030_t005.jpg"
        raw = directory / f"raw_{annotated.name}"
        annotated.write_bytes(b"annotated")
        raw.write_bytes(b"raw")
        test_db.insert_review_queue(
            video_id=vid, track_id=5, violation_type="Obstruction", confidence=.7,
            frame_number=30, evidence_path=str(annotated), status="pending",
        )
        test_db.insert_violation(
            video_id=vid, track_id=5, violation_type="Obstruction", confidence=.7,
            frame_number=30, timestamp_sec=3.0, evidence_path=str(annotated), status="confirmed",
        )

        invalidate_processing_results_for_annotation_change(vid, is_busy=lambda _v: False)

        assert annotated.is_file()
        assert raw.is_file()

    def test_annotation_invalidation_preserves_retained_sequence_parent_of_pending_file(
        self, test_db, tmp_path, monkeypatch
    ):
        _ready_paths(tmp_path, monkeypatch)
        vid, _path = _seed_video_with_source(test_db, tmp_path, "retained-sequence.mp4")
        sequence = tmp_path / "ev" / f"video_{vid}_run_21" / "sequence"
        sequence.mkdir(parents=True)
        retained_frame = sequence / "frame-1.jpg"
        pending_frame = sequence / "frame-2.jpg"
        retained_frame.write_bytes(b"confirmed")
        pending_frame.write_bytes(b"pending")
        test_db.insert_review_queue(
            video_id=vid, track_id=7, violation_type="Counterflow", confidence=.8,
            frame_number=1, evidence_sequence_dir=str(sequence), status="confirmed",
        )
        test_db.insert_review_queue(
            video_id=vid, track_id=8, violation_type="Counterflow", confidence=.8,
            frame_number=2, evidence_path=str(pending_frame), status="pending",
        )

        invalidate_processing_results_for_annotation_change(vid, is_busy=lambda _v: False)

        assert retained_frame.is_file()
        assert pending_frame.is_file()

    def test_remove_results_preserves_retained_sequence_parent_of_pending_file(
        self, test_db, tmp_path, monkeypatch
    ):
        _ready_paths(tmp_path, monkeypatch)
        vid, _path = _seed_video_with_source(test_db, tmp_path, "remove-retained-sequence.mp4")
        sequence = tmp_path / "ev" / f"video_{vid}_run_23" / "sequence"
        sequence.mkdir(parents=True)
        retained_frame = sequence / "frame-1.jpg"
        pending_frame = sequence / "frame-2.jpg"
        retained_frame.write_bytes(b"confirmed")
        pending_frame.write_bytes(b"pending")
        test_db.insert_review_queue(
            video_id=vid, track_id=7, violation_type="Counterflow", confidence=.8,
            frame_number=1, evidence_sequence_dir=str(sequence), status="confirmed",
        )
        test_db.insert_review_queue(
            video_id=vid, track_id=8, violation_type="Counterflow", confidence=.8,
            frame_number=2, evidence_path=str(pending_frame), status="pending",
        )

        remove_processing_results(vid, is_busy=lambda _v: False)

        assert retained_frame.is_file()
        assert pending_frame.is_file()

    def test_annotation_invalidation_preserves_cross_video_shared_parent_of_file(
        self, test_db, tmp_path, monkeypatch
    ):
        _ready_paths(tmp_path, monkeypatch)
        vid, _path = _seed_video_with_source(test_db, tmp_path, "shared-child.mp4")
        other, _other_path = _seed_video_with_source(test_db, tmp_path, "shared-parent.mp4")
        shared_dir = tmp_path / "ev" / "shared-sequence"
        shared_dir.mkdir(parents=True)
        child = shared_dir / "frame.jpg"
        child.write_bytes(b"shared")
        test_db.insert_review_queue(
            video_id=vid, track_id=8, violation_type="Counterflow", confidence=.8,
            frame_number=2, evidence_path=str(child), status="pending",
        )
        test_db.insert_review_queue(
            video_id=other, track_id=9, violation_type="Counterflow", confidence=.8,
            frame_number=3, evidence_sequence_dir=str(shared_dir), status="pending",
        )

        invalidate_processing_results_for_annotation_change(vid, is_busy=lambda _v: False)

        assert child.is_file()

    def test_remove_results_cleans_raw_sidecar_when_annotated_file_is_missing(
        self, test_db, tmp_path, monkeypatch
    ):
        _ready_paths(tmp_path, monkeypatch)
        vid, _path = _seed_video_with_source(test_db, tmp_path, "missing-annotated.mp4")
        directory = tmp_path / "ev" / f"video_{vid}_run_22"
        directory.mkdir(parents=True)
        annotated = directory / "scene.jpg"
        raw = directory / "raw_scene.jpg"
        raw.write_bytes(b"raw")
        test_db.insert_review_queue(
            video_id=vid, track_id=8, violation_type="Counterflow", confidence=.8,
            frame_number=2, evidence_path=str(annotated), status="pending",
        )

        remove_processing_results(vid, is_busy=lambda _v: False)

        assert not raw.exists()

    def test_purge_failure_after_commit_reports_cleanup_warning(
        self, test_db, tmp_path, monkeypatch
    ):
        from core import video_lifecycle as vl

        _ready_paths(tmp_path, monkeypatch)
        vid, path = _seed_video_with_source(test_db, tmp_path, "purge.mp4")
        other, other_path = _seed_video_with_source(test_db, tmp_path, "keep.mp4")

        real_unlink = Path.unlink
        real_rmtree = vl.shutil.rmtree

        def flaky_unlink(self, *a, **k):
            if "_quarantine" in str(self):
                raise OSError("purge blocked")
            return real_unlink(self, *a, **k)

        def flaky_rmtree(target, *a, **k):
            if "_quarantine" in str(target):
                raise OSError("purge blocked")
            return real_rmtree(target, *a, **k)

        monkeypatch.setattr(Path, "unlink", flaky_unlink)
        monkeypatch.setattr(vl.shutil, "rmtree", flaky_rmtree)

        result = delete_video_permanently(
            vid, is_busy=lambda _v: False, expected_filename="purge.mp4"
        )
        assert result["success"] is True
        assert result["database_committed"] is True
        assert "cleanup_warning" in result
        assert result.get("quarantine_token")
        assert test_db.get_video(vid) is None
        assert not path.exists()
        assert test_db.get_video(other) is not None
        assert other_path.exists()
        audits = test_db.list_system_audit_events(event_type="video_deleted")
        assert len(audits) == 1
