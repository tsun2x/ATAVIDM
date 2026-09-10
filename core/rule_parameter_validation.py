"""Server-side validation for Detection & Rule Parameters.

Bounds mirror the Settings HTML controls. Dwell fields without an HTML max
are validated as finite numbers at or above the HTML minimum only — no new
legal thresholds are invented.
"""

from __future__ import annotations

import math
import re
from typing import Any

from core.detection_config import DEFAULT_RULE_PARAMETERS

# HTML control bounds from templates/settings.html (min/max attributes).
# confidence_threshold is stored as a fraction; the slider uses percent 40–95.
RULE_PARAMETER_BOUNDS: dict[str, dict[str, float]] = {
    "confidence_threshold": {"min": 0.40, "max": 0.95},
    "frame_skip": {"min": 1.0, "max": 10.0},
    "stopping_dwell_sec": {"min": 1.0},
    "parking_dwell_sec": {"min": 1.0},
    "obstruction_dwell_sec": {"min": 1.0},
    "loading_dwell_sec": {"min": 1.0},
    "crossing_block_sec": {"min": 1.0},
    "lane_flow_degrees": {"min": 0.0, "max": 359.0},
    "flow_tolerance_degrees": {"min": 10.0, "max": 90.0},
}

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class RuleParameterValidationError(ValueError):
    """Raised when one or more rule-parameter values fail validation."""


def _parse_finite_number(raw: Any, *, key: str) -> float:
    if raw is None:
        raise RuleParameterValidationError(f"{key} is required.")
    if isinstance(raw, bool):
        raise RuleParameterValidationError(f"{key} must be a number.")
    if isinstance(raw, (int, float)):
        value = float(raw)
    elif isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise RuleParameterValidationError(f"{key} is required.")
        try:
            value = float(text)
        except ValueError as exc:
            raise RuleParameterValidationError(f"{key} must be a number.") from exc
    else:
        raise RuleParameterValidationError(f"{key} must be a number.")
    if not math.isfinite(value):
        raise RuleParameterValidationError(f"{key} must be a finite number.")
    return value


def _validate_time(raw: Any, *, key: str) -> str:
    if raw is None:
        raise RuleParameterValidationError(f"{key} is required.")
    text = str(raw).strip()
    if not _TIME_RE.match(text):
        raise RuleParameterValidationError(
            f"{key} must be a 24-hour time in HH:MM format."
        )
    return text


def validate_rule_parameters(
    payload: dict[str, Any],
    *,
    known_keys: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate and coerce recognized rule-parameter keys atomically.

    Returns a dict ready for persistence. Raises
    ``RuleParameterValidationError`` if any provided value is invalid.
    Unknown keys outside ``known_keys`` are ignored (same as the settings
    route filter). Keys present in the payload but not in known_keys are
    not returned.
    """
    defaults = known_keys if known_keys is not None else DEFAULT_RULE_PARAMETERS
    result: dict[str, Any] = {}
    errors: list[str] = []

    for key in defaults:
        if key not in payload:
            continue
        raw = payload[key]
        default = defaults[key]
        try:
            if key in ("truck_ban_start", "truck_ban_end"):
                result[key] = _validate_time(raw, key=key)
            elif key == "truck_ban_classes":
                if isinstance(raw, str):
                    import json

                    try:
                        raw = json.loads(raw)
                    except json.JSONDecodeError as exc:
                        raise RuleParameterValidationError(
                            f"{key} must be a JSON list of class names."
                        ) from exc
                if not isinstance(raw, list) or not all(
                    isinstance(item, str) and item for item in raw
                ):
                    raise RuleParameterValidationError(
                        f"{key} must be a list of non-empty class names."
                    )
                result[key] = list(raw)
            elif isinstance(default, bool):
                if isinstance(raw, bool):
                    result[key] = raw
                elif isinstance(raw, str) and raw.strip().lower() in (
                    "1",
                    "true",
                    "yes",
                    "0",
                    "false",
                    "no",
                ):
                    result[key] = raw.strip().lower() in ("1", "true", "yes")
                else:
                    raise RuleParameterValidationError(f"{key} must be a boolean.")
            elif isinstance(default, int) and not isinstance(default, bool):
                value = _parse_finite_number(raw, key=key)
                if not value.is_integer():
                    raise RuleParameterValidationError(f"{key} must be an integer.")
                ivalue = int(value)
                bounds = RULE_PARAMETER_BOUNDS.get(key)
                if bounds is not None:
                    if ivalue < bounds["min"] or (
                        "max" in bounds and ivalue > bounds["max"]
                    ):
                        raise RuleParameterValidationError(
                            _range_message(key, bounds)
                        )
                result[key] = ivalue
            elif isinstance(default, float):
                value = _parse_finite_number(raw, key=key)
                bounds = RULE_PARAMETER_BOUNDS.get(key)
                if bounds is not None:
                    if value < bounds["min"] or (
                        "max" in bounds and value > bounds["max"]
                    ):
                        raise RuleParameterValidationError(
                            _range_message(key, bounds)
                        )
                result[key] = value
            else:
                result[key] = str(raw)
        except RuleParameterValidationError as exc:
            errors.append(str(exc))

    if errors:
        raise RuleParameterValidationError("; ".join(errors))
    return result


def _range_message(key: str, bounds: dict[str, float]) -> str:
    if "max" in bounds:
        return f"{key} must be between {bounds['min']} and {bounds['max']}."
    return f"{key} must be at least {bounds['min']}."
