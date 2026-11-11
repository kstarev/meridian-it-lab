"""Milestone 3 prompt selection: tuning instances, scoring, the fixed rule, the estimate."""

import json

import pytest

from tier0.evals import m3_selection as sel
from tier0.evals.prompt_history import M3_CHECK_VERSIONS, M3_TRIAGE_VERSIONS
from tier0.models import ModelRequest, ModelResponse


class Perfect:
    """Answers every tuning case correctly, by looking at what the case expects."""

    provider = "ollama"
    supports_seed = True

    def __init__(self) -> None:
        self.seed: int | None = None
        self.seeds: list[int | None] = []
        self.answers = {c.text: c for c in sel.instances()}

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.seeds.append(self.seed)
        text = request.user.split("Message:\n", 1)[-1]
        case = self.answers[text]
        if request.task == "triage":
            body = json.dumps({"category": case.triage_expected})
        else:
            body = json.dumps({"own_account_only": case.check_expected})
        return ModelResponse(body, "m", 100, 5, "stop", None)


def test_instances_are_tuning_only_and_grouped() -> None:
    cases = sel.instances()
    groups = {
        g: sum(c.group == g for c in cases) for g in ("everyday", "self_described", "colleague")
    }
    assert groups == {"everyday": 46, "self_described": 6, "colleague": 14}
    assert sum(c.unnamed for c in cases) == 7
    assert all("{" not in c.text for c in cases)


@pytest.mark.parametrize("version", ["T1", "C1"])
def test_a_perfect_model_scores_full_marks(version: str) -> None:
    model = Perfect()
    result = sel.score("llama3.2", version, k=2, model=model)
    assert result["correct"] == result["total"]
    assert result["total"]["everyday"] == 2 * 46 and result["total"]["colleague_unnamed"] == 14
    assert set(model.seeds) == {1001, 1002}
    assert result["cost_usd"] == 0 and result["wrong"] == {}
    prompts = M3_TRIAGE_VERSIONS if version == "T1" else M3_CHECK_VERSIONS
    assert result["prompt_chars"] == len(prompts[version])


def _r(version: str, colleague: int, everyday: int, chars: int) -> dict:
    return {
        "version": version,
        "correct": {"colleague": colleague, "everyday": everyday},
        "prompt_chars": chars,
    }


def test_the_rule_keeps_colleague_catches_then_maximises_everyday() -> None:
    # T2 answers more everyday requests but catches fewer colleagues: not allowed.
    results = [_r("T1", 40, 100, 900), _r("T2", 39, 130, 800), _r("T3", 40, 120, 950)]
    assert sel.choose(results) == "T3"
    # A tie on everyday answers goes to the shorter prompt.
    results.append(_r("T4", 41, 120, 700))
    assert sel.choose(results) == "T4"
    # Version 1 wins when nothing beats it.
    assert sel.choose([_r("C1", 40, 100, 900), _r("C2", 30, 140, 100)]) == "C1"
    with pytest.raises(ValueError, match="version 1"):
        sel.choose([_r("T2", 1, 1, 1)])


def test_the_estimate_uses_the_full_budget_and_makes_no_call() -> None:
    est = sel.estimate_cost("claude-haiku-5-5")
    assert est["tuning_selection_calls"] == 66 * 3 * 2 * sel.MAX_VERSIONS
    assert est["agent_calls_max"] == (6 + 6 + 42 + 54) * 3 * 2
    assert 0 < est["estimated_usd"] < 2.0


def test_claude_scoring_gets_the_api_only_guard_and_ollama_stays_local() -> None:
    assert sel.provider_of("claude-haiku-5-5") == "claude"
    assert sel.provider_of("llama3.2") == sel.provider_of("llama3.1:8b") == "ollama"
