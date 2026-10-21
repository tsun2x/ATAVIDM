"""Launch or resume the owner-approved TAVIDM V9 YOLOv8m pilot."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(r"D:\tavidm")
DATA = ROOT / "dataset" / "derived" / "tavidm_v9_merged_provisional_20260924" / "data.yaml"
RUNS = ROOT / "dataset" / "training_runs"
RUN_NAME = "tavidm_v9_pilot_01_yolov8m"
INITIAL = ROOT / "yolov8m.pt"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    run = RUNS / RUN_NAME
    if args.resume:
        checkpoint = run / "weights" / "last.pt"
        if not checkpoint.is_file():
            print(f"ERROR: Resume checkpoint not found: {checkpoint}", file=sys.stderr)
            return 2
        payload = {"mode": "resume", "checkpoint": str(checkpoint), "run": str(run), "device": 0}
    else:
        if run.exists():
            print(f"ERROR: Refusing to overwrite existing run: {run}", file=sys.stderr)
            return 2
        for required in (DATA, INITIAL):
            if not required.is_file():
                print(f"ERROR: Required file not found: {required}", file=sys.stderr)
                return 2
        payload = {
            "mode": "fresh", "data": str(DATA), "checkpoint": str(INITIAL), "run": str(run),
            "epochs": 60, "patience": 15, "imgsz": 640, "batch": 6, "device": 0,
            "workers": 2, "optimizer": "AdamW", "lr0": 0.0002,
        }
    if args.preflight_only:
        print(json.dumps(payload, indent=2))
        return 0

    import torch
    from ultralytics import YOLO

    if not torch.cuda.is_available():
        print("ERROR: CUDA is unavailable; refusing CPU training", file=sys.stderr)
        return 2
    if args.resume:
        YOLO(checkpoint).train(resume=True, device=0)
        return 0
    YOLO(INITIAL).train(
        data=str(DATA), epochs=60, patience=15, imgsz=640, batch=6, device=0,
        workers=2, cache=False, optimizer="AdamW", lr0=0.0002, lrf=0.05,
        cos_lr=True, amp=True, seed=42, deterministic=True, save=True,
        save_period=5, close_mosaic=10, plots=True, project=str(RUNS),
        name=RUN_NAME, exist_ok=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
