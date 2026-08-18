"""
core/congestion.py
==================

Hermosa Connect congestion analysis (PROPOSED / CONFIGURABLE).

Design notes
------------
* This module analyzes traffic *observations* over time, not isolated frames.
  A single stopped vehicle is explicitly NOT treated as congestion.
* The congestion signal is intentionally derived from a PLUGGABLE observation
  source. For the v0.1 expansion / reverse-pitch demo we ship a clearly-labeled
  DEMO observation generator. The same pipeline accepts real per-frame vehicle
  counts (e.g. from the existing detection/tracking pipeline or live RTSP) by
  supplying a different observation source.
* Thresholds below are PROPOSED starting points, not established operational or
  legal thresholds. They are centralized here and should be made configurable
  (system_settings) before any real deployment.

Decision classes (per instruction taxonomy):
  EXISTING     - none (new capability)
  FROZEN       - none
  PROPOSED     - the algorithm + thresholds in this file
  CONFIGURABLE - CONGESTION_THRESHOLDS, WINDOW, MIN_PERSISTENCE
  TBD          - real detection -> observation mapping for production
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable, Sequence

import database.db as db


# ---------------------------------------------------------------------------
# CONFIGURABLE / PROPOSED parameters (do NOT present as finalized thresholds)
# ---------------------------------------------------------------------------

# Minimum fraction of a time window that must show elevated density before we
# call it a congestion *event* (persistence requirement -> not a single frame).
MIN_CONGESTED_FRACTION = 0.5

# Density score (0..1) at/above which a single observation window is "congested".
# PROPOSED starting value; TBD for production tuning.
DENSITY_CONGESTED = 0.6
DENSITY_BUILDING = 0.4

# How many consecutive *congested* observations are required before an event is
# opened (guards against a single stopped vehicle / transient spike).
MIN_PERSISTENCE = 2

# Observation window length in seconds used by the demo generator / ingestion.
WINDOW_SECONDS = 300

SEVERITY_BANDS = {
    "low": 0.6,
    "moderate": 0.7,
    "heavy": 0.82,
    "severe": 0.92,
}


@dataclass
class Observation:
    camera_id: int | None
    location: str | None
    obs_time: datetime
    vehicle_count: int = 0
    moving_count: int = 0
    stationary_count: int = 0
    avg_speed_kmh: float = 0.0
    density_score: float = 0.0
    source: str = "demo"


@dataclass
class CongestionEvent:
    camera_id: int | None
    location: str | None
    severity: str
    state: str  # building | congested | improving | cleared
    started_at: datetime
    ended_at: datetime | None = None
    duration_minutes: float | None = None
    peak_vehicle_count: int = 0
    avg_density_score: float = 0.0


def severity_from_density(density: float) -> str:
    """Map a density score to the highest severity band it meets (PROPOSED)."""
    chosen = "low"
    for sev, threshold in sorted(SEVERITY_BANDS.items(), key=lambda kv: kv[1]):
        if density >= threshold:
            chosen = sev
    return chosen


def classify_window(obs: Observation) -> str:
    """Classify a single observation window.

    Returns one of: 'normal', 'building', 'congested'.
    A single observation can only be 'building'/'congested' by density; the
    temporal *event* logic (below) enforces persistence.
    """
    if obs.density_score >= DENSITY_CONGESTED:
        return "congested"
    if obs.density_score >= DENSITY_BUILDING:
        return "building"
    return "normal"


def detect_events(observations: Sequence[Observation]) -> list[CongestionEvent]:
    """Temporal analysis: turn an observation series into congestion events.

    Persistence rule: an event opens only after MIN_PERSISTENCE consecutive
    congested/building windows, and only if at least MIN_CONGESTED_FRACTION of
    the active window span is congested. This is what prevents a single stopped
    vehicle from becoming a "congestion event".
    """
    if not observations:
        return []

    observations = sorted(observations, key=lambda o: o.obs_time)
    events: list[CongestionEvent] = []
    i = 0
    n = len(observations)

    while i < n:
        # find a run of elevated-density windows
        run: list[Observation] = []
        while i < n and classify_window(observations[i]) in ("building", "congested"):
            run.append(observations[i])
            i += 1
        i += 1  # skip the first normal window after the run

        if len(run) < MIN_PERSISTENCE:
            continue

        congested = [o for o in run if classify_window(o) == "congested"]
        if len(congested) / len(run) < MIN_CONGESTED_FRACTION:
            continue

        started = run[0].obs_time
        ended = run[-1].obs_time
        peak = max((o.vehicle_count for o in run), default=0)
        avg_density = sum(o.density_score for o in run) / len(run)
        duration = (ended - started).total_seconds() / 60.0 + (WINDOW_SECONDS / 60.0)

        # state within the run: building -> congested -> improving (simple view)
        states = [classify_window(o) for o in run]
        if "congested" in states:
            state = "congested" if states.count("congested") >= len(states) / 2 else "improving"
        else:
            state = "building"

        events.append(
            CongestionEvent(
                camera_id=run[0].camera_id,
                location=run[0].location,
                severity=severity_from_density(avg_density),
                state=state,
                started_at=started,
                ended_at=ended,
                duration_minutes=round(duration, 1),
                peak_vehicle_count=peak,
                avg_density_score=round(avg_density, 3),
            )
        )
    return events


def compute_hotspots(
    events: Sequence[CongestionEvent],
    observations: Sequence[Observation],
) -> list[dict[str, Any]]:
    """Spatial analysis: aggregate recurrent congestion into ranked hotspots.

    Frequency score (0..1) blends how often a location appears in events with
    how long congestion persists there. PROPOSED weighting.
    """
    by_loc: dict[str, list[CongestionEvent]] = {}
    for ev in events:
        loc = ev.location or f"camera:{ev.camera_id}"
        by_loc.setdefault(loc, []).append(ev)

    hotspots: list[dict[str, Any]] = []
    for loc, loc_events in by_loc.items():
        total_duration = sum((e.duration_minutes or 0) for e in loc_events)
        peak_sev = max(
            (SEVERITY_BANDS.get(e.severity, 0) for e in loc_events), default=0
        )
        # frequency = normalized event count * persistence factor
        freq = min(1.0, len(loc_events) / max(1, len(events))) * min(
            1.0, total_duration / max(1.0, total_duration + 30.0)
        )
        camera_id = next((e.camera_id for e in loc_events if e.camera_id), None)
        hotspots.append(
            {
                "location": loc,
                "camera_id": camera_id,
                "frequency_score": round(freq, 3),
                "avg_duration_minutes": round(total_duration / len(loc_events), 1),
                "peak_severity": max(loc_events, key=lambda e: SEVERITY_BANDS.get(e.severity, 0)).severity,
            }
        )

    hotspots.sort(key=lambda h: h["frequency_score"], reverse=True)
    return hotspots


# ---------------------------------------------------------------------------
# DEMO observation source (clearly labeled; for pitch / no-RTSP environments)
# ---------------------------------------------------------------------------

def demo_observation_source(
    cameras: list[dict[str, Any]],
    start: datetime,
    steps: int = 24,
    seed: int | None = None,
) -> Callable[[], Iterable[Observation]]:
    """Return a generator factory producing DEMO observations for each camera.

    !!! DEMO DATA ONLY — not real CCTV/RTSP. !!!
    Simulates a rush-hour-like density curve per camera.
    """
    rng = random.Random(seed)

    def generate() -> Iterable[Observation]:
        for step in range(steps):
            t = start + timedelta(seconds=step * WINDOW_SECONDS)
            # rush-hour-ish bell curve across the window
            phase = step / max(1, steps - 1)
            rush = 0.5 + 0.45 * (1 - abs(phase - 0.5) * 2)  # 0.5..0.95..0.5
            for cam in cameras:
                noise = rng.uniform(-0.1, 0.1)
                density = max(0.0, min(1.0, rush + noise + rng.uniform(-0.15, 0.15)))
                vehicles = int(20 + density * 120)
                moving = int(vehicles * max(0.1, 1 - density))
                stationary = vehicles - moving
                yield Observation(
                    camera_id=cam.get("id"),
                    location=cam.get("location") or cam.get("name"),
                    obs_time=t,
                    vehicle_count=vehicles,
                    moving_count=moving,
                    stationary_count=stationary,
                    avg_speed_kmh=round(max(2.0, 45 * (1 - density)), 1),
                    density_score=round(density, 3),
                    source="demo",
                )

    return generate


# ---------------------------------------------------------------------------
# Persistence helpers (integrate analysis results into the TAVIDM DB)
# ---------------------------------------------------------------------------

def persist_demo_analysis(
    cameras: list[dict[str, Any]],
    start: datetime | None = None,
    steps: int = 24,
    seed: int | None = 42,
) -> dict[str, int]:
    """Generate DEMO observations, detect events, compute + store hotspots.

    Returns a small summary (counts). Clearly DEMO data.
    """
    start = start or datetime.now().replace(hour=7, minute=0, second=0, microsecond=0)
    gen = demo_observation_source(cameras, start, steps=steps, seed=seed)
    observations = list(gen())

    # persist raw observations
    for o in observations:
        db.insert_congestion_observation(
            o.camera_id,
            o.location,
            o.obs_time.strftime("%Y-%m-%d %H:%M:%S"),
            vehicle_count=o.vehicle_count,
            moving_count=o.moving_count,
            stationary_count=o.stationary_count,
            avg_speed_kmh=o.avg_speed_kmh,
            density_score=o.density_score,
            source="demo",
        )

    events = detect_events(observations)
    for ev in events:
        db.insert_congestion_event(
            ev.camera_id,
            ev.location,
            ev.severity,
            ev.state,
            ev.started_at.strftime("%Y-%m-%d %H:%M:%S"),
            ended_at=ev.ended_at.strftime("%Y-%m-%d %H:%M:%S") if ev.ended_at else None,
            duration_minutes=ev.duration_minutes,
            peak_vehicle_count=ev.peak_vehicle_count,
            avg_density_score=ev.avg_density_score,
        )

    hotspots = compute_hotspots(events, observations)
    db.replace_hotspots(hotspots)

    return {
        "observations": len(observations),
        "events": len(events),
        "hotspots": len(hotspots),
    }
