"""Standalone event-time provenance helpers (Stage C2).

Pure, side-effect-free helpers for candidate violation event instants.
This module does **not**:
  - perform CCTV timestamp OCR or visual reliability assessment
  - authenticate reviewers (supplied identities are preserved claims only)
  - write to the database, evidence files, or network
  - import Flask, the video pipeline, or the recurrence matcher
  - treat processing, upload, file-modification, or printing times as
    automatic event-time substitutes
  - grant plate verification, legal classification, or recurrence eligibility

Caller supplies reliability explicitly. Conflicting sources are never
auto-selected. Confirmation/correction returns a new structured result and
retains prior candidate/source information.

Distinct from ``core/recording_time.normalize_recorded_at`` (upload-path
video ``recorded_at`` normalization to naive wall-clock strings) and from
``database.sqlite_adapter.record_event_time`` (persistence).
"""

from __future__ import annotations

import enum
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# ---------------------------------------------------------------------------
# Source / reliability / usability contracts
# ---------------------------------------------------------------------------


class EventTimeSource(str, enum.Enum):
    """Provenance of a candidate or confirmed event instant.

    Matches persistence labels used by ``record_event_time`` for the four
    event-capable sources. Non-event operational clocks are listed so callers
    can name them explicitly — they never become automatic event time.
    """

    CCTV_TIMESTAMP = "cctv_timestamp"
    VIDEO_METADATA = "video_metadata"
    USER_ENTRY = "user_entry"
    USER_CONFIRMATION = "user_confirmation"
    # Operational clocks — never automatic event-time substitutes:
    PROCESSING = "processing"
    UPLOAD = "upload"
    FILE_MODIFICATION = "file_modification"
    PRINTING = "printing"


EVENT_CAPABLE_SOURCES: frozenset[EventTimeSource] = frozenset(
    {
        EventTimeSource.CCTV_TIMESTAMP,
        EventTimeSource.VIDEO_METADATA,
        EventTimeSource.USER_ENTRY,
        EventTimeSource.USER_CONFIRMATION,
    }
)

NON_EVENT_SOURCES: frozenset[EventTimeSource] = frozenset(
    {
        EventTimeSource.PROCESSING,
        EventTimeSource.UPLOAD,
        EventTimeSource.FILE_MODIFICATION,
        EventTimeSource.PRINTING,
    }
)


class CctvReliability(str, enum.Enum):
    """Caller-assessed reliability of a CCTV-derived timestamp.

    This module does not OCR overlays or judge visual quality; the future
    caller supplies one of these states with the candidate value.
    """

    RELIABLE = "reliable"
    UNRELIABLE = "unreliable"
    UNREADABLE = "unreadable"
    MISSING = "missing"


class EventTimeUsability(str, enum.Enum):
    """Whether the structured result exposes a usable event instant."""

    USABLE = "usable"
    REQUIRES_CONFIRMATION = "requires_confirmation"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"
    CONFLICT = "conflict"


class ParseStatus(str, enum.Enum):
    """Outcome of the narrow ISO-8601 parsing interface."""

    OK = "ok"
    INVALID = "invalid"
    UNRESOLVED = "unresolved"
    AMBIGUOUS_LOCAL = "ambiguous_local"
    NONEXISTENT_LOCAL = "nonexistent_local"


# ---------------------------------------------------------------------------
# Structured values
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EventTimeInstant:
    """Timezone-aware event instant with preserved offset / source-tz labels.

    ``value`` must be timezone-aware. Naive datetimes are rejected at
    construction sites rather than silently localized to the host TZ.
    """

    value: datetime
    offset_label: str | None = None
    source_timezone: str | None = None

    def __post_init__(self) -> None:
        if self.value.tzinfo is None:
            raise ValueError(
                "EventTimeInstant requires a timezone-aware datetime; "
                "naive values are not accepted."
            )


@dataclass(frozen=True)
class ReviewConfirmation:
    """Reviewer confirmation metadata — distinct from the event instant.

    ``confirmed_by`` is a supplied identity claim, not authentication.
    ``confirmed_at`` is review/confirmation wall time, never event time.
    """

    confirmed_by: str
    confirmed_at: datetime

    def __post_init__(self) -> None:
        if not str(self.confirmed_by).strip():
            raise ValueError("confirmed_by must be a non-empty identity claim.")
        if self.confirmed_at.tzinfo is None:
            raise ValueError(
                "confirmed_at must be timezone-aware; do not use naive host time."
            )


@dataclass(frozen=True)
class EventTimeClaim:
    """One caller-supplied time claim before resolution.

    ``raw_text`` and ``instant`` are mutually supportive: provide an aware
    ``instant``, or ``raw_text`` for ISO parsing (optionally with
    ``source_timezone`` for offset-less strings).
    """

    source: EventTimeSource
    instant: EventTimeInstant | None = None
    raw_text: str | None = None
    source_timezone: str | None = None
    reliability: CctvReliability | None = None
    video_relative_sec: float | None = None
    notes: str | None = None


@dataclass(frozen=True)
class ParseResult:
    """Result of ``parse_iso8601_event_instant``."""

    status: ParseStatus
    instant: EventTimeInstant | None = None
    reason: str | None = None


@dataclass(frozen=True)
class EventTimeResult:
    """Structured event-time provenance result (immutable).

    Confirmation does not erase ``original_source`` / ``candidate``.
    Corrections return a new result with ``previous`` pointing at the prior
    snapshot; callers own persistence of that history.

    ``contributing_claims`` retains every input claim for multi-source
    resolution (including unresolved ones). ``conflict_claims`` is set when
    usability is ``CONFLICT``. Neither invents a source-priority order.
    """

    usability: EventTimeUsability
    candidate: EventTimeInstant | None = None
    original_source: EventTimeSource | None = None
    reliability: CctvReliability | None = None
    confirmed_event_time: EventTimeInstant | None = None
    confirmation: ReviewConfirmation | None = None
    video_relative_sec: float | None = None
    previous: EventTimeResult | None = None
    contributing_claims: tuple[EventTimeClaim, ...] = ()
    conflict_claims: tuple[EventTimeClaim, ...] = ()
    reason: str | None = None
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    @property
    def event_instant(self) -> EventTimeInstant | None:
        """Effective confirmed instant, else candidate when usability is USABLE."""
        if self.confirmed_event_time is not None:
            return self.confirmed_event_time
        if self.usability is EventTimeUsability.USABLE:
            return self.candidate
        return None

    @property
    def is_ready_for_recurrence_time_evaluation(self) -> bool:
        """True only when a usable event instant is present.

        Narrow time-readiness signal only — not plate, legal, or case
        eligibility for recurrence matching.
        """
        return (
            self.usability is EventTimeUsability.USABLE
            and self.event_instant is not None
        )

    @property
    def grants_recurrence_eligibility(self) -> bool:
        """Usable event time alone never grants recurrence eligibility."""
        return False

    @property
    def grants_plate_verification(self) -> bool:
        return False

    @property
    def grants_legal_classification(self) -> bool:
        return False


# ---------------------------------------------------------------------------
# ISO-8601 parsing (narrow, documented)
# ---------------------------------------------------------------------------

# Explicit-offset / Z forms only for fully specified instants without a
# separate source timezone. Fractional seconds optional.
_ISO_AWARE_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})"
    r"[T ]"
    r"(?P<time>\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?)"
    r"(?P<off>Z|[+-]\d{2}:\d{2})$"
)

# Offset-less wall time — usable only with an explicit source_timezone.
_ISO_NAIVE_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})"
    r"[T ]"
    r"(?P<time>\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?)$"
)

# Rejected patterns (ambiguous day/month, slash dates, etc.) are anything
# that does not match the two forms above.


def _offset_label_from_datetime(dt: datetime) -> str:
    off = dt.utcoffset()
    if off is None:
        return ""
    if off == timedelta(0) and (
        dt.tzinfo is timezone.utc
        or getattr(dt.tzinfo, "key", None) == "UTC"
        or str(dt.tzinfo) in ("UTC", "utc")
    ):
        # Preserve Z vs +00:00 only when caller used UTC; otherwise use ±HH:MM.
        return "Z" if dt.tzinfo is timezone.utc else "+00:00"
    total = int(off.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    hours, rem = divmod(total, 3600)
    minutes = rem // 60
    return f"{sign}{hours:02d}:{minutes:02d}"


# Narrow explicit-offset grammar: ``Z`` or ``±HH:MM`` with HH in 00–23 and
# MM in 00–59 (magnitude strictly less than 24 hours). Invalid minutes are
# rejected — never normalized (e.g. ``+08:60`` is INVALID, not ``+09:00``).
_MAX_OFFSET_HOURS = 23


def _parse_offset(token: str) -> tuple[timezone | None, str | None]:
    """Validate and parse an explicit offset token.

    Returns ``(timezone, None)`` on success or ``(None, reason)`` on failure.
    Does not raise for unsupported magnitudes or invalid minutes.
    """
    if token == "Z":
        return timezone.utc, None
    if len(token) != 6 or token[0] not in "+-" or token[3] != ":":
        return None, f"Unsupported offset syntax '{token}'."
    try:
        hours = int(token[1:3])
        minutes = int(token[4:6])
    except ValueError:
        return None, f"Invalid offset digits in '{token}'."
    if minutes > 59:
        return None, (
            f"Invalid offset minutes in '{token}': minutes must be 00-59 "
            "(not normalized)."
        )
    if hours > _MAX_OFFSET_HOURS:
        return None, (
            f"Invalid offset hours in '{token}': hours must be 00-23 "
            "(magnitude strictly less than 24 hours)."
        )
    sign = 1 if token[0] == "+" else -1
    return timezone(sign * timedelta(hours=hours, minutes=minutes)), None


def _parse_wall(date_s: str, time_s: str) -> datetime | None:
    """Parse Y-M-D + H:M:S[.fff] as a naive datetime; None if calendar-invalid."""
    if "." in time_s:
        base, frac = time_s.split(".", 1)
        fmt = "%Y-%m-%d %H:%M:%S"
        try:
            dt = datetime.strptime(f"{date_s} {base}", fmt)
        except ValueError:
            return None
        # Normalize fractional seconds to microseconds (truncate/pad).
        micro = int((frac + "000000")[:6])
        return dt.replace(microsecond=micro)
    try:
        return datetime.strptime(f"{date_s} {time_s}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _localize_with_named_zone(
    naive: datetime,
    zone_name: str,
) -> ParseResult:
    """Attach a named zone using stdlib zoneinfo; detect ambiguous/nonexistent."""
    try:
        tz = ZoneInfo(zone_name)
    except ZoneInfoNotFoundError:
        return ParseResult(
            status=ParseStatus.UNRESOLVED,
            reason=(
                f"Named timezone '{zone_name}' is not available in this "
                "environment; refusing to guess an offset."
            ),
        )
    except Exception as exc:  # pragma: no cover - defensive
        return ParseResult(
            status=ParseStatus.UNRESOLVED,
            reason=f"Named timezone '{zone_name}' could not be resolved: {exc!s}.",
        )

    aware0 = naive.replace(tzinfo=tz, fold=0)
    aware1 = naive.replace(tzinfo=tz, fold=1)
    if aware0.utcoffset() != aware1.utcoffset():
        return ParseResult(
            status=ParseStatus.AMBIGUOUS_LOCAL,
            reason=(
                f"Local time {naive.isoformat()} is ambiguous in '{zone_name}' "
                "(DST overlap); caller must supply an explicit offset."
            ),
        )

    # Nonexistent local time (DST gap): UTC round-trip changes the wall clock.
    roundtrip = aware0.astimezone(timezone.utc).astimezone(tz)
    wall = (
        roundtrip.year,
        roundtrip.month,
        roundtrip.day,
        roundtrip.hour,
        roundtrip.minute,
        roundtrip.second,
        roundtrip.microsecond,
    )
    expected = (
        naive.year,
        naive.month,
        naive.day,
        naive.hour,
        naive.minute,
        naive.second,
        naive.microsecond,
    )
    if wall != expected:
        return ParseResult(
            status=ParseStatus.NONEXISTENT_LOCAL,
            reason=(
                f"Local time {naive.isoformat()} does not exist in '{zone_name}' "
                "(DST gap); refusing to invent an instant."
            ),
        )

    instant = EventTimeInstant(
        value=aware0,
        offset_label=_offset_label_from_datetime(aware0),
        source_timezone=zone_name,
    )
    return ParseResult(status=ParseStatus.OK, instant=instant)


def parse_iso8601_event_instant(
    raw: str | None,
    *,
    source_timezone: str | None = None,
) -> ParseResult:
    """Parse a narrow ISO-8601 event-time string into an aware instant.

    Accepted forms:
      - ``YYYY-MM-DDTHH:MM:SS±HH:MM`` (space instead of ``T`` allowed)
      - ``YYYY-MM-DDTHH:MM:SS.ffffff±HH:MM``
      - ``YYYY-MM-DDTHH:MM:SSZ`` (and fractional Z)
      - Offset-less ``YYYY-MM-DDTHH:MM:SS[.ffffff]`` **only** when
        ``source_timezone`` is an explicit IANA name resolvable via
        ``zoneinfo.ZoneInfo``

    Accepted explicit offsets:
      - ``Z`` (UTC)
      - ``±HH:MM`` with ``HH`` in ``00``–``23`` and ``MM`` in ``00``–``59``
        (magnitude strictly less than 24 hours)
      - Invalid minutes (e.g. ``+08:60``) and unsupported magnitudes
        (e.g. ``+24:00``) → ``INVALID`` (never normalized, never raised)

    Rejected / unresolved:
      - Empty / missing input → ``UNRESOLVED``
      - Ambiguous day/month slash formats and other non-ISO shapes → ``INVALID``
      - Invalid calendar components → ``INVALID``
      - Offset-less string without ``source_timezone`` → ``UNRESOLVED``
        (never attach the host local timezone)
      - Unresolvable named zone → ``UNRESOLVED``
      - Ambiguous / nonexistent local times under a named zone → dedicated status

    Does not use the current clock. Does not assume Australia/Perth or
    Asia/Manila from the development machine.
    """
    if raw is None:
        return ParseResult(
            status=ParseStatus.UNRESOLVED,
            reason="No timestamp string was supplied.",
        )
    text = str(raw).strip()
    if not text:
        return ParseResult(
            status=ParseStatus.UNRESOLVED,
            reason="Timestamp string is empty.",
        )

    m_aware = _ISO_AWARE_RE.match(text)
    if m_aware:
        naive = _parse_wall(m_aware.group("date"), m_aware.group("time"))
        if naive is None:
            return ParseResult(
                status=ParseStatus.INVALID,
                reason=f"Invalid calendar date/time in '{text}'.",
            )
        tz, offset_error = _parse_offset(m_aware.group("off"))
        if tz is None:
            return ParseResult(
                status=ParseStatus.INVALID,
                reason=offset_error or f"Invalid offset in '{text}'.",
            )
        aware = naive.replace(tzinfo=tz)
        label = "Z" if m_aware.group("off") == "Z" else m_aware.group("off")
        return ParseResult(
            status=ParseStatus.OK,
            instant=EventTimeInstant(
                value=aware,
                offset_label=label,
                source_timezone=None,
            ),
        )

    m_naive = _ISO_NAIVE_RE.match(text)
    if m_naive:
        naive = _parse_wall(m_naive.group("date"), m_naive.group("time"))
        if naive is None:
            return ParseResult(
                status=ParseStatus.INVALID,
                reason=f"Invalid calendar date/time in '{text}'.",
            )
        if not source_timezone or not str(source_timezone).strip():
            return ParseResult(
                status=ParseStatus.UNRESOLVED,
                reason=(
                    "Timestamp has no offset/timezone; supply an explicit "
                    "source_timezone or an offset-qualified ISO-8601 string. "
                    "Host local timezone is never applied automatically."
                ),
            )
        return _localize_with_named_zone(naive, str(source_timezone).strip())

    return ParseResult(
        status=ParseStatus.INVALID,
        reason=(
            f"Unsupported timestamp format '{text}'. Expected narrow ISO-8601 "
            "with explicit offset/Z, or offset-less ISO wall time with an "
            "explicit source_timezone. Ambiguous day/month formats are rejected."
        ),
    )


def instant_from_aware_datetime(
    value: datetime,
    *,
    source_timezone: str | None = None,
) -> EventTimeInstant:
    """Wrap an already-aware datetime; reject naive values."""
    if value.tzinfo is None:
        raise ValueError(
            "Naive datetime refused: provide tz-aware value or parse via "
            "parse_iso8601_event_instant with an explicit offset/source_timezone."
        )
    return EventTimeInstant(
        value=value,
        offset_label=_offset_label_from_datetime(value),
        source_timezone=source_timezone,
    )


# ---------------------------------------------------------------------------
# Resolution helpers
# ---------------------------------------------------------------------------


def _claim_to_instant(claim: EventTimeClaim) -> ParseResult:
    if claim.instant is not None:
        return ParseResult(status=ParseStatus.OK, instant=claim.instant)
    if claim.raw_text is not None:
        return parse_iso8601_event_instant(
            claim.raw_text,
            source_timezone=claim.source_timezone,
        )
    return ParseResult(
        status=ParseStatus.UNRESOLVED,
        reason="Claim has neither an aware instant nor a parseable raw_text.",
    )


def missing_event_time(*, reason: str | None = None) -> EventTimeResult:
    """Return an unavailable result when no event time is known."""
    return EventTimeResult(
        usability=EventTimeUsability.UNAVAILABLE,
        reliability=CctvReliability.MISSING,
        reason=reason or "Event time is missing.",
        diagnostics={"kind": "missing"},
    )


def from_cctv_timestamp(
    *,
    reliability: CctvReliability,
    instant: EventTimeInstant | None = None,
    raw_text: str | None = None,
    source_timezone: str | None = None,
    video_relative_sec: float | None = None,
) -> EventTimeResult:
    """Build a result from a CCTV-derived timestamp and caller reliability.

    Reliable + resolvable aware time → ``USABLE`` with source retained.
    Unreliable / unreadable → ``REQUIRES_CONFIRMATION`` (candidate kept when
    parseable). Missing → ``UNAVAILABLE``. Invalid parse → ``INVALID``.
    """
    if reliability is CctvReliability.MISSING:
        return missing_event_time(
            reason="CCTV timestamp is missing; user entry or confirmation required."
        )

    parsed = _claim_to_instant(
        EventTimeClaim(
            source=EventTimeSource.CCTV_TIMESTAMP,
            instant=instant,
            raw_text=raw_text,
            source_timezone=source_timezone,
            reliability=reliability,
            video_relative_sec=video_relative_sec,
        )
    )

    if reliability in (CctvReliability.UNRELIABLE, CctvReliability.UNREADABLE):
        candidate = parsed.instant if parsed.status is ParseStatus.OK else None
        return EventTimeResult(
            usability=EventTimeUsability.REQUIRES_CONFIRMATION,
            candidate=candidate,
            original_source=EventTimeSource.CCTV_TIMESTAMP,
            reliability=reliability,
            video_relative_sec=video_relative_sec,
            reason=(
                f"CCTV timestamp is {reliability.value}; confirmation or "
                "user entry is required before the value is usable."
            ),
            diagnostics={
                "parse_status": parsed.status.value,
                "parse_reason": parsed.reason,
            },
        )

    # RELIABLE
    if parsed.status is not ParseStatus.OK or parsed.instant is None:
        usability = (
            EventTimeUsability.INVALID
            if parsed.status is ParseStatus.INVALID
            else EventTimeUsability.UNAVAILABLE
        )
        return EventTimeResult(
            usability=usability,
            original_source=EventTimeSource.CCTV_TIMESTAMP,
            reliability=reliability,
            video_relative_sec=video_relative_sec,
            reason=parsed.reason
            or "Reliable CCTV claim could not be resolved to an aware instant.",
            diagnostics={"parse_status": parsed.status.value},
        )

    return EventTimeResult(
        usability=EventTimeUsability.USABLE,
        candidate=parsed.instant,
        original_source=EventTimeSource.CCTV_TIMESTAMP,
        reliability=reliability,
        video_relative_sec=video_relative_sec,
        reason="Reliable CCTV timestamp accepted as event-time candidate.",
        diagnostics={"parse_status": ParseStatus.OK.value},
    )


def from_video_metadata(
    *,
    instant: EventTimeInstant | None = None,
    raw_text: str | None = None,
    source_timezone: str | None = None,
    video_relative_sec: float | None = None,
) -> EventTimeResult:
    """Video metadata may supply a candidate but never silent trusted time."""
    parsed = _claim_to_instant(
        EventTimeClaim(
            source=EventTimeSource.VIDEO_METADATA,
            instant=instant,
            raw_text=raw_text,
            source_timezone=source_timezone,
            video_relative_sec=video_relative_sec,
        )
    )
    if parsed.status is not ParseStatus.OK or parsed.instant is None:
        usability = (
            EventTimeUsability.INVALID
            if parsed.status is ParseStatus.INVALID
            else EventTimeUsability.REQUIRES_CONFIRMATION
        )
        return EventTimeResult(
            usability=usability,
            original_source=EventTimeSource.VIDEO_METADATA,
            video_relative_sec=video_relative_sec,
            reason=parsed.reason
            or "Video metadata did not yield a resolvable candidate.",
            diagnostics={"parse_status": parsed.status.value},
        )

    return EventTimeResult(
        usability=EventTimeUsability.REQUIRES_CONFIRMATION,
        candidate=parsed.instant,
        original_source=EventTimeSource.VIDEO_METADATA,
        video_relative_sec=video_relative_sec,
        reason=(
            "Video metadata provides a candidate only; explicit confirmation "
            "is required before the value is usable as event time."
        ),
        diagnostics={"parse_status": ParseStatus.OK.value},
    )


def from_user_entry(
    *,
    instant: EventTimeInstant | None = None,
    raw_text: str | None = None,
    source_timezone: str | None = None,
    video_relative_sec: float | None = None,
) -> EventTimeResult:
    """User-entered candidate — still requires confirmation metadata to become usable.

    A bare user entry is not auto-confirmed; call ``confirm_event_time``.
    """
    parsed = _claim_to_instant(
        EventTimeClaim(
            source=EventTimeSource.USER_ENTRY,
            instant=instant,
            raw_text=raw_text,
            source_timezone=source_timezone,
            video_relative_sec=video_relative_sec,
        )
    )
    if parsed.status is not ParseStatus.OK or parsed.instant is None:
        usability = (
            EventTimeUsability.INVALID
            if parsed.status is ParseStatus.INVALID
            else EventTimeUsability.UNAVAILABLE
        )
        return EventTimeResult(
            usability=usability,
            original_source=EventTimeSource.USER_ENTRY,
            video_relative_sec=video_relative_sec,
            reason=parsed.reason or "User entry could not be resolved.",
            diagnostics={"parse_status": parsed.status.value},
        )

    return EventTimeResult(
        usability=EventTimeUsability.REQUIRES_CONFIRMATION,
        candidate=parsed.instant,
        original_source=EventTimeSource.USER_ENTRY,
        video_relative_sec=video_relative_sec,
        reason=(
            "User-entered candidate recorded; confirmation metadata is "
            "required before the value is usable."
        ),
        diagnostics={"parse_status": ParseStatus.OK.value},
    )


def reject_non_event_source(
    source: EventTimeSource,
    *,
    instant: EventTimeInstant | None = None,
    raw_text: str | None = None,
) -> EventTimeResult:
    """Processing/upload/file-modification/printing times are not event time."""
    if source not in NON_EVENT_SOURCES:
        raise ValueError(
            f"{source.value} is event-capable; use resolve_event_time / "
            "from_* helpers instead of reject_non_event_source."
        )
    return EventTimeResult(
        usability=EventTimeUsability.UNAVAILABLE,
        candidate=None,
        original_source=source,
        reason=(
            f"{source.value} timestamps are operational clocks and never "
            "become automatic event-time substitutes."
        ),
        diagnostics={
            "rejected_source": source.value,
            "had_instant": instant is not None,
            "had_raw_text": raw_text is not None,
        },
    )


def resolve_event_time(
    claims: Sequence[EventTimeClaim],
) -> EventTimeResult:
    """Resolve one or more claims without automatic conflict selection.

    - Empty claims → unavailable.
    - Any non-event operational source alone → unavailable (rejected).
    - Multiple event-capable claims with differing resolved instants →
      ``CONFLICT`` (no auto-pick); all claims retained in
      ``contributing_claims`` / ``conflict_claims``.
    - Multiple event-capable claims that agree (or mix resolved/unresolved)
      → ``REQUIRES_CONFIRMATION`` with the agreed candidate when known.
      Order is irrelevant; no source-priority policy is applied. Single-source
      reliable-CCTV behavior is unchanged when only one claim is present.
    - Single CCTV / metadata / user_entry claim → delegated to ``from_*``.
    """
    claim_list = list(claims)
    if not claim_list:
        return missing_event_time(reason="No event-time claims were supplied.")

    # Materialize a shallow copy of claim tuples so caller-owned sequences
    # are never mutated (claims themselves are frozen).
    non_event = [c for c in claim_list if c.source in NON_EVENT_SOURCES]
    event_claims = [c for c in claim_list if c.source in EVENT_CAPABLE_SOURCES]

    if non_event and not event_claims:
        return reject_non_event_source(
            non_event[0].source,
            instant=non_event[0].instant,
            raw_text=non_event[0].raw_text,
        )

    if len(event_claims) > 1:
        return _resolve_multi_source(tuple(event_claims))

    claim = event_claims[0]
    if claim.source is EventTimeSource.CCTV_TIMESTAMP:
        reliability = claim.reliability or CctvReliability.MISSING
        return from_cctv_timestamp(
            reliability=reliability,
            instant=claim.instant,
            raw_text=claim.raw_text,
            source_timezone=claim.source_timezone,
            video_relative_sec=claim.video_relative_sec,
        )
    if claim.source is EventTimeSource.VIDEO_METADATA:
        return from_video_metadata(
            instant=claim.instant,
            raw_text=claim.raw_text,
            source_timezone=claim.source_timezone,
            video_relative_sec=claim.video_relative_sec,
        )
    if claim.source is EventTimeSource.USER_ENTRY:
        return from_user_entry(
            instant=claim.instant,
            raw_text=claim.raw_text,
            source_timezone=claim.source_timezone,
            video_relative_sec=claim.video_relative_sec,
        )
    if claim.source is EventTimeSource.USER_CONFIRMATION:
        return EventTimeResult(
            usability=EventTimeUsability.REQUIRES_CONFIRMATION,
            original_source=EventTimeSource.USER_CONFIRMATION,
            contributing_claims=(claim,),
            reason=(
                "USER_CONFIRMATION claims must go through confirm_event_time "
                "with full confirmation metadata."
            ),
            diagnostics={},
        )

    return missing_event_time()


def _resolve_multi_source(
    event_claims: tuple[EventTimeClaim, ...],
) -> EventTimeResult:
    """Order-independent multi-claim resolution; never invents source priority."""
    resolved: list[tuple[EventTimeClaim, EventTimeInstant]] = []
    unresolved: list[tuple[EventTimeClaim, ParseResult]] = []
    for claim in event_claims:
        parsed = _claim_to_instant(claim)
        if parsed.status is ParseStatus.OK and parsed.instant is not None:
            resolved.append((claim, parsed.instant))
        else:
            unresolved.append((claim, parsed))

    provenance = {
        "claim_count": len(event_claims),
        "resolved_count": len(resolved),
        "unresolved_count": len(unresolved),
        "resolved_sources": sorted({c.source.value for c, _ in resolved}),
        "unresolved_sources": sorted({c.source.value for c, _ in unresolved}),
        "unresolved_reasons": [
            p.reason or f"{c.source.value} unresolved" for c, p in unresolved
        ],
    }

    if len(resolved) >= 2:
        first_utc = resolved[0][1].value.astimezone(timezone.utc)
        if any(
            inst.value.astimezone(timezone.utc) != first_utc
            for _, inst in resolved[1:]
        ):
            return EventTimeResult(
                usability=EventTimeUsability.CONFLICT,
                contributing_claims=event_claims,
                conflict_claims=event_claims,
                reason=(
                    "Conflicting event-time sources differ; explicit "
                    "confirmation must choose or correct a value. "
                    "No automatic selection was performed."
                ),
                diagnostics={
                    **provenance,
                    "instants_utc": sorted(
                        {
                            inst.value.astimezone(timezone.utc).isoformat()
                            for _, inst in resolved
                        }
                    ),
                },
            )

        # Agreeing resolved instants — confirmation required because choosing
        # which claim's single-source readiness rules to apply would invent
        # an unapproved source-priority policy (order must not matter).
        agreed = resolved[0][1]
        return EventTimeResult(
            usability=EventTimeUsability.REQUIRES_CONFIRMATION,
            candidate=agreed,
            contributing_claims=event_claims,
            reason=(
                "Multiple event-time claims agree on an absolute instant, but "
                "no source-priority policy is approved; confirmation is "
                "required before the value is usable."
            ),
            diagnostics={
                **provenance,
                "agreement": True,
                "agreed_instant_utc": first_utc.isoformat(),
            },
        )

    if len(resolved) == 1:
        # One resolved claim among several — retain unresolved provenance;
        # do not discard uncertainty by falling through to single-source USABLE.
        only_instant = resolved[0][1]
        return EventTimeResult(
            usability=EventTimeUsability.REQUIRES_CONFIRMATION,
            candidate=only_instant,
            contributing_claims=event_claims,
            reason=(
                "Multiple claims present with mixed resolved/unresolved "
                "provenance; confirmation is required. Unresolved claims "
                "were retained and not discarded."
            ),
            diagnostics={
                **provenance,
                "agreement": False,
                "partial_resolution": True,
            },
        )

    return EventTimeResult(
        usability=EventTimeUsability.REQUIRES_CONFIRMATION,
        contributing_claims=event_claims,
        reason=(
            "Multiple claims present but none fully resolved; "
            "confirmation or clearer input is required."
        ),
        diagnostics=provenance,
    )


def confirm_event_time(
    prior: EventTimeResult,
    *,
    confirmed_by: str | None,
    confirmed_at: datetime | None,
    accept_candidate: bool = True,
    corrected_instant: EventTimeInstant | None = None,
    corrected_raw_text: str | None = None,
    corrected_source_timezone: str | None = None,
    video_relative_sec: float | None = None,
) -> EventTimeResult:
    """Accept or correct a prior result; return a new snapshot.

    Side-effect-free: does not mutate ``prior``. Retains original candidate
    and ``original_source``. Sets ``previous`` to the prior result so
    correction history is preserved for later persistence.

    Reconfirmation selection (no new correction supplied):
      1. Retain ``prior.confirmed_event_time`` when present.
      2. Otherwise accept ``prior.candidate`` when present.
      3. Otherwise reject — a supplied correction is required.

    Incomplete confirmation metadata → returns a non-confirmed result
    (``REQUIRES_CONFIRMATION`` / ``INVALID``) rather than a false confirmation.
    ``confirmed_at`` is review time and must not be treated as event time.
    """
    rel_sec = (
        video_relative_sec
        if video_relative_sec is not None
        else prior.video_relative_sec
    )

    def _reject(
        *,
        usability: EventTimeUsability,
        reason: str,
        diagnostics: dict[str, Any],
        confirmation: ReviewConfirmation | None = None,
    ) -> EventTimeResult:
        return EventTimeResult(
            usability=usability,
            candidate=prior.candidate,
            original_source=prior.original_source,
            reliability=prior.reliability,
            confirmed_event_time=None,
            confirmation=confirmation,
            video_relative_sec=rel_sec,
            previous=prior,
            contributing_claims=prior.contributing_claims,
            conflict_claims=prior.conflict_claims,
            reason=reason,
            diagnostics=diagnostics,
        )

    if confirmed_by is None or not str(confirmed_by).strip():
        return _reject(
            usability=EventTimeUsability.REQUIRES_CONFIRMATION,
            reason=(
                "Confirmation rejected: confirmed_by identity claim is required. "
                "No confirmed result was produced."
            ),
            diagnostics={"confirmation_rejected": "missing_confirmed_by"},
        )

    if confirmed_at is None:
        return _reject(
            usability=EventTimeUsability.REQUIRES_CONFIRMATION,
            reason=(
                "Confirmation rejected: confirmed_at (timezone-aware review "
                "timestamp) is required. No confirmed result was produced."
            ),
            diagnostics={"confirmation_rejected": "missing_confirmed_at"},
        )

    if confirmed_at.tzinfo is None:
        return _reject(
            usability=EventTimeUsability.INVALID,
            reason=(
                "Confirmation rejected: confirmed_at must be timezone-aware; "
                "host local timezone is never attached automatically."
            ),
            diagnostics={"confirmation_rejected": "naive_confirmed_at"},
        )

    try:
        confirmation = ReviewConfirmation(
            confirmed_by=str(confirmed_by).strip(),
            confirmed_at=confirmed_at,
        )
    except ValueError as exc:
        return _reject(
            usability=EventTimeUsability.INVALID,
            reason=str(exc),
            diagnostics={"confirmation_rejected": "invalid_confirmation"},
        )

    applying_correction = (
        corrected_instant is not None or corrected_raw_text is not None
    )

    if not applying_correction and accept_candidate:
        if prior.confirmed_event_time is not None:
            confirmed_instant = prior.confirmed_event_time
            reason = (
                "Existing confirmed event time retained on reconfirmation; "
                "original candidate and source are preserved. "
                f"New confirmation actor/time recorded separately."
            )
            accepted_confirmed = True
            accepted_candidate = False
            corrected = False
        elif prior.candidate is not None:
            confirmed_instant = prior.candidate
            reason = (
                "Candidate accepted by reviewer confirmation; original source "
                f"{prior.original_source.value if prior.original_source else 'unknown'} "
                "is preserved."
            )
            accepted_confirmed = False
            accepted_candidate = True
            corrected = False
        else:
            return _reject(
                usability=EventTimeUsability.REQUIRES_CONFIRMATION,
                reason=(
                    "Confirmation rejected: no confirmed event time or "
                    "candidate to accept, and no corrected event time supplied."
                ),
                diagnostics={"confirmation_rejected": "no_candidate"},
            )
    else:
        if corrected_instant is not None:
            confirmed_instant = corrected_instant
        else:
            parsed = parse_iso8601_event_instant(
                corrected_raw_text,
                source_timezone=corrected_source_timezone,
            )
            if parsed.status is not ParseStatus.OK or parsed.instant is None:
                usability = (
                    EventTimeUsability.INVALID
                    if parsed.status is ParseStatus.INVALID
                    else EventTimeUsability.REQUIRES_CONFIRMATION
                )
                return _reject(
                    usability=usability,
                    reason=parsed.reason
                    or "Corrected event time could not be resolved.",
                    diagnostics={
                        "confirmation_rejected": "invalid_correction",
                        "parse_status": parsed.status.value,
                    },
                )
            confirmed_instant = parsed.instant
        reason = (
            "Reviewer correction applied; prior candidate and source are "
            "retained on this result and via previous."
        )
        accepted_confirmed = False
        accepted_candidate = False
        corrected = True

    return EventTimeResult(
        usability=EventTimeUsability.USABLE,
        candidate=prior.candidate,
        original_source=prior.original_source,
        reliability=prior.reliability,
        confirmed_event_time=confirmed_instant,
        confirmation=confirmation,
        video_relative_sec=rel_sec,
        previous=prior,
        contributing_claims=prior.contributing_claims,
        conflict_claims=prior.conflict_claims,
        reason=reason,
        diagnostics={
            "confirmation_source_label": EventTimeSource.USER_CONFIRMATION.value,
            "accepted_candidate": accepted_candidate,
            "accepted_confirmed_value": accepted_confirmed,
            "corrected": corrected,
        },
    )


def usable_event_instant(result: EventTimeResult) -> datetime | None:
    """Return the aware event datetime when time-ready; else ``None``.

    Indicates **time readiness only**. Does not imply plate verification,
    legal classification, recurrence eligibility, or authenticated review.
    """
    if not result.is_ready_for_recurrence_time_evaluation:
        return None
    instant = result.event_instant
    return None if instant is None else instant.value
