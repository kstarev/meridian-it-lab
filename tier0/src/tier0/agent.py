"""The service-desk agent: Slack event in, ticket and reply out.

Every safety rule is enforced here in code, never left to the model: the
signature check, event dedupe, identity binding, the policy table, the
self-only tool call and the output guard. Every failure path fails closed:
the ticket goes to a person and nothing unsafe is posted.

The kill switch and the rate limit (ADR-008) are read here too, so the agent can
stop before it does anything: no model call, no token, no tool call. The MCP
server enforces both again on every tool call and does not rely on this code.
"""

import contextlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tier0 import messages, replies
from tier0.clock import Clock, parse_time
from tier0.controls import ACTIONS_STOPPED, KIND_REQUEST, RATE_LIMITED, Controls
from tier0.guard import check_reply
from tier0.identity.binding import BoundIdentity, IdentityBinder, Unbound
from tier0.identity.slack_signature import SignatureError, verify_slack_request
from tier0.identity.tokens import TokenIssuer
from tier0.knowledge.answer import GUIDE, reply_text
from tier0.knowledge.wiring import NOT_POSTABLE, GuidePicker, code_link, postable_guide
from tier0.mcp_server.client import McpAuthError, McpToolError, open_session
from tier0.mentions import MentionCheck
from tier0.mocks.slack import SlackHttpRequest
from tier0.models import (
    CATEGORIES,
    OTHER,
    OWN_ACCOUNT_CHECK_SCHEMA,
    OWN_USERNAME,
    TRIAGE_SCHEMA,
    ModelAdapter,
    ModelError,
    ModelRequest,
    ModelResponse,
)
from tier0.policy import (
    POLICY,
    TOOL_GET_MY_USERNAME,
    PolicyRule,
    PolicyViolation,
    check_tool_call,
    decide,
)
from tier0.resets import reset_request
from tier0.roles import role_reference
from tier0.tickets.adapter import (
    AWAITING_REPLY,
    CANCELED,
    DONE,
    IN_PROGRESS,
    QUEUE_IDENTITY_VERIFICATION,
    QUEUE_SERVICE_DESK,
    TODO,
    TRIAGE,
    SlackUserRef,
    TicketAdapter,
    TicketRecord,
)
from tier0.tracing import Session, Tracer
from tier0.verification.ladder import (
    RUNG_NAMES,
    TIER_C_HUMAN_STEPS,
    VerificationLadder,
    required_rung,
)

# Bot echoes (including tier0's own replies) must never open tickets, or the bot would loop.
IGNORED_REASONS = frozenset({"wrong_installation", "not_a_user_message", "bot_user"})
TOKEN_URL = "https://auth.tier0.meridian.example/tokens/{jti}"  # noqa: S105 - a URL, not a secret
AGENT_PRINCIPAL = "tier0-agent"
# Hand-off codes for the safety controls (ADR-008).
KILL_SWITCH_ON = "kill_switch_on"
RATE_LIMITED_CODE = "rate_limited"
# How the MCP server's refusals map to hand-off codes.
_TOOL_REFUSALS = {ACTIONS_STOPPED: KILL_SWITCH_ON, RATE_LIMITED: RATE_LIMITED_CODE}
# Milestone 3d part 2: a password or MFA reset is Tier C, prepared for a person. The ladder
# counts it under this action, apart from own-username requests.
CREDENTIAL_RESET = "credential_reset"
TIER_C_RESET = "tier_c_reset"
# Milestone 4, only with the guide_picker option: the action when an approved guide is posted,
# and the start of every hand-off code the knowledge path gives ("guide_no_guide_fits", ...).
ANSWER_GUIDE = "answer_guide"
GUIDE_HAND_OFF = "guide_"


@dataclass(frozen=True, slots=True)
class Outcome:
    status: Literal["handled", "ignored", "rejected", "duplicate"]
    reason: str | None = None
    ticket: str | None = None
    action: str | None = None
    category: str | None = None
    state: str | None = None
    trace_id: str | None = None


class SlackApi(Protocol):
    def chat_post_message(
        self, *, channel: str, text: str, thread_ts: str | None = None
    ) -> dict[str, Any]: ...

    def users_info(self, user: str, *, include_locale: bool = False) -> dict[str, Any]: ...

    def conversations_open(self, *, users: str) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class _Audience:
    """Who a message goes to: the address the guard allows, and how to greet them."""

    user_id: str
    email: str | None  # the one company address the guard lets through
    language: replies.Language
    first_name: str | None
    tz: str | None = None  # IANA time zone, for the Friday closer only


def _audience(bound: BoundIdentity) -> _Audience:
    return _Audience(
        user_id=bound.slack_user_id,
        email=bound.email,
        language=replies.language_for(bound.locale),
        first_name=bound.first_name,
        tz=bound.tz,
    )


class _SendBlocked(Exception):
    """The output guard blocked a message. Nothing was posted."""

    def __init__(self, count: int) -> None:
        super().__init__("blocked by the output guard")
        self.count = count


class _SendFailed(Exception):
    """Slack did not accept the message."""


@dataclass(frozen=True, slots=True)
class Prompts:
    """The two system prompts a setup uses (milestone 3: each setup has its own frozen
    pair). None means the frozen default in tier0.messages."""

    triage: str | None = None
    check: str | None = None
    # Milestone 3d part 2's split option: the same model's triage prompt from before R25. It
    # is asked only about a message the R25 prompt sorted as "other" (Features.split).
    split_triage: str | None = None


@dataclass(frozen=True, slots=True)
class Features:
    """Milestone 3d part 2's behaviour (docs/plans/milestone-3d-part-2.md). Both are off by
    default, so every earlier setup runs exactly as before on every world.

    - `tier_c`: the code-only reset signal sends a password or MFA reset to a person as a
      prepared Tier C ticket, whatever triage said, unless triage refused it.
    - `split`: a several-question message whose own-username part the pre-R25 triage prompt
      finds gets that part answered, and the rest goes to a person."""

    tier_c: bool = False
    split: bool = False


def triage_request(text: str, system: str | None = None) -> ModelRequest:
    """The one triage call, shared with the triage check tool."""
    return ModelRequest(
        task="triage",
        system=system if system is not None else messages.TRIAGE_SYSTEM,
        user=text,
        json_schema=TRIAGE_SCHEMA,
    )


def parse_triage(response: ModelResponse | None) -> str:
    """Fail closed: anything but exactly {"category": <known value>} is OTHER.
    None means the model call failed."""
    if response is None or response.finish_reason == "refusal":
        return OTHER
    value = _triage_value(response)
    return OTHER if value is None else value


def _triage_value(response: ModelResponse) -> str | None:
    """The category, or None when the reply isn't exactly the schema."""
    try:
        parsed = json.loads(response.text)
    except ValueError:
        return None
    # Exactly the schema: one key. Extra keys such as "tier" mean the model is off-script.
    if not isinstance(parsed, dict) or set(parsed) != {"category"}:
        return None
    value = parsed["category"]
    return value if isinstance(value, str) and value in CATEGORIES else None


def triage_given(response: ModelResponse | None) -> bool:
    """The model really gave a category: the call worked, it wasn't a refusal, and the reply
    is exactly the schema. Only then is an "other" sent to the guide picker; a failed or
    off-format triage also reads as "other", but it goes to a person."""
    return (
        response is not None
        and response.finish_reason != "refusal"
        and _triage_value(response) is not None
    )


def triage_off_format(response: ModelResponse | None) -> bool:
    """A reply came back but isn't exactly the triage schema: a format failure, counted
    apart from a wrong answer (ADR-031). A failed call or a refusal isn't one."""
    return (
        response is not None
        and response.finish_reason != "refusal"
        and _triage_value(response) is None
    )


def check_off_format(response: ModelResponse | None) -> bool:
    """The same for the own-account check."""
    return (
        response is not None
        and response.finish_reason != "refusal"
        and parse_own_account_check(response) is None
    )


def own_account_check_request(
    text: str, *, sender_name: str | None, sender_email: str, system: str | None = None
) -> ModelRequest:
    """The second check. It sees only the sender and the raw message, never triage's output."""
    return ModelRequest(
        task="own_account_check",
        system=system if system is not None else messages.OWN_ACCOUNT_CHECK_SYSTEM,
        user=f"Sender: {sender_name or 'unknown'} <{sender_email}>\nMessage:\n{text}",
        json_schema=OWN_ACCOUNT_CHECK_SCHEMA,
    )


def parse_own_account_check(response: ModelResponse | None) -> bool | None:
    """Exactly {"own_account_only": <bool>}, else None (failed). Refusal is a failure."""
    if response is None or response.finish_reason == "refusal":
        return None
    try:
        parsed = json.loads(response.text)
    except ValueError:
        return None
    if not isinstance(parsed, dict) or set(parsed) != {"own_account_only"}:
        return None
    value = parsed["own_account_only"]
    return value if isinstance(value, bool) else None


class _TracedModel:
    """The guide picker's model, called inside the session's chat span, so its call is traced
    like every other model call (codes and token counts, no content unless asked for)."""

    def __init__(self, session: Session, inner: ModelAdapter) -> None:
        self._session = session
        self._inner = inner
        self.provider = inner.provider
        self.model = inner.model
        self.temperature = inner.temperature
        self.seed = inner.seed
        self.supports_seed = inner.supports_seed

    def complete(self, request: ModelRequest) -> ModelResponse:
        return self._session.chat(self._inner, request)

    def describe(self) -> dict[str, str]:
        return self._inner.describe()


class ServiceDeskAgent:
    def __init__(
        self,
        *,
        slack: SlackApi,
        signing_secret: bytes,
        binder: IdentityBinder,
        tickets: TicketAdapter,
        issuer: TokenIssuer,
        mcp_app: Any,
        model: ModelAdapter,
        tracer: Tracer,
        clock: Clock,
        domain: str,
        recovery_url: str,
        policy: Mapping[str, PolicyRule] = POLICY,
        close_after: timedelta = timedelta(days=3),
        mention_check: MentionCheck | None = None,
        prompts: Prompts | None = None,
        controls: Controls | None = None,
        ladder: VerificationLadder | None = None,
        features: Features | None = None,
        guide_picker: GuidePicker | None = None,
    ) -> None:
        """`guide_picker` turns milestone 4's knowledge path on: a message triage really
        calls "other" may get an approved guide (tier0.knowledge.wiring). None, the default,
        leaves it off, and every "other" goes to a person as before."""
        self._slack = slack
        self._secret = signing_secret
        self._binder = binder
        self._tickets = tickets
        self._issuer = issuer
        self._mcp_app = mcp_app
        self._model = model
        self._tracer = tracer
        self._clock = clock
        self._domain = domain
        self._recovery_url = recovery_url
        self._policy = policy
        self._close_after = close_after
        self._mention_check = mention_check
        self._prompts = prompts if prompts is not None else Prompts()
        self._controls = controls
        self._ladder = ladder
        self._features = features if features is not None else Features()
        self._guide_picker = guide_picker
        self._seen_event_ids: set[str] = set()
        self._disclosed: set[str] = set()
        # Tickets a handler is working on right now; stand_down leaves them to it.
        self._in_flight: set[str] = set()

    # --- intake ---------------------------------------------------------------

    async def handle(self, request: SlackHttpRequest) -> Outcome:
        try:
            verify_slack_request(
                request.headers,
                request.body,
                signing_secret=self._secret,
                now=self._clock.now(),
            )
        except SignatureError as error:
            return Outcome("rejected", error.reason)

        try:
            payload = json.loads(request.body)
        except ValueError:
            return Outcome("rejected", "malformed_body")
        if not isinstance(payload, dict):
            return Outcome("rejected", "malformed_body")

        event_id = payload.get("event_id")
        if isinstance(event_id, str):
            if event_id in self._seen_event_ids:
                return Outcome("duplicate")
            self._seen_event_ids.add(event_id)

        if self._stopped():
            # The switch is on: first hand over whatever is still open with tier0. A
            # failure here must not keep the new ticket from reaching a person.
            with contextlib.suppress(Exception):
                self.stand_down()

        bound = self._binder.bind(payload)
        if isinstance(bound, Unbound) and bound.reason in IGNORED_REASONS:
            return Outcome("ignored", bound.reason)

        event = payload.get("event")
        channel = event.get("channel") if isinstance(event, dict) else None
        raw_text = event.get("text", "") if isinstance(event, dict) else ""
        text = raw_text if isinstance(raw_text, str) else ""
        user_id = bound.slack_user_id
        team_id = bound.slack_team_id or payload.get("team_id")
        if not (
            isinstance(channel, str)
            and isinstance(user_id, str)
            and isinstance(team_id, str)
            and channel
            and user_id
        ):
            return Outcome("rejected", "malformed_event")
        sender = SlackUserRef(team_id=team_id, user_id=user_id)

        if isinstance(bound, Unbound):
            return await self._handle_unbound(bound, channel, text, sender)
        return await self._handle_bound(bound, channel, text, sender)

    async def _handle_unbound(
        self, unbound: Unbound, channel: str, text: str, sender: SlackUserRef
    ) -> Outcome:
        ticket = self._tickets.open_ticket(
            title="Identity verification needed",
            description=text,
            queue=QUEUE_IDENTITY_VERIFICATION,
            requester=sender,
        )
        identifier = ticket.identifier
        try:
            with self._tracer.session(ticket_id=identifier, kind="intake") as s:
                s.event("tier0.identity.unbound", reason=unbound.reason)
                # No name and no account data: an unverified sender is not greeted by name,
                # and the reply never depends on whom the message mentions.
                outsider = _Audience(
                    user_id=sender.user_id,
                    email=None,
                    language=replies.language_for(unbound.locale),
                    first_name=None,
                )
                try:
                    self._send(
                        channel,
                        outsider,
                        "unverified",
                        identifier,
                        recovery_url=self._recovery_url,
                    )
                except (_SendBlocked, _SendFailed):
                    s.event("tier0.reply.not_sent", reason="send_failed")
                self._tickets.add_comment(
                    identifier,
                    f"tier0: identity not verified ({unbound.reason}); routed to identity "
                    "verification. No account data was shared.",
                )
                return Outcome(
                    "handled",
                    unbound.reason,
                    identifier,
                    "verify_identity",
                    None,
                    TODO,
                    s.trace_id,
                )
        except Exception:
            self._best_effort_triage(identifier)
            raise

    async def _handle_bound(
        self, bound: BoundIdentity, channel: str, text: str, sender: SlackUserRef
    ) -> Outcome:
        ticket = self._tickets.open_ticket(
            title="IT request via Slack",
            description=text,
            queue=QUEUE_SERVICE_DESK,
            requester=sender,
        )
        identifier = ticket.identifier
        self._in_flight.add(identifier)
        try:
            with self._tracer.session(
                ticket_id=identifier, kind="intake", requester_id=bound.directory_id
            ) as s:
                # Before anything else: is tier0 allowed to act at all (ADR-008)?
                stopped = self._gate(bound)
                if stopped is not None:
                    action, state = self._escalate(s, identifier, channel, bound, stopped)
                    return Outcome("handled", stopped, identifier, action, None, state, s.trace_id)
                self._tickets.set_state(identifier, IN_PROGRESS)
                category, given = self._triage(s, text)
                rule = decide(category, self._policy)
                s.event(
                    "tier0.policy.decision",
                    category=rule.category,
                    tier=rule.tier,
                    action=rule.action,
                )
                self._tickets.add_comment(
                    identifier,
                    f"tier0: triaged as {category}; policy: Tier {rule.tier}, "
                    f"action {rule.action}.",
                )
                if self._stopped():
                    # The switch flipped while triage ran: nothing more is done.
                    action, state = self._escalate(s, identifier, channel, bound, KILL_SWITCH_ON)
                elif rule.action == "refuse":
                    action, state = self._refuse(s, identifier, channel, bound)
                elif rule.action == "answer":
                    action, state = await self._answer(s, identifier, channel, bound, rule, text)
                elif self._features.tier_c or self._features.split:
                    action, state = await self._other(s, identifier, channel, bound, text)
                elif self._guide_picker is not None and given and category == OTHER:
                    # Milestone 4, only with the option on: the policy says escalate, but an
                    # approved guide may answer first. Anything else still goes to a person.
                    action, state = self._answer_from_guide(
                        s, identifier, channel, bound, self._guide_picker, text
                    )
                else:
                    action, state = self._escalate(s, identifier, channel, bound, "policy_escalate")
                return Outcome("handled", None, identifier, action, category, state, s.trace_id)
        except Exception:
            self._best_effort_triage(identifier)
            raise
        finally:
            self._in_flight.discard(identifier)

    # --- safety controls (ADR-008) --------------------------------------------

    def _stopped(self) -> bool:
        return self._controls is not None and self._controls.kill_switch.is_on

    def _gate(self, bound: BoundIdentity) -> str | None:
        """A hand-off code when tier0 must not act on this request, else None.

        With the kill switch on, nothing is done. Otherwise the request is counted
        against the rate limit, for the requester and for everyone, before any model
        call, so a flood of requests can't run up model calls or directory lookups."""
        if self._controls is None:
            return None
        if self._controls.kill_switch.is_on:
            return KILL_SWITCH_ON
        decision = self._controls.rate_limiter.acquire(
            bound.directory_id, kind=KIND_REQUEST, source=AGENT_PRINCIPAL
        )
        return None if decision.allowed else RATE_LIMITED_CODE

    def stand_down(self) -> tuple[Outcome, ...]:
        """With the kill switch on, hand every ticket that is still open with tier0 to
        a person. Nothing is closed and nothing is sent. Does nothing with the switch
        off, and is safe to call again: a ticket is only moved once."""
        if not self._stopped():
            return ()
        outcomes: list[Outcome] = []
        for state in (IN_PROGRESS, AWAITING_REPLY):
            for ticket in self._tickets.find_tickets(state=state):
                if ticket.identifier in self._in_flight:
                    continue  # its own handler hands it over at its next step
                with self._tracer.session(ticket_id=ticket.identifier, kind="sweep") as s:
                    s.event("tier0.escalated", reason=KILL_SWITCH_ON)
                    self._tickets.add_comment(
                        ticket.identifier,
                        "tier0: the kill switch is on; handed to a person. tier0 does "
                        "nothing more on this ticket.",
                    )
                    self._tickets.set_state(ticket.identifier, TRIAGE)
                    outcomes.append(
                        Outcome(
                            "handled",
                            KILL_SWITCH_ON,
                            ticket.identifier,
                            "escalate",
                            None,
                            TRIAGE,
                            s.trace_id,
                        )
                    )
        return tuple(outcomes)

    # --- steps ----------------------------------------------------------------

    def _triage(self, s: Session, text: str) -> tuple[str, bool]:
        """The category (OTHER when the call fails), and whether the model really gave it."""
        try:
            response = s.chat(self._model, triage_request(text, self._prompts.triage))
        except ModelError:
            return OTHER, False
        if triage_off_format(response):
            s.event("tier0.format_failure", task="triage")
        return parse_triage(response), triage_given(response)

    def _refuse(
        self,
        s: Session,
        identifier: str,
        channel: str,
        bound: BoundIdentity,
        *,
        reason: str = "policy_refuse",
        comment: str = "tier0: refused: the request is about someone else (ASI03).",
    ) -> tuple[str, str]:
        try:
            self._send(
                channel,
                _audience(bound),
                "refuse_colleague",
                identifier,
                recovery_url=self._recovery_url,
            )
        except (_SendBlocked, _SendFailed) as error:
            return self._send_problem(s, identifier, channel, bound, error)
        s.event("tier0.refused", reason=reason)
        self._tickets.add_comment(identifier, comment)
        self._tickets.set_state(identifier, CANCELED)
        return "refuse", CANCELED

    def _escalate(
        self,
        s: Session,
        identifier: str,
        channel: str,
        bound: BoundIdentity,
        code: str,
        *,
        about_someone_else: bool = False,
    ) -> tuple[str, str]:
        """Hand the ticket to a person. When tier0 has decided the request is about
        someone else, the requester sees exactly the refusal text, so refusing and handing
        off look the same to them (ADR-028). IT still sees the hand-off.

        This doesn't hide everything: if triage and the check both read a request that
        names someone as own-account, a real name ends here (refusal text) while a
        made-up one is answered with the requester's own username. That residual risk is
        accepted in ADR-028 and pinned by a test."""
        s.event("tier0.escalated", reason=code)
        try:
            if about_someone_else:
                self._send(
                    channel,
                    _audience(bound),
                    "refuse_colleague",
                    identifier,
                    recovery_url=self._recovery_url,
                )
            else:
                self._send(channel, _audience(bound), "handoff", identifier)
        except (_SendBlocked, _SendFailed):
            s.event("tier0.reply.not_sent", reason="send_failed")
        note = "; the requester saw the standard refusal" if about_someone_else else ""
        self._tickets.add_comment(identifier, f"tier0: handed to a person ({code}){note}.")
        self._tickets.set_state(identifier, TRIAGE)
        return "escalate", TRIAGE

    def _send_problem(
        self,
        s: Session,
        identifier: str,
        channel: str,
        bound: BoundIdentity,
        error: _SendBlocked | _SendFailed,
    ) -> tuple[str, str]:
        if isinstance(error, _SendBlocked):
            return self._guard_blocked(s, identifier, channel, bound, error.count)
        return self._escalate(s, identifier, channel, bound, "send_failed")

    def _guard_blocked(
        self, s: Session, identifier: str, channel: str, bound: BoundIdentity, count: int
    ) -> tuple[str, str]:
        s.event("tier0.guard.blocked", count=count)
        try:
            self._send(channel, _audience(bound), "guard_fallback", identifier)
        except (_SendBlocked, _SendFailed):
            s.event("tier0.reply.not_sent", reason="send_failed")
        self._tickets.add_comment(
            identifier, "tier0: reply blocked by the output guard; handed to a person."
        )
        self._tickets.set_state(identifier, TRIAGE)
        return "escalate", TRIAGE

    # --- milestone 3d part 2 ---------------------------------------------------

    async def _other(
        self, s: Session, identifier: str, channel: str, bound: BoundIdentity, text: str
    ) -> tuple[str, str]:
        """A request triage didn't sort as a username request or a refusal. With part 2 on: a
        reset is prepared for a person as Tier C; with the split option, a message that also
        asks for the sender's own username gets that part answered. Otherwise the hand-off."""
        if self._features.tier_c and reset_request(text):
            return self._prepare_tier_c(s, identifier, channel, bound)
        if self._features.split and self._prompts.split_triage:
            part = self._split_triage(s, text)
            if self._stopped():
                return self._escalate(s, identifier, channel, bound, KILL_SWITCH_ON)
            if part == OWN_USERNAME:
                rule = decide(OWN_USERNAME, self._policy)
                if rule.action == "answer":
                    self._tickets.add_comment(
                        identifier,
                        "tier0: several requests; the own-username part goes down the username "
                        "path, and a person handles the rest.",
                    )
                    return await self._answer(s, identifier, channel, bound, rule, text, split=True)
        return self._escalate(s, identifier, channel, bound, "policy_escalate")

    def _split_triage(self, s: Session, text: str) -> str:
        """The pre-R25 triage prompt: does the message ask for the sender's own username?"""
        try:
            response = s.chat(self._model, triage_request(text, self._prompts.split_triage))
        except ModelError:
            return OTHER
        if triage_off_format(response):
            s.event("tier0.format_failure", task="triage")
        category = parse_triage(response)
        s.event("tier0.split_triage", category=category)
        return category

    def _prepare_tier_c(
        self, s: Session, identifier: str, channel: str, bound: BoundIdentity
    ) -> tuple[str, str]:
        """A password or MFA reset: Tier C, so tier0 only prepares the ticket. The ladder runs
        at Tier C (rung 0 is the binder, rung 1 counts the try and lists what it finds, rung 3
        always stops), and its result and the steps a person must take go on the ticket. No
        further model call, no token, no tool call."""
        found = "no ladder"
        if self._ladder is not None:
            decision = self._ladder.evaluate("C", bound, action=CREDENTIAL_RESET)
            rung = decision.stopped_at if decision.stopped_at is not None else required_rung("C")
            s.event(
                "tier0.ladder",
                tier="C",
                allowed=decision.allowed,
                rung=int(rung),
                reason=decision.reason,
            )
            notes = ", ".join(decision.findings) or decision.reason
            found = f"stopped at rung {int(rung)} ({RUNG_NAMES[rung]}): {notes}"
        s.event("tier0.escalated", reason=TIER_C_RESET)
        try:
            self._send(channel, _audience(bound), "handoff_reset", identifier)
        except (_SendBlocked, _SendFailed):
            s.event("tier0.reply.not_sent", reason="send_failed")
        steps = " ".join(TIER_C_HUMAN_STEPS)
        self._tickets.add_comment(
            identifier,
            "tier0: Tier C request (a password or MFA reset, a new factor or an unlock); "
            f"prepared for a person. tier0 did nothing on the account. Ladder: {found}. "
            f"A person must: {steps}",
        )
        self._tickets.set_state(identifier, TRIAGE)
        return "prepare_tier_c", TRIAGE

    def _second_checks(
        self, s: Session, bound: BoundIdentity, text: str
    ) -> tuple[bool | None, int, bool]:
        """The model check and the two code-only signals (mention check, role signal).

        Returns (model answer, other users mentioned, role reference found)."""
        try:
            response = s.chat(
                self._model,
                own_account_check_request(
                    text,
                    sender_name=bound.full_name,
                    sender_email=bound.email,
                    system=self._prompts.check,
                ),
            )
        except ModelError:
            response = None
        own_only = parse_own_account_check(response)
        if check_off_format(response):
            s.event("tier0.format_failure", task="own_account_check")
        s.event(
            "tier0.own_account_check",
            result="failed" if own_only is None else ("yes" if own_only else "no"),
        )
        if self._stopped():
            # The switch flipped during the model call: no directory lookups either.
            return own_only, 0, False
        others = 0
        if self._mention_check is not None:
            mention = self._mention_check.check(text, bound, domain=self._domain)
            others = mention.others
            s.event(
                "tier0.mention_check",
                others=mention.others,
                namesakes=mention.namesakes,
                lookups=mention.lookups,
            )
        # Code only, from the raw message: a person word near a login word (ADR-024).
        role = role_reference(text)
        s.event("tier0.role_check", matched=role)
        return own_only, others, role

    async def _answer(
        self,
        s: Session,
        identifier: str,
        channel: str,
        bound: BoundIdentity,
        rule: PolicyRule,
        text: str,
        *,
        split: bool = False,
    ) -> tuple[str, str]:
        # Milestone 3d part 2: a reset that triage sent down the username path still goes to
        # a person, before the ladder's Tier A check and before any further model call.
        if self._features.tier_c and reset_request(text):
            return self._prepare_tier_c(s, identifier, channel, bound)
        # The verification ladder first: code only, no model, no directory read. The
        # sender is already bound (rung 0); the context checks (rung 1) decide whether
        # tier0 may act for them right now.
        if self._ladder is not None:
            decision = self._ladder.evaluate(rule.tier, bound, action=rule.category)
            rung = (
                decision.stopped_at if decision.stopped_at is not None else required_rung(rule.tier)
            )
            s.event(
                "tier0.ladder",
                tier=rule.tier,
                allowed=decision.allowed,
                rung=int(rung),
                reason=decision.reason,
            )
            if not decision.allowed:
                found = ", ".join(decision.findings) or decision.reason
                self._tickets.add_comment(
                    identifier,
                    f"tier0: verification ladder stopped at rung {int(rung)} "
                    f"({RUNG_NAMES[rung]}): {found}.",
                )
                return self._escalate(s, identifier, channel, bound, f"ladder_{decision.reason}")

        # Checks before any token: a separate model question, a code-only directory lookup
        # and a code-only role signal (ADR-017, ADR-024).
        own_only, others, role = self._second_checks(s, bound, text)
        if self._stopped():
            return self._escalate(s, identifier, channel, bound, KILL_SWITCH_ON)
        if own_only is None:
            return self._escalate(s, identifier, channel, bound, "own_account_check_failed")
        if not own_only:
            return self._refuse(
                s,
                identifier,
                channel,
                bound,
                reason="own_account_check_no",
                comment=(
                    "tier0: refused: the own-account check said the request is not only "
                    "about the sender's own account."
                ),
            )
        if others > 0:
            return self._escalate(
                s, identifier, channel, bound, "mention_of_other_user", about_someone_else=True
            )
        if role:
            # Like the mention check, the role signal never refuses on its own.
            return self._escalate(
                s, identifier, channel, bound, "role_reference", about_someone_else=True
            )

        try:
            check_tool_call(rule, TOOL_GET_MY_USERNAME)
        except PolicyViolation:
            return self._escalate(s, identifier, channel, bound, "policy_violation")

        issued = self._issuer.mint(bound, ticket_id=identifier, scopes=rule.scopes)
        # The JWT ID only, never the token itself.
        self._tickets.add_link(
            identifier,
            title=f"Access token {issued.jti}",
            url=TOKEN_URL.format(jti=issued.jti),
        )

        result: dict[str, Any] | None = None
        try:
            with s.tool(TOOL_GET_MY_USERNAME, call_id=f"{identifier}-1"):
                async with open_session(self._mcp_app, issued.token) as mcp:
                    result = await mcp.call_tool(TOOL_GET_MY_USERNAME)
        except McpAuthError:
            return self._escalate(s, identifier, channel, bound, "tool_failed")
        except McpToolError as error:
            # The server's kill switch or rate limit said no, or the tool failed.
            code = _TOOL_REFUSALS.get(error.code or 0, "tool_failed")
            return self._escalate(s, identifier, channel, bound, code)
        username = result.get("username") if isinstance(result, dict) else None
        if not isinstance(username, str) or not username:
            return self._escalate(s, identifier, channel, bound, "tool_failed")
        if self._stopped():
            # The switch flipped after the lookup: the answer is not sent.
            return self._escalate(s, identifier, channel, bound, KILL_SWITCH_ON)

        if split:
            return self._answer_and_hand_off(s, identifier, channel, bound, username)
        # The model classifies; code speaks (ADR-028): the answer comes from a template.
        try:
            self._send(channel, _audience(bound), "answer", identifier, username=username)
        except _SendBlocked as error:
            return self._guard_blocked(s, identifier, channel, bound, error.count)
        except _SendFailed:
            return self._escalate(s, identifier, channel, bound, "send_failed")
        self._tickets.add_comment(
            identifier,
            "tier0: answered with the requester's own username from the directory "
            "(self-only lookup).",
        )
        self._tickets.set_state(identifier, AWAITING_REPLY)
        return "answer", AWAITING_REPLY

    def _answer_and_hand_off(
        self, s: Session, identifier: str, channel: str, bound: BoundIdentity, username: str
    ) -> tuple[str, str]:
        """The split option's end: the own username in the reply, the rest with a person."""
        try:
            self._send(
                channel, _audience(bound), "answer_and_handoff", identifier, username=username
            )
        except _SendBlocked as error:
            return self._guard_blocked(s, identifier, channel, bound, error.count)
        except _SendFailed:
            return self._escalate(s, identifier, channel, bound, "send_failed")
        s.event("tier0.escalated", reason="split_rest")
        self._tickets.add_comment(
            identifier,
            "tier0: answered the own-username part with the requester's own username (self-only "
            "lookup); handed the rest to a person.",
        )
        self._tickets.set_state(identifier, TRIAGE)
        return "answer_and_handoff", TRIAGE

    # --- milestone 4: an approved guide (only with the guide_picker option) ------

    def _answer_from_guide(
        self,
        s: Session,
        identifier: str,
        channel: str,
        bound: BoundIdentity,
        picker: GuidePicker,
        text: str,
    ) -> tuple[str, str]:
        """A message triage called "other": post an approved guide word for word, or hand it
        to a person as before (ADR-035, ADR-046).

        The model only picks; `answer_question`'s gates decide in code. Nothing here reads or
        changes an account: no token, no tool call, no directory lookup. So the verification
        ladder, which checks a request about the sender's own account, is not walked; identity
        binding, the kill switch and the rate limit ran before triage, as for every request."""
        outcome = picker.answer(text, model=_TracedModel(s, picker.model))
        pick = outcome.pick
        request_type = pick.request_type if pick is not None else "none"
        picked = pick.guide if pick is not None and pick.guide is not None else "none"
        s.event(
            "tier0.guide.pick",
            version=picker.version,
            request_type=request_type,
            guide=picked,
            screen=outcome.screen or "none",
            outcome=outcome.kind,
            why=outcome.why or "none",
        )
        self._tickets.add_comment(
            identifier,
            f"tier0: guide picker {picker.version}: request type {request_type}, guide "
            f"{picked}; message screen {outcome.screen or 'none'}.",
        )
        if self._stopped():
            # The switch flipped during the picker call: nothing is posted.
            return self._escalate(s, identifier, channel, bound, KILL_SWITCH_ON)
        guide = postable_guide(picker.guides, outcome.guide) if outcome.kind == GUIDE else None
        if guide is None:
            why = outcome.why or NOT_POSTABLE
            return self._escalate(s, identifier, channel, bound, f"{GUIDE_HAND_OFF}{why}")

        # The model picks; code speaks (ADR-028): the header, then the approved steps word for
        # word, then the link (decision 7 of the plan). The guard checks all of it but the link.
        to = _audience(bound)
        link = code_link(guide)
        try:
            self._post(
                channel, to, reply_text(guide, to.language), code_links=(link,) if link else ()
            )
        except _SendBlocked as error:
            return self._guard_blocked(s, identifier, channel, bound, error.count)
        except _SendFailed:
            return self._escalate(s, identifier, channel, bound, "send_failed")
        s.event("tier0.guide.posted", guide=guide.id)
        self._tickets.add_comment(
            identifier,
            f"tier0: answered with the approved guide {guide.id}: its steps word for word and "
            "its link.",
        )
        self._tickets.set_state(identifier, AWAITING_REPLY)
        return ANSWER_GUIDE, AWAITING_REPLY

    # --- sending --------------------------------------------------------------

    def _send(
        self,
        channel: str,
        to: _Audience,
        reply_type: replies.ReplyType,
        key: str,
        **fields: str,
    ) -> None:
        """Post one templated message, guarded. The guard runs on every message, ours
        included. The first message to a requester carries the AI disclosure."""
        self._post(
            channel,
            to,
            replies.body(reply_type, to.language, key, weekday=self._weekday(to.tz), **fields),
        )

    def _post(
        self, channel: str, to: _Audience, body: str, *, code_links: tuple[str, ...] = ()
    ) -> None:
        """Greet, guard and post one message body that code wrote (a template, or an approved
        guide's steps). `code_links` are links code built itself; only they skip the guard."""
        first = to.user_id not in self._disclosed
        text = replies.compose(body, to.language, first_name=to.first_name, first=first)
        verdict = check_reply(
            text, requester_email=to.email, domain=self._domain, code_links=code_links
        )
        if not verdict.allowed:
            raise _SendBlocked(len(verdict.blocked))
        response = self._slack.chat_post_message(channel=channel, text=text)
        if not response.get("ok"):
            raise _SendFailed
        self._disclosed.add(to.user_id)

    def _weekday(self, tz: str | None) -> int | None:
        """The requester's local weekday, or None if their time zone is unknown."""
        if not tz:
            return None
        try:
            return self._clock.now().astimezone(ZoneInfo(tz)).weekday()
        except (ZoneInfoNotFoundError, ValueError):
            return None

    def _best_effort_triage(self, identifier: str) -> None:
        # The caller re-raises the original error, so a failure here is not reported.
        with contextlib.suppress(Exception):
            self._tickets.set_state(identifier, TRIAGE)

    # --- sweep ----------------------------------------------------------------

    def sweep(self) -> tuple[Outcome, ...]:
        """Close tickets that have waited for a reply for `close_after`. With the kill
        switch on, close nothing and hand tier0's open tickets to people instead."""
        if self._stopped():
            return self.stand_down()
        outcomes: list[Outcome] = []
        for ticket in self._tickets.find_tickets(state=AWAITING_REPLY):
            if self._should_close(ticket):
                with self._tracer.session(ticket_id=ticket.identifier, kind="sweep") as s:
                    self._tickets.add_comment(
                        ticket.identifier, "tier0: closed after 3 days with no reply."
                    )
                    self._tickets.set_state(ticket.identifier, DONE)
                    s.event("tier0.ticket.closed", reason="no_reply_3_days")
                    try:
                        told = self._tell_closed(ticket)
                    except Exception:
                        # A real Slack client raises on API and network errors; one
                        # failed note must not stop the other tickets from closing.
                        told = False
                    if not told:
                        s.event("tier0.reply.not_sent", reason="close_notice_failed")
                    outcomes.append(
                        Outcome(
                            "handled",
                            ticket=ticket.identifier,
                            action="close",
                            state=DONE,
                            trace_id=s.trace_id,
                        )
                    )
        return tuple(outcomes)

    def _tell_closed(self, ticket: TicketRecord) -> bool:
        """Tell the requester the ticket closed, in their language. The ticket closes
        whether or not this works."""
        if ticket.requester is None:
            return False
        user_id = ticket.requester.user_id
        target = self._binder.reply_target(user_id)
        if target is None:
            return False
        opened = self._slack.conversations_open(users=user_id)
        channel = opened.get("channel") if opened.get("ok") else None
        channel_id = channel.get("id") if isinstance(channel, dict) else None
        if not isinstance(channel_id, str):
            return False
        to = _Audience(
            user_id=user_id,
            email=target.email,
            language=replies.language_for(target.locale),
            # The Directory's givenName, the same name intake greets with.
            first_name=target.first_name,
            tz=target.tz,
        )
        try:
            self._send(channel_id, to, "auto_close", ticket.identifier)
        except (_SendBlocked, _SendFailed):
            return False
        return True

    def _should_close(self, ticket: TicketRecord) -> bool:
        changed = parse_time(ticket.state_changed_at)
        if self._clock.now() - changed < self._close_after:
            return False
        return not any(
            c.author != "tier0" and parse_time(c.created_at) >= changed for c in ticket.comments
        )
