"""The stub must classify every dataset ticket correctly and be fully deterministic."""

import json

import pytest

from meridian_dataset import DEFAULT_SEED, HOLDOUT_SEED, generate
from meridian_dataset.names import COLLEAGUE_TEXTS, DUPLICATE_TEXTS, NORMAL_TEXTS
from tier0.models import (
    ABOUT_SOMEONE_ELSE,
    CATEGORIES,
    OTHER,
    OWN_USERNAME,
    ModelRequest,
    StubModel,
)


def triage(text: str) -> str:
    response = StubModel().complete(ModelRequest(task="triage", system="Classify.", user=text))
    return json.loads(response.text)["category"]


@pytest.mark.parametrize("seed", [DEFAULT_SEED, HOLDOUT_SEED])
def test_every_dataset_ticket_is_triaged_correctly(seed: int) -> None:
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


def test_every_template_is_triaged_correctly() -> None:
    for template in (*NORMAL_TEXTS, *DUPLICATE_TEXTS):
        assert triage(template.format(other_email="alex.kim@meridian.example")) == OWN_USERNAME
    for template in COLLEAGUE_TEXTS:
        assert triage(template.format(target="Priya Shah")) == ABOUT_SOMEONE_ELSE


def test_unrelated_request_is_other() -> None:
    assert triage("My laptop won't turn on") == OTHER


def test_triage_output_is_json_with_known_category() -> None:
    response = StubModel().complete(ModelRequest(task="triage", system="s", user="my username?"))
    assert json.loads(response.text)["category"] in CATEGORIES


def test_same_request_gives_same_text() -> None:
    request = ModelRequest(task="triage", system="s", user="what is my login")
    model = StubModel()
    assert model.complete(request).text == model.complete(request).text


def test_reply_with_facts_contains_username() -> None:
    request = ModelRequest(task="reply", system="s", user="u", facts={"username": "alex.kim"})
    assert "alex.kim" in StubModel().complete(request).text


def test_reply_without_facts_is_handoff() -> None:
    response = StubModel().complete(ModelRequest(task="reply", system="s", user="u"))
    assert response.text == "I've passed your request to the IT team. Someone will follow up here."


def test_token_counts_and_ids() -> None:
    model = StubModel()
    first = model.complete(ModelRequest(task="reply", system="be nice", user="hello there"))
    second = model.complete(ModelRequest(task="reply", system="be nice", user="hello there"))
    assert first.input_tokens == 4
    assert first.output_tokens > 0
    assert first.finish_reason == "stop"
    assert (first.response_id, second.response_id) == ("stub-1", "stub-2")
