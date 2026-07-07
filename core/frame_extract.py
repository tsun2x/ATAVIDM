"""Extract reference frames from uploaded videos."""

from __future__ import annotations

from pathlib import Path

import config


class FrameExtractError(Exception):
    """Raised when a reference frame cannot be extracted."""


def frame_path_for_video(video_id: int) -> Path:
    return Path(config.FRAMES_FOLDER) / f"{video_id}.jpg"


def ensure_frames_dir() -> Path:
    frames_dir = Path(config.FRAMES_FOLDER)
    frames_dir.mkdir(parents=True, exist_ok=True)
    return frames_dir


def extract_first_frame(video_path: str, video_id: int) -> str:
    """Extract frame 0 and save as JPEG. Returns absolute filepath."""
    try:
        import cv2
    except ImportError as exc:
        raise FrameExtractError("OpenCV is required for frame extraction.") from exc

    src = Path(video_path)
    if not src.is_file():
        raise FrameExtractError(f"Video file not found: {video_path}")

    ensure_frames_dir()
    dest = frame_path_for_video(video_id)

    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        raise FrameExtractError("Unable to open video for frame extraction.")

    try:
        ok, frame = cap.read()
        if not ok or frame is None:
            raise FrameExtractError("Could not read the first frame from video.")
        if not cv2.imwrite(str(dest), frame):
            raise FrameExtractError("Failed to write reference frame image.")
    finally:
        cap.release()

    return str(dest.resolve())
