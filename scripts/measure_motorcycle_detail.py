"""Compare explicit local full-frame and motorcycle-detail measurements.

Metadata-only video inspection is the default. Running inference requires
``--run-inference --video PATH --main-weights PATH`` and is bounded by
``--max-frames``. The optional detail checkpoint is explicit. No training,
promotion, upload, database access, or dataset modification is performed.
"""

from __future__ import annotations

import argparse
import gc
import json
import math
import shutil
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


def _peak_vram_stats():
    try:
        import torch

        if torch.cuda.is_available():
            return {
                "peak_allocated_mb": round(torch.cuda.max_memory_allocated() / (1024 * 1024), 2),
                "peak_reserved_mb": round(torch.cuda.max_memory_reserved() / (1024 * 1024), 2),
            }
    except Exception:
        pass
    return {"peak_allocated_mb": None, "peak_reserved_mb": None}


def _cuda_sync():
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.synchronize()
    except Exception:
        pass


def _reset_peak_memory():
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
    except Exception:
        pass


def _peak_rss_mb():
    try:
        import psutil  # type: ignore

        return round(psutil.Process().memory_info().rss / (1024 * 1024), 2)
    except Exception:
        return None


def _iter_frames(path: Path, max_frames: int):
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open {path}")
    try:
        reported_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        fps = reported_fps if math.isfinite(reported_fps) and reported_fps > 0 else None
        index = 0
        while index < max_frames:
            ok, frame = cap.read()
            if not ok:
                break
            reported_ms = float(cap.get(cv2.CAP_PROP_POS_MSEC) or 0.0)
            if reported_ms > 0 and math.isfinite(reported_ms):
                timestamp, source = reported_ms / 1000.0, "container"
            elif fps is not None:
                timestamp, source = index / fps, "fps_derived"
            else:
                timestamp, source = None, "unknown"
            yield index, timestamp, source, frame
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
    warm_image = None
    for candidate in candidates:
        for row in json.loads(candidate.get("frames_json") or "[]"):
            warm_image = cv2.imread(str(row.get("crop_path")))
            if warm_image is not None:
                break
        if warm_image is not None:
            break
    if warm_image is not None:
        try:
            predict(warm_image)
        except Exception:
            pass
    _reset_peak_memory()
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
            _cuda_sync()
            t0 = time.perf_counter()
            try:
                predict(image)
            except Exception:
                failures += 1
                continue
            _cuda_sync()
            latencies.append((time.perf_counter() - t0) * 1000.0)
    return {
        "median_ms": _median(latencies),
        "p95_ms": _p95(latencies),
        "warmup_samples": 1 if warm_image is not None else 0,
        "crops_scanned": len(latencies),
        "failures": failures,
        "model": checkpoint.identity,
        "gpu_memory": _peak_vram_stats(),
    }



def run_measurement(
    video: Path,
    max_frames: int,
    main_weights: Path,
    detail_weights: Path | None = None,
) -> dict:
    from core.detector import Detector
    from core.motorcycle_detail import MotorcycleDetailCollector
    from core.motorcycle_detail_scan import load_detail_checkpoint

    import config
    import core.evidence

    if not video.is_file():
        raise ValueError("The explicitly selected local video does not exist.")
    if not main_weights.is_file():
        raise ValueError("An explicit existing main-detector checkpoint is required.")
    if detail_weights is not None and not detail_weights.is_file():
        raise ValueError("The explicit detail checkpoint does not exist.")
    if not 1 <= max_frames <= 3000:
        raise ValueError("max_frames must be between 1 and 3000.")
    evidence_dir = Path(tempfile.mkdtemp(prefix="tavidm_detail_measure_"))
    original_evidence = config.EVIDENCE_FOLDER
    config.EVIDENCE_FOLDER = str(evidence_dir)
    core.evidence.EVIDENCE_FOLDER = str(evidence_dir)
    try:
        # Phase A: unchanged full-frame path.
        first = next(iter(_iter_frames(video, 1)), None)
        if first is None:
            raise RuntimeError("Video contains no decodable frames.")
        _first_index, first_ts, _first_ts_source, first_frame = first
        if first_ts is None:
            raise RuntimeError("Source timing is unknown; measurement requires container timestamps or a valid FPS.")
        detector = Detector(weights=main_weights)
        detector.load()
        detector._model.predict(source=first_frame, conf=0.6, verbose=False)
        _reset_peak_memory()
        full_latencies = []
        started = time.perf_counter()
        full_timestamps = []
        timestamp_sources = set()
        for _index, ts, ts_source, frame in _iter_frames(video, max_frames):
            _cuda_sync()
            t0 = time.perf_counter()
            detector.track_frame(frame, conf=0.6, timestamp_sec=ts)
            _cuda_sync()
            full_latencies.append((time.perf_counter() - t0) * 1000.0)
            if ts is not None:
                full_timestamps.append(float(ts))
            timestamp_sources.add(ts_source)
        full_seconds = time.perf_counter() - started
        frames_processed = len(full_latencies)
        full_gpu_memory = _peak_vram_stats()

        del detector
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

        # Phase B: same frames with the detail collector attached.
        hybrid = Detector(weights=main_weights)
        hybrid.load()
        collector = MotorcycleDetailCollector(
            video_id=0, run_key="measure_run", frame_w=0, frame_h=0,
            processing_run_id=0, enabled=True,
        )
        hybrid_latencies = []
        _warm_index, warm_ts, _warm_source, warm_frame = first
        hybrid._model.predict(source=warm_frame, conf=0.6, verbose=False)
        _reset_peak_memory()
        for index, ts, _ts_source, frame in _iter_frames(video, max_frames):
            collector.frame_w = int(frame.shape[1])
            collector.frame_h = int(frame.shape[0])
            _cuda_sync()
            t0 = time.perf_counter()
            dets = hybrid.track_frame(frame, conf=0.6, timestamp_sec=ts)
            collector.observe(frame, dets, frame_number=index, timestamp_sec=ts)
            _cuda_sync()
            hybrid_latencies.append((time.perf_counter() - t0) * 1000.0)
        collector.wrap_up()
        hybrid_gpu_memory = _peak_vram_stats()
        summary = collector.summary()
        candidates = collector.candidates()
        bytes_total = sum(int(c.get("size_bytes") or 0) for c in candidates)
        crops = sum(len(json.loads(c.get("frames_json") or "[]")) for c in candidates)
        tracks = int(summary.get("occurrences_seen") or 0)
        duration_sec = (
            max(0.0, full_timestamps[-1] - full_timestamps[0])
            if len(full_timestamps) > 1
            else None
        )
        if duration_sec is None:
            try:
                cap = cv2.VideoCapture(str(video))
                fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
                cap.release()
                if math.isfinite(fps) and fps > 0:
                    duration_sec = frames_processed / fps
                    timestamp_sources.add("fps_derived_duration")
            except Exception:
                duration_sec = None
        hours = duration_sec / 3600.0 if duration_sec and duration_sec > 0 else 0.0
        checkpoint = load_detail_checkpoint(str(detail_weights) if detail_weights else "")
        del hybrid
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
        crop_pass = _measure_crops(candidates, checkpoint)
        return {
            "main_model_weights": str(main_weights.resolve()),
            "detail_model_weights": str(detail_weights.resolve()) if detail_weights else None,
            "video": str(video),
            "frames_processed": frames_processed,
            "full_frame": {
                "median_ms": _median(full_latencies),
                "p95_ms": _p95(full_latencies),
                "throughput_fps": round(frames_processed / full_seconds, 3) if full_seconds else None,
                "gpu_memory": full_gpu_memory,
                "peak_rss_mb": _peak_rss_mb(),
            },
            "hybrid": {
                "median_ms": _median(hybrid_latencies),
                "p95_ms": _p95(hybrid_latencies),
                "gpu_memory": hybrid_gpu_memory,
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
                "hours_of_footage": round(hours, 5) if hours else None,
                "measured_duration_sec": round(duration_sec, 4) if duration_sec is not None else None,
                "timestamp_sources": sorted(timestamp_sources),
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
        shutil.rmtree(evidence_dir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--main-weights", type=Path, help="Explicit local YOLOv8m/ByteTrack checkpoint")
    parser.add_argument("--detail-weights", type=Path, help="Optional explicit local detail checkpoint")
    parser.add_argument("--run-inference", action="store_true", help="Run explicitly requested local measurement")
    parser.add_argument("--max-frames", type=int, default=120)
    parser.add_argument("--json", default=None)
    args = parser.parse_args()

    video = Path(args.video)
    if not video.is_file():
        print(json.dumps({"error": "explicit local video path does not exist"}))
        return 2
    if not args.run_inference:
        stat = video.stat()
        capture = cv2.VideoCapture(str(video))
        try:
            fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
            frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        finally:
            capture.release()
        print(json.dumps({
            "mode": "metadata_only_dry_run", "video": str(video.resolve()),
            "size_bytes": stat.st_size, "modified_utc_ns": stat.st_mtime_ns,
            "width": width, "height": height, "reported_fps": fps if fps > 0 else None,
            "reported_frames": frames, "inference_performed": False,
        }, indent=2))
        return 0
    if not args.main_weights or not args.main_weights.is_file():
        parser.error("--run-inference requires an explicit existing --main-weights path")
    if args.detail_weights is not None and not args.detail_weights.is_file():
        parser.error("--detail-weights must identify an existing local checkpoint")
    result = run_measurement(video, args.max_frames, args.main_weights, args.detail_weights)
    payload = json.dumps(result, indent=2)
    if args.json:
        Path(args.json).write_text(payload, encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

