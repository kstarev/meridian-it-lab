"""Shared fixtures: one seeded world, a clock at its reference time, and the mocks."""

import pytest

from meridian_dataset import DEFAULT_SEED, World, generate
from meridian_dataset.month import HELD_OUT_MONTH1, HELD_OUT_MONTH2, HELD_OUT_MONTH2B
from tier0.clock import MockClock, parse_time
from tier0.evals import month as month_eval
from tier0.mocks.directory import MockDirectory
from tier0.mocks.linear import MockLinear
from tier0.mocks.slack import MockSlack
from tier0.roles import role_reference
from tier0.tickets.linear import LinearTicketAdapter


@pytest.fixture
def world() -> World:
    return generate(DEFAULT_SEED)


def _colleague_names_someone(world: World) -> bool:
    ticket = next(t for t in world.tickets if t.id == "TKT-005")
    return any(
        p.full_name in ticket.text
        for p in world.people
        if p.slack_user_id != ticket.sender_slack_user_id
    )


def _colleague_has_role_word(world: World) -> bool:
    return role_reference(next(t.text for t in world.tickets if t.id == "TKT-005"))


def _first_tuning_world(named: bool, role: bool | None = None) -> World:
    # Tuning seeds only: the held-out seeds (2611, 3107) are far outside this range.
    for seed in range(DEFAULT_SEED, DEFAULT_SEED + 100):
        world = generate(seed)
        if world.message_pool != "tuning" or _colleague_names_someone(world) != named:
            continue
        if role is None or _colleague_has_role_word(world) == role:
            return world
    raise AssertionError("no such tuning world")


@pytest.fixture
def named_colleague_world() -> World:
    """A tuning world whose colleague request (TKT-005) names the colleague."""
    return _first_tuning_world(named=True)


@pytest.fixture
def unnamed_colleague_world() -> World:
    """A tuning world whose colleague request (TKT-005) names nobody (ADR-023)."""
    return _first_tuning_world(named=False)


@pytest.fixture
def role_word_colleague_world() -> World:
    """Unnamed, but with a person word near a login word ("our intern ... their login")."""
    return _first_tuning_world(named=False, role=True)


@pytest.fixture
def no_code_signal_colleague_world() -> World:
    """Unnamed and with no person word: only the model check can stop it (ADR-024)."""
    return _first_tuning_world(named=False, role=False)


@pytest.fixture
def clock(world: World) -> MockClock:
    return MockClock(parse_time(world.reference_time))


@pytest.fixture
def directory(world: World, clock: MockClock) -> MockDirectory:
    return MockDirectory(world, clock)


@pytest.fixture
def slack(world: World, clock: MockClock) -> MockSlack:
    return MockSlack(world, clock)


@pytest.fixture
def linear(clock: MockClock) -> MockLinear:
    return MockLinear(clock)


@pytest.fixture
def tickets(linear: MockLinear) -> LinearTicketAdapter:
    return LinearTicketAdapter(linear)


@pytest.fixture
def sealed_month1(monkeypatch: pytest.MonkeyPatch) -> None:
    """held_out_month1 stands in for a sealed set, so the sealed machinery stays tested."""
    monkeypatch.setattr(month_eval, "SEALED_SETS", frozenset({HELD_OUT_MONTH1}))
    monkeypatch.setattr(month_eval, "SPENT_SETS", {})


@pytest.fixture
def sealed_month2(monkeypatch: pytest.MonkeyPatch) -> None:
    """held_out_month2 and held_out_month2b stand in for sealed sets again, so their frozen plan
    (spent on 2026-11-19, ADR-052) stays tested."""
    monkeypatch.setattr(month_eval, "SEALED_SETS", frozenset({HELD_OUT_MONTH2, HELD_OUT_MONTH2B}))
    monkeypatch.setattr(month_eval, "SPENT_SETS", {HELD_OUT_MONTH1: "2026-11-17"})
