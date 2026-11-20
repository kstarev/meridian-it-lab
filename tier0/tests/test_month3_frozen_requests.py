"""held_out_month3's frozen plan pins the gate's and the picker's whole requests, and how the
runner reads their replies, not only the prompts' text (the 3f review's Q3). No model is called,
and no month is read."""

import json
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest

from meridian_dataset.knowledge import load_runbooks
from meridian_dataset.month import HELD_OUT_MONTH1, HELD_OUT_MONTH2, HELD_OUT_MONTH2B
from tier0.evals import month as mo
from tier0.evals.setups import ALL_SETUPS
from tier0.knowledge import answer, gate, picker, prompts
from tier0.knowledge.prompts import PICKER_VERSIONS
from tier0.knowledge.screen import hand_off_signal
from tier0.models import ModelRequest

MONTH3 = "held_out_month3"
PLAN = mo.SEALED_PLANS[MONTH3]
OK = {"picker": "knowledge", "picker_prompt": "KS6A-H", "own_model_picker": False, "k": 3}


@pytest.fixture
def sealed_month3(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only month3 is sealed, as today. Set here so the test doesn't depend on the registry."""
    monkeypatch.setattr(mo, "SEALED_SETS", frozenset({MONTH3}))


def _month3(arm: str = "haiku-3d2") -> str | None:
    return mo.check_sealed_plan(MONTH3, ALL_SETUPS[arm], gate="G3", **OK)


def test_month3_pins_its_whole_requests_and_the_months_before_it_do_not() -> None:
    pins = (
        PLAN.gate_request_sha256,
        PLAN.gate_replies_sha256,
        PLAN.picker_request_sha256,
        PLAN.picker_replies_sha256,
    )
    assert all(isinstance(p, str) and len(p) == 64 for p in pins)
    for name in (HELD_OUT_MONTH1, HELD_OUT_MONTH2, HELD_OUT_MONTH2B):
        plan = mo.SEALED_PLANS[name]
        assert plan.gate_request_sha256 is None and plan.gate_replies_sha256 is None
        assert plan.picker_request_sha256 is None and plan.picker_replies_sha256 is None


def test_the_pins_match_todays_code() -> None:
    """The rehearsal ran on this code: the request files are unchanged since its commit
    (a768ab2), and that commit's code gives the same digests."""
    assert mo.requests_sha256(mo.probe_gate("G3")[1]) == PLAN.gate_request_sha256
    assert mo.requests_sha256(mo.probe_picker("KS6A-H")[1]) == PLAN.picker_request_sha256
    assert mo.cases_sha256(mo.GATE_REPLY_CASES) == PLAN.gate_replies_sha256
    assert mo.cases_sha256(mo.PICKER_REPLY_CASES) == PLAN.picker_replies_sha256
    assert mo.check_frozen_requests(MONTH3, PLAN) is None


@pytest.mark.usefixtures("sealed_month3")
def test_month3_runs_as_frozen_on_both_arms() -> None:
    for arm in mo.SEALED_ARMS:
        assert _month3(arm) is None


def test_the_probe_reaches_both_calls_so_the_pins_hold_something() -> None:
    """The probe passes the message screen, so each pin holds one real request: the one the
    runner sends a how-to message."""
    assert hand_off_signal(mo.REQUEST_PROBE) is None
    verdict, (sent,) = mo.probe_gate("G3")
    assert verdict == mo.ASK_PICKER
    assert sent == gate.gate_request(mo.REQUEST_PROBE, gate.GATE_VERSIONS["G3"])
    assert (sent.task, sent.max_tokens, sent.json_schema) == ("howto_gate", 1024, gate.GATE_SCHEMA)
    assert f"<message>\n{mo.REQUEST_PROBE}\n</message>" in sent.user
    outcome, (asked,) = mo.probe_picker("KS6A-H")
    assert outcome == ("guide", "KB-017")
    want = picker.pick_request(
        mo.REQUEST_PROBE, answer.entries_for(load_runbooks()), PICKER_VERSIONS["KS6A-H"]
    )
    assert asked == want
    assert (asked.task, asked.max_tokens) == ("guide_pick", 64)
    assert asked.system.startswith(PICKER_VERSIONS["KS6A-H"])


def test_the_reply_tables_are_read_as_frozen() -> None:
    for text, finish, frozen in mo.GATE_REPLY_CASES:
        assert mo.probe_gate("G3", None if text is None else (text, finish))[0] == frozen
    for text, finish, frozen in mo.PICKER_REPLY_CASES:
        assert mo.probe_picker("KS6A-H", None if text is None else (text, finish))[0] == frozen
    # Only one exact reply lets the gate through, and only a how-to with a listed guide is
    # answered: the tables show the parsers fail closed.
    through = [c for c in mo.GATE_REPLY_CASES if c[2] == mo.ASK_PICKER]
    assert all(json.loads(t) == {"plain_how_to": True} and f == "stop" for t, f, _ in through)
    answered = [c for c in mo.PICKER_REPLY_CASES if c[2][0] == "guide"]
    assert all(f == "stop" and json.loads(t)["request_type"] == "how_to" for t, f, _ in answered)


# --- a change around a prompt, its text unchanged, is refused for month3 only ------------------


def _gate_wrapped(
    monkeypatch: pytest.MonkeyPatch, change: Callable[[ModelRequest], ModelRequest]
) -> None:
    built = gate.gate_request
    monkeypatch.setattr(gate, "gate_request", lambda text, system: change(built(text, system)))


def _picker_wrapped(
    monkeypatch: pytest.MonkeyPatch, change: Callable[[ModelRequest], ModelRequest]
) -> None:
    # answer_question calls the name it imported, so the change goes there.
    built = picker.pick_request

    def pick_request(*args: Any, **kwargs: Any) -> ModelRequest:
        return change(built(*args, **kwargs))

    monkeypatch.setattr(answer, "pick_request", pick_request)


def _reordered(schema: dict[str, Any]) -> dict[str, Any]:
    return {**schema, "properties": dict(reversed(list(schema["properties"].items())))}


GATE_CHANGES: dict[str, Callable[[pytest.MonkeyPatch], None]] = {
    "reply limit 1023": lambda mp: _gate_wrapped(mp, lambda r: replace(r, max_tokens=1023)),
    "reply limit 64": lambda mp: _gate_wrapped(mp, lambda r: replace(r, max_tokens=64)),
    "wrapper": lambda mp: _gate_wrapped(
        mp, lambda r: replace(r, user=r.user.replace("<message>", "<ticket>"))
    ),
    "wrapper's first line": lambda mp: _gate_wrapped(
        mp, lambda r: replace(r, user=r.user.replace("Message from the employee:", "Message:"))
    ),
    "task": lambda mp: _gate_wrapped(mp, lambda r: replace(r, task="triage")),
    "no schema": lambda mp: _gate_wrapped(mp, lambda r: replace(r, json_schema=None)),
    "schema allows more keys": lambda mp: mp.setattr(
        gate, "GATE_SCHEMA", {**gate.GATE_SCHEMA, "additionalProperties": True}
    ),
}
PICKER_CHANGES: dict[str, Callable[[pytest.MonkeyPatch], None]] = {
    "reply limit 65": lambda mp: _picker_wrapped(mp, lambda r: replace(r, max_tokens=65)),
    "reply limit 1024": lambda mp: _picker_wrapped(mp, lambda r: replace(r, max_tokens=1024)),
    "wrapper": lambda mp: _picker_wrapped(
        mp, lambda r: replace(r, user=r.user.replace("<message>", "<ticket>"))
    ),
    "schema's property order": lambda mp: _picker_wrapped(
        mp, lambda r: replace(r, json_schema=_reordered(r.json_schema or {}))
    ),
    "schema without 'none'": lambda mp: mp.setattr(
        picker,
        "pick_schema",
        lambda entries, asks=False: {
            "type": "object",
            "properties": {
                "request_type": {"type": "string", "enum": list(picker.REQUEST_TYPES)},
                "guide": {"type": "string", "enum": [e.id for e in entries]},
            },
            "required": ["request_type", "guide"],
            "additionalProperties": False,
        },
    ),
    "guide list format": lambda mp: mp.setattr(
        picker, "guide_list", lambda entries: "\n".join(f"{e.id}: {e.title}" for e in entries)
    ),
    "guide list header": lambda mp: _picker_wrapped(
        mp, lambda r: replace(r, system=r.system.replace("(id | title | what it covers)", ""))
    ),
    "the asks mode": lambda mp: mp.setitem(prompts.PICKER_MODES, "KS6A-H", "asks"),
}


@pytest.mark.usefixtures("sealed_month3")
@pytest.mark.parametrize("change", sorted(GATE_CHANGES))
def test_a_change_to_the_gates_request_is_refused(
    change: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    GATE_CHANGES[change](monkeypatch)
    for arm in mo.SEALED_ARMS:
        refusal = _month3(arm)
        assert refusal is not None and "gate G3's whole request" in refusal
        assert MONTH3 in refusal and "SHA-256" in refusal


@pytest.mark.usefixtures("sealed_month3")
@pytest.mark.parametrize("change", sorted(PICKER_CHANGES))
def test_a_change_to_the_pickers_request_is_refused(
    change: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    PICKER_CHANGES[change](monkeypatch)
    for arm in mo.SEALED_ARMS:
        refusal = _month3(arm)
        assert refusal is not None and "the picker's whole request with KS6A-H" in refusal
        assert MONTH3 in refusal and "SHA-256" in refusal


@pytest.mark.usefixtures("sealed_month3")
def test_a_change_to_how_the_gates_reply_is_read_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    strict = gate.parse_gate

    def lenient(response: Any) -> bool | None:
        """A changed parser: it also takes a bare "yes"."""
        if response is not None and response.text.strip().lower() == "yes":
            return True
        return strict(response)

    monkeypatch.setattr(gate, "parse_gate", lenient)
    refusal = _month3()
    assert refusal is not None and "the gate's reply" in refusal and "'yes'" in refusal
    assert MONTH3 in refusal and "'picker'" in refusal


@pytest.mark.usefixtures("sealed_month3")
def test_a_change_to_how_the_pickers_reply_is_read_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    strict = picker.parse_pick

    def cut_short_is_fine(response: Any, entries: Any, *, asks: bool = False) -> picker.Pick:
        return strict(replace(response, finish_reason="stop"), entries, asks=asks)

    monkeypatch.setattr(answer, "parse_pick", cut_short_is_fine)
    refusal = _month3()
    assert refusal is not None and "the picker's reply" in refusal and "'length'" in refusal


TABLE_CHANGES: dict[str, tuple[str, tuple[mo.ReplyCase, ...]]] = {
    "a gate case dropped": ("gate", mo.GATE_REPLY_CASES[:-1]),
    "a gate verdict changed": (
        "gate",
        ((mo.GATE_REPLY_CASES[0][0], "stop", mo.GATE_NO), *mo.GATE_REPLY_CASES[1:]),
    ),
    "a picker case dropped": ("picker", mo.PICKER_REPLY_CASES[1:]),
    "a picker verdict changed": (
        "picker",
        ((mo.PICKER_REPLY_CASES[0][0], "stop", ("hand_off", None)), *mo.PICKER_REPLY_CASES[1:]),
    ),
}


@pytest.mark.usefixtures("sealed_month3")
@pytest.mark.parametrize("change", sorted(TABLE_CHANGES))
def test_a_reply_table_with_a_case_dropped_or_changed_is_refused(
    change: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    what, cases = TABLE_CHANGES[change]
    monkeypatch.setattr(mo, f"{what.upper()}_REPLY_CASES", cases)
    refusal = _month3()
    assert refusal is not None and f"the {what}'s reply table is not the one frozen" in refusal


# --- month2's plan is as it was ----------------------------------------------------------------


@pytest.mark.usefixtures("sealed_month2")
@pytest.mark.parametrize("change", sorted(GATE_CHANGES) + sorted(PICKER_CHANGES))
def test_month2s_plan_is_unaffected(change: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Month2 and month2b pinned the picker's text only, and that stays so: a change around the
    prompt is not refused there (they are spent dev sets now, ADR-052)."""
    (GATE_CHANGES | PICKER_CHANGES)[change](monkeypatch)
    for name in (HELD_OUT_MONTH2, HELD_OUT_MONTH2B):
        for arm in mo.SEALED_ARMS:
            assert mo.check_sealed_plan(name, ALL_SETUPS[arm], **OK) is None
