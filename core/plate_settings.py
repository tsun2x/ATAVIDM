"""Experimental local plate OCR configuration (disabled by default).

Nothing here loads a model, opens a socket, or touches the database. The
settings object is a pure, hashable description of an *explicitly enabled*
local experiment. The default state is :data:`PLATE_OCR_DISABLED` so that a
stock TAVIDM checkout never runs plate OCR.

Enablement contract
--------------------
* ``TAVIDM_PLATE_OCR_CONFIG`` must point at a JSON file that sets
  ``"enabled": true`` **and** supplies every artifact path, every expected
  SHA-256, and an explicit ``provider``. There is no partial configuration and
  no environment-only shortcut that could silently half-enable the feature.
* The provider is validated against an explicit allowlist. Automatic provider
  selection and undocumented CPU fallback are rejected.
* ``evaluation_record`` is retained as provenance for evaluation tooling. A
  passing evaluation is not required to start an isolated local thesis demo.
  Configured model and config artifact hashes are still checked before OCR
  runs.

This module never reads ``os.environ`` for anything other than the single
config-path variable, so the demo launcher can scope it to one child process
without touching the user's global environment.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from config import BASE_DIR

CONFIG_ENV_VAR = "TAVIDM_PLATE_OCR_CONFIG"

CONTRACT_VERSION = "plate-ocr-manifest/1"

#: Explicit providers this build will accept. There is deliberately no
#: "auto" and no silent CPU fallback: an operator must name the device.
ALLOWED_PROVIDERS = ("CPUExecutionProvider",)

#: Frozen quality thresholds for the reported evaluation. They are *not*
#: tuned at runtime and are recorded in every manifest so a result can always
#: be traced back to the thresholds that produced it.
DEFAULT_QUALITY = {
    # Native plate size below this is not recognizable by a 64x128 OCR input.
    "min_plate_width_px": 24,
    "min_plate_height_px": 10,
    # Variance-of-Laplacian floor. Below this the crop is rejected as blurred.
    "min_laplacian_variance": 18.0,
    # Specular blow-out fraction inside the plate box.
    "max_blown_highlight_ratio": 0.35,
    # Plate aspect ratio (w/h) outside this band means the box is probably not
    # a plate, or is heavily foreshortened.
    "min_aspect_ratio": 1.6,
    "max_aspect_ratio": 9.0,
    # Vehicle-crop association: reject when another tracked vehicle overlaps
    # the target vehicle box above this IoU.
    "max_vehicle_overlap_iou": 0.30,
    # Plate box must sit inside the vehicle crop with at least this much of its
    # own area retained after clamping.
    "min_plate_area_retention": 0.55,
}

#: Frozen engineering bounds. Reported honestly in every manifest.
DEFAULT_BOUNDS = {
    "collection_window_sec": 2.0,
    "sample_fps": 2.0,
    "max_crops_per_observation": 3,
    "max_detector_calls": 3,
    "max_ocr_calls": 6,
    "worker_count": 1,
    "max_queued_jobs": 16,
    "retained_crop_memory_budget_bytes": 64 * 1024 * 1024,
    "schedule_budget_sec": 1.0,
}

#: Hard ceiling on a single serialized manifest. A larger payload is a bug or
#: an attack, not a result worth storing.
MAX_MANIFEST_BYTES = 512 * 1024

#: Colour handling for the OCR head. See :attr:`PlateOcrSettings.ocr_color_mode`.
ALLOWED_OCR_COLOR_MODES = ("gray", "rgb")


class PlateOcrConfigError(ValueError):
    """The plate OCR configuration is unusable. Never silently repaired."""


@dataclass(frozen=True)
class PlateOcrSettings:
    """Explicitly-enabled local plate OCR settings."""

    enabled: bool = False
    disabled_reason: str = "plate_ocr_disabled_by_default"

    provider: str | None = None
    detector_path: str | None = None
    detector_sha256: str | None = None
    ocr_path: str | None = None
    ocr_sha256: str | None = None
    ocr_config_path: str | None = None
    ocr_config_sha256: str | None = None

    detector_conf_threshold: float = 0.25
    ocr_min_char_confidence: float = 0.0
    #: The published plate config omits ``image_color_mode`` (library default
    #: "grayscale", 1 channel) but this ONNX export takes 3 channels, so the
    #: colour handling must be declared. "gray" replicates single-channel
    #: grayscale across 3 channels; "rgb" feeds BGR->RGB. Frozen for the
    #: reported evaluation.
    ocr_color_mode: str = "gray"

    quality: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_QUALITY))
    bounds: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_BOUNDS))

    evaluation_record_path: str | None = None
    config_path: str | None = None
    config_sha256: str | None = None

    # ------------------------------------------------------------------
    @property
    def is_enabled(self) -> bool:
        return bool(self.enabled)

    def artifact_paths(self) -> tuple[str, ...]:
        return tuple(
            p for p in (self.detector_path, self.ocr_path, self.ocr_config_path) if p
        )

    def expected_hashes(self) -> dict[str, str]:
        out: dict[str, str] = {}
        if self.detector_path and self.detector_sha256:
            out[self.detector_path] = self.detector_sha256
        if self.ocr_path and self.ocr_sha256:
            out[self.ocr_path] = self.ocr_sha256
        if self.ocr_config_path and self.ocr_config_sha256:
            out[self.ocr_config_path] = self.ocr_config_sha256
        return out

    def provenance(self) -> dict[str, Any]:
        """Serializable identity of this configuration (no secrets, no paths
        outside the ones already declared by the operator)."""
        return {
            "provider_requested": self.provider,
            "detector_path": self.detector_path,
            "detector_sha256": self.detector_sha256,
            "ocr_path": self.ocr_path,
            "ocr_sha256": self.ocr_sha256,
            "ocr_config_path": self.ocr_config_path,
            "ocr_config_sha256": self.ocr_config_sha256,
            "config_path": self.config_path,
            "config_sha256": self.config_sha256,
            "detector_conf_threshold": self.detector_conf_threshold,
            "ocr_min_char_confidence": self.ocr_min_char_confidence,
            "ocr_color_mode": self.ocr_color_mode,
            "quality": dict(self.quality),
            "bounds": dict(self.bounds),
            "evaluation_record_path": self.evaluation_record_path,
        }


PLATE_OCR_DISABLED = PlateOcrSettings()


# ----------------------------------------------------------------------
# Loading
# ----------------------------------------------------------------------


def _require(mapping: dict[str, Any], key: str, source: str) -> Any:
    if key not in mapping or mapping[key] in (None, ""):
        raise PlateOcrConfigError(f"plate OCR config {source} is missing required key '{key}'")
    return mapping[key]


def _sha(value: Any, key: str) -> str:
    text = str(value or "").strip().lower()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise PlateOcrConfigError(
            f"plate OCR config key '{key}' must be a 64-character lowercase hex SHA-256"
        )
    return text


def _resolve_path(value: Any, key: str, base: Path) -> str:
    """Resolve a declared path.

    Absolute paths are used verbatim. Relative paths are resolved against the
    repository root first (the demo configuration is documented as
    repository-relative) and then against the configuration file's own
    directory, so both conventions work without ambiguity.
    """
    raw = str(_require({"v": value}, "v", f"key '{key}'"))
    candidate = Path(raw)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        from_root = (BASE_DIR / candidate).resolve()
        resolved = from_root if from_root.exists() else (base / candidate).resolve()
    if not resolved.is_file():
        raise PlateOcrConfigError(f"plate OCR config key '{key}' does not point at a file: {resolved}")
    return str(resolved)


def _merge(defaults: dict[str, float], override: Any, section: str) -> dict[str, float]:
    merged = dict(defaults)
    if override in (None, {}):
        return merged
    if not isinstance(override, dict):
        raise PlateOcrConfigError(f"plate OCR config '{section}' must be an object")
    for key, value in override.items():
        if key not in defaults:
            raise PlateOcrConfigError(
                f"plate OCR config '{section}' has unknown key '{key}'. "
                "Bounds and quality thresholds are fixed for the reported evaluation."
            )
        try:
            merged[key] = type(defaults[key])(value)
        except (TypeError, ValueError) as exc:
            raise PlateOcrConfigError(
                f"plate OCR config '{section}.{key}' is not numeric"
            ) from exc
    return merged


def load_plate_ocr_settings(config_path: str | Path | None = None) -> PlateOcrSettings:
    """Load explicit settings, or return the disabled default.

    Never raises for a plain "not configured" case: an absent variable is the
    normal disabled state.
    """
    path_text = config_path if config_path else os.environ.get(CONFIG_ENV_VAR, "").strip()
    if not path_text:
        return PLATE_OCR_DISABLED

    raw_path = Path(path_text)
    if not raw_path.is_absolute():
        raw_path = BASE_DIR / raw_path
    raw_path = raw_path.resolve()
    if not raw_path.is_file():
        raise PlateOcrConfigError(f"plate OCR config file not found: {raw_path}")

    try:
        payload = json.loads(raw_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlateOcrConfigError(f"plate OCR config is unreadable: {type(exc).__name__}") from exc
    if not isinstance(payload, dict):
        raise PlateOcrConfigError("plate OCR config must be a JSON object")

    if payload.get("enabled") is not True:
        # An explicitly disabled config file is honoured, not repaired.
        return replace(
            PLATE_OCR_DISABLED,
            config_path=str(raw_path),
            disabled_reason=str(payload.get("disabled_reason") or "config_file_disabled"),
        )

    config_sha = _file_sha256(raw_path)
    base = raw_path.parent

    provider = str(_require(payload, "provider", "file")).strip()
    if provider not in ALLOWED_PROVIDERS:
        raise PlateOcrConfigError(
            f"plate OCR provider '{provider}' is not allowed. "
            f"Explicitly choose one of {list(ALLOWED_PROVIDERS)}. "
            "Automatic provider selection is not supported by this build."
        )

    detector_path = _resolve_path(_require(payload, "detector_path", "file"), "detector_path", base)
    ocr_path = _resolve_path(_require(payload, "ocr_path", "file"), "ocr_path", base)
    ocr_config_path = _resolve_path(
        _require(payload, "ocr_config_path", "file"), "ocr_config_path", base
    )

    color_mode = str(payload.get("ocr_color_mode", "gray")).strip().lower()
    if color_mode not in ALLOWED_OCR_COLOR_MODES:
        raise PlateOcrConfigError(
            f"plate OCR ocr_color_mode '{color_mode}' is not allowed. "
            f"Explicitly choose one of {list(ALLOWED_OCR_COLOR_MODES)}."
        )

    evaluation_record = _resolve_path(
        _require(payload, "evaluation_record", "file"), "evaluation_record", base
    )

    return PlateOcrSettings(
        enabled=True,
        disabled_reason="",
        provider=provider,
        detector_path=detector_path,
        detector_sha256=_sha(_require(payload, "detector_sha256", "file"), "detector_sha256"),
        ocr_path=ocr_path,
        ocr_sha256=_sha(_require(payload, "ocr_sha256", "file"), "ocr_sha256"),
        ocr_config_path=ocr_config_path,
        ocr_config_sha256=_sha(_require(payload, "ocr_config_sha256", "file"), "ocr_config_sha256"),
        detector_conf_threshold=float(payload.get("detector_conf_threshold", 0.25)),
        ocr_min_char_confidence=float(payload.get("ocr_min_char_confidence", 0.0)),
        ocr_color_mode=color_mode,
        quality=_merge(DEFAULT_QUALITY, payload.get("quality"), "quality"),
        bounds=_merge(DEFAULT_BOUNDS, payload.get("bounds"), "bounds"),
        evaluation_record_path=evaluation_record,
        config_path=str(raw_path),
        config_sha256=config_sha,
    )


def _file_sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_artifact_hashes(settings: PlateOcrSettings) -> list[dict[str, Any]]:
    """Hash every declared artifact. Returns one report row per artifact.

    Missing, unreadable, or mismatched artifacts are reported here and the
    caller turns them into a controlled ``unavailable``/``failed`` outcome.
    """
    report: list[dict[str, Any]] = []
    for path, expected in settings.expected_hashes().items():
        row: dict[str, Any] = {"path": path, "expected_sha256": expected}
        candidate = Path(path)
        if not candidate.is_file():
            report.append({**row, "ok": False, "reason": "artifact_missing"})
            continue
        try:
            actual = _file_sha256(candidate)
        except OSError:
            report.append({**row, "ok": False, "reason": "artifact_unreadable"})
            continue
        row["actual_sha256"] = actual
        row["ok"] = actual == expected
        if not row["ok"]:
            row["reason"] = "artifact_hash_mismatch"
        report.append(row)
    return report


def settings_to_json(settings: PlateOcrSettings) -> str:
    return json.dumps(asdict(settings), indent=2, sort_keys=True, default=str)


# ----------------------------------------------------------------------
# Demo gate
# ----------------------------------------------------------------------


def demo_gate_status(settings: PlateOcrSettings) -> dict[str, Any]:
    """Check a recorded evaluation against the configured artifacts.

    The demo launcher refuses to start unless this returns
    ``{"ok": True, ...}``. Changing a model, a model config, or a declared
    bound changes the configuration hash and therefore invalidates the record.
    """
    if not settings.is_enabled:
        return {"ok": False, "reason": "plate_ocr_disabled"}
    if not settings.evaluation_record_path:
        return {"ok": False, "reason": "no_evaluation_record"}
    path = Path(settings.evaluation_record_path)
    if not path.is_file():
        return {"ok": False, "reason": "evaluation_record_missing"}
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {"ok": False, "reason": "evaluation_record_unreadable"}
    if not isinstance(record, dict):
        return {"ok": False, "reason": "evaluation_record_invalid"}

    if record.get("schema_version") != "plate-ocr-evaluation/1":
        return {"ok": False, "reason": "evaluation_schema_invalid"}

    hashes = record.get("model_hashes")
    if not isinstance(hashes, dict):
        return {"ok": False, "reason": "evaluation_record_has_no_model_hashes"}
    expected = settings.expected_hashes()
    for path_str, digest in expected.items():
        if hashes.get(path_str) != digest:
            return {
                "ok": False,
                "reason": "evaluation_record_hash_mismatch",
                "artifact": path_str,
                "recorded": hashes.get(path_str),
                "configured": digest,
            }
    if record.get("config_sha256") != settings.config_sha256:
        return {
            "ok": False,
            "reason": "configuration_changed_since_evaluation",
            "recorded_config_sha256": record.get("config_sha256"),
            "configured_config_sha256": settings.config_sha256,
        }
    if record.get("provider") != settings.provider:
        return {"ok": False, "reason": "provider_changed_since_evaluation"}
    color_mode = record.get("ocr_color_mode")
    if color_mode != settings.ocr_color_mode:
        return {"ok": False, "reason": "ocr_color_mode_changed_since_evaluation"}
    counts = record.get("counts")
    if not isinstance(counts, dict):
        return {"ok": False, "reason": "evaluation_counts_missing"}
    names = ("total_units", "labels_complete", "human_readable_denominator", "exact_matches", "human_unreadable", "human_uncertain", "human_unreadable_or_uncertain")
    if any(type(counts.get(name)) is not int or counts[name] < 0 for name in names):
        return {"ok": False, "reason": "evaluation_counts_invalid"}
    total, labels, readable, exact, human_unreadable, human_uncertain, unreadable = (counts[name] for name in names)
    if total <= 0 or labels != total or readable <= 0 or exact > readable or readable + human_unreadable + human_uncertain != total or human_unreadable + human_uncertain != unreadable:
        return {"ok": False, "reason": "evaluation_counts_inconsistent"}
    expected_rate = exact / readable
    try:
        rate = float(record.get("read_rate"))
    except (TypeError, ValueError):
        return {"ok": False, "reason": "evaluation_read_rate_invalid"}
    import math
    if not math.isfinite(rate) or rate != expected_rate:
        return {"ok": False, "reason": "evaluation_read_rate_inconsistent"}
    if expected_rate < 0.40:
        return {"ok": False, "reason": "evaluation_read_rate_below_threshold"}
    package_sha = record.get("frozen_package_sha256")
    if (
        not record.get("evaluation_set_id")
        or not isinstance(package_sha, str)
        or len(package_sha) != 64
        or any(char not in "0123456789abcdef" for char in package_sha.lower())
        or record.get("evaluation_set_id") != package_sha
    ):
        return {"ok": False, "reason": "evaluation_package_identity_missing"}
    package_path = record.get("frozen_package_path")
    if not isinstance(package_path, str) or not Path(package_path).is_file():
        return {"ok": False, "reason": "evaluation_package_missing"}
    try:
        package = json.loads(Path(package_path).read_text(encoding="utf-8"))
        if not isinstance(package, dict):
            return {"ok": False, "reason": "evaluation_package_malformed"}
        stored_package_sha = package.pop("frozen_package_sha256", None)
        canonical = json.dumps(package, sort_keys=True, separators=(",", ":")).encode("utf-8")
        import hashlib
        actual_package_sha = hashlib.sha256(canonical).hexdigest()
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, TypeError):
        return {"ok": False, "reason": "evaluation_package_unreadable"}
    if stored_package_sha != package_sha or actual_package_sha != package_sha:
        return {"ok": False, "reason": "evaluation_package_hash_mismatch"}
    package_units = package.get("units") if isinstance(package, dict) else None
    if (
        package.get("schema_version") != "plate-ocr-frozen-units/1"
        or not isinstance(package_units, list)
        or len(package_units) != total
        or any(not isinstance(unit, dict) or not isinstance(unit.get("unit_id"), str) or not unit["unit_id"] for unit in package_units)
        or any(
            not isinstance(unit.get("source"), str)
            or not isinstance(unit.get("run_key"), str)
            or "live_session_id" not in unit
            or type(unit.get("track_id")) is not int
            or type(unit.get("track_identity_epoch")) is not int
            for unit in package_units
            if isinstance(unit, dict)
        )
        or len({unit["unit_id"] for unit in package_units if isinstance(unit, dict) and isinstance(unit.get("unit_id"), str)}) != total
    ):
        return {"ok": False, "reason": "evaluation_package_units_inconsistent"}
    package_ids = {unit["unit_id"] for unit in package_units}
    label_path = record.get("labels_path")
    label_sha = record.get("labels_sha256")
    if not isinstance(label_path, str) or not Path(label_path).is_file() or not isinstance(label_sha, str):
        return {"ok": False, "reason": "evaluation_labels_missing"}
    try:
        label_bytes = Path(label_path).read_bytes()
        actual_label_sha = hashlib.sha256(label_bytes).hexdigest()

        def unique_pairs(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate label id")
                result[key] = value
            return result

        labels = json.loads(label_bytes.decode("utf-8"), object_pairs_hook=unique_pairs)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return {"ok": False, "reason": "evaluation_labels_unreadable"}
    if actual_label_sha != label_sha or not isinstance(labels, dict) or set(labels) != package_ids:
        return {"ok": False, "reason": "evaluation_labels_identity_mismatch"}
    label_readable = label_exact = label_uncertain = label_unreadable = 0
    for unit in package_units:
        label = labels.get(unit["unit_id"])
        if not isinstance(label, dict) or type(label.get("readable")) is not bool or type(label.get("uncertain")) is not bool:
            return {"ok": False, "reason": "evaluation_labels_incomplete_or_invalid"}
        readable_label = label["readable"]
        uncertain_label = label["uncertain"]
        text = label.get("text")
        text = text.strip() if isinstance(text, str) else None
        if readable_label and uncertain_label or (readable_label and not text) or (not readable_label and text):
            return {"ok": False, "reason": "evaluation_labels_inconsistent"}
        if readable_label:
            label_readable += 1
            candidate = unit.get("scored_candidate") or {}
            machine_text = candidate.get("ocr_raw") if isinstance(candidate, dict) else None
            if isinstance(machine_text, str) and machine_text == text:
                label_exact += 1
        elif uncertain_label:
            label_uncertain += 1
        else:
            label_unreadable += 1
    if (
        label_readable != readable
        or label_exact != exact
        or label_uncertain != human_uncertain
        or label_unreadable != human_unreadable
        or label_uncertain + label_unreadable != unreadable
    ):
        return {"ok": False, "reason": "evaluation_label_counts_inconsistent"}
    if record.get("settings_sha256") != settings.config_sha256:
        return {"ok": False, "reason": "evaluation_settings_identity_mismatch"}
    associations = record.get("association_audit")
    association_results = associations.get("results") if isinstance(associations, dict) else None
    if (
        not isinstance(associations, dict)
        or associations.get("audited") is not True
        or type(associations.get("unresolved_errors")) is not int
        or associations["unresolved_errors"] != 0
        or not isinstance(association_results, list)
        or len(association_results) != total
        or any(not isinstance(row, dict) or not isinstance(row.get("unit_id"), str) or row.get("status") not in ("associated", "no_candidate") for row in association_results)
        or {row["unit_id"] for row in association_results if isinstance(row, dict) and isinstance(row.get("unit_id"), str)} != package_ids
    ):
        return {"ok": False, "reason": "evaluation_association_audit_failed"}
    checks = record.get("verification_checks")
    required = ("offline", "isolation", "admin_authorization", "retry_preservation", "browser_review")
    if not isinstance(checks, dict) or any(checks.get(key) is not True for key in required):
        return {"ok": False, "reason": "evaluation_verification_incomplete"}
    blockers = record.get("blockers")
    if not isinstance(blockers, list) or blockers:
        return {"ok": False, "reason": "evaluation_blockers_unresolved"}
    if record.get("result") != "pass":
        return {"ok": False, "reason": "evaluation_not_passing", "recorded_result": record.get("result")}
    return {
        "ok": True,
        "reason": "recorded_evaluation_passes",
        "evaluated_at": record.get("evaluated_at"),
        "read_rate": record.get("read_rate"),
        "human_readable_denominator": record.get("human_readable_denominator"),
    }
