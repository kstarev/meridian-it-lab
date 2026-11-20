"""The stub must classify every tuning-set ticket correctly and be fully deterministic.

Only tuning wording is checked here. The stub's keyword rules were written for that
wording, and checking them against held-out wording would be tuning on the held-out
set (ADR-013).
"""

import json

import pytest

from meridian_dataset import DEFAULT_SEED, generate
from meridian_dataset.month import TUNING_MONTH, load_templates
from meridian_dataset.names import TUNING_POOL
from tier0.knowledge.gate import GATE_VERSIONS, gate_request, parse_gate
from tier0.knowledge.wiring import HOW_TO_GATE_VERSION
from tier0.models import (
    ABOUT_SOMEONE_ELSE,
    CATEGORIES,
    OTHER,
    OWN_USERNAME,
    ModelError,
    ModelRequest,
    StubModel,
)


def triage(text: str) -> str:
    response = StubModel().complete(ModelRequest(task="triage", system="Classify.", user=text))
    return json.loads(response.text)["category"]


@pytest.mark.parametrize("seed", [DEFAULT_SEED, 1, 2, 3])
def test_every_tuning_ticket_is_triaged_correctly(seed: int) -> None:
    world = generate(seed)
    expected = {
        "normal": OWN_USERNAME,
        "duplicate_name": OWN_USERNAME,
        "colleague_lookup": ABOUT_SOMEONE_ELSE,
    }
    checked = 0
    for ticket in world.tickets:
        scenario = world.answer_key.entry(ticket.id).scenario
        if scenario in expected:
            assert triage(ticket.text) == expected[scenario], ticket.id
            checked += 1
    assert checked > 0


def test_every_tuning_template_is_triaged_correctly() -> None:
    for template in (*TUNING_POOL.normal, *TUNING_POOL.duplicate):
        assert triage(template.format(other_email="alex.kim@meridian.example")) == OWN_USERNAME
    for template in TUNING_POOL.colleague:
        assert (
            triage(template.format(target="Priya Shah", target_guess="priya.shah"))
            == ABOUT_SOMEONE_ELSE
        )


def test_unrelated_request_is_other() -> None:
    assert triage("My laptop won't turn on") == OTHER


def test_triage_output_is_json_with_known_category() -> None:
    response = StubModel().complete(ModelRequest(task="triage", system="s", user="my username?"))
    assert json.loads(response.text)["category"] in CATEGORIES


def test_same_request_gives_same_text() -> None:
    request = ModelRequest(task="triage", system="s", user="what is my login")
    model = StubModel()
    assert model.complete(request).text == model.complete(request).text


def test_unknown_task_is_a_model_error() -> None:
    # The model only classifies; it never writes replies (ADR-028).
    with pytest.raises(ModelError):
        StubModel().complete(ModelRequest(task="reply", system="s", user="u"))  # type: ignore[arg-type]


def test_token_counts_and_ids() -> None:
    model = StubModel()
    first = model.complete(ModelRequest(task="triage", system="be nice", user="hello there"))
    second = model.complete(ModelRequest(task="triage", system="be nice", user="hello there"))
    assert first.input_tokens == 4
    assert first.output_tokens > 0
    assert first.finish_reason == "stop"
    assert (first.response_id, second.response_id) == ("stub-1", "stub-2")


# --- the how-to gate (milestone 3f), so offline how-to questions still reach the picker --------


def gate(text: str) -> bool | None:
    system = GATE_VERSIONS[HOW_TO_GATE_VERSION or ""]
    return parse_gate(StubModel().complete(gate_request(text, system)))


def test_the_gate_says_yes_exactly_for_what_its_own_triage_calls_other() -> None:
    assert gate("How do I connect to the office Wi-Fi?") is True
    # The stub can't tell a fault report from a how-to question; the message screen and a real
    # model's gate do. It only proves the wiring.
    assert gate("My laptop won't turn on") is True
    assert gate("what is my login") is False
    assert gate("What is my manager's username?") is False


def test_the_gate_agrees_with_triage_on_every_tuning_ticket_and_month_template() -> None:
    messages = [
        *((t.id, t.text) for t in generate(DEFAULT_SEED).tickets),
        *((t.id, t.text) for t in load_templates(TUNING_MONTH)),
    ]
    assert {triage(text) == OTHER for _, text in messages} == {True, False}
    for message_id, text in messages:
        assert gate(text) is (triage(text) == OTHER), message_id


def test_the_gate_reply_is_exactly_the_schema_and_the_same_every_time() -> None:
    request = gate_request("How do I set up the VPN?", GATE_VERSIONS["G3"])
    model = StubModel()
    first, second = model.complete(request), model.complete(request)
    assert first.text == second.text == '{"plain_how_to": true}'
    assert first.finish_reason == "stop"
