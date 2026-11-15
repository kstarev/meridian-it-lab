"""Rung 1 of the verification ladder: context checks, in code only.

A signed Slack user ID (rung 0) says which account sent the message. It does not
say the right person is at the keyboard. These checks look for signs that
something is off:

- **Out of office:** the calendar says the requester is away right now.
- **Recent security change:** the account's sign-in methods or password changed
  lately, or the identity provider flagged it.
- **Managed device:** the session is on a device IT does not manage, or one that
  fails the device policy.
- **Same-day repeats:** more requests for one account in a day than is normal,
  the pattern in Clorox v. Cognizant (2025).

Any finding hands the ticket to a person. So does a source that cannot answer
(fail closed). No model is involved and no message text is read.

Repeats are counted in memory, so a restart forgets them. That is a known limit.
"""

from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from tier0.clock import Clock, parse_time
from tier0.identity.binding import BoundIdentity

LADDER_PRINCIPAL = "tier0-ladder"

OK = "ok"
OUT_OF_OFFICE = "out_of_office"
RECENT_SECURITY_CHANGE = "recent_security_change"
UNMANAGED_DEVICE = "unmanaged_device"
NONCOMPLIANT_DEVICE = "noncompliant_device"
SAME_DAY_REPEAT = "same_day_repeat"
CALENDAR_UNAVAILABLE = "calendar_unavailable"
SECURITY_EVENTS_UNAVAILABLE = "security_events_unavailable"
DEVICE_UNAVAILABLE = "device_unavailable"

# Okta System Log event types that count as a recent security change.
SECURITY_CHANGE_EVENTS = frozenset(
    {
        "user.mfa.factor.activate",
        "user.mfa.factor.deactivate",
        "user.mfa.factor.reset_all",
        "user.account.update_password",
        "user.account.reset_password",
        "user.account.report_suspicious_activity_by_enduser",
        "security.threat.detected",
    }
)


class ContextSourceError(Exception):
    """A context source (calendar, devices, security events) could not answer."""


class CalendarApi(Protocol):
    def events_list(
        self,
        calendar_id: str,
        *,
        time_min: datetime,
        time_max: datetime,
        event_types: tuple[str, ...] = ("outOfOffice",),
        principal: str,
    ) -> Mapping[str, Any]: ...


class DeviceAssuranceApi(Protocol):
    def session_device(self, user_id: str, *, principal: str) -> Mapping[str, Any]: ...


class SecurityEventsApi(Protocol):
    def logs(
        self, user_id: str, *, since: datetime, principal: str
    ) -> Sequence[Mapping[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class ContextSettings:
    """Rung 1's settings.

    - `same_day_limit`: requests for one account that tier0 acts on within
      `same_day_window`. The default lets a person ask 3 times in a rolling
      24 hours; the 4th goes to a person. A rolling window has no midnight to
      slip past.
    - `security_lookback`: how far back a security change still counts.
    """

    same_day_limit: int = 3
    same_day_window: timedelta = timedelta(hours=24)
    security_lookback: timedelta = timedelta(hours=24)

    def __post_init__(self) -> None:
        if self.same_day_limit < 1:
            raise ValueError("same_day_limit must be at least 1")
        if self.same_day_window <= timedelta(0) or self.security_lookback <= timedelta(0):
            raise ValueError("windows must be positive")


@dataclass(frozen=True, slots=True)
class ContextResult:
    """What rung 1 found. `findings` is empty when nothing is unusual."""

    findings: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.findings

    @property
    def reason(self) -> str:
        """The first finding, or "ok"."""
        return self.findings[0] if self.findings else OK


class ContextChecks:
    def __init__(
        self,
        *,
        clock: Clock,
        calendar: CalendarApi,
        devices: DeviceAssuranceApi,
        security_events: SecurityEventsApi,
        settings: ContextSettings | None = None,
    ) -> None:
        self._clock = clock
        self._calendar = calendar
        self._devices = devices
        self._security_events = security_events
        self.settings = settings if settings is not None else ContextSettings()
        self._requests: dict[tuple[str, str], deque[datetime]] = {}

    def check(self, identity: BoundIdentity, *, action: str) -> ContextResult:
        """Run every check for one request by `identity` about their own account.

        Every call counts as one request for the same-day check, whatever the
        other checks find: a refused try is still a try."""
        now = self._clock.now()
        findings = [
            finding
            for finding in (
                self._out_of_office(identity, now),
                self._security_change(identity, now),
                self._device(identity),
                self._same_day_repeat(identity, action, now),
            )
            if finding is not None
        ]
        return ContextResult(tuple(findings))

    def _out_of_office(self, identity: BoundIdentity, now: datetime) -> str | None:
        try:
            found = self._calendar.events_list(
                identity.email,
                time_min=now,
                time_max=now + timedelta(minutes=1),
                principal=LADDER_PRINCIPAL,
            )
            items = found.get("items", [])
            if not isinstance(items, list):
                return CALENDAR_UNAVAILABLE
            for item in items:
                start = parse_time(item["start"]["dateTime"])
                end = parse_time(item["end"]["dateTime"])
                if start <= now < end:
                    return OUT_OF_OFFICE
        except Exception:
            # An outage or an answer we can't read: a person decides.
            return CALENDAR_UNAVAILABLE
        return None

    def _security_change(self, identity: BoundIdentity, now: datetime) -> str | None:
        since = now - self.settings.security_lookback
        try:
            events = self._security_events.logs(
                identity.directory_id, since=since, principal=LADDER_PRINCIPAL
            )
            for event in events:
                recent = parse_time(event["published"]) >= since
                if recent and event["eventType"] in SECURITY_CHANGE_EVENTS:
                    return RECENT_SECURITY_CHANGE
        except Exception:
            return SECURITY_EVENTS_UNAVAILABLE
        return None

    def _device(self, identity: BoundIdentity) -> str | None:
        try:
            device = self._devices.session_device(identity.directory_id, principal=LADDER_PRINCIPAL)
            if device.get("managementStatus") != "MANAGED":
                return UNMANAGED_DEVICE
            if device.get("compliant") is not True:
                return NONCOMPLIANT_DEVICE
        except Exception:
            return DEVICE_UNAVAILABLE
        return None

    def _same_day_repeat(self, identity: BoundIdentity, action: str, now: datetime) -> str | None:
        # Only the newest limit + 1 times are needed to know the limit is passed.
        times = self._requests.setdefault(
            (identity.directory_id, action), deque(maxlen=self.settings.same_day_limit + 1)
        )
        floor = now - self.settings.same_day_window
        while times and times[0] <= floor:
            times.popleft()
        times.append(now)
        return SAME_DAY_REPEAT if len(times) > self.settings.same_day_limit else None
