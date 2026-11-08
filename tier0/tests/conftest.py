"""Shared fixtures: one seeded world, a clock at its reference time, and the mocks."""

import pytest

from meridian_dataset import DEFAULT_SEED, World, generate
from tier0.clock import MockClock, parse_time
from tier0.mocks.directory import MockDirectory
from tier0.mocks.slack import MockSlack


@pytest.fixture
def world() -> World:
    return generate(DEFAULT_SEED)


@pytest.fixture
def clock(world: World) -> MockClock:
    return MockClock(parse_time(world.reference_time))


@pytest.fixture
def directory(world: World, clock: MockClock) -> MockDirectory:
    return MockDirectory(world, clock)


@pytest.fixture
def slack(world: World, clock: MockClock) -> MockSlack:
    return MockSlack(world, clock)
