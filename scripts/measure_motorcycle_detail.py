"""Measure the motorcycle detail workflow against the unchanged full-frame path.

Local prototype measurement only. It reads an authorized local video, runs the
existing full-frame YOLOv8m + ByteTrack path, then the same frames with the
detail collector attached, and reports:

  * full-frame throughput (fps) and per-frame latency (median / p95)
  * the same for the hybrid path (collector attached, no extra inference)
  * crop-pass latency (only when a detail checkpoint is *designated*; otherwise
    reported as unmeasured because the scan gate fails closed)
  * peak GPU VRAM and process RAM
  * crops and bytes per track, candidates per hour of footage, scan failures

Nothing is trained, promoted, or uploaded, and no dataset is modified. Reviewer
time/outcomes and helmet/mirror precision/recall need a labeled development set
and are reported as unmeasured.

Usage:
    venv\\Scripts\\python.exe scripts\\measure_motorcycle_detail.py --json tmp\\measure.json
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2  # noqa: E402

UNMEASURED = "unmeasured"


def _median(values):
    return round(float(statistics.median(values)), 4) if values else None


def _p95(values):
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
    return round(float(ordered[index]), 4)


def _peak_vram_mb():
    try:
        import torch

        if torch.cuda.is_available():
            return round(torch.cuda.max_memory_allocated() / (1024 * 1024), 2)
    except Exception:
        return None


def _peak_rss_mb():
    try:
        import psutil  # type: ignore

        return round(psutil.Process().memory_info().rss / (1024 * 1024), 2)
    except Exception:
        return None


def _default_video() -> Path | None:
    raw = Path(__file__).resolve().parents[1] / "dataset" / "raw"
    if not raw.is_dir():
        return None
    clips = sorted(p for p in raw.glob("*.mp4") if p.is_file())
    return clips[0] if clips else None


def _iter_frames(path: Path, max_frames: int):
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open {path}")
    try:
        index = 0
        while index < max_frames:
            ok, frame = cap.read()
            if not ok:
                break
            yield index, index / 30.0, frame
            index += 1
    finally:
        cap.release()


def _measure_crops(candidates, checkpoint) -> dict:
    """Crop-pass latency, or an explicit fail-closed unmeasured result."""
    if not checkpoint.ok:
        return {
            "median_ms": UNMEASURED,
            "p95_ms": UNMEASURED,
            "crops_scanned": 0,
            "failures": 0,
            "gate_reason": checkpoint.reason,
            "note": "No designated detail checkpoint: the crop pass fails closed.",
        }
    from core.motorcycle_detail_scan import default_predictor_factory

    latencies = []
    failures = 0
    predict = default_predictor_factory(checkpoint, conf=0.25)
    for candidate in candidates:
        for row in json.loads(candidate.get("frames_json") or "[]"):
            image = cv2.imread(str(row.get("crop_path")))
            if image is None:
                failures += 1
                continue
            scale = float(row.get("scan_scale_x") or 1.0)
            if scale != 1.0:
                image = cv2.resize(
                    image, (int(image.shape[1] * scale), int(image.shape[0] * scale))
                )
            t0 = time.perf_counter()
            try:
                predict(image)
            except Exception:
                failures += 1
                continue
            latencies.append((time.perf_counter() - t0) * 1000.0)
    return {
        "median_ms": _median(latencies),
        "p95_ms": _p95(latencies),
        "crops_scanned": len(latencies),
        "failures": failures,
        "model": checkpoint.identity,
    }



def run_measurement(video: Path, max_frames: int) -> dict:
    from core.detector import Detector
    from core.motorcycle_detail import MotorcycleDetailCollector
    from core.motorcycle_detail_scan import load_detail_checkpoint

    import config
    import core.evidence

    evidence_dir = Path(tempfile.mkdtemp(prefix="tavidm_detail_measure_"))
    original_evidence = config.EVIDENCE_FOLDER
    config.EVIDENCE_FOLDER = str(evidence_dir)
    core.evidence.EVIDENCE_FOLDER = str(evidence_dir)
    try:
        # Phase A: unchanged full-frame path.
        detector = Detector()
        detector.load()
        full_latencies = []
        started = time.perf_counter()
        for _index, ts, frame in _iter_frames(video, max_frames):
            t0 = time.perf_counter()
            detector.track_frame(frame, conf=0.6, timestamp_sec=ts)
            full_latencies.append((time.perf_counter() - t0) * 1000.0)
        full_seconds = time.perf_counter() - started
        frames_processed = len(full_latencies)

        # Phase B: same frames with the detail collector attached.
        hybrid = Detector()
        hybrid.load()
        collector = MotorcycleDetailCollector(
            video_id=0, run_key="measure_run", frame_w=0, frame_h=0,
            processing_run_id=0, enabled=True,
        )
        hybrid_latencies = []
        for index, ts, frame in _iter_frames(video, max_frames):
            collector.frame_w = int(frame.shape[1])
            collector.frame_h = int(frame.shape[0])
            t0 = time.perf_counter()
            dets = hybrid.track_frame(frame, conf=0.6, timestamp_sec=ts)
            collector.observe(frame, dets, frame_number=index, timestamp_sec=ts)
            hybrid_latencies.append((time.perf_counter() - t0) * 1000.0)
        collector.wrap_up()
        summary = collector.summary()
        candidates = collector.candidates()
        bytes_total = sum(int(c.get("size_bytes") or 0) for c in candidates)
        crops = sum(len(json.loads(c.get("frames_json") or "[]")) for c in candidates)
        tracks = int(summary.get("occurrences_seen") or 0)
        hours = (frames_processed / 30.0) / 3600.0
        checkpoint = load_detail_checkpoint(
            os.environ.get("TAVIDM_MOTORCYCLE_DETAIL_WEIGHTS", "").strip() or None
        )
        crop_pass = _measure_crops(candidates, checkpoint)
        return {
            "video": str(video),
            "frames_processed": frames_processed,
            "full_frame": {
                "median_ms": _median(full_latencies),
                "p95_ms": _p95(full_latencies),
                "throughput_fps": round(frames_processed / full_seconds, 3) if full_seconds else None,
                "peak_vram_mb": _peak_vram_mb(),
                "peak_rss_mb": _peak_rss_mb(),
            },
            "hybrid": {
                "median_ms": _median(hybrid_latencies),
                "p95_ms": _p95(hybrid_latencies),
                "selection_overhead_ms_median": round(
                    (_median(hybrid_latencies) or 0) - (_median(full_latencies) or 0), 4
                ),
            },
            "candidates": {
                "occurrences_seen": tracks,
                "candidates_persisted": len(candidates),
                "crops_written": crops,
                "bytes_total": bytes_total,
                "crops_per_track": round(crops / tracks, 3) if tracks else None,
                "bytes_per_track": int(bytes_total / tracks) if tracks else None,
                "candidates_per_hour_of_footage": (
                    round(len(candidates) / hours, 3) if hours else None
                ),
                "hours_of_footage": round(hours, 5),
                "selector_stats": summary,
            },
            "crop_pass": crop_pass,
            "queue_wait": {
                "batch_limit": 4,
                "poll_sec": 5.0,
                "note": "Single-slot background scanner polls only while the GPU is idle.",
            },
            "scan_failures": crop_pass.get("failures", 0),
            "reviewer_time_and_outcomes": UNMEASURED,
            "helmet_mirror_and_association_precision_recall": UNMEASURED,
            "unmeasured_reason": (
                "Reviewer outcomes and attribute precision/recall require a labeled "
                "development evaluation set, which is not assembled yet."
            ),
        }
    finally:
        config.EVIDENCE_FOLDER = original_evidence
        core.evidence.EVIDENCE_FOLDER = original_evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", default=None)
    parser.add_argument("--max-frames", type=int, default=120)
    parser.add_argument("--json", default=None)
    args = parser.parse_args()

    video = Path(args.video) if args.video else _default_video()
    if video is None or not video.is_file():
        print(json.dumps({"error": "no authorized local video found"}))
        return 2
    result = run_measurement(video, args.max_frames)
    payload = json.dumps(result, indent=2)
    if args.json:
        Path(args.json).write_text(payload, encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

