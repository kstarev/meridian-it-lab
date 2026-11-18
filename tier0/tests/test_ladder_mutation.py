"""The same-day count's edge in `ContextChecks.preload`, pinned (ADR-043).

`preload` keeps only times inside the window: later than one window before now. The mutation
run of 2026-11-18 (docs/evals/2026-11-18-safety-mutation-testing.md) changed `>` to `>=` there
and no test failed, because a time loaded into an empty count is dropped again by the next
check. It is not dropped when the count already holds a newer request, as here. Mocks and
made-up people only.
"""

from datetime import UTC, datetime, timedelta

import pytest

from tier0.clock import MockClock
from tier0.identity.binding import BoundIdentity
from tier0.mocks.calendar import MockCalendar
from tier0.mocks.device_assurance import MockDeviceAssurance
from tier0.mocks.security_events import MockSecurityEvents
from tier0.verification.context import SAME_DAY_REPEAT, ContextChecks, ContextSettings

T0 = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
WINDOW = timedelta(hours=24)
ANA = BoundIdentity("00u-ana", "ana.made-up@meridian.example", "U1", "T1")
ACTION = "own_username"


def _checks(clock: MockClock, limit: int) -> ContextChecks:
    return ContextChecks(
        clock=clock,
        calendar=MockCalendar(clock),
        devices=MockDeviceAssurance(clock),
        security_events=MockSecurityEvents(clock),
        settings=ContextSettings(same_day_limit=limit, same_day_window=WINDOW),
    )


@pytest.mark.parametrize(
    ("age", "counted"),
    [(WINDOW, False), (WINDOW - timedelta(microseconds=1), True)],
    ids=["exactly one window old", "one microsecond younger"],
)
def test_a_loaded_time_counts_only_when_it_is_inside_the_window(
    age: timedelta, counted: bool
) -> None:
    clock = MockClock(T0)
    checks = _checks(clock, limit=2)
    assert checks.check(ANA, action=ACTION).ok  # 1 request in the window
    clock.advance_to(T0 + timedelta(hours=1))
    checks.preload(ANA.directory_id, ACTION, [clock.now() - age])
    # The request now is the 2nd, or the 3rd when the loaded time counts.
    findings = checks.check(ANA, action=ACTION).findings
    assert findings == ((SAME_DAY_REPEAT,) if counted else ())
