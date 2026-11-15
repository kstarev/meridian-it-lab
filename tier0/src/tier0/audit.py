"""The audit log for tier0's safety controls (ADR-008).

Every kill-switch toggle, every refused tool call, every rate-limit hit and
every override is written here: what happened, who did it, when and why.

The log is append-only. It holds short codes and opaque IDs (a directory ID,
never a name, an email or message text). The one free-text field is the reason
a person gives for a toggle or an override. Times come from the injected clock.

It is kept in memory and, when a path is given, also appended to a JSON-lines
file. A real deployment would send the same entries to a write-once store.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from tier0.clock import Clock, format_time

MAX_REASON_LENGTH = 200
_MAX_DETAIL_LENGTH = 64

type Detail = str | int | bool


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """One line of the audit log."""

    at: str  # clock.format_time
    event: str  # a short code, for example "kill_switch.on"
    actor: str  # a person for toggles and overrides, a service name for automatic entries
    reason: str | None  # why, in the actor's words; None for automatic entries
    details: Mapping[str, Detail]

    def as_json(self) -> dict[str, object]:
        return {
            "at": self.at,
            "event": self.event,
            "actor": self.actor,
            "reason": self.reason,
            "details": dict(self.details),
        }


class AuditLog:
    """Append-only. Nothing here can edit or remove an entry."""

    def __init__(self, clock: Clock, *, path: Path | None = None) -> None:
        self._clock = clock
        self._path = path
        self._entries: list[AuditEntry] = []
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def entries(self) -> tuple[AuditEntry, ...]:
        return tuple(self._entries)

    def events(self, code: str) -> tuple[AuditEntry, ...]:
        """Entries with this event code, oldest first. A code that ends with a dot
        ("rate_limit.") matches every event of that family."""
        if code.endswith("."):
            return tuple(e for e in self._entries if e.event.startswith(code))
        return tuple(e for e in self._entries if e.event == code)

    def record(
        self, event: str, *, actor: str, reason: str | None = None, **details: Detail
    ) -> AuditEntry:
        if not event or not actor.strip():
            raise ValueError("an audit entry needs an event code and an actor")
        if reason is not None:
            reason = reason.strip()[:MAX_REASON_LENGTH]
        for name, value in details.items():
            # The same rule as trace events: codes and IDs, never content.
            if isinstance(value, str) and (len(value) > _MAX_DETAIL_LENGTH or "@" in value):
                raise ValueError(f"audit detail {name!r} looks like content; pass a short code")
        entry = AuditEntry(
            at=format_time(self._clock.now()),
            event=event,
            actor=actor.strip(),
            reason=reason,
            details=MappingProxyType(dict(details)),
        )
        self._entries.append(entry)
        if self._path is not None:
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry.as_json(), sort_keys=True) + "\n")
        return entry
