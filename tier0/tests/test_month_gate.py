"""Milestone 3f's how-to gate in the month runner: the screen first, then the gate on the arm's
own model, then the picker. Each can only hand off. No model is called."""

import json
from collections import Counter
from pathlib import Path

import pytest

from tier0.evals import month as mo
from tier0.evals.setups import ALL_SETUPS
from tier0.models import ModelError, ModelRequest, ModelResponse


class Fake:
    """Triage says `label`; every other call gets `check` as its reply."""

    provider = "fake"
    model = "fake-1"
    temperature = None

    def __init__(self, label: str, *, check: str = "", fail: frozenset[str] = frozenset()) -> None:
        self.label, self.check, self.fail = label, check, fail
        self.calls: Counter[str] = Counter()

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls[request.task] += 1
        if request.task in self.fail:
            raise ModelError("down")
        if request.task == "triage":
            return ModelResponse(
                json.dumps({"category": self.label}), self.model, 300, 8, "stop", None
            )
        return ModelResponse(self.check, self.model, 320, 9, "stop", None)


SHIP = ALL_SETUPS["haiku-3d2"]
HOW_TO = "how do I connect my work laptop to the office wifi in Lisbon"
ACCESS = "I need access to the Sales shared drive please"  # the code-only screen fires on this
YES = '{"plain_how_to": true}'
NO = '{"plain_how_to": false}'


class _Asked:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def __call__(self, text: str) -> tuple[str, str | None]:
        self.texts.append(text)
        return ("guide", "KB-001")


def _route(model: Fake, text: str, picker: _Asked, gate: mo.Gate | None) -> mo.Route:
    return mo.route_ticket(
        text,
        sender_name="Ben Tan",
        sender_email="ben.tan@meridian.example",
        model=model,
        setup=SHIP,
        second_stage=picker,
        gate=gate,
    )


GATE = mo.gate_for("G2")


def test_a_yes_lets_the_picker_answer() -> None:
    model, picker = Fake("other", check=YES), _Asked()
    route = _route(model, HOW_TO, picker, GATE)
    assert route == mo.Route("answer_guide", "KB-001", mo.PICKER_GUIDE, "other")
    assert picker.texts == [HOW_TO]
    assert model.calls == {"triage": 1, "howto_gate": 1}


def test_a_no_hands_off_and_the_picker_is_never_asked() -> None:
    model, picker = Fake("other", check=NO), _Asked()
    assert _route(model, HOW_TO, picker, GATE).stage == mo.GATE_NO
    assert picker.texts == []


def test_a_reply_off_the_schema_or_a_failed_call_hands_off() -> None:
    for model in (Fake("other", check="yes"), Fake("other", fail=frozenset({"howto_gate"}))):
        picker = _Asked()
        route = _route(model, HOW_TO, picker, GATE)
        assert (route.outcome, route.stage) == ("hand_off", mo.GATE_FAILED)
        assert picker.texts == []


def test_the_screen_runs_first_and_saves_both_calls() -> None:
    model, picker = Fake("other", check=YES), _Asked()
    route = _route(model, ACCESS, picker, GATE)
    assert (route.outcome, route.stage) == ("hand_off", mo.SCREEN)
    assert picker.texts == [] and model.calls == {"triage": 1}


def test_without_a_gate_the_path_is_as_before() -> None:
    model, picker = Fake("other"), _Asked()
    route = _route(model, ACCESS, picker, None)
    assert route.stage == mo.PICKER_GUIDE and picker.texts == [ACCESS]
    assert model.calls == {"triage": 1}


def test_the_gate_is_named_in_the_result_file_name() -> None:
    stem = mo.result_stem("tuning_month", "haiku-3d2", "knowledge", 3, 1130, gate="G2")
    assert stem == "month-tuning_month-haiku-3d2-picker-knowledge-k3-seed1130-gate-G2"
    assert "gate" not in mo.result_stem("tuning_month", "haiku-3d2", "knowledge", 3, 1130)


def test_a_gate_needs_the_picker_and_a_known_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = ["run", "--set", "tuning_month", "--setup", "haiku-3d2", "--k", "1"]
    env = {"TIER0_CLAUDE_BUDGET_ITEM": mo.BUDGET_ITEM}
    assert mo.main([*base, "--gate", "G2", "--out", str(tmp_path)], env=env) == 2
    assert "--picker knowledge" in capsys.readouterr().err
    argv = [*base, "--picker", "knowledge", "--picker-prompt", "KS6A-H", "--gate", "G9"]
    assert mo.main([*argv, "--out", str(tmp_path)], env=env) == 2
    assert "no how-to gate" in capsys.readouterr().err


def test_month3_is_frozen_to_ks6a_h_behind_g3() -> None:
    """The selection rule's pick (docs/evals/2026-11-20-m3f-gate-dev.md), frozen before the
    rehearsal. Any other gate, none, or a changed G3 text is refused."""
    import hashlib

    from tier0.knowledge.gate import GATE_VERSIONS

    plan = mo.SEALED_PLANS["held_out_month3"]
    assert (plan.picker_prompt, plan.prompt_cache, plan.gate) == ("KS6A-H", True, "G3")
    assert hashlib.sha256(GATE_VERSIONS["G3"].encode()).hexdigest() == plan.gate_sha256
    assert mo.SEALED_PLANS["held_out_month2"].gate is None


def test_a_sealed_run_without_its_frozen_gate_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from tier0.knowledge import gate as gate_module

    monkeypatch.setattr(mo, "SEALED_SETS", frozenset({"held_out_month3"}))
    kw = {"picker": "knowledge", "picker_prompt": "KS6A-H", "own_model_picker": False, "k": 3}
    assert mo.check_sealed_plan("held_out_month3", SHIP, gate="G3", **kw) is None
    for other in (None, "G1", "G2"):
        refusal = mo.check_sealed_plan("held_out_month3", SHIP, gate=other, **kw)
        assert refusal is not None and "G3" in refusal
    changed = dict(gate_module.GATE_VERSIONS, G3=gate_module.GATE_VERSIONS["G3"] + " ")
    monkeypatch.setattr(gate_module, "GATE_VERSIONS", changed)
    refusal = mo.check_sealed_plan("held_out_month3", SHIP, gate="G3", **kw)
    assert refusal is not None and "SHA-256" in refusal
