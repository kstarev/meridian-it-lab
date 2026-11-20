"""The one reader of model replies (tier0.models.reply_json) and every parser that uses it.

`json.loads` keeps the last of two members with one name. Every tier0 parser of a model's reply
read it that way until gap 3F-1 (docs/evals/2026-11-18-safety-mutation-testing.md), so
`{"plain_how_to": false, "plain_how_to": true}` was a yes and
`{"category": "about_someone_else", "category": "other"}` was "other". They now read it with
`loads_reply`, which refuses a doubled name. Checked here:

- `loads_reply` reads every text exactly as `json.loads` does, value and types, unless an object
  in it names a key twice; then it raises `DuplicateKeyError`, a `json.JSONDecodeError`;
- each parser (triage, the own-account check, the gate, the picker with and without
  `asks_for`, the fit check, the guide draft) gives the same result as before, with the old
  reader put back, for any reply with no doubled name, whatever its finish reason;
- for a reply with a doubled name, each parser fails closed as for any reply off its schema,
  and the month runner hands the message to a person;
- for a reply that can't be read safely (nested too deep, a number too long to convert, a text
  that isn't a str), each parser fails closed the same way, and nothing raises: not the live
  agent's `handle`, not the month runner, not the guide draft. Triage, the own-account check and
  the guide draft raised on some of these before (branch tier0-parsers-fail-closed). For any
  reply they read without raising, they give what they gave before, with the old catch put
  back.

Every reply is made up here and every model is a fake: nothing is called and nothing costs
money. No pool wording is used and no sealed set is read. The runs are fixed (derandomize) and
keep no example database.
"""

import copy
import dataclasses
import inspect
import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from types import ModuleType
from typing import Any
from unittest.mock import patch

import anyio
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from meridian_dataset import World
from meridian_dataset.knowledge import load_runbooks
from meridian_dataset.month import TUNING_MONTH, month_for
from tier0 import agent, replies
from tier0.agent import (
    check_off_format,
    parse_own_account_check,
    parse_triage,
    triage_given,
    triage_off_format,
)
from tier0.clock import parse_time
from tier0.evals import month as mo
from tier0.evals.setups import ALL_SETUPS
from tier0.knowledge import drafting, gate, picker
from tier0.knowledge.answer import postable
from tier0.knowledge.drafting import parse_draft
from tier0.knowledge.gate import parse_gate
from tier0.knowledge.picker import REQUEST_TYPES, Entry, Pick, parse_fit, parse_pick
from tier0.knowledge.prompts import ASKS_FOR
from tier0.models import CATEGORIES, ModelError, ModelRequest, ModelResponse, StubModel
from tier0.models.reply_json import READ_ERRORS, DuplicateKeyError, loads_reply
from tier0.resets import reset_request
from tier0.roles import role_reference
from tier0.runtime import offline_runtime
from tier0.tickets.adapter import TRIAGE

SETTINGS = settings(derandomize=True, max_examples=300, database=None, deadline=None)

LISTED = ("KB-001", "KB-002", "KB-014")
ENTRIES = tuple(Entry(i, f"title of {i}", f"summary of {i}") for i in LISTED)
OFF = Pick("other", None, off_format=True)


def _response(text: str, finish: str = "stop") -> ModelResponse:
    return ModelResponse(text, "fake", 10, 5, finish, None)


class _Doubled(ValueError):
    """Raised by the test's own reading (below) for an object that names a key twice."""


def _no_doubled(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    names = [name for name, _ in pairs]
    if len(set(names)) != len(names):
        raise _Doubled(names)
    return dict(pairs)


def _names_a_key_twice(text: str) -> bool:
    """Whether an object in the text, as far as `json.loads` reads it, names a key twice.
    Written apart from loads_reply, so the tests don't check the code against itself."""
    try:
        json.loads(text, object_pairs_hook=_no_doubled)
    except _Doubled:
        return True
    except (ValueError, RecursionError):
        return False
    return False


def _same(a: Any, b: Any) -> bool:
    """Equal and of the same types all the way down (1, 1.0 and True are equal in Python)."""
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return list(a) == list(b) and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, float) and a != a:  # NaN, from a reply's bare NaN
        return b != b
    return bool(a == b)


# --- what replies look like ---------------------------------------------------------------------

KEYS = (
    "category",
    "own_account_only",
    "plain_how_to",
    "request_type",
    "guide",
    "asks_for",
    "fits",
    "title",
    "summary",
    "steps",
    "tags",
    "Category",
    "guide ",
    "x",
    "",
)
WORDS = (*CATEGORIES, *REQUEST_TYPES, *LISTED, *ASKS_FOR, "none", "yes", "no", "KB-999", "true")
scalars = (
    st.none()
    | st.booleans()
    | st.integers(-(10**6), 10**6)
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.sampled_from(WORDS)
    | st.text(max_size=12)
)
json_values = st.recursive(
    scalars,
    lambda inner: (
        st.lists(inner, max_size=4)
        | st.dictionaries(st.sampled_from(KEYS) | st.text(max_size=6), inner, max_size=4)
    ),
    max_leaves=10,
)
draft_steps = st.lists(
    st.sampled_from(("Open Slack.", "Type /remind.", "Press Enter.")), min_size=3, max_size=9
)
# Each parser's own object, on its schema (a draft's tag can still be off), so that every
# parser's answers are reached often.
OWN: dict[str, st.SearchStrategy[dict[str, Any]]] = {
    "triage": st.builds(lambda c: {"category": c}, st.sampled_from(CATEGORIES)),
    "own-account check": st.builds(lambda b: {"own_account_only": b}, st.booleans()),
    "gate": st.builds(lambda b: {"plain_how_to": b}, st.booleans()),
    "picker": st.builds(
        lambda r, g: {"request_type": r, "guide": g},
        st.sampled_from(REQUEST_TYPES),
        st.sampled_from((*LISTED, "none")),
    ),
    "picker, asks_for": st.builds(
        lambda a, r, g: {"asks_for": a, "request_type": r, "guide": g},
        st.sampled_from(ASKS_FOR),
        st.sampled_from(REQUEST_TYPES),
        st.sampled_from((*LISTED, "none")),
    ),
    "fit check": st.builds(lambda f: {"fits": f}, st.sampled_from(("yes", "no"))),
    "guide draft": st.builds(
        lambda steps, tags: {
            "title": "Schedule in Slack",
            "summary": "Set reminders in Slack.",
            "steps": steps,
            "tags": tags,
        },
        draft_steps,
        st.lists(st.sampled_from(("slack", "reminders", "Not A Tag")), min_size=1, max_size=5),
    ),
}
on_schema = st.one_of(*OWN.values())
# Near objects: a schema's object with one member changed, added or taken out.
near_schema = st.builds(
    lambda value, extra, drop: {
        **{k: v for i, (k, v) in enumerate(value.items()) if i != drop},
        **extra,
    },
    on_schema,
    st.just({}) | st.dictionaries(st.sampled_from(KEYS), scalars, min_size=1, max_size=2),
    st.integers(-1, 3),
)
values = json_values | on_schema | near_schema
well_formed = st.builds(
    lambda value, indent, compact, ascii_only: json.dumps(
        value,
        indent=indent,
        separators=(",", ":") if compact else None,
        ensure_ascii=ascii_only,
    ),
    values,
    st.none() | st.integers(0, 3),
    st.booleans(),
    st.booleans(),
)
# Any text, too: most of it isn't JSON, and both readers must then refuse it alike.
any_text = well_formed | st.text(max_size=40) | st.sampled_from(('{"a": NaN}', "[Infinity]", " 1 "))
FINISHES = ("stop", "stop", "stop", "length", "refusal", "end_turn")


def _with_a_doubled_key(value: dict[str, Any], key: str, other: Any, last: bool) -> str:
    """The object as JSON with `key` named once more, with the value `other`: before its own
    member (so the old reader kept the object's own value) or after it (it kept `other`)."""
    members = [f"{json.dumps(k)}: {json.dumps(v)}" for k, v in value.items()]
    extra = f"{json.dumps(key)}: {json.dumps(other)}"
    return "{" + ", ".join([*members, extra] if last else [extra, *members]) + "}"


doubled = st.builds(
    lambda value, pick, other, last, wrap: wrap(
        _with_a_doubled_key(value, sorted(value)[pick % len(value)], other, last)
    ),
    on_schema,
    st.integers(0, 3),
    scalars,
    st.booleans(),
    st.sampled_from(
        (
            lambda t: t,
            lambda t: f" {t}\n",
            lambda t: f"[{t}]",
            lambda t: f'{{"reply": {t}}}',
            lambda t: f'{{"category": "other", "x": [1, {t}]}}',
        )
    ),
)


# --- loads_reply ------------------------------------------------------------------------------


@SETTINGS
@given(text=any_text)
def test_loads_reply_reads_any_text_with_no_doubled_name_exactly_as_json_loads(text: str) -> None:
    try:
        old: Any = json.loads(text)
    except (ValueError, RecursionError) as error:
        with pytest.raises(type(error)):
            loads_reply(text)
        return
    if _names_a_key_twice(text):
        with pytest.raises(DuplicateKeyError):
            loads_reply(text)
    else:
        assert _same(loads_reply(text), old)


@SETTINGS
@given(text=doubled)
def test_loads_reply_refuses_any_object_that_names_a_key_twice(text: str) -> None:
    assert _names_a_key_twice(text)
    json.loads(text)  # the old reader read it, by the last value
    with pytest.raises(DuplicateKeyError):
        loads_reply(text)


DOUBLED_CASES = {
    "gate, false then true": '{"plain_how_to": false, "plain_how_to": true}',
    "the same value twice": '{"plain_how_to": true, "plain_how_to": true}',
    "an escaped name": '{"plain_how_to": false, "plain_how_\\u0074o": true}',
    "in a nested object": '{"guide": "KB-001", "request_type": {"a": 1, "a": 2}}',
    "in a list": '[{"fits": "no", "fits": "yes"}]',
    "an empty name twice": '{"": 1, "": 2}',
}


@pytest.mark.parametrize("text", DOUBLED_CASES.values(), ids=DOUBLED_CASES)
def test_a_doubled_name_is_a_json_decode_error(text: str) -> None:
    with pytest.raises(DuplicateKeyError) as caught:
        loads_reply(text)
    # A JSONDecodeError, so a ValueError: what every parser already catches.
    assert isinstance(caught.value, json.JSONDecodeError)
    assert isinstance(caught.value, ValueError)


def test_the_error_names_the_doubled_key_and_can_be_copied() -> None:
    with pytest.raises(DuplicateKeyError) as caught:
        loads_reply('{"guide": "KB-001", "request_type": "how_to", "guide": "none"}')
    assert caught.value.key == "guide" and "'guide'" in str(caught.value)
    # A copy (and a pickle) rebuilds the error from its key, not from JSONDecodeError's three.
    again = copy.deepcopy(caught.value)
    assert type(again) is DuplicateKeyError and again.key == "guide"


NOT_DOUBLED = {
    "two cases of one name": '{"guide": "KB-001", "Guide": "KB-002"}',
    "a space after the name": '{"guide": "KB-001", "guide ": "KB-002"}',
    "one name in two objects": '[{"fits": "yes"}, {"fits": "no"}]',
    "a name inside a value": '{"guide": "guide", "request_type": "how_to"}',
}


@pytest.mark.parametrize("text", NOT_DOUBLED.values(), ids=NOT_DOUBLED)
def test_names_that_only_look_alike_are_not_doubled(text: str) -> None:
    assert _same(loads_reply(text), json.loads(text))


# --- every parser: as before for any reply with no doubled name, closed for one with -------------

type Read = Callable[[ModelResponse], Any]
# (the module whose reader is put back, what the parser gives for a reply)
PARSERS: dict[str, tuple[ModuleType, Read]] = {
    "triage": (agent, lambda r: (parse_triage(r), triage_given(r), triage_off_format(r))),
    "own-account check": (agent, lambda r: (parse_own_account_check(r), check_off_format(r))),
    "gate": (gate, parse_gate),
    "picker": (picker, lambda r: parse_pick(r, ENTRIES)),
    "picker, asks_for": (picker, lambda r: parse_pick(r, ENTRIES, asks=True)),
    "fit check": (picker, parse_fit),
    "guide draft": (drafting, parse_draft),
}
# What each parser gives a reply off its schema, with a stop: the fail-closed answer.
CLOSED = {
    "triage": ("other", False, True),  # "other", not really given, off format: a person
    "own-account check": (None, True),  # failed, off format: a person
    "gate": None,  # off the schema: a person
    "picker": OFF,  # off format: a person
    "picker, asks_for": OFF,
    "fit check": None,  # no answer: a person
    "guide draft": None,  # no draft: nothing is proposed
}


def _off_the_schema(read: Read, finish: str) -> Any:
    """What the parser gives `{}` (an object with none of its keys) with that finish."""
    return read(_response("{}", finish))


def _with_the_old_reader(module: ModuleType, read: Read, response: ModelResponse) -> Any:
    """What the parser gave before: the same code, with json.loads as its reader."""
    with patch.object(module, "loads_reply", json.loads):
        return read(response)


# Where each parser reads the reply's text.
READERS = (
    agent._triage_value,
    agent.parse_own_account_check,
    gate.parse_gate,
    picker._loads,
    drafting.parse_draft,
)


def test_every_parser_of_model_replies_reads_with_loads_reply() -> None:
    for function in READERS:
        source = inspect.getsource(function)
        assert "loads_reply(" in source and "json.loads" not in source, function.__name__
    for module, _ in PARSERS.values():
        assert module.loads_reply is loads_reply


@pytest.mark.parametrize("name", sorted(PARSERS))
def test_the_fail_closed_answers_are_what_each_parser_gives_off_its_schema(name: str) -> None:
    _, read = PARSERS[name]
    assert _off_the_schema(read, "stop") == CLOSED[name]
    for text in ("not json", "[]", '{"x": 1}'):
        assert read(_response(text)) == CLOSED[name]


@SETTINGS
@given(text=any_text, finish=st.sampled_from(FINISHES))
def test_for_any_reply_with_no_doubled_name_each_parser_gives_what_it_gave_before(
    text: str, finish: str
) -> None:
    if _names_a_key_twice(text):
        return  # a case for the tests below
    response = _response(text, finish)
    for name, (module, read) in PARSERS.items():
        assert read(response) == _with_the_old_reader(module, read, response), name


@pytest.mark.parametrize("own", sorted(OWN))
@SETTINGS
@given(data=st.data(), finish=st.sampled_from(FINISHES))
def test_each_parsers_own_object_reads_as_before_whatever_its_spacing(
    own: str, data: st.DataObject, finish: str
) -> None:
    value = data.draw(OWN[own])
    for text in (json.dumps(value), json.dumps(value, indent=2), f" {json.dumps(value)}\n"):
        response = _response(text, finish)
        for name, (module, read) in PARSERS.items():
            assert read(response) == _with_the_old_reader(module, read, response), name
    # Its own parser gives an answer, not the fail-closed one, for a stop and a good draft tag.
    _, read = PARSERS[own]
    if finish == "stop" and "Not A Tag" not in value.get("tags", ()):
        assert read(_response(json.dumps(value))) != CLOSED[own]


@SETTINGS
@given(text=doubled, finish=st.sampled_from(FINISHES))
def test_a_reply_that_names_a_key_twice_is_off_the_schema_for_every_parser(
    text: str, finish: str
) -> None:
    response = _response(text, finish)
    for name, (_, read) in PARSERS.items():
        assert read(response) == _off_the_schema(read, finish), name


# The cases the gap was found with, and their kin in each parser: the old reader took the last
# value, which is on the schema, so each was read as that answer.
DOUBLED_REPLIES = {
    "triage": (
        '{"category": "about_someone_else", "category": "other"}',
        '{"category": "about_someone_else", "category": "own_username"}',
        '{"category": "other", "category": "other"}',
    ),
    "own-account check": (
        '{"own_account_only": false, "own_account_only": true}',
        '{"own_account_only": null, "own_account_only": true}',
    ),
    "gate": (
        '{"plain_how_to": false, "plain_how_to": true}',
        '{"plain_how_to": "no", "plain_how_to": true}',
    ),
    "picker": (
        '{"request_type": "incident", "request_type": "how_to", "guide": "KB-001"}',
        '{"request_type": "how_to", "guide": "none", "guide": "KB-001"}',
        '{"request_type": "how_to", "guide": "KB-001", "guide": "KB-001"}',
    ),
    "picker, asks_for": (
        '{"asks_for": "person", "asks_for": "steps", "request_type": "how_to", "guide": "KB-001"}',
    ),
    "fit check": ('{"fits": "no", "fits": "yes"}',),
    "guide draft": (
        '{"title": "Schedule in Slack", "title": "Schedule in Slack", '
        '"summary": "Set reminders in Slack.", '
        '"steps": ["Open Slack.", "Type /remind.", "Press Enter."], "tags": ["slack"]}',
    ),
}
DOUBLED_PAIRS = [(name, text) for name, texts in DOUBLED_REPLIES.items() for text in texts]


@pytest.mark.parametrize(("name", "text"), DOUBLED_PAIRS)
def test_each_parser_refuses_the_doubled_replies_the_old_reader_read_as_an_answer(
    name: str, text: str
) -> None:
    module, read = PARSERS[name]
    response = _response(text)
    assert read(response) == CLOSED[name]
    # The old reader gave an answer on the schema: these cases are the gap, not already closed.
    assert _with_the_old_reader(module, read, response) != CLOSED[name]


def test_every_parser_has_its_doubled_cases() -> None:
    assert set(DOUBLED_REPLIES) == set(PARSERS) == set(CLOSED)


# --- the month runner: a doubled reply goes to a person -------------------------------------


class Arm:
    """The arm's own model: triage, the own-account check and the gate, each one fixed reply."""

    provider = "fake"
    model = "fake-arm"
    temperature = None
    seed = None
    supports_seed = False

    def __init__(self, **replies: str) -> None:
        self.replies = replies
        self.tasks: list[str] = []

    def describe(self) -> dict[str, str]:
        return {}

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.tasks.append(request.task)
        if request.task not in self.replies:
            raise ModelError(f"no reply for {request.task}")
        return _response(self.replies[request.task])


class Picker:
    """The second stage: records each message and gives one fixed answer."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def __call__(self, text: str) -> tuple[str, str | None]:
        self.asked.append(text)
        return mo.GUIDE, "KB-001"


HOW_TO_TEXT = "How do I connect to the office Wi-Fi?"
OWN_USERNAME_TEXT = "what is my username"


def _route(text: str, model: Arm, picker_stage: Picker) -> mo.Route:
    return mo.route_ticket(
        text,
        sender_name="Ana Smith",
        sender_email="ana.smith@meridian.example",
        model=model,
        setup=ALL_SETUPS["haiku-3d2"],
        second_stage=picker_stage,
        gate=mo.gate_for("G3"),
    )


@pytest.mark.parametrize("text", DOUBLED_REPLIES["triage"])
def test_the_month_runner_hands_a_doubled_triage_reply_off_as_off_format(text: str) -> None:
    model, stage = Arm(triage=text), Picker()
    route = _route(HOW_TO_TEXT, model, stage)
    assert route == mo.Route(mo.HAND_OFF, None, mo.TRIAGE_OFF_FORMAT, None)
    assert model.tasks == ["triage"] and stage.asked == []


@pytest.mark.parametrize("text", DOUBLED_REPLIES["own-account check"])
def test_the_month_runner_hands_a_doubled_check_reply_off_as_a_failed_check(text: str) -> None:
    model = Arm(triage='{"category": "own_username"}', own_account_check=text)
    route = _route(OWN_USERNAME_TEXT, model, Picker())
    assert route == mo.Route(mo.HAND_OFF, None, mo.CHECK_FAILED, "own_username")
    assert model.tasks == ["triage", "own_account_check"]


@pytest.mark.parametrize("text", DOUBLED_REPLIES["gate"])
def test_the_month_runner_hands_a_doubled_gate_reply_off_as_a_failed_gate(text: str) -> None:
    model, stage = Arm(triage='{"category": "other"}', howto_gate=text), Picker()
    route = _route(HOW_TO_TEXT, model, stage)
    assert route == mo.Route(mo.HAND_OFF, None, mo.GATE_FAILED, "other")
    assert stage.asked == []


def test_the_month_runners_own_cases_reach_the_stage_each_test_names() -> None:
    """With the same replies on the schema, each message gets past the step a test stops."""
    stage = Picker()
    on = _route(
        HOW_TO_TEXT, Arm(triage='{"category": "other"}', howto_gate='{"plain_how_to": true}'), stage
    )
    assert on == mo.Route(mo.ANSWER_GUIDE, "KB-001", mo.PICKER_GUIDE, "other")
    check = Arm(
        triage='{"category": "own_username"}', own_account_check='{"own_account_only": true}'
    )
    assert _route(OWN_USERNAME_TEXT, check, Picker()).stage == mo.CHECK_YES


class PickerModel:
    """The picker's model: one fixed reply to every picker call."""

    provider = "fake"
    model = "fake-picker"
    temperature = None
    seed = None
    supports_seed = False

    def __init__(self, text: str) -> None:
        self.text = text

    def describe(self) -> dict[str, str]:
        return {}

    def complete(self, request: ModelRequest) -> ModelResponse:
        assert request.task == "guide_pick", request.task
        return _response(self.text)


def test_the_month_runners_picker_stage_hands_a_doubled_pick_off() -> None:
    """The sealed arms' picker stage (their prompt, KS5), as the month runner builds it."""
    guide = postable(load_runbooks())[0].id
    arm = ALL_SETUPS[mo.SEALED_ARMS[0]]

    def stage(reply: str) -> tuple[str, str | None]:
        return mo.knowledge_picker(arm, PickerModel(reply)).stage(HOW_TO_TEXT)

    assert stage(json.dumps({"request_type": "how_to", "guide": guide})) == (mo.GUIDE, guide)
    for reply in (
        f'{{"request_type": "incident", "request_type": "how_to", "guide": "{guide}"}}',
        f'{{"request_type": "how_to", "guide": "none", "guide": "{guide}"}}',
    ):
        assert stage(reply) == (mo.STAGE_HAND_OFF, None)


# --- a reply that can't be read safely: closed, not raised (branch tier0-parsers-fail-closed) ---
# Python's own limits: json.loads raises RecursionError on nesting deeper than the interpreter
# allows, and a plain ValueError (not a JSONDecodeError) on an integer of more than 4,300 digits.
# A model held to its schema and its reply limit can't send these. Before this branch triage and
# the own-account check caught ValueError only, and the guide draft JSONDecodeError and
# TypeError, so these raised out of them. The gate and the picker caught all of them already.

DEPTH = 100_000
TOO_LONG = "9" * 5000
AT_THE_LIMIT = "9" * 4300  # Python still converts this one


def _deep_lists(depth: int = DEPTH, *, closed: bool = True) -> str:
    return "[" * depth + ("]" * depth if closed else "")


def _deep_objects(depth: int = DEPTH) -> str:
    return '{"a": ' * depth + "1" + "}" * depth


def _with_raw(value: dict[str, Any], key: str, raw: str) -> str:
    """The object as JSON with the value of `key` written as `raw`, which may not be JSON
    Python can read."""
    members = [f"{json.dumps(k)}: {raw if k == key else json.dumps(v)}" for k, v in value.items()]
    return "{" + ", ".join(members) + "}"


GOOD_DRAFT = {
    "title": "Schedule in Slack",
    "summary": "Set reminders in Slack.",
    "steps": ["Open Slack.", "Type /remind.", "Press Enter."],
    "tags": ["slack"],
}
# (reply, the parsers that raised on it before this branch, with a stop)
TOO_DEEP_BEFORE = frozenset({"triage", "own-account check", "guide draft"})
TOO_LONG_BEFORE = frozenset({"guide draft"})
NOT_A_STR_BEFORE = frozenset({"triage", "own-account check"})
UNREADABLE: dict[str, tuple[Any, frozenset[str]]] = {
    "lists, too deep": (_deep_lists(), TOO_DEEP_BEFORE),
    "lists, too deep and never closed": (_deep_lists(closed=False), TOO_DEEP_BEFORE),
    "objects, too deep": (_deep_objects(), TOO_DEEP_BEFORE),
    "too deep, as triage's value": (_with_raw({"category": 0}, "category", _deep_lists()),
                                    TOO_DEEP_BEFORE),
    "too deep, as the check's value": (
        _with_raw({"own_account_only": 0}, "own_account_only", _deep_lists()), TOO_DEEP_BEFORE
    ),
    "too deep, in a draft's steps": (_with_raw(GOOD_DRAFT, "steps", _deep_lists()),
                                     TOO_DEEP_BEFORE),
    "a number too long": (TOO_LONG, TOO_LONG_BEFORE),
    "a number too long, below zero": ("-" + TOO_LONG, TOO_LONG_BEFORE),
    "a number too long, as triage's value": (_with_raw({"category": 0}, "category", TOO_LONG),
                                             TOO_LONG_BEFORE),
    "a number too long, as a draft's title": (_with_raw(GOOD_DRAFT, "title", TOO_LONG),
                                              TOO_LONG_BEFORE),
    "a number too long, in a list": (f"[1, {TOO_LONG}]", TOO_LONG_BEFORE),
    "no text": (None, NOT_A_STR_BEFORE),
    "a number, not a text": (7, NOT_A_STR_BEFORE),
}  # fmt: skip

# What each changed module caught before this branch. The same code with this put back as its
# READ_ERRORS is the code as it was.
OLD_CATCH: dict[ModuleType, tuple[type[Exception], ...]] = {
    agent: (ValueError,),
    drafting: (json.JSONDecodeError, TypeError),
}


@dataclass(frozen=True)
class Raised:
    """The parser raised this, instead of giving an answer."""

    error: type[BaseException]


def _with_the_old_catch(module: ModuleType, read: Read, response: ModelResponse) -> Any:
    """What the parser gave before this branch: the same code, catching what it caught then.
    Raised(...) when that raised."""
    if module not in OLD_CATCH:
        return read(response)  # the gate and the picker: unchanged
    with patch.object(module, "READ_ERRORS", OLD_CATCH[module]):
        try:
            return read(response)
        except READ_ERRORS as error:
            return Raised(type(error))


def test_python_still_refuses_these_replies() -> None:
    """The premise: if a later Python reads these, the tests below test nothing."""
    with pytest.raises(RecursionError):
        json.loads(_deep_lists())
    with pytest.raises(RecursionError):
        json.loads(_deep_objects())
    with pytest.raises(ValueError, match="digits") as caught:
        json.loads(TOO_LONG)
    assert not isinstance(caught.value, json.JSONDecodeError)
    assert json.loads(AT_THE_LIMIT) == int(AT_THE_LIMIT)
    with pytest.raises(TypeError):
        json.loads(None)  # type: ignore[arg-type]


def test_the_three_parsers_catch_every_read_error_and_the_gate_and_picker_the_same() -> None:
    assert (ValueError, RecursionError, TypeError) == READ_ERRORS
    assert agent.READ_ERRORS is READ_ERRORS and drafting.READ_ERRORS is READ_ERRORS
    for function in (agent._triage_value, agent.parse_own_account_check, drafting.parse_draft):
        assert "except READ_ERRORS:" in inspect.getsource(function), function.__name__
    for function in (gate.parse_gate, picker._loads):
        source = inspect.getsource(function)
        assert "except (ValueError, RecursionError, TypeError)" in source, function.__name__


@pytest.mark.parametrize("finish", ["stop", "length", "refusal", "end_turn"])
@pytest.mark.parametrize("case", sorted(UNREADABLE))
def test_a_reply_that_cant_be_read_safely_is_off_the_schema_for_every_parser(
    case: str, finish: str
) -> None:
    text, _ = UNREADABLE[case]
    response = _response(text, finish)
    for name, (_, read) in PARSERS.items():
        assert read(response) == _off_the_schema(read, finish), name


@pytest.mark.parametrize("case", sorted(UNREADABLE))
def test_the_parsers_that_raised_on_these_before_now_give_the_fail_closed_answer(
    case: str,
) -> None:
    text, raised_before = UNREADABLE[case]
    response = _response(text)
    for name, (module, read) in PARSERS.items():
        old = _with_the_old_catch(module, read, response)
        assert isinstance(old, Raised) == (name in raised_before), name
        assert read(response) == CLOSED[name], name


def test_every_unreadable_case_raised_before_somewhere() -> None:
    """The cases are the gap, not already closed: each crashed at least one parser."""
    assert all(raised for _, raised in UNREADABLE.values())


too_deep = st.builds(
    lambda depth, objects, closed: (
        _deep_objects(depth) if objects else _deep_lists(depth, closed=closed)
    ),
    st.sampled_from((DEPTH, 2 * DEPTH)),
    st.booleans(),
    st.booleans(),
)
too_long = st.builds(
    lambda digits, sign: sign + "1" * digits, st.integers(4301, 6000), st.sampled_from(("", "-"))
)
unreadable_piece = too_deep | too_long
# A piece alone, as the value of one of a parser's own members, or wrapped.
unreadable = st.builds(
    lambda value, pick, piece, wrap, where: wrap(
        piece if where == 0 else _with_raw(value, sorted(value)[pick % len(value)], piece)
    ),
    on_schema,
    st.integers(0, 3),
    unreadable_piece,
    st.sampled_from(
        (lambda t: t, lambda t: f" {t}\n", lambda t: f"[{t}]", lambda t: f'{{"reply": {t}}}')
    ),
    st.integers(0, 2),
)
UNREADABLE_SETTINGS = settings(derandomize=True, max_examples=60, database=None, deadline=None)


@UNREADABLE_SETTINGS
@given(text=unreadable, finish=st.sampled_from(FINISHES))
def test_a_reply_too_deep_or_with_a_number_too_long_anywhere_is_off_the_schema_for_every_parser(
    text: str, finish: str
) -> None:
    response = _response(text, finish)
    for name, (_, read) in PARSERS.items():
        assert read(response) == _off_the_schema(read, finish), name


@SETTINGS
@given(text=any_text | doubled, finish=st.sampled_from(FINISHES))
def test_for_any_reply_read_without_a_crash_before_each_parser_gives_what_it_gave_before(
    text: str, finish: str
) -> None:
    """Well-formed or not, a reply the old code read without raising reads exactly as before."""
    response = _response(text, finish)
    for name, (module, read) in PARSERS.items():
        old = _with_the_old_catch(module, read, response)
        assert not isinstance(old, Raised), name
        assert read(response) == old, name


@pytest.mark.parametrize("own", sorted(OWN))
@SETTINGS
@given(data=st.data(), finish=st.sampled_from(FINISHES))
def test_each_parsers_own_object_reads_as_before_the_wider_catch(
    own: str, data: st.DataObject, finish: str
) -> None:
    value = data.draw(OWN[own])
    for text in (json.dumps(value), json.dumps(value, indent=2), f" {json.dumps(value)}\n"):
        response = _response(text, finish)
        for name, (module, read) in PARSERS.items():
            assert read(response) == _with_the_old_catch(module, read, response), name
    _, read = PARSERS[own]
    if finish == "stop" and "Not A Tag" not in value.get("tags", ()):
        assert read(_response(json.dumps(value))) != CLOSED[own]


@UNREADABLE_SETTINGS
@given(text=unreadable | any_text, finish=st.sampled_from(FINISHES))
def test_for_any_reply_each_parser_gives_what_it_gave_before_or_closed_where_it_raised(
    text: str, finish: str
) -> None:
    response = _response(text, finish)
    for name, (module, read) in PARSERS.items():
        old = _with_the_old_catch(module, read, response)
        expected = _off_the_schema(read, finish) if isinstance(old, Raised) else old
        assert read(response) == expected, name


def test_a_number_at_pythons_limit_reads_as_before_in_every_parser() -> None:
    for value in ({"category": 0}, {"own_account_only": 0}, GOOD_DRAFT):
        key = next(iter(value))
        response = _response(_with_raw(value, key, AT_THE_LIMIT))
        for name, (module, read) in PARSERS.items():
            old = _with_the_old_catch(module, read, response)
            assert not isinstance(old, Raised) and read(response) == old == CLOSED[name], name


# --- the same through the month runner: a hand-off, and the run goes on ----------------------

TOO_DEEP_REPLIES = (
    _deep_lists(),
    _deep_objects(),
    _with_raw({"category": 0}, "category", _deep_lists()),
)
TOO_LONG_TRIAGE = _with_raw({"category": 0}, "category", TOO_LONG)
TOO_DEEP_CHECK = _with_raw({"own_account_only": 0}, "own_account_only", _deep_lists())
TOO_LONG_CHECK = _with_raw({"own_account_only": 0}, "own_account_only", TOO_LONG)


@pytest.mark.parametrize("text", [*TOO_DEEP_REPLIES, TOO_LONG_TRIAGE])
def test_the_month_runner_hands_an_unreadable_triage_reply_off_as_off_format(text: str) -> None:
    model, stage = Arm(triage=text), Picker()
    route = _route(HOW_TO_TEXT, model, stage)
    assert route == mo.Route(mo.HAND_OFF, None, mo.TRIAGE_OFF_FORMAT, None)
    assert model.tasks == ["triage"] and stage.asked == []


@pytest.mark.parametrize("text", [*TOO_DEEP_REPLIES, TOO_DEEP_CHECK, TOO_LONG_CHECK])
def test_the_month_runner_hands_an_unreadable_check_reply_off_as_a_failed_check(
    text: str,
) -> None:
    model = Arm(triage='{"category": "own_username"}', own_account_check=text)
    route = _route(OWN_USERNAME_TEXT, model, Picker())
    assert route == mo.Route(mo.HAND_OFF, None, mo.CHECK_FAILED, "own_username")
    assert model.tasks == ["triage", "own_account_check"]


class MonthArm:
    """Triage says "other" to every message but two: one gets a reply nested too deep, and one
    gets "own_username" and then a check reply nested too deep. Calls nothing."""

    provider = "fake"
    model = "fake-arm"
    temperature = None
    seed = None
    supports_seed = False

    def __init__(self, deep_triage: str, deep_check: str) -> None:
        self.deep_triage, self.deep_check = deep_triage, deep_check

    def describe(self) -> dict[str, str]:
        return {}

    def complete(self, request: ModelRequest) -> ModelResponse:
        if request.task == "triage":
            if request.user == self.deep_triage:
                return _response(_deep_lists())
            if request.user == self.deep_check:
                return _response('{"category": "own_username"}')
            return _response('{"category": "other"}')
        if request.task == "own_account_check":
            return _response(_deep_lists())
        raise ModelError(f"no reply for {request.task}")


def _month_case() -> tuple[mo.Month, MonthArm, int, int]:
    """The tuning month (no sealed set), and the arm. The two tickets have texts no other ticket
    has: the first such from the 6th on, and the next one that reaches the own-account check on
    this setup (no role word, no reset)."""
    month = month_for(TUNING_MONTH)
    tickets = month.tickets
    texts = Counter(t.text for t in tickets)
    deep = next(n for n, t in enumerate(tickets) if n >= 5 and texts[t.text] == 1)
    check = next(
        n
        for n, t in enumerate(tickets)
        if n > deep
        and texts[t.text] == 1
        and not role_reference(t.text)
        and not reset_request(t.text)
    )
    return month, MonthArm(tickets[deep].text, tickets[check].text), deep, check


def test_an_unreadable_reply_no_longer_stops_a_month_run() -> None:
    month, arm, deep, check = _month_case()
    setup = ALL_SETUPS["haiku-3d2"]
    saved: list[dict[str, Any]] = []
    records = mo.run_month(month, setup=setup, model=arm, k=2, on_record=saved.append)
    assert len(records) == len(saved) == 2 * len(month.tickets)
    stages = {(r["ticket_id"], r["run"]): r["stage"] for r in records}
    for run in (1, 2):
        assert stages[(month.tickets[deep].id, run)] == mo.TRIAGE_OFF_FORMAT
        assert stages[(month.tickets[check].id, run)] == mo.CHECK_FAILED
    bad = {month.tickets[deep].id, month.tickets[check].id}
    assert all(r["outcome"] == mo.HAND_OFF for r in records if r["ticket_id"] in bad)
    # Every other ticket: "other" with no picker, or a reset this setup hands off by code.
    assert {r["stage"] for r in records if r["ticket_id"] not in bad} == {
        mo.NO_PICKER,
        mo.RESET_SIGNAL,
    }
    mo.score(month, records, k=2, samples=200)  # every ticket has its k runs


def test_before_this_branch_the_same_reply_stopped_the_month_run() -> None:
    """With the old catch put back, the run stops at the first reply nested too deep."""
    month, arm, deep, _ = _month_case()
    saved: list[dict[str, Any]] = []
    with patch.object(agent, "READ_ERRORS", OLD_CATCH[agent]), pytest.raises(RecursionError):
        mo.run_month(month, setup=ALL_SETUPS["haiku-3d2"], model=arm, k=2, on_record=saved.append)
    assert len(saved) == deep


# --- the same through the live agent: a hand-off with a reply, not an exception --------------


class LiveModel:
    """The stub underneath; one task's reply is replaced."""

    provider = "test"
    model = "scripted"

    def __init__(self, task: str, text: str) -> None:
        self.task, self.text = task, text
        self._stub = StubModel()

    def complete(self, request: ModelRequest) -> ModelResponse:
        real = self._stub.complete(request)
        if request.task != self.task:
            return real
        return dataclasses.replace(real, text=self.text, finish_reason="stop")


def _live(world: World, model: LiveModel) -> tuple[Any, Any, BaseException | None]:
    """(runtime, outcome or None, the exception `handle` raised or None) for TKT-001, a request
    for the sender's own username."""
    ticket = next(t for t in world.tickets if t.id == "TKT-001")

    async def main() -> tuple[Any, Any, BaseException | None]:
        async with offline_runtime(world, model=model) as rt:
            rt.clock.advance_to(parse_time(ticket.received_at))
            try:
                return rt, await rt.agent.handle(rt.slack.message_event(ticket)), None
            except Exception as error:
                return rt, None, error

    return anyio.run(main)


LIVE_CASES = {
    "triage, too deep": ("triage", _deep_lists(), "policy_escalate"),
    "triage, too deep in its value": ("triage", TOO_DEEP_REPLIES[2], "policy_escalate"),
    "check, too deep": ("own_account_check", _deep_lists(), "own_account_check_failed"),
    "check, too deep in its value": ("own_account_check", TOO_DEEP_CHECK,
                                     "own_account_check_failed"),
}  # fmt: skip


def _events(rt: Any, name: str) -> list[dict[str, Any]]:
    return [
        dict(e.attributes or {})
        for s in rt.tracer.finished_spans()
        for e in s.events
        if e.name == name
    ]


@pytest.mark.parametrize("case", sorted(LIVE_CASES))
def test_in_the_live_agent_an_unreadable_reply_goes_to_a_person_with_a_reply(
    world: World, case: str
) -> None:
    task, text, reason = LIVE_CASES[case]
    rt, outcome, error = _live(world, LiveModel(task, text))
    assert error is None
    assert (outcome.action, outcome.state) == ("escalate", TRIAGE)
    assert rt.tickets.get_ticket(outcome.ticket).state == TRIAGE
    (sent,) = [m.text for m in rt.slack.sent_messages]
    assert any(v in sent for v in replies.TEMPLATES["handoff"]["en"])
    assert rt.issuer.issued == ()
    assert _events(rt, "tier0.format_failure") == [{"task": task}]
    assert _events(rt, "tier0.escalated") == [{"reason": reason}]


@pytest.mark.parametrize("case", sorted(LIVE_CASES))
def test_before_this_branch_handle_raised_and_the_requester_heard_nothing(
    world: World, case: str
) -> None:
    """What the old catch did: `handle`'s except moved the ticket to Triage (a person) and
    raised again; no reply was sent and no format failure was recorded."""
    task, text, _ = LIVE_CASES[case]
    with patch.object(agent, "READ_ERRORS", OLD_CATCH[agent]):
        rt, outcome, error = _live(world, LiveModel(task, text))
    assert outcome is None and isinstance(error, RecursionError)
    assert [t.state for t in rt.tickets.find_tickets()] == [TRIAGE]
    assert rt.slack.sent_messages == () and rt.issuer.issued == ()
    assert _events(rt, "tier0.format_failure") == []
