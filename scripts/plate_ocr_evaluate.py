"""Local evaluation harness for the experimental plate detection/OCR feature.

Explicit inputs only. No network, no telemetry, no model download, no upload.
Run it from the repository root with an interpreter that has the application
dependencies (the full TAVIDM pipeline) available::

    python scripts/plate_ocr_evaluate.py \
        --video dataset/raw/test_pick-up_cargo_person_1.mp4 \
        --config artifacts/plate_alpr/plate_ocr_demo.json \
        --db output/plate_eval/tavidm_plate_eval.db \
        --evidence-root output/plate_eval/evidence \
        --out output/plate_eval/evaluation.json

What it does, in this exact order:

1. Configures the temporary database and private evidence roots **before**
   importing the application or database modules.
2. Registers the clip as an uploaded video and runs the real TAVIDM pipeline
   (YOLOv8m + ByteTrack + the existing rule engine, unchanged) with the plate
   runtime explicitly enabled.
3. Freezes and writes the evaluation-unit list: one unit per
   (run, track_id, identity_epoch, plate episode) using a predeclared episode
   gap, and one predeclared scored candidate per unit.
4. Exports the *vehicle crop* for each unit to ``--out``/``crops`` so a human can
   label readability and exact text **without** seeing any OCR output.
5. With ``--labels`` supplied, scores the frozen units and writes the gate
   verdict.

The harness never relabels, never drops a readable no-read, and never tunes a
threshold on the scored set.
"""

from __future__ import annotations

import argparse
import json
import json as _json
import os
import shutil
import sys
import time
import tempfile
import uuid
import hashlib
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Predeclared before any scoring: two machine attempts for the same track
#: identity more than this many seconds apart belong to different plate
#: episodes. ByteTrack ids are reused, so the identity epoch is part of the key.
EPISODE_GAP_SEC = 5.0

#: Predeclared before any scoring: which manifest supplies the scored candidate
#: for an episode. The earliest attempt by (frame_number, attempt_id) wins; its
#: own declared primary candidate is the scored string. Ground truth is never
#: consulted, and no candidate is picked retrospectively from a list.
SCORED_MANIFEST_RULE = "earliest_attempt_by_frame_then_attempt_id"


def configure_environment(db_path: Path, evidence_root: Path, plate_config: Path, isolation_root: Path, *, create_roots: bool = True) -> None:
    """Point every writable root at temporary locations, before app imports."""
    os.environ["SQLITE_PATH"] = str(db_path)
    os.environ["DATABASE_URL"] = str(db_path)
    os.environ["EVIDENCE_FOLDER"] = str(evidence_root)
    os.environ["TAVIDM_PLATE_OCR_EVIDENCE_ROOT"] = str(evidence_root / "plate_ocr")
    os.environ["UPLOAD_FOLDER"] = str(evidence_root.parent / "raw")
    os.environ["FRAMES_FOLDER"] = str(evidence_root.parent / "frames")
    os.environ["ANNOTATED_FOLDER"] = str(evidence_root.parent / "annotated")
    os.environ["REPORTS_FOLDER"] = str(evidence_root.parent / "reports")
    os.environ["TAVIDM_PLATE_OCR_CONFIG"] = str(plate_config)
    os.environ["TAVIDM_PLATE_OCR_EVALUATION"] = "1"
    os.environ.pop("TAVIDM_PLATE_OCR_DEMO", None)
    os.environ["TAVIDM_PLATE_OCR_ISOLATED_ROOT"] = str(isolation_root.resolve())
    os.environ["TAVIDM_PLATE_ARTIFACTS"] = str(plate_config.parent)
    os.environ["TAVIDM_BOOTSTRAP_ADMIN_PASSWORD"] = "plate-eval-harness-only-pw"
    os.environ["TAVIDM_PLATE_EVAL_HARNESS"] = "1"
    if create_roots:
        for folder in (evidence_root.parent / "raw", evidence_root.parent / "frames", evidence_root.parent / "annotated", evidence_root.parent / "reports"):
            folder.mkdir(parents=True, exist_ok=True)


def block_network() -> None:
    """Hard-disable outbound sockets for the whole harness process."""
    import socket
    import urllib.request

    def blocked(*args, **kwargs):  # pragma: no cover - must never be reached
        raise RuntimeError("network access attempted during the plate evaluation harness")

    socket.socket = blocked  # type: ignore[assignment]
    socket.create_connection = blocked  # type: ignore[assignment]
    urllib.request.urlopen = blocked  # type: ignore[assignment]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", default=None, help="exact local simulation footage path")
    parser.add_argument("--config", default=None, help="explicit plate OCR config JSON")
    parser.add_argument("--db", default=None, help="isolated SQLite path (defaults under a unique output root)")
    parser.add_argument("--evidence-root", default=None, help="private evidence root (defaults under a unique output root)")
    parser.add_argument("--out", default=None, help="new evaluation output JSON (defaults under a unique output root)")
    parser.add_argument("--isolation-root", default=None, help="dedicated root containing all harness outputs")
    parser.add_argument("--frozen-units", default=None, help="immutable frozen evaluation package used by --score-only")
    parser.add_argument("--qualification-evidence", default=None, help="reviewed JSON verification, association, and licensing evidence")
    parser.add_argument(
        "--labels",
        default=None,
        help="human labels JSON: {unit_id: {readable: bool, text: str|null, note: str}}",
    )
    parser.add_argument(
        "--split",
        default="second_half",
        choices=["first_half", "second_half", "all"],
        help="which slice of the clip is the reported evaluation set",
    )
    parser.add_argument(
        "--reuse-db",
        action="store_true",
        help="reuse an existing harness database instead of recreating it",
    )
    parser.add_argument(
        "--zones",
        default=None,
        help=(
            "JSON scene annotation for the simulation clip. This is the operator's "
            "scene annotation, not a plate setting: the existing rule engine decides "
            "whether any violation is real. Nothing here fabricates a review row."
        ),
    )
    parser.add_argument(
        "--score-only",
        action="store_true",
        help=(
            "skip video processing and only re-freeze units and score them from the "
            "existing harness database and manifests"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:  # noqa: C901 - a linear harness
    args = parse_args(argv)

    video_path = Path(args.video).resolve() if args.video else None
    if not args.score_only and (video_path is None or not video_path.is_file()):
        print(f"ERROR: video not found: {video_path}", file=sys.stderr)
        return 2
    plate_config = Path(args.config).resolve() if args.config else None
    if plate_config is None or not plate_config.is_file():
        print(f"ERROR: plate config not found: {plate_config}", file=sys.stderr)
        return 2

    isolation_root = Path(args.isolation_root).resolve() if args.isolation_root else Path(tempfile.gettempdir()) / f"tavidm_plate_eval_{uuid.uuid4().hex}"
    db_path = Path(args.db).resolve() if args.db else isolation_root / "database.sqlite"
    evidence_root = Path(args.evidence_root).resolve() if args.evidence_root else isolation_root / "evidence"
    out_path = Path(args.out).resolve() if args.out else isolation_root / "evaluation.json"
    out_dir = out_path.parent
    repo_root = REPO_ROOT.resolve()
    if isolation_root == repo_root or isolation_root in repo_root.parents or repo_root in isolation_root.parents:
        print("ERROR: isolation root overlaps the repository", file=sys.stderr)
        return 2
    for candidate in (db_path, evidence_root, out_path):
        if candidate == isolation_root or isolation_root not in candidate.parents:
            print(f"ERROR: output is outside the isolated root: {candidate}", file=sys.stderr)
            return 2
        if video_path is not None and (candidate == video_path or candidate in video_path.parents or video_path in candidate.parents):
            print("ERROR: output overlaps source footage", file=sys.stderr)
            return 2
    if db_path == evidence_root or db_path in evidence_root.parents or evidence_root in db_path.parents:
        print("ERROR: database and evidence paths overlap", file=sys.stderr)
        return 2
    if not args.score_only and db_path.exists() and not args.reuse_db:
        print("ERROR: database already exists; refusing to reset or overwrite it", file=sys.stderr)
        return 2
    protected_inputs = [Path(value).resolve() for value in (args.config, args.labels, args.qualification_evidence, args.frozen_units) if value]
    for output in (db_path, evidence_root, out_path, isolation_root):
        for protected_input in protected_inputs:
            if output == protected_input or output in protected_input.parents or protected_input in output.parents:
                print(f"ERROR: output overlaps an input artifact: {protected_input}", file=sys.stderr)
                return 2
    if not args.score_only:
        sys.path.insert(0, str(REPO_ROOT))
        from core.plate_settings import load_plate_ocr_settings as _load_settings_preflight
        try:
            preflight_settings = _load_settings_preflight(str(plate_config))
        except Exception as exc:
            print(f"ERROR: invalid plate configuration: {type(exc).__name__}", file=sys.stderr)
            return 2
        protected = [*preflight_settings.artifact_paths(), preflight_settings.config_path, preflight_settings.evaluation_record_path]
        for path_text in filter(None, protected):
            path = Path(path_text).resolve()
            if path == isolation_root or path in isolation_root.parents or isolation_root in path.parents:
                print("ERROR: output root overlaps configuration or model artifacts", file=sys.stderr)
                return 2
    if out_path.exists():
        print("ERROR: output already exists; refusing to overwrite it", file=sys.stderr)
        return 2
    if args.score_only and not args.frozen_units:
        print("ERROR: --score-only requires --frozen-units and never rebuilds units", file=sys.stderr)
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)
    configure_environment(db_path, evidence_root, plate_config, isolation_root, create_roots=not args.score_only)
    block_network()

    sys.path.insert(0, str(REPO_ROOT))
    from database import db
    from core.plate_settings import load_plate_ocr_settings
    settings = load_plate_ocr_settings()
    if not args.score_only:
        from core.plate_runtime import get_runtime, shutdown_runtime
        from core.video_processor import process_video
        if not settings.is_enabled:
            print("ERROR: plate OCR is not enabled by the supplied config", file=sys.stderr)
            return 2
    runtime = None
    result = None
    report = {"worker": None}
    elapsed = 0.0
    video_id = None
    crops_dir = None
    frozen_sha = None
    if args.score_only:
        frozen_input = Path(args.frozen_units).resolve()
        if not frozen_input.is_file():
            print("ERROR: frozen unit package not found", file=sys.stderr)
            return 2
        frozen_payload = _json.loads(frozen_input.read_text(encoding="utf-8"))
        frozen_sha = frozen_payload.pop("frozen_package_sha256", None)
        frozen_bytes = _json.dumps(frozen_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if frozen_payload.get("schema_version") != "plate-ocr-frozen-units/1" or frozen_sha != hashlib.sha256(frozen_bytes).hexdigest():
            print("ERROR: frozen unit package hash/schema is invalid", file=sys.stderr)
            return 2
        units = frozen_payload.get("units")
        frozen = frozen_payload.get("frozen_units")
        if not isinstance(units, list) or not isinstance(frozen, dict):
            print("ERROR: frozen unit package is malformed", file=sys.stderr)
            return 2
    else:
        if db_path.exists() and not args.reuse_db:
            print("ERROR: refusing to initialize an existing database", file=sys.stderr)
            return 2
        if not args.reuse_db:
            db.init_db()
        video_id = db.insert_video(filename=video_path.name, filepath=str(video_path), duration_sec=0.0, status="ready")
        zones = {"no_parking": [], "active_lane": [], "pedestrian_crossing": [], "truck_ban_zone": [], "no_loading": [], "restricted_lane": []}
        if args.zones:
            zones = json.loads(Path(args.zones).read_text(encoding="utf-8"))
        db.upsert_annotation(video_id, _json.dumps(zones))
        runtime = get_runtime()
        if runtime is None:
            print("ERROR: isolated evaluation runtime unavailable", file=sys.stderr)
            return 2
        started = time.monotonic()
        try:
            result = process_video(video_id, processing_run_id=None)
            worker = runtime.worker
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline and worker._queue.unfinished_tasks:
                time.sleep(0.1)
            report = runtime.status()
            print("plate runtime status:", _json.dumps(report.get("worker", {}), sort_keys=True))
        finally:
            elapsed = time.monotonic() - started
            shutdown_runtime()
        units, frozen = freeze_units(db, video_id, split=args.split)
        crops_dir = out_dir / "crops"
        crops_dir.mkdir(parents=True, exist_ok=False)
        for unit in units:
            export_unit_crop(unit, crops_dir)

    payload: dict[str, Any] = {
        "harness": "plate_ocr_evaluate",
        "created_at_unix": time.time(),
        "video": str(video_path) if video_path is not None else frozen_payload.get("video"),
        "pipeline_seconds": round(elapsed, 2),
        "split": args.split,
        "episode_gap_sec": EPISODE_GAP_SEC,
        "scored_manifest_rule": SCORED_MANIFEST_RULE,
        "status_report": report,
        "units": units,
        "frozen_units": frozen,
        "frozen_package_sha256": frozen_sha,
        "crop_dir": str(crops_dir) if crops_dir is not None else None,
    }

    if not args.score_only:
        frozen_body = {"schema_version": "plate-ocr-frozen-units/1", "video": str(video_path), "units": units, "frozen_units": frozen}
        frozen_bytes = _json.dumps(frozen_body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        frozen_sha = hashlib.sha256(frozen_bytes).hexdigest()
        frozen_body["frozen_package_sha256"] = frozen_sha
        with (out_dir / "frozen_units.json").open("x", encoding="utf-8") as handle:
            _json.dump(frozen_body, handle, indent=2, sort_keys=True)
        blinded = [{"unit_id": u["unit_id"], "vehicle_crop_name": u.get("vehicle_crop_name"), "vehicle_box_in_frame": u.get("vehicle_box_in_frame"), "frame_width": u.get("frame_width"), "frame_height": u.get("frame_height"), "readable": None, "uncertain": None, "text": None, "note": ""} for u in units]
        with (out_dir / "labeling_package.json").open("x", encoding="utf-8") as handle:
            _json.dump({"schema_version": "plate-ocr-blinded-labels/1", "evaluation_set_id": frozen_sha, "units": blinded}, handle, indent=2, sort_keys=True)
        payload["frozen_package_sha256"] = frozen_sha
    if args.labels:
        labels = json.loads(Path(args.labels).read_text(encoding="utf-8"), object_pairs_hook=_unique_json_object)
        payload["score"] = score(units, labels)
        evidence = json.loads(Path(args.qualification_evidence).read_text(encoding="utf-8")) if args.qualification_evidence else None
        payload["score"]["qualification_evidence"] = evidence
        payload["gate"] = gate_verdict(payload["score"])
        blockers = evidence.get("license_blockers") if isinstance(evidence, dict) else None
        record = {
            "schema_version": "plate-ocr-evaluation/1",
            "result": payload["gate"]["verdict"],
            "evaluation_set_id": payload.get("frozen_package_sha256") or frozen_payload.get("frozen_package_sha256"),
            "frozen_package_sha256": payload.get("frozen_package_sha256") or frozen_payload.get("frozen_package_sha256"),
            "frozen_package_path": str(Path(args.frozen_units).resolve()) if args.score_only else str(out_dir / "frozen_units.json"),
            "labels_path": str(Path(args.labels).resolve()),
            "labels_sha256": hashlib.sha256(Path(args.labels).read_bytes()).hexdigest(),
            "model_hashes": settings.expected_hashes(),
            "config_sha256": settings.config_sha256,
            "settings_sha256": settings.config_sha256,
            "provider": settings.provider,
            "ocr_color_mode": settings.ocr_color_mode,
            "read_rate": payload["score"]["read_rate"],
            "counts": {
                "total_units": payload["score"]["total_units"],
                "labels_complete": payload["score"]["total_units"] - payload["score"]["unlabeled_units"],
                "human_readable_denominator": payload["score"]["human_readable_denominator"],
                "exact_matches": payload["score"]["exact_matches"],
                    "human_unreadable": payload["score"]["human_unreadable"],
                "human_uncertain": payload["score"]["human_uncertain"],
                "human_unreadable_or_uncertain": payload["score"]["human_unreadable_or_uncertain"],
            },
            "association_audit": {
                "audited": isinstance(evidence, dict) and evidence.get("association_audited") is True,
                "unresolved_errors": evidence.get("unresolved_association_errors") if isinstance(evidence, dict) else None,
                "results": evidence.get("association_results") if isinstance(evidence, dict) else None,
            },
            "verification_checks": {key: evidence.get(key) for key in ("offline", "isolation", "admin_authorization", "retry_preservation", "browser_review")} if isinstance(evidence, dict) else {},
            "blockers": blockers if isinstance(blockers, list) else ["qualification evidence missing"],
        }
        with (out_dir / "evaluation_gate_record.json").open("x", encoding="utf-8") as handle:
            _json.dump(record, handle, indent=2, sort_keys=True)

    out_path.write_text(_json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {out_path} ({len(units)} evaluation units)")
    return 0


# ----------------------------------------------------------------------
# Unit freezing
# ----------------------------------------------------------------------


def freeze_units(adapter, video_id: int, *, split: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build the evaluation-unit list. Runs before any OCR output is scored."""
    from core import plate_manifest as manifests

    reviews = {
        int(r["id"]): r
        for r in adapter.list_review_items_for_video(video_id, status=None)
        if r.get("id") is not None
    }
    ordered_ids = sorted(reviews)

    attempts: list[dict[str, Any]] = []
    for review_id in ordered_ids:
        for loaded in manifests.attempts_for_review(review_id):
            data = loaded.data or {}
            source = data.get("source") or {}
            trigger = data.get("trigger") or {}
            attempts.append(
                {
                    "attempt_id": loaded.attempt_id,
                    "review_id": review_id,
                    "source": source.get("kind"),
                    "run_key": source.get("run_key"),
                    "live_session_id": source.get("live_session_id"),
                    "track_id": source.get("track_id"),
                    "track_identity_epoch": source.get("track_identity_epoch"),
                    "frame_number": trigger.get("frame_number"),
                    "timestamp_sec": trigger.get("timestamp_sec"),
                    "outcome": data.get("outcome"),
                    "association_uncertain": bool(data.get("association_uncertain")),
                    "quality_rejections": data.get("quality_rejections") or [],
                    "candidates": data.get("candidates") or [],
                    "primary_candidate_id": data.get("primary_candidate_id"),
                    "calls": data.get("calls"),
                    "truncated": data.get("truncated"),
                    "timings": data.get("timings"),
                }
            )

    # Apply the time split on the trigger timestamp.
    timestamps = [float(a["timestamp_sec"] or 0.0) for a in attempts]
    if split == "all" or not timestamps:
        lower, upper = None, None
    else:
        midpoint = (min(timestamps) + max(timestamps)) / 2.0
        if split == "first_half":
            lower, upper = None, midpoint
        else:
            lower, upper = midpoint, None
    selected = [
        a
        for a in attempts
        if (lower is None or float(a["timestamp_sec"] or 0.0) >= lower)
        and (upper is None or float(a["timestamp_sec"] or 0.0) < upper)
    ]

    # Group into plate episodes: same run/session/track/epoch, gaps > the
    # predeclared threshold start a new episode.
    episodes: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for attempt in selected:
        key = (
            attempt["source"],
            attempt["run_key"],
            attempt["live_session_id"],
            attempt["track_id"],
            attempt["track_identity_epoch"],
        )
        episodes.setdefault(key, []).append(attempt)

    units: list[dict[str, Any]] = []
    for key in sorted(episodes, key=lambda k: tuple(str(part) for part in k)):
        group = sorted(
            episodes[key],
            key=lambda a: (int(a["frame_number"] or 0), str(a["attempt_id"])),
        )
        current: list[dict[str, Any]] = []
        previous_ts: float | None = None
        episode_index = 0
        for attempt in group:
            ts = float(attempt["timestamp_sec"] or 0.0)
            if previous_ts is not None and (ts - previous_ts) > EPISODE_GAP_SEC:
                units.append(_unit_from(key, episode_index, current))
                episode_index += 1
                current = []
            current.append(attempt)
            previous_ts = ts
        if current:
            units.append(_unit_from(key, episode_index, current))

    units.sort(key=lambda u: (str(u["source"]), str(u["run_key"]), str(u["live_session_id"]), int(u["track_id"] or 0), int(u["track_identity_epoch"] or 0), float(u["first_timestamp_sec"] or 0.0)))
    frozen = {
        "count": len(units),
        "episode_gap_sec": EPISODE_GAP_SEC,
        "scored_manifest_rule": SCORED_MANIFEST_RULE,
        "split": split,
        "split_lower_bound_sec": lower,
        "split_upper_bound_sec": upper,
        "total_attempts": len(attempts),
        "selected_attempts": len(selected),
    }
    return units, frozen


def _unit_from(
    key: tuple[Any, ...], episode_index: int, attempts: list[dict[str, Any]]
) -> dict[str, Any]:
    scored = attempts[0]
    scored_candidate = next(
        (
            c
            for c in scored["candidates"]
            if c.get("candidate_id") == scored.get("primary_candidate_id")
        ),
        None,
    )
    identity = {"source": key[0], "run_key": key[1], "live_session_id": key[2], "track_id": key[3], "track_identity_epoch": key[4], "episode": episode_index, "first_attempt_id": scored["attempt_id"]}
    unit_id = "u_" + hashlib.sha256(_json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()[:20]
    return {
        "unit_id": unit_id,
        "source": key[0],
        "run_key": key[1],
        "live_session_id": key[2],
        "track_id": key[3],
        "track_identity_epoch": key[4],
        "attempt_count": len(attempts),
        "first_review_id": scored["review_id"],
        "scored_attempt_id": scored["attempt_id"],
        "first_frame_number": scored["frame_number"],
        "first_timestamp_sec": scored["timestamp_sec"],
        "machine_outcome": scored["outcome"],
        "machine_association_uncertain": scored["association_uncertain"],
        "machine_quality_rejections": scored["quality_rejections"],
        "machine_calls": scored["calls"],
        "machine_truncated": scored["truncated"],
        "machine_timings": scored["timings"],
        "all_attempts": [
            {
                "attempt_id": a["attempt_id"],
                "review_id": a["review_id"],
                "frame_number": a["frame_number"],
                "timestamp_sec": a["timestamp_sec"],
                "outcome": a["outcome"],
                "candidate_texts": [c.get("ocr_raw") for c in a["candidates"]],
                "calls": a["calls"],
            }
            for a in attempts
        ],
        # withheld until after human labelling
        "scored_candidate": scored_candidate,
        "vehicle_crop_name": None,
        "vehicle_crop_path": None,
    }


def export_unit_crop(unit: dict[str, Any], crops_dir: Path) -> None:
    """Copy the scored attempt's first vehicle crop for independent labelling."""
    from core import plate_manifest as manifests

    data = manifests.load_manifest(unit["scored_attempt_id"])
    if not data.ok or data.data is None:
        return
    samples = data.data.get("samples") or []
    if not samples:
        return
    ref = samples[0].get("vehicle_crop_ref") or {}
    source = manifests.resolve_crop_ref(ref.get("stored_path"))
    if source is None:
        return
    target = crops_dir / f"{unit['unit_id']}{source.suffix}"
    if target.exists():
        raise FileExistsError(f"evaluation crop already exists: {target}")
    shutil.copyfile(source, target)
    unit["vehicle_crop_name"] = target.name
    unit["vehicle_crop_path"] = str(target)
    unit["vehicle_box_in_frame"] = samples[0].get("vehicle_box_frame_px")
    unit["frame_width"] = samples[0].get("frame_width")
    unit["frame_height"] = samples[0].get("frame_height")
    unit["crop_width"] = samples[0].get("crop_width")
    unit["crop_height"] = samples[0].get("crop_height")


# ----------------------------------------------------------------------
# Scoring
# ----------------------------------------------------------------------


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def score(units: list[dict[str, Any]], labels: dict[str, Any]) -> dict[str, Any]:
    """Score the frozen units against independent human labels.

    read rate = exact full-plate string matches
                / plates independently judged readable by a human

    Quality-rejected and no-read examples that a human judged readable stay in
    the denominator. Human-unreadable/uncertain examples are excluded from that
    denominator and reported separately.
    """
    if not isinstance(labels, dict) or set(labels) != {unit["unit_id"] for unit in units}:
        raise ValueError("labels must contain exactly one entry for every frozen unit")
    total = len(units)
    readable = 0
    unreadable = 0
    uncertain = 0
    unlabeled = 0
    exact = 0
    wrong_nonempty = 0
    no_read = 0
    false_nonempty = 0
    conflicts = 0
    association_uncertain = 0
    quality_excluded = 0
    per_unit: list[dict[str, Any]] = []

    for unit in units:
        label = labels.get(unit["unit_id"])
        if not isinstance(label, dict):
            raise ValueError(f"label entry must be an object: {unit['unit_id']}")
        candidate = unit.get("scored_candidate") or {}
        machine_text = candidate.get("ocr_raw")
        machine_outcome = unit["machine_outcome"]

        if unit["machine_association_uncertain"]:
            association_uncertain += 1
        if unit["machine_quality_rejections"]:
            quality_excluded += 1
        texts = {
            (c.get("ocr_raw") or "").strip()
            for a in unit["all_attempts"]
            for c in []
        }
        attempt_texts = {
            (t or "").strip()
            for a in unit["all_attempts"]
            for t in a["candidate_texts"]
            if (t or "").strip()
        }
        if len(attempt_texts) > 1:
            conflicts += 1

        if label is None:
            unlabeled += 1
            per_unit.append({"unit_id": unit["unit_id"], "labelled": False})
            continue

        if type(label.get("readable")) is not bool or type(label.get("uncertain")) is not bool:
            raise ValueError(f"label readability and uncertainty must be booleans: {unit['unit_id']}")
        human_readable = label["readable"]
        human_uncertain = label["uncertain"]
        if human_readable and human_uncertain:
            raise ValueError(f"label cannot be both readable and uncertain: {unit['unit_id']}")
        human_text = (label.get("text") or "").strip() or None
        if human_readable and not human_text:
            raise ValueError(f"readable label requires exact text: {unit['unit_id']}")
        if not human_readable and human_text:
            raise ValueError(f"unreadable or uncertain label cannot include plate text: {unit['unit_id']}")
        if human_readable:
            readable += 1
        elif human_uncertain:
            uncertain += 1
        else:
            unreadable += 1
            if machine_text:
                false_nonempty += 1

        if human_readable:
            if machine_text is None or machine_text == "":
                no_read += 1
                verdict = "no_read"
            elif machine_text == human_text:
                exact += 1
                verdict = "exact"
            else:
                wrong_nonempty += 1
                verdict = "wrong_nonempty"
        else:
            verdict = "excluded_human_unreadable"

        per_unit.append(
            {
                "unit_id": unit["unit_id"],
                "labelled": True,
                "human_readable": human_readable,
                "human_uncertain": human_uncertain,
                "human_text": human_text,
                "machine_text": machine_text,
                "machine_outcome": machine_outcome,
                "verdict": verdict,
                "machine_association_uncertain": unit["machine_association_uncertain"],
                "machine_quality_rejections": unit["machine_quality_rejections"],
            }
        )

    read_rate = (exact / readable) if readable else None
    return {
        "total_units": total,
        "human_readable_denominator": readable,
        "human_unreadable": unreadable,
        "human_uncertain": uncertain,
        "human_unreadable_or_uncertain": unreadable + uncertain,
        "unlabeled_units": unlabeled,
        "exact_matches": exact,
        "wrong_nonempty_reads": wrong_nonempty,
        "no_read_or_unreadable_machine_outcomes": no_read,
        "false_nonempty_reads_on_human_unreadable": false_nonempty,
        "candidate_conflict_units": conflicts,
        "association_uncertain_units": association_uncertain,
        "quality_excluded_units": quality_excluded,
        "read_rate": read_rate,
        "read_rate_formula": (
            "exact full-plate string matches / plates independently judged readable by a human"
        ),
        "per_unit": per_unit,
    }


def gate_verdict(score_payload: dict[str, Any]) -> dict[str, Any]:
    """Evaluate the experimental defense-demo gate. Simulation scope only."""
    checks: list[dict[str, Any]] = []
    denominator = int(score_payload["human_readable_denominator"] or 0)
    read_rate = score_payload["read_rate"]
    labels_complete = int(score_payload.get("unlabeled_units", -1)) == 0 and int(score_payload.get("total_units", 0)) > 0
    checks.append({"name": "labels_complete", "passed": labels_complete, "detail": f"unlabeled={score_payload.get('unlabeled_units')}"})
    checks.append(
        {
            "name": "readable_denominator_above_zero",
            "passed": denominator > 0,
            "detail": f"denominator={denominator}",
        }
    )
    rate_ok = read_rate is not None and float(read_rate) >= 0.40
    checks.append(
        {
            "name": "read_rate_at_least_40_percent",
            "passed": rate_ok,
            "detail": (
                f"read_rate={read_rate} ({score_payload['exact_matches']}/{denominator})"
                if read_rate is not None
                else "read_rate undefined (no readable denominator)"
            ),
        }
    )
    evidence = score_payload.get("qualification_evidence")
    required = ("offline", "isolation", "admin_authorization", "retry_preservation", "browser_review")
    association_results = evidence.get("association_results") if isinstance(evidence, dict) else None
    association_ids = {row.get("unit_id") for row in association_results if isinstance(row, dict)} if isinstance(association_results, list) else set()
    expected_ids = {row.get("unit_id") for row in score_payload.get("per_unit", []) if isinstance(row, dict)}
    associations_ok = isinstance(association_results, list) and len(association_results) == int(score_payload.get("total_units", 0)) and association_ids == expected_ids and all(row.get("status") in ("associated", "no_candidate") for row in association_results if isinstance(row, dict))
    evidence_ok = isinstance(evidence, dict) and all(evidence.get(key) is True for key in required) and evidence.get("association_audited") is True and type(evidence.get("unresolved_association_errors")) is int and evidence.get("unresolved_association_errors") == 0 and associations_ok and evidence.get("license_blockers") == []
    checks.append({"name": "qualification_evidence_complete", "passed": evidence_ok, "detail": "required verification evidence and license review are missing or failing" if not evidence_ok else "complete"})
    return {
        "checks": checks,
        "scope": "experimental defense-demo gate; simulation footage only; not a production-readiness threshold",
        "verdict": "pass" if all(c["passed"] for c in checks) else "fail",
    }


if __name__ == "__main__":
    raise SystemExit(main())
