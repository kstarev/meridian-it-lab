"""Property-based tests for milestone 3f's how-to gate (hypothesis).

The rule for all of them: a guide is chosen only if the code-only message screen found
nothing, the gate's reply was exactly {"plain_how_to": true} and finished normally, and the
picker picked a guide. Checked three ways:

- `parse_gate` says yes only for exactly that object, with finish "stop", whatever reply text,
  finish reason or JSON shape it gets;
- the month runner's `route_ticket`, with a gate, for any message, triage label, gate reply and
  picker answer;
- the live path, through the agent against the offline mocks, with fake models.

Gap 3F-1, found by these tests and fixed since (tier0/src/tier0/models/reply_json.py): a reply
that named "plain_how_to" twice was read by its last value, so
`{"plain_how_to": false, "plain_how_to": true}` was a yes. The tests named "gap 3F-1" were
strict xfails until then; now they pass.

Every model here is a fake: no model is called and nothing costs money. The messages are made
up here; no pool wording is used and no sealed set is read. The runs are fixed (derandomize)
and keep no example database.
"""

import json
from functools import cache
from typing import Any

import anyio
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from meridian_dataset import DEFAULT_SEED, World, generate
from meridian_dataset.knowledge import load_runbooks
from meridian_dataset.model import Person, Ticket
from tier0.agent import (
    ANSWER_GUIDE,
    DRAFT_GUIDE,
    GUIDE_GATE_FAILED,
    GUIDE_GATE_NO,
    GUIDE_SCREEN_FIRST,
    Features,
    Outcome,
)
from tier0.evals import month as mo
from tier0.evals.setups import ALL_SETUPS
from tier0.knowledge import review
from tier0.knowledge.answer import postable
from tier0.knowledge.gate import GATE_SCHEMA, GATE_VERSIONS, gate_request, parse_gate
from tier0.knowledge.picker import HOW_TO, NO_GUIDE, REQUEST_TYPES
from tier0.knowledge.screen import hand_off_signal
from tier0.knowledge.wiring import GuidePicker
from tier0.mocks.slack import CHANNELS
from tier0.models import ModelError, ModelRequest, ModelResponse
from tier0.resets import reset_request
from tier0.runtime import offline_runtime

SETTINGS = settings(derandomize=True, max_examples=300, database=None, deadline=None)
# The live path builds a whole offline runtime per example (a few milliseconds each).
LIVE = settings(SETTINGS, suppress_health_check=[HealthCheck.too_slow])

KEY = "plain_how_to"
YES = '{"plain_how_to": true}'
NO = '{"plain_how_to": false}'
KEYS = (KEY, "plain_how_to ", "Plain_how_to", "plainHowTo", "how_to", "plain", "x", "")
WORDS = ("true", "false", "True", "yes", "no", "1", "0", "null", KEY)
FINISHES = ("stop", "length", "max_tokens", "refusal", "end_turn", "tool_use", "", "STOP", "stop ")

scalars = (
    st.none()
    | st.booleans()
    | st.integers(-2, 2)
    | st.integers(-(10**6), 10**6)
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.sampled_from(WORDS)
    | st.text(max_size=8)
)
json_values = st.recursive(
    scalars,
    lambda inner: (
        st.lists(inner, max_size=3)
        | st.dictionaries(st.sampled_from(KEYS) | st.text(max_size=5), inner, max_size=3)
    ),
    max_leaves=8,
)
# Objects close to the expected one: the key with any value, and often a key too many.
near_objects = st.builds(
    lambda value, extra: {KEY: value, **extra},
    st.booleans() | json_values,
    st.just({}) | st.dictionaries(st.sampled_from(KEYS[1:]), scalars, min_size=1, max_size=2),
)
exact_objects = st.builds(lambda value: {KEY: value}, st.booleans())
replies = json_values | near_objects | exact_objects
reply_texts = (
    st.builds(
        lambda value, indent: json.dumps(value, indent=indent),
        replies,
        st.none() | st.integers(0, 3),
    )
    | st.text(max_size=40)
    | st.sampled_from((YES, NO, "true", "yes", "", "{}", "[true]"))
)
finishes = st.sampled_from(FINISHES) | st.text(max_size=6)


def _response(text: str, finish: str = "stop") -> ModelResponse:
    return ModelResponse(text, "fake", 100, 10, finish, "r1")


class _Members(list[tuple[str, Any]]):
    """A JSON object's members in the order written, duplicates kept."""


def _exactly(text: str) -> bool | None:
    """The docstring's rule, read from the text: True or False when the reply is exactly one
    object with one member, "plain_how_to", whose value is true or false (any spacing). None
    for anything else, including an object that names the key twice."""
    try:
        top = json.loads(text, object_pairs_hook=_Members)
    except (ValueError, RecursionError):
        return None
    if not isinstance(top, _Members) or len(top) != 1:
        return None
    ((key, value),) = top
    return value if key == KEY and type(value) is bool else None


# --- parse_gate -------------------------------------------------------------------------------


@SETTINGS
@given(text=reply_texts, finish=finishes)
def test_the_gate_says_yes_only_for_exactly_the_true_object_with_finish_stop(
    text: str, finish: str
) -> None:
    verdict = parse_gate(_response(text, finish))
    assert verdict is True or verdict is False or verdict is None
    want = _exactly(text) if finish == "stop" else None
    assert verdict is want


@SETTINGS
@given(value=replies, finish=finishes)
def test_any_json_reply_that_is_not_exactly_one_boolean_under_the_key_is_no_yes(
    value: Any, finish: str
) -> None:
    verdict = parse_gate(_response(json.dumps(value), finish))
    exact = type(value) is dict and list(value) == [KEY] and type(value[KEY]) is bool
    if exact and finish == "stop":
        assert verdict is value[KEY]
    else:
        assert verdict is None


@SETTINGS
@given(
    value=st.booleans(),
    indent=st.none() | st.integers(0, 4),
    pad=st.sampled_from(("", " ", "\n", "\t", " \r\n ")),
)
def test_the_expected_object_parses_whatever_its_spacing(
    value: bool, indent: int | None, pad: str
) -> None:
    text = pad + json.dumps({KEY: value}, indent=indent) + pad
    assert parse_gate(_response(text)) is value


NOISE = ("Sure: ", "```json\n", "\n```", " ok", ",", "}", "[", "true ", "//", "﻿", "\x00")


@SETTINGS
@given(
    value=st.booleans(),
    before=st.sampled_from(("", *NOISE)),
    after=st.sampled_from(("", *NOISE)),
)
def test_text_around_the_expected_object_is_no_yes(value: bool, before: str, after: str) -> None:
    if before or after:
        assert parse_gate(_response(before + json.dumps({KEY: value}) + after)) is None


@SETTINGS
@given(text=st.sampled_from((YES, NO)) | reply_texts, finish=finishes)
def test_a_reply_that_did_not_finish_with_stop_is_no_yes(text: str, finish: str) -> None:
    if finish != "stop":
        assert parse_gate(_response(text, finish)) is None


def test_no_reply_is_no_yes() -> None:
    assert parse_gate(None) is None


UNREADABLE = {
    "5000 digits": '{"plain_how_to": ' + "1" * 5000 + "}",
    "5000 digits, alone": "1" * 5000,
    "100000 deep": "[" * 100_000,
    "100000 deep, in the value": '{"plain_how_to": ' + "{" * 100_000 + "}",
}


@pytest.mark.parametrize("text", UNREADABLE.values(), ids=UNREADABLE)
def test_a_reply_too_big_or_too_deep_to_read_is_no_yes(text: str) -> None:
    assert parse_gate(_response(text)) is None


@SETTINGS
@given(text=st.text(max_size=80), system=st.sampled_from(sorted(GATE_VERSIONS.values())))
def test_the_gate_sees_the_message_only_in_the_user_turn_and_its_frozen_rules(
    text: str, system: str
) -> None:
    request = gate_request(text, system)
    assert (request.task, request.system, request.json_schema) == (
        "howto_gate",
        system,
        GATE_SCHEMA,
    )
    assert request.user == f"Message from the employee:\n<message>\n{text}\n</message>"
    assert request.max_tokens == 1024


# gap 3F-1: json.loads keeps the last of two members with one name, so a reply that said false
# (or anything) and then true was read as a yes. A model held to GATE_SCHEMA is unlikely to send
# one, but the parser's rule is "anything that isn't exactly the object above is no". The gate
# now reads its reply with loads_reply, which refuses a doubled name: the reply is off the
# schema (None), so the message goes to a person.
DOUBLED = {
    "false, then true": '{"plain_how_to": false, "plain_how_to": true}',
    "null, then true": '{"plain_how_to": null, "plain_how_to": true}',
    "a word, then true": '{"plain_how_to": "no", "plain_how_to": true}',
}


@pytest.mark.parametrize("text", DOUBLED.values(), ids=DOUBLED)
def test_gap_3f_1_a_reply_that_names_the_key_twice_is_no_yes(text: str) -> None:
    assert parse_gate(_response(text)) is None
    assert _exactly(text) is None


# --- the month runner: route_ticket with a gate -----------------------------------------------

SCREENED = (
    "I need access to the Sales shared drive please",
    "I lost my laptop on the train yesterday, what do I do now?",
    "can you reset my password",
)
PLAIN = (
    "How do I connect to the office Wi-Fi?",
    "how do I add the printer on my mac",
    "where do I return an old monitor",
)
MESSAGES = SCREENED + PLAIN
messages = (
    st.sampled_from(MESSAGES)
    | st.text(max_size=60)
    | st.builds(lambda m, extra: f"{m} {extra}", st.sampled_from(MESSAGES), st.text(max_size=20))
)
LABELS = ("other",) * 5 + ("own_username", "about_someone_else", "off_format", "fail")
GUIDE_IDS = ("KB-001", "KB-017", "", None)
# Weighted so that every branch is reached often: a third of the picker's answers name a guide,
# and a third of the gate's replies are a plain yes (text, finish, whether the call fails).
picker_answers = (
    st.just((mo.GUIDE, "KB-001"))
    | st.just((mo.GUIDE, "KB-017"))
    | st.tuples(st.sampled_from((mo.GUIDE, mo.STAGE_HAND_OFF, "other")), st.sampled_from(GUIDE_IDS))
)
gate_replies = (
    st.just((YES, "stop", False))
    | st.just((NO, "stop", False))
    | st.tuples(
        reply_texts,
        st.sampled_from(("stop",) * 6 + FINISHES) | finishes,
        st.sampled_from((False, False, False, True)),
    )
)
ARMS = ("haiku-3d2", "sonnet-3d2", "haiku-3d", "sonnet-3d")


class Arm:
    """The arm's own model: triage says `label` (or fails, or is off the schema), the gate gets
    `text` with `finish` (or its call fails), and the own-account check says yes."""

    provider = "fake"
    model = "fake-arm"
    temperature = None
    seed = None
    supports_seed = False

    def __init__(self, label: str, gate: tuple[str, str, bool]) -> None:
        self.label = label
        self.text, self.finish, self.fail = gate
        self.tasks: list[str] = []

    def describe(self) -> dict[str, str]:
        return {}

    def gate_response(self) -> ModelResponse | None:
        """What the gate's call gave: None when it failed."""
        return None if self.fail else _response(self.text, self.finish)

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.tasks.append(request.task)
        if request.task == "triage":
            if self.label == "fail":
                raise ModelError("down")
            if self.label == "off_format":
                return _response("other")
            return _response(json.dumps({"category": self.label}))
        if request.task == "howto_gate":
            response = self.gate_response()
            if response is None:
                raise ModelError("down")
            return response
        return _response(json.dumps({"own_account_only": True}))


class Picker:
    """The second stage: records each message it is asked about and gives one fixed answer."""

    def __init__(self, answer: tuple[str, str | None]) -> None:
        self.answer = answer
        self.asked: list[str] = []

    def __call__(self, text: str) -> tuple[str, str | None]:
        self.asked.append(text)
        return self.answer


def _verdict(model: Arm) -> bool | None:
    return parse_gate(model.gate_response())


@SETTINGS
@given(
    text=messages,
    label=st.sampled_from(LABELS),
    gate=gate_replies,
    answer=picker_answers,
    arm=st.sampled_from(ARMS),
    version=st.sampled_from(sorted(GATE_VERSIONS)),
)
def test_the_month_runner_answers_with_a_guide_only_when_screen_gate_and_picker_all_let_it(
    text: str,
    label: str,
    gate: tuple[str, str, bool],
    answer: tuple[str, str | None],
    arm: str,
    version: str,
) -> None:
    model, picker = Arm(label, gate), Picker(answer)
    route = mo.route_ticket(
        text,
        sender_name="Ana Smith",
        sender_email="ana.smith@meridian.example",
        model=model,
        setup=ALL_SETUPS[arm],
        second_stage=picker,
        gate=mo.gate_for(version),
    )
    screen = hand_off_signal(text)
    if route.outcome == mo.ANSWER_GUIDE:
        kind, guide = answer
        assert screen is None and _verdict(model) is True
        assert picker.asked == [text] and kind == mo.GUIDE and guide
        assert route == mo.Route(mo.ANSWER_GUIDE, guide, mo.PICKER_GUIDE, "other")
    else:
        # A route that answers no guide carries none.
        assert route.guide is None
    # The gate is asked only when the screen found nothing, the picker only on the gate's yes.
    if "howto_gate" in model.tasks:
        assert screen is None and model.tasks == ["triage", "howto_gate"]
    if picker.asked:
        assert "howto_gate" in model.tasks and _verdict(model) is True


@SETTINGS
@given(
    text=messages,
    gate=gate_replies,
    answer=picker_answers,
    arm=st.sampled_from(ARMS),
    version=st.sampled_from(sorted(GATE_VERSIONS)),
)
def test_the_month_runner_stops_at_the_screen_then_at_the_gate_with_its_own_stage(
    text: str,
    gate: tuple[str, str, bool],
    answer: tuple[str, str | None],
    arm: str,
    version: str,
) -> None:
    setup = ALL_SETUPS[arm]
    model, picker = Arm("other", gate), Picker(answer)
    route = mo.route_ticket(
        text,
        sender_name="Ana Smith",
        sender_email="ana.smith@meridian.example",
        model=model,
        setup=setup,
        second_stage=picker,
        gate=mo.gate_for(version),
    )
    assert mo.gate_route(text, Arm("other", gate), mo.gate_for(version)) == (
        mo.SCREEN
        if hand_off_signal(text) is not None
        else {None: mo.GATE_FAILED, False: mo.GATE_NO, True: None}[_verdict(model)]
    )
    if setup.tier_c and reset_request(text):
        assert route == mo.Route(mo.HAND_OFF, None, mo.RESET_SIGNAL, "other")
        return
    if hand_off_signal(text) is not None:
        assert route == mo.Route(mo.HAND_OFF, None, mo.SCREEN, "other")
        assert model.tasks == ["triage"] and picker.asked == []
        return
    verdict = _verdict(model)
    assert model.tasks == ["triage", "howto_gate"]
    if verdict is not True:
        stage = mo.GATE_FAILED if verdict is None else mo.GATE_NO
        assert route == mo.Route(mo.HAND_OFF, None, stage, "other")
        assert picker.asked == []
        return
    assert picker.asked == [text]
    kind, guide = answer
    if kind == mo.GUIDE and guide:
        assert route == mo.Route(mo.ANSWER_GUIDE, guide, mo.PICKER_GUIDE, "other")
    else:
        assert route == mo.Route(mo.HAND_OFF, None, mo.PICKER_HAND_OFF, "other")


# --- the live path: the agent against the offline mocks ----------------------------------------

RUNBOOKS = tuple(load_runbooks())
POSTABLE = tuple(g.id for g in postable(RUNBOOKS))
STEPS = tuple(step for g in RUNBOOKS for step in g.steps)
_ENGLISH = frozenset({"US", "CA", "GB", "IE", "PL", "NL", "SG", "AU", "IN"})
LIVE_LABELS = ("other",) * 4 + ("about_someone_else", "off_format", "fail")
# A third of the picker's replies are an exact how-to pick of a postable guide.
picker_replies = st.tuples(
    st.sampled_from(POSTABLE[:3]).map(lambda g: json.dumps({"request_type": HOW_TO, "guide": g})),
    st.just("stop"),
) | st.tuples(
    st.builds(
        lambda request_type, guide: json.dumps({"request_type": request_type, "guide": guide}),
        st.sampled_from(REQUEST_TYPES),
        st.sampled_from((*POSTABLE[:3], NO_GUIDE, "KB-999")),
    )
    | reply_texts,
    st.sampled_from(("stop", "stop", "stop", "length", "refusal")),
)


class Triage(Arm):
    """The agent's own model, as in the month: triage and the gate. Nothing else may ask it."""

    provider = "test"
    model = "triage-under-test"

    def complete(self, request: ModelRequest) -> ModelResponse:
        assert request.task in ("triage", "howto_gate"), request.task
        return super().complete(request)


class PickerModel:
    """The picker's model: one fixed reply to every picker call."""

    provider = "fake"
    model = "fake-picker"
    temperature = None
    seed = None
    supports_seed = False

    def __init__(self, reply: tuple[str, str]) -> None:
        self.text, self.finish = reply
        self.requests: list[ModelRequest] = []

    def describe(self) -> dict[str, str]:
        return {}

    def complete(self, request: ModelRequest) -> ModelResponse:
        assert request.task == "guide_pick", request.task
        self.requests.append(request)
        return _response(self.text, self.finish)


@cache
def _world() -> World:
    return generate(DEFAULT_SEED)


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


class Seen:
    """What one message did: the outcome, the hand-off codes and every Slack message sent."""

    def __init__(self, outcome: Outcome, codes: list[str], sent: list[tuple[str, str]]) -> None:
        self.outcome, self.codes, self.sent = outcome, codes, sent


def _ask(text: str, model: Triage, picker: GuidePicker, **options: Any) -> tuple[Seen, str]:
    world = _world()
    person = _requester(world)

    async def main() -> tuple[Seen, str]:
        with anyio.fail_after(30):
            async with offline_runtime(world, model=model, guide_picker=picker, **options) as rt:
                dm = rt.slack.dm_channel_for(person.slack_user_id)
                ticket = Ticket(
                    id="GATE-P",
                    channel=dm,
                    sender_slack_user_id=person.slack_user_id,
                    sender_team_id=rt.world.slack_team.team_id,
                    text=text,
                    received_at=rt.world.reference_time,
                )
                outcome = await rt.agent.handle(rt.slack.message_event(ticket))
                codes = [
                    str(dict(e.attributes or {})["reason"])
                    for span in rt.tracer.finished_spans()
                    for e in span.events
                    if e.name == "tier0.escalated"
                ]
                sent = [(m.channel, m.text) for m in rt.slack.sent_messages]
                return Seen(outcome, codes, sent), dm

    return anyio.run(main)


def _picked(reply: tuple[str, str]) -> str | None:
    """The postable guide the picker's reply names as a how-to answer, exactly; else None.
    A reply that names a key twice names none."""
    text, finish = reply
    try:
        members = json.loads(text, object_pairs_hook=_Members)
    except ValueError:
        return None
    if finish != "stop" or not isinstance(members, _Members):
        return None
    if sorted(key for key, _ in members) != ["guide", "request_type"]:
        return None
    said = dict(members)
    if said["request_type"] != HOW_TO or said["guide"] not in POSTABLE:
        return None
    return str(said["guide"])


def _guide_reached_anyone(seen: Seen, dm: str) -> bool:
    """A draft in #it-team, or a guide's steps in the requester's DM."""
    return any(
        channel == CHANNELS["it-team"] or (channel == dm and any(s in text for s in STEPS))
        for channel, text in seen.sent
    )


@LIVE
@given(
    text=messages,
    label=st.sampled_from(LIVE_LABELS),
    gate=gate_replies,
    reply=picker_replies,
    version=st.sampled_from(sorted(GATE_VERSIONS)),
    tier_c=st.booleans(),
    click=st.booleans(),
)
def test_the_live_path_drafts_or_posts_a_guide_only_when_screen_gate_and_picker_all_let_it(
    text: str,
    label: str,
    gate: tuple[str, str, bool],
    reply: tuple[str, str],
    version: str,
    tier_c: bool,
    click: bool,
) -> None:
    model, picker_model = Triage(label, gate), PickerModel(reply)
    seen, dm = _ask(
        text,
        model,
        GuidePicker(model=picker_model, guides=RUNBOOKS, gate_version=version),
        features=Features(tier_c=tier_c),
        guide_answers_need_it_click=click,
    )
    screen, verdict = hand_off_signal(text), _verdict(model)
    # Only the mocks are reached: the requester's DM and #it-team.
    assert {channel for channel, _ in seen.sent} <= {dm, CHANNELS["it-team"]}
    reached = _guide_reached_anyone(seen, dm)
    if reached or seen.outcome.action in (DRAFT_GUIDE, ANSWER_GUIDE):
        assert label == "other" and not (tier_c and reset_request(text))
        assert screen is None and verdict is True
        assert model.tasks == ["triage", "howto_gate"] and len(picker_model.requests) == 1
        assert _picked(reply) is not None
        assert seen.outcome.action == (DRAFT_GUIDE if click else ANSWER_GUIDE)
    # The gate is asked only when the screen found nothing; the picker only on the gate's yes.
    if "howto_gate" in model.tasks:
        assert screen is None and model.tasks == ["triage", "howto_gate"]
    if picker_model.requests:
        assert "howto_gate" in model.tasks and verdict is True
    # Where it stops, with triage's own "other": the screen, then the gate, each with its code.
    if label == "other" and not (tier_c and reset_request(text)):
        if screen is not None:
            assert seen.codes == [GUIDE_SCREEN_FIRST] and model.tasks == ["triage"]
        elif verdict is not True:
            assert seen.codes == [GUIDE_GATE_FAILED if verdict is None else GUIDE_GATE_NO]
            assert picker_model.requests == []
        if screen is not None or verdict is not True:
            assert not reached and seen.outcome.action == "escalate"


@pytest.mark.parametrize("text", DOUBLED.values(), ids=DOUBLED)
def test_gap_3f_1_a_gate_reply_that_says_false_and_then_true_never_reaches_the_picker(
    text: str,
) -> None:
    model = Triage("other", (text, "stop", False))
    picker_model = PickerModel((json.dumps({"request_type": HOW_TO, "guide": POSTABLE[0]}), "stop"))
    seen, dm = _ask(
        PLAIN[0], model, GuidePicker(model=picker_model, guides=RUNBOOKS, gate_version="G3")
    )
    assert picker_model.requests == []
    assert not _guide_reached_anyone(seen, dm)
    # Off the schema, so the gate failed: the code is guide_gate_failed, not guide_gate_no.
    assert seen.codes == [GUIDE_GATE_FAILED] and seen.outcome.action == "escalate"


@pytest.mark.parametrize("text", DOUBLED.values(), ids=DOUBLED)
def test_gap_3f_1_the_month_runner_hands_a_doubled_gate_reply_off_as_a_failed_gate(
    text: str,
) -> None:
    model, picker = Arm("other", (text, "stop", False)), Picker((mo.GUIDE, "KB-001"))
    route = mo.route_ticket(
        PLAIN[0],
        sender_name="Ana Smith",
        sender_email="ana.smith@meridian.example",
        model=model,
        setup=ALL_SETUPS["haiku-3d2"],
        second_stage=picker,
        gate=mo.gate_for("G3"),
    )
    assert route == mo.Route(mo.HAND_OFF, None, mo.GATE_FAILED, "other")
    assert picker.asked == []


def test_the_made_up_messages_are_what_the_tests_say_they_are() -> None:
    assert all(hand_off_signal(m) is not None for m in SCREENED)
    assert all(hand_off_signal(m) is None for m in PLAIN)
    assert not any(reset_request(m) for m in PLAIN)
    assert len(POSTABLE) >= 3
    for guide, want in ((POSTABLE[0], POSTABLE[0]), ("KB-999", None), (NO_GUIDE, None)):
        assert _picked((json.dumps({"request_type": HOW_TO, "guide": guide}), "stop")) == want
