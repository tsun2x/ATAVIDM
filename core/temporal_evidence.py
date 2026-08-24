"""Bounded temporal evidence for uploaded prerecorded CCTV.

Evidence window: 6s before confirmation + full episode + 3s after end.
Pre-roll lives in a JPEG ring; open episodes spool JPEGs to a run-specific
directory so long episodes stay RAM-bounded and abort cannot wipe other runs.
"""

from __future__ import annotations

import re
import shutil
import uuid
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from config import EVIDENCE_FOLDER

EVIDENCE_PRE_SEC = 6.0
EVIDENCE_POST_SEC = 3.0
DEFAULT_JPEG_QUALITY = 70
# Hard cap on the *pre-roll* ring only. Open episodes spool to disk.
DEFAULT_MAX_FRAMES = 450


class TemporalEvidenceFinalizationError(RuntimeError):
    """Episode could not be written as durable temporal evidence."""


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")
    return cleaned or "evidence"


def _safe_join(base: Path, *parts: str) -> Path:
    """Resolve a path under ``base``; reject traversal."""
    candidate = base.joinpath(*parts).resolve()
    base_resolved = base.resolve()
    if not str(candidate).startswith(str(base_resolved)):
        raise ValueError("Unsafe evidence path rejected (directory traversal).")
    return candidate


def clip_window_start(
    *,
    episode_start_sec: float,
    confirmed_at_sec: float,
    pre_sec: float,
) -> float:
    """Inclusive clip start: 6s before confirmation, not 6s before episode start.

    If ``episode_start_sec`` is already ``confirmed - pre``, using
    ``episode_start - pre`` would double the pre-roll. Take the earlier of
    episode start and confirmation-minus-pre-roll.
    """
    confirmed = float(confirmed_at_sec)
    start = float(episode_start_sec)
    pre = max(0.0, float(pre_sec))
    return min(start, max(0.0, confirmed - pre))


@dataclass
class BufferedFrame:
    frame_number: int
    timestamp_sec: float
    jpeg: bytes = field(default=b"", repr=False)
    path: str | None = None


@dataclass
class EvidenceEpisode:
    violation_type: str
    track_id: int
    confirmed_at_sec: float
    episode_start_sec: float
    episode_end_sec: float | None = None
    still_path: str | None = None
    vehicle_crop_path: str | None = None
    clip_path: str | None = None
    sequence_dir: str | None = None
    finalized: bool = False
    review_id: int | None = None
    ending_at_sec: float | None = None
    # Lightweight spill index (no JPEG bytes). JPEG lives on disk under spool_dir.
    spill: list[BufferedFrame] = field(default_factory=list, repr=False)
    spilled_ids: set[int] = field(default_factory=set, repr=False)
    spool_dir: str | None = None
    run_id: str = "direct"


@dataclass
class TemporalEvidenceBuffer:
    """Ring buffer + per-run disk spool for one processing run.

    ``fps`` is the *effective processed* frame rate (source_fps / frame_skip),
    including values below 1 FPS, so clip playback matches the sampled timeline.
    """

    source_key: str
    fps: float = 30.0
    pre_sec: float = EVIDENCE_PRE_SEC
    post_sec: float = EVIDENCE_POST_SEC
    jpeg_quality: int = DEFAULT_JPEG_QUALITY
    max_frames: int = DEFAULT_MAX_FRAMES
    run_id: str | int | None = None
    _frames: deque[BufferedFrame] = field(default_factory=deque)
    _open_episodes: dict[tuple[str, int], EvidenceEpisode] = field(default_factory=dict)
    _finalized: list[EvidenceEpisode] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._frames = deque(maxlen=self.max_frames)
        self.source_key = _slug(self.source_key)
        if self.run_id not in (None, ""):
            self.run_id = _slug(str(self.run_id))
        else:
            self.run_id = _slug("auto_" + uuid.uuid4().hex)
        self.fps = float(self.fps)
        self._jpeg_peak_bytes = 0
        if self.fps <= 0:
            self.fps = 1.0

    @property
    def root_dir(self) -> Path:
        return Path(EVIDENCE_FOLDER) / self.source_key / f"run_{self.run_id}"

    def _window_start(self, ep: EvidenceEpisode) -> float:
        return clip_window_start(
            episode_start_sec=ep.episode_start_sec,
            confirmed_at_sec=ep.confirmed_at_sec,
            pre_sec=self.pre_sec,
        )

    def _ensure_spool(self, ep: EvidenceEpisode) -> Path:
        if ep.spool_dir:
            path = Path(ep.spool_dir)
            path.mkdir(parents=True, exist_ok=True)
            return path
        stamp = int(ep.confirmed_at_sec * 1000)
        name = (
            f"spool_{_slug(ep.violation_type)}_t{ep.track_id:03d}_f{stamp}"
            + (f"_r{ep.review_id}" if ep.review_id is not None else "")
        )
        path = _safe_join(self.root_dir, name)
        path.mkdir(parents=True, exist_ok=True)
        ep.spool_dir = str(path)
        return path

    def _write_spill_frame(self, ep: EvidenceEpisode, bf: BufferedFrame) -> None:
        if bf.frame_number in ep.spilled_ids:
            return
        if bf.jpeg:
            spool = self._ensure_spool(ep)
            dest = spool / f"frame_{bf.frame_number:08d}.jpg"
            if not dest.exists():
                dest.write_bytes(bf.jpeg)
            ep.spill.append(
                BufferedFrame(
                    frame_number=bf.frame_number,
                    timestamp_sec=bf.timestamp_sec,
                    jpeg=b"",
                    path=str(dest),
                )
            )
            ep.spilled_ids.add(bf.frame_number)
        elif bf.path:
            ep.spill.append(
                BufferedFrame(
                    frame_number=bf.frame_number,
                    timestamp_sec=bf.timestamp_sec,
                    jpeg=b"",
                    path=bf.path,
                )
            )
            ep.spilled_ids.add(bf.frame_number)

    def push(self, frame: np.ndarray, frame_number: int, timestamp_sec: float) -> list[EvidenceEpisode]:
        """Append a frame; finalize any episodes whose post-roll just completed."""
        ok, encoded = cv2.imencode(
            ".jpg",
            frame,
            [int(cv2.IMWRITE_JPEG_QUALITY), int(self.jpeg_quality)],
        )
        if not ok:
            return []
        bf = BufferedFrame(
            frame_number=int(frame_number),
            timestamp_sec=float(timestamp_sec),
            jpeg=encoded.tobytes(),
        )
        self._frames.append(bf)

        for ep in self._open_episodes.values():
            self._maybe_spill(ep, bf)

        return self.finalize_due(float(timestamp_sec))

    def _maybe_spill(self, ep: EvidenceEpisode, bf: BufferedFrame) -> None:
        window_start = self._window_start(ep)
        if bf.timestamp_sec < window_start:
            return
        if ep.ending_at_sec is not None:
            if bf.timestamp_sec > float(ep.ending_at_sec) + self.post_sec:
                return
        if bf.frame_number in ep.spilled_ids:
            return
        self._write_spill_frame(ep, bf)

    def begin_episode(
        self,
        *,
        violation_type: str,
        track_id: int,
        confirmed_at_sec: float,
        episode_start_sec: float | None = None,
        still_path: str | None = None,
        vehicle_crop_path: str | None = None,
        review_id: int | None = None,
    ) -> EvidenceEpisode:
        key = (violation_type, int(track_id))
        prior = self._open_episodes.pop(key, None)
        if prior is not None and not prior.finalized:
            if prior.ending_at_sec is None:
                prior.episode_end_sec = float(confirmed_at_sec)
                prior.ending_at_sec = float(confirmed_at_sec)
            if prior.review_id is not None:
                park_key = (f"__ending__{prior.violation_type}", int(prior.review_id))
                self._open_episodes[park_key] = prior
            else:
                self._open_episodes[key] = prior
                self.finalize_episode(
                    violation_type, track_id, now_sec=float(confirmed_at_sec)
                )

        start = (
            float(episode_start_sec)
            if episode_start_sec is not None
            else float(confirmed_at_sec)
        )
        ep = EvidenceEpisode(
            violation_type=violation_type,
            track_id=int(track_id),
            confirmed_at_sec=float(confirmed_at_sec),
            episode_start_sec=start,
            still_path=still_path,
            vehicle_crop_path=vehicle_crop_path,
            review_id=review_id,
            run_id=str(self.run_id),
        )
        window_start = self._window_start(ep)
        for bf in self._frames:
            if window_start <= bf.timestamp_sec <= float(confirmed_at_sec):
                self._write_spill_frame(ep, bf)
        self._open_episodes[key] = ep
        return ep

    def end_episode(
        self,
        violation_type: str,
        track_id: int,
        episode_end_sec: float,
    ) -> EvidenceEpisode | None:
        """Mark episode end and start the 3s post-roll (does not finalize yet)."""
        key = (violation_type, int(track_id))
        ep = self._open_episodes.get(key)
        if ep is None:
            return None
        if ep.ending_at_sec is not None:
            return ep
        ep.episode_end_sec = float(episode_end_sec)
        ep.ending_at_sec = float(episode_end_sec)
        return ep

    def finalize_due(self, now_sec: float) -> list[EvidenceEpisode]:
        """Finalize episodes whose post-roll window has elapsed."""
        done: list[EvidenceEpisode] = []
        for key, ep in list(self._open_episodes.items()):
            if ep.ending_at_sec is None:
                continue
            if float(now_sec) >= float(ep.ending_at_sec) + self.post_sec:
                finalized = self.finalize_episode(
                    ep.violation_type,
                    ep.track_id,
                    now_sec=now_sec,
                    _key=key,
                )
                if finalized is not None:
                    done.append(finalized)
        return done

    def _iter_window_frames(self, ep: EvidenceEpisode, now_sec: float) -> Iterator[BufferedFrame]:
        """Yield one window frame at a time. Spill JPEGs are read from disk and
        not accumulated into a list of decoded/encoded byte buffers.
        """
        end = ep.episode_end_sec if ep.episode_end_sec is not None else now_sec
        window_start = self._window_start(ep)
        window_end = float(end) + self.post_sec
        if ep.spill:
            refs = sorted(ep.spill, key=lambda f: (f.timestamp_sec, f.frame_number))
        else:
            refs = sorted(self._frames, key=lambda f: (f.timestamp_sec, f.frame_number))
        seen: set[int] = set()
        for ref in refs:
            if ref.frame_number in seen:
                continue
            if not (window_start <= ref.timestamp_sec <= window_end):
                continue
            seen.add(ref.frame_number)
            jpeg = b""
            if ref.path:
                try:
                    jpeg = Path(ref.path).read_bytes()
                except OSError:
                    jpeg = b""
            if not jpeg:
                jpeg = ref.jpeg
            if not jpeg:
                continue
            yield BufferedFrame(
                frame_number=ref.frame_number,
                timestamp_sec=ref.timestamp_sec,
                jpeg=jpeg,
                path=ref.path,
            )

    def finalize_episode(
        self,
        violation_type: str,
        track_id: int,
        *,
        now_sec: float | None = None,
        write_clip: bool = True,
        _key: tuple[str, int] | None = None,
    ) -> EvidenceEpisode | None:
        key = _key if _key is not None else (violation_type, int(track_id))
        ep = self._open_episodes.get(key)
        if ep is None:
            return None
        if ep.finalized:
            self._open_episodes.pop(key, None)
            return ep
        ts = now_sec if now_sec is not None else (
            ep.episode_end_sec if ep.episode_end_sec is not None else ep.confirmed_at_sec
        )
        if ep.episode_end_sec is None:
            ep.episode_end_sec = float(ts)

        self._jpeg_peak_bytes = 0
        out_dir = _safe_join(
            self.root_dir,
            f"temporal_{_slug(ep.violation_type)}_t{ep.track_id:03d}_f{int(ep.confirmed_at_sec * 1000)}"
            + (f"_r{ep.review_id}" if ep.review_id is not None else ""),
        )
        writer = None
        written = 0
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            clip_path = out_dir / "evidence_clip.mp4"
            h = w = 0
            for i, bf in enumerate(self._iter_window_frames(ep, float(ts))):
                jpeg = bf.jpeg
                self._jpeg_peak_bytes = max(self._jpeg_peak_bytes, len(jpeg))
                path = out_dir / f"frame_{i:04d}_n{bf.frame_number:06d}.jpg"
                path.write_bytes(jpeg)
                written += 1
                if not write_clip:
                    bf.jpeg = b""
                    continue
                img = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
                jpeg = b""
                bf.jpeg = b""
                if img is None:
                    continue
                if writer is None:
                    h, w = img.shape[:2]
                    fps = float(self.fps) if self.fps > 0 else 1.0
                    writer = cv2.VideoWriter(
                        str(clip_path),
                        cv2.VideoWriter_fourcc(*"mp4v"),
                        fps,
                        (w, h),
                    )
                    if not writer.isOpened():
                        writer.release()
                        writer = None
                        raise TemporalEvidenceFinalizationError(
                            "failed to open evidence clip writer"
                        )
                elif img.shape[0] != h or img.shape[1] != w:
                    img = cv2.resize(img, (w, h))
                writer.write(img)

            if writer is not None:
                writer.release()
                writer = None

            if written < 1:
                raise TemporalEvidenceFinalizationError(
                    "finalization wrote no evidence frames "
                    "(spool files missing or unreadable)"
                )
            if write_clip and not clip_path.exists():
                raise TemporalEvidenceFinalizationError(
                    "finalization did not produce an evidence clip"
                )

            self._open_episodes.pop(key, None)
            if clip_path.exists():
                ep.clip_path = _project_relative(clip_path)
            ep.sequence_dir = _project_relative(out_dir)
            ep.finalized = True
            marker = out_dir / ".finalized"
            marker.write_text("ok", encoding="utf-8")
            self._finalized.append(ep)
            self._cleanup_spool(ep)
            return ep
        except Exception:
            if writer is not None:
                writer.release()
            if out_dir.exists() and not (out_dir / ".finalized").exists():
                shutil.rmtree(out_dir, ignore_errors=True)
            ep.finalized = False
            ep.sequence_dir = None
            ep.clip_path = None
            self._open_episodes[key] = ep
            raise

    def finalize_all(self, now_sec: float) -> list[EvidenceEpisode]:
        """End any open episodes and finalize immediately (end-of-video / failure path).

        At EOV there may be fewer than ``post_sec`` of future frames; finalize with
        whatever spool/ring content is available.
        """
        for key, ep in list(self._open_episodes.items()):
            if ep.ending_at_sec is None:
                ep.episode_end_sec = float(now_sec)
                ep.ending_at_sec = float(now_sec)
        keys = list(self._open_episodes.keys())
        out: list[EvidenceEpisode] = []
        for key in keys:
            ep = self._open_episodes.get(key)
            if ep is None:
                continue
            finalized = self.finalize_episode(
                ep.violation_type,
                ep.track_id,
                now_sec=now_sec,
                _key=key,
            )
            if finalized is not None:
                out.append(finalized)
        return out

    def cleanup_partial(self) -> None:
        """Remove unfinished temporal dirs for this run only."""
        self.abort()

    def abort(self) -> None:
        """Drop open episodes and wipe *unfinished* artifacts of this run only.

        Finalized clips (``.finalized`` marker) and other runs under the same
        source_key are preserved.
        """
        for ep in list(self._open_episodes.values()):
            self._cleanup_spool(ep)
        self._open_episodes.clear()
        root = self.root_dir
        if not root.exists():
            return
        for child in root.iterdir():
            if not child.is_dir():
                continue
            marker = child / ".finalized"
            if marker.exists():
                continue
            shutil.rmtree(child, ignore_errors=True)

    def _cleanup_spool(self, ep: EvidenceEpisode) -> None:
        if not ep.spool_dir:
            return
        spool = Path(ep.spool_dir)
        if spool.exists() and spool.is_dir():
            shutil.rmtree(spool, ignore_errors=True)
        ep.spool_dir = None


def _project_relative(path: Path) -> str:
    base = Path(EVIDENCE_FOLDER).resolve().parent.parent
    try:
        return str(path.resolve().relative_to(base)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def evidence_timing_dict(ep: EvidenceEpisode) -> dict[str, Any]:
    return {
        "pre_sec": EVIDENCE_PRE_SEC,
        "post_sec": EVIDENCE_POST_SEC,
        "confirmed_at_sec": ep.confirmed_at_sec,
        "episode_start_sec": ep.episode_start_sec,
        "episode_end_sec": ep.episode_end_sec,
        "still_path": ep.still_path,
        "vehicle_crop_path": ep.vehicle_crop_path,
        "clip_path": ep.clip_path,
        "sequence_dir": ep.sequence_dir,
        "review_id": ep.review_id,
        "run_id": ep.run_id,
        "clip_window_start_sec": clip_window_start(
            episode_start_sec=ep.episode_start_sec,
            confirmed_at_sec=ep.confirmed_at_sec,
            pre_sec=EVIDENCE_PRE_SEC,
        ),
    }
