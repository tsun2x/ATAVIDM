"""Bounded live-processing preview: latest annotated JPEG only (drop stale).

JPEG encoding is gated on active viewers and wall-clock display rate (~5 FPS).
Inference and annotated output continue regardless of preview subscribers.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Generator

from core.frame_annotate import encode_jpeg

# Display rate cap — inference is not throttled to this rate.
DEFAULT_PREVIEW_INTERVAL_SEC = 0.2  # ~5 FPS display


class ProcessingPreviewHub:
    """Per-video latest-frame publisher. Slow viewers never block inference."""

    def __init__(self, *, max_display_fps: float = 5.0) -> None:
        self._lock = threading.Lock()
        self._frames: dict[int, bytes] = {}
        self._generations: dict[int, int] = {}
        self._active_viewers: dict[int, int] = {}
        self._last_encode_mono: dict[int, float] = {}
        self._max_display_fps = max(0.1, float(max_display_fps))
        self._min_interval_sec = 1.0 / self._max_display_fps

    def publish(self, video_id: int, jpeg_bytes: bytes) -> None:
        if not jpeg_bytes:
            return
        with self._lock:
            self._frames[int(video_id)] = jpeg_bytes
            self._generations[int(video_id)] = self._generations.get(int(video_id), 0) + 1

    def should_encode(self, video_id: int, *, now: float | None = None) -> bool:
        """True when at least one viewer is active and display-rate budget allows."""
        vid = int(video_id)
        ts = time.monotonic() if now is None else float(now)
        with self._lock:
            if self._active_viewers.get(vid, 0) <= 0:
                return False
            last = self._last_encode_mono.get(vid)
            if last is not None and (ts - last) < self._min_interval_sec:
                return False
            return True

    def publish_frame(self, video_id: int, frame: Any, *, quality: int = 75) -> bool:
        """Encode and publish only when viewers are present and rate allows.

        Returns True if a JPEG was encoded. Multiple viewers share one encode.
        """
        vid = int(video_id)
        ts = time.monotonic()
        with self._lock:
            if self._active_viewers.get(vid, 0) <= 0:
                return False
            last = self._last_encode_mono.get(vid)
            if last is not None and (ts - last) < self._min_interval_sec:
                return False
            # Reserve the encode slot before leaving the lock so concurrent
            # callers do not duplicate JPEG work for the same frame window.
            self._last_encode_mono[vid] = ts

        data = encode_jpeg(frame, quality=quality)
        if not data:
            return False
        self.publish(vid, data)
        return True

    def latest(self, video_id: int) -> bytes | None:
        with self._lock:
            return self._frames.get(int(video_id))

    def clear(self, video_id: int) -> None:
        with self._lock:
            vid = int(video_id)
            self._frames.pop(vid, None)
            self._generations.pop(vid, None)
            self._last_encode_mono.pop(vid, None)

    def register_viewer(self, video_id: int) -> None:
        with self._lock:
            vid = int(video_id)
            self._active_viewers[vid] = self._active_viewers.get(vid, 0) + 1

    def unregister_viewer(self, video_id: int) -> None:
        with self._lock:
            vid = int(video_id)
            count = self._active_viewers.get(vid, 0) - 1
            if count <= 0:
                self._active_viewers.pop(vid, None)
                # Stop encoding promptly; drop last-encode so a reconnect starts fresh.
                self._last_encode_mono.pop(vid, None)
            else:
                self._active_viewers[vid] = count

    def viewer_count(self, video_id: int) -> int:
        with self._lock:
            return self._active_viewers.get(int(video_id), 0)

    def mjpeg_generator(
        self,
        video_id: int,
        *,
        is_active: Callable[[], bool],
        interval_sec: float = DEFAULT_PREVIEW_INTERVAL_SEC,
    ) -> Generator[bytes, None, None]:
        """Yield multipart JPEG parts. Drops frames by always reading latest."""
        self.register_viewer(video_id)
        last_gen = -1
        try:
            while is_active():
                with self._lock:
                    gen = self._generations.get(int(video_id), 0)
                    frame = self._frames.get(int(video_id))
                if frame is not None and gen != last_gen:
                    last_gen = gen
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                    )
                time.sleep(max(0.05, float(interval_sec)))
        finally:
            self.unregister_viewer(video_id)


# Process-wide hub (single Flask process / single worker architecture).
preview_hub = ProcessingPreviewHub()
