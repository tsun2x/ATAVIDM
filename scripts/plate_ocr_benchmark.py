"""Latency and resource benchmark for the local plate OCR stages.

Runs standalone in the isolated plate environment (onnxruntime +
opencv-python-headless + numpy). Uses only synthetic images and the locally
qualified artifacts; there is no network access and no application import.

Reported percentiles are cold-start (first call, includes session/graph setup)
and warm (steady state). Confidence semantics are unchanged: the OCR head
returns raw logits.
"""

from __future__ import annotations

import json
import os
import platform
import statistics
import sys
import time
from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from core.plate_onnx_backend import PlateOnnxBackend, check_plate_quality, crop_from_box  # noqa: E402
from core.plate_settings import PlateOcrSettings, verify_artifact_hashes  # noqa: E402

ARTIFACTS = Path(
    os.environ.get("TAVIDM_PLATE_ARTIFACTS", str(REPO / "artifacts" / "plate_alpr"))
)
DETECTOR = ARTIFACTS / "weights" / "yolo-v9-t-384-license-plates-end2end.onnx"
OCR = ARTIFACTS / "weights" / "cct_s_v2_global.onnx"
OCR_CONFIG = ARTIFACTS / "config" / "cct_s_v2_global_plate_config.yaml"
CONFIG = ARTIFACTS / "plate_ocr_demo.json"


def rss_mib() -> float:
    """Current resident set size in MiB (Windows-native, no extra dependency)."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
            handle = ctypes.windll.kernel32.GetCurrentProcess()  # type: ignore[attr-defined]
            ok = ctypes.windll.psapi.GetProcessMemoryInfo(  # type: ignore[attr-defined]
                handle, ctypes.byref(counters), counters.cb
            )
            if ok:
                return counters.WorkingSetSize / (1024 * 1024)
        except Exception:
            pass
    try:
        import resource  # type: ignore

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return peak / (1024 * 1024) if sys.platform == "win32" else peak / 1024
    except Exception:
        return float("nan")


def pct(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


def synthetic_plate(text: str = "ABC 123", width: int = 240, height: int = 70) -> np.ndarray:
    plate = np.full((height, width, 3), 232, np.uint8)
    cv2.rectangle(plate, (2, 2), (width - 3, height - 3), (70, 70, 70), 3)
    cv2.putText(plate, text, (18, height - 20), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (15, 15, 15), 3)
    return plate


def realistic_vehicle_crop(width: int = 101, height: int = 99) -> np.ndarray:
    """A crop at the size the real evaluation actually produced."""
    rng = np.random.default_rng(1337)
    base = np.full((height, width, 3), 120, np.uint8)
    noise = rng.integers(0, 40, size=(height, width, 3), dtype=np.uint8)
    return np.clip(base.astype(np.int16) + noise, 0, 255).astype(np.uint8)


def main() -> int:
    from core.plate_settings import load_plate_ocr_settings

    settings = load_plate_ocr_settings(str(CONFIG))
    if not settings.is_enabled:
        print("ERROR: demo config did not load")
        return 2

    artifacts = verify_artifact_hashes(settings)
    if not all(row["ok"] for row in artifacts):
        print("ERROR: artifact verification failed", artifacts)
        return 2

    backend = PlateOnnxBackend(settings)

    # Synthetic 1080p scene with a large plate (upper bound on achievable quality).
    scene = np.zeros((1080, 1920, 3), np.uint8)
    scene[300:370, 700:940] = synthetic_plate("ABC 123")
    # Realistic 101x99 vehicle crop from the evaluation footage.
    small = realistic_vehicle_crop()

    report: dict = {
        "host": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
        },
        "artifacts": {row["path"].split("\\")[-1]: row["actual_sha256"] for row in artifacts},
        "provider_requested": settings.provider,
        "peak_rss_mib_after_sessions": None,
    }

    # --- cold detector call (includes session + graph setup) ---
    t0 = time.perf_counter()
    cold_boxes = backend.detect_plates(scene)
    cold_detect_ms = (time.perf_counter() - t0) * 1000.0
    report["cold_detector_ms_including_session_setup"] = round(cold_detect_ms, 2)
    report["cold_detector_boxes_found"] = len(cold_boxes)

    # --- cold OCR call ---
    if cold_boxes:
        box = cold_boxes[0]
        plate_crop, rect = crop_from_box(scene, (box.x1, box.y1, box.x2, box.y2))
    else:
        plate_crop, rect = synthetic_plate(), (0, 0, 240, 70)
    t0 = time.perf_counter()
    read = backend.recognize_plate(plate_crop)
    cold_ocr_ms = (time.perf_counter() - t0) * 1000.0
    report["cold_ocr_ms_including_session_setup"] = round(cold_ocr_ms, 2)
    report["cold_ocr_text"] = read.text
    report["cold_ocr_score_semantics"] = "raw uncalibrated per-slot max logit"
    report["peak_rss_mib_after_sessions"] = round(rss_mib(), 1)

    # --- warm detector latency (synthetic scene + realistic small crop) ---
    warm_detect: list[float] = []
    for _ in range(60):
        for image in (scene, small):
            t0 = time.perf_counter()
            backend.detect_plates(image)
            warm_detect.append((time.perf_counter() - t0) * 1000.0)

    # --- warm OCR latency ---
    warm_ocr: list[float] = []
    for _ in range(120):
        t0 = time.perf_counter()
        backend.recognize_plate(plate_crop)
        warm_ocr.append((time.perf_counter() - t0) * 1000.0)

    # --- end-to-end detect+quality+ocr on the realistic crop size ---
    e2e: list[float] = []
    quality_rejections = 0
    for _ in range(60):
        t0 = time.perf_counter()
        boxes = backend.detect_plates(small)
        if boxes:
            pc, rc = crop_from_box(small, (boxes[0].x1, boxes[0].y1, boxes[0].x2, boxes[0].y2))
            q = check_plate_quality(
                pc,
                settings=settings,
                requested_box=(boxes[0].x1, boxes[0].y1, boxes[0].x2, boxes[0].y2),
                effective_rect=rc,
            )
            if not q.accepted:
                quality_rejections += 1
            else:
                backend.recognize_plate(pc)
        e2e.append((time.perf_counter() - t0) * 1000.0)

    def stats(values: list[float]) -> dict:
        return {
            "n": len(values),
            "min_ms": round(min(values), 2),
            "p50_ms": round(pct(values, 0.50), 2),
            "p95_ms": round(pct(values, 0.95), 2),
            "p99_ms": round(pct(values, 0.99), 2),
            "max_ms": round(max(values), 2),
            "mean_ms": round(statistics.fmean(values), 2),
        }

    report["warm_detector_latency"] = stats(warm_detect)
    report["warm_ocr_latency"] = stats(warm_ocr)
    report["end_to_end_detect_quality_ocr_on_101x99_crop"] = stats(e2e)
    report["end_to_end_quality_rejections"] = quality_rejections
    report["end_to_end_note"] = (
        "101x99 is the actual vehicle-crop size the evaluation produced. Detector "
        "calls are reported with boxes found separately so a no-read is not confused "
        "with a slow call."
    )

    # --- detector score probe (raw, pre-threshold) --------------------
    # Reported honestly: the configured threshold is 0.25 (the upstream default)
    # and is frozen for this evaluation, so a lower raw score is a real no-read,
    # not something to tune away after the fact.
    def _raw_top_scores(image, n: int = 6):
        tensor, ratio, padding = _letterbox(image)
        raw = backend._detector.run(
            [backend._detector_output_name], {backend._detector_input_name: tensor}
        )[0]
        arr = np.asarray(raw, dtype=np.float32).reshape(-1, 7)
        scores = sorted((float(r[6]) for r in arr), reverse=True)
        return scores[:n], int(arr.shape[0])

    import core.plate_onnx_backend as backend_module

    _letterbox = backend_module.letterbox_detector_input
    report["detector_raw_top_scores"] = {
        "synthetic_1080p_with_240x70_plate": _raw_top_scores(scene),
        "realistic_101x99_crop": _raw_top_scores(small),
        "configured_detector_conf_threshold": settings.detector_conf_threshold,
        "note": (
            "Rows are [batch, x1, y1, x2, y2, class, score]. NMS is embedded in the "
            "exported graph, so these are post-NMS scores."
        ),
    }
    for _ in range(20):
        backend.detect_plates(small)
    report["detector_boxes_above_threshold_on_101x99"] = len(backend.detect_plates(small))
    report["detector_boxes_above_threshold_on_synthetic"] = len(backend.detect_plates(scene))
    report["peak_rss_mib_final"] = round(rss_mib(), 1)
    report["onnxruntime_version"] = __import__("onnxruntime").__version__
    report["opencv_version"] = cv2.__version__
    report["numpy_version"] = np.__version__

    out = Path(os.environ.get("TAVIDM_PLATE_BENCH_OUT", REPO / "output" / "plate_eval" / "latency.json"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())