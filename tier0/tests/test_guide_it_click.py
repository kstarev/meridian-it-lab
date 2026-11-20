"""Guide answers wait for one click from a person in IT (ADR-053, the owner's ruling 12b).

With `GUIDE_ANSWERS_NEED_IT_CLICK` on, the default, a guide the picker chooses goes to #it-team as
a draft, the requester gets the existing hand-off reply, and only a signed click from #it-team by
a member of the IT team posts the guide, once. Everything runs on the offline mocks with a fake
picker: no model call costs money and no real Slack call is made. The messages are written in
this file; no pool wording is used. Each picker is built with no how-to gate
(`gate_version=None`), the default before milestone 3f: these tests are of the draft and the
click; tier0/tests/test_guide_gate_slack.py tests the gate.
"""

import dataclasses
import inspect
import json
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

import anyio
import pytest

from meridian_dataset import World
from meridian_dataset.knowledge import Guide, load_runbooks
from meridian_dataset.model import Person, Ticket
from tier0 import controls, replies
from tier0.agent import (
    ANSWER_GUIDE,
    DRAFT_GUIDE,
    GUIDE_WAITS_FOR_IT,
    KILL_SWITCH_ON,
    Outcome,
    ServiceDeskAgent,
)
from tier0.knowledge import review
from tier0.knowledge.answer import reply_text
from tier0.knowledge.wiring import GuidePicker, code_link
from tier0.mocks.slack import CHANNELS, SentMessage, SlackHttpRequest
from tier0.models import ModelRequest, ModelResponse
from tier0.runtime import Runtime, offline_runtime
from tier0.tickets.adapter import AWAITING_REPLY, DONE, TRIAGE

RUNBOOKS = load_runbooks()
BY_ID = {g.id: g for g in RUNBOOKS}
WIFI = "How do I connect to the office Wi-Fi?"
GUIDE_ID = "KB-001"
IT_LEAD = "U0ITLEAD"
_ENGLISH = frozenset({"US", "CA", "GB", "IE", "PL", "NL", "SG", "AU", "IN"})


class FakePicker:
    """Answers every picker call with one guide. No model, no cost."""

    provider = "fake"
    model = "fake-picker"
    temperature: float | None = None
    seed: int | None = None
    supports_seed = False

    def __init__(self, guide: str = GUIDE_ID) -> None:
        self.guide = guide
        self.requests: list[ModelRequest] = []

    def describe(self) -> dict[str, str]:
        return {}

    def complete(self, request: ModelRequest) -> ModelResponse:
        assert request.task == "guide_pick"
        self.requests.append(request)
        text = json.dumps({"request_type": "how_to", "guide": self.guide})
        return ModelResponse(text, self.model, 900, 12, "stop", "fake-1")


def _run[T](world: World, body: Callable[[Runtime], Awaitable[T]], **options: Any) -> T:
    async def main() -> T:
        with anyio.fail_after(30):
            async with offline_runtime(world, **options) as rt:
                return await body(rt)

    return anyio.run(main)


def _requester(world: World) -> Person:
    """An active Slack user outside IT who gets replies in English."""
    return next(
        p
        for p in world.people
        if p.status == "active"
        and p.slack_active
        and p.country in _ENGLISH
        and p.department != review.IT_DEPARTMENT
    )


def _it_member(world: World) -> str:
    return sorted(review.it_team(world))[0]


async def _ask(rt: Runtime, person: Person, text: str = WIFI) -> Outcome:
    ticket = Ticket(
        id="KNOW-1",
        channel=rt.slack.dm_channel_for(person.slack_user_id),
        sender_slack_user_id=person.slack_user_id,
        sender_team_id=rt.world.slack_team.team_id,
        text=text,
        received_at=rt.world.reference_time,
    )
    return await rt.agent.handle(rt.slack.message_event(ticket))


def _click(
    rt: Runtime,
    ticket: str,
    user: str,
    *,
    action: str = review.SEND_GUIDE_ACTION,
    channel: str = review.IT_TEAM_CHANNEL,
) -> str:
    return rt.agent.handle_interaction(
        rt.slack.button_click(channel=channel, user=user, action_id=action, value=ticket)
    )


def _in(rt: Runtime, channel: str) -> list[SentMessage]:
    return [m for m in rt.slack.sent_messages if m.channel == channel]


def _dm(rt: Runtime, person: Person) -> list[str]:
    return [m.text for m in _in(rt, rt.slack.dm_channel_for(person.slack_user_id))]


def _body(message: str) -> str:
    """What follows the greeting (and, in the first message, the AI disclosure)."""
    return message.split("\n\n", 1)[1]


def _has_steps(texts: list[str], guide: Guide) -> bool:
    return any(step in text for text in texts for step in guide.steps)


def _events(rt: Runtime, name: str) -> list[dict[str, Any]]:
    return [
        dict(e.attributes or {})
        for span in rt.tracer.finished_spans()
        for e in span.events
        if e.name == name
    ]


def _comments(rt: Runtime, ticket: str) -> list[str]:
    return [c.body for c in rt.tickets.get_ticket(ticket).comments]


def _clicks(rt: Runtime) -> list[tuple[str, str, str]]:
    """(event, who, why) for every click the audit log holds."""
    return [
        (e.event, e.actor, str(e.details["why"])) for e in rt.controls.audit.events("guide_draft.")
    ]


# --- the default ------------------------------------------------------------------------------


def test_the_setting_is_on_by_default_and_the_agent_takes_its_default_from_it() -> None:
    assert controls.GUIDE_ANSWERS_NEED_IT_CLICK is True
    default = inspect.signature(ServiceDeskAgent).parameters["guide_answers_need_it_click"].default
    assert default is controls.GUIDE_ANSWERS_NEED_IT_CLICK


def test_the_it_team_is_the_active_it_department(world: World) -> None:
    team = review.it_team(world)
    it = [p for p in world.people if p.department == "IT"]
    assert team == {p.slack_user_id for p in it if p.status == "active" and p.slack_active}
    assert team and len(team) < len(it)  # this world has a suspended IT member, who is left out


# --- a guide pick produces an IT draft, and no guide reaches the requester ----------------------


def test_a_guide_pick_makes_an_it_draft_and_the_requester_gets_the_hand_off_only(
    world: World,
) -> None:
    person = _requester(world)
    guide = BY_ID[GUIDE_ID]
    picker = FakePicker()

    async def body(rt: Runtime) -> tuple[Runtime, Outcome]:
        return rt, await _ask(rt, person)

    rt, outcome = _run(world, body, guide_picker=GuidePicker(model=picker, gate_version=None))
    ticket = outcome.ticket or ""
    assert (outcome.action, outcome.category, outcome.state) == (DRAFT_GUIDE, "other", TRIAGE)
    assert rt.tickets.get_ticket(ticket).state == TRIAGE
    # The requester: the existing hand-off reply, with the AI disclosure, and no guide.
    (ack,) = _dm(rt, person)
    assert "AI assistant" in ack
    assert _body(ack) in replies.TEMPLATES["handoff"]["en"]
    assert not _has_steps(_dm(rt, person), guide)
    # IT: one draft in #it-team, with the reply word for word and the two buttons.
    (draft,) = _in(rt, CHANNELS["it-team"])
    assert draft.text == review.draft_text(ticket, guide, reply_text(guide, "en"))
    assert reply_text(guide, "en") in draft.text
    (buttons,) = [b["elements"] for b in draft.blocks if b["type"] == "actions"]
    assert [(b["action_id"], b["value"], b["text"]["text"]) for b in buttons] == [
        (review.SEND_GUIDE_ACTION, ticket, "Send this guide"),
        (review.TAKE_OVER_ACTION, ticket, "Take it over"),
    ]
    # The draft names the ticket and the guide, never the message or the requester.
    assert WIFI not in draft.text
    assert person.primary_email not in draft.text and person.given_name not in draft.text
    # The ticket is a hand-off, with the draft noted on it.
    assert [e["reason"] for e in _events(rt, "tier0.escalated")] == [GUIDE_WAITS_FOR_IT]
    assert _events(rt, "tier0.guide.drafted") == [{"guide": GUIDE_ID}]
    assert _events(rt, "tier0.guide.posted") == []
    assert _comments(rt, ticket)[-2:] == [
        f"tier0: handed to a person ({GUIDE_WAITS_FOR_IT}).",
        f"tier0: the guide picker chose the approved guide {GUIDE_ID}. It waits in #it-team for "
        "one click from a person in IT, who can send it word for word or take the ticket over "
        "(ADR-053). The requester has the hand-off reply only.",
    ]
    assert len(picker.requests) == 1
    assert rt.issuer.issued == ()


# --- a click by a member of IT posts the guide once --------------------------------------------


def test_an_it_members_click_posts_the_guide_once_and_it_closes_like_an_answer(
    world: World,
) -> None:
    person = _requester(world)
    member = _it_member(world)
    guide = BY_ID[GUIDE_ID]

    async def body(rt: Runtime) -> tuple[Runtime, str, str, str, Any]:
        ticket = (await _ask(rt, person)).ticket or ""
        result = _click(rt, ticket, member)
        state = rt.tickets.get_ticket(ticket).state
        rt.clock.advance(timedelta(days=3, minutes=1))
        return rt, ticket, result, state, rt.agent.sweep()

    rt, ticket, result, state, swept = _run(
        world, body, guide_picker=GuidePicker(model=FakePicker(), gate_version=None)
    )
    assert result == "sent"
    assert state == AWAITING_REPLY
    ack, answer, closing = _dm(rt, person)
    # The guide exactly as code builds it, after the hand-off reply, which carried the disclosure.
    assert "AI assistant" in ack
    assert _body(answer) == reply_text(guide, "en")
    assert "AI assistant" not in answer
    assert answer.endswith(guide.link)
    assert _events(rt, "tier0.guide.posted") == [{"guide": GUIDE_ID}]
    assert _comments(rt, ticket)[-2] == (
        f"tier0: answered with the approved guide {GUIDE_ID}: its steps word for word and its "
        f"link, after one click by a person in IT ({member})."
    )
    # Then it closes like an answered request.
    assert [o.action for o in swept] == ["close"]
    assert rt.tickets.get_ticket(ticket).state == DONE
    assert any(note in closing for note in replies.TEMPLATES["auto_close"]["en"])
    assert _clicks(rt) == [("guide_draft.sent", member, "none")]
    # Still no token, no tool call and no lookup beyond binding.
    assert rt.issuer.issued == ()
    assert not any(s.name.startswith("execute_tool") for s in rt.tracer.finished_spans())


def test_a_second_click_posts_nothing(world: World) -> None:
    person = _requester(world)
    team = sorted(review.it_team(world))

    async def body(rt: Runtime) -> tuple[Runtime, list[str], int]:
        ticket = (await _ask(rt, person)).ticket or ""
        results = [_click(rt, ticket, team[0])]
        sent = len(rt.slack.sent_messages)
        results.append(_click(rt, ticket, team[0]))  # the same person again
        results.append(_click(rt, ticket, team[1]))  # a colleague in IT, a moment later
        results.append(_click(rt, ticket, team[1], action=review.TAKE_OVER_ACTION))
        assert len(rt.slack.sent_messages) == sent
        return rt, results, sent

    rt, results, _ = _run(
        world, body, guide_picker=GuidePicker(model=FakePicker(), gate_version=None)
    )
    assert results == ["sent", "already sent", "already sent", "already sent"]
    assert len(_events(rt, "tier0.guide.posted")) == 1
    assert sum(BY_ID[GUIDE_ID].steps[0] in text for text in _dm(rt, person)) == 1
    assert [event for event, _, _ in _clicks(rt)] == [
        "guide_draft.sent",
        "guide_draft.click_repeated",
        "guide_draft.click_repeated",
        "guide_draft.click_repeated",
    ]


# --- anyone else's click is refused and logged ------------------------------------------------


def _clickers(world: World) -> dict[str, str]:
    suspended_it = next(
        p for p in world.people if p.department == review.IT_DEPARTMENT and p.status != "active"
    )
    return {
        "an employee outside IT": next(
            p.slack_user_id
            for p in world.people
            if p.department != review.IT_DEPARTMENT and p.status == "active"
        ),
        "an IT member who is suspended": suspended_it.slack_user_id,
        "a guest from another workspace": world.external_slack_users[0].slack_user_id,
        "a user nobody knows": "U0NOBODY01",
    }


@pytest.mark.parametrize(
    "who",
    [
        "an employee outside IT",
        "an IT member who is suspended",
        "a guest from another workspace",
        "a user nobody knows",
    ],
)
def test_a_click_by_anyone_outside_the_it_team_is_refused_and_logged(
    world: World, who: str
) -> None:
    person = _requester(world)
    clicker = _clickers(world)[who]

    async def body(rt: Runtime) -> tuple[Runtime, list[str], list[str]]:
        ticket = (await _ask(rt, person)).ticket or ""
        before = _dm(rt, person)
        results = [
            _click(rt, ticket, clicker),
            _click(rt, ticket, clicker, action="take_over_guide"),
        ]
        assert _dm(rt, person) == before  # nothing reached the requester
        assert rt.tickets.get_ticket(ticket).state == TRIAGE
        # The draft still waits: someone in IT can still send it.
        results.append(_click(rt, ticket, _it_member(world)))
        return rt, results, before

    rt, results, _ = _run(
        world, body, guide_picker=GuidePicker(model=FakePicker(), gate_version=None)
    )
    assert results == ["refused: not_in_it_team", "refused: not_in_it_team", "sent"]
    assert _clicks(rt)[:2] == [("guide_draft.click_refused", clicker, "not_in_it_team")] * 2


def test_a_click_from_outside_it_team_channel_is_refused(world: World) -> None:
    person = _requester(world)
    member = _it_member(world)

    async def body(rt: Runtime) -> tuple[Runtime, str]:
        ticket = (await _ask(rt, person)).ticket or ""
        dm = rt.slack.dm_channel_for(member)
        return rt, _click(rt, ticket, member, channel=dm)

    rt, result = _run(world, body, guide_picker=GuidePicker(model=FakePicker(), gate_version=None))
    assert result == "refused: wrong_channel"
    assert not _has_steps(_dm(rt, person), BY_ID[GUIDE_ID])
    assert _clicks(rt) == [("guide_draft.click_refused", member, "wrong_channel")]


def test_a_forged_or_unsigned_click_is_rejected(world: World) -> None:
    person = _requester(world)
    member = _it_member(world)

    async def body(rt: Runtime) -> tuple[Runtime, list[str]]:
        ticket = (await _ask(rt, person)).ticket or ""
        real = rt.slack.button_click(
            channel=review.IT_TEAM_CHANNEL,
            user=member,
            action_id=review.SEND_GUIDE_ACTION,
            value=ticket,
        )
        forged = SlackHttpRequest(
            headers={**real.headers, "X-Slack-Signature": "v0=" + "0" * 64}, body=real.body
        )
        unsigned = SlackHttpRequest(headers={}, body=real.body)
        results = [rt.agent.handle_interaction(r) for r in (forged, unsigned)]
        rt.clock.advance(timedelta(minutes=10))
        results.append(rt.agent.handle_interaction(real))  # a replay, too late
        return rt, results

    rt, results = _run(world, body, guide_picker=GuidePicker(model=FakePicker(), gate_version=None))
    assert all(r.startswith("rejected: ") for r in results), results
    assert not _has_steps(_dm(rt, person), BY_ID[GUIDE_ID])
    assert {(event, who) for event, who, _ in _clicks(rt)} == {
        ("guide_draft.click_rejected", "unverified")
    }


def test_a_click_on_a_draft_that_does_not_exist_is_refused(world: World) -> None:
    member = _it_member(world)

    async def body(rt: Runtime) -> tuple[Runtime, str]:
        return rt, _click(rt, "IT-999", member)

    rt, result = _run(world, body, guide_picker=GuidePicker(model=FakePicker(), gate_version=None))
    assert result == "refused: unknown_draft"
    assert rt.slack.sent_messages == ()


# --- taking it over, the kill switch, a ticket already moved on --------------------------------


def test_take_it_over_sends_nothing_and_a_later_send_does_nothing(world: World) -> None:
    person = _requester(world)
    member = _it_member(world)

    async def body(rt: Runtime) -> tuple[Runtime, str, list[str]]:
        ticket = (await _ask(rt, person)).ticket or ""
        results = [_click(rt, ticket, member, action=review.TAKE_OVER_ACTION)]
        results.append(_click(rt, ticket, member))
        return rt, ticket, results

    rt, ticket, results = _run(
        world, body, guide_picker=GuidePicker(model=FakePicker(), gate_version=None)
    )
    assert results == ["taken_over", "already taken_over"]
    assert len(_dm(rt, person)) == 1  # the hand-off reply only
    assert rt.tickets.get_ticket(ticket).state == TRIAGE
    assert _comments(rt, ticket)[-1] == (
        f"tier0: a person in IT ({member}) took the ticket over; the guide {GUIDE_ID} was not "
        "sent, and tier0 sends nothing more on it."
    )
    assert _events(rt, "tier0.guide.taken_over") == [{"guide": GUIDE_ID}]


def test_with_the_kill_switch_on_a_click_sends_nothing_until_it_is_off(world: World) -> None:
    person = _requester(world)
    member = _it_member(world)

    async def body(rt: Runtime) -> tuple[Runtime, list[str]]:
        ticket = (await _ask(rt, person)).ticket or ""
        rt.controls.kill_switch.turn_on(by=IT_LEAD, reason="drill")
        results = [_click(rt, ticket, member)]
        assert len(_dm(rt, person)) == 1
        rt.controls.kill_switch.turn_off(by=IT_LEAD, reason="drill over")
        results.append(_click(rt, ticket, member))
        return rt, results

    rt, results = _run(world, body, guide_picker=GuidePicker(model=FakePicker(), gate_version=None))
    assert results == [f"refused: {KILL_SWITCH_ON}", "sent"]
    assert _clicks(rt) == [
        ("guide_draft.click_refused", _it_member(world), KILL_SWITCH_ON),
        ("guide_draft.sent", _it_member(world), "none"),
    ]


def test_a_late_click_on_a_ticket_a_person_has_moved_on_posts_nothing(world: World) -> None:
    person = _requester(world)
    member = _it_member(world)

    async def body(rt: Runtime) -> tuple[Runtime, list[str]]:
        ticket = (await _ask(rt, person)).ticket or ""
        rt.tickets.set_state(ticket, DONE)  # someone answered it by hand and closed it
        return rt, [_click(rt, ticket, member), _click(rt, ticket, member)]

    rt, results = _run(world, body, guide_picker=GuidePicker(model=FakePicker(), gate_version=None))
    assert results == ["refused: ticket_moved_on", "already closed"]
    assert len(_dm(rt, person)) == 1


def test_a_guide_the_guard_blocks_never_becomes_a_draft(world: World) -> None:
    leaky = dataclasses.replace(
        BY_ID[GUIDE_ID], id="KB-904", steps=("Ask kenji.sato@meridian.example for the room code.",)
    )
    picker = FakePicker(leaky.id)

    async def body(rt: Runtime) -> tuple[Runtime, Outcome]:
        return rt, await _ask(rt, _requester(world))

    rt, outcome = _run(
        world,
        body,
        guide_picker=GuidePicker(model=picker, guides=(*RUNBOOKS, leaky), gate_version=None),
    )
    assert (outcome.action, outcome.state) == ("escalate", TRIAGE)
    assert len(_events(rt, "tier0.guard.blocked")) == 1
    assert _in(rt, CHANNELS["it-team"]) == []
    assert not any("kenji.sato" in m.text for m in rt.slack.sent_messages)


# --- the setting off: as before -----------------------------------------------------------------


def test_with_the_setting_off_the_guide_is_posted_at_once_as_before(world: World) -> None:
    person = _requester(world)
    guide = BY_ID[GUIDE_ID]

    async def body(rt: Runtime) -> tuple[Runtime, Outcome, str]:
        outcome = await _ask(rt, person)
        return rt, outcome, _click(rt, outcome.ticket or "", _it_member(world))

    rt, outcome, click = _run(
        world,
        body,
        guide_picker=GuidePicker(model=FakePicker(), gate_version=None),
        guide_answers_need_it_click=False,
    )
    assert (outcome.action, outcome.state) == (ANSWER_GUIDE, AWAITING_REPLY)
    (answer,) = _dm(rt, person)
    assert "AI assistant" in answer and _body(answer) == reply_text(guide, "en")
    assert _in(rt, CHANNELS["it-team"]) == []
    assert _events(rt, "tier0.escalated") == []
    assert click == "refused: unknown_draft"  # there is no draft to click


def test_every_exempt_link_is_the_picked_guides_own_with_the_click(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    # R-W1 with the default on: the draft check, the draft itself and the send are each guarded
    # with only the picked guide's code-built link exempt, and each message ends with it.
    import tier0.agent as agent_module

    calls: list[tuple[str, tuple[str, ...]]] = []
    real = agent_module.check_reply

    def recording(text: str, **kwargs: Any) -> Any:
        calls.append((text, tuple(kwargs.get("code_links", ()))))
        return real(text, **kwargs)

    monkeypatch.setattr(agent_module, "check_reply", recording)
    link = code_link(BY_ID[GUIDE_ID])

    async def body(rt: Runtime) -> None:
        ticket = (await _ask(rt, _requester(world))).ticket or ""
        _click(rt, ticket, _it_member(world))

    _run(world, body, guide_picker=GuidePicker(model=FakePicker(), gate_version=None))
    exempt = [(text, links) for text, links in calls if links]
    assert len(exempt) == 3
    assert all(links == (link,) and text.endswith(link) for text, links in exempt)
    assert len(calls) - len(exempt) == 1  # the hand-off reply, with nothing exempt


def test_no_message_text_or_address_reaches_the_traces_or_the_audit_log(world: World) -> None:
    person = _requester(world)

    async def body(rt: Runtime) -> Runtime:
        ticket = (await _ask(rt, person)).ticket or ""
        _click(rt, ticket, _it_member(world))
        _click(rt, ticket, person.slack_user_id)
        return rt

    rt = _run(world, body, guide_picker=GuidePicker(model=FakePicker(), gate_version=None))
    dump = json.dumps(
        [
            {
                "attributes": dict(s.attributes or {}),
                "events": [dict(e.attributes or {}) for e in s.events],
            }
            for s in rt.tracer.finished_spans()
        ]
        + [e.as_json() for e in rt.controls.audit.entries],
        default=str,
    )
    assert WIFI not in dump
    assert "@meridian.example" not in dump
    assert person.directory_id not in dump
    assert not any(step in dump for step in BY_ID[GUIDE_ID].steps)
