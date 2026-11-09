"""The service-desk agent: Slack event in, ticket and reply out.

Every safety rule is enforced here in code, never left to the model: the
signature check, event dedupe, identity binding, the policy table, the
self-only tool call and the output guard. Every failure path fails closed:
the ticket goes to a person and nothing unsafe is posted.
"""

import contextlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal, Protocol

from tier0 import messages, replies
from tier0.clock import Clock, parse_time
from tier0.guard import check_reply
from tier0.identity.binding import BoundIdentity, IdentityBinder, Unbound
from tier0.identity.slack_signature import SignatureError, verify_slack_request
from tier0.identity.tokens import TokenIssuer
from tier0.mcp_server.client import McpAuthError, McpToolError, open_session
from tier0.mentions import MentionCheck
from tier0.mocks.slack import SlackHttpRequest
from tier0.models import (
    CATEGORIES,
    OTHER,
    OWN_ACCOUNT_CHECK_SCHEMA,
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

# Bot echoes (including tier0's own replies) must never open tickets, or the bot would loop.
IGNORED_REASONS = frozenset({"wrong_installation", "not_a_user_message", "bot_user"})
TOKEN_URL = "https://auth.tier0.meridian.example/tokens/{jti}"  # noqa: S105 - a URL, not a secret


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


def _audience(bound: BoundIdentity) -> _Audience:
    return _Audience(
        user_id=bound.slack_user_id,
        email=bound.email,
        language=replies.language_for(bound.locale),
        first_name=bound.first_name,
    )


class _SendBlocked(Exception):
    """The output guard blocked a message. Nothing was posted."""

    def __init__(self, count: int) -> None:
        super().__init__("blocked by the output guard")
        self.count = count


class _SendFailed(Exception):
    """Slack did not accept the message."""


def triage_request(text: str) -> ModelRequest:
    """The one triage call, shared with the triage check tool."""
    return ModelRequest(
        task="triage", system=messages.TRIAGE_SYSTEM, user=text, json_schema=TRIAGE_SCHEMA
    )


def parse_triage(response: ModelResponse | None) -> str:
    """Fail closed: anything but exactly {"category": <known value>} is OTHER.
    None means the model call failed."""
    if response is None or response.finish_reason == "refusal":
        return OTHER
    try:
        parsed = json.loads(response.text)
    except ValueError:
        return OTHER
    # Exactly the schema: one key. Extra keys such as "tier" mean the model is off-script.
    if not isinstance(parsed, dict) or set(parsed) != {"category"}:
        return OTHER
    value = parsed["category"]
    return value if isinstance(value, str) and value in CATEGORIES else OTHER


def own_account_check_request(
    text: str, *, sender_name: str | None, sender_email: str
) -> ModelRequest:
    """The second check. It sees only the sender and the raw message, never triage's output."""
    return ModelRequest(
        task="own_account_check",
        system=messages.OWN_ACCOUNT_CHECK_SYSTEM,
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
    ) -> None:
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
        self._seen_event_ids: set[str] = set()
        self._disclosed: set[str] = set()

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
        try:
            with self._tracer.session(
                ticket_id=identifier, kind="intake", requester_id=bound.directory_id
            ) as s:
                self._tickets.set_state(identifier, IN_PROGRESS)
                category = self._triage(s, text)
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
                if rule.action == "refuse":
                    action, state = self._refuse(s, identifier, channel, bound)
                elif rule.action == "answer":
                    action, state = await self._answer(s, identifier, channel, bound, rule, text)
                else:
                    action, state = self._escalate(s, identifier, channel, bound, "policy_escalate")
                return Outcome("handled", None, identifier, action, category, state, s.trace_id)
        except Exception:
            self._best_effort_triage(identifier)
            raise

    # --- steps ----------------------------------------------------------------

    def _triage(self, s: Session, text: str) -> str:
        try:
            response = s.chat(self._model, triage_request(text))
        except ModelError:
            return OTHER
        return parse_triage(response)

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
        """Hand the ticket to a person. When the request is about someone else, the
        requester sees the same reply as a refusal, so whether a name they mentioned
        exists in the directory doesn't show in what they read (ADR-028). IT still sees
        the hand-off."""
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

    def _second_checks(
        self, s: Session, bound: BoundIdentity, text: str
    ) -> tuple[bool | None, int, bool]:
        """The model check and the two code-only signals (mention check, role signal).

        Returns (model answer, other users mentioned, role reference found)."""
        try:
            response = s.chat(
                self._model,
                own_account_check_request(
                    text, sender_name=bound.full_name, sender_email=bound.email
                ),
            )
        except ModelError:
            response = None
        own_only = parse_own_account_check(response)
        s.event(
            "tier0.own_account_check",
            result="failed" if own_only is None else ("yes" if own_only else "no"),
        )
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
    ) -> tuple[str, str]:
        # Checks before any token: a separate model question, a code-only directory lookup
        # and a code-only role signal (ADR-017, ADR-024).
        own_only, others, role = self._second_checks(s, bound, text)
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
        except (McpAuthError, McpToolError):
            return self._escalate(s, identifier, channel, bound, "tool_failed")
        username = result.get("username") if isinstance(result, dict) else None
        if not isinstance(username, str) or not username:
            return self._escalate(s, identifier, channel, bound, "tool_failed")

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
        first = to.user_id not in self._disclosed
        text = replies.compose(
            replies.body(reply_type, to.language, key, **fields),
            to.language,
            first_name=to.first_name,
            first=first,
        )
        verdict = check_reply(text, requester_email=to.email, domain=self._domain)
        if not verdict.allowed:
            raise _SendBlocked(len(verdict.blocked))
        response = self._slack.chat_post_message(channel=channel, text=text)
        if not response.get("ok"):
            raise _SendFailed
        self._disclosed.add(to.user_id)

    def _best_effort_triage(self, identifier: str) -> None:
        # The caller re-raises the original error, so a failure here is not reported.
        with contextlib.suppress(Exception):
            self._tickets.set_state(identifier, TRIAGE)

    # --- sweep ----------------------------------------------------------------

    def sweep(self) -> tuple[Outcome, ...]:
        """Close tickets that have waited for a reply for `close_after`."""
        outcomes: list[Outcome] = []
        for ticket in self._tickets.find_tickets(state=AWAITING_REPLY):
            if self._should_close(ticket):
                with self._tracer.session(ticket_id=ticket.identifier, kind="sweep") as s:
                    self._tickets.add_comment(
                        ticket.identifier, "tier0: closed after 3 days with no reply."
                    )
                    self._tickets.set_state(ticket.identifier, DONE)
                    s.event("tier0.ticket.closed", reason="no_reply_3_days")
                    if not self._tell_closed(ticket):
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
        info = self._slack.users_info(user_id, include_locale=True)
        user = info.get("user") if info.get("ok") else None
        if not isinstance(user, dict) or user.get("deleted"):
            return False
        profile = user.get("profile")
        email = profile.get("email") if isinstance(profile, dict) else None
        first_name = profile.get("first_name") if isinstance(profile, dict) else None
        locale = user.get("locale")
        opened = self._slack.conversations_open(users=user_id)
        channel = opened.get("channel") if opened.get("ok") else None
        channel_id = channel.get("id") if isinstance(channel, dict) else None
        if not isinstance(channel_id, str):
            return False
        to = _Audience(
            user_id=user_id,
            email=email if isinstance(email, str) else None,
            language=replies.language_for(locale if isinstance(locale, str) else None),
            # Slack's profile name goes only to this same person, and compose() drops
            # anything that isn't a plain first name.
            first_name=first_name if isinstance(first_name, str) else None,
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
