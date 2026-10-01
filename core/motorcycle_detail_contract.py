"""Class-map contracts for the separate motorcycle detail checkpoint.

The detail pass scans padded motorcycle crops for attribute labels only.
It is not the main object detector: the main YOLOv8m/ByteTrack checkpoint keeps
its own ordered 15-class contract (``core.detection_config.OBJECT_DETECTOR_CLASSES``
and ``core.detector.enforce_object_class_contract``), and nothing here changes it.

Two explicit contracts are accepted, and only those. IDs are never relabeled
to make a checkpoint fit a different order.

``md-detail-3c-v1`` (``DETAIL_MODEL_CLASSES``), the September 30 three-class
annotation working copy
(``dataset/derived/tavidm_nut_shell_side_mirror_annotations_20260930``)::

    0 = helmet_nut_shell
    1 = helmet_acceptable
    2 = side_mirror

``md-detail-4c-v1`` (``DETAIL_MODEL_CLASSES_4C``) keeps that order and appends
one positive class::

    0 = helmet_nut_shell
    1 = helmet_acceptable
    2 = side_mirror
    3 = no_helmet

``no_helmet`` means a visible uncovered head was observed. A three-class
checkpoint cannot emit it. No helmet-class detection is ``unknown``, never an
inferred uncovered head.

The exploratory YOLOv8n pilots use a different order
(``PILOT_DETAIL_CLASS_ORDER``) and are rejected with a visible reason.

The detail model cannot output ``rider`` or ``motorcycle``. Rider and target
motorcycle attribution always comes from the main detector's stored context
(see ``core.motorcycle_detail.MainDetectorContext``).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.detection_config import (
    YOLO_CLASS_HELMET_ACCEPTABLE,
    YOLO_CLASS_HELMET_NUT_SHELL,
    YOLO_CLASS_SIDE_MIRROR,
    _ordered_names_from_map,
)

DETAIL_CONTRACT_VERSION = "md-detail-3c-v1"
DETAIL_MODEL_KIND = "motorcycle_detail_3class"
# Prefix of the stored ``scan_model`` identity. Deliberately not "YOLOv8m": the
# detail checkpoint is a separate attribute model, not the main object detector.
DETAIL_IDENTITY_PREFIX = "md3c"

DETAIL_MODEL_CLASSES: tuple[str, ...] = (
    YOLO_CLASS_HELMET_NUT_SHELL,
    YOLO_CLASS_HELMET_ACCEPTABLE,
    YOLO_CLASS_SIDE_MIRROR,
)
DETAIL_CLASS_COUNT = len(DETAIL_MODEL_CLASSES)

# Positive observation of a visible uncovered head. This name is not part of
# the main 15-class object roster and must not be added there.
DETAIL_CLASS_NO_HELMET = "no_helmet"
DETAIL_CONTRACT_VERSION_4C = "md-detail-4c-v1"
DETAIL_MODEL_KIND_4C = "motorcycle_detail_4class"
DETAIL_IDENTITY_PREFIX_4C = "md4c"
DETAIL_MODEL_CLASSES_4C: tuple[str, ...] = DETAIL_MODEL_CLASSES + (DETAIL_CLASS_NO_HELMET,)
DETAIL_CLASS_COUNT_4C = len(DETAIL_MODEL_CLASSES_4C)

# Order used by the exploratory full-frame and crop YOLOv8n pilots
# (``dataset/training_runs/tavidm_motorcycle_detail*_yolov8n_20260930``).
PILOT_DETAIL_CLASS_ORDER: tuple[str, ...] = (
    YOLO_CLASS_SIDE_MIRROR,
    YOLO_CLASS_HELMET_NUT_SHELL,
    YOLO_CLASS_HELMET_ACCEPTABLE,
)


@dataclass(frozen=True)
class DetailContractSpec:
    """One accepted detail checkpoint contract. Order is the contract."""

    version: str
    model_kind: str
    identity_prefix: str
    classes: tuple[str, ...]


DETAIL_CONTRACT_3C = DetailContractSpec(
    version=DETAIL_CONTRACT_VERSION,
    model_kind=DETAIL_MODEL_KIND,
    identity_prefix=DETAIL_IDENTITY_PREFIX,
    classes=DETAIL_MODEL_CLASSES,
)
DETAIL_CONTRACT_4C = DetailContractSpec(
    version=DETAIL_CONTRACT_VERSION_4C,
    model_kind=DETAIL_MODEL_KIND_4C,
    identity_prefix=DETAIL_IDENTITY_PREFIX_4C,
    classes=DETAIL_MODEL_CLASSES_4C,
)
# The only maps a checkpoint may match. Nothing else is accepted or relabeled.
SUPPORTED_DETAIL_CONTRACTS: tuple[DetailContractSpec, ...] = (
    DETAIL_CONTRACT_3C,
    DETAIL_CONTRACT_4C,
)

# The only accepted Ultralytics task. Missing/blank/non-string metadata is not
# evidence of a detection head, so it is rejected rather than assumed.
DETAIL_MODEL_TASK = "detect"

_ARCHITECTURE_PATTERN = re.compile(r"^(yolov?\d+[nsmlx]?)(?:[-_.].*)?$", re.IGNORECASE)
_TASK_REASON_PATTERN = re.compile(r"[^A-Za-z0-9_.-]")


def check_detail_task(task: Any) -> str | None:
    """Gate reason when ``task`` does not explicitly declare a detection model.

    Returns ``None`` only for the exact task ``"detect"`` (surrounding
    whitespace ignored). Everything else fails closed with a visible reason:

    - ``detail_checkpoint_task_missing`` — ``None`` or blank;
    - ``detail_checkpoint_task_malformed:<type>`` — not a string;
    - ``detail_checkpoint_wrong_task:<task>`` — any other task name.
    """
    if task is None or (isinstance(task, str) and not task.strip()):
        return "detail_checkpoint_task_missing"
    if not isinstance(task, str):
        return f"detail_checkpoint_task_malformed:{type(task).__name__}"
    value = task.strip()
    if value == DETAIL_MODEL_TASK:
        return None
    return f"detail_checkpoint_wrong_task:{_TASK_REASON_PATTERN.sub('?', value)[:40]}"


@dataclass(frozen=True)
class DetailClassMapReport:
    """Outcome of checking a checkpoint's ID->name map against the detail contract."""

    ok: bool
    class_names: tuple[str, ...] = ()
    code: str | None = None
    reason: str | None = None

    def class_map(self) -> dict[str, str]:
        return {str(i): name for i, name in enumerate(self.class_names)}


def check_detail_class_map(
    raw_map: Any, *, expected: Sequence[str] = DETAIL_MODEL_CLASSES
) -> DetailClassMapReport:
    """Strictly validate a detail checkpoint's class map. Never raises.

    Contiguous integer IDs starting at 0, exact names, exact order, and exact
    count are all required. Missing, malformed, renamed, duplicated, reordered,
    or mis-sized maps are rejected with a machine-readable code and a reason
    suitable for display.
    """
    roster = tuple(str(n).strip().lower() for n in expected)
    if raw_map is None:
        return DetailClassMapReport(
            ok=False, code="missing", reason="checkpoint exposes no class map"
        )
    ordered, malformed = _ordered_names_from_map(raw_map)
    if ordered is None:
        code = "non_contiguous_class_ids" if malformed == "non_contiguous_class_ids" else "malformed"
        return DetailClassMapReport(
            ok=False, code=code, reason=f"class map is unreadable ({malformed})"
        )
    duplicates = sorted(n for n, c in Counter(ordered).items() if c > 1)
    if duplicates:
        return DetailClassMapReport(
            ok=False,
            class_names=ordered,
            code="duplicate_names",
            reason="duplicate class name(s): " + ", ".join(duplicates),
        )
    if len(ordered) != len(roster):
        return DetailClassMapReport(
            ok=False,
            class_names=ordered,
            code="wrong_class_count",
            reason=(
                f"class map has {len(ordered)} classes; the motorcycle detail contract "
                f"requires exactly {len(roster)} ({_format_order(roster)})"
            ),
        )
    unknown = [n for n in ordered if n not in roster]
    missing = [n for n in roster if n not in ordered]
    if unknown or missing:
        parts = []
        if missing:
            parts.append("missing " + ", ".join(missing))
        if unknown:
            parts.append("unknown " + ", ".join(unknown))
        return DetailClassMapReport(
            ok=False,
            class_names=ordered,
            code="renamed_names",
            reason="class names do not match the detail contract: " + "; ".join(parts),
        )
    if ordered != roster:
        pilot_order = (
            ordered == PILOT_DETAIL_CLASS_ORDER
            or tuple(ordered[: len(PILOT_DETAIL_CLASS_ORDER)]) == PILOT_DETAIL_CLASS_ORDER
        )
        pilot = " (exploratory pilot order; IDs are never relabeled)" if pilot_order else ""
        return DetailClassMapReport(
            ok=False,
            class_names=ordered,
            code="reordered_names",
            reason=(
                f"class IDs are reordered: expected {_format_order(roster)} but the "
                f"checkpoint reports {_format_order(ordered)}{pilot}"
            ),
        )
    raw_names = _raw_ordered_values(raw_map)
    if raw_names != list(roster):
        return DetailClassMapReport(
            ok=False,
            class_names=ordered,
            code="renamed_names",
            reason=(
                "class names must match the detail contract exactly (case and "
                f"whitespace); checkpoint reports {raw_names!r}"
            ),
        )
    return DetailClassMapReport(ok=True, class_names=ordered)


@dataclass(frozen=True)
class ResolvedDetailContract:
    """The one supported contract a checkpoint matched, or a visible rejection."""

    ok: bool
    version: str | None = None
    model_kind: str | None = None
    identity_prefix: str | None = None
    class_names: tuple[str, ...] = ()
    code: str | None = None
    reason: str | None = None

    def class_map(self) -> dict[str, str]:
        return {str(i): name for i, name in enumerate(self.class_names)}


_STRUCTURAL_MAP_CODES = frozenset(
    {"missing", "malformed", "non_contiguous_class_ids", "duplicate_names"}
)


def resolve_detail_contract(raw_map: Any) -> ResolvedDetailContract:
    """Match a checkpoint map to exactly one supported detail contract.

    ``md-detail-3c-v1`` and ``md-detail-4c-v1`` are tried as whole ordered
    rosters. A map that matches neither is rejected. A three-class map is never
    described as four-class, and a four-class map is never described as
    three-class. This function does not relabel IDs.
    """
    reports = [
        (spec, check_detail_class_map(raw_map, expected=spec.classes))
        for spec in SUPPORTED_DETAIL_CONTRACTS
    ]
    matched = [(spec, report) for spec, report in reports if report.ok]
    if len(matched) == 1:
        spec, report = matched[0]
        return ResolvedDetailContract(
            ok=True,
            version=spec.version,
            model_kind=spec.model_kind,
            identity_prefix=spec.identity_prefix,
            class_names=report.class_names,
        )
    if len(matched) > 1:  # pragma: no cover - the two rosters have different lengths
        return ResolvedDetailContract(
            ok=False,
            code="ambiguous_contract",
            reason="checkpoint class map matches more than one detail contract",
        )
    for _spec, report in reports:
        if report.code in _STRUCTURAL_MAP_CODES:
            return ResolvedDetailContract(
                ok=False,
                class_names=report.class_names,
                code=report.code,
                reason=report.reason,
            )
    parsed = reports[0][1].class_names
    for spec, report in reports:
        if parsed and len(parsed) == len(spec.classes):
            return ResolvedDetailContract(
                ok=False,
                class_names=report.class_names,
                code=report.code,
                reason=report.reason,
            )
    accepted = " or ".join(
        f"exactly {len(spec.classes)} ({_format_order(spec.classes)})"
        for spec in SUPPORTED_DETAIL_CONTRACTS
    )
    return ResolvedDetailContract(
        ok=False,
        class_names=parsed,
        code="wrong_class_count",
        reason=(
            f"class map has {len(parsed)} classes; accepted motorcycle detail "
            f"contracts require {accepted}"
        ),
    )


def detail_architecture_hint(model: Any) -> str | None:
    """Best-effort base architecture read from checkpoint metadata (display only).

    Ultralytics checkpoints record their training base weights under
    ``ckpt["train_args"]["model"]`` (for example ``.../yolov8n.pt``). Only the
    recognised architecture stem is returned, never a path. This never gates a
    scan: the class-map contract does.
    """
    try:
        ckpt = getattr(model, "ckpt", None)
        args = ckpt.get("train_args") if isinstance(ckpt, dict) else None
        base = args.get("model") if isinstance(args, dict) else None
    except Exception:
        return None
    if not isinstance(base, str) or not base.strip():
        return None
    stem = Path(base.strip().replace("\\", "/")).stem
    match = _ARCHITECTURE_PATTERN.match(stem)
    return match.group(1).lower() if match else None


def _raw_ordered_values(raw_map: Any) -> list[Any]:
    """Un-normalized names in ID order (only called after the map parsed cleanly)."""
    if isinstance(raw_map, Mapping):
        return [value for _key, value in sorted(raw_map.items(), key=lambda kv: int(kv[0]))]
    return list(raw_map)


def _format_order(names: Sequence[str]) -> str:
    return "[" + ", ".join(f"{i}={n}" for i, n in enumerate(names)) + "]"
