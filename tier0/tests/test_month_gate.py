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


# --- the report of a run with the gate (found in the 3f review, 2026-11-20) -----------------


class _GateSaysNoOrFails:
    """Triage says "other"; the gate says no, then replies off the schema, in turn. So no
    how-to ticket reaches the picker, and both kinds of gate hand-off occur."""

    provider = "fake"
    model = "fake-1"
    temperature = None
    supports_seed = False
    seed = None

    def __init__(self) -> None:
        self.gate_calls = 0

    def describe(self) -> dict[str, str]:
        return {}

    def complete(self, request: ModelRequest) -> ModelResponse:
        if request.task == "triage":
            return ModelResponse('{"category": "other"}', self.model, 300, 8, "stop", None)
        assert request.task == "howto_gate", request.task
        self.gate_calls += 1
        reply = NO if self.gate_calls % 2 else "nope"
        return ModelResponse(reply, self.model, 320, 9, "stop", None)


_PICKED: list[str] = []


def _hands_off(setup: object, model: object, prompt: str | None) -> mo.Picker:
    def stage(text: str) -> tuple[str, str | None]:
        _PICKED.append(text)
        return mo.STAGE_HAND_OFF, None

    return mo.Picker("knowledge", stage, prompt, "0" * 64)


def _gate_run(tmp_path: Path, *gate: str) -> tuple[dict, str, list[dict]]:
    argv = ["run", "--set", "tuning_month", "--setup", "haiku-3d2", "--k", "1"]
    argv += ["--picker", "knowledge", "--picker-prompt", "KS6A-H", *gate]
    _PICKED.clear()
    code = mo.main(
        [*argv, "--out", str(tmp_path)],
        make_model=_GateSaysNoOrFails,  # type: ignore[arg-type]
        make_picker=_hands_off,  # type: ignore[arg-type]
        make_picker_model=_GateSaysNoOrFails,  # type: ignore[arg-type]
        samples=50,
    )
    assert code == 0  # everything was handed off: nothing wrongly answered
    if gate:
        assert _PICKED == []  # the gate never said yes, so the picker was never asked
    (out,) = tmp_path.glob("*.json")
    result = json.loads(out.read_text(encoding="utf-8"))
    md = out.with_suffix(".md").read_text(encoding="utf-8")
    lines = out.with_name(out.stem + ".runs.jsonl").read_text(encoding="utf-8").splitlines()
    return result, md, [json.loads(x) for x in lines[1:]]


def _recorded_command(md: str) -> str:
    line = next(x for x in md.splitlines() if x.startswith("Generated by `"))
    return line.removeprefix("Generated by `").removesuffix("`. Do not edit by hand.")


def test_the_recorded_command_names_the_gate(tmp_path: Path) -> None:
    """The summary's command must run the same configuration again. Before the fix it dropped
    --gate, so month3's summary would have recorded a command its frozen plan refuses."""
    _, md, _ = _gate_run(tmp_path, "--gate", "G3")
    command = _recorded_command(md)
    assert " --gate G3 " in f"{command} "
    _, md, _ = _gate_run(tmp_path / "none")
    assert "--gate" not in _recorded_command(md)


def test_the_report_counts_how_to_runs_the_gate_handed_off(tmp_path: Path) -> None:
    """Month3's rule 4 reports how-to runs the gate handed off, with a template-level interval."""
    result, md, records = _gate_run(tmp_path, "--gate", "G3")
    how_to = {t["ticket_id"] for t in result["tickets"] if t["expected"] == "answer_guide"}
    stages = Counter(r["stage"] for r in records if r["ticket_id"] in how_to)
    g = result["scores"]["how_to_gate"]
    assert g["how_to_runs"] == len(how_to) == sum(stages.values())
    assert (g["gate_no_runs"], g["gate_failed_runs"], g["screen_runs"]) == (
        stages[mo.GATE_NO],
        stages[mo.GATE_FAILED],
        stages[mo.SCREEN],
    )
    assert g["gate_no_runs"] > 0 and g["gate_failed_runs"] > 0
    handed = (g["gate_no_runs"] + g["gate_failed_runs"]) / g["how_to_runs"]
    assert g["handed_off"]["value"] == round(handed, 4)
    assert g["handed_off"]["tickets"] == len(how_to)
    assert "**With the how-to gate G3** on claude-haiku-5-5" in md
    assert f"| How-to runs the how-to gate handed off ({len(how_to)} tickets) |" in md
    assert f"{g['gate_no_runs']} said no and {g['gate_failed_runs']} failed" in md


def test_without_a_gate_the_report_has_no_gate_lines(tmp_path: Path) -> None:
    result, md, _ = _gate_run(tmp_path)
    assert "how-to gate" not in md
    assert result["scores"]["how_to_gate"]["gate_no_runs"] == 0


@pytest.mark.parametrize("arm", ["haiku-3d2", "sonnet-3d2"])
def test_the_published_rehearsal_still_renders_as_the_run_wrote_it(arm: str) -> None:
    """The 3f rehearsal was scored before the gate's counts existed: its summary is unchanged."""
    evals = Path(__file__).resolve().parents[2] / "docs" / "evals"
    result = json.loads((evals / f"2026-11-20-m3f-rehearsal-{arm}.json").read_text())
    md = (evals / f"2026-11-20-m3f-rehearsal-{arm}.md").read_text(encoding="utf-8")
    assert "how_to_gate" not in result["scores"]
    assert mo.render(result, _recorded_command(md)) == md
