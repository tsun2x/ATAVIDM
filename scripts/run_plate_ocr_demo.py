"""Process-scoped launcher for the experimental local plate OCR defense demo.

This script is the ONLY supported way to enable plate OCR. It never touches the
canonical database, the canonical evidence root, the user's global environment,
or any application setting. Everything it changes is scoped to the child
process it starts.

Usage (Windows, PowerShell)::

    python scripts/run_plate_ocr_demo.py --config artifacts/plate_alpr/plate_ocr_demo.json

Disable / stop::

    * Press Ctrl+C in the demo console, or close the window: the child process
      exits and no further plate jobs can run.
    * The feature is *off* again simply by not running this launcher. The
      repository default and every normal `python app.py` start has plate OCR
      disabled.
    * Verify no further jobs run: see ``docs/plate_ocr/README.md``
      ("Verify the demo is stopped").

The launcher refuses to start when:

* the config is missing, malformed, or does not explicitly enable the feature;
* any artifact is missing, unreadable, or hash-mismatched;
* the requested execution provider is not available (no automatic fallback);
* ``onnxruntime`` is not importable;
* the isolated output root overlaps any input artifact or the repository.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="explicit plate OCR demo config JSON")
    parser.add_argument(
        "--db",
        default=None,
        help="demo database path (defaults to output/plate_demo/tavidm_plate_demo.db)",
    )
    parser.add_argument(
        "--evidence-root",
        default=None,
        help="private demo evidence root (defaults to output/plate_demo/evidence)",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="validate the configuration and gate, then exit without starting the app",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:  # noqa: C901 - a linear launcher
    args = parse_args(argv)
    config_path = Path(args.config).resolve()
    isolated_root = Path(tempfile.gettempdir()) / f"tavidm_plate_demo_{uuid.uuid4().hex}"
    db_path = Path(args.db).resolve() if args.db else isolated_root / "tavidm_plate_demo.db"
    evidence_root = Path(args.evidence_root).resolve() if args.evidence_root else isolated_root / "evidence"
    if args.db or args.evidence_root:
        if not (args.db and args.evidence_root):
            print("BLOCKED: supply both --db and --evidence-root together", file=sys.stderr)
            return 2
        if db_path.parent != evidence_root.parent:
            print("BLOCKED: --db and --evidence-root must share one isolated parent", file=sys.stderr)
            return 2
        isolated_root = db_path.parent
    isolated_root = isolated_root.resolve()
    repo_root = REPO_ROOT.resolve()
    if isolated_root == repo_root or isolated_root in repo_root.parents or repo_root in isolated_root.parents:
        print("BLOCKED: isolated paths may not overlap the repository", file=sys.stderr)
        return 2
    if db_path == evidence_root or db_path in evidence_root.parents or evidence_root in db_path.parents:
        print("BLOCKED: database and evidence paths overlap", file=sys.stderr)
        return 2
    db_path = db_path.resolve()
    evidence_root = evidence_root.resolve()
    recovery_allowed = not isolated_root.exists()
    upload = evidence_root.parent / "raw"
    frames = evidence_root.parent / "frames"
    annotated = evidence_root.parent / "annotated"
    reports = evidence_root.parent / "reports"

    if not config_path.is_file():
        print(f"BLOCKED: demo config not found: {config_path}", file=sys.stderr)
        return 2

    sys.path.insert(0, str(REPO_ROOT))

    from core.plate_settings import (
        PlateOcrConfigError,
        load_plate_ocr_settings,
        verify_artifact_hashes,
    )

    try:
        settings = load_plate_ocr_settings(str(config_path))
    except PlateOcrConfigError as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2

    if not settings.is_enabled:
        print(f"BLOCKED: config does not enable the experiment ({settings.disabled_reason})", file=sys.stderr)
        return 2

    protected_paths = [*settings.artifact_paths(), settings.config_path, settings.evaluation_record_path]
    for protected_text in filter(None, protected_paths):
        protected = Path(protected_text).resolve()
        if protected == isolated_root or isolated_root in protected.parents or protected in isolated_root.parents:
            print("BLOCKED: isolated output overlaps configuration or model artifacts", file=sys.stderr)
            return 2

    artifacts = verify_artifact_hashes(settings)
    bad = [row for row in artifacts if not row["ok"]]
    if bad:
        print("BLOCKED: artifact verification failed:", file=sys.stderr)
        for row in bad:
            print(f"  {row['path']}: {row.get('reason')}", file=sys.stderr)
        return 2

    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        print(
            "BLOCKED: onnxruntime is not importable in this interpreter. Install the "
            "pinned wheel (onnxruntime==1.30.0) before starting the demo.",
            file=sys.stderr,
        )
        return 2

    child_env = dict(os.environ)
    child_env.update(
        {
            "TAVIDM_PLATE_OCR_CONFIG": str(config_path),
            "TAVIDM_PLATE_OCR_DEMO": "1",
            "TAVIDM_PLATE_OCR_EVALUATION": "",
            "TAVIDM_PLATE_OCR_ISOLATED_ROOT": str(isolated_root.resolve()),
            "TAVIDM_PLATE_OCR_RECOVERY_ALLOWED": "1" if recovery_allowed else "0",
            "SQLITE_PATH": str(db_path),
            "DATABASE_URL": str(db_path),
            "EVIDENCE_FOLDER": str(evidence_root),
            "TAVIDM_PLATE_OCR_EVIDENCE_ROOT": str(evidence_root / "plate_ocr"),
            "UPLOAD_FOLDER": str(upload),
            "FRAMES_FOLDER": str(frames),
            "ANNOTATED_FOLDER": str(annotated),
            "REPORTS_FOLDER": str(reports),
        }
    )

    print("Experimental local plate OCR demo")
    print(f"  config           : {config_path}")
    print(f"  provider         : {settings.provider}")
    print(f"  demo database    : {db_path}")
    print(f"  demo evidence    : {evidence_root}")
    print("  evaluation       : informational; supervised thesis demo")
    print("  Plate confirmation is restricted to an active System Administrator.")
    print("  Stop the demo with Ctrl+C; plate OCR is off in every other start path.")

    if args.check_only:
        return 0

    if isolated_root.exists() and any(isolated_root.iterdir()):
        print("BLOCKED: isolated demo root already contains user data", file=sys.stderr)
        return 2

    for folder in (db_path.parent, evidence_root, upload, frames, annotated, reports):
        folder.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        "-c",
        (
            "import sys, json, os; sys.path.insert(0, r'%s')\n"
            "from core.plate_runtime import _authorized_mode\n"
            "mode, error = _authorized_mode()\n"
            "if mode != 'demo' or error: raise RuntimeError('plate isolation rejected: ' + str(error))\n"
            "if os.environ.get('TAVIDM_PLATE_OCR_RECOVERY_ALLOWED') == '1':\n"
            "    from core.plate_manifest import sweep_partial_writes\n"
            "    print('recovery sweep:', json.dumps(sweep_partial_writes(), sort_keys=True))\n"
            "import app\n"
            "app.app.run(host=%r, port=%d, debug=False, use_reloader=False)"
        )
        % (str(REPO_ROOT), args.host, args.port),
    ]
    try:
        completed = subprocess.run(command, cwd=str(REPO_ROOT), env=child_env, check=False)
        return int(completed.returncode)
    except KeyboardInterrupt:  # pragma: no cover - interactive
        return 130
    finally:
        # Belt and braces: the child already exited, but make the intent explicit.
        try:
            from core.plate_runtime import shutdown_runtime

            shutdown_runtime()
        except Exception:  # pragma: no cover - best effort
            pass


if __name__ == "__main__":
    raise SystemExit(main())