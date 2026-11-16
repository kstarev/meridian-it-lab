"""The kill switch, the rate limiter and the audit log, on their own (ADR-008)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tier0.audit import MAX_REASON_LENGTH, AuditLog
from tier0.clock import MockClock, format_time
from tier0.controls import (
    GLOBAL,
    KIND_REQUEST,
    KIND_TOOL_CALL,
    REQUESTER,
    RateLimiter,
    RateLimitSettings,
    build_controls,
)

START = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
ANA = "1001"
BEN = "1002"


@pytest.fixture
def clock() -> MockClock:
    return MockClock(START)


def _limiter(clock: MockClock, **settings: int) -> tuple[RateLimiter, AuditLog]:
    controls = build_controls(clock, rate_limits=RateLimitSettings(**settings))  # type: ignore[arg-type]
    return controls.rate_limiter, controls.audit


def _take(limiter: RateLimiter, who: str, times: int, kind: str = KIND_TOOL_CALL) -> list[bool]:
    return [limiter.acquire(who, kind=kind, source="test").allowed for _ in range(times)]  # type: ignore[arg-type]


# --- the audit log -------------------------------------------------------------------


def test_audit_entries_say_who_when_why_and_can_only_be_added(clock: MockClock) -> None:
    log = AuditLog(clock)
    first = log.record("kill_switch.on", actor="U0ITLEAD", reason="looping", changed=True)
    clock.advance(timedelta(minutes=5))
    log.record("rate_limit.hit", actor="tier0-mcp", scope="global")
    assert (first.at, first.actor, first.reason) == (format_time(START), "U0ITLEAD", "looping")
    assert [e.event for e in log.entries] == ["kill_switch.on", "rate_limit.hit"]
    assert [e.event for e in log.events("rate_limit.")] == ["rate_limit.hit"]
    # Nothing can edit or drop an entry: the list handed out is a copy, entries are frozen.
    assert not any(hasattr(log, name) for name in ("clear", "reset", "remove", "delete"))
    with pytest.raises(AttributeError):
        first.actor = "someone else"  # type: ignore[misc]
    with pytest.raises(TypeError):
        first.details["changed"] = False  # type: ignore[index]


def test_audit_refuses_content_and_anonymous_entries(clock: MockClock) -> None:
    log = AuditLog(clock)
    with pytest.raises(ValueError, match="actor"):
        log.record("kill_switch.on", actor="  ")
    with pytest.raises(ValueError, match="content"):
        log.record("rate_limit.hit", actor="tier0-mcp", requester="ana@meridian.example")
    with pytest.raises(ValueError, match="content"):
        log.record("rate_limit.hit", actor="tier0-mcp", note="x" * 65)
    long = log.record("kill_switch.on", actor="U0ITLEAD", reason="r" * 500)
    assert long.reason is not None and len(long.reason) == MAX_REASON_LENGTH
    assert len(log.entries) == 1


def test_audit_log_can_also_be_written_to_a_file(clock: MockClock, tmp_path: Path) -> None:
    path = tmp_path / "audit" / "audit.jsonl"
    log = AuditLog(clock, path=path)
    log.record("kill_switch.on", actor="U0ITLEAD", reason="drill", changed=True)
    log.record("kill_switch.off", actor="U0ITLEAD", reason="drill over", changed=True)
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [line["event"] for line in lines] == ["kill_switch.on", "kill_switch.off"]
    assert lines[0] == {
        "at": format_time(START),
        "event": "kill_switch.on",
        "actor": "U0ITLEAD",
        "reason": "drill",
        "details": {"changed": True},
    }


# --- the kill switch -----------------------------------------------------------------


def test_kill_switch_starts_off_and_every_toggle_is_logged(clock: MockClock) -> None:
    controls = build_controls(clock)
    switch = controls.kill_switch
    assert not switch.is_on and switch.since is None and controls.audit.entries == ()

    switch.turn_on(by="U0ITLEAD", reason="agent is looping")
    clock.advance(timedelta(minutes=2))
    switch.turn_on(by="U0ITENG1", reason="saw it too")  # changes nothing, still logged
    assert switch.is_on and switch.since == START
    clock.advance(timedelta(minutes=30))
    switch.turn_off(by="U0ITLEAD", reason="fixed and checked")
    assert not switch.is_on and switch.since is None

    log = controls.audit.entries
    assert [(e.event, e.actor, e.reason, e.details["changed"]) for e in log] == [
        ("kill_switch.on", "U0ITLEAD", "agent is looping", True),
        ("kill_switch.on", "U0ITENG1", "saw it too", False),
        ("kill_switch.off", "U0ITLEAD", "fixed and checked", True),
    ]
    assert [e.at for e in log] == [
        format_time(START),
        format_time(START + timedelta(minutes=2)),
        format_time(START + timedelta(minutes=32)),
    ]


@pytest.mark.parametrize(("by", "reason"), [("", "why"), ("U0ITLEAD", ""), ("  ", "  ")])
def test_a_toggle_needs_a_name_and_a_reason(clock: MockClock, by: str, reason: str) -> None:
    controls = build_controls(clock)
    with pytest.raises(ValueError, match="must not be empty"):
        controls.kill_switch.turn_on(by=by, reason=reason)
    assert not controls.kill_switch.is_on and controls.audit.entries == ()


# --- the rate limiter ----------------------------------------------------------------


def test_default_limits_are_five_per_requester_and_thirty_for_everyone() -> None:
    settings = RateLimitSettings()
    assert (settings.per_requester_limit, settings.global_limit) == (5, 30)
    assert settings.window == timedelta(hours=1)
    assert settings.max_override == timedelta(hours=24)


def test_per_requester_limit_refuses_the_action_over_it_and_logs_the_hit(
    clock: MockClock,
) -> None:
    limiter, audit = _limiter(clock, per_requester_limit=3, global_limit=100)
    assert _take(limiter, ANA, 5) == [True, True, True, False, False]
    assert _take(limiter, BEN, 1) == [True]  # someone else is not affected
    assert limiter.count(KIND_TOOL_CALL, ANA) == 3  # refused actions are not counted
    hits = audit.events("rate_limit.hit")
    assert len(hits) == 2
    assert dict(hits[0].details) == {
        "scope": REQUESTER,
        "kind": KIND_TOOL_CALL,
        "requester": ANA,
        "limit": 3,
        "window_seconds": 3600,
    }
    assert hits[0].actor == "test" and hits[0].reason is None


def test_global_limit_counts_everyone_together(clock: MockClock) -> None:
    limiter, audit = _limiter(clock, per_requester_limit=2, global_limit=5)
    allowed = [_take(limiter, str(2000 + n), 1)[0] for n in range(8)]
    assert allowed == [True] * 5 + [False] * 3
    assert limiter.count(KIND_TOOL_CALL) == 5
    assert {h.details["scope"] for h in audit.events("rate_limit.hit")} == {GLOBAL}


def test_the_window_rolls_on_the_injected_clock(clock: MockClock) -> None:
    limiter, _ = _limiter(clock, per_requester_limit=2, global_limit=100)
    assert _take(limiter, ANA, 1) == [True]
    clock.advance(timedelta(minutes=40))
    assert _take(limiter, ANA, 2) == [True, False]
    clock.advance(timedelta(minutes=20))  # the first action is now an hour old
    assert _take(limiter, ANA, 2) == [True, False]
    clock.advance(timedelta(hours=1))
    assert limiter.count(KIND_TOOL_CALL, ANA) == 0
    assert _take(limiter, ANA, 3) == [True, True, False]


def test_a_flood_does_not_lock_a_requester_out_past_the_window(clock: MockClock) -> None:
    limiter, _ = _limiter(clock, per_requester_limit=1, global_limit=100)
    assert _take(limiter, ANA, 50) == [True] + [False] * 49
    clock.advance(timedelta(hours=1))
    assert _take(limiter, ANA, 1) == [True]


def test_requests_and_tool_calls_are_counted_apart(clock: MockClock) -> None:
    limiter, _ = _limiter(clock, per_requester_limit=2, global_limit=100)
    assert _take(limiter, ANA, 3, KIND_REQUEST) == [True, True, False]
    assert _take(limiter, ANA, 3, KIND_TOOL_CALL) == [True, True, False]


def test_a_person_can_raise_a_limit_for_a_while_and_it_is_logged(clock: MockClock) -> None:
    limiter, audit = _limiter(clock, per_requester_limit=1, global_limit=2)
    assert _take(limiter, ANA, 1) + _take(limiter, BEN, 1) == [True, True]
    assert _take(limiter, "1003", 1) == [False]  # the global limit

    limiter.override(
        scope=GLOBAL,
        limit=4,
        by="U0ITLEAD",
        reason="onboarding 20 new hires",
        lasts=timedelta(hours=2),
    )
    assert limiter.limit_for(GLOBAL) == 4
    assert _take(limiter, "1003", 1) + _take(limiter, "1004", 1) == [True, True]
    assert _take(limiter, "1005", 1) == [False]

    (entry,) = audit.events("rate_limit.override")
    assert (entry.actor, entry.reason) == ("U0ITLEAD", "onboarding 20 new hires")
    assert dict(entry.details) == {
        "scope": GLOBAL,
        "limit": 4,
        "default": 2,
        "expires_at": format_time(START + timedelta(hours=2)),
    }

    # It ends by itself, and that is logged too.
    clock.advance(timedelta(hours=2))
    assert limiter.limit_for(GLOBAL) == 2
    assert _take(limiter, ANA, 1) + _take(limiter, BEN, 1) == [True, True]
    assert _take(limiter, "1003", 1) == [False]
    (expired,) = audit.events("rate_limit.override_expired")
    assert (expired.actor, expired.details["scope"]) == ("tier0-controls", GLOBAL)


def test_one_requesters_limit_can_be_raised_and_cleared(clock: MockClock) -> None:
    limiter, audit = _limiter(clock, per_requester_limit=1, global_limit=100)
    limiter.override(
        scope=ANA, limit=3, by="U0ITENG1", reason="testing her new laptop", lasts=timedelta(hours=1)
    )
    assert _take(limiter, ANA, 4) == [True, True, True, False]
    assert _take(limiter, BEN, 2) == [True, False]  # only Ana's limit moved
    limiter.clear_override(scope=ANA, by="U0ITENG1", reason="done")
    limiter.clear_override(scope=ANA, by="U0ITENG1", reason="again")  # nothing to clear
    assert limiter.limit_for(ANA) == 1
    (raised,) = audit.events("rate_limit.override")
    assert (raised.details["scope"], raised.details["requester"]) == (REQUESTER, ANA)
    cleared = audit.events("rate_limit.override_cleared")
    assert [(e.actor, e.reason, e.details["changed"]) for e in cleared] == [
        ("U0ITENG1", "done", True),
        ("U0ITENG1", "again", False),
    ]


@pytest.mark.parametrize(
    "bad",
    [
        {"limit": 0},
        {"by": ""},
        {"reason": " "},
        {"lasts": timedelta(0)},
        {"lasts": timedelta(hours=25)},
        {"scope": ""},
    ],
    ids=["zero_limit", "no_name", "no_reason", "no_time", "too_long", "no_scope"],
)
def test_a_bad_override_is_refused_and_changes_nothing(
    clock: MockClock, bad: dict[str, object]
) -> None:
    limiter, audit = _limiter(clock)
    good: dict[str, object] = {
        "scope": GLOBAL,
        "limit": 50,
        "by": "U0ITLEAD",
        "reason": "cohort",
        "lasts": timedelta(hours=1),
    }
    with pytest.raises(ValueError, match=r"must|at least"):
        limiter.override(**{**good, **bad})  # type: ignore[arg-type]
    assert limiter.limit_for(GLOBAL) == 30 and audit.entries == ()


def test_limits_must_be_positive() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        RateLimitSettings(per_requester_limit=0)
    with pytest.raises(ValueError, match="positive"):
        RateLimitSettings(window=timedelta(0))


def test_an_address_never_reaches_the_audit_log(clock: MockClock) -> None:
    limiter, audit = _limiter(clock, per_requester_limit=1)
    assert _take(limiter, "ana@meridian.example", 2) == [True, False]
    (hit,) = audit.events("rate_limit.hit")
    assert hit.details["requester"] == "hidden"
