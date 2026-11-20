"""Milestone 3f's how-to gate in the Slack flow, end to end against the offline mocks.

With a gate set on the `GuidePicker`, a message triage calls "other" goes to the code-only
message screen first (if it finds anything, nothing else is asked), then to the gate on
triage's own model, and only on exactly yes to the picker, as before. Each step can only hand
off, with its own code. The default is G3, the version the 3f dev tuning picked; with no gate
set (`gate_version=None`), the path is exactly as before. Every model here is a fake or the
offline stub, so no test makes a call that costs money. The messages are written in this file;
no pool wording is used.
"""

import json
from collections.abc import Awaitable, Callable
from typing import Any

import anyio
import pytest

from meridian_dataset import World
from meridian_dataset.knowledge import load_runbooks
from meridian_dataset.model import Person, Ticket
from tier0 import replies
from tier0.agent import (
    ANSWER_GUIDE,
    DRAFT_GUIDE,
    GUIDE_GATE_FAILED,
    GUIDE_GATE_NO,
    GUIDE_SCREEN_FIRST,
    GUIDE_WAITS_FOR_IT,
    KILL_SWITCH_ON,
    Features,
    Outcome,
)
from tier0.evals import month
from tier0.knowledge import review, wiring
from tier0.knowledge.answer import reply_text
from tier0.knowledge.gate import GATE_VERSIONS
from tier0.knowledge.screen import ACCOUNT_ACTION, INCIDENT, hand_off_signal
from tier0.knowledge.wiring import GuidePicker
from tier0.mocks.slack import CHANNELS
from tier0.models import ModelError, ModelRequest, ModelResponse, StubModel
from tier0.runtime import Runtime, offline_runtime
from tier0.tickets.adapter import AWAITING_REPLY, TRIAGE

RUNBOOKS = load_runbooks()
BY_ID = {g.id: g for g in RUNBOOKS}
GATE = "G2"
DEFAULT_GATE = "G3"
WIFI = "How do I connect to the office Wi-Fi?"
ACCESS = "I need access to the Sales shared drive please"  # the screen: account_action
LOST = "I lost my laptop on the train yesterday, what do I do now?"  # the screen: incident
GUIDE_ID = "KB-001"
YES = '{"plain_how_to": true}'
NO = '{"plain_how_to": false}'
IT_LEAD = "U0ITLEAD"
_ENGLISH = frozenset({"US", "CA", "GB", "IE", "PL", "NL", "SG", "AU", "IN"})


class TriageAndGate:
    """The agent's own model: triage always says "other"; the gate gets `gate` as its reply,
    with `finish` as the finish reason, or fails. Any other call is a test failure."""

    provider = "test"
    model = "triage-under-test"
    temperature: float | None = None
    seed: int | None = None
    supports_seed = False

    def __init__(self, gate: str = YES, *, finish: str = "stop", fail: bool = False) -> None:
        self.gate, self.finish, self.fail = gate, finish, fail
        self.requests: list[ModelRequest] = []
        self.on_gate: Callable[[], None] | None = None

    def describe(self) -> dict[str, str]:
        return {}

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if request.task == "triage":
            return ModelResponse(
                json.dumps({"category": "other"}), self.model, 300, 8, "stop", "fake-triage"
            )
        assert request.task == "howto_gate", request.task
        if self.on_gate is not None:
            self.on_gate()
        if self.fail:
            raise ModelError("down")
        return ModelResponse(self.gate, self.model, 320, 9, self.finish, "fake-gate")

    def tasks(self) -> list[str]:
        return [r.task for r in self.requests]


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


async def _ask(rt: Runtime, person: Person, text: str) -> Outcome:
    ticket = Ticket(
        id="GATE-1",
        channel=rt.slack.dm_channel_for(person.slack_user_id),
        sender_slack_user_id=person.slack_user_id,
        sender_team_id=rt.world.slack_team.team_id,
        text=text,
        received_at=rt.world.reference_time,
    )
    return await rt.agent.handle(rt.slack.message_event(ticket))


def _ask_once(
    world: World, text: str, model: TriageAndGate, picker: GuidePicker, **options: Any
) -> tuple[Runtime, Outcome]:
    person = _requester(world)

    async def body(rt: Runtime) -> tuple[Runtime, Outcome]:
        return rt, await _ask(rt, person, text)

    return _run(world, body, model=model, guide_picker=picker, **options)


def _events(rt: Runtime, name: str) -> list[dict[str, Any]]:
    return [
        dict(e.attributes or {})
        for span in rt.tracer.finished_spans()
        for e in span.events
        if e.name == name
    ]


def _codes(rt: Runtime) -> list[str]:
    return [str(e["reason"]) for e in _events(rt, "tier0.escalated")]


def _comments(rt: Runtime, outcome: Outcome) -> list[str]:
    return [c.body for c in rt.tickets.get_ticket(outcome.ticket or "").comments]


def _requester_saw_the_hand_off_only(rt: Runtime, world: World) -> bool:
    person = _requester(world)
    dm = rt.slack.dm_channel_for(person.slack_user_id)
    texts = [m.text for m in rt.slack.sent_messages if m.channel == dm]
    return (
        len(texts) == 1
        and texts[0].split("\n\n", 1)[1] in replies.TEMPLATES["handoff"]["en"]
        and not any(step in texts[0] for g in RUNBOOKS for step in g.steps)
    )


def _no_draft(rt: Runtime) -> bool:
    return not [m for m in rt.slack.sent_messages if m.channel == CHANNELS["it-team"]]


# --- the setting ------------------------------------------------------------------------------


def test_the_gate_is_on_by_default_with_g3_from_the_3f_dev_tuning() -> None:
    # The 3f dev tuning picked G3 on both arms; the blind month3 will measure it.
    assert wiring.HOW_TO_GATE_VERSION == DEFAULT_GATE == "G3"
    assert DEFAULT_GATE in GATE_VERSIONS
    assert GuidePicker(model=FakePicker()).gate_version == DEFAULT_GATE
    assert GuidePicker(model=FakePicker(), gate_version=None).gate_version is None
    # The live picker is KS6A-H, the prompt the blind months measure behind the gate
    # (test_live_picker_is_the_months.py pins it to the month runner's call).
    assert wiring.PICKER_VERSION == "KS6A-H"


def test_an_unknown_gate_version_is_refused_and_a_picker_without_a_gate_has_none_to_ask() -> None:
    assert set(GATE_VERSIONS) >= {GATE}
    with pytest.raises(ValueError, match="no how-to gate"):
        GuidePicker(model=FakePicker(), gate_version="G9")
    with pytest.raises(ValueError, match="no how-to gate"):
        GuidePicker(model=FakePicker(), gate_version=None).ask_gate(WIFI, model=TriageAndGate())


def test_the_hand_off_codes_are_distinct_and_named_like_the_other_guide_codes() -> None:
    codes = (GUIDE_SCREEN_FIRST, GUIDE_GATE_NO, GUIDE_GATE_FAILED)
    assert codes == ("guide_screen_first", "guide_gate_no", "guide_gate_failed")
    # "guide_screen" stays the code for the screen firing after a picker call, with no gate.
    assert len({*codes, "guide_screen", GUIDE_WAITS_FOR_IT}) == 5


# --- no gate: exactly as before ---------------------------------------------------------------


@pytest.mark.parametrize(("text", "code"), [(WIFI, GUIDE_WAITS_FOR_IT), (ACCESS, "guide_screen")])
def test_with_no_gate_the_picker_is_asked_as_before_and_no_gate_call_is_made(
    world: World, text: str, code: str
) -> None:
    model, picker = TriageAndGate(), FakePicker()
    rt, outcome = _ask_once(world, text, model, GuidePicker(model=picker, gate_version=None))
    assert model.tasks() == ["triage"]
    assert len(picker.requests) == 1
    assert _codes(rt) == [code]
    assert _events(rt, "tier0.guide.gate") == []
    assert _events(rt, "tier0.guide.screen_first") == []
    assert not any("how-to gate" in c or "was not asked" in c for c in _comments(rt, outcome))


# --- step 1: the code-only message screen, first ----------------------------------------------


@pytest.mark.parametrize(("text", "kind"), [(ACCESS, ACCOUNT_ACTION), (LOST, INCIDENT)])
@pytest.mark.parametrize("features", [Features(), Features(tier_c=True)])
def test_a_screen_hit_hands_off_with_no_gate_call_and_no_picker_call(
    world: World, text: str, kind: str, features: Features
) -> None:
    assert hand_off_signal(text) == kind
    model, picker = TriageAndGate(), FakePicker()
    rt, outcome = _ask_once(
        world, text, model, GuidePicker(model=picker, gate_version=GATE), features=features
    )
    assert (outcome.action, outcome.category, outcome.state) == ("escalate", "other", TRIAGE)
    assert model.tasks() == ["triage"]
    assert picker.requests == []
    assert _codes(rt) == [GUIDE_SCREEN_FIRST]
    assert _events(rt, "tier0.guide.screen_first") == [{"screen": kind}]
    assert _events(rt, "tier0.guide.gate") == []
    assert _events(rt, "tier0.guide.pick") == []
    assert _comments(rt, outcome)[-2:] == [
        f"tier0: message screen {kind}; the guide picker was not asked.",
        f"tier0: handed to a person ({GUIDE_SCREEN_FIRST}).",
    ]
    assert _requester_saw_the_hand_off_only(rt, world)
    assert _no_draft(rt)


# --- step 2: the how-to gate, on triage's own model --------------------------------------------


@pytest.mark.parametrize(
    ("reply", "finish", "fail", "said", "code"),
    [
        (NO, "stop", False, "no", GUIDE_GATE_NO),
        (YES, "stop", True, "failed", GUIDE_GATE_FAILED),  # the call fails
        (YES, "refusal", False, "failed", GUIDE_GATE_FAILED),
        (YES, "max_tokens", False, "failed", GUIDE_GATE_FAILED),  # cut short
        ('{"plain_how_to": "yes"}', "stop", False, "failed", GUIDE_GATE_FAILED),
        ('{"plain_how_to": true, "why": "fine"}', "stop", False, "failed", GUIDE_GATE_FAILED),
        ('{"plain_how_to": 1}', "stop", False, "failed", GUIDE_GATE_FAILED),
        ("true", "stop", False, "failed", GUIDE_GATE_FAILED),
        ("", "stop", False, "failed", GUIDE_GATE_FAILED),
    ],
)
def test_a_gate_that_says_no_fails_or_is_off_the_schema_hands_off_with_no_picker_call(
    world: World, reply: str, finish: str, fail: bool, said: str, code: str
) -> None:
    model, picker = TriageAndGate(reply, finish=finish, fail=fail), FakePicker()
    rt, outcome = _ask_once(world, WIFI, model, GuidePicker(model=picker, gate_version=GATE))
    assert (outcome.action, outcome.category, outcome.state) == ("escalate", "other", TRIAGE)
    assert model.tasks() == ["triage", "howto_gate"]
    assert picker.requests == []
    assert _codes(rt) == [code]
    assert _events(rt, "tier0.guide.gate") == [{"version": GATE, "plain_how_to": said}]
    assert _events(rt, "tier0.guide.pick") == []
    assert _comments(rt, outcome)[-2:] == [
        f"tier0: how-to gate {GATE}: plain how-to {said}.",
        f"tier0: handed to a person ({code}).",
    ]
    assert _requester_saw_the_hand_off_only(rt, world)
    assert _no_draft(rt)


def test_the_gate_asks_the_pinned_prompt_on_triages_model_and_never_sees_a_guide(
    world: World,
) -> None:
    model, picker = TriageAndGate(NO), FakePicker()
    rt, _ = _ask_once(world, WIFI, model, GuidePicker(model=picker, gate_version=GATE))
    (gate,) = [r for r in model.requests if r.task == "howto_gate"]
    assert gate.system == GATE_VERSIONS[GATE] == month.gate_for(GATE).system
    assert WIFI in gate.user and WIFI not in gate.system
    seen = gate.system + gate.user
    assert not any(g.id in seen or g.title in seen for g in RUNBOOKS)
    # Its call is traced like every other model call, on triage's model, never the picker's.
    chats = [s.name for s in rt.tracer.finished_spans() if s.name.startswith("chat ")]
    assert chats == [f"chat {model.model}", f"chat {model.model}"]


def test_a_kill_switch_flipped_during_the_gate_call_stops_before_the_picker(world: World) -> None:
    model, picker = TriageAndGate(YES), FakePicker()
    person = _requester(world)

    async def body(rt: Runtime) -> tuple[Runtime, Outcome]:
        model.on_gate = lambda: rt.controls.kill_switch.turn_on(by=IT_LEAD, reason="drill")
        return rt, await _ask(rt, person, WIFI)

    rt, outcome = _run(
        world, body, model=model, guide_picker=GuidePicker(model=picker, gate_version=GATE)
    )
    assert (outcome.action, outcome.state) == ("escalate", TRIAGE)
    assert _codes(rt) == [KILL_SWITCH_ON]
    assert picker.requests == []
    assert _no_draft(rt)


# --- step 3: only on yes, the picker, then the IT click as today -------------------------------


@pytest.mark.parametrize("features", [Features(), Features(tier_c=True)])
def test_a_gate_yes_asks_the_picker_and_the_guide_waits_for_the_it_click_as_today(
    world: World, features: Features
) -> None:
    model, picker = TriageAndGate(YES), FakePicker()
    rt, outcome = _ask_once(
        world, WIFI, model, GuidePicker(model=picker, gate_version=GATE), features=features
    )
    assert (outcome.action, outcome.category, outcome.state) == (DRAFT_GUIDE, "other", TRIAGE)
    assert model.tasks() == ["triage", "howto_gate"]
    assert len(picker.requests) == 1
    assert _codes(rt) == [GUIDE_WAITS_FOR_IT]
    assert _events(rt, "tier0.guide.gate") == [{"version": GATE, "plain_how_to": "yes"}]
    assert _events(rt, "tier0.guide.drafted") == [{"guide": GUIDE_ID}]
    assert _events(rt, "tier0.guide.posted") == []
    comments = _comments(rt, outcome)
    assert f"tier0: how-to gate {GATE}: plain how-to yes." in comments
    assert comments.index(f"tier0: how-to gate {GATE}: plain how-to yes.") < next(
        i for i, c in enumerate(comments) if c.startswith("tier0: guide picker KS6A-H")
    )
    (draft,) = [m for m in rt.slack.sent_messages if m.channel == CHANNELS["it-team"]]
    guide = BY_ID[GUIDE_ID]
    assert draft.text == review.draft_text(outcome.ticket or "", guide, reply_text(guide, "en"))
    assert _requester_saw_the_hand_off_only(rt, world)


def test_a_gate_yes_with_the_it_click_off_posts_the_guide_as_before(world: World) -> None:
    model, picker = TriageAndGate(YES), FakePicker()
    rt, outcome = _ask_once(
        world,
        WIFI,
        model,
        GuidePicker(model=picker, gate_version=GATE),
        guide_answers_need_it_click=False,
    )
    assert (outcome.action, outcome.state) == (ANSWER_GUIDE, AWAITING_REPLY)
    assert len(picker.requests) == 1
    assert _events(rt, "tier0.guide.posted") == [{"guide": GUIDE_ID}]


# --- the live path and the month runner take the same steps ------------------------------------


@pytest.mark.parametrize(
    ("text", "reply", "fail", "stage", "code"),
    [
        (ACCESS, YES, False, month.SCREEN, GUIDE_SCREEN_FIRST),
        (WIFI, NO, False, month.GATE_NO, GUIDE_GATE_NO),
        (WIFI, "nope", False, month.GATE_FAILED, GUIDE_GATE_FAILED),
        (WIFI, YES, True, month.GATE_FAILED, GUIDE_GATE_FAILED),
        (WIFI, YES, False, None, None),
    ],
)
def test_the_live_path_stops_where_the_month_runner_stops(
    world: World, text: str, reply: str, fail: bool, stage: str | None, code: str | None
) -> None:
    assert month.gate_route(text, TriageAndGate(reply, fail=fail), month.gate_for(GATE)) == stage
    model, picker = TriageAndGate(reply, fail=fail), FakePicker()
    rt, _ = _ask_once(world, text, model, GuidePicker(model=picker, gate_version=GATE))
    assert _codes(rt) == [code if code is not None else GUIDE_WAITS_FOR_IT]
    assert len(picker.requests) == (1 if code is None else 0)


# --- the default: the live path calls the G3 gate -----------------------------------------------


@pytest.mark.parametrize(("reply", "said", "code"), [(YES, "yes", None), (NO, "no", GUIDE_GATE_NO)])
def test_by_default_the_live_path_asks_the_g3_gate_before_the_picker(
    world: World, reply: str, said: str, code: str | None
) -> None:
    model, picker = TriageAndGate(reply), FakePicker()
    rt, outcome = _ask_once(world, WIFI, model, GuidePicker(model=picker))
    assert model.tasks() == ["triage", "howto_gate"]
    (gate,) = [r for r in model.requests if r.task == "howto_gate"]
    assert gate.system == GATE_VERSIONS[DEFAULT_GATE] == month.gate_for(DEFAULT_GATE).system
    assert _events(rt, "tier0.guide.gate") == [{"version": DEFAULT_GATE, "plain_how_to": said}]
    assert f"tier0: how-to gate {DEFAULT_GATE}: plain how-to {said}." in _comments(rt, outcome)
    assert len(picker.requests) == (1 if code is None else 0)
    assert _codes(rt) == [code if code is not None else GUIDE_WAITS_FOR_IT]


def test_offline_on_the_stub_a_how_to_question_still_reaches_the_picker(world: World) -> None:
    """The offline mode's model, the stub, answers the gate too: yes for what its own triage
    calls "other", so a how-to question still gets to the picker with the default gate."""
    picker = FakePicker()
    person = _requester(world)

    async def body(rt: Runtime) -> tuple[Runtime, Outcome]:
        return rt, await _ask(rt, person, WIFI)

    rt, outcome = _run(world, body, guide_picker=GuidePicker(model=picker))
    assert isinstance(rt.model, StubModel)
    assert (outcome.action, outcome.category) == (DRAFT_GUIDE, "other")
    assert _events(rt, "tier0.guide.gate") == [{"version": DEFAULT_GATE, "plain_how_to": "yes"}]
    assert len(picker.requests) == 1
    assert _codes(rt) == [GUIDE_WAITS_FOR_IT]
