"""Launch or resume the approved Vehicle PH v5 pickup YOLOv8m run."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


DEFAULT_ROOT = Path(r"D:\yolo zip tavidm datasets\local_training")
DEFAULT_RUN_NAME = "vehicle_ph_v5_pickup_yolov8m"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--run-name", default=DEFAULT_RUN_NAME)
    parser.add_argument("--data", type=Path)
    parser.add_argument("--initial-checkpoint", type=Path)
    args = parser.parse_args()

    root = args.root.resolve()
    run = root / "runs" / args.run_name
    data = (args.data or root / "vehicle_ph_v5_pickup_clean" / "data.yaml").resolve()
    initial = (args.initial_checkpoint or root / "runs" / "vehicle_ph_v5_bmd_yolov8m" / "weights" / "best.pt").resolve()
    if args.resume:
        checkpoint = run / "weights" / "last.pt"
        if not checkpoint.is_file():
            print(f"ERROR: Resume checkpoint not found: {checkpoint}", file=sys.stderr)
            return 2
        payload = {"mode": "resume", "run": str(run), "checkpoint": str(checkpoint), "device": 0}
    else:
        if run.exists():
            print(f"ERROR: Refusing to overwrite existing run: {run}", file=sys.stderr)
            return 2
        if not data.is_file():
            print(f"ERROR: Dataset YAML not found: {data}", file=sys.stderr)
            return 2
        if not initial.is_file():
            print(f"ERROR: Initial checkpoint not found: {initial}", file=sys.stderr)
            return 2
        payload = {
            "mode": "fresh", "run": str(run), "data": str(data), "checkpoint": str(initial),
            "epochs": 60, "patience": 15, "imgsz": 640, "batch": 6, "device": 0,
            "workers": 2, "optimizer": "AdamW", "lr0": 0.0002,
        }
    if args.preflight_only:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    from ultralytics import YOLO

    if args.resume:
        YOLO(checkpoint).train(resume=True, device=0)
        return 0
    YOLO(initial).train(
        data=str(data), epochs=60, patience=15, imgsz=640, batch=6, device=0,
        workers=2, cache=False, optimizer="AdamW", lr0=0.0002, lrf=0.05,
        cos_lr=True, amp=True, seed=42, deterministic=True, save=True,
        save_period=5, close_mosaic=10, plots=True, project=str(root / "runs"),
        name=args.run_name, exist_ok=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
