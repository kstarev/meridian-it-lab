"""The global kill switch and the rate limit on agent actions (ADR-008).

Both live here, in plain code and plain state. Nothing in this module is ever
shown to the model or built from a prompt, so no message can turn them off.

The MCP server enforces both on every tool call (`tier0.mcp_server.server`).
The agent reads the same objects so it can stop early and hand the ticket to a
person, but the server does not rely on the agent doing so.

What counts as an agent action: a model call, a minted token, a tool call, a
directory lookup by the mention check, and closing a ticket. Checking a Slack
signature, binding the sender to the directory, opening the ticket, posting the
fixed hand-off reply and moving the ticket to the human queue are not agent
actions: they are how a ticket reaches a person.

The kill switch can be kept in a small state file (`KillSwitchFile`), so a
restart cannot turn the agent back on (ADR-043, R6). Writes are atomic. On
start-up a missing file means a fresh deployment and the switch starts off. A
file that says "on" keeps the switch on. A file that cannot be read or makes no
sense fails closed: the switch starts on and the audit log says why. Without a
state file the switch lives in memory only, as before.

Times come from the injected clock, never the wall clock.
"""

import contextlib
import json
import os
import tempfile
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from tier0.audit import AuditLog
from tier0.clock import Clock, format_time, parse_time

# JSON-RPC error codes the MCP server answers with. They sit in the range the
# spec leaves to the server (-32000 to -32099) and clash with none the SDK uses.
ACTIONS_STOPPED = -32081
RATE_LIMITED = -32082
# Short fixed refusals with no user data.
ACTIONS_STOPPED_MESSAGE = "agent actions are stopped"
RATE_LIMITED_MESSAGE = "rate limit reached"

GLOBAL = "global"
REQUESTER = "requester"

type ActionKind = Literal["request", "tool_call"]
KIND_REQUEST: ActionKind = "request"
KIND_TOOL_CALL: ActionKind = "tool_call"


def _named(value: str, what: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{what} must not be empty")
    return cleaned


class KillSwitchNotSaved(OSError):
    """The switch's state could not be written to its file.

    A restart would not remember the change. Turning the switch on still takes
    effect in memory; turning it off does not happen at all."""


@dataclass(frozen=True, slots=True)
class _SavedSwitch:
    on: bool
    since: datetime | None


class KillSwitchFile:
    """Where the kill switch keeps its state between runs: one small JSON file.

    {"on": bool, "since": time or null, "by": str, "reason": str, "at": time}
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def read(self) -> _SavedSwitch | None:
        """The saved state, or None when there is no file. Raises ValueError or
        OSError when the file is there but cannot be used."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("state is not an object")
        on, since = data.get("on"), data.get("since")
        if not isinstance(on, bool):
            raise ValueError("'on' is not true or false")
        if not all(isinstance(data.get(k), str) for k in ("by", "reason", "at")):
            raise ValueError("'by', 'reason' or 'at' is not text")
        parse_time(data["at"])
        if since is not None and not isinstance(since, str):
            raise ValueError("'since' is not a time")
        moment = parse_time(since) if since is not None else None
        if on and moment is None:
            raise ValueError("the switch is on but has no start time")
        return _SavedSwitch(on, moment if on else None)

    def write(
        self, *, on: bool, since: datetime | None, by: str, reason: str, now: datetime
    ) -> None:
        """Replace the file atomically: write a temp file beside it, sync, rename."""
        payload = {
            "on": on,
            "since": format_time(since) if since is not None else None,
            "by": by,
            "reason": reason,
            "at": format_time(now),
        }
        directory = self.path.parent
        directory.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(dir=directory, prefix=f".{self.path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, sort_keys=True))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temp_name)
            raise


class KillSwitch:
    """One switch for every agent action. Off until a person turns it on.

    Every toggle is written to the audit log with who, when and why, including
    a toggle that changes nothing (turning on a switch that is already on).

    With a state file the switch survives a restart. Turning it on takes effect
    first and is saved second. Turning it off is saved first, and only then does
    it happen. If a save fails the switch stays on and `KillSwitchNotSaved` is
    raised. A missing file starts the switch off, with no audit entry. A file
    that says on starts it on. A file that cannot be read starts it on too.
    """

    def __init__(
        self, clock: Clock, audit: AuditLog, *, state: KillSwitchFile | None = None
    ) -> None:
        self._clock = clock
        self._audit = audit
        self._state = state
        self._on = False
        self._since: datetime | None = None
        if state is not None:
            self._restore(state)

    def _restore(self, state: KillSwitchFile) -> None:
        try:
            saved = state.read()
        except (OSError, ValueError):  # bad JSON and bad UTF-8 are ValueErrors
            self._on, self._since = True, self._clock.now()
            self._audit.record(
                "kill_switch.restored",
                actor="system",
                reason="the saved switch state could not be read, so the switch starts on",
                on=True,
                since=format_time(self._since),
                readable=False,
            )
            return
        if saved is None or not saved.on:
            return
        self._on, self._since = True, saved.since
        self._audit.record(
            "kill_switch.restored",
            actor="system",
            reason="the saved switch state says on, so the switch stays on",
            on=True,
            since=format_time(saved.since) if saved.since is not None else "",
            readable=True,
        )

    @property
    def is_on(self) -> bool:
        return self._on

    @property
    def since(self) -> datetime | None:
        """When the switch was last turned on, while it is on."""
        return self._since

    def turn_on(self, *, by: str, reason: str) -> None:
        self._toggle(True, by, reason)

    def turn_off(self, *, by: str, reason: str) -> None:
        self._toggle(False, by, reason)

    def _toggle(self, on: bool, by: str, reason: str) -> None:
        actor, why = _named(by, "by"), _named(reason, "reason")
        changed = self._on != on
        new_since = (self._clock.now() if changed else self._since) if on else None
        failure: OSError | None = None
        if on:
            # On first, so actions stop at once; then save.
            self._on, self._since = True, new_since
            failure = self._save(on, actor, why)
        else:
            # Save first; the switch only goes off if the file says so.
            failure = self._save(on, actor, why)
            if failure is None:
                self._on, self._since = False, None
        if failure is not None and not on:
            self._not_saved(actor, why, on, failure)
        self._audit.record(
            "kill_switch.on" if on else "kill_switch.off",
            actor=actor,
            reason=why,
            changed=changed,
        )
        if failure is not None and on:
            self._not_saved(actor, why, on, failure)

    def _save(self, on: bool, actor: str, why: str) -> OSError | None:
        if self._state is None:
            return None
        try:
            self._state.write(
                on=on,
                since=self._since if on else None,
                by=actor,
                reason=why,
                now=self._clock.now(),
            )
        except OSError as error:
            return error
        return None

    def _not_saved(self, actor: str, why: str, on: bool, error: OSError) -> None:
        self._audit.record(
            "kill_switch.not_saved",
            actor=actor,
            reason=why,
            wanted_on=on,
            now_on=self._on,
            error=type(error).__name__,
        )
        raise KillSwitchNotSaved(
            "the switch state could not be saved; a restart would not remember it"
        ) from error


@dataclass(frozen=True, slots=True)
class RateLimitSettings:
    """The default limits. Each one counts over a rolling window.

    The same numbers apply to each kind of action on its own: requests tier0
    starts work on (counted when the agent picks one up) and tool calls
    (counted by the MCP server).

    - 5 per requester per hour: asking for a username is a once-or-twice thing,
      so 5 leaves room for honest retries and stops a loop or a probe quickly.
    - 30 for everyone per hour: Meridian has about 300 tickets a month, so a
      normal hour has a handful. 30 covers a busy Monday. A bigger wave, such
      as a new-hire cohort, needs a person to raise the limit for a while.
    """

    per_requester_limit: int = 5
    global_limit: int = 30
    window: timedelta = timedelta(hours=1)
    # The longest a raised limit may last before it falls back by itself.
    max_override: timedelta = timedelta(hours=24)

    def __post_init__(self) -> None:
        if self.per_requester_limit < 1 or self.global_limit < 1:
            raise ValueError("limits must be at least 1")
        if self.window <= timedelta(0) or self.max_override <= timedelta(0):
            raise ValueError("the window and the longest override must be positive")


@dataclass(frozen=True, slots=True)
class RateDecision:
    allowed: bool
    # Which limit said no: "requester" or "global". None when allowed.
    scope: str | None = None


@dataclass(frozen=True, slots=True)
class _Override:
    limit: int
    expires_at: datetime


class RateLimiter:
    """Caps agent actions for everyone together and for each requester.

    A refused action is not counted, so a flood does not lock a requester out
    for longer than the window. Every refusal ("hit") and every override goes
    to the audit log.
    """

    def __init__(
        self, clock: Clock, audit: AuditLog, settings: RateLimitSettings | None = None
    ) -> None:
        self._clock = clock
        self._audit = audit
        self.settings = settings if settings is not None else RateLimitSettings()
        self._everyone: dict[str, deque[datetime]] = {}
        self._own: dict[tuple[str, str], deque[datetime]] = {}
        self._overrides: dict[str, _Override] = {}

    # --- counting --------------------------------------------------------------

    def acquire(self, requester_id: str, *, kind: ActionKind, source: str) -> RateDecision:
        """Count one action for this requester, or refuse it and log the hit.

        `source` is the service that asked (the MCP server or the agent)."""
        requester = _named(requester_id, "requester_id")
        now = self._clock.now()
        self._drop_expired_overrides(now)
        own = self._recent(self._own.setdefault((kind, requester), deque()), now)
        everyone = self._recent(self._everyone.setdefault(kind, deque()), now)
        scope: str | None = None
        if len(own) >= self.limit_for(requester):
            scope = REQUESTER
        elif len(everyone) >= self.limit_for(GLOBAL):
            scope = GLOBAL
        if scope is None:
            own.append(now)
            everyone.append(now)
            return RateDecision(True)
        if not own:
            del self._own[(kind, requester)]
        self._audit.record(
            "rate_limit.hit",
            actor=_named(source, "source"),
            scope=scope,
            kind=kind,
            requester=_code(requester),
            limit=self.limit_for(requester if scope == REQUESTER else GLOBAL),
            window_seconds=int(self.settings.window.total_seconds()),
        )
        return RateDecision(False, scope)

    def count(self, kind: ActionKind, requester_id: str | None = None) -> int:
        """Actions counted in the current window: one requester's, or everyone's."""
        now = self._clock.now()
        if requester_id is None:
            return len(self._recent(self._everyone.get(kind, deque()), now))
        return len(self._recent(self._own.get((kind, requester_id), deque()), now))

    def _recent(self, times: deque[datetime], now: datetime) -> deque[datetime]:
        floor = now - self.settings.window
        while times and times[0] <= floor:
            times.popleft()
        return times

    # --- limits and overrides --------------------------------------------------

    def limit_for(self, scope: str) -> int:
        """The limit in force now for "global" or for one requester's ID."""
        override = self._overrides.get(scope)
        if override is not None and self._clock.now() < override.expires_at:
            return override.limit
        return self._default(scope)

    def _default(self, scope: str) -> int:
        return self.settings.global_limit if scope == GLOBAL else self.settings.per_requester_limit

    def override(self, *, scope: str, limit: int, by: str, reason: str, lasts: timedelta) -> None:
        """A person changes one limit for a while: the global one (scope "global") or
        one requester's (scope is their directory ID). It always ends by itself."""
        actor, why = _named(by, "by"), _named(reason, "reason")
        target = _named(scope, "scope")
        if limit < 1:
            raise ValueError("a limit must be at least 1; use the kill switch to stop everything")
        if lasts <= timedelta(0) or lasts > self.settings.max_override:
            raise ValueError("an override must last more than 0 and at most the longest allowed")
        expires_at = self._clock.now() + lasts
        self._overrides[target] = _Override(limit, expires_at)
        self._audit.record(
            "rate_limit.override",
            actor=actor,
            reason=why,
            limit=limit,
            default=self._default(target),
            expires_at=format_time(expires_at),
            **_scope_details(target),
        )

    def clear_override(self, *, scope: str, by: str, reason: str) -> None:
        """A person ends an override early. Logged even when there was none."""
        actor, why = _named(by, "by"), _named(reason, "reason")
        target = _named(scope, "scope")
        had = self._overrides.pop(target, None) is not None
        self._audit.record(
            "rate_limit.override_cleared",
            actor=actor,
            reason=why,
            changed=had,
            **_scope_details(target),
        )

    def _drop_expired_overrides(self, now: datetime) -> None:
        for target, override in list(self._overrides.items()):
            if now >= override.expires_at:
                del self._overrides[target]
                self._audit.record(
                    "rate_limit.override_expired",
                    actor="tier0-controls",
                    limit=self._default(target),
                    **_scope_details(target),
                )


def _code(value: str) -> str:
    """An ID that is safe to log: short and not an address. Anything else is hidden."""
    return value if len(value) <= 64 and "@" not in value else "hidden"


def _scope_details(target: str) -> dict[str, str]:
    if target == GLOBAL:
        return {"scope": GLOBAL}
    return {"scope": REQUESTER, "requester": _code(target)}


@dataclass(frozen=True, slots=True)
class Controls:
    """The safety controls of one tier0 runtime, and the audit log they write to."""

    audit: AuditLog
    kill_switch: KillSwitch
    rate_limiter: RateLimiter


def build_controls(
    clock: Clock,
    *,
    rate_limits: RateLimitSettings | None = None,
    audit: AuditLog | None = None,
    kill_switch_file: Path | None = None,
) -> Controls:
    log = audit if audit is not None else AuditLog(clock)
    state = KillSwitchFile(kill_switch_file) if kill_switch_file is not None else None
    return Controls(
        audit=log,
        kill_switch=KillSwitch(clock, log, state=state),
        rate_limiter=RateLimiter(clock, log, rate_limits),
    )
