"""Bounded temporal evidence for uploaded prerecorded CCTV.

Evidence window: 3s before confirmation + full episode + 3s after end.
Pre-roll lives in a JPEG ring; open episodes spool JPEGs to a run-specific
directory so long episodes stay RAM-bounded and abort cannot wipe other runs.
"""

from __future__ import annotations

import re
import json
import shutil
import subprocess
import uuid
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from config import EVIDENCE_FOLDER
from core.frame_annotate import annotate_frame

EVIDENCE_PRE_SEC = 3.0
EVIDENCE_POST_SEC = 3.0
DEFAULT_JPEG_QUALITY = 70
# Hard cap on the *pre-roll* ring only. Open episodes spool to disk.
DEFAULT_MAX_FRAMES = 450

_SUSTAINED_RULE_KEYS = {
    "Illegal Parking": ("illegal_parking_park",),
    "Illegal Terminal": ("illegal_terminal",),
    "Obstruction": ("obstruction", "blocking_pedestrian"),
    "Counterflow": ("counterflow",),
    "Truck-Ban Violation": ("truck_ban",),
    "No Side Mirror": ("no_side_mirror",),
    "Motorcycle Overloading": ("overloading",),
    "Substandard / Nut-Shell Helmet": ("substandard_helmet",),
}


def active_sustained_event_keys(rule_state: Any, timestamp_sec: float) -> set[tuple[str, int]]:
    """Return emitted events whose duration-based rule condition is true now.

    Instantaneous transition rules are intentionally absent: their trigger
    frame remains primary and later detections cannot compete for preference.
    """
    active: set[tuple[str, int]] = set()
    now = float(timestamp_sec)
    for violation_type, track_id in rule_state.fired:
        for rule_key in _SUSTAINED_RULE_KEYS.get(violation_type, ()):
            tracker = rule_state.persistence.get((rule_key, track_id))
            if tracker is not None and tracker.last_true_at is not None and abs(
                float(tracker.last_true_at) - now
            ) <= 1e-6:
                active.add((str(violation_type), int(track_id)))
                break
    return active


class TemporalEvidenceFinalizationError(RuntimeError):
    """Episode could not be written as durable temporal evidence."""


def select_preferred_evidence_frame(
    candidates: list[dict[str, Any]],
    *,
    track_id: int,
    episode_start_sec: float,
    episode_end_sec: float,
    confirmed_at_sec: float,
    identity_epoch: int | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """Select a same-track frame inside the established event window."""
    eligible: list[tuple[float, float, int, dict[str, Any]]] = []
    for candidate in candidates:
        try:
            if int(candidate.get("track_id")) != int(track_id):
                continue
            if identity_epoch is not None and _safe_track_id(
                candidate.get("track_identity_epoch")
            ) != int(identity_epoch):
                continue
            timestamp = float(candidate["timestamp_sec"])
            if not float(episode_start_sec) <= timestamp <= float(episode_end_sec):
                continue
            sharpness = max(0.0, float(candidate.get("sharpness", 0.0)))
            area = max(0.0, float(candidate.get("area_fraction", 0.0)))
            reliability = max(0.0, min(1.0, float(candidate.get("detection_confidence", 0.0))))
            visible = 1.0 if candidate.get("fully_visible") is True else 0.0
            sharpness_score = sharpness / (sharpness + 100.0)
            area_score = min(area / 0.20, 1.0)
            proximity = max(0.0, 1.0 - abs(timestamp - float(confirmed_at_sec)) / max(
                float(episode_end_sec) - float(episode_start_sec), 0.001
            ))
            score = (
                0.35 * sharpness_score + 0.20 * area_score + 0.20 * visible
                + 0.15 * reliability + 0.10 * proximity
            )
            frame_number = int(candidate["frame_number"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if not np.isfinite(score):
            continue
        eligible.append((score, abs(timestamp - float(confirmed_at_sec)), frame_number, candidate))
    if not eligible:
        return None, "Preferred frame unavailable: no matching track frame inside the established event window."
    eligible.sort(key=lambda item: (-item[0], item[1], item[2]))
    score, _distance, _number, selected = eligible[0]
    reason = (
        f"Selected within the established event window; sharpness, crop area, "
        f"full-frame visibility, detector reliability, and event-time proximity "
        f"contributed to the display score ({score:.3f})."
    )
    return selected, reason


def _browser_compatible_clip(
    source_path: Path, *, target_name: str = "evidence_clip_browser.mp4"
) -> Path | None:
    """Create an H.264 MP4 sidecar that browsers can play reliably.

    OpenCV's ``mp4v`` writer creates valid MPEG-4 Part 2 files, but several
    browsers cannot decode that codec in an HTML ``<video>`` element. Keep the
    OpenCV original and write a separate H.264 derivative when ffmpeg is
    available; callers can still retain the source clip if transcoding fails.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None

    target = source_path.with_name(target_name)
    temporary = source_path.with_name(f".{target.stem}_{uuid.uuid4().hex}.tmp.mp4")
    command = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source_path),
        "-map",
        "0:v:0",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "24",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-an",
        str(temporary),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0 or not temporary.is_file() or temporary.stat().st_size == 0:
            return None
        temporary.replace(target)
        return target
    except OSError:
        return None
    finally:
        temporary.unlink(missing_ok=True)


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")
    return cleaned or "evidence"


def _safe_track_id(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _event_identity_manifest(source_key: str, run_id: str, ep: "EvidenceEpisode") -> dict[str, Any]:
    """Build only identity fields that have explicit producer provenance."""
    video_match = re.fullmatch(r"video_(\d+)", str(source_key))
    video_id = int(video_match.group(1)) if video_match else None
    run_match = re.fullmatch(r"\d+", str(run_id))
    processing_run_id = int(run_match.group(0)) if run_match else None
    return {
        "version": 1,
        "video_id": video_id,
        "processing_run_id": processing_run_id,
        "review_id": ep.review_id,
        "track_id": int(ep.track_id),
        "violation_type": str(ep.violation_type),
        "track_identity_epoch": ep.track_identity_epoch,
    }


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
    """Inclusive clip start: pre-roll before confirmation, not before episode start.

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
    overlay_detections: list[dict[str, Any]] | None = field(default=None, repr=False)
    overlay_zones: dict[str, Any] | None = field(default=None, repr=False)
    overlay_scene: Any | None = field(default=None, repr=False)
    overlay_violation_ids: tuple[int, ...] = field(default=(), repr=False)
    preferred_event_keys: tuple[tuple[str, int], ...] = field(default=(), repr=False)


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
    annotated_clip_path: str | None = None
    sequence_dir: str | None = None
    finalized: bool = False
    review_id: int | None = None
    ending_at_sec: float | None = None
    # Lightweight spill index (no JPEG bytes). JPEG lives on disk under spool_dir.
    spill: list[BufferedFrame] = field(default_factory=list, repr=False)
    spilled_ids: set[int] = field(default_factory=set, repr=False)
    spool_dir: str | None = None
    run_id: str = "direct"
    track_identity_epoch: int | None = None


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
                    overlay_detections=bf.overlay_detections,
                    overlay_zones=bf.overlay_zones,
                    overlay_scene=bf.overlay_scene,
                    overlay_violation_ids=bf.overlay_violation_ids,
                    preferred_event_keys=bf.preferred_event_keys,
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
                    overlay_detections=bf.overlay_detections,
                    overlay_zones=bf.overlay_zones,
                    overlay_scene=bf.overlay_scene,
                    overlay_violation_ids=bf.overlay_violation_ids,
                    preferred_event_keys=bf.preferred_event_keys,
                )
            )
            ep.spilled_ids.add(bf.frame_number)

    def push(
        self,
        frame: np.ndarray,
        frame_number: int,
        timestamp_sec: float,
        *,
        finalize_due: bool = True,
    ) -> list[EvidenceEpisode]:
        """Append a frame and optionally finalize episodes whose post-roll ended."""
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

        return self.finalize_due(float(timestamp_sec)) if finalize_due else []

    def set_frame_annotations(
        self,
        frame_number: int,
        detections: list[dict[str, Any]],
        zones: dict[str, Any],
        scene: Any | None,
        violation_track_ids: set[int],
        preferred_event_keys: set[tuple[str, int]] | None = None,
    ) -> None:
        """Attach overlays to the ring/spool copies of one processed frame."""
        frame_number = int(frame_number)
        for frame in self._frames:
            if frame.frame_number == frame_number:
                self._set_overlay(frame, detections, zones, scene, violation_track_ids, preferred_event_keys)
        for episode in self._open_episodes.values():
            for frame in episode.spill:
                if frame.frame_number == frame_number:
                    self._set_overlay(frame, detections, zones, scene, violation_track_ids, preferred_event_keys)

    @staticmethod
    def _set_overlay(
        frame: BufferedFrame,
        detections: list[dict[str, Any]],
        zones: dict[str, Any],
        scene: Any | None,
        violation_track_ids: set[int],
        preferred_event_keys: set[tuple[str, int]] | None = None,
    ) -> None:
        frame.overlay_detections = detections
        frame.overlay_zones = zones
        frame.overlay_scene = scene
        frame.overlay_violation_ids = tuple(violation_track_ids)
        frame.preferred_event_keys = tuple(preferred_event_keys or ())

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
        track_identity_epoch: int | None = None,
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
            track_identity_epoch=(
                int(track_identity_epoch) if track_identity_epoch is not None else None
            ),
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
                overlay_detections=ref.overlay_detections,
                overlay_zones=ref.overlay_zones,
                overlay_scene=ref.overlay_scene,
                overlay_violation_ids=ref.overlay_violation_ids,
                preferred_event_keys=ref.preferred_event_keys,
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
        annotated_writer = None
        annotated_failed = False
        written = 0
        preferred_candidate: dict[str, Any] | None = None
        preferred_frame: np.ndarray | None = None
        preferred_reason: str | None = None
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            clip_path = out_dir / "evidence_clip.mp4"
            annotated_clip_path = out_dir / "evidence_clip_annotated.mp4"
            h = w = 0
            for i, bf in enumerate(self._iter_window_frames(ep, float(ts))):
                jpeg = bf.jpeg
                self._jpeg_peak_bytes = max(self._jpeg_peak_bytes, len(jpeg))
                path = out_dir / f"frame_{i:04d}_n{bf.frame_number:06d}.jpg"
                path.write_bytes(jpeg)
                written += 1
                img = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
                jpeg = b""
                bf.jpeg = b""
                if img is None:
                    continue
                if (
                    ep.track_identity_epoch is not None
                    and float(ep.episode_start_sec) <= bf.timestamp_sec <= float(ep.episode_end_sec)
                    and (ep.violation_type, int(ep.track_id)) in set(bf.preferred_event_keys or ())
                ):
                    detection = next((
                        det for det in (bf.overlay_detections or ())
                        if _safe_track_id(det.get("track_id")) == int(ep.track_id)
                    ), None)
                    if detection is not None:
                        try:
                            x = float(detection["bbox_x"])
                            y = float(detection["bbox_y"])
                            bw = float(detection["bbox_w"])
                            bh = float(detection["bbox_h"])
                            x1, y1 = max(0, int(x)), max(0, int(y))
                            x2, y2 = min(img.shape[1], int(x + bw)), min(img.shape[0], int(y + bh))
                            if bw > 0 and bh > 0 and x2 > x1 and y2 > y1:
                                crop = img[y1:y2, x1:x2]
                                sharpness = float(cv2.Laplacian(
                                    cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), cv2.CV_64F
                                ).var())
                                candidate = {
                                    "frame_number": int(bf.frame_number),
                                    "timestamp_sec": float(bf.timestamp_sec),
                                    "track_id": int(ep.track_id),
                                    "track_identity_epoch": _safe_track_id(
                                        detection.get("track_identity_epoch")
                                    ),
                                    "sharpness": sharpness,
                                    "area_fraction": (bw * bh) / float(img.shape[0] * img.shape[1]),
                                    "fully_visible": x >= 0 and y >= 0 and x + bw <= img.shape[1] and y + bh <= img.shape[0],
                                    "detection_confidence": float(detection.get("confidence") or 0.0),
                                }
                                selected, reason = select_preferred_evidence_frame(
                                    [candidate] + ([preferred_candidate] if preferred_candidate else []),
                                    track_id=ep.track_id,
                                    episode_start_sec=ep.episode_start_sec,
                                    episode_end_sec=ep.episode_end_sec,
                                    confirmed_at_sec=ep.confirmed_at_sec,
                                    identity_epoch=ep.track_identity_epoch,
                                )
                                if selected is candidate:
                                    preferred_candidate = candidate
                                    preferred_frame = img.copy()
                                    preferred_reason = reason
                        except (KeyError, TypeError, ValueError, cv2.error):
                            pass
                if not write_clip:
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
                try:
                    if annotated_failed:
                        continue
                    annotated = annotate_frame(
                        img,
                        bf.overlay_detections or [],
                        zones=bf.overlay_zones,
                        violation_track_ids=bf.overlay_violation_ids,
                        scene=bf.overlay_scene,
                    )
                    if annotated_writer is None:
                        annotated_writer = cv2.VideoWriter(
                            str(annotated_clip_path),
                            cv2.VideoWriter_fourcc(*"mp4v"),
                            float(self.fps) if self.fps > 0 else 1.0,
                            (w, h),
                        )
                        if not annotated_writer.isOpened():
                            annotated_writer.release()
                            annotated_writer = None
                            raise TemporalEvidenceFinalizationError(
                                "failed to open annotated evidence clip writer"
                            )
                    annotated_writer.write(annotated)
                except Exception:
                    annotated_failed = True
                    if annotated_writer is not None:
                        annotated_writer.release()
                        annotated_writer = None
                    annotated_clip_path.unlink(missing_ok=True)

            if writer is not None:
                writer.release()
                writer = None
            if annotated_writer is not None:
                annotated_writer.release()
                annotated_writer = None

            if written < 1:
                raise TemporalEvidenceFinalizationError(
                    "finalization wrote no evidence frames "
                    "(spool files missing or unreadable)"
                )
            if preferred_candidate is not None and preferred_frame is not None:
                preferred_path = out_dir / "preferred_scene.jpg"
                try:
                    if cv2.imwrite(str(preferred_path), preferred_frame) and preferred_path.is_file():
                        (out_dir / "preferred_scene.json").write_text(
                            json.dumps({
                                "frame_number": preferred_candidate["frame_number"],
                                "timestamp_sec": preferred_candidate["timestamp_sec"],
                                "track_identity_epoch": preferred_candidate["track_identity_epoch"],
                                "selection_reason": preferred_reason,
                                "score_components": {
                                    key: preferred_candidate[key]
                                    for key in ("sharpness", "area_fraction", "fully_visible", "detection_confidence")
                                },
                            }, indent=2),
                            encoding="utf-8",
                        )
                except (OSError, cv2.error):
                    preferred_path.unlink(missing_ok=True)
            if write_clip and not clip_path.exists():
                raise TemporalEvidenceFinalizationError(
                    "finalization did not produce an evidence clip"
                )

            browser_clip_path = _browser_compatible_clip(clip_path) if write_clip else None
            annotated_browser_clip_path = (
                _browser_compatible_clip(
                    annotated_clip_path,
                    target_name="evidence_clip_annotated_browser.mp4",
                )
                if write_clip and annotated_clip_path.is_file()
                else None
            )

            self._open_episodes.pop(key, None)
            selected_clip_path = browser_clip_path or clip_path
            if write_clip and selected_clip_path.exists():
                ep.clip_path = _project_relative(selected_clip_path)
            if write_clip and annotated_clip_path.is_file():
                ep.annotated_clip_path = _project_relative(
                    annotated_browser_clip_path or annotated_clip_path
                )
            ep.sequence_dir = _project_relative(out_dir)
            ep.finalized = True
            (out_dir / "event_identity.json").write_text(
                json.dumps(_event_identity_manifest(self.source_key, str(self.run_id), ep), sort_keys=True),
                encoding="utf-8",
            )
            marker = out_dir / ".finalized"
            marker.write_text("ok", encoding="utf-8")
            self._finalized.append(ep)
            self._cleanup_spool(ep)
            return ep
        except Exception:
            if writer is not None:
                writer.release()
            if annotated_writer is not None:
                annotated_writer.release()
            if out_dir.exists() and not (out_dir / ".finalized").exists():
                shutil.rmtree(out_dir, ignore_errors=True)
            ep.finalized = False
            ep.sequence_dir = None
            ep.clip_path = None
            ep.annotated_clip_path = None
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
    base = Path(__file__).resolve().parents[1]
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
