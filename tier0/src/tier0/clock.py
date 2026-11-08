"""Time for tier0: a clock interface, a controllable mock, and one time format."""

from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    """Anything that can tell the current time."""

    def now(self) -> datetime: ...


class MockClock:
    """A controllable clock.

    Evals can jump 3 days ahead for the ticket sweeper and tests can expire
    tokens. Nothing in tier0 reads the wall clock directly, so runs repeat.
    """

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None or start.utcoffset() is None:
            raise ValueError("start must be timezone-aware")
        self._now = start.astimezone(UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        if delta < timedelta(0):
            raise ValueError("cannot advance by a negative amount")
        self._now += delta

    def advance_to(self, moment: datetime) -> None:
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError("moment must be timezone-aware")
        target = moment.astimezone(UTC)
        if target < self._now:
            raise ValueError("cannot move the clock backwards")
        self._now = target


def format_time(moment: datetime) -> str:
    """Format as UTC with milliseconds, e.g. 2026-11-02T09:00:00.000Z."""
    utc = moment.astimezone(UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


def parse_time(value: str) -> datetime:
    """Inverse of format_time; accepts the trailing Z and returns aware UTC."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"time has no timezone: {value!r}")
    return parsed.astimezone(UTC)
