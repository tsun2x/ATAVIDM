"""Per-source geometry profiles for rule thresholds.

Removes dependence on one global pixel threshold for all cameras/resolutions.
Physical speed/distance are only reported when genuine calibration exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.rule_types import GeometryMode


# Reference frame used when converting legacy pixel defaults → normalized units.
_LEGACY_REF_WIDTH = 1920.0
_LEGACY_REF_HEIGHT = 1080.0


@dataclass
class GeometryProfile:
    """Resolved geometry for one video/camera processing run."""

    frame_width: int
    frame_height: int
    mode: GeometryMode = GeometryMode.NORMALIZED
    # Displacement / speed expressed in frame-diagonal fractions per second
    # unless ``mode`` is CALIBRATED (then meters when meters_per_norm is set).
    stationary_norm_per_sec: float = 0.0
    min_direction_norm: float = 0.0
    # Optional calibrated scale: meters corresponding to 1.0 normalized diagonal.
    meters_per_norm: float | None = None
    # Homography 3x3 row-major when provided (road-plane calibration).
    homography: tuple[float, ...] | None = None
    source_overrides: dict[str, Any] = field(default_factory=dict)
    zone_polygons: dict[str, list[list[float]]] = field(default_factory=dict)
    diagnostic: str = ""

    @property
    def diagonal_px(self) -> float:
        return float((self.frame_width**2 + self.frame_height**2) ** 0.5) or 1.0

    def px_to_norm(self, px: float) -> float:
        return float(px) / self.diagonal_px

    def norm_to_px(self, norm: float) -> float:
        return float(norm) * self.diagonal_px

    def stationary_px_per_sec(self) -> float:
        """Pixel speed threshold equivalent for trackers still using px units."""
        return self.norm_to_px(self.stationary_norm_per_sec)

    def min_direction_px(self) -> float:
        return self.norm_to_px(self.min_direction_norm)

    def can_report_physical(self) -> bool:
        return (
            self.mode is GeometryMode.CALIBRATED
            and self.meters_per_norm is not None
            and self.meters_per_norm > 0
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "frame_width": self.frame_width,
            "frame_height": self.frame_height,
            "mode": self.mode.value,
            "stationary_norm_per_sec": self.stationary_norm_per_sec,
            "min_direction_norm": self.min_direction_norm,
            "meters_per_norm": self.meters_per_norm,
            "homography_present": self.homography is not None,
            "source_overrides": dict(self.source_overrides),
            "zone_keys": sorted(k for k, v in self.zone_polygons.items() if v),
            "diagnostic": self.diagnostic,
            "stationary_px_per_sec_resolved": self.stationary_px_per_sec(),
            "min_direction_px_resolved": self.min_direction_px(),
        }


def _legacy_px_to_norm(px: float, ref_diagonal: float | None = None) -> float:
    diag = ref_diagonal or (( _LEGACY_REF_WIDTH**2 + _LEGACY_REF_HEIGHT**2) ** 0.5)
    return float(px) / float(diag)


def build_geometry_profile(
    frame_width: int,
    frame_height: int,
    *,
    rule_params: dict[str, Any] | None = None,
    source_overrides: dict[str, Any] | None = None,
    zone_polygons: dict[str, list[list[float]]] | None = None,
    homography: list[float] | tuple[float, ...] | None = None,
    meters_per_norm: float | None = None,
) -> GeometryProfile:
    """Build a resolved geometry profile for one processing source.

    Priority:
    1. Calibrated (homography or meters_per_norm provided) → CALIBRATED
    2. Otherwise NORMALIZED from frame size + legacy defaults converted via
       reference diagonal (visible fallback values remain in the snapshot)
    """
    params = dict(rule_params or {})
    overrides = dict(source_overrides or {})
    zones = {k: list(v) for k, v in (zone_polygons or {}).items() if v}

    legacy_stationary_px = float(overrides.get("stationary_px", params.get("stationary_px", 8.0)))
    legacy_min_dir_px = float(
        overrides.get("min_direction_px", params.get("min_direction_px", 40.0))
    )

    # Allow direct normalized overrides from source config.
    if "stationary_norm_per_sec" in overrides:
        stationary_norm = float(overrides["stationary_norm_per_sec"])
    else:
        stationary_norm = _legacy_px_to_norm(legacy_stationary_px)

    if "min_direction_norm" in overrides:
        min_dir_norm = float(overrides["min_direction_norm"])
    else:
        min_dir_norm = _legacy_px_to_norm(legacy_min_dir_px)

    homo_tuple: tuple[float, ...] | None = None
    if homography is not None and len(homography) == 9:
        homo_tuple = tuple(float(x) for x in homography)

    scale = overrides.get("meters_per_norm", meters_per_norm)
    if scale is not None:
        scale = float(scale)

    if homo_tuple is not None or (scale is not None and scale > 0):
        mode = GeometryMode.CALIBRATED
        diagnostic = (
            "Using calibrated road-plane geometry "
            f"(homography={'yes' if homo_tuple else 'no'}, "
            f"meters_per_norm={scale})."
        )
    else:
        mode = GeometryMode.NORMALIZED
        diagnostic = (
            "Using frame-normalized thresholds derived from legacy pixel defaults "
            f"(stationary_px={legacy_stationary_px} @ {_LEGACY_REF_WIDTH}x{_LEGACY_REF_HEIGHT} "
            f"→ norm={stationary_norm:.6f}; "
            f"min_direction_px={legacy_min_dir_px} → norm={min_dir_norm:.6f}). "
            "Physical speed/distance are not reported."
        )
        # If caller explicitly requested legacy_fallback diagnostic naming:
        if overrides.get("force_legacy_fallback"):
            mode = GeometryMode.LEGACY_FALLBACK
            diagnostic = (
                "LEGACY FALLBACK: applying absolute pixel thresholds without "
                f"normalization (stationary_px={legacy_stationary_px}, "
                f"min_direction_px={legacy_min_dir_px}). Prefer normalized mode."
            )
            # Keep norm fields consistent with current frame so helpers still work.
            diag = ((frame_width**2 + frame_height**2) ** 0.5) or 1.0
            stationary_norm = legacy_stationary_px / diag
            min_dir_norm = legacy_min_dir_px / diag

    return GeometryProfile(
        frame_width=int(frame_width),
        frame_height=int(frame_height),
        mode=mode,
        stationary_norm_per_sec=stationary_norm,
        min_direction_norm=min_dir_norm,
        meters_per_norm=scale if mode is GeometryMode.CALIBRATED else None,
        homography=homo_tuple,
        source_overrides=overrides,
        zone_polygons=zones,
        diagnostic=diagnostic,
    )
