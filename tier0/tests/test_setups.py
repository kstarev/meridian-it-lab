"""Milestone 3's setups: models per task, their prompts, and the routing adapter."""

import json

import pytest

from tier0 import messages
from tier0.evals import __main__ as cli
from tier0.evals.setups import SETUPS
from tier0.models import ModelRequest, ModelResponse
from tier0.models.routed import RoutedModel


class Fake:
    provider = "ollama"
    temperature = 0.5
    supports_seed = True

    def __init__(self, model: str) -> None:
        self.model = model
        self.seed: int | None = None
        self.tasks: list[str] = []
        self.texts: list[str] = []

    def describe(self) -> dict[str, str]:
        return {"model_id": self.model[:4], "model_digest": self.model, "ollama_version": "1"}

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.tasks.append(request.task)
        self.texts.append(request.user)
        text = (
            '{"category": "own_username"}'
            if request.task == "triage"
            else '{"own_account_only": true}'
        )
        return ModelResponse(text, self.model, 1, 1, "stop", None)


def test_the_four_setups() -> None:
    assert set(SETUPS) == {"llama3.2", "llama3.1:8b", "mixed", "haiku"}
    assert SETUPS["mixed"].label == "llama3.2+llama3.1:8b"
    assert (
        SETUPS["haiku"].provider == "claude" and SETUPS["haiku"].triage_model == "claude-haiku-5-5"
    )


def test_the_routed_model_sends_each_task_to_its_own_model() -> None:
    triage, check = Fake("llama3.2"), Fake("llama3.1:8b")
    routed = RoutedModel(triage=triage, check=check)
    routed.seed = 1001
    assert triage.seed == check.seed == 1001 and routed.supports_seed
    routed.complete(ModelRequest(task="triage", system="s", user="u"))
    routed.complete(ModelRequest(task="own_account_check", system="s", user="u"))
    assert triage.tasks == ["triage"] and check.tasks == ["own_account_check"]
    assert routed.model == "llama3.2+llama3.1:8b"
    described = routed.describe()
    assert described["model_digest"] == "llama3.2 (triage), llama3.1:8b (check)"


def test_a_setups_prompts_reach_the_model(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    seen: list[ModelRequest] = []

    class Recording(Fake):
        def complete(self, request: ModelRequest) -> ModelResponse:
            seen.append(request)
            return super().complete(request)

    setup = SETUPS["llama3.2"]
    monkeypatch.setattr(
        type(setup), "factory", lambda self, temperature=0.5: lambda: Recording("llama3.2")
    )
    monkeypatch.setattr("tier0.evals.setups.M3_TRIAGE_VERSIONS", {"T1": "TRIAGE PROMPT UNDER TEST"})
    monkeypatch.setattr("tier0.evals.setups.M3_CHECK_VERSIONS", {"C1": "CHECK PROMPT UNDER TEST"})
    assert (
        cli.main(["--setup", "llama3.2", "--seed", "1108", "--k", "1", "--out", str(tmp_path)]) == 0
    )
    systems = {r.task: r.system for r in seen}
    assert systems == {
        "triage": "TRIAGE PROMPT UNDER TEST",
        "own_account_check": "CHECK PROMPT UNDER TEST",
    }
    report = json.loads((tmp_path / "report.json").read_text())
    import hashlib

    assert report["triage_prompt_sha256"] == hashlib.sha256(b"TRIAGE PROMPT UNDER TEST").hexdigest()
    assert report["check_prompt_sha256"] == hashlib.sha256(b"CHECK PROMPT UNDER TEST").hexdigest()
    assert messages.TRIAGE_SYSTEM != "TRIAGE PROMPT UNDER TEST"  # nothing global changed


def test_real_models_get_one_untimed_warm_up_call_per_task(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    made: list[Fake] = []

    def make() -> Fake:
        made.append(Fake("llama3.2"))
        return made[-1]

    setup = SETUPS["llama3.2"]
    monkeypatch.setattr(type(setup), "factory", lambda self, temperature=0.5: make)
    args = ["--setup", "llama3.2", "--seed", "1108", "--k", "1", "--out", str(tmp_path)]
    assert cli.main(args) == 0
    assert made[0].tasks == ["triage", "own_account_check"]  # the warm-up model
    assert all(cli.WARM_UP_TEXT in t for t in made[0].texts)
    report = json.loads((tmp_path / "report.json").read_text())
    assert len(report["runs_detail"]) == 6  # the warm-up isn't a run
    made.clear()
    assert cli.main([*args, "--no-warm-up"]) == 0
    assert not any(cli.WARM_UP_TEXT in t for m in made for t in m.texts)
