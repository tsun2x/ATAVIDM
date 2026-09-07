"""Select attributable, deduplicated still frames from one local CCTV video."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np


class SelectionError(RuntimeError):
    """Raised when frame selection cannot preserve its safety contract."""


SAFE_GROUP = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _signature(frame: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, (16, 16), interpolation=cv2.INTER_AREA)


def select(video: Path, source_group: str, output: Path, every_frames: int, near_duplicate_distance: float) -> dict:
    video = video.resolve()
    output = output.resolve()
    if output.exists():
        raise SelectionError(f"refusing to overwrite existing output: {output}")
    if not video.is_file():
        raise SelectionError(f"source video not found: {video}")
    if not SAFE_GROUP.fullmatch(source_group):
        raise SelectionError("source-group must be 1-80 safe filename characters")
    if every_frames < 1:
        raise SelectionError("every-frames must be positive")
    if not 0 <= near_duplicate_distance <= 255:
        raise SelectionError("near-duplicate-distance must be between 0 and 255")

    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise SelectionError(f"video could not be opened: {video}")
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if not np.isfinite(fps) or fps <= 0 or width <= 0 or height <= 0:
        capture.release()
        raise SelectionError("video metadata is invalid")

    temporary = output.with_name(output.name + ".building")
    if temporary.exists():
        capture.release()
        raise SelectionError(f"temporary output already exists: {temporary}")
    images = temporary / "images"
    images.mkdir(parents=True)
    sampled = duplicates = 0
    signatures: list[np.ndarray] = []
    frame_rows = []
    frame_index = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if frame_index % every_frames:
                frame_index += 1
                continue
            sampled += 1
            signature = _signature(frame)
            if any(float(np.mean(np.abs(signature.astype(np.int16) - prior.astype(np.int16)))) <= near_duplicate_distance for prior in signatures):
                duplicates += 1
                frame_index += 1
                continue
            timestamp_ms = round(frame_index / fps * 1000)
            name = f"{source_group}__frame_{frame_index:08d}__ms_{timestamp_ms:012d}.jpg"
            path = images / name
            if not cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, 95]):
                raise SelectionError(f"failed to write selected frame: {path}")
            signatures.append(signature)
            frame_rows.append({
                "frame_index": frame_index,
                "timestamp_ms": timestamp_ms,
                "relative_path": f"images/{name}",
                "sha256": _sha256(path),
            })
            frame_index += 1
        if not frame_rows:
            raise SelectionError("selection produced no usable frames")
        manifest = {
            "schema_version": "1.0.0",
            "status": "selected_frames_pending_annotation",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_video": str(video),
            "source_video_sha256": _sha256(video),
            "source_group": source_group,
            "fps": fps,
            "reported_total_frames": total_frames,
            "width": width,
            "height": height,
            "every_frames": every_frames,
            "near_duplicate_distance": near_duplicate_distance,
            "sampled_frames": sampled,
            "selected_frames": len(frame_rows),
            "duplicates_rejected": duplicates,
            "frames": frame_rows,
            "annotation_status": "not_started",
            "training_authorized": False,
        }
        (temporary / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.rename(output)
        return manifest
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    finally:
        capture.release()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--source-group", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--every-frames", type=int, default=30)
    parser.add_argument("--near-duplicate-distance", type=float, default=2.0)
    args = parser.parse_args()
    try:
        manifest = select(args.video, args.source_group, args.output, args.every_frames, args.near_duplicate_distance)
    except (SelectionError, OSError, cv2.error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({key: manifest[key] for key in ("source_group", "sampled_frames", "selected_frames", "duplicates_rejected")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
