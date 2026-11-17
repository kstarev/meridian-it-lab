"""The month runner's opt-in prompt cache: counting the cache tokens, pricing them, the names
and the refusals. Fake models and the tuning month only: no paid call, and no test here loads,
sizes or runs the sealed month.
"""

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from meridian_dataset.month import HELD_OUT_MONTH1, TUNING_MONTH
from tier0.agent import triage_request
from tier0.evals import month as mo
from tier0.evals.setups import ALL_SETUPS
from tier0.models import ModelRequest, ModelResponse
from tier0.models.base import OWN_USERNAME

# This fake answers out-of-scope tickets, so a finished run exits 1 (the safety flag), not 0.
SCORED = (0, 1)
SETUP = "haiku-3d2"
STEM = ("month-tuning_month-haiku-3d2-picker-knowledge-k1-seed1130", "")


class CachingFake:
    """Reports fixed token counts per call, cache tokens included. Triage alternates between
    an own-username request and "other", so every routed task is called."""

    temperature = None
    supports_seed = False

    def __init__(
        self, provider: str, model: str, tokens: tuple[int, int, int, int], *, cache: bool = True
    ) -> None:
        self.provider = provider
        self.model = model
        self.tokens = tokens
        self.cache = cache
        self.seed: int | None = None
        self.calls: Counter[str] = Counter()

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls[request.task] += 1
        if request.task == "triage":
            category = OWN_USERNAME if self.calls["triage"] % 2 else "other"
            text = json.dumps({"category": category})
        elif request.task == "own_account_check":
            text = json.dumps({"own_account_only": True})
        else:
            text = '{"kind": "hand_off"}'
        i, o, w, r = self.tokens
        extra = {"cache_write_tokens": w, "cache_read_tokens": r} if self.cache else {}
        return ModelResponse(text, self.model, i, o, "stop", None, **extra)

    def describe(self) -> dict[str, str]:
        return {}


def _haiku(cache: bool = True) -> CachingFake:
    return CachingFake("anthropic", "claude-haiku-5-5", (80, 5, 0, 800), cache=cache)


def _sonnet(cache: bool = True) -> CachingFake:
    return CachingFake("anthropic", "claude-sonnet-5-5", (100, 20, 0, 2600), cache=cache)


def _picker(setup: Any, model: Any, prompt: str | None) -> mo.Picker:
    def stage(text: str) -> tuple[str, str | None]:
        model.complete(ModelRequest(task="guide_pick", system="picker", user=text))
        return mo.STAGE_HAND_OFF, None

    return mo.Picker("knowledge", stage, prompt, "0" * 64)


def _run(tmp: Path, *extra: str) -> int:
    argv = ["run", "--set", TUNING_MONTH, "--setup", SETUP, "--k", "1", "--picker", "knowledge"]
    return mo.main(
        [*argv, *extra, "--out", str(tmp)],
        make_model=_haiku,  # type: ignore[arg-type]
        make_picker=_picker,
        make_picker_model=_sonnet,  # type: ignore[arg-type]
        samples=50,
    )


def _stem(cache: bool) -> str:
    return mo.result_stem(TUNING_MONTH, SETUP, "knowledge", 1, 1130, prompt_cache=cache)


def _expected_cost(calls: dict[str, int]) -> float:
    total = 0.0
    for task, n in calls.items():
        if task == "guide_pick":
            total += n * (100 * 2.00 + 2600 * 0.10 + 20 * 10.00) / 1e6
        else:
            total += n * (80 * 0.10 + 800 * 0.01 + 5 * 0.50) / 1e6
    return total


def _load(tmp: Path, cache: bool) -> tuple[dict[str, Any], str, list[str]]:
    stem = _stem(cache)
    result = json.loads((tmp / f"{stem}.json").read_text(encoding="utf-8"))
    md = (tmp / f"{stem}.md").read_text(encoding="utf-8")
    lines = (tmp / f"{stem}.runs.jsonl").read_text(encoding="utf-8").splitlines()
    return result, md, lines


def test_counted_model_counts_cache_tokens_per_task() -> None:
    counted = mo.CountedModel(CachingFake("anthropic", "m", (80, 5, 30, 800)))  # type: ignore[arg-type]
    counted.complete(triage_request("hello"))
    counted.complete(triage_request("again"))
    counted.complete(ModelRequest(task="guide_pick", system="s", user="u"))
    assert counted.cache_write_tokens == {"triage": 60, "guide_pick": 30}
    assert counted.cache_read_tokens == {"triage": 1600, "guide_pick": 800}
    plain = mo.CountedModel(CachingFake("fake", "m", (80, 5, 0, 0), cache=False))  # type: ignore[arg-type]
    plain.complete(triage_request("hello"))
    assert plain.cache_write_tokens["triage"] == 0 and plain.cache_read_tokens["triage"] == 0


def test_a_run_with_the_prompt_cache_counts_prices_and_says_so(tmp_path: Path) -> None:
    assert _run(tmp_path, "--prompt-cache") in SCORED
    stem = _stem(True)
    assert stem.endswith("-cache")
    for suffix in (".json", ".md", ".runs.jsonl"):
        assert (tmp_path / f"{stem}{suffix}").exists()
    result, md, lines = _load(tmp_path, True)
    c = result["config"]
    assert c["prompt_cache"] is True
    assert list(c["cache_read_tokens"]) == list(c["calls"])
    assert list(c["cache_write_tokens"]) == list(c["calls"])
    assert {"triage", "own_account_check", "guide_pick"} <= set(c["calls"])
    assert c["cost_usd"] == pytest.approx(_expected_cost(c["calls"]))
    assert json.loads(lines[0])["header"]["prompt_cache"] is True
    assert any(line.startswith("- Prompt cache on:") for line in md.splitlines())
    assert "--prompt-cache" in md


def test_a_run_without_the_flag_is_unchanged_and_still_prices_cache_tokens(
    tmp_path: Path,
) -> None:
    assert _run(tmp_path) in SCORED
    assert "-cache" not in _stem(False)
    result, md, lines = _load(tmp_path, False)
    c = result["config"]
    assert not {"prompt_cache", "cache_write_tokens", "cache_read_tokens"} & set(c)
    assert "prompt_cache" not in json.loads(lines[0])["header"]
    assert "Prompt cache" not in md and "--prompt-cache" not in md
    assert c["cost_usd"] == pytest.approx(_expected_cost(c["calls"]))


@pytest.mark.usefixtures("sealed_month1")
def test_the_prompt_cache_is_refused_on_the_sealed_set_before_anything_is_built(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def never() -> Any:
        raise AssertionError("no model is built on a refusal")

    argv = [
        "run",
        "--set",
        HELD_OUT_MONTH1,
        f"--final-test={HELD_OUT_MONTH1}",
        "--setup",
        SETUP,
        "--k",
        "3",
        "--picker",
        "knowledge",
        "--prompt-cache",
        "--out",
        str(tmp_path),
    ]
    assert mo.main(argv, make_model=never) == 2
    err = capsys.readouterr().err
    assert err.startswith("refused:") and "--prompt-cache" in err
    assert list(tmp_path.iterdir()) == []


@pytest.mark.usefixtures("sealed_month1")
def test_check_prompt_cache_refuses_only_the_sealed_set_and_non_claude_setups() -> None:
    setup = ALL_SETUPS[SETUP]
    assert mo.check_prompt_cache(TUNING_MONTH, setup, True) is None
    assert mo.check_prompt_cache(HELD_OUT_MONTH1, setup, False) is None
    assert "--prompt-cache" in (mo.check_prompt_cache(HELD_OUT_MONTH1, setup, True) or "")
    local = next(s for s in ALL_SETUPS.values() if s.provider != "claude")
    assert mo.check_prompt_cache(TUNING_MONTH, local, False) is None
    assert "Claude setups" in (mo.check_prompt_cache(TUNING_MONTH, local, True) or "")


def test_the_prompt_cache_is_refused_for_a_local_setup(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    local = next(
        s for s in ALL_SETUPS.values() if s.provider != "claude" and s.split_prompt is None
    )
    argv = ["run", "--set", TUNING_MONTH, "--setup", local.name, "--k", "1", "--prompt-cache"]
    assert mo.main([*argv, "--out", str(tmp_path)], make_model=_haiku) == 2  # type: ignore[arg-type]
    assert capsys.readouterr().err.startswith("refused:")
    assert list(tmp_path.iterdir()) == []


def test_the_cache_suffix_is_last_in_the_result_stem() -> None:
    args = (TUNING_MONTH, SETUP, "knowledge", 1, 1130)
    plain = mo.result_stem(*args)
    assert mo.result_stem(*args, prompt_cache=False) == plain
    assert mo.result_stem(*args, prompt_cache=True) == plain + "-cache"
    kinds = ("own_username", "how_to")
    with_kinds = mo.result_stem(*args, kinds)
    assert mo.result_stem(*args, kinds, prompt_cache=True) == with_kinds + "-cache"
    assert "-kinds-" in with_kinds


def test_the_setup_factory_turns_the_cache_on_for_claude_only() -> None:
    setup = ALL_SETUPS[SETUP]
    assert "prompt_cache" in setup.factory(prompt_cache=True)().describe()
    assert "prompt_cache" not in setup.factory()().describe()


def test_an_older_result_without_the_cache_keys_renders_as_the_run_wrote_it(
    tmp_path: Path,
) -> None:
    assert _run(tmp_path) in SCORED
    result, md, _ = _load(tmp_path, False)
    assert mo.render(result, md_command(md)) == md


def md_command(md: str) -> str:
    """The recorded command, read back from the summary's "Generated by" line."""
    line = next(x for x in md.splitlines() if x.startswith("Generated by `"))
    return line.removeprefix("Generated by `").removesuffix("`. Do not edit by hand.")
