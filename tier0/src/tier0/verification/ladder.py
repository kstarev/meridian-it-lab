"""The verification ladder: how much proof each tier needs before tier0 acts.

- **Rung 0, every tier:** a signed Slack user ID bound to the directory. Guests,
  leavers and suspended accounts are refused (`tier0.identity.binding`).
- **Rung 1, every tier:** context checks in code: not out of office, no recent
  security change, a managed device, no same-day repeats (`context`).
- **Rung 2, Tier B:** a fresh SSO step-up per RFC 9470 (`step_up`), or an Okta
  Verify push with a number challenge (`push`).
- **Rung 3, Tier C:** a human only: an ID check plus a call-back to the number on
  file or manager approval, then a notice to the user and their manager.

The policy is code, like the tier and tool policy (`tier0.policy`): the model
never picks a rung, and nothing tier0 learns can change it.

Two rules are built into the shape of the code:

- **Tier C is never allowed here.** tier0 only prepares the ticket; a person does
  the rest. `evaluate` has no path that allows Tier C, and `LadderDecision`
  refuses to be built as "allowed" for it. An unknown tier is treated as Tier C.
- **What a caller says is never proof.** A date of birth, a manager's name, an
  employee ID, a username, answers to security questions or a phone number the
  caller supplies change nothing. Proof comes only from systems: Slack, the
  directory, the calendar, device assurance, the security feed, the identity
  provider.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import IntEnum
from types import MappingProxyType

from tier0.identity.binding import BoundIdentity, Unbound
from tier0.verification.context import ContextChecks
from tier0.verification.push import PushChallenge, PushVerifier
from tier0.verification.step_up import StepUpChallenge, StepUpVerifier


class Rung(IntEnum):
    SLACK_IDENTITY = 0
    CONTEXT = 1
    STEP_UP = 2
    HUMAN = 3


RUNG_NAMES: Mapping[Rung, str] = MappingProxyType(
    {
        Rung.SLACK_IDENTITY: "signed Slack user ID bound to the directory",
        Rung.CONTEXT: "context checks in code",
        Rung.STEP_UP: "fresh SSO step-up or a push with a number challenge",
        Rung.HUMAN: "a human only",
    }
)

# The highest rung each tier must clear. Rung 3 can't be cleared by tier0.
REQUIRED_RUNG: Mapping[str, Rung] = MappingProxyType(
    {"A": Rung.CONTEXT, "B": Rung.STEP_UP, "C": Rung.HUMAN}
)
# The only tiers tier0 may ever be allowed to act on by itself.
AUTOMATIC_TIERS = frozenset({"A", "B"})

# Never accepted as proof, whoever offers them and however right they are.
NEVER_PROOF = frozenset(
    {
        "date_of_birth",
        "manager_name",
        "employee_id",
        "username",
        "security_answers",
        "phone_number",
    }
)

# What the person who picks up a Tier C ticket must do (brief, item 6).
TIER_C_HUMAN_STEPS = (
    "Check the requester's ID.",
    "Call back on the number on file, or get their manager's approval.",
    "After the change, tell the user and their manager.",
)

HUMAN_ONLY = "human_only"
STEP_UP_REQUIRED = "step_up_required"
STEP_UP_UNAVAILABLE = "step_up_unavailable"


def required_rung(tier: str) -> Rung:
    """The highest rung a tier must clear. An unknown tier needs a human."""
    return REQUIRED_RUNG.get(tier, Rung.HUMAN)


@dataclass(frozen=True, slots=True)
class OidcStepUp:
    """Rung 2 evidence: the ID token that answered a step-up challenge."""

    id_token: str
    challenge: StepUpChallenge


@dataclass(frozen=True, slots=True)
class PushApproval:
    """Rung 2 evidence: a push that was sent to the requester's own account."""

    challenge: PushChallenge


type StepUpEvidence = OidcStepUp | PushApproval


@dataclass(frozen=True, slots=True)
class LadderDecision:
    """The ladder's answer for one request."""

    tier: str
    allowed: bool
    # The first rung that said no. None when allowed.
    stopped_at: Rung | None
    reason: str  # "ok" or a short code
    # Everything rung 1 found, for the person who picks the ticket up.
    findings: tuple[str, ...] = ()
    # Names (never values) of what the caller offered as proof. All of it was ignored.
    claims_ignored: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.allowed and (self.tier not in AUTOMATIC_TIERS or self.stopped_at is not None):
            raise ValueError("the ladder can't allow this: only Tier A and B, with no rung failed")
        if not self.allowed and self.stopped_at is None:
            raise ValueError("a refusal must say which rung stopped it")

    @property
    def verdict(self) -> tuple[bool, Rung | None, str]:
        """What was decided, without the notes."""
        return (self.allowed, self.stopped_at, self.reason)


class VerificationLadder:
    def __init__(
        self,
        *,
        context: ContextChecks,
        step_up: StepUpVerifier | None = None,
        push: PushVerifier | None = None,
    ) -> None:
        self._context = context
        self._step_up = step_up
        self._push = push

    @property
    def context(self) -> ContextChecks:
        """Rung 1's checks, with their settings."""
        return self._context

    def evaluate(
        self,
        tier: str,
        identity: BoundIdentity | Unbound,
        *,
        action: str,
        step_up: StepUpEvidence | None = None,
        claimed: Mapping[str, object] | None = None,
    ) -> LadderDecision:
        """Walk the rungs for one request and say whether tier0 may act.

        `identity` is rung 0's result: what the identity binder made of the signed
        Slack event. `action` names what is asked, for the same-day repeat count.
        `claimed` is anything the caller offered as proof about themselves; it is
        only listed in the decision, never used.
        """
        ignored = tuple(sorted({n if n in NEVER_PROOF else "other" for n in (claimed or {})}))

        def stop(rung: Rung, reason: str, findings: tuple[str, ...] = ()) -> LadderDecision:
            return LadderDecision(tier, False, rung, reason, findings, ignored)

        if not isinstance(identity, BoundIdentity):
            reason = identity.reason if isinstance(identity, Unbound) else "not_verified"
            return stop(Rung.SLACK_IDENTITY, reason)

        context = self._context.check(identity, action=action)
        if not context.ok:
            return stop(Rung.CONTEXT, context.reason, context.findings)

        required = required_rung(tier)
        if required >= Rung.HUMAN:
            return stop(Rung.HUMAN, HUMAN_ONLY)
        if required >= Rung.STEP_UP:
            failure = self._step_up_failure(identity, step_up)
            if failure is not None:
                return stop(Rung.STEP_UP, failure)
        return LadderDecision(tier, True, None, "ok", (), ignored)

    def _step_up_failure(
        self, identity: BoundIdentity, evidence: StepUpEvidence | None
    ) -> str | None:
        """None when the evidence proves a fresh, strong sign-in by the requester."""
        if evidence is None:
            return STEP_UP_REQUIRED
        if isinstance(evidence, OidcStepUp):
            if self._step_up is None:
                return STEP_UP_UNAVAILABLE
            checked = self._step_up.verify(evidence.id_token, identity, evidence.challenge)
            return None if checked.ok else checked.reason
        if isinstance(evidence, PushApproval):
            if self._push is None:
                return STEP_UP_UNAVAILABLE
            answered = self._push.finish(identity, evidence.challenge)
            return None if answered.ok else answered.reason
        return STEP_UP_REQUIRED
