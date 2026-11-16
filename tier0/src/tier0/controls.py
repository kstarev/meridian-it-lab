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

Times come from the injected clock, never the wall clock.
"""

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from tier0.audit import AuditLog
from tier0.clock import Clock, format_time

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


class KillSwitch:
    """One switch for every agent action. Off until a person turns it on.

    Every toggle is written to the audit log with who, when and why, including
    a toggle that changes nothing (turning on a switch that is already on).
    """

    def __init__(self, clock: Clock, audit: AuditLog) -> None:
        self._clock = clock
        self._audit = audit
        self._on = False
        self._since: datetime | None = None

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
        self._on = on
        if changed:
            self._since = self._clock.now() if on else None
        self._audit.record(
            "kill_switch.on" if on else "kill_switch.off",
            actor=actor,
            reason=why,
            changed=changed,
        )


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
) -> Controls:
    log = audit if audit is not None else AuditLog(clock)
    return Controls(
        audit=log,
        kill_switch=KillSwitch(clock, log),
        rate_limiter=RateLimiter(clock, log, rate_limits),
    )
