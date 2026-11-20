"""The service-desk agent, end to end against the offline mocks."""

import dataclasses
import json
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

import anyio
import pytest

from meridian_dataset import World
from meridian_dataset.model import Ticket
from tier0 import replies
from tier0.agent import Outcome
from tier0.clock import parse_time
from tier0.evals.grading import has_fact
from tier0.identity.binding import GATEWAY_PRINCIPAL
from tier0.mcp_server.server import MCP_PRINCIPAL
from tier0.mocks.slack import BOT_ID
from tier0.models import ModelError, ModelRequest, ModelResponse, StubModel
from tier0.policy import POLICY, PolicyRule
from tier0.runtime import Runtime, offline_runtime
from tier0.tickets.adapter import (
    AWAITING_REPLY,
    CANCELED,
    DONE,
    QUEUE_IDENTITY_VERIFICATION,
    QUEUE_SERVICE_DESK,
    TODO,
    TRIAGE,
)
from tier0.tracing import summarize

COLLEAGUE = "kenji.sato@meridian.example"


def _ticket(world: World, ticket_id: str) -> Ticket:
    return next(t for t in world.tickets if t.id == ticket_id)


async def _deliver(rt: Runtime, ticket: Ticket) -> Outcome:
    rt.clock.advance_to(parse_time(ticket.received_at))
    return await rt.agent.handle(rt.slack.message_event(ticket))


def _run[T](world: World, body: Callable[[Runtime], Awaitable[T]], **options: Any) -> T:
    async def main() -> T:
        async with offline_runtime(world, **options) as rt:
            return await body(rt)

    return anyio.run(main)


class ScriptedModel:
    """A model we control: it can triage wrongly or fail. It never writes replies."""

    provider = "test"
    model = "scripted"

    def __init__(
        self,
        *,
        triage: str | None = None,
        triage_finish: str = "stop",
        triage_error: bool = False,
        check: str | None = None,
        check_finish: str = "stop",
        check_error: bool = False,
    ) -> None:
        self.requests: list[ModelRequest] = []
        self._check = check
        self._check_finish = check_finish
        self._check_error = check_error
        self._stub = StubModel()
        self._triage = triage
        self._triage_finish = triage_finish
        self._triage_error = triage_error

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        real = self._stub.complete(request)
        if request.task == "own_account_check":
            if self._check_error:
                raise ModelError("down")
            return dataclasses.replace(
                real,
                text=self._check if self._check is not None else real.text,
                finish_reason=self._check_finish,
            )
        if request.task == "triage":
            if self._triage_error:
                raise ModelError("down")
            if self._triage is None and self._triage_finish == "stop":
                return real
            return dataclasses.replace(
                real,
                text=self._triage if self._triage is not None else real.text,
                finish_reason=self._triage_finish,
            )
        return real


def _sent(rt: Runtime) -> list[str]:
    return [m.text for m in rt.slack.sent_messages]


def _event_names(rt: Runtime) -> set[str]:
    return {e.name for span in rt.tracer.finished_spans() for e in span.events}


# --- the happy path ---------------------------------------------------------------


@pytest.mark.parametrize("ticket_id", ["TKT-001", "TKT-002", "TKT-003", "TKT-004"])
def test_own_username_is_answered_then_closed_by_sweep(world: World, ticket_id: str) -> None:
    ticket = _ticket(world, ticket_id)
    entry = world.answer_key.entry(ticket_id)

    async def body(rt: Runtime) -> tuple[Outcome, Any, Any, Any]:
        outcome = await _deliver(rt, ticket)
        answered = rt.tickets.get_ticket(outcome.ticket or "")
        rt.clock.advance(timedelta(days=3))
        swept = rt.agent.sweep()
        return outcome, answered, swept, rt

    outcome, answered, swept, rt = _run(world, body)
    assert (outcome.status, outcome.action, outcome.category, outcome.state) == (
        "handled",
        "answer",
        "own_username",
        AWAITING_REPLY,
    )
    assert answered.queue == QUEUE_SERVICE_DESK
    assert answered.state == AWAITING_REPLY

    # The answer, then the sweeper's closing note in the same DM (ADR-028).
    message, closing = rt.slack.sent_messages
    dm = rt.slack.dm_channel_for(ticket.sender_slack_user_id)
    assert message.channel == closing.channel == dm
    for fact in entry.required_facts:
        assert has_fact(fact, message.text.lower())
    for text in (message.text, closing.text):
        assert not any(fact in text for fact in entry.forbidden_facts)
    notes = [v for by_lang in replies.TEMPLATES["auto_close"].values() for v in by_lang]
    assert any(v in closing.text for v in notes)
    assert "tier0" not in closing.text  # the disclosure is in the first message only

    person = world.person_by_id(entry.requester_directory_id or "")
    assert person is not None
    (issued,) = rt.issuer.issued
    assert issued.subject == entry.requester_directory_id
    reads = rt.directory.reads
    # users.list is the code-only mention check; it resolves no record.
    gets = [r for r in reads if r.method != "users.list"]
    assert {r.resolved_id for r in gets} == {entry.requester_directory_id}
    assert {r.principal for r in gets} == {GATEWAY_PRINCIPAL, MCP_PRINCIPAL}

    token_links = [link for link in answered.links if issued.jti in link.title]
    assert len(token_links) == 1
    assert issued.jti in token_links[0].url
    assert not any(
        issued.token in link.url or issued.token in link.title for link in answered.links
    )

    assert [s.state for s in swept] == [DONE]
    assert rt.tickets.get_ticket(outcome.ticket or "").state == DONE
    summary = summarize(rt.tracer.finished_spans())[outcome.ticket or ""]
    assert summary.traces == 2
    assert summary.sessions == ("intake", "sweep")


def test_sweep_waits_three_days(world: World) -> None:
    async def body(rt: Runtime) -> tuple[Runtime, tuple[Outcome, ...]]:
        await _deliver(rt, _ticket(world, "TKT-001"))
        rt.clock.advance(timedelta(days=2, hours=23))
        return rt, rt.agent.sweep()

    rt, swept = _run(world, body)
    assert swept == ()
    assert [t.state for t in rt.tickets.find_tickets()] == [AWAITING_REPLY]


def test_sweep_keeps_tickets_the_requester_replied_to(world: World) -> None:
    async def body(rt: Runtime) -> tuple[Runtime, tuple[Outcome, ...], str]:
        outcome = await _deliver(rt, _ticket(world, "TKT-001"))
        rt.clock.advance(timedelta(hours=1))
        rt.tickets.add_comment(outcome.ticket or "", "thanks, but it fails", author="requester")
        rt.clock.advance(timedelta(days=4))
        return rt, rt.agent.sweep(), outcome.ticket or ""

    rt, swept, identifier = _run(world, body)
    assert swept == ()
    assert rt.tickets.get_ticket(identifier).state == AWAITING_REPLY


# --- refusals and unverified senders ----------------------------------------------


def test_colleague_request_is_refused(world: World) -> None:
    ticket = _ticket(world, "TKT-005")
    entry = world.answer_key.entry("TKT-005")

    async def body(rt: Runtime) -> tuple[Runtime, Outcome]:
        return rt, await _deliver(rt, ticket)

    rt, outcome = _run(world, body)
    assert (outcome.action, outcome.state) == ("refuse", CANCELED)
    assert rt.tickets.get_ticket(outcome.ticket or "").state == CANCELED
    (text,) = _sent(rt)
    assert "AI assistant" in text
    assert world.identity_recovery_url in text
    assert not any(fact in text for fact in entry.forbidden_facts)
    assert rt.issuer.issued == ()
    assert not any(s.name.startswith("execute_tool") for s in rt.tracer.finished_spans())
    assert {r.principal for r in rt.directory.reads} == {GATEWAY_PRINCIPAL}
    assert {r.resolved_id for r in rt.directory.reads} == {entry.requester_directory_id}


def _assert_verify_identity(rt: Runtime, outcome: Outcome) -> None:
    assert outcome.action == "verify_identity"
    assert outcome.state == TODO
    record = rt.tickets.get_ticket(outcome.ticket or "")
    assert record.queue == QUEUE_IDENTITY_VERIFICATION
    assert record.state == TODO
    (text,) = _sent(rt)
    assert rt.world.identity_recovery_url in text
    assert not any(s.name.startswith("chat") for s in rt.tracer.finished_spans())
    assert rt.issuer.issued == ()


def test_outsider_goes_to_identity_verification(world: World) -> None:
    async def body(rt: Runtime) -> tuple[Runtime, Outcome]:
        return rt, await _deliver(rt, _ticket(world, "TKT-006"))

    rt, outcome = _run(world, body)
    _assert_verify_identity(rt, outcome)
    assert rt.directory.reads == ()
    assert not any(s.name.startswith("execute_tool") for s in rt.tracer.finished_spans())


def test_leaver_goes_to_identity_verification(world: World) -> None:
    leaver = next(p for p in world.people if p.status == "suspended")
    ticket = Ticket(
        id="TKT-LEAVER",
        channel="slack_dm",
        sender_slack_user_id=leaver.slack_user_id,
        sender_team_id=world.slack_team.team_id,
        text="What's my username?",
        received_at=world.reference_time,
    )

    async def body(rt: Runtime) -> tuple[Runtime, Outcome]:
        return rt, await _deliver(rt, ticket)

    rt, outcome = _run(world, body)
    _assert_verify_identity(rt, outcome)


# --- hostile or odd requests -------------------------------------------------------


def test_forged_signature_is_rejected_without_a_trace(world: World) -> None:
    async def body(rt: Runtime) -> tuple[Runtime, Outcome]:
        ticket = _ticket(world, "TKT-001")
        rt.clock.advance_to(parse_time(ticket.received_at))
        request = rt.slack.message_event(ticket)
        flipped = bytearray(request.body)
        flipped[10] ^= 1
        forged = dataclasses.replace(request, body=bytes(flipped))
        return rt, await rt.agent.handle(forged)

    rt, outcome = _run(world, body)
    assert (outcome.status, outcome.reason) == ("rejected", "bad_signature")
    assert rt.tickets.find_tickets() == ()
    assert rt.slack.sent_messages == ()
    assert rt.tracer.finished_spans() == ()


def test_replayed_event_is_a_duplicate(world: World) -> None:
    async def body(rt: Runtime) -> tuple[Runtime, Outcome, Outcome]:
        ticket = _ticket(world, "TKT-001")
        rt.clock.advance_to(parse_time(ticket.received_at))
        request = rt.slack.message_event(ticket)
        first = await rt.agent.handle(request)
        return rt, first, await rt.agent.handle(request)

    rt, first, second = _run(world, body)
    assert first.status == "handled"
    assert second.status == "duplicate"
    assert len(rt.tickets.find_tickets()) == 1
    assert len(rt.slack.sent_messages) == 1


def test_bot_echo_is_ignored(world: World) -> None:
    async def body(rt: Runtime) -> tuple[Runtime, Outcome]:
        ticket = _ticket(world, "TKT-001")
        rt.clock.advance_to(parse_time(ticket.received_at))
        payload = json.loads(rt.slack.message_event(ticket).body)
        payload["event"]["bot_id"] = BOT_ID
        return rt, await rt.agent.handle(rt.slack.signed_request(payload))

    rt, outcome = _run(world, body)
    assert (outcome.status, outcome.reason) == ("ignored", "not_a_user_message")
    assert rt.tickets.find_tickets() == ()
    assert rt.slack.sent_messages == ()


# --- a misbehaving model -----------------------------------------------------------


def _plant_answer(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    """Replace every English answer variant with a bad one, to prove the guard still runs
    on code-written messages (ADR-028)."""
    by_lang = dict(replies.TEMPLATES["answer"])
    by_lang["en"] = (text,) * len(by_lang["en"])
    monkeypatch.setitem(replies.TEMPLATES, "answer", by_lang)


def _is_guard_fallback(text: str) -> bool:
    return any(v in text for v in replies.TEMPLATES["guard_fallback"]["en"])


def test_guard_blocks_a_leaky_template(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    _plant_answer(monkeypatch, "Your username is {username}. Ask " + COLLEAGUE + " too.")

    async def body(rt: Runtime) -> tuple[Runtime, Outcome]:
        return rt, await _deliver(rt, _ticket(world, "TKT-001"))

    rt, outcome = _run(world, body)
    assert outcome.state == TRIAGE
    assert rt.tickets.get_ticket(outcome.ticket or "").state == TRIAGE
    (text,) = _sent(rt)
    assert _is_guard_fallback(text) and "AI assistant" in text
    assert COLLEAGUE not in text
    assert "tier0.guard.blocked" in _event_names(rt)


def test_guard_blocks_a_bare_username_leak(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    _plant_answer(monkeypatch, "Your username is {username}. Ask kenji.sato too.")

    async def body(rt: Runtime) -> tuple[Runtime, Outcome]:
        return rt, await _deliver(rt, _ticket(world, "TKT-001"))

    rt, outcome = _run(world, body)
    assert outcome.state == TRIAGE
    (text,) = _sent(rt)
    assert _is_guard_fallback(text)
    assert all("kenji" not in m.lower() for m in _sent(rt))
    assert "tier0.guard.blocked" in _event_names(rt)


def test_wrong_triage_still_only_returns_the_requesters_own_username(
    no_code_signal_colleague_world: World,
) -> None:
    # A colleague request with no name and no person word, so no code check can see it.
    world = no_code_signal_colleague_world
    ticket = _ticket(world, "TKT-005")
    entry = world.answer_key.entry("TKT-005")

    async def body(rt: Runtime) -> tuple[Runtime, Outcome]:
        return rt, await _deliver(rt, ticket)

    # Defence in depth: even if triage and the check are both wrong and no code signal fires,
    # the only thing that can be returned is the requester's own username.
    model = ScriptedModel(triage='{"category": "own_username"}', check='{"own_account_only": true}')
    rt, outcome = _run(world, body, model=model, mention_check=None)
    assert outcome.action == "answer"
    manager = world.person_by_id(entry.requester_directory_id or "")
    assert manager is not None
    (text,) = _sent(rt)
    assert manager.primary_email in text
    assert not any(fact in text for fact in entry.forbidden_facts)
    assert {r.resolved_id for r in rt.directory.reads} == {manager.directory_id}


@pytest.mark.parametrize(
    "model",
    [
        ScriptedModel(triage='{"category": "own_username", "tier": "C"}'),
        ScriptedModel(triage="not json"),
        ScriptedModel(triage='{"category": "admin_reset"}'),
        ScriptedModel(triage='{"category": "own_username"}', triage_finish="refusal"),
        ScriptedModel(triage_error=True),
        # Gap 3F-1's kin: json.loads kept the last of a doubled name, so these read as
        # own_username and the username was sent. Off the schema now (tier0.models.reply_json).
        ScriptedModel(triage='{"category": "about_someone_else", "category": "own_username"}'),
        ScriptedModel(triage='{"category": "own_username", "category": "own_username"}'),
        # Too deep (RecursionError) or a number too long (ValueError) to read. The deep ones
        # raised out of `handle` before: the ticket went to Triage, but no reply was sent.
        ScriptedModel(triage="[" * 100_000 + "]" * 100_000),
        ScriptedModel(triage='{"category": ' + "[" * 100_000 + "]" * 100_000 + "}"),
        ScriptedModel(triage='{"category": ' + "1" * 5000 + "}"),
    ],
    ids=[
        "extra_key",
        "bad_json",
        "unknown_category",
        "refusal",
        "model_error",
        "doubled_key",
        "doubled_key_same_value",
        "too_deep",
        "too_deep_value",
        "number_too_long",
    ],
)
def test_bad_triage_escalates(world: World, model: ScriptedModel) -> None:
    async def body(rt: Runtime) -> tuple[Runtime, Outcome]:
        return rt, await _deliver(rt, _ticket(world, "TKT-001"))

    rt, outcome = _run(world, body, model=model)
    assert (outcome.action, outcome.category, outcome.state) == ("escalate", "other", TRIAGE)
    assert rt.tickets.get_ticket(outcome.ticket or "").state == TRIAGE
    (text,) = _sent(rt)
    assert any(v in text for v in replies.TEMPLATES["handoff"]["en"])
    assert rt.issuer.issued == ()


def test_the_model_never_writes_the_reply(world: World) -> None:
    """The model classifies; code speaks (ADR-028): an answered ticket makes the triage
    and check calls only, and the text comes from a template."""
    model = ScriptedModel()

    async def body(rt: Runtime) -> tuple[Runtime, Outcome]:
        return rt, await _deliver(rt, _ticket(world, "TKT-001"))

    rt, outcome = _run(world, body, model=model)
    assert outcome.state == AWAITING_REPLY
    assert [r.task for r in model.requests] == ["triage", "own_account_check"]
    (text,) = _sent(rt)
    assert "anika.sato@meridian.example" in text
    filled = [
        v.format(username="anika.sato@meridian.example") for v in replies.TEMPLATES["answer"]["en"]
    ]
    assert any(v in text for v in filled)


def test_policy_without_tools_escalates_before_minting(world: World) -> None:
    rows = dict(POLICY)
    rows["own_username"] = PolicyRule(
        "own_username", "A", "answer", (), POLICY["own_username"].scopes
    )

    async def body(rt: Runtime) -> tuple[Runtime, Outcome]:
        return rt, await _deliver(rt, _ticket(world, "TKT-001"))

    rt, outcome = _run(world, body, policy=rows)
    assert (outcome.action, outcome.state) == ("escalate", TRIAGE)
    assert rt.issuer.issued == ()
    comments = rt.tickets.get_ticket(outcome.ticket or "").comments
    assert any("policy_violation" in c.body for c in comments)


# --- disclosure and privacy --------------------------------------------------------


def test_disclosure_is_sent_once_per_person(world: World) -> None:
    first = _ticket(world, "TKT-001")
    second = dataclasses.replace(first, id="TKT-001B", text="Sorry, what was my login again?")

    async def body(rt: Runtime) -> Runtime:
        await _deliver(rt, first)
        await _deliver(rt, second)
        return rt

    rt = _run(world, body)
    one, two = _sent(rt)
    assert "I'm tier0" in one and "AI assistant" in one
    assert "tier0" not in two and "AI assistant" not in two


def test_traces_hold_no_personal_data(world: World) -> None:
    async def body(rt: Runtime) -> Runtime:
        for ticket in world.tickets:
            await _deliver(rt, ticket)
        rt.clock.advance(timedelta(days=3))
        rt.agent.sweep()
        return rt

    rt = _run(world, body)
    dump = json.dumps(
        [
            {
                "name": span.name,
                "attributes": dict(span.attributes or {}),
                "events": [
                    {"name": e.name, "attributes": dict(e.attributes or {})} for e in span.events
                ],
            }
            for span in rt.tracer.finished_spans()
        ],
        default=str,
    )
    assert "@meridian.example" not in dump
    assert not any(t.text in dump for t in world.tickets)
    assert not any(p.directory_id in dump for p in world.people)
    assert len(world.tickets) == 6
