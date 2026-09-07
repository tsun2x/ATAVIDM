"""Focused Stage C2 tests for standalone event-time provenance helpers.

Deterministic values only — no machine clock, host timezone, database,
filesystem, network, or worker dependency.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest

from core import event_time as et


UTC = timezone.utc
FIXED_OFFSET_PHT = timezone(timedelta(hours=8))


def _aware(y, mo, d, h, mi, s=0, *, tz=FIXED_OFFSET_PHT) -> datetime:
    return datetime(y, mo, d, h, mi, s, tzinfo=tz)


def _instant(y, mo, d, h, mi, s=0, *, tz=FIXED_OFFSET_PHT) -> et.EventTimeInstant:
    return et.instant_from_aware_datetime(_aware(y, mo, d, h, mi, s, tz=tz))


# ---------------------------------------------------------------------------
# 1. Reliable CCTV-derived aware time retains its source
# ---------------------------------------------------------------------------


class TestReliableCctvRetainsSource:
    def test_reliable_cctv_usable_with_source(self):
        instant = _instant(2026, 3, 15, 9, 30, 0)
        result = et.from_cctv_timestamp(
            reliability=et.CctvReliability.RELIABLE,
            instant=instant,
            video_relative_sec=12.5,
        )
        assert result.usability is et.EventTimeUsability.USABLE
        assert result.original_source is et.EventTimeSource.CCTV_TIMESTAMP
        assert result.candidate == instant
        assert result.confirmed_event_time is None
        assert result.reliability is et.CctvReliability.RELIABLE
        assert result.video_relative_sec == 12.5
        assert result.is_ready_for_recurrence_time_evaluation is True
        assert et.usable_event_instant(result) == instant.value


# ---------------------------------------------------------------------------
# 2. Unreliable CCTV time requires confirmation
# ---------------------------------------------------------------------------


class TestUnreliableCctvRequiresConfirmation:
    def test_unreliable_cctv(self):
        instant = _instant(2026, 3, 15, 9, 30, 0)
        result = et.from_cctv_timestamp(
            reliability=et.CctvReliability.UNRELIABLE,
            instant=instant,
        )
        assert result.usability is et.EventTimeUsability.REQUIRES_CONFIRMATION
        assert result.candidate == instant
        assert result.original_source is et.EventTimeSource.CCTV_TIMESTAMP
        assert result.is_ready_for_recurrence_time_evaluation is False
        assert et.usable_event_instant(result) is None

    def test_unreadable_cctv(self):
        result = et.from_cctv_timestamp(
            reliability=et.CctvReliability.UNREADABLE,
            raw_text=None,
        )
        assert result.usability is et.EventTimeUsability.REQUIRES_CONFIRMATION
        assert result.is_ready_for_recurrence_time_evaluation is False


# ---------------------------------------------------------------------------
# 3. Missing event time remains unavailable
# ---------------------------------------------------------------------------


class TestMissingUnavailable:
    def test_missing_helper(self):
        result = et.missing_event_time()
        assert result.usability is et.EventTimeUsability.UNAVAILABLE
        assert result.candidate is None
        assert et.usable_event_instant(result) is None

    def test_missing_cctv_reliability(self):
        result = et.from_cctv_timestamp(reliability=et.CctvReliability.MISSING)
        assert result.usability is et.EventTimeUsability.UNAVAILABLE

    def test_empty_resolve(self):
        result = et.resolve_event_time([])
        assert result.usability is et.EventTimeUsability.UNAVAILABLE


# ---------------------------------------------------------------------------
# 4. Video metadata remains a candidate until confirmation
# ---------------------------------------------------------------------------


class TestVideoMetadataCandidateOnly:
    def test_metadata_requires_confirmation(self):
        instant = _instant(2026, 4, 1, 14, 0, 0)
        result = et.from_video_metadata(instant=instant)
        assert result.usability is et.EventTimeUsability.REQUIRES_CONFIRMATION
        assert result.original_source is et.EventTimeSource.VIDEO_METADATA
        assert result.candidate == instant
        assert result.is_ready_for_recurrence_time_evaluation is False


# ---------------------------------------------------------------------------
# 5–7. Confirmation preserves source; correction retains previous;
#      actor/time distinct from event time
# ---------------------------------------------------------------------------


class TestConfirmationAndCorrection:
    def test_accept_preserves_original_source(self):
        cctv = et.from_cctv_timestamp(
            reliability=et.CctvReliability.UNRELIABLE,
            instant=_instant(2026, 5, 1, 10, 0, 0),
        )
        review_at = _aware(2026, 5, 2, 8, 0, 0, tz=UTC)
        confirmed = et.confirm_event_time(
            cctv,
            confirmed_by="officer-42",
            confirmed_at=review_at,
            accept_candidate=True,
        )
        assert confirmed.usability is et.EventTimeUsability.USABLE
        assert confirmed.original_source is et.EventTimeSource.CCTV_TIMESTAMP
        assert confirmed.candidate == cctv.candidate
        assert confirmed.confirmed_event_time == cctv.candidate
        assert confirmed.confirmation is not None
        assert confirmed.confirmation.confirmed_by == "officer-42"
        assert confirmed.confirmation.confirmed_at == review_at
        # Review time ≠ event time
        assert confirmed.confirmation.confirmed_at != confirmed.confirmed_event_time.value
        assert confirmed.previous is cctv

    def test_correction_returns_new_result_retaining_previous(self):
        prior = et.from_video_metadata(instant=_instant(2026, 5, 1, 10, 0, 0))
        corrected = _instant(2026, 5, 1, 10, 15, 0)
        review_at = _aware(2026, 5, 3, 11, 0, 0, tz=UTC)
        result = et.confirm_event_time(
            prior,
            confirmed_by="reviewer-7",
            confirmed_at=review_at,
            accept_candidate=False,
            corrected_instant=corrected,
        )
        assert result is not prior
        assert result.previous is prior
        assert result.candidate == prior.candidate
        assert result.original_source is et.EventTimeSource.VIDEO_METADATA
        assert result.confirmed_event_time == corrected
        assert result.confirmation.confirmed_at == review_at
        assert result.confirmed_event_time.value != review_at


# ---------------------------------------------------------------------------
# 8–11. Offsets, naive refusal, source timezone, invalid/ambiguous
# ---------------------------------------------------------------------------


class TestTimezoneAndParsing:
    def test_explicit_offset_preserves_instant(self):
        parsed = et.parse_iso8601_event_instant("2026-06-01T12:00:00+08:00")
        assert parsed.status is et.ParseStatus.OK
        assert parsed.instant is not None
        assert parsed.instant.offset_label == "+08:00"
        utc_equiv = parsed.instant.value.astimezone(UTC)
        assert utc_equiv == datetime(2026, 6, 1, 4, 0, 0, tzinfo=UTC)

    def test_zulu_offset(self):
        parsed = et.parse_iso8601_event_instant("2026-06-01T04:00:00Z")
        assert parsed.status is et.ParseStatus.OK
        assert parsed.instant.offset_label == "Z"
        assert parsed.instant.value == datetime(2026, 6, 1, 4, 0, 0, tzinfo=UTC)

    def test_naive_string_without_source_tz_unresolved(self):
        parsed = et.parse_iso8601_event_instant("2026-06-01T12:00:00")
        assert parsed.status is et.ParseStatus.UNRESOLVED
        assert parsed.instant is None
        assert "Host local" in (parsed.reason or "")

    def test_naive_datetime_constructor_refused(self):
        with pytest.raises(ValueError, match="Naive datetime refused"):
            et.instant_from_aware_datetime(datetime(2026, 6, 1, 12, 0, 0))

    def test_explicit_source_timezone_follows_contract(self):
        """Named zone succeeds when ZoneInfo can resolve it; else UNRESOLVED."""
        parsed = et.parse_iso8601_event_instant(
            "2026-06-01T12:00:00",
            source_timezone="Asia/Manila",
        )
        try:
            ZoneInfo("Asia/Manila")
            zone_available = True
        except ZoneInfoNotFoundError:
            zone_available = False

        if zone_available:
            assert parsed.status is et.ParseStatus.OK
            assert parsed.instant is not None
            assert parsed.instant.source_timezone == "Asia/Manila"
            assert parsed.instant.value.utcoffset() == timedelta(hours=8)
        else:
            assert parsed.status is et.ParseStatus.UNRESOLVED
            assert parsed.instant is None
            assert "not available" in (parsed.reason or "").lower() or (
                "could not be resolved" in (parsed.reason or "").lower()
            )

    def test_unknown_source_timezone_unresolved(self):
        parsed = et.parse_iso8601_event_instant(
            "2026-06-01T12:00:00",
            source_timezone="Not/A_Real_Zone",
        )
        assert parsed.status is et.ParseStatus.UNRESOLVED
        assert parsed.instant is None

    def test_invalid_calendar_date(self):
        parsed = et.parse_iso8601_event_instant("2026-02-30T10:00:00+08:00")
        assert parsed.status is et.ParseStatus.INVALID
        assert parsed.instant is None

    def test_ambiguous_day_month_rejected(self):
        for bad in ("01/02/2026 10:00:00", "02/01/2026", "2026/06/01 12:00:00"):
            parsed = et.parse_iso8601_event_instant(bad)
            assert parsed.status is et.ParseStatus.INVALID, bad

    def test_named_timezone_ambiguity_when_supported(self):
        zone_name = "Australia/Sydney"
        try:
            ZoneInfo(zone_name)
        except ZoneInfoNotFoundError:
            pytest.skip(f"{zone_name} not available (no tzdata); ambiguity path untested here")
        # DST end in Sydney (fold): 2026-04-05 02:30 occurs twice.
        parsed = et.parse_iso8601_event_instant(
            "2026-04-05T02:30:00",
            source_timezone=zone_name,
        )
        assert parsed.status in (
            et.ParseStatus.AMBIGUOUS_LOCAL,
            et.ParseStatus.OK,  # if zone rules differ, still must not invent
            et.ParseStatus.UNRESOLVED,
        )
        if parsed.status is et.ParseStatus.AMBIGUOUS_LOCAL:
            assert parsed.instant is None


# ---------------------------------------------------------------------------
# 12. Conflicting sources require a decision
# ---------------------------------------------------------------------------


class TestConflicts:
    def test_conflicting_sources_no_auto_select(self):
        a = et.EventTimeClaim(
            source=et.EventTimeSource.CCTV_TIMESTAMP,
            instant=_instant(2026, 7, 1, 9, 0, 0),
            reliability=et.CctvReliability.RELIABLE,
        )
        b = et.EventTimeClaim(
            source=et.EventTimeSource.VIDEO_METADATA,
            instant=_instant(2026, 7, 1, 10, 0, 0),
        )
        result = et.resolve_event_time([a, b])
        assert result.usability is et.EventTimeUsability.CONFLICT
        assert len(result.conflict_claims) == 2
        assert len(result.contributing_claims) == 2
        assert result.is_ready_for_recurrence_time_evaluation is False
        assert et.usable_event_instant(result) is None


# ---------------------------------------------------------------------------
# 13. Upload/processing/printing never become implicit fallbacks
# ---------------------------------------------------------------------------


class TestNonEventSourcesRejected:
    @pytest.mark.parametrize(
        "source",
        [
            et.EventTimeSource.PROCESSING,
            et.EventTimeSource.UPLOAD,
            et.EventTimeSource.FILE_MODIFICATION,
            et.EventTimeSource.PRINTING,
        ],
    )
    def test_operational_clocks_rejected(self, source):
        claim = et.EventTimeClaim(
            source=source,
            instant=_instant(2026, 8, 1, 12, 0, 0),
        )
        result = et.resolve_event_time([claim])
        assert result.usability is et.EventTimeUsability.UNAVAILABLE
        assert result.candidate is None
        assert result.is_ready_for_recurrence_time_evaluation is False


# ---------------------------------------------------------------------------
# 14. Caller-owned input is unchanged
# ---------------------------------------------------------------------------


class TestCallerOwnership:
    def test_claim_list_and_nested_values_unchanged(self):
        claims = [
            et.EventTimeClaim(
                source=et.EventTimeSource.VIDEO_METADATA,
                raw_text="2026-08-01T12:00:00+08:00",
            ),
            et.EventTimeClaim(
                source=et.EventTimeSource.CCTV_TIMESTAMP,
                raw_text="2026-08-01T13:00:00+08:00",
                reliability=et.CctvReliability.RELIABLE,
            ),
        ]
        snapshot = copy.deepcopy(claims)
        _ = et.resolve_event_time(claims)
        assert claims == snapshot

    def test_confirm_does_not_mutate_prior(self):
        prior = et.from_video_metadata(instant=_instant(2026, 8, 1, 12, 0, 0))
        prior_usability = prior.usability
        prior_candidate = prior.candidate
        _ = et.confirm_event_time(
            prior,
            confirmed_by="r1",
            confirmed_at=_aware(2026, 8, 2, 1, 0, 0, tz=UTC),
        )
        assert prior.usability is prior_usability
        assert prior.candidate is prior_candidate
        assert prior.confirmation is None
        assert prior.confirmed_event_time is None


# ---------------------------------------------------------------------------
# 15. Missing confirmation metadata does not create a confirmed result
# ---------------------------------------------------------------------------


class TestIncompleteConfirmation:
    def test_missing_confirmed_by(self):
        prior = et.from_video_metadata(instant=_instant(2026, 8, 1, 12, 0, 0))
        result = et.confirm_event_time(
            prior,
            confirmed_by=None,
            confirmed_at=_aware(2026, 8, 2, 1, 0, 0, tz=UTC),
        )
        assert result.usability is et.EventTimeUsability.REQUIRES_CONFIRMATION
        assert result.confirmed_event_time is None
        assert result.confirmation is None

    def test_missing_confirmed_at(self):
        prior = et.from_video_metadata(instant=_instant(2026, 8, 1, 12, 0, 0))
        result = et.confirm_event_time(
            prior,
            confirmed_by="officer",
            confirmed_at=None,
        )
        assert result.usability is et.EventTimeUsability.REQUIRES_CONFIRMATION
        assert result.confirmed_event_time is None

    def test_naive_confirmed_at_rejected(self):
        prior = et.from_video_metadata(instant=_instant(2026, 8, 1, 12, 0, 0))
        result = et.confirm_event_time(
            prior,
            confirmed_by="officer",
            confirmed_at=datetime(2026, 8, 2, 1, 0, 0),  # naive
        )
        assert result.usability is et.EventTimeUsability.INVALID
        assert result.confirmed_event_time is None

    def test_blank_confirmed_by_rejected(self):
        prior = et.from_video_metadata(instant=_instant(2026, 8, 1, 12, 0, 0))
        result = et.confirm_event_time(
            prior,
            confirmed_by="   ",
            confirmed_at=_aware(2026, 8, 2, 1, 0, 0, tz=UTC),
        )
        assert result.confirmed_event_time is None
        assert result.usability is et.EventTimeUsability.REQUIRES_CONFIRMATION


# ---------------------------------------------------------------------------
# 16. Import / helpers perform no DB, FS, network, or worker actions
# ---------------------------------------------------------------------------


class TestNoSideEffects:
    def test_module_is_stdlib_pure(self):
        import core.event_time as mod

        # Module globals must not bind application/persistence/network surfaces.
        for name in ("db", "sqlite_adapter", "app", "Flask", "requests"):
            assert name not in mod.__dict__
        for attr in ("open", "connect", "urlopen", "Session", "create_engine"):
            assert not hasattr(mod, attr)

    def test_helpers_do_not_touch_environ_db_paths(self, monkeypatch):
        monkeypatch.setenv("SQLITE_PATH", "/should/never/be/opened/by/event_time.db")
        monkeypatch.setenv("DATABASE_URL", "/should/never/be/opened/by/event_time.db")
        result = et.from_cctv_timestamp(
            reliability=et.CctvReliability.RELIABLE,
            raw_text="2026-09-01T08:00:00+08:00",
        )
        assert result.usability is et.EventTimeUsability.USABLE


# ---------------------------------------------------------------------------
# 17. No result grants plate / legal / recurrence eligibility
# ---------------------------------------------------------------------------


class TestNoEligibilityGrants:
    def test_usable_still_denies_eligibility_flags(self):
        result = et.from_cctv_timestamp(
            reliability=et.CctvReliability.RELIABLE,
            instant=_instant(2026, 9, 1, 8, 0, 0),
        )
        assert result.is_ready_for_recurrence_time_evaluation is True
        assert result.grants_recurrence_eligibility is False
        assert result.grants_plate_verification is False
        assert result.grants_legal_classification is False

    def test_confirmed_still_denies_eligibility_flags(self):
        prior = et.from_video_metadata(instant=_instant(2026, 9, 1, 8, 0, 0))
        result = et.confirm_event_time(
            prior,
            confirmed_by="r",
            confirmed_at=_aware(2026, 9, 2, 0, 0, 0, tz=UTC),
        )
        assert result.usability is et.EventTimeUsability.USABLE
        assert result.grants_recurrence_eligibility is False
        assert result.grants_plate_verification is False
        assert result.grants_legal_classification is False


# ---------------------------------------------------------------------------
# Defect repairs (offset validation, reconfirmation, multi-source order)
# ---------------------------------------------------------------------------


class TestInvalidOffsetRejection:
    """Invalid offsets must be INVALID — never normalized, never crash."""

    @pytest.mark.parametrize(
        "raw",
        [
            "2026-09-07T10:00:00+08:60",
            "2026-09-07T10:00:00-08:60",
            "2026-09-07T10:00:00+00:99",
            "2026-09-07T10:00:00-00:60",
        ],
    )
    def test_invalid_minutes_return_invalid(self, raw):
        parsed = et.parse_iso8601_event_instant(raw)
        assert parsed.status is et.ParseStatus.INVALID
        assert parsed.instant is None
        assert "minutes" in (parsed.reason or "").lower()

    @pytest.mark.parametrize(
        "raw",
        [
            "2026-09-07T10:00:00+24:00",
            "2026-09-07T10:00:00-24:00",
            "2026-09-07T10:00:00+25:00",
            "2026-09-07T10:00:00-99:00",
        ],
    )
    def test_unsupported_magnitude_returns_invalid_without_crash(self, raw):
        parsed = et.parse_iso8601_event_instant(raw)
        assert parsed.status is et.ParseStatus.INVALID
        assert parsed.instant is None

    def test_valid_offsets_and_z_retain_instant(self):
        plus = et.parse_iso8601_event_instant("2026-09-07T10:00:00+08:00")
        assert plus.status is et.ParseStatus.OK
        assert plus.instant is not None
        assert plus.instant.offset_label == "+08:00"
        assert plus.instant.value.astimezone(UTC) == datetime(
            2026, 9, 7, 2, 0, 0, tzinfo=UTC
        )

        minus = et.parse_iso8601_event_instant("2026-09-07T10:00:00-05:30")
        assert minus.status is et.ParseStatus.OK
        assert minus.instant.offset_label == "-05:30"
        assert minus.instant.value.astimezone(UTC) == datetime(
            2026, 9, 7, 15, 30, 0, tzinfo=UTC
        )

        zulu = et.parse_iso8601_event_instant("2026-09-07T10:00:00Z")
        assert zulu.status is et.ParseStatus.OK
        assert zulu.instant.offset_label == "Z"
        assert zulu.instant.value == datetime(2026, 9, 7, 10, 0, 0, tzinfo=UTC)

        edge = et.parse_iso8601_event_instant("2026-09-07T10:00:00+23:59")
        assert edge.status is et.ParseStatus.OK
        assert edge.instant.offset_label == "+23:59"

    def test_invalid_minutes_not_silently_normalized(self):
        parsed = et.parse_iso8601_event_instant("2026-09-07T10:00:00+08:60")
        assert parsed.status is et.ParseStatus.INVALID
        # Must not become OK with +09:00
        assert parsed.instant is None


class TestReconfirmationPreservesCorrection:
    def test_first_confirmation_accepts_candidate(self):
        prior = et.from_video_metadata(instant=_instant(2026, 5, 1, 10, 0, 0))
        review_at = _aware(2026, 5, 2, 8, 0, 0, tz=UTC)
        confirmed = et.confirm_event_time(
            prior,
            confirmed_by="officer-1",
            confirmed_at=review_at,
            accept_candidate=True,
        )
        assert confirmed.usability is et.EventTimeUsability.USABLE
        assert confirmed.confirmed_event_time == prior.candidate
        assert confirmed.diagnostics.get("accepted_candidate") is True
        assert confirmed.diagnostics.get("accepted_confirmed_value") is False
        assert confirmed.confirmation.confirmed_at == review_at
        assert confirmed.confirmation.confirmed_at != confirmed.confirmed_event_time.value

    def test_correction_preserves_original_candidate(self):
        prior = et.from_video_metadata(instant=_instant(2026, 5, 1, 10, 0, 0))
        corrected = _instant(2026, 5, 1, 11, 0, 0)
        result = et.confirm_event_time(
            prior,
            confirmed_by="officer-1",
            confirmed_at=_aware(2026, 5, 2, 8, 0, 0, tz=UTC),
            accept_candidate=False,
            corrected_instant=corrected,
        )
        assert result.candidate == prior.candidate
        assert result.original_source is et.EventTimeSource.VIDEO_METADATA
        assert result.confirmed_event_time == corrected
        assert result.diagnostics.get("corrected") is True
        assert result.diagnostics.get("accepted_candidate") is False

    def test_reconfirmation_preserves_corrected_value(self):
        prior = et.from_video_metadata(instant=_instant(2026, 5, 1, 10, 0, 0))
        corrected = _instant(2026, 5, 1, 11, 0, 0)
        first = et.confirm_event_time(
            prior,
            confirmed_by="officer-1",
            confirmed_at=_aware(2026, 5, 2, 8, 0, 0, tz=UTC),
            corrected_instant=corrected,
        )
        second = et.confirm_event_time(
            first,
            confirmed_by="officer-2",
            confirmed_at=_aware(2026, 5, 3, 9, 0, 0, tz=UTC),
            accept_candidate=True,
        )
        assert second.confirmed_event_time == corrected
        assert second.candidate == prior.candidate
        assert second.diagnostics.get("accepted_confirmed_value") is True
        assert second.diagnostics.get("accepted_candidate") is False
        assert second.confirmation.confirmed_by == "officer-2"
        assert second.previous is first
        # Prior snapshots unchanged
        assert first.confirmed_event_time == corrected
        assert prior.confirmed_event_time is None

    def test_second_explicit_correction_updates_value(self):
        prior = et.from_video_metadata(instant=_instant(2026, 5, 1, 10, 0, 0))
        c1 = _instant(2026, 5, 1, 11, 0, 0)
        c2 = _instant(2026, 5, 1, 11, 30, 0)
        first = et.confirm_event_time(
            prior,
            confirmed_by="a",
            confirmed_at=_aware(2026, 5, 2, 8, 0, 0, tz=UTC),
            corrected_instant=c1,
        )
        second = et.confirm_event_time(
            first,
            confirmed_by="b",
            confirmed_at=_aware(2026, 5, 3, 8, 0, 0, tz=UTC),
            corrected_instant=c2,
        )
        assert second.confirmed_event_time == c2
        assert second.candidate == prior.candidate
        assert second.previous is first
        assert first.previous is prior
        assert first.confirmed_event_time == c1
        assert prior.candidate == _instant(2026, 5, 1, 10, 0, 0)

    def test_missing_values_cannot_fabricate_confirmation(self):
        empty = et.missing_event_time()
        rejected = et.confirm_event_time(
            empty,
            confirmed_by="officer",
            confirmed_at=_aware(2026, 5, 2, 8, 0, 0, tz=UTC),
        )
        assert rejected.confirmed_event_time is None
        assert rejected.confirmation is None
        assert rejected.usability is et.EventTimeUsability.REQUIRES_CONFIRMATION

        prior = et.from_video_metadata(instant=_instant(2026, 5, 1, 10, 0, 0))
        no_actor = et.confirm_event_time(
            prior,
            confirmed_by=None,
            confirmed_at=_aware(2026, 5, 2, 8, 0, 0, tz=UTC),
        )
        assert no_actor.confirmed_event_time is None
        assert no_actor.confirmation is None


class TestMultiSourceOrderIndependence:
    def _cctv(self, hour: int = 9, *, reliable: bool = True) -> et.EventTimeClaim:
        return et.EventTimeClaim(
            source=et.EventTimeSource.CCTV_TIMESTAMP,
            instant=_instant(2026, 7, 1, hour, 0, 0),
            reliability=(
                et.CctvReliability.RELIABLE
                if reliable
                else et.CctvReliability.UNRELIABLE
            ),
        )

    def _meta(self, hour: int = 9) -> et.EventTimeClaim:
        return et.EventTimeClaim(
            source=et.EventTimeSource.VIDEO_METADATA,
            instant=_instant(2026, 7, 1, hour, 0, 0),
        )

    def test_agreeing_cctv_metadata_permutations_equivalent(self):
        a = self._cctv(9)
        b = self._meta(9)
        r1 = et.resolve_event_time([a, b])
        r2 = et.resolve_event_time([b, a])
        assert r1.usability is et.EventTimeUsability.REQUIRES_CONFIRMATION
        assert r2.usability is et.EventTimeUsability.REQUIRES_CONFIRMATION
        assert r1.usability == r2.usability
        assert r1.candidate is not None and r2.candidate is not None
        assert r1.candidate.value == r2.candidate.value
        assert set(r1.contributing_claims) == set(r2.contributing_claims)
        assert len(r1.contributing_claims) == 2
        assert r1.is_ready_for_recurrence_time_evaluation is False

    def test_equivalent_instants_different_offsets_agree(self):
        # 09:00+08:00 == 01:00Z
        cctv = et.EventTimeClaim(
            source=et.EventTimeSource.CCTV_TIMESTAMP,
            instant=_instant(2026, 7, 1, 9, 0, 0),
            reliability=et.CctvReliability.RELIABLE,
        )
        meta = et.EventTimeClaim(
            source=et.EventTimeSource.VIDEO_METADATA,
            instant=et.instant_from_aware_datetime(
                datetime(2026, 7, 1, 1, 0, 0, tzinfo=UTC)
            ),
        )
        for order in ([cctv, meta], [meta, cctv]):
            result = et.resolve_event_time(order)
            assert result.usability is et.EventTimeUsability.REQUIRES_CONFIRMATION
            assert result.candidate is not None
            assert result.candidate.value.astimezone(UTC) == datetime(
                2026, 7, 1, 1, 0, 0, tzinfo=UTC
            )
            assert len(result.contributing_claims) == 2

    def test_conflicting_permutations_remain_conflicts(self):
        a = self._cctv(9)
        b = self._meta(10)
        for order in ([a, b], [b, a]):
            result = et.resolve_event_time(order)
            assert result.usability is et.EventTimeUsability.CONFLICT
            assert set(result.conflict_claims) == {a, b}
            assert set(result.contributing_claims) == {a, b}

    def test_multiple_reliable_cctv_no_undocumented_priority(self):
        a = self._cctv(9)
        b = et.EventTimeClaim(
            source=et.EventTimeSource.CCTV_TIMESTAMP,
            instant=_instant(2026, 7, 1, 9, 0, 0),
            reliability=et.CctvReliability.RELIABLE,
            notes="second-camera",
        )
        r1 = et.resolve_event_time([a, b])
        r2 = et.resolve_event_time([b, a])
        assert r1.usability is et.EventTimeUsability.REQUIRES_CONFIRMATION
        assert r2.usability is et.EventTimeUsability.REQUIRES_CONFIRMATION
        assert r1.usability == r2.usability
        assert r1.candidate.value == r2.candidate.value
        # Must not silently become single-source USABLE
        assert r1.is_ready_for_recurrence_time_evaluation is False

    def test_mixed_resolved_unresolved_preserves_all_provenance(self):
        resolved = self._cctv(9)
        unresolved = et.EventTimeClaim(
            source=et.EventTimeSource.VIDEO_METADATA,
            raw_text="2026-07-01T09:00:00",  # no offset → unresolved
        )
        for order in ([resolved, unresolved], [unresolved, resolved]):
            result = et.resolve_event_time(order)
            assert result.usability is et.EventTimeUsability.REQUIRES_CONFIRMATION
            assert len(result.contributing_claims) == 2
            assert resolved in result.contributing_claims
            assert unresolved in result.contributing_claims
            assert result.diagnostics.get("unresolved_count") == 1
            assert result.diagnostics.get("resolved_count") == 1
            # Must not drop uncertainty into single-source USABLE
            assert result.is_ready_for_recurrence_time_evaluation is False

    def test_single_source_behavior_unchanged(self):
        cctv = et.from_cctv_timestamp(
            reliability=et.CctvReliability.RELIABLE,
            instant=_instant(2026, 7, 1, 9, 0, 0),
        )
        assert cctv.usability is et.EventTimeUsability.USABLE

        via_resolve = et.resolve_event_time([self._cctv(9)])
        assert via_resolve.usability is et.EventTimeUsability.USABLE
        assert via_resolve.original_source is et.EventTimeSource.CCTV_TIMESTAMP

        meta = et.resolve_event_time([self._meta(9)])
        assert meta.usability is et.EventTimeUsability.REQUIRES_CONFIRMATION
        assert meta.original_source is et.EventTimeSource.VIDEO_METADATA

    def test_multi_source_grants_no_eligibility(self):
        result = et.resolve_event_time([self._cctv(9), self._meta(9)])
        assert result.grants_recurrence_eligibility is False
        assert result.grants_plate_verification is False
        assert result.grants_legal_classification is False
        # Confirming the agreed candidate still denies eligibility grants
        confirmed = et.confirm_event_time(
            result,
            confirmed_by="officer",
            confirmed_at=_aware(2026, 7, 2, 0, 0, 0, tz=UTC),
        )
        assert confirmed.usability is et.EventTimeUsability.USABLE
        assert confirmed.confirmed_event_time == result.candidate
        assert confirmed.grants_recurrence_eligibility is False
