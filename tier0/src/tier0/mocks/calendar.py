"""Mock of the one Google Calendar call the verification ladder needs.

`events_list` is shaped like Calendar API `events.list` with
`eventTypes=outOfOffice`: it returns only out-of-office blocks, with their start
and end and nothing else (no titles, no guests). The mock is separate from the
generated world: nobody is out of office until a test or a needle says so, so
existing worlds behave exactly as before.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from tier0.clock import Clock, format_time
from tier0.verification.context import ContextSourceError

OUT_OF_OFFICE = "outOfOffice"


@dataclass(frozen=True, slots=True)
class CalendarRead:
    """One logged call."""

    principal: str
    calendar_id: str
    at: str


class MockCalendar:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._blocks: dict[str, list[tuple[datetime, datetime]]] = {}
        self._reads: list[CalendarRead] = []
        # Set to True to act like an outage: every call raises.
        self.unavailable = False

    @property
    def reads(self) -> tuple[CalendarRead, ...]:
        return tuple(self._reads)

    def set_out_of_office(self, calendar_id: str, *, start: datetime, end: datetime) -> None:
        """Put an out-of-office block into one person's calendar (their primary email)."""
        if start.utcoffset() is None or end.utcoffset() is None:
            raise ValueError("start and end must be timezone-aware")
        if end <= start:
            raise ValueError("the block must end after it starts")
        self._blocks.setdefault(calendar_id.lower(), []).append((start, end))

    def events_list(
        self,
        calendar_id: str,
        *,
        time_min: datetime,
        time_max: datetime,
        event_types: tuple[str, ...] = (OUT_OF_OFFICE,),
        principal: str,
    ) -> dict[str, Any]:
        """Out-of-office blocks that overlap [time_min, time_max)."""
        self._reads.append(
            CalendarRead(
                principal=principal, calendar_id=calendar_id, at=format_time(self._clock.now())
            )
        )
        if self.unavailable:
            raise ContextSourceError("calendar unavailable")
        if event_types != (OUT_OF_OFFICE,):
            raise ContextSourceError("only outOfOffice events can be read")
        items = [
            {
                "kind": "calendar#event",
                "eventType": OUT_OF_OFFICE,
                "start": {"dateTime": format_time(start)},
                "end": {"dateTime": format_time(end)},
            }
            for start, end in self._blocks.get(calendar_id.lower(), [])
            if start < time_max and end > time_min
        ]
        return {"kind": "calendar#events", "items": items}
