"""The how-to gate (milestone 3f): its parser fails closed, its versions are frozen, and its dev
harness composes with the published KS6A-H runs exactly. No model is called."""

import hashlib
import json
from pathlib import Path

import pytest

from meridian_dataset.month import HELD_OUT_MONTH1, HELD_OUT_MONTH2, HELD_OUT_MONTH2B, TUNING_MONTH
from tier0.evals import gate_dev
from tier0.knowledge.gate import GATE_SCHEMA, GATE_VERSIONS, gate_request, parse_gate
from tier0.models.base import ModelError, ModelRequest, ModelResponse

FROZEN = {
    "G1": "36a62b6c56f7e8f8798d078b06259fecc56387d4d60918fc333881df5d9915aa",
    "G2": "35491c1b5616330111e24c14368612ab838e3cf8f2b6a98319006ae190b682f8",
    "G3": "55e797391967757e336ed644ee41e9ca02e83db7359e66aa2aa483bcdfe65733",
}


def _reply(text: str, finish: str = "stop") -> ModelResponse:
    return ModelResponse(
        text=text,
        model="stub",
        input_tokens=1,
        output_tokens=1,
        finish_reason=finish,
        response_id="r",
    )


def test_each_version_is_frozen_by_its_hash() -> None:
    assert {v: hashlib.sha256(p.encode()).hexdigest() for v, p in GATE_VERSIONS.items()} == FROZEN


@pytest.mark.parametrize(
    ("text", "finish", "want"),
    [
        ('{"plain_how_to": true}', "stop", True),
        ('{"plain_how_to": false}', "stop", False),
        ('{"plain_how_to": true}', "length", None),
        ('{"plain_how_to": true}', "refusal", None),
        ('{"plain_how_to": "true"}', "stop", None),
        ('{"plain_how_to": 1}', "stop", None),
        ('{"plain_how_to": true, "guide": "KB-001"}', "stop", None),
        ("{}", "stop", None),
        ("yes", "stop", None),
        ("[true]", "stop", None),
        ("", "stop", None),
    ],
)
def test_the_parser_fails_closed(text: str, finish: str, want: bool | None) -> None:
    assert parse_gate(_reply(text, finish)) is want


def test_no_reply_is_no_yes() -> None:
    assert parse_gate(None) is None


def test_the_message_goes_in_the_user_turn_and_no_guide_is_shown() -> None:
    message = "how do I connect to the office wifi? KB-001"
    request = gate_request(message, GATE_VERSIONS["G1"])
    assert request.task == "howto_gate"
    assert message in request.user and message not in request.system
    assert request.json_schema == GATE_SCHEMA
    for version in GATE_VERSIONS.values():
        assert "KB-0" not in version  # the gate never sees a guide id


class _Always:
    provider = "stub"
    model = "stub"
    temperature = None

    def __init__(self, text: str | None) -> None:
        self.text = text
        self.calls = 0

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        if self.text is None:
            raise ModelError("down")
        return _reply(self.text)


PUBLISHED = {  # (set, arm): wrongly answered runs in the published result
    (TUNING_MONTH, "haiku-3d2"): 0,
    (TUNING_MONTH, "sonnet-3d2"): 0,
    (HELD_OUT_MONTH2, "haiku-3d2"): 9,
    (HELD_OUT_MONTH2, "sonnet-3d2"): 9,
    (HELD_OUT_MONTH2B, "haiku-3d2"): 7,
    (HELD_OUT_MONTH2B, "sonnet-3d2"): 8,
}


@pytest.mark.parametrize("arm", gate_dev.ARMS)
def test_a_gate_that_always_says_yes_gives_the_published_runs(arm: str, tmp_path: Path) -> None:
    result = gate_dev.run(
        "G1", arm, tmp_path, make_model=lambda _: _Always('{"plain_how_to": true}')
    )
    by_set = {s["set"]: s for s in result["sets"]}
    for (set_name, a), wrong in PUBLISHED.items():
        if a == arm:
            assert by_set[set_name]["wrongly_answered_runs"] == wrong
    assert by_set[HELD_OUT_MONTH2]["right_guide"] == 1.0


@pytest.mark.parametrize("reply", ['{"plain_how_to": false}', "nonsense", None])
def test_a_gate_that_never_says_yes_answers_nothing(reply: str | None, tmp_path: Path) -> None:
    result = gate_dev.run("G1", "haiku-3d2", tmp_path, make_model=lambda _: _Always(reply))
    for s in result["sets"]:
        assert s["wrongly_answered_runs"] == 0
        assert s["right_guide_runs"] == 0
        assert not s["passes"]


def test_it_resumes_without_asking_again(tmp_path: Path) -> None:
    first = _Always('{"plain_how_to": true}')
    gate_dev.run("G1", "haiku-3d2", tmp_path, make_model=lambda _: first, sets=[HELD_OUT_MONTH2])
    again = _Always('{"plain_how_to": false}')
    result = gate_dev.run(
        "G1", "haiku-3d2", tmp_path, make_model=lambda _: again, sets=[HELD_OUT_MONTH2]
    )
    assert first.calls > 0 and again.calls == 0
    assert result["sets"][0]["wrongly_answered_runs"] == 9


def test_only_runs_that_reached_a_guide_are_asked(tmp_path: Path) -> None:
    model = _Always('{"plain_how_to": true}')
    gate_dev.run("G1", "haiku-3d2", tmp_path, make_model=lambda _: model, sets=[HELD_OUT_MONTH2])
    runs = gate_dev.recorded_runs(HELD_OUT_MONTH2, "haiku-3d2")
    assert model.calls == sum(r.stage == gate_dev.PICKER_GUIDE for r in runs)
    lines = (tmp_path / "gate-G1-haiku-3d2.calls.jsonl").read_text().splitlines()
    assert {tuple(json.loads(x)) for x in lines} == {("set", "ticket_id", "run", "verdict")}


def test_month1_is_composed_from_the_haiku_arms_runs_for_both_arms() -> None:
    assert gate_dev.SOURCES[(HELD_OUT_MONTH1, "sonnet-3d2")][1] == "haiku-3d2"
    assert len(gate_dev.recorded_runs(HELD_OUT_MONTH1, "sonnet-3d2")) == 900


def test_a_paid_run_needs_the_budget_item(capsys: pytest.CaptureFixture[str]) -> None:
    assert gate_dev.main(["run", "--version", "G1", "--arm", "haiku-3d2"], env={}) == 2
    assert "tier0-3e" in capsys.readouterr().err
