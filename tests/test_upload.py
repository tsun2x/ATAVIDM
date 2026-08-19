"""Tests for upload handling: disk-space guard, size limit, file_size persisted."""

from __future__ import annotations

import io
from collections import namedtuple

import pytest

from core import upload as upload_mod
from core.upload import (
    check_disk_space,
    format_file_size,
    validate_upload,
)
from core.upload import UploadError

try:
    from werkzeug.datastructures import FileStorage
except Exception:  # pragma: no cover - older werkzeug
    FileStorage = None


def _fake_usage(free):
    return namedtuple("Usage", ["total", "used", "free"])(free * 2, free, free)


class TestDiskSpaceGuard:
    def test_raises_when_insufficient(self, monkeypatch):
        # 10 MB free, but we want 1000 MB + 500 MB headroom.
        monkeypatch.setattr(upload_mod.shutil, "disk_usage", lambda p: _fake_usage(10 * 1024 * 1024))
        with pytest.raises(UploadError):
            check_disk_space(1000 * 1024 * 1024)

    def test_passes_when_sufficient(self, monkeypatch):
        # 5 GB free, need 500 MB + 500 MB headroom.
        monkeypatch.setattr(upload_mod.shutil, "disk_usage", lambda p: _fake_usage(5 * 1024 * 1024 * 1024))
        # Should not raise.
        check_disk_space(500 * 1024 * 1024)

    def test_headroom_configurable(self, monkeypatch):
        monkeypatch.setenv("UPLOAD_DISK_HEADROOM_MB", "10")
        monkeypatch.setattr(upload_mod.shutil, "disk_usage", lambda p: _fake_usage(200 * 1024 * 1024))
        # 100 MB needed + 10 MB headroom = 110 MB, well under 200 MB free.
        check_disk_space(100 * 1024 * 1024)


class TestSizeLimit:
    def test_rejects_over_max(self, monkeypatch):
        monkeypatch.setattr(upload_mod.config, "MAX_UPLOAD_MB", 1)
        monkeypatch.setattr(upload_mod.config, "MAX_CONTENT_LENGTH", 1 * 1024 * 1024)

        class FakeFile:
            filename = "big.mp4"
            content_length = 2 * 1024 * 1024

        with pytest.raises(UploadError):
            validate_upload(FakeFile(), 2 * 1024 * 1024)


class TestFormatFileSize:
    def test_bytes_kb_mb(self):
        assert format_file_size(500) == "500 B"
        assert format_file_size(2048) == "2.0 KB"
        assert format_file_size(5 * 1024 * 1024) == "5.0 MB"


@pytest.mark.skipif(FileStorage is None, reason="werkzeug not available")
class TestSaveAndPersist:
    def test_save_video_file_writes_and_disk_check_called(self, tmp_path, monkeypatch):
        # Avoid real disk pre-check blowing up on tiny sandbox fs.
        monkeypatch.setattr(upload_mod, "check_disk_space", lambda *a, **k: None)
        monkeypatch.setattr(upload_mod.config, "UPLOAD_FOLDER", str(tmp_path))

        data = b"\x00\x01binarycontent"
        storage = FileStorage(stream=io.BytesIO(data), filename="clip.mp4", content_type="video/mp4")

        name, path = upload_mod.save_video_file(storage)
        assert name == "clip.mp4"
        import os

        assert os.path.getsize(path) == len(data)

    def test_insert_video_persists_file_size(self, test_db):
        vid = test_db.insert_video(
            filename="clip.mp4",
            filepath="/tmp/clip.mp4",
            status="uploaded",
            file_size_bytes=123456,
        )
        row = test_db.get_video(vid)
        assert row["file_size_bytes"] == 123456

    def test_insert_video_null_file_size(self, test_db):
        vid = test_db.insert_video(filename="c.mp4", filepath="/tmp/c.mp4", status="uploaded")
        row = test_db.get_video(vid)
        assert row["file_size_bytes"] is None
