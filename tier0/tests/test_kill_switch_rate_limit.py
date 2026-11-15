"""The global kill switch and the rate limit, end to end against the offline mocks (ADR-008).

ADR-008's two targets are checked here by counting what tier0 did:
0 agent actions after the kill switch is on, and 0 actions over the rate limit.

An agent action is a model call, a minted token, a tool call that ran, a directory lookup
by the mention check, or a ticket tier0 closed. The messages are written in this file, so
no pool wording is used.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import anyio
import pytest
from mcp.shared.exceptions import MCPError

from meridian_dataset import World
from meridian_dataset.model import Person, Ticket
from tier0 import replies
from tier0.agent import KILL_SWITCH_ON, RATE_LIMITED_CODE, Outcome
from tier0.clock import parse_time
from tier0.controls import (
    ACTIONS_STOPPED,
    ACTIONS_STOPPED_MESSAGE,
    GLOBAL,
    KIND_REQUEST,
    KIND_TOOL_CALL,
    RATE_LIMITED,
    RATE_LIMITED_MESSAGE,
    REQUESTER,
    RateLimitSettings,
)
from tier0.evals.ladder import identity_of
from tier0.mcp_server.client import McpToolError, open_session
from tier0.mcp_server.server import MCP_PRINCIPAL, enforce_controls
from tier0.mentions import MENTION_PRINCIPAL
from tier0.models import ModelRequest, ModelResponse, StubModel
from tier0.runtime import Runtime, offline_runtime
from tier0.tickets.adapter import (
    AWAITING_REPLY,
    DONE,
    QUEUE_IDENTITY_VERIFICATION,
    QUEUE_SERVICE_DESK,
    TODO,
    TRIAGE,
)
from tier0.tracing import GEN_AI_OPERATION_NAME, OPERATION_CHAT
from tier0.verification.context import ContextSettings

OWN_REQUEST = "what's my username? i can't find it."
IT_LEAD = "U0ITLEAD"
# Keeps rung 1's same-day check out of the way, so a test sees the rate limit alone.
NO_REPEAT_CHECK = ContextSettings(same_day_limit=1000)


@dataclass(frozen=True, slots=True)
class Actions:
    model_calls: int
    tokens: int
    tool_runs: int
    lookups: int
    closed: int

    def __sub__(self, other: "Actions") -> "Actions":
        return Actions(
            self.model_calls - other.model_calls,
            self.tokens - other.tokens,
            self.tool_runs - other.tool_runs,
            self.lookups - other.lookups,
            self.closed - other.closed,
        )


NONE = Actions(0, 0, 0, 0, 0)


def _actions(rt: Runtime) -> Actions:
    """Every agent action so far, read from the mocks and the traces."""
    spans = rt.tracer.finished_spans()
    reads = rt.directory.reads
    return Actions(
        model_calls=sum(
            1 for s in spans if (s.attributes or {}).get(GEN_AI_OPERATION_NAME) == OPERATION_CHAT
        ),
        tokens=len(rt.issuer.issued),
        tool_runs=sum(1 for r in reads if r.principal == MCP_PRINCIPAL),
        lookups=sum(1 for r in reads if r.principal == MENTION_PRINCIPAL),
        closed=len(rt.tickets.find_tickets(state=DONE)),
    )


def _run[T](world: World, body: Callable[[Runtime], Awaitable[T]], **options: Any) -> T:
    async def main() -> T:
        with anyio.fail_after(30):
            async with offline_runtime(world, **options) as rt:
                return await body(rt)

    return anyio.run(main)


def _people(world: World) -> list[Person]:
    return [p for p in world.people if p.status == "active" and p.slack_active]


async def _ask(rt: Runtime, person: Person, number: int, text: str = OWN_REQUEST) -> Outcome:
    """`person` sends one message, now."""
    ticket = Ticket(
        id=f"CTL-{person.directory_id}-{number}",
        channel=rt.slack.dm_channel_for(person.slack_user_id),
        sender_slack_user_id=person.slack_user_id,
        sender_team_id=rt.world.slack_team.team_id,
        text=text,
        received_at=rt.world.reference_time,
    )
    return await rt.agent.handle(rt.slack.message_event(ticket))


def _is_handoff(text: str) -> bool:
    return any(v in text for variants in replies.TEMPLATES["handoff"].values() for v in variants)


def _turn_on(rt: Runtime, reason: str = "drill") -> None:
    rt.controls.kill_switch.turn_on(by=IT_LEAD, reason=reason)


# --- kill switch: on before the tickets arrive ---------------------------------------


def test_switch_on_means_no_agent_action_for_any_ticket(world: World) -> None:
    async def body(rt: Runtime) -> tuple[Runtime, list[Outcome]]:
        _turn_on(rt, "agent is looping")
        outcomes = []
        for ticket in world.tickets:
            rt.clock.advance_to(parse_time(ticket.received_at))
            outcomes.append(await rt.agent.handle(rt.slack.message_event(ticket)))
        rt.clock.advance(timedelta(days=3, minutes=1))
        rt.agent.sweep()
        return rt, outcomes

    rt, outcomes = _run(world, body)
    assert _actions(rt) == NONE  # the target: 0 agent actions after the switch is on

    bound = [o for o in outcomes if o.action == "escalate"]
    (outsider,) = [o for o in outcomes if o.action == "verify_identity"]
    assert len(bound) == len(world.tickets) - 1
    for outcome in bound:
        ticket = rt.tickets.get_ticket(outcome.ticket or "")
        assert (outcome.status, outcome.reason, outcome.category) == (
            "handled",
            KILL_SWITCH_ON,
            None,
        )
        assert (ticket.state, ticket.queue) == (TRIAGE, QUEUE_SERVICE_DESK)
        assert any(KILL_SWITCH_ON in c.body for c in ticket.comments)
    # An unverified sender is routed to identity verification, as before.
    verify = rt.tickets.get_ticket(outsider.ticket or "")
    assert (verify.state, verify.queue) == (TODO, QUEUE_IDENTITY_VERIFICATION)

    # Everyone got one reply, and every verified requester got the normal hand-off.
    assert len(rt.slack.sent_messages) == len(world.tickets)
    assert sum(1 for m in rt.slack.sent_messages if _is_handoff(m.text)) == len(bound)

    (toggle,) = rt.controls.audit.events("kill_switch.on")
    assert (toggle.actor, toggle.reason) == (IT_LEAD, "agent is looping")
    # The agent never tried a tool call, so the server had nothing to refuse.
    assert rt.controls.audit.events("kill_switch.refused_call") == ()


# --- kill switch: flipped in the middle of a run -------------------------------------


class FlippingModel:
    """The stub, plus a hand on the switch: it turns the kill switch on while it
    answers one task, as if IT pressed it in the middle of a run."""

    provider = "test"
    model = "flipping"

    def __init__(self, flip_on_task: str) -> None:
        self._stub = StubModel()
        self._flip_on_task = flip_on_task
        self.rt: Runtime | None = None
        self.before: Actions | None = None

    def complete(self, request: ModelRequest) -> ModelResponse:
        response = self._stub.complete(request)
        if request.task == self._flip_on_task:
            assert self.rt is not None
            self.before = _actions(self.rt)
            _turn_on(self.rt, "pressed mid-run")
        return response


def _colleague_name(world: World, person: Person) -> str:
    """Another employee's full name, so the mention check has someone to look up."""
    names = {p.full_name for p in world.people}
    return next(
        p.full_name
        for p in _people(world)
        if p.full_name != person.full_name and sum(n == p.full_name for n in names) == 1
    )


@pytest.mark.parametrize("task", ["triage", "own_account_check"])
def test_switch_flipped_during_a_model_call_stops_everything_after_it(
    world: World, task: str
) -> None:
    model = FlippingModel(task)
    person = _people(world)[0]
    # The message names a real colleague: without the switch, the mention check would
    # look the name up in the directory.
    text = f"what's my username? {_colleague_name(world, person)} said to ask here."

    async def body(rt: Runtime) -> tuple[Runtime, Outcome]:
        model.rt = rt
        return rt, await _ask(rt, person, 1, text)

    rt, outcome = _run(world, body, model=model)
    assert model.before is not None
    # The model call that was running when the switch flipped is the last action:
    # its span closes after the flip, and nothing else follows.
    after = _actions(rt) - model.before
    assert after == Actions(model_calls=1, tokens=0, tool_runs=0, lookups=0, closed=0)
    assert _actions(rt).model_calls == (1 if task == "triage" else 2)
    assert _actions(rt).tokens == 0 and _actions(rt).lookups == 0

    assert (outcome.action, outcome.state) == ("escalate", TRIAGE)
    ticket = rt.tickets.get_ticket(outcome.ticket or "")
    assert ticket.state == TRIAGE and any(KILL_SWITCH_ON in c.body for c in ticket.comments)
    (reply,) = rt.slack.sent_messages
    assert _is_handoff(reply.text) and person.primary_email not in reply.text


def test_switch_flipped_after_the_token_is_minted_is_refused_by_the_server(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    person = _people(world)[0]

    async def body(rt: Runtime) -> tuple[Runtime, Outcome]:
        real_mint = rt.issuer.mint

        def mint_then_flip(*args: Any, **kwargs: Any) -> Any:
            issued = real_mint(*args, **kwargs)
            _turn_on(rt, "pressed between the token and the tool call")
            return issued

        monkeypatch.setattr(rt.issuer, "mint", mint_then_flip)
        return rt, await _ask(rt, person, 1)

    rt, outcome = _run(world, body)
    # The token existed before the flip. After it: the tool never ran.
    assert _actions(rt) == Actions(model_calls=2, tokens=1, tool_runs=0, lookups=0, closed=0)
    (refused,) = rt.controls.audit.events("kill_switch.refused_call")
    assert (refused.actor, refused.details["tool"]) == (MCP_PRINCIPAL, "get_my_username")
    assert (outcome.action, outcome.state) == ("escalate", TRIAGE)
    ticket = rt.tickets.get_ticket(outcome.ticket or "")
    assert any(KILL_SWITCH_ON in c.body for c in ticket.comments)
    (reply,) = rt.slack.sent_messages
    assert _is_handoff(reply.text) and person.primary_email not in reply.text


def test_switch_flipped_while_the_tool_runs_means_the_answer_is_not_sent(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    person = _people(world)[0]

    async def body(rt: Runtime) -> tuple[Runtime, Outcome]:
        real_get = rt.directory.users_get

        def get_then_flip(user_key: str, *, principal: str, **kwargs: Any) -> Any:
            found = real_get(user_key, principal=principal, **kwargs)
            if principal == MCP_PRINCIPAL:
                _turn_on(rt, "pressed while the tool ran")
            return found

        monkeypatch.setattr(rt.directory, "users_get", get_then_flip)
        return rt, await _ask(rt, person, 1)

    rt, outcome = _run(world, body)
    assert _actions(rt).tool_runs == 1  # it was already running when the switch flipped
    assert (outcome.action, outcome.state) == ("escalate", TRIAGE)
    (reply,) = rt.slack.sent_messages
    assert _is_handoff(reply.text) and person.primary_email not in reply.text
    assert rt.tickets.find_tickets(state=AWAITING_REPLY) == ()


# --- kill switch: an agent that ignores it -------------------------------------------


def test_the_server_refuses_every_tool_call_while_the_switch_is_on(world: World) -> None:
    """Straight to the MCP server with valid tokens, as a hijacked agent would."""
    people = _people(world)[:3]

    async def body(rt: Runtime) -> tuple[Runtime, list[McpToolError], int]:
        _turn_on(rt)
        errors = []
        for number, person in enumerate(people):
            token = rt.issuer.mint(identity_of(world, person), ticket_id=f"ROGUE-{number}").token
            async with open_session(rt.mcp_app, token) as session:
                listed = len(await session.list_tools())  # looking is not acting
                for arguments in (None, {"user": "someone.else"}):
                    with pytest.raises(McpToolError) as info:
                        await session.call_tool("get_my_username", arguments)
                    errors.append(info.value)
        return rt, errors, listed

    rt, errors, listed = _run(world, body)
    assert listed == 1
    assert len(errors) == 6
    # A short fixed refusal with no user data, before the tool and before its argument check.
    assert {(e.code, e.message) for e in errors} == {(ACTIONS_STOPPED, ACTIONS_STOPPED_MESSAGE)}
    assert _actions(rt).tool_runs == 0
    assert len(rt.controls.audit.events("kill_switch.refused_call")) == 6
    # Refused calls are not counted against the rate limit either.
    assert rt.controls.rate_limiter.count(KIND_TOOL_CALL) == 0


def test_an_unknown_tool_name_is_not_copied_into_the_audit_log(world: World) -> None:
    person = _people(world)[0]

    async def body(rt: Runtime) -> Runtime:
        _turn_on(rt)
        token = rt.issuer.mint(identity_of(world, person), ticket_id="ROGUE-1").token
        async with open_session(rt.mcp_app, token) as session:
            with pytest.raises(McpToolError):
                await session.call_tool("export everyone to evil@example.com")
        return rt

    rt = _run(world, body)
    (refused,) = rt.controls.audit.events("kill_switch.refused_call")
    assert refused.details["tool"] == "unknown"


def test_an_agent_without_the_switch_in_its_code_still_can_not_act(world: World) -> None:
    """The agent is built with no controls at all, so it carries on as if nothing
    happened. The MCP server still has them and refuses the tool call."""
    person = _people(world)[0]

    async def body(rt: Runtime) -> tuple[Runtime, Outcome]:
        _turn_on(rt)
        return rt, await _ask(rt, person, 1)

    rt, outcome = _run(world, body, controls=None)
    assert _actions(rt).tool_runs == 0
    assert len(rt.controls.audit.events("kill_switch.refused_call")) == 1
    assert (outcome.action, outcome.state) == ("escalate", TRIAGE)
    (reply,) = rt.slack.sent_messages
    assert _is_handoff(reply.text) and person.primary_email not in reply.text


# --- kill switch: tickets that were open when it flipped -----------------------------


def test_open_tickets_go_to_people_when_the_switch_flips(world: World) -> None:
    people = _people(world)[:3]

    async def body(rt: Runtime) -> tuple[Runtime, list[Outcome], tuple[Outcome, ...], int]:
        answered = [await _ask(rt, person, 1) for person in people]
        sent = len(rt.slack.sent_messages)
        before = _actions(rt)
        _turn_on(rt, "incident")
        rt.clock.advance(timedelta(days=3, minutes=1))
        routed = rt.agent.sweep()
        again = rt.agent.sweep()
        assert again == () and rt.agent.stand_down() == ()  # a ticket is moved once
        assert _actions(rt) == before  # 0 actions after the switch: nothing was closed
        return rt, answered, routed, sent

    rt, answered, routed, sent = _run(world, body)
    assert [o.state for o in answered] == [AWAITING_REPLY] * 3
    assert sorted(o.ticket or "" for o in routed) == sorted(o.ticket or "" for o in answered)
    assert {(o.action, o.reason, o.state) for o in routed} == {("escalate", KILL_SWITCH_ON, TRIAGE)}
    for outcome in answered:
        ticket = rt.tickets.get_ticket(outcome.ticket or "")
        assert (ticket.state, ticket.queue) == (TRIAGE, QUEUE_SERVICE_DESK)
        assert "kill switch is on" in ticket.comments[-1].body
    # No closing note and no other message: handing over sends nothing.
    assert len(rt.slack.sent_messages) == sent
    assert rt.tickets.find_tickets(state=DONE) == ()


def test_a_new_ticket_also_hands_over_the_open_ones(world: World) -> None:
    first, second, third = _people(world)[:3]

    async def body(rt: Runtime) -> tuple[Runtime, Outcome]:
        await _ask(rt, first, 1)
        await _ask(rt, second, 1)
        _turn_on(rt)
        return rt, await _ask(rt, third, 1)

    rt, outcome = _run(world, body)
    assert outcome.reason == KILL_SWITCH_ON
    assert len(rt.tickets.find_tickets(state=TRIAGE)) == 3
    assert rt.tickets.find_tickets(state=AWAITING_REPLY) == ()


def test_switch_off_again_and_tier0_works_as_before(world: World) -> None:
    first, second = _people(world)[:2]

    async def body(rt: Runtime) -> tuple[Runtime, Outcome, Outcome]:
        _turn_on(rt)
        stopped = await _ask(rt, first, 1)
        rt.controls.kill_switch.turn_off(by=IT_LEAD, reason="fixed and checked")
        return rt, stopped, await _ask(rt, second, 1)

    rt, stopped, resumed = _run(world, body)
    assert (stopped.action, resumed.action, resumed.state) == (
        "escalate",
        "answer",
        AWAITING_REPLY,
    )
    # What was handed to a person stays with that person.
    assert rt.tickets.get_ticket(stopped.ticket or "").state == TRIAGE
    assert [e.event for e in rt.controls.audit.entries] == ["kill_switch.on", "kill_switch.off"]


def test_with_the_switch_off_nothing_changes_and_nothing_is_logged(world: World) -> None:
    async def body(rt: Runtime) -> Runtime:
        for ticket in world.tickets:
            rt.clock.advance_to(parse_time(ticket.received_at))
            await rt.agent.handle(rt.slack.message_event(ticket))
        assert rt.agent.stand_down() == ()
        return rt

    rt = _run(world, body)
    assert not rt.controls.kill_switch.is_on
    assert rt.controls.audit.entries == ()
    assert len(rt.tickets.find_tickets(state=AWAITING_REPLY)) == 4


# --- rate limit ----------------------------------------------------------------------


def test_requests_over_a_requesters_limit_go_to_a_person_with_no_action(world: World) -> None:
    person, other = _people(world)[:2]
    limits = RateLimitSettings(per_requester_limit=2, global_limit=100)

    async def body(rt: Runtime) -> tuple[Runtime, list[Outcome], Actions, Outcome, Outcome]:
        outcomes = []
        for number in range(1, 6):
            outcomes.append(await _ask(rt, person, number))
            rt.clock.advance(timedelta(minutes=5))
        during = _actions(rt)
        someone_else = await _ask(rt, other, 1)
        rt.clock.advance(timedelta(hours=1))
        return rt, outcomes, during, someone_else, await _ask(rt, person, 6)

    rt, outcomes, during, someone_else, later = _run(
        world, body, rate_limits=limits, context_settings=NO_REPEAT_CHECK
    )
    assert [o.action for o in outcomes] == ["answer", "answer", "escalate", "escalate", "escalate"]
    assert [o.reason for o in outcomes[2:]] == [RATE_LIMITED_CODE] * 3
    # Only the two requests under the limit led to any action at all.
    assert during == Actions(model_calls=4, tokens=2, tool_runs=2, lookups=0, closed=0)
    for outcome in outcomes[2:]:
        ticket = rt.tickets.get_ticket(outcome.ticket or "")
        assert (ticket.state, ticket.queue) == (TRIAGE, QUEUE_SERVICE_DESK)
        assert any(RATE_LIMITED_CODE in c.body for c in ticket.comments)
    assert sum(1 for m in rt.slack.sent_messages if _is_handoff(m.text)) == 3

    hits = rt.controls.audit.events("rate_limit.hit")
    assert [dict(h.details) for h in hits] == [
        {
            "scope": REQUESTER,
            "kind": KIND_REQUEST,
            "requester": person.directory_id,
            "limit": 2,
            "window_seconds": 3600,
        }
    ] * 3
    assert {h.actor for h in hits} == {"tier0-agent"}
    # It is per requester, and it rolls: a colleague is served, and so is the same
    # person an hour later.
    assert (someone_else.action, later.action) == ("answer", "answer")


def test_global_limit_hands_the_requests_over_it_to_people(world: World) -> None:
    people = _people(world)[:6]
    limits = RateLimitSettings(per_requester_limit=5, global_limit=4)

    async def body(rt: Runtime) -> tuple[Runtime, list[Outcome]]:
        return rt, [await _ask(rt, person, 1) for person in people]

    rt, outcomes = _run(world, body, rate_limits=limits)
    assert [o.action for o in outcomes] == ["answer"] * 4 + ["escalate"] * 2
    assert _actions(rt) == Actions(model_calls=8, tokens=4, tool_runs=4, lookups=0, closed=0)
    assert [h.details["scope"] for h in rt.controls.audit.events("rate_limit.hit")] == [GLOBAL] * 2


def test_the_server_refuses_tool_calls_over_the_limit_whatever_the_agent_does(
    world: World,
) -> None:
    """Straight to the MCP server with a fresh valid token each time."""
    person, other = _people(world)[:2]
    limits = RateLimitSettings(per_requester_limit=3, global_limit=100)

    async def call(rt: Runtime, who: Person, number: int) -> McpToolError | None:
        token = rt.issuer.mint(identity_of(world, who), ticket_id=f"ROGUE-{number}").token
        try:
            async with open_session(rt.mcp_app, token) as session:
                await session.call_tool("get_my_username")
        except McpToolError as error:
            return error
        return None

    async def body(rt: Runtime) -> tuple[Runtime, list[McpToolError | None], McpToolError | None]:
        results = [await call(rt, person, number) for number in range(8)]
        return rt, results, await call(rt, other, 99)

    rt, results, other_result = _run(world, body, rate_limits=limits)
    assert [r is None for r in results] == [True] * 3 + [False] * 5
    refused = [r for r in results if r is not None]
    assert {(e.code, e.message) for e in refused} == {(RATE_LIMITED, RATE_LIMITED_MESSAGE)}
    assert other_result is None
    # The target: 0 actions over the limit. 3 ran for this requester, never a 4th.
    runs = [r for r in rt.directory.reads if r.principal == MCP_PRINCIPAL]
    assert sum(1 for r in runs if r.resolved_id == person.directory_id) == 3
    hits = rt.controls.audit.events("rate_limit.hit")
    assert len(hits) == 5 and {h.actor for h in hits} == {MCP_PRINCIPAL}
    assert {h.details["kind"] for h in hits} == {KIND_TOOL_CALL}


def test_an_agent_without_the_limit_in_its_code_is_still_held_by_the_server(
    world: World,
) -> None:
    person = _people(world)[0]
    limits = RateLimitSettings(per_requester_limit=2, global_limit=100)

    async def body(rt: Runtime) -> tuple[Runtime, list[Outcome]]:
        return rt, [await _ask(rt, person, number) for number in range(1, 5)]

    rt, outcomes = _run(
        world, body, rate_limits=limits, context_settings=NO_REPEAT_CHECK, controls=None
    )
    assert [o.action for o in outcomes] == ["answer", "answer", "escalate", "escalate"]
    assert _actions(rt).tool_runs == 2
    for outcome in outcomes[2:]:
        comments = rt.tickets.get_ticket(outcome.ticket or "").comments
        assert any(RATE_LIMITED_CODE in c.body for c in comments)
    assert sum(1 for m in rt.slack.sent_messages if person.primary_email in m.text) == 2


def test_a_person_can_raise_the_limit_for_bulk_work(world: World) -> None:
    people = _people(world)[:5]
    limits = RateLimitSettings(per_requester_limit=5, global_limit=2)

    async def body(rt: Runtime) -> tuple[Runtime, list[Outcome]]:
        rt.controls.rate_limiter.override(
            scope=GLOBAL,
            limit=5,
            by=IT_LEAD,
            reason="five new hires start today",
            lasts=timedelta(hours=4),
        )
        return rt, [await _ask(rt, person, 1) for person in people]

    rt, outcomes = _run(world, body, rate_limits=limits)
    assert [o.action for o in outcomes] == ["answer"] * 5
    (override,) = rt.controls.audit.events("rate_limit.override")
    assert (override.actor, override.reason) == (IT_LEAD, "five new hires start today")
    assert rt.controls.audit.events("rate_limit.hit") == ()


def test_default_limits_leave_normal_use_alone(world: World) -> None:
    async def body(rt: Runtime) -> Runtime:
        for ticket in world.tickets:
            rt.clock.advance_to(parse_time(ticket.received_at))
            await rt.agent.handle(rt.slack.message_event(ticket))
        return rt

    rt = _run(world, body)
    assert rt.controls.rate_limiter.settings == RateLimitSettings()
    assert rt.controls.audit.events("rate_limit.hit") == ()


# --- the name-existence probe (ADR-017's residual risk) ------------------------------

MADE_UP = "Zorblax Quimby"


def _probe(name: str) -> str:
    return f"what's my username? {name} said to ask here."


def test_rate_limit_bounds_the_name_existence_probe(world: World) -> None:
    """ADR-017: a verified employee can tell whether a name is in the directory, because
    a real name is handed off with the refusal text and a made-up one is answered. The
    per-requester limit caps how many of those answers one person gets per hour. After
    that every request gets the same plain hand-off, with no lookup, whatever it names."""
    person = _people(world)[0]
    real = _colleague_name(world, person)
    assert all(p.full_name != MADE_UP for p in world.people)
    limit = RateLimitSettings().per_requester_limit

    async def body(rt: Runtime) -> tuple[Runtime, list[Outcome], list[int]]:
        outcomes, lookups = [], []
        for number in range(1, 13):
            name = real if number % 2 else MADE_UP
            outcomes.append(await _ask(rt, person, number, _probe(name)))
            lookups.append(_actions(rt).lookups)
            rt.clock.advance(timedelta(minutes=4))
        return rt, outcomes, lookups

    rt, outcomes, lookups = _run(world, body, context_settings=NO_REPEAT_CHECK)
    # Under the limit the probe works: real names are handed off, made-up ones answered.
    assert [o.action for o in outcomes[:limit]] == [
        "escalate" if number % 2 else "answer" for number in range(1, limit + 1)
    ]
    assert lookups[limit - 1] > 0
    # Over it, nothing more is learned: same outcome and same reply for both kinds of
    # name, and not one more directory lookup.
    over = outcomes[limit:]
    assert len(over) == 12 - limit
    assert {(o.action, o.reason) for o in over} == {("escalate", RATE_LIMITED_CODE)}
    assert lookups[limit:] == [lookups[limit - 1]] * len(over)
    texts = [m.text for m in rt.slack.sent_messages][limit:]
    assert len(texts) == len(over) and all(_is_handoff(text) for text in texts)
    assert len(rt.controls.audit.events("rate_limit.hit")) == len(over)


def test_with_default_settings_the_probe_is_bounded_even_sooner(world: World) -> None:
    """Rung 1's same-day check hands the 4th own-account request in a day to a person,
    before the hourly rate limit of 5 is reached."""
    person = _people(world)[0]
    real = _colleague_name(world, person)

    async def body(rt: Runtime) -> tuple[Runtime, list[Outcome], list[int]]:
        outcomes, lookups = [], []
        for number in range(1, 9):
            name = real if number % 2 else MADE_UP
            outcomes.append(await _ask(rt, person, number, _probe(name)))
            lookups.append(_actions(rt).lookups)
            rt.clock.advance(timedelta(minutes=4))
        return rt, outcomes, lookups

    rt, outcomes, lookups = _run(world, body)
    assert [o.action for o in outcomes] == ["escalate", "answer", "escalate"] + ["escalate"] * 5
    assert lookups[3:] == [lookups[2]] * 5
    assert all(_is_handoff(m.text) for m in rt.slack.sent_messages[3:])


# --- the server's middleware, on its own ---------------------------------------------


class _Ctx:
    def __init__(self, method: str, params: dict[str, Any] | None = None) -> None:
        self.method = method
        self.params = params


def test_a_tool_call_with_no_usable_subject_is_refused(world: World) -> None:
    """Fail closed: the rate limit is per requester, so a call the server can't tie to
    one is refused. (The token check would have stopped it earlier; this is the backstop.)"""

    async def body(rt: Runtime) -> tuple[Runtime, list[str]]:
        gate = enforce_controls(rt.mcp.server, rt.controls)
        passed: list[str] = []

        async def call_next(ctx: Any) -> str:
            passed.append(ctx.method)
            return "ran"

        # No request is in flight here, so there is no access token at all.
        with pytest.raises(MCPError) as info:
            await gate(_Ctx("tools/call", {"name": "get_my_username"}), call_next)
        assert (info.value.error.code, info.value.error.message) == (
            RATE_LIMITED,
            RATE_LIMITED_MESSAGE,
        )
        # Anything that is not a tool call is passed on untouched.
        assert await gate(_Ctx("tools/list"), call_next) == "ran"
        return rt, passed

    rt, passed = _run(world, body)
    assert passed == ["tools/list"]
    assert rt.controls.rate_limiter.count(KIND_TOOL_CALL) == 0
