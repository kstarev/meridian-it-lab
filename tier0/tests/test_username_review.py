"""The username path's security review (2026-11-20): fixes, pinned behaviour, open questions.

Mocks and fake models only, on the tuning seed. The fixed bugs each failed on main before
their fix. The strict xfail tests at the end are design questions for the owner: each
fails today, and passes only once the owner says yes and the change is made.
"""

import dataclasses
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import anyio
import pytest

from meridian_dataset import World
from meridian_dataset.model import Person, Ticket
from tier0 import replies
from tier0.agent import NOT_A_DIRECT_MESSAGE, Outcome
from tier0.clock import parse_time
from tier0.identity.binding import (
    GATEWAY_PRINCIPAL,
    BoundIdentity,
    IdentityBinder,
    Unbound,
)
from tier0.mcp_server.client import McpAuthError, McpToolError, open_session
from tier0.mentions import slack_mentions
from tier0.mocks.directory import MockDirectory
from tier0.mocks.slack import APP_ID, CHANNELS, MockSlack
from tier0.models import ModelRequest, ModelResponse, StubModel
from tier0.policy import TOOL_GET_MY_USERNAME
from tier0.runtime import Runtime, offline_runtime
from tier0.tickets.adapter import AWAITING_REPLY, TRIAGE
from tier0.tracing import summarize

OWN = '{"category": "own_username"}'
YES = '{"own_account_only": true}'


class Model:
    """The stub underneath. With `wrong=True`, triage says own_username and the check says
    yes whatever the message says: both model answers are wrong on a colleague request."""

    provider = "test"
    model = "scripted"
    temperature: float | None = None
    seed: int | None = None
    supports_seed = False

    def __init__(self, *, wrong: bool = False) -> None:
        self.requests: list[ModelRequest] = []
        self._stub = StubModel()
        self._wrong = wrong

    def describe(self) -> dict[str, str]:
        return {}

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        real = self._stub.complete(request)
        if self._wrong and request.task == "triage":
            return dataclasses.replace(real, text=OWN)
        if self._wrong and request.task == "own_account_check":
            return dataclasses.replace(real, text=YES)
        return real


def _ticket(world: World, ticket_id: str) -> Ticket:
    return next(t for t in world.tickets if t.id == ticket_id)


def _requester(world: World, ticket: Ticket) -> Person:
    person = world.person_by_slack_id(ticket.sender_slack_user_id)
    assert person is not None
    return person


def _colleague(world: World, requester: Person) -> Person:
    """An active colleague who doesn't share the requester's name."""
    return next(
        p
        for p in world.people
        if p.directory_id != requester.directory_id
        and p.slack_active
        and p.status == "active"
        and p.full_name != requester.full_name
    )


def _run[T](world: World, body: Callable[[Runtime], Awaitable[T]], **options: Any) -> T:
    async def main() -> T:
        async with offline_runtime(world, **options) as rt:
            return await body(rt)

    return anyio.run(main)


def _payload(rt: Runtime, ticket: Ticket) -> dict[str, Any]:
    """The signed DM event Slack would send for this ticket, as a dict to change."""
    rt.clock.advance_to(parse_time(ticket.received_at))
    return json.loads(rt.slack.message_event(ticket).body)


def _events(rt: Runtime, name: str) -> list[dict[str, Any]]:
    return [
        dict(e.attributes or {})
        for span in rt.tracer.finished_spans()
        for e in span.events
        if e.name == name
    ]


def _tool_calls(rt: Runtime) -> int:
    return sum(s.tool_calls for s in summarize(rt.tracer.finished_spans()).values())


def _is(reply_type: replies.ReplyType, text: str) -> bool:
    return any(v.split("{", 1)[0] in text for v in replies.TEMPLATES[reply_type]["en"])


# --- fixed: a username is posted only in a direct message ------------------------------


@pytest.mark.parametrize(
    ("channel_type", "channel", "shared"),
    [
        ("channel", CHANNELS["it-help"], False),  # a public channel tier0 is in
        ("channel", "C0SHARED001", True),  # a Slack Connect channel with another company
        ("group", "G0PRIVATE01", False),  # a private channel
        ("mpim", "G0GROUPDM01", False),  # a group DM with tier0 and other people
        (None, "D0NOLABEL01", False),  # no channel_type at all: fail closed
    ],
    ids=["public", "shared_with_another_company", "private", "group_dm", "no_label"],
)
def test_a_username_is_never_posted_outside_a_direct_message(
    world: World, channel_type: str | None, channel: str, shared: bool
) -> None:
    """On main the answer went to whatever channel the message came from, so everyone in
    #it-help, or another company in a shared channel, read the requester's username."""
    ticket = _ticket(world, "TKT-001")
    me = _requester(world, ticket)
    model = Model()

    async def body(rt: Runtime) -> tuple[Runtime, Outcome]:
        payload = _payload(rt, ticket)
        payload["is_ext_shared_channel"] = shared
        payload["event"]["channel"] = channel
        if channel_type is None:
            del payload["event"]["channel_type"]
        else:
            payload["event"]["channel_type"] = channel_type
        return rt, await rt.agent.handle(rt.slack.signed_request(payload))

    rt, outcome = _run(world, body, model=model)
    assert (outcome.action, outcome.category, outcome.state) == ("escalate", "own_username", TRIAGE)
    assert _events(rt, "tier0.escalated") == [{"reason": NOT_A_DIRECT_MESSAGE}]
    (sent,) = rt.slack.sent_messages
    assert sent.channel == channel and _is("handoff", sent.text)
    assert me.primary_email not in sent.text
    # Nothing past triage: no check, no lookup, no token, no tool call.
    assert [r.task for r in model.requests] == ["triage"]
    assert {r.principal for r in rt.directory.reads} == {GATEWAY_PRINCIPAL}
    assert rt.issuer.issued == ()
    assert _tool_calls(rt) == 0
    comments = rt.tickets.get_ticket(outcome.ticket or "").comments
    assert any(NOT_A_DIRECT_MESSAGE in c.body for c in comments)


def test_the_same_request_in_its_direct_message_is_answered(world: World) -> None:
    ticket = _ticket(world, "TKT-001")
    me = _requester(world, ticket)

    async def body(rt: Runtime) -> tuple[Runtime, Outcome]:
        return rt, await rt.agent.handle(rt.slack.signed_request(_payload(rt, ticket)))

    rt, outcome = _run(world, body)
    assert (outcome.action, outcome.state) == ("answer", AWAITING_REPLY)
    (sent,) = rt.slack.sent_messages
    assert sent.channel == rt.slack.dm_channel_for(me.slack_user_id)
    assert me.primary_email in sent.text


# --- fixed: a Slack @-mention of someone else ---------------------------------------


def test_slack_mentions_counts_other_people_only() -> None:
    assert slack_mentions("what's my login?", "U0ME") == 0
    assert slack_mentions("<@U0ME> what's my login?", "U0ME") == 0
    assert slack_mentions("login for <@U0KENJI>?", "U0ME") == 1
    assert slack_mentions("login for <@W0KENJI|kenji>?", "U0ME") == 1
    assert slack_mentions("<@U0A> and <@U0A> and <@U0B>", "U0ME") == 2
    assert slack_mentions("logins for <!subteam^S0NEW|@new-hires>", "U0ME") == 1
    assert slack_mentions("logins for <!subteam^S0NEW>", "U0ME") == 1


@pytest.mark.parametrize("form", ["plain", "with_label", "user_group"])
def test_a_slack_mention_of_someone_else_goes_to_a_person(world: World, form: str) -> None:
    """In Slack's event text a typed "@Kenji Sato" is `<@U…>`, not the name, so on main
    the mention check saw nothing. With triage and the check both wrong, tier0 answered."""
    ticket = _ticket(world, "TKT-001")
    me = _requester(world, ticket)
    other = _colleague(world, me)
    mention = {
        "plain": f"<@{other.slack_user_id}>",
        "with_label": f"<@{other.slack_user_id}|{other.full_name}>",
        "user_group": "<!subteam^S0NEWHIRES|@new-hires>",
    }[form]

    async def body(rt: Runtime) -> tuple[Runtime, Outcome]:
        payload = _payload(rt, ticket)
        payload["event"]["text"] = f"hey, what's the username for {mention}?"
        return rt, await rt.agent.handle(rt.slack.signed_request(payload))

    rt, outcome = _run(world, body, model=Model(wrong=True))
    assert (outcome.action, outcome.state) == ("escalate", TRIAGE)
    assert _events(rt, "tier0.escalated") == [{"reason": "mention_of_other_user"}]
    (mention_event,) = _events(rt, "tier0.mention_check")
    assert mention_event["others"] >= 1
    # The requester sees the refusal text, as for any request about someone else (ADR-028).
    (sent,) = rt.slack.sent_messages
    assert _is("refuse_colleague", sent.text)
    assert rt.issuer.issued == ()
    assert _tool_calls(rt) == 0


def test_a_slack_mention_of_the_sender_alone_is_answered(world: World) -> None:
    ticket = _ticket(world, "TKT-001")
    me = _requester(world, ticket)

    async def body(rt: Runtime) -> tuple[Runtime, Outcome]:
        payload = _payload(rt, ticket)
        payload["event"]["text"] = f"<@{me.slack_user_id}> here, what's my username?"
        return rt, await rt.agent.handle(rt.slack.signed_request(payload))

    rt, outcome = _run(world, body)
    assert outcome.action == "answer"
    (mention_event,) = _events(rt, "tier0.mention_check")
    assert mention_event["others"] == 0


# --- fixed: an event without an event id ------------------------------------------


@pytest.mark.parametrize("event_id", [None, "", 7])
def test_an_event_without_an_event_id_is_refused(world: World, event_id: object) -> None:
    """Dedupe is the replay check inside the signature's 5-minute window. On main a signed
    body without an event id skipped it, and the same request was answered twice."""
    ticket = _ticket(world, "TKT-001")

    async def body(rt: Runtime) -> tuple[Runtime, Outcome, Outcome]:
        payload = _payload(rt, ticket)
        if event_id is None:
            del payload["event_id"]
        else:
            payload["event_id"] = event_id
        request = rt.slack.signed_request(payload)
        return rt, await rt.agent.handle(request), await rt.agent.handle(request)

    rt, first, second = _run(world, body)
    assert first == second == Outcome("rejected", "malformed_body")
    assert rt.tickets.find_tickets() == ()
    assert rt.slack.sent_messages == ()
    assert rt.issuer.issued == ()


# --- fixed: users.info must describe the sender -----------------------------------


class _OtherRecord:
    """A users.info that answers with another person's record for one user id, as a stale
    cache or a proxy that mixes up users might."""

    def __init__(self, real: Callable[..., dict[str, Any]], asked: str, answered: str) -> None:
        self._real = real
        self._asked = asked
        self._answered = answered

    def users_info(self, user: str, *, include_locale: bool = False) -> dict[str, Any]:
        if user == self._asked:
            user = self._answered
        return self._real(user, include_locale=include_locale)


def test_a_users_info_record_for_someone_else_never_binds(
    world: World, slack: MockSlack, directory: MockDirectory
) -> None:
    ticket = _ticket(world, "TKT-001")
    me = _requester(world, ticket)
    other = _colleague(world, me)
    binder = IdentityBinder(
        slack=_OtherRecord(slack.users_info, me.slack_user_id, other.slack_user_id),
        directory=directory,
        team_id=world.slack_team.team_id,
        app_id=APP_ID,
        domain=world.domain,
    )
    result = binder.bind(json.loads(slack.message_event(ticket).body))
    assert isinstance(result, Unbound)
    assert result.reason == "unknown_slack_user"
    assert directory.reads == ()


def test_a_users_info_record_for_someone_else_never_gets_their_username(world: World) -> None:
    """End to end: on main the sender was bound to the other person and got their username."""
    ticket = _ticket(world, "TKT-001")
    me = _requester(world, ticket)
    other = _colleague(world, me)

    async def body(rt: Runtime) -> tuple[Runtime, Outcome]:
        swap = _OtherRecord(rt.slack.users_info, me.slack_user_id, other.slack_user_id)
        rt.slack.users_info = swap.users_info  # type: ignore[method-assign]
        return rt, await rt.agent.handle(rt.slack.signed_request(_payload(rt, ticket)))

    rt, outcome = _run(world, body)
    assert (outcome.action, outcome.reason) == ("verify_identity", "unknown_slack_user")
    assert all(other.primary_email not in m.text for m in rt.slack.sent_messages)
    assert rt.issuer.issued == ()


# --- checked and fine: pinned so it stays that way ---------------------------------


def test_people_asking_at_the_same_time_each_get_only_their_own_username(world: World) -> None:
    tickets = [_ticket(world, t) for t in ("TKT-001", "TKT-002", "TKT-003")]
    people = [_requester(world, t) for t in tickets]
    assert len({p.directory_id for p in people}) == 3

    async def body(rt: Runtime) -> tuple[Runtime, list[Outcome]]:
        rt.clock.advance_to(max(parse_time(t.received_at) for t in tickets))
        requests = [rt.slack.message_event(t) for t in tickets]
        outcomes: list[Outcome | None] = [None] * len(requests)

        async def one(i: int) -> None:
            outcomes[i] = await rt.agent.handle(requests[i])

        async with anyio.create_task_group() as tg:
            for i in range(len(requests)):
                tg.start_soon(one, i)
        return rt, [o for o in outcomes if o is not None]

    rt, outcomes = _run(world, body)
    assert [o.action for o in outcomes] == ["answer"] * 3
    for person in people:
        dm = rt.slack.dm_channel_for(person.slack_user_id)
        (text,) = [m.text for m in rt.slack.sent_messages if m.channel == dm]
        assert person.primary_email in text
        assert not any(p.primary_email in text for p in people if p is not person)


def test_concurrent_tool_calls_with_different_tokens_never_cross(world: World) -> None:
    people = [p for p in world.people if p.slack_active and p.status == "active"][:8]

    async def body(rt: Runtime) -> list[tuple[str, object]]:
        tokens = [
            (
                p,
                rt.issuer.mint(
                    BoundIdentity(p.directory_id, p.primary_email, p.slack_user_id, "T"),
                    ticket_id=f"IT-{n}",
                ),
            )
            for n, p in enumerate(people)
        ]
        wrong: list[tuple[str, object]] = []

        async def calls(person: Person, token: str) -> None:
            for _ in range(3):
                async with open_session(rt.mcp_app, token) as mcp:
                    got = await mcp.call_tool(TOOL_GET_MY_USERNAME)
                if got != {"username": person.primary_email}:
                    wrong.append((person.primary_email, got))

        async with anyio.create_task_group() as tg:
            for person, issued in tokens:
                tg.start_soon(calls, person, issued.token)
        return wrong

    assert _run(world, body) == []


def test_debug_logs_hold_no_username(world: World, caplog: pytest.LogCaptureFixture) -> None:
    """Even with every logger at DEBUG (the MCP SDK's and the HTTP client's included), the
    answered request leaves no username and no message text in the logs."""
    ticket = _ticket(world, "TKT-001")
    me = _requester(world, ticket)
    caplog.set_level(logging.DEBUG)
    names = ("mcp", "httpx2", "starlette", "tier0")
    before = {name: logging.getLogger(name).level for name in names}
    for name in names:
        logging.getLogger(name).setLevel(logging.DEBUG)
    try:

        async def body(rt: Runtime) -> Outcome:
            return await rt.agent.handle(rt.slack.signed_request(_payload(rt, ticket)))

        outcome = _run(world, body)
    finally:
        for name, level in before.items():
            logging.getLogger(name).setLevel(level)
    assert outcome.action == "answer"
    assert caplog.records  # the SDK did log at DEBUG
    for record in caplog.records:
        assert me.primary_email not in record.getMessage()
        assert ticket.text not in record.getMessage()


def test_the_rate_limit_still_bounds_a_flood_of_new_event_ids(world: World) -> None:
    """Twelve copies of one request with fresh event ids, all at once: at most 5 tokens."""
    ticket = _ticket(world, "TKT-001")

    async def body(rt: Runtime) -> Runtime:
        rt.clock.advance_to(parse_time(ticket.received_at))
        requests = []
        for n in range(12):
            payload = json.loads(rt.slack.message_event(ticket).body)
            payload["event_id"] = f"EvFLOOD{n:04d}"
            requests.append(rt.slack.signed_request(payload))
        async with anyio.create_task_group() as tg:
            for request in requests:
                tg.start_soon(rt.agent.handle, request)
        return rt

    rt = _run(world, body)
    assert len(rt.issuer.issued) <= rt.controls.rate_limiter.settings.per_requester_limit


# --- design questions for the owner (strict xfail: each passes only after a "yes") ---


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="Question 1: should the MCP server accept each token for one tool call only?",
)
def test_a_token_works_for_one_tool_call_only(world: World) -> None:
    """Today a token works for every call in its 5 minutes. It never leaves the process,
    and it can only ever return its own subject's username."""
    ticket = _ticket(world, "TKT-001")

    async def body(rt: Runtime) -> bool:
        await rt.agent.handle(rt.slack.message_event(ticket))
        (issued,) = rt.issuer.issued
        try:
            async with open_session(rt.mcp_app, issued.token) as mcp:
                await mcp.call_tool(TOOL_GET_MY_USERNAME)
        except (McpAuthError, McpToolError):
            return False
        return True

    reused = _run(world, body)
    assert not reused, "the same token returned the username a second time"


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "Question 2: should the mention check also find a colleague's name in lower case, "
        "in capitals, or by first name only?"
    ),
)
@pytest.mark.parametrize("form", ["lower_case", "capitals", "first_name_only"])
def test_a_colleague_named_in_another_form_goes_to_a_person(world: World, form: str) -> None:
    """Today only "First Last" with capital initials is looked up. With triage and the
    check both wrong, tier0 answers, with the requester's own username only (ADR-023)."""
    ticket = _ticket(world, "TKT-001")
    me = _requester(world, ticket)
    other = _colleague(world, me)
    name = {
        "lower_case": other.full_name.lower(),
        "capitals": other.full_name.upper(),
        "first_name_only": other.given_name,
    }[form]

    async def body(rt: Runtime) -> Outcome:
        payload = _payload(rt, ticket)
        payload["event"]["text"] = f"what's the login for {name}?"
        return await rt.agent.handle(rt.slack.signed_request(payload))

    outcome = _run(world, body, model=Model(wrong=True))
    assert outcome.action != "answer"


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "Question 3: should a message an app posted for the sender (app_id or bot_profile "
        "on the event) be ignored like a bot's?"
    ),
)
@pytest.mark.parametrize(
    "marks",
    [{"app_id": "A0OTHERAPP1"}, {"bot_profile": {"id": "B0OTHERBOT1", "app_id": "A0OTHERAPP1"}}],
    ids=["app_id", "bot_profile"],
)
def test_a_message_an_app_posted_for_the_sender_is_not_served(
    world: World, marks: dict[str, object]
) -> None:
    """Today it is served like the person's own message: the answer still goes only to the
    sender's DM, and the app needs the sender's own token to post there."""
    ticket = _ticket(world, "TKT-001")

    async def body(rt: Runtime) -> Outcome:
        payload = _payload(rt, ticket)
        payload["event"].update(marks)
        return await rt.agent.handle(rt.slack.signed_request(payload))

    outcome = _run(world, body)
    assert outcome.status == "ignored"
