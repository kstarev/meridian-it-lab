from datetime import UTC, datetime, timedelta

import pytest

from tier0.clock import MockClock, format_time, parse_time

START = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


def test_naive_start_rejected() -> None:
    with pytest.raises(ValueError):
        MockClock(datetime(2026, 11, 2, 9, 0))


def test_advance_and_advance_to() -> None:
    clock = MockClock(START)
    clock.advance(timedelta(days=3))
    assert clock.now() == START + timedelta(days=3)
    clock.advance_to(START + timedelta(days=5))
    assert clock.now() == START + timedelta(days=5)


def test_cannot_go_backwards() -> None:
    clock = MockClock(START)
    with pytest.raises(ValueError):
        clock.advance(timedelta(seconds=-1))
    with pytest.raises(ValueError):
        clock.advance_to(START - timedelta(seconds=1))


def test_format_parse_round_trip() -> None:
    text = "2026-11-02T09:00:00.000Z"
    parsed = parse_time(text)
    assert parsed == START
    assert format_time(parsed) == text
