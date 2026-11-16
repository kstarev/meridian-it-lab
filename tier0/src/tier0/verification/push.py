"""Rung 2, the alternative: an Okta Verify push with a number challenge.

tier0 sends a push to the requester's phone and shows a number in the requester's
own Slack DM. The phone shows a few numbers; only the person who can see the DM
knows which one to tap. The rules, all in code:

- **Only the requester's own bound account.** `start` takes the verified
  identity and sends the push there. A request that names anyone else as the
  target is refused and nothing is sent. So a push can't be aimed at a colleague.
- **Rate-limited per account.** At most a few pushes in a short window, so tier0
  can't be used to flood someone's phone until they tap "yes" (push bombing).
- **One use, short life.** A challenge counts once, only for the account it was
  sent to, and only until it times out.
- **Fail closed.** No answer, a wrong number, "not me", a timeout or an outage
  are all refusals.

Every refusal to send goes to the audit log. Times come from the injected clock.
"""

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from tier0.audit import AuditLog
from tier0.clock import Clock
from tier0.identity.binding import BoundIdentity

PUSH_PRINCIPAL = "tier0-push"

SUCCESS = "SUCCESS"
WAITING = "WAITING"
REJECTED = "REJECTED"
TIMEOUT = "TIMEOUT"


class PushError(Exception):
    """The push service could not send or could not answer."""


class PushApi(Protocol):
    """The two Okta Verify calls tier0 needs."""

    def send_push(self, user_id: str, *, principal: str) -> Mapping[str, Any]: ...

    def poll(self, transaction_id: str, *, principal: str) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class PushSettings:
    """At most `max_pushes` to one account per `window`, and `timeout` to answer."""

    max_pushes: int = 3
    window: timedelta = timedelta(minutes=15)
    timeout: timedelta = timedelta(minutes=2)

    def __post_init__(self) -> None:
        if self.max_pushes < 1:
            raise ValueError("max_pushes must be at least 1")
        if self.window <= timedelta(0) or self.timeout <= timedelta(0):
            raise ValueError("window and timeout must be positive")


@dataclass(frozen=True, slots=True)
class PushChallenge:
    """A push that was sent. `number` is shown to the requester in their own DM."""

    subject: str  # the directory ID the push went to
    transaction_id: str
    number: int
    sent_at: datetime


@dataclass(frozen=True, slots=True)
class PushRefused:
    """No push was sent. `reason` is a short code."""

    reason: str


@dataclass(frozen=True, slots=True)
class PushResult:
    ok: bool
    reason: str  # "ok" or a short code


class PushVerifier:
    def __init__(
        self,
        okta: PushApi,
        clock: Clock,
        *,
        audit: AuditLog | None = None,
        settings: PushSettings | None = None,
    ) -> None:
        self._okta = okta
        self._clock = clock
        self._audit = audit
        self.settings = settings if settings is not None else PushSettings()
        self._sent: dict[str, deque[datetime]] = {}
        self._open: dict[str, PushChallenge] = {}

    def start(
        self, identity: BoundIdentity, *, requested_target: str | None = None
    ) -> PushChallenge | PushRefused:
        """Send one push to the requester's own account.

        `requested_target` is whoever the request asked the push to go to, if it
        said so. It is untrusted and never used as the destination: when it names
        anyone but the requester, nothing is sent."""
        if requested_target is not None and requested_target.strip().lower() not in (
            identity.directory_id.lower(),
            identity.email.lower(),
        ):
            return self._refuse(identity, "target_not_requester")

        now = self._clock.now()
        sent = self._sent.setdefault(identity.directory_id, deque())
        floor = now - self.settings.window
        while sent and sent[0] <= floor:
            sent.popleft()
        if len(sent) >= self.settings.max_pushes:
            return self._refuse(identity, "push_rate_limited")

        try:
            answer = self._okta.send_push(identity.directory_id, principal=PUSH_PRINCIPAL)
            transaction_id, number = answer["transactionId"], answer["correctAnswer"]
        except Exception:
            return self._refuse(identity, "push_unavailable")
        if not isinstance(transaction_id, str) or not transaction_id:
            return self._refuse(identity, "push_unavailable")
        if not isinstance(number, int) or isinstance(number, bool):
            return self._refuse(identity, "push_unavailable")
        sent.append(now)
        challenge = PushChallenge(
            subject=identity.directory_id,
            transaction_id=transaction_id,
            number=number,
            sent_at=now,
        )
        self._open[transaction_id] = challenge
        return challenge

    def finish(self, identity: BoundIdentity, challenge: PushChallenge) -> PushResult:
        """Did the requester approve this push with the right number? Fails closed.

        A push that is still waiting can be asked about again until it times out.
        Any other outcome spends the challenge."""
        known = self._open.get(challenge.transaction_id)
        if known is None or known != challenge:
            return PushResult(False, "unknown_challenge")
        if challenge.subject != identity.directory_id:
            del self._open[challenge.transaction_id]
            return PushResult(False, "wrong_subject")
        if self._clock.now() - challenge.sent_at > self.settings.timeout:
            del self._open[challenge.transaction_id]
            return PushResult(False, "timed_out")
        try:
            state = self._okta.poll(challenge.transaction_id, principal=PUSH_PRINCIPAL)
            result = state["factorResult"]
        except Exception:
            del self._open[challenge.transaction_id]
            return PushResult(False, "push_unavailable")
        if result == WAITING:
            return PushResult(False, "not_answered")
        del self._open[challenge.transaction_id]
        if result == SUCCESS:
            return PushResult(True, "ok")
        return PushResult(False, "timed_out" if result == TIMEOUT else "rejected")

    def _refuse(self, identity: BoundIdentity, reason: str) -> PushRefused:
        if self._audit is not None:
            # The requester's opaque ID and a code. Never the target that was asked for.
            self._audit.record(
                "push.refused",
                actor=PUSH_PRINCIPAL,
                reason_code=reason,
                requester=identity.directory_id,
            )
        return PushRefused(reason)
