"""Deterministic interleavings for competing Review Queue decisions.

Each test injects the competing request at a fixed point of the first
request instead of starting two threads and hoping they overlap:

- ``_compete_after_first_decision_read`` runs the competitor to completion
  right after the first request read the review's decision. No lock is held
  there, so the competitor commits inside that window.
- ``_compete_inside_window`` starts the competitor at a point that used to be
  unprotected (after group construction, or after the atomic case write but
  before sibling linking) and waits for it. With the write lock held there,
  the competitor cannot commit inside the window; without it, it commits and
  the first request consumes stale state.

Every test checks the response and the persisted rows.
"""

from __future__ import annotations

import sqlite3
import threading
from typing import Any, Callable

import pytest

import core.case_review_service as case_review_service
from core.detection_config import (
    VIOLATION_COUNTERFLOW,
    VIOLATION_ILLEGAL_PARKING,
    VIOLATION_OBSTRUCTION,
)
from core.review_decision_service import (
    DECISION_CONFIRM,
    DECISION_CORRECT,
    DECISION_INSUFFICIENT,
    DECISION_NO_VIOLATION,
    ReviewDecisionConflict,
    apply_review_decision,
)

# Long enough for an unprotected competitor to commit, shorter than the
# SQLite busy timeout (5 s) a blocked competitor waits before giving up.
WINDOW_SEC = 1.5
JOIN_SEC = 30


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "races.db"
    monkeypatch.setenv("SQLITE_PATH", str(path))
    monkeypatch.setenv("DATABASE_URL", str(path))
    return path


@pytest.fixture
def test_db(db_path):
    from database import sqlite_adapter

    sqlite_adapter.init_db(force=True)
    return sqlite_adapter


@pytest.fixture
def enforcer(test_db):
    return test_db.create_user("race_enf", "hash", role="enforcer", full_name="Race Enforcer")


def _review(test_db, violation_type, **kwargs):
    video_id = kwargs.pop("video_id", None)
    if video_id is None:
        video_id = test_db.insert_video("clip.mp4", "/tmp/clip.mp4", status="ready")
    defaults = dict(
        video_id=video_id,
        track_id=7,
        violation_type=violation_type,
        confidence=0.91,
        frame_number=12,
        timestamp_sec=4.0,
        episode_start_sec=1.0,
        episode_end_sec=8.0,
        evidence_path="/tmp/scene.jpg",
        reason_log="system suggestion",
        processing_run_id=1,
    )
    defaults.update(kwargs)
    return video_id, test_db.insert_review_queue(**defaults)


def _decide(test_db, review_id, reviewer, decision, reason, key, rule=None):
    return apply_review_decision(
        test_db,
        review_id,
        reviewer,
        decision=decision,
        reason=reason,
        selected_canonical_rule=rule,
        idempotency_key=key,
    )


def _snapshot(test_db) -> dict[str, Any]:
    """Every row a decision or materialization can touch."""
    with test_db.db_session() as conn:
        def rows(sql):
            return [tuple(r) for r in conn.execute(sql).fetchall()]

        return {
            "violations": rows("SELECT id, violation_type, status FROM violations ORDER BY id"),
            "policies": rows(
                "SELECT id, violation_id, canonical_rule, policy_version_id "
                "FROM case_policy_records ORDER BY id"
            ),
            "actions": rows(
                "SELECT id, violation_id, review_id, action_type, detail_json "
                "FROM case_action_events ORDER BY id"
            ),
            "decisions": rows("SELECT * FROM review_decisions ORDER BY id"),
            "queue": rows(
                "SELECT id, status, reviewed_by, reviewed_at, violation_type "
                "FROM review_queue ORDER BY id"
            ),
        }


def _links(test_db, review_id) -> list[int]:
    with test_db.db_session() as conn:
        rows = conn.execute(
            """
            SELECT violation_id FROM case_action_events
            WHERE review_id = ? AND action_type = ? AND violation_id IS NOT NULL
            ORDER BY id
            """,
            (review_id, test_db.ACTION_REVIEW_CONFIRMED),
        ).fetchall()
    return [int(r["violation_id"]) for r in rows]


def _rules(test_db, violation_id) -> set[str]:
    return {r["canonical_rule"] for r in test_db.get_case_policy_records(violation_id)}


def _committed_decision(db_path, review_id):
    """Read committed state on a separate connection (never the shared transaction)."""
    conn = sqlite3.connect(str(db_path), timeout=JOIN_SEC)
    try:
        row = conn.execute(
            "SELECT decision FROM review_decisions WHERE review_id = ?", (review_id,)
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


class _Competitor:
    """One competing request on its own thread (own connection, own lock state)."""

    def __init__(self, fn: Callable[[], Any]) -> None:
        self._fn = fn
        self.result: Any = None
        self.error: BaseException | None = None
        self.done = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        try:
            self.result = self._fn()
        except BaseException as exc:  # noqa: BLE001 - reported to the test
            self.error = exc
        finally:
            self.done.set()

    def start(self) -> "_Competitor":
        self._thread.start()
        return self

    def join(self) -> "_Competitor":
        self._thread.join(JOIN_SEC)
        assert not self._thread.is_alive(), "competing request did not finish"
        return self


def _compete_after_first_decision_read(monkeypatch, test_db, review_id, competitor_fn):
    """Commit the competitor right after the first request read the decision."""
    main = threading.get_ident()
    real = test_db.get_review_decision
    state: dict[str, Any] = {"competitor": None, "after_winner": None}

    def seam(rid):
        value = real(rid)
        if (
            state["competitor"] is None
            and int(rid) == int(review_id)
            and threading.get_ident() == main
        ):
            state["competitor"] = _Competitor(competitor_fn).start().join()
            state["after_winner"] = _snapshot(test_db)
        return value

    monkeypatch.setattr(test_db, "get_review_decision", seam)
    return state


def _compete_inside_window(monkeypatch, target, name, db_path, review_id, competitor_fn, *, after=False):
    """Start the competitor at ``target.name`` and wait up to WINDOW_SEC for it."""
    main = threading.get_ident()
    real = getattr(target, name)
    state: dict[str, Any] = {"competitor": None, "committed_in_window": None}

    def fire():
        competitor = _Competitor(competitor_fn).start()
        state["competitor"] = competitor
        competitor.done.wait(WINDOW_SEC)
        state["committed_in_window"] = _committed_decision(db_path, review_id)

    def seam(*args, **kwargs):
        first = state["competitor"] is None and threading.get_ident() == main
        if first and not after:
            fire()
        value = real(*args, **kwargs)
        if first and after:
            fire()
        return value

    monkeypatch.setattr(target, name, seam)
    return state


# ---------------------------------------------------------------------------
# Reported defect A: no-violation lands after group construction on reuse.
# ---------------------------------------------------------------------------


def test_no_violation_cannot_commit_between_group_build_and_reuse(
    test_db, enforcer, db_path, monkeypatch
):
    video_id, parking_id = _review(test_db, VIOLATION_ILLEGAL_PARKING)
    parked = _decide(
        test_db, parking_id, enforcer, DECISION_CONFIRM, "Parked in the zone.", "park"
    )
    case_id = parked["violation_id"]
    _, obstruction_id = _review(test_db, VIOLATION_OBSTRUCTION, video_id=video_id)

    window = _compete_inside_window(
        monkeypatch,
        case_review_service,
        "find_existing_fused_case_violation_id",
        db_path,
        obstruction_id,
        lambda: _decide(
            test_db, obstruction_id, enforcer, DECISION_NO_VIOLATION,
            "Not blocking the lane.", "obs-none",
        ),
    )
    result = _decide(
        test_db, obstruction_id, enforcer, DECISION_CONFIRM,
        "Blocking the traveled lane.", "obs-confirm",
    )
    competitor = window["competitor"].join()

    assert window["committed_in_window"] is None
    assert isinstance(competitor.error, ReviewDecisionConflict)
    assert result["decision"] == DECISION_CONFIRM
    assert result["creates_case"] is True
    assert result["violation_id"] == case_id
    assert result["fused"] is True
    stored = test_db.get_review_decision(obstruction_id)
    assert stored["decision"] == DECISION_CONFIRM
    assert stored["idempotency_key"] == "obs-confirm"
    assert stored["id"] == result["review_decision_id"]
    assert test_db.get_review_item(obstruction_id)["status"] == "confirmed"
    assert _links(test_db, obstruction_id) == [case_id]
    assert _rules(test_db, case_id) == {VIOLATION_ILLEGAL_PARKING, VIOLATION_OBSTRUCTION}
    snap = _snapshot(test_db)
    assert len(snap["decisions"]) == 2
    assert len(snap["violations"]) == 1

    # The losing client's retry gets the same conflict and changes nothing.
    with pytest.raises(ReviewDecisionConflict):
        _decide(
            test_db, obstruction_id, enforcer, DECISION_NO_VIOLATION,
            "Not blocking the lane.", "obs-none",
        )
    assert _snapshot(test_db) == snap


@pytest.mark.parametrize("no_case", [DECISION_NO_VIOLATION, DECISION_INSUFFICIENT])
def test_no_case_decision_after_initial_read_blocks_reuse(
    test_db, enforcer, monkeypatch, no_case
):
    video_id, parking_id = _review(test_db, VIOLATION_ILLEGAL_PARKING)
    parked = _decide(
        test_db, parking_id, enforcer, DECISION_CONFIRM, "Parked in the zone.", "park"
    )
    case_id = parked["violation_id"]
    _, obstruction_id = _review(test_db, VIOLATION_OBSTRUCTION, video_id=video_id)

    seam = _compete_after_first_decision_read(
        monkeypatch,
        test_db,
        obstruction_id,
        lambda: _decide(
            test_db, obstruction_id, enforcer, no_case, "Frames do not show it.", "obs-close"
        ),
    )
    with pytest.raises(ReviewDecisionConflict):
        _decide(
            test_db, obstruction_id, enforcer, DECISION_CONFIRM,
            "Blocking the traveled lane.", "obs-confirm",
        )

    winner = seam["competitor"]
    assert winner.error is None
    assert winner.result["decision"] == no_case
    assert winner.result["creates_case"] is False
    assert winner.result["violation_id"] is None
    assert _snapshot(test_db) == seam["after_winner"]
    stored = test_db.get_review_decision(obstruction_id)
    assert stored["decision"] == no_case
    assert stored["id"] == winner.result["review_decision_id"]
    assert test_db.get_review_item(obstruction_id)["status"] == "dismissed"
    assert _links(test_db, obstruction_id) == []
    assert _rules(test_db, case_id) == {VIOLATION_ILLEGAL_PARKING}


# ---------------------------------------------------------------------------
# Reported defect B: a competing correction lands after the initial read.
# ---------------------------------------------------------------------------


def test_correction_after_initial_read_conflicts_on_new_case(test_db, enforcer, monkeypatch):
    _, review_id = _review(test_db, VIOLATION_COUNTERFLOW, track_id=21)
    seam = _compete_after_first_decision_read(
        monkeypatch,
        test_db,
        review_id,
        lambda: _decide(
            test_db, review_id, enforcer, DECISION_CORRECT,
            "Stopped in the lane.", "corr", VIOLATION_OBSTRUCTION,
        ),
    )
    with pytest.raises(ReviewDecisionConflict):
        _decide(test_db, review_id, enforcer, DECISION_CONFIRM, "Opposing flow.", "confirm")

    winner = seam["competitor"]
    assert winner.error is None
    case_id = winner.result["violation_id"]
    assert _snapshot(test_db) == seam["after_winner"]
    stored = test_db.get_review_decision(review_id)
    assert stored["decision"] == DECISION_CORRECT
    assert stored["selected_canonical_rule"] == VIOLATION_OBSTRUCTION
    assert stored["id"] == winner.result["review_decision_id"]
    assert test_db.get_review_item(review_id)["violation_type"] == VIOLATION_COUNTERFLOW
    assert test_db.get_violation(case_id)["violation_type"] == VIOLATION_OBSTRUCTION
    assert _rules(test_db, case_id) == {VIOLATION_OBSTRUCTION}
    assert _links(test_db, review_id) == [case_id]
    assert len(seam["after_winner"]["violations"]) == 1


def test_correction_after_initial_read_conflicts_on_reused_case(test_db, enforcer, monkeypatch):
    video_id, parking_id = _review(test_db, VIOLATION_ILLEGAL_PARKING)
    parked = _decide(
        test_db, parking_id, enforcer, DECISION_CONFIRM, "Parked in the zone.", "park"
    )
    _, obstruction_id = _review(test_db, VIOLATION_OBSTRUCTION, video_id=video_id)
    seam = _compete_after_first_decision_read(
        monkeypatch,
        test_db,
        obstruction_id,
        lambda: _decide(
            test_db, obstruction_id, enforcer, DECISION_CORRECT,
            "Driving against traffic.", "obs-corr", VIOLATION_COUNTERFLOW,
        ),
    )
    with pytest.raises(ReviewDecisionConflict):
        _decide(
            test_db, obstruction_id, enforcer, DECISION_CONFIRM,
            "Blocking the traveled lane.", "obs-confirm",
        )

    winner = seam["competitor"]
    assert winner.error is None
    corrected_case = winner.result["violation_id"]
    assert corrected_case != parked["violation_id"]
    assert _snapshot(test_db) == seam["after_winner"]
    assert _links(test_db, obstruction_id) == [corrected_case]
    assert _rules(test_db, parked["violation_id"]) == {VIOLATION_ILLEGAL_PARKING}
    assert _rules(test_db, corrected_case) == {VIOLATION_COUNTERFLOW}
    assert test_db.get_review_decision(obstruction_id)["decision"] == DECISION_CORRECT


def test_confirm_after_initial_read_conflicts_with_correction(test_db, enforcer, monkeypatch):
    _, review_id = _review(test_db, VIOLATION_COUNTERFLOW, track_id=22)
    seam = _compete_after_first_decision_read(
        monkeypatch,
        test_db,
        review_id,
        lambda: _decide(test_db, review_id, enforcer, DECISION_CONFIRM, "Opposing flow.", "confirm"),
    )
    with pytest.raises(ReviewDecisionConflict):
        _decide(
            test_db, review_id, enforcer, DECISION_CORRECT,
            "Stopped in the lane.", "corr", VIOLATION_OBSTRUCTION,
        )
    winner = seam["competitor"]
    assert winner.error is None
    assert _snapshot(test_db) == seam["after_winner"]
    assert test_db.get_review_decision(review_id)["decision"] == DECISION_CONFIRM
    assert test_db.get_violation(winner.result["violation_id"])["violation_type"] == (
        VIOLATION_COUNTERFLOW
    )
    assert _rules(test_db, winner.result["violation_id"]) == {VIOLATION_COUNTERFLOW}


def test_two_different_corrections_keep_the_first(test_db, enforcer, monkeypatch):
    _, review_id = _review(test_db, VIOLATION_COUNTERFLOW, track_id=23)
    seam = _compete_after_first_decision_read(
        monkeypatch,
        test_db,
        review_id,
        lambda: _decide(
            test_db, review_id, enforcer, DECISION_CORRECT,
            "Parked, not moving.", "corr-park", VIOLATION_ILLEGAL_PARKING,
        ),
    )
    with pytest.raises(ReviewDecisionConflict):
        _decide(
            test_db, review_id, enforcer, DECISION_CORRECT,
            "Stopped in the lane.", "corr-obs", VIOLATION_OBSTRUCTION,
        )
    winner = seam["competitor"]
    assert winner.error is None
    case_id = winner.result["violation_id"]
    assert _snapshot(test_db) == seam["after_winner"]
    stored = test_db.get_review_decision(review_id)
    assert stored["selected_canonical_rule"] == VIOLATION_ILLEGAL_PARKING
    assert test_db.get_violation(case_id)["violation_type"] == VIOLATION_ILLEGAL_PARKING
    assert _rules(test_db, case_id) == {VIOLATION_ILLEGAL_PARKING}
    assert test_db.get_review_item(review_id)["violation_type"] == VIOLATION_COUNTERFLOW


def test_matching_retry_inside_window_returns_the_established_result(
    test_db, enforcer, monkeypatch
):
    _, review_id = _review(test_db, VIOLATION_COUNTERFLOW, track_id=24)
    seam = _compete_after_first_decision_read(
        monkeypatch,
        test_db,
        review_id,
        lambda: _decide(test_db, review_id, enforcer, DECISION_CONFIRM, "Opposing flow.", "same"),
    )
    result = _decide(test_db, review_id, enforcer, DECISION_CONFIRM, "Opposing flow.", "same")
    winner = seam["competitor"]
    assert winner.error is None
    assert result["idempotent_retry"] is True
    assert result["violation_id"] == winner.result["violation_id"]
    assert result["review_decision_id"] == winner.result["review_decision_id"]
    assert _snapshot(test_db) == seam["after_winner"]


@pytest.mark.parametrize(
    "reason,key",
    [("A different reason.", "same"), ("Opposing flow.", "another-key")],
)
def test_near_matching_retry_inside_window_conflicts(test_db, enforcer, monkeypatch, reason, key):
    _, review_id = _review(test_db, VIOLATION_COUNTERFLOW, track_id=25)
    seam = _compete_after_first_decision_read(
        monkeypatch,
        test_db,
        review_id,
        lambda: _decide(test_db, review_id, enforcer, DECISION_CONFIRM, "Opposing flow.", "same"),
    )
    with pytest.raises(ReviewDecisionConflict):
        _decide(test_db, review_id, enforcer, DECISION_CONFIRM, reason, key)
    assert seam["competitor"].error is None
    assert _snapshot(test_db) == seam["after_winner"]


# ---------------------------------------------------------------------------
# Sibling state that changes after the group was built.
# ---------------------------------------------------------------------------


def test_sibling_no_violation_cannot_land_between_case_write_and_linking(
    test_db, enforcer, db_path, monkeypatch
):
    video_id, parking_id = _review(test_db, VIOLATION_ILLEGAL_PARKING)
    _, obstruction_id = _review(test_db, VIOLATION_OBSTRUCTION, video_id=video_id)
    window = _compete_inside_window(
        monkeypatch,
        test_db,
        "create_case_with_materialization_intent",
        db_path,
        parking_id,
        lambda: _decide(
            test_db, parking_id, enforcer, DECISION_NO_VIOLATION,
            "Vehicle was moving.", "park-none",
        ),
        after=True,
    )
    result = _decide(
        test_db, obstruction_id, enforcer, DECISION_CONFIRM,
        "Blocking the traveled lane.", "obs-confirm",
    )
    competitor = window["competitor"].join()

    assert window["committed_in_window"] is None
    assert isinstance(competitor.error, ReviewDecisionConflict)
    case_id = result["violation_id"]
    assert result["fused"] is True
    assert set(result["review_ids"]) == {parking_id, obstruction_id}
    assert test_db.get_review_decision(parking_id) is None
    assert test_db.get_review_item(parking_id)["status"] == "confirmed"
    assert _links(test_db, parking_id) == [case_id]
    assert _links(test_db, obstruction_id) == [case_id]
    assert _rules(test_db, case_id) == {VIOLATION_ILLEGAL_PARKING, VIOLATION_OBSTRUCTION}
    assert len(_snapshot(test_db)["violations"]) == 1


def test_sibling_correction_before_lock_is_not_fused(test_db, enforcer, monkeypatch):
    video_id, parking_id = _review(test_db, VIOLATION_ILLEGAL_PARKING)
    _, obstruction_id = _review(test_db, VIOLATION_OBSTRUCTION, video_id=video_id)
    seam = _compete_after_first_decision_read(
        monkeypatch,
        test_db,
        obstruction_id,
        lambda: _decide(
            test_db, parking_id, enforcer, DECISION_CORRECT,
            "Driving against traffic.", "park-corr", VIOLATION_COUNTERFLOW,
        ),
    )
    result = _decide(
        test_db, obstruction_id, enforcer, DECISION_CONFIRM,
        "Blocking the traveled lane.", "obs-confirm",
    )
    corrected_case = seam["competitor"].result["violation_id"]
    assert result["fused"] is False
    assert result["violation_id"] != corrected_case
    assert parking_id not in result["review_ids"]
    assert _rules(test_db, result["violation_id"]) == {VIOLATION_OBSTRUCTION}
    assert _rules(test_db, corrected_case) == {VIOLATION_COUNTERFLOW}
    assert _links(test_db, parking_id) == [corrected_case]
    assert test_db.get_review_item(parking_id)["violation_type"] == VIOLATION_ILLEGAL_PARKING


def test_sibling_no_violation_before_lock_is_not_linked(test_db, enforcer, monkeypatch):
    video_id, parking_id = _review(test_db, VIOLATION_ILLEGAL_PARKING)
    _, obstruction_id = _review(test_db, VIOLATION_OBSTRUCTION, video_id=video_id)
    seam = _compete_after_first_decision_read(
        monkeypatch,
        test_db,
        obstruction_id,
        lambda: _decide(
            test_db, parking_id, enforcer, DECISION_NO_VIOLATION,
            "Vehicle was moving.", "park-none",
        ),
    )
    result = _decide(
        test_db, obstruction_id, enforcer, DECISION_CONFIRM,
        "Blocking the traveled lane.", "obs-confirm",
    )
    assert seam["competitor"].error is None
    assert result["fused"] is False
    assert parking_id not in result["review_ids"]
    assert _links(test_db, parking_id) == []
    assert _rules(test_db, result["violation_id"]) == {VIOLATION_OBSTRUCTION}
    assert test_db.get_review_decision(parking_id)["decision"] == DECISION_NO_VIOLATION


# ---------------------------------------------------------------------------
# Failure during completion, then competing and matching requests.
# ---------------------------------------------------------------------------


def _fail_policy_writes(monkeypatch, test_db):
    real = test_db.create_case_policy_record

    def boom(*_args, **_kwargs):
        raise RuntimeError("policy snapshot boom")

    monkeypatch.setattr(test_db, "create_case_policy_record", boom)
    return real


def test_completion_failure_on_new_case_then_conflict_then_recovery(
    test_db, enforcer, monkeypatch
):
    _, review_id = _review(test_db, VIOLATION_COUNTERFLOW, track_id=31)
    real = _fail_policy_writes(monkeypatch, test_db)
    with pytest.raises(RuntimeError, match="policy snapshot boom"):
        _decide(test_db, review_id, enforcer, DECISION_CONFIRM, "Opposing flow.", "c1")
    monkeypatch.setattr(test_db, "create_case_policy_record", real)

    stored = test_db.get_review_decision(review_id)
    assert stored["decision"] == DECISION_CONFIRM
    (case_id,) = _links(test_db, review_id)
    assert test_db.get_case_policy_records(case_id) == []
    staged = _snapshot(test_db)

    with pytest.raises(ReviewDecisionConflict):
        _decide(test_db, review_id, enforcer, DECISION_NO_VIOLATION, "Not a violation.", "n1")
    with pytest.raises(ReviewDecisionConflict):
        _decide(
            test_db, review_id, enforcer, DECISION_CORRECT,
            "Stopped in the lane.", "x1", VIOLATION_OBSTRUCTION,
        )
    assert _snapshot(test_db) == staged

    recovered = _decide(test_db, review_id, enforcer, DECISION_CONFIRM, "Opposing flow.", "c1")
    assert recovered["idempotent_retry"] is True
    assert recovered["violation_id"] == case_id
    assert recovered["review_decision_id"] == stored["id"]
    assert _rules(test_db, case_id) == {VIOLATION_COUNTERFLOW}
    intent_version = case_review_service._resolve_original_policy_version(test_db, case_id)
    assert {
        r["policy_version_id"] for r in test_db.get_case_policy_records(case_id)
    } == {intent_version}
    assert len(_snapshot(test_db)["decisions"]) == 1


def test_completion_failure_on_reused_case_then_conflict_then_recovery(
    test_db, enforcer, monkeypatch
):
    video_id, parking_id = _review(test_db, VIOLATION_ILLEGAL_PARKING)
    parked = _decide(
        test_db, parking_id, enforcer, DECISION_CONFIRM, "Parked in the zone.", "park"
    )
    case_id = parked["violation_id"]
    original_versions = {
        r["policy_version_id"] for r in test_db.get_case_policy_records(case_id)
    }
    _, obstruction_id = _review(test_db, VIOLATION_OBSTRUCTION, video_id=video_id)

    real = _fail_policy_writes(monkeypatch, test_db)
    with pytest.raises(RuntimeError, match="policy snapshot boom"):
        _decide(
            test_db, obstruction_id, enforcer, DECISION_CONFIRM,
            "Blocking the traveled lane.", "obs",
        )
    monkeypatch.setattr(test_db, "create_case_policy_record", real)

    assert test_db.get_review_decision(obstruction_id)["decision"] == DECISION_CONFIRM
    assert _links(test_db, obstruction_id) == [case_id]
    assert _rules(test_db, case_id) == {VIOLATION_ILLEGAL_PARKING}
    staged = _snapshot(test_db)

    with pytest.raises(ReviewDecisionConflict):
        _decide(
            test_db, obstruction_id, enforcer, DECISION_INSUFFICIENT,
            "Frames are unclear.", "obs-weak",
        )
    assert _snapshot(test_db) == staged

    recovered = _decide(
        test_db, obstruction_id, enforcer, DECISION_CONFIRM,
        "Blocking the traveled lane.", "obs",
    )
    assert recovered["idempotent_retry"] is True
    assert recovered["violation_id"] == case_id
    assert recovered["fused"] is True
    assert _rules(test_db, case_id) == {VIOLATION_ILLEGAL_PARKING, VIOLATION_OBSTRUCTION}
    assert {
        r["policy_version_id"] for r in test_db.get_case_policy_records(case_id)
    } == original_versions


# ---------------------------------------------------------------------------
# HTTP translation.
# ---------------------------------------------------------------------------


def _login_client(test_db, monkeypatch):
    import bcrypt

    monkeypatch.setenv("TAVIDM_BOOTSTRAP_ADMIN_PASSWORD", "isolated-race-test-pw")
    import app as flask_app
    from core import auth

    auth.ensure_default_admin()
    password = bcrypt.hashpw(b"enforcer123", bcrypt.gensalt()).decode("utf-8")
    enforcer_id = test_db.create_user(
        "race_ui", password, role="enforcer", full_name="Race UI Enforcer"
    )
    flask_app.app.config["TESTING"] = True
    flask_app.app.config["WTF_CSRF_ENABLED"] = False
    return flask_app, enforcer_id


def test_api_returns_409_for_correction_inside_window(test_db, monkeypatch):
    flask_app, enforcer_id = _login_client(test_db, monkeypatch)
    _, review_id = _review(test_db, VIOLATION_COUNTERFLOW, track_id=41)
    try:
        with flask_app.app.test_client() as client:
            login = client.post("/login", data={"username": "race_ui", "password": "enforcer123"})
            assert login.status_code in (200, 302)
            seam = _compete_after_first_decision_read(
                monkeypatch,
                test_db,
                review_id,
                lambda: _decide(
                    test_db, review_id, enforcer_id, DECISION_CORRECT,
                    "Stopped in the lane.", "api-corr", VIOLATION_OBSTRUCTION,
                ),
            )
            response = client.post(
                f"/api/review-queue/{review_id}/decision",
                json={
                    "decision": DECISION_CONFIRM,
                    "reason": "Opposing flow.",
                    "idempotency_key": "api-confirm",
                },
            )
            assert response.status_code == 409
            assert response.get_json()["success"] is False
            assert seam["competitor"].error is None
            assert _snapshot(test_db) == seam["after_winner"]
            assert test_db.get_review_decision(review_id)["decision"] == DECISION_CORRECT
    finally:
        flask_app.stop_processing_worker()


def test_api_legacy_routes_return_409_for_both_reported_races(
    test_db, db_path, monkeypatch
):
    flask_app, enforcer_id = _login_client(test_db, monkeypatch)
    video_id, parking_id = _review(test_db, VIOLATION_ILLEGAL_PARKING)
    parked = _decide(
        test_db, parking_id, enforcer_id, DECISION_CONFIRM, "Parked in the zone.", "park"
    )
    _, obstruction_id = _review(test_db, VIOLATION_OBSTRUCTION, video_id=video_id)
    _, second_id = _review(test_db, VIOLATION_COUNTERFLOW, track_id=42)
    try:
        with flask_app.app.test_client() as client:
            login = client.post("/login", data={"username": "race_ui", "password": "enforcer123"})
            assert login.status_code in (200, 302)

            # Defect A through the legacy confirm route: the competing dismiss
            # cannot land inside the group/reuse window and loses with 409.
            window = _compete_inside_window(
                monkeypatch,
                case_review_service,
                "find_existing_fused_case_violation_id",
                db_path,
                obstruction_id,
                lambda: _decide(
                    test_db, obstruction_id, enforcer_id, DECISION_NO_VIOLATION,
                    "Not blocking.", "legacy-none",
                ),
            )
            confirmed = client.post(f"/api/review-queue/{obstruction_id}/confirm")
            competitor = window["competitor"].join()
            assert confirmed.status_code == 200
            assert confirmed.get_json()["decision"] == DECISION_CONFIRM
            assert confirmed.get_json()["violation_id"] == parked["violation_id"]
            assert window["committed_in_window"] is None
            assert isinstance(competitor.error, ReviewDecisionConflict)
            before = _snapshot(test_db)
            assert client.post(f"/api/review-queue/{obstruction_id}/dismiss").status_code == 409
            assert _snapshot(test_db) == before

            # Defect B through the legacy confirm route.
            seam = _compete_after_first_decision_read(
                monkeypatch,
                test_db,
                second_id,
                lambda: _decide(
                    test_db, second_id, enforcer_id, DECISION_CORRECT,
                    "Stopped in the lane.", "legacy-corr", VIOLATION_OBSTRUCTION,
                ),
            )
            conflict = client.post(f"/api/review-queue/{second_id}/confirm")
            assert conflict.status_code == 409
            assert seam["competitor"].error is None
            assert _snapshot(test_db) == seam["after_winner"]
    finally:
        flask_app.stop_processing_worker()
