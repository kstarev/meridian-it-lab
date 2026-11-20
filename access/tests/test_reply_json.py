"""The reader of model replies (meridian_access.ai.reply_json) and the client that uses it.

`json.loads` keeps the last of two members with one name. The client read model replies that
way, so `{"employee_id": "none", "employee_id": "E-PROBE-1", "evidence": []}` was a match and
`{"role": "none", "role": "probe-role-a", "reason": "r"}` was a role. It now reads them with
`loads_reply`, which refuses a doubled name. Checked here:

- `loads_reply` reads every text exactly as `json.loads` does, values and types, unless an
  object in it names a key twice; then it raises `DoubledKeyError`, a `json.JSONDecodeError`;
- `ClaudeClient.complete_json` gives the same answer as before, with the old reader put back,
  for any reply with no doubled name, whatever its stop reason, for each of the three jobs'
  schemas, and records the same spend;
- for a reply with a doubled name it gives None, as for a reply off the schema, and records
  the same spend; so each job fails closed as it does for an off-schema reply: no role, no
  owner, and the plan summary falls back to the template.

Every reply is made up here and every model is a fake: nothing is called and nothing costs
money. No world file, artifact or sealed set is read beyond the tuning world's plan. The runs
are fixed (derandomize) and keep no example database.
"""

import copy
import json
import os
import pickle
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from meridian_access.ai import client as client_module
from meridian_access.ai import frozen
from meridian_access.ai.client import ClaudeClient
from meridian_access.ai.matching import EVIDENCE_FIELDS, MatchSuggestion, suggest_owner
from meridian_access.ai.pricing import CHOSEN_MODEL
from meridian_access.ai.reply_json import DoubledKeyError, loads_reply
from meridian_access.ai.spend import SpendLedger
from meridian_access.ai.titles import RoleSuggestion, suggest_role
from meridian_access.models import Account
from meridian_access.plan import build_plan
from meridian_access.summary import choose_summary, model_summary, template_summary
from meridian_dataset.access.model import DOMAIN, HrisRecord

SETTINGS = settings(derandomize=True, max_examples=300, database=None, deadline=None)

PARTS = frozen.prompt_parts()  # the three jobs' real schemas, built from fake probe inputs
JOBS = tuple(PARTS)  # title, owner, summary
ROLES = ("probe-role-a", "probe-role-b")
IDS = ("E-PROBE-1", "E-PROBE-2")


# --- fakes ------------------------------------------------------------------------------------


class _MemoryLedger(SpendLedger):
    """Refuses nothing and writes nothing; keeps what the client records, to compare."""

    def __init__(self) -> None:
        super().__init__(path=Path(os.devnull), cap_usd=1.0, run_limit_usd=1.0)
        self.records: list[tuple[Any, ...]] = []

    def check(self, next_call_usd: float = 0.0) -> None:
        return None

    def record(self, *args: Any, **kwargs: Any) -> float:
        self.records.append((args, tuple(sorted(kwargs.items()))))
        return 0.0


class _ReplySDK:
    """Stands in for the SDK client: answers every request with the same text."""

    def __init__(self, text: str, stop_reason: str = "end_turn") -> None:
        self.text, self.stop_reason = text, stop_reason
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **params: Any) -> SimpleNamespace:
        return SimpleNamespace(
            model=params["model"],
            stop_reason=self.stop_reason,
            usage=SimpleNamespace(input_tokens=100, output_tokens=20),
            content=[SimpleNamespace(type="text", text=self.text)],
        )


def _client(text: str, stop_reason: str = "end_turn") -> ClaudeClient:
    return ClaudeClient(CHOSEN_MODEL, client=_ReplySDK(text, stop_reason), ledger=_MemoryLedger())


def _ask(job: str, text: str, stop_reason: str = "end_turn") -> tuple[Any, ClaudeClient]:
    """What complete_json gives the reply for one job's schema, and the client it used."""
    c = _client(text, stop_reason)
    p = PARTS[job]
    return c.complete_json(p["system"], p["user"], p["schema"], p["max_tokens"]), c


def _the_old_reader() -> Any:
    """The client as it was: json.loads as its reader."""
    return patch.object(client_module, "loads_reply", json.loads)


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
    if isinstance(a, float) and a != a:  # NaN, from a bare NaN in a reply
        return b != b
    return bool(a == b)


# --- what replies look like -------------------------------------------------------------------

KEYS = (
    "role",
    "reason",
    "employee_id",
    "evidence",
    "paragraph",
    "Role",
    "role ",
    "employee_Id",
    "x",
    "",
)
WORDS = (*ROLES, *IDS, *EVIDENCE_FIELDS, "none", "E-PROBE-3", "true", "The plan has {total}.")
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
# Each job's own object, on its schema, so that each job's answers are reached often.
OWN: dict[str, st.SearchStrategy[dict[str, Any]]] = {
    "title": st.builds(
        lambda r, why: {"role": r, "reason": why},
        st.sampled_from((*ROLES, "none")),
        st.text(max_size=12),
    ),
    "owner": st.builds(
        lambda i, ev: {"employee_id": i, "evidence": ev},
        st.sampled_from((*IDS, "none")),
        st.lists(st.sampled_from(EVIDENCE_FIELDS), max_size=3),
    ),
    "summary": st.builds(
        lambda t: {"paragraph": t},
        st.sampled_from(("The plan has {total} changes.", "", " ")) | st.text(max_size=20),
    ),
}
assert set(OWN) == set(JOBS)
on_schema = st.one_of(*OWN.values())
# Near objects: an object on a schema with one member taken out, and maybe some added.
near_schema = st.builds(
    lambda value, extra, drop: {
        **{k: v for i, (k, v) in enumerate(value.items()) if i != drop},
        **extra,
    },
    on_schema,
    st.just({}) | st.dictionaries(st.sampled_from(KEYS), scalars, min_size=1, max_size=2),
    st.integers(-1, 2),
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
# Any text, too: most of it isn't JSON, and both readers must refuse it alike.
any_text = well_formed | st.text(max_size=40) | st.sampled_from(('{"a": NaN}', "[Infinity]", ""))
STOPS = ("end_turn", "end_turn", "end_turn", "max_tokens", "refusal", "stop_sequence")


def _with_a_doubled_key(value: dict[str, Any], key: str, other: Any, last: bool) -> str:
    """The object as JSON with `key` named once more, with the value `other`: before its own
    member (so the old reader kept the object's own value) or after it (it kept `other`)."""
    members = [f"{json.dumps(k)}: {json.dumps(v)}" for k, v in value.items()]
    extra = f"{json.dumps(key)}: {json.dumps(other)}"
    return "{" + ", ".join([*members, extra] if last else [extra, *members]) + "}"


def _doubled(job_objects: st.SearchStrategy[dict[str, Any]]) -> st.SearchStrategy[str]:
    return st.builds(
        lambda value, pick, other, last: _with_a_doubled_key(
            value, sorted(value)[pick % len(value)], other, last
        ),
        job_objects,
        st.integers(0, 3),
        scalars | on_schema.map(lambda v: next(iter(v.values()))),
        st.booleans(),
    )


doubled = st.builds(
    lambda text, wrap: wrap(text),
    _doubled(on_schema),
    st.sampled_from(
        (
            lambda t: t,
            lambda t: f" {t}\n",
            lambda t: f"[{t}]",
            lambda t: f'{{"reply": {t}}}',
            lambda t: f'{{"role": "none", "x": [1, {t}]}}',
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
        with pytest.raises(DoubledKeyError):
            loads_reply(text)
    else:
        assert _same(loads_reply(text), old)


@SETTINGS
@given(text=doubled)
def test_loads_reply_refuses_any_object_that_names_a_key_twice(text: str) -> None:
    assert _names_a_key_twice(text)
    json.loads(text)  # the old reader read it, by the last value
    with pytest.raises(DoubledKeyError):
        loads_reply(text)


DOUBLED_CASES = {
    "the matcher, none then a match": '{"employee_id": "none", "employee_id": "E-PROBE-1"}',
    "the same value twice": '{"role": "none", "role": "none", "reason": "r"}',
    "an escaped name": '{"role": "none", "r\\u006fle": "probe-role-a", "reason": "r"}',
    "in a nested object": '{"paragraph": "p", "x": {"a": 1, "a": 2}}',
    "in a list": '[{"paragraph": "a", "paragraph": "b"}]',
    "an empty name twice": '{"": 1, "": 2}',
}


@pytest.mark.parametrize("text", DOUBLED_CASES.values(), ids=DOUBLED_CASES)
def test_a_doubled_name_is_a_json_decode_error(text: str) -> None:
    with pytest.raises(DoubledKeyError) as caught:
        loads_reply(text)
    # A JSONDecodeError, so a ValueError: what the client already catches.
    assert isinstance(caught.value, json.JSONDecodeError)
    assert isinstance(caught.value, ValueError)


def test_the_error_names_the_doubled_key_and_can_be_copied() -> None:
    with pytest.raises(DoubledKeyError) as caught:
        loads_reply('{"employee_id": "E-PROBE-1", "evidence": [], "employee_id": "none"}')
    assert caught.value.key == "employee_id" and "'employee_id'" in str(caught.value)
    # A copy or a pickle rebuilds the error from its key, not from JSONDecodeError's three.
    for again in (copy.deepcopy(caught.value), pickle.loads(pickle.dumps(caught.value))):  # noqa: S301
        assert type(again) is DoubledKeyError and again.key == "employee_id"


NOT_DOUBLED = {
    "two cases of one name": '{"role": "probe-role-a", "Role": "none", "reason": "r"}',
    "a space after the name": '{"role": "probe-role-a", "role ": "none", "reason": "r"}',
    "one name in two objects": '[{"paragraph": "a"}, {"paragraph": "b"}]',
    "a name inside a value": '{"role": "none", "reason": "role"}',
}


@pytest.mark.parametrize("text", NOT_DOUBLED.values(), ids=NOT_DOUBLED)
def test_names_that_only_look_alike_are_not_doubled(text: str) -> None:
    assert _same(loads_reply(text), json.loads(text))


# --- the client: as before with no doubled name, None with one ---------------------------------


@SETTINGS
@given(job=st.sampled_from(JOBS), text=any_text, stop=st.sampled_from(STOPS))
def test_the_client_reads_a_reply_with_no_doubled_name_as_before(
    job: str, text: str, stop: str
) -> None:
    if _names_a_key_twice(text):
        return  # the doubled ones are the next test's
    now, c_now = _ask(job, text, stop)
    with _the_old_reader():
        before, c_before = _ask(job, text, stop)
    assert _same(now, before)
    assert c_now.calls == c_before.calls == 1
    assert c_now.ledger.records == c_before.ledger.records  # type: ignore[attr-defined]


@SETTINGS
@given(job=st.sampled_from(JOBS), data=st.data())
def test_a_doubled_reply_gives_none_and_records_what_an_off_schema_reply_does(
    job: str, data: st.DataObject
) -> None:
    text = data.draw(st.one_of(_doubled(OWN[job]), doubled), label="reply")
    now, c_now = _ask(job, text)
    off, c_off = _ask(job, "{}")  # an object with none of the schema's keys
    assert now is None and off is None
    assert c_now.calls == c_off.calls == 1
    assert c_now.ledger.records == c_off.ledger.records  # type: ignore[attr-defined]


# --- each job: closed as for an off-schema reply; the old reader took the last value -----------
# Each reply below names a key twice and its last value fits the schema, so the old reader gave
# an answer: these tests fail if the client reads with json.loads again.

TITLE_REPLY = '{"role": "none", "role": "probe-role-a", "reason": "r"}'
OWNER_REPLY = '{"employee_id": "none", "evidence": ["email_local"], "employee_id": "E-PROBE-1"}'


def _record(n: int) -> HrisRecord:
    return HrisRecord(
        employee_id=f"E-PROBE-{n}",
        given_name="Probe",
        family_name=f"Person{n}",
        preferred_name="",
        work_email=f"probe.person{n}@{DOMAIN}",
        personal_email="",
        job_title="Probe Title",
        department="Probe Department",
        team="Probe Team",
        manager_id="",
        country="XX",
        hub="Probe Hub",
        employment_type="employee",
        status="active",
        start_date="2026-01-01",
        end_date="",
        leave_start="",
        leave_end="",
    )


PROBE_ACCOUNT = Account(
    system="probe-app",
    account="probe.person1",
    email=f"probe.person1@{DOMAIN}",
    given="Probe",
    family="Person1",
    display="Probe Person1",
)


def _role(text: str) -> RoleSuggestion | None:
    return suggest_role("Probe Title", "Probe Department", "Probe Team", ROLES, _client(text))


def _owner(text: str) -> MatchSuggestion | None:
    return suggest_owner(PROBE_ACCOUNT, [_record(1), _record(2)], _client(text))


def test_the_title_job_gives_no_role_for_a_doubled_reply() -> None:
    assert _role(TITLE_REPLY) is None and _role("{}") is None
    with _the_old_reader():
        assert _role(TITLE_REPLY) == RoleSuggestion("probe-role-a", "r", CHOSEN_MODEL)


def test_the_matcher_drops_its_suggestion_for_a_doubled_reply() -> None:
    assert _owner(OWNER_REPLY) is None and _owner("{}") is None
    with _the_old_reader():
        assert _owner(OWNER_REPLY) == MatchSuggestion("E-PROBE-1", ("email_local",), CHOSEN_MODEL)


@pytest.fixture(scope="module")
def plan(world_dir, policy):
    return build_plan(world_dir, policy)


def test_the_summary_falls_back_to_the_template_for_a_doubled_reply(plan) -> None:
    good = "The plan has {total} changes and needs {approvals} approvals."
    reply = json.dumps({"paragraph": "x"})[:-1] + ", " + json.dumps({"paragraph": good})[1:]
    assert _names_a_key_twice(reply)
    assert model_summary(plan, _client(reply)) is None
    template = (template_summary(plan), False)
    assert choose_summary(plan, _client(reply)) == template == choose_summary(plan, _client("{}"))
    with _the_old_reader():
        assert model_summary(plan, _client(reply)) == good
        assert choose_summary(plan, _client(reply))[1] is True  # written by the model


def test_the_frozen_client_of_the_final_run_gives_none_for_a_doubled_reply() -> None:
    p = PARTS["title"]
    client = frozen.FrozenClaudeClient(
        frozen.FROZEN_MODEL, client=_ReplySDK(TITLE_REPLY), ledger=_MemoryLedger()
    )
    assert client.complete_json(p["system"], p["user"], p["schema"], p["max_tokens"]) is None


def test_the_reader_does_not_move_the_frozen_prompts() -> None:
    # Reading a reply is not part of any request: the twelve pins and the digest stay.
    assert frozen.problems() == []
    assert frozen.FREEZE_DIGEST.startswith("e48df223")
