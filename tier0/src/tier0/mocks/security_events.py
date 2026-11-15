"""Mock security-events feed: what changed on an account lately?

Shaped like a filtered read of the Okta System Log: events about one user since
a given time, each with an event type and when it was published. The mock is
separate from the generated world and starts empty, so existing worlds behave
exactly as before.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from tier0.clock import Clock, format_time
from tier0.verification.context import ContextSourceError


@dataclass(frozen=True, slots=True)
class SecurityEventsRead:
    """One logged call."""

    principal: str
    user_id: str
    at: str


class MockSecurityEvents:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._events: dict[str, list[tuple[datetime, str]]] = {}
        self._reads: list[SecurityEventsRead] = []
        # Set to True to act like an outage: every call raises.
        self.unavailable = False

    @property
    def reads(self) -> tuple[SecurityEventsRead, ...]:
        return tuple(self._reads)

    def add_event(self, user_id: str, event_type: str, *, at: datetime | None = None) -> None:
        """Record one event about a user, now or at a given time."""
        when = at if at is not None else self._clock.now()
        if when.utcoffset() is None:
            raise ValueError("at must be timezone-aware")
        self._events.setdefault(user_id, []).append((when, event_type))

    def logs(self, user_id: str, *, since: datetime, principal: str) -> list[dict[str, Any]]:
        """Events about this user published at or after `since`, oldest first."""
        self._reads.append(
            SecurityEventsRead(
                principal=principal, user_id=user_id, at=format_time(self._clock.now())
            )
        )
        if self.unavailable:
            raise ContextSourceError("security events unavailable")
        return [
            {"eventType": event_type, "published": format_time(when)}
            for when, event_type in sorted(self._events.get(user_id, []))
            if when >= since
        ]
