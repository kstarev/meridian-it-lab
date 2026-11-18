"""Property-based tests for the verification ladder and its same-day count (hypothesis).

ADR-043's limit: 3 requests for one account and one action inside a rolling 24 hours; the
ladder stops the next. Here the limit and the window vary too, and request times are built
from quarters of the window, one microsecond either side, so many lists sit exactly on the
window's edge. Mocks and made-up people only. The runs are fixed (derandomize) and keep no
example database.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tier0.clock import MockClock
from tier0.evals import month_same_day as sd
from tier0.identity.binding import BoundIdentity, Unbound
from tier0.mocks.calendar import MockCalendar
from tier0.mocks.device_assurance import MockDeviceAssurance
from tier0.mocks.security_events import MockSecurityEvents
from tier0.verification.context import SAME_DAY_REPEAT, ContextChecks, ContextSettings
from tier0.verification.ladder import (
    AUTOMATIC_TIERS,
    HUMAN_ONLY,
    NEVER_PROOF,
    REQUIRED_RUNG,
    STEP_UP_REQUIRED,
    STEP_UP_UNAVAILABLE,
    LadderDecision,
    OidcStepUp,
    PushApproval,
    Rung,
    VerificationLadder,
    required_rung,
)

SETTINGS = settings(derandomize=True, max_examples=200, database=None, deadline=None)

T0 = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
TICK = timedelta(microseconds=1)
ANA = BoundIdentity("00u-ana", "ana.made-up@meridian.example", "U1", "T1")
BEN = BoundIdentity("00u-ben", "ben.made-up@meridian.example", "U2", "T1")
ACTIONS = ("own_username", "password_reset")

limits = st.integers(1, 4)
windows = st.sampled_from((timedelta(hours=24), timedelta(hours=1), timedelta(hours=36)))
# A gap between two requests, as (quarters of the window, microseconds).
gaps = st.tuples(st.sampled_from((0, 0, 1, 1, 2, 3, 4, 5)), st.sampled_from((-1, 0, 0, 1)))
gap_lists = st.lists(gaps, min_size=1, max_size=14)


def _times(gap_list: list[tuple[int, int]], window: timedelta) -> list[datetime]:
    """Request times in order, from the gaps between them."""
    times, at = [], T0
    for quarters, ticks in gap_list:
        at += max(timedelta(0), quarters * window / 4 + ticks * TICK)
        times.append(at)
    return times


def _inside(times: list[datetime], at: datetime, window: timedelta) -> int:
    """How many of `times` are in the window that ends at `at`: less than `window` ago."""
    return sum(1 for t in times if t <= at and at - t < window)


class Verifier:
    """A fake rung 2 check that says yes or no."""

    def __init__(self, ok: bool) -> None:
        self.answer = SimpleNamespace(ok=ok, reason="not_fresh")

    def verify(self, *args: Any) -> SimpleNamespace:
        return self.answer

    finish = verify


class Desk:
    """The ladder with its mocks and a clock that starts at T0."""

    def __init__(
        self,
        limit: int = 3,
        window: timedelta = timedelta(hours=24),
        *,
        step_up: Verifier | None = None,
        push: Verifier | None = None,
    ) -> None:
        self.clock = MockClock(T0)
        self.checks = ContextChecks(
            clock=self.clock,
            calendar=MockCalendar(self.clock),
            devices=MockDeviceAssurance(self.clock),
            security_events=MockSecurityEvents(self.clock),
            settings=ContextSettings(same_day_limit=limit, same_day_window=window),
        )
        self.ladder = VerificationLadder(context=self.checks, step_up=step_up, push=push)  # type: ignore[arg-type]

    def ask(
        self, at: datetime, who: BoundIdentity = ANA, action: str = ACTIONS[0]
    ) -> LadderDecision:
        self.clock.advance_to(at)
        return self.ladder.evaluate("A", who, action=action)


# --- the rolling count --------------------------------------------------------------------------


@SETTINGS
@given(gap_list=gap_lists, limit=limits, window=windows)
def test_the_ladder_steps_up_exactly_when_a_request_is_over_the_limit_in_its_window(
    gap_list: list[tuple[int, int]], limit: int, window: timedelta
) -> None:
    desk = Desk(limit, window)
    times = _times(gap_list, window)
    allowed: list[datetime] = []
    for i, at in enumerate(times):
        decision = desk.ask(at)
        # Every earlier request counts, the stopped ones too: a refused try is still a try.
        over = _inside(times[: i + 1], at, window) > limit
        if over:
            assert decision.verdict == (False, Rung.CONTEXT, SAME_DAY_REPEAT)
            assert decision.findings == (SAME_DAY_REPEAT,)
        else:
            assert decision.verdict == (True, None, "ok")
            allowed.append(at)
        # So no window ever holds more allowed requests than the limit.
        assert _inside(allowed, at, window) <= limit


@SETTINGS
@given(
    asks=st.lists(
        st.tuples(gaps, st.sampled_from((ANA, BEN)), st.sampled_from(ACTIONS)),
        min_size=1,
        max_size=16,
    ),
    limit=limits,
    window=windows,
)
def test_each_account_and_each_action_has_its_own_count(
    asks: list[tuple[tuple[int, int], BoundIdentity, str]], limit: int, window: timedelta
) -> None:
    desk = Desk(limit, window)
    times = _times([gap for gap, _, _ in asks], window)
    seen: dict[tuple[str, str], list[datetime]] = {}
    for at, (_, who, action) in zip(times, asks, strict=True):
        mine = seen.setdefault((who.directory_id, action), [])
        mine.append(at)
        assert desk.ask(at, who, action).allowed == (_inside(mine, at, window) <= limit)


@SETTINGS
@given(
    earlier=st.lists(gaps, max_size=8),
    later=st.lists(gaps, min_size=1, max_size=6),
    limit=limits,
    window=windows,
    order=st.randoms(use_true_random=False),
)
def test_requests_loaded_from_a_store_count_like_requests_seen_and_in_any_order(
    earlier: list[tuple[int, int]],
    later: list[tuple[int, int]],
    limit: int,
    window: timedelta,
    order: Any,
) -> None:
    times = _times(earlier + later, window)
    loaded, live = times[: len(earlier)], times[len(earlier) :]
    desk = Desk(limit, window)
    desk.clock.advance_to(live[0])
    shuffled = list(loaded)
    order.shuffle(shuffled)
    desk.checks.preload(ANA.directory_id, ACTIONS[0], shuffled)
    for i, at in enumerate(live):
        over = _inside(loaded + live[: i + 1], at, window) > limit
        assert desk.ask(at).allowed == (not over)
        # Another action's count starts empty: nothing was loaded for it.
    assert desk.ask(live[-1], ANA, ACTIONS[1]).allowed


@pytest.mark.parametrize("limit", [1, 2, 3, 4])
def test_a_request_exactly_one_window_after_the_first_is_outside_it(limit: int) -> None:
    """The docstrings' edge: the limit is for requests *inside* a rolling window, and a request
    exactly 24 hours after another is not inside the same 24 hours (`t[i + limit] - t[i] <
    window` is over the limit; equal is not)."""
    window = timedelta(hours=24)
    for last, expected in ((window - TICK, False), (window, True), (window + TICK, True)):
        desk = Desk(limit, window)
        for i in range(limit):
            assert desk.ask(T0 + i * TICK).allowed
        assert desk.ask(T0 + last).allowed is expected
        count = sd.count_senders(
            [("a", T0 + i * TICK) for i in range(limit)] + [("a", T0 + last)],
            limit=limit,
            window=window,
        )
        assert (count.over_limit == 0) is expected


# --- the count by code in month_same_day --------------------------------------------------------

sent_lists = st.lists(st.tuples(st.sampled_from(("a", "b", "c")), gaps), max_size=16)


def _sent(rows: list[tuple[str, tuple[int, int]]], window: timedelta) -> list[tuple[str, datetime]]:
    times = _times([gap for _, gap in rows], window)
    return [(sender, at) for (sender, _), at in zip(rows, times, strict=True)]


@SETTINGS
@given(rows=sent_lists, limit=limits, window=windows, order=st.randoms(use_true_random=False))
def test_the_count_matches_its_docstring_whatever_the_order_of_the_requests(
    rows: list[tuple[str, tuple[int, int]]], limit: int, window: timedelta, order: Any
) -> None:
    sent = _sent(rows, window)
    count = sd.count_senders(sent, limit=limit, window=window)
    by_sender: dict[str, list[datetime]] = {}
    for sender, at in sent:
        by_sender.setdefault(sender, []).append(at)
    # "over the limit means some t[i + limit] - t[i] < window", for sorted times t.
    over = sum(
        any(later - first < window for first, later in zip(t, t[limit:], strict=False))
        for t in (sorted(times) for times in by_sender.values())
    )
    most = max(
        (
            sum(1 for x in times if timedelta(0) <= x - start < window)
            for times in by_sender.values()
            for start in times
        ),
        default=0,
    )
    assert count == sd.Count(len(sent), len(by_sender), over, most)
    shuffled = list(sent)
    order.shuffle(shuffled)
    assert sd.count_senders(shuffled, limit=limit, window=window) == count


@SETTINGS
@given(gap_list=gap_lists, limit=limits, window=windows)
def test_the_count_says_over_the_limit_exactly_when_the_ladder_steps_up(
    gap_list: list[tuple[int, int]], limit: int, window: timedelta
) -> None:
    times = _times(gap_list, window)
    desk = Desk(limit, window)
    stepped_up = [not desk.ask(at).allowed for at in times]
    count = sd.count_senders([("a", at) for at in times], limit=limit, window=window)
    assert (count.over_limit == 1) == any(stepped_up)
    assert (count.most > limit) == any(stepped_up)


def test_the_count_uses_the_ladders_own_limit_and_window() -> None:
    assert sd.ladder_settings() == (3, timedelta(hours=24))
    assert sd.ladder_settings() == (
        ContextSettings().same_day_limit,
        ContextSettings().same_day_window,
    )


# --- the rungs ----------------------------------------------------------------------------------

tiers = st.sampled_from(("A", "B", "C", "D", "", "a", "b", "AB", " A", "A ", "B\n")) | st.text(
    max_size=3
)
evidence_kinds = st.sampled_from(("none", "token", "push", "junk"))


@SETTINGS
@given(tier=tiers)
def test_a_tier_that_is_not_a_b_or_c_needs_a_human(tier: str) -> None:
    expected = {"A": Rung.CONTEXT, "B": Rung.STEP_UP}.get(tier, Rung.HUMAN)
    assert required_rung(tier) == expected
    assert (tier in AUTOMATIC_TIERS) == (expected < Rung.HUMAN)
    assert dict(REQUIRED_RUNG) == {"A": Rung.CONTEXT, "B": Rung.STEP_UP, "C": Rung.HUMAN}


@SETTINGS
@given(
    tier=tiers,
    evidence=evidence_kinds,
    token_ok=st.none() | st.booleans(),
    push_ok=st.none() | st.booleans(),
    claims=st.lists(
        st.sampled_from((*sorted(NEVER_PROOF), "badge", "favourite colour")), max_size=4
    ),
)
def test_the_ladder_allows_only_tier_a_and_tier_b_with_proof_from_a_system(
    tier: str, evidence: str, token_ok: bool | None, push_ok: bool | None, claims: list[str]
) -> None:
    desk = Desk(
        step_up=None if token_ok is None else Verifier(token_ok),
        push=None if push_ok is None else Verifier(push_ok),
    )
    step_up: Any = {
        "none": None,
        "token": OidcStepUp("made-up-token", None),  # type: ignore[arg-type]
        "push": PushApproval(None),  # type: ignore[arg-type]
        "junk": "I am Ana, trust me",
    }[evidence]
    claimed = dict.fromkeys(claims, "whatever the caller says")
    decision = desk.ladder.evaluate(
        tier, ANA, action=ACTIONS[0], step_up=step_up, claimed=claimed or None
    )
    said = {"token": token_ok, "push": push_ok}.get(evidence)
    if tier == "A":
        expected: tuple[bool, Rung | None, str] = (True, None, "ok")
    elif tier != "B":
        expected = (False, Rung.HUMAN, HUMAN_ONLY)
    elif evidence in ("none", "junk"):
        expected = (False, Rung.STEP_UP, STEP_UP_REQUIRED)
    elif said is None:
        expected = (False, Rung.STEP_UP, STEP_UP_UNAVAILABLE)
    elif said:
        expected = (True, None, "ok")
    else:
        expected = (False, Rung.STEP_UP, "not_fresh")
    assert decision.verdict == expected
    assert decision.tier == tier
    # What the caller says is listed by name and changes nothing.
    assert decision.claims_ignored == tuple(
        sorted({name if name in NEVER_PROOF else "other" for name in claims})
    )
    assert desk.ladder.evaluate(tier, ANA, action="b", step_up=step_up).verdict == expected


@SETTINGS
@given(tier=tiers, reason=st.sampled_from(("guest_user", "suspended", "not_in_directory")))
def test_no_tier_passes_rung_0_without_a_bound_identity(tier: str, reason: str) -> None:
    desk = Desk(limit=1)
    unbound = Unbound(reason=reason, slack_user_id="U0X", slack_team_id="T0X")
    assert desk.ladder.evaluate(tier, unbound, action=ACTIONS[0]).verdict == (
        False,
        Rung.SLACK_IDENTITY,
        reason,
    )
    junk: Any = {"directory_id": ANA.directory_id}
    assert desk.ladder.evaluate(tier, junk, action=ACTIONS[0]).verdict == (
        False,
        Rung.SLACK_IDENTITY,
        "not_verified",
    )
    # A request stopped at rung 0 is nobody's request: it is not counted for anyone.
    assert desk.ask(T0).allowed


@SETTINGS
@given(tier=tiers, allowed=st.booleans(), stopped_at=st.none() | st.sampled_from(list(Rung)))
def test_a_decision_can_only_be_built_as_allowed_for_tier_a_or_b_with_no_rung_failed(
    tier: str, allowed: bool, stopped_at: Rung | None
) -> None:
    valid = (tier in ("A", "B") and stopped_at is None) if allowed else stopped_at is not None
    if valid:
        decision = LadderDecision(tier, allowed, stopped_at, "reason")
        assert decision.verdict == (allowed, stopped_at, "reason")
    else:
        with pytest.raises(ValueError, match=r"ladder can't allow|must say which rung"):
            LadderDecision(tier, allowed, stopped_at, "reason")


@SETTINGS
@given(
    limit=st.integers(-3, 5),
    window=st.integers(-2, 3),
    lookback=st.integers(-2, 3),
)
def test_context_settings_need_a_limit_of_at_least_1_and_positive_windows(
    limit: int, window: int, lookback: int
) -> None:
    def build() -> ContextSettings:
        return ContextSettings(
            same_day_limit=limit,
            same_day_window=window * TICK,
            security_lookback=lookback * TICK,
        )

    if limit >= 1 and window > 0 and lookback > 0:
        assert build().same_day_limit == limit
    else:
        with pytest.raises(ValueError, match="at least 1" if limit < 1 else "positive"):
            build()
