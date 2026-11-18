"""Caching on by default for dev and tuning runs, off for sealed runs, and every eval counting
the cache tokens (docs/plans/prompt-caching.md). Fake models and fake clients only: no paid
call, no network, and nothing runs on a sealed set.
"""

import dataclasses
import json
from pathlib import Path
from typing import Any, ClassVar

import anyio
import pytest

from meridian_dataset import DEFAULT_SEED
from meridian_dataset import knowledge as kn
from meridian_dataset.generator import HOLDOUT_V5_SEED
from meridian_dataset.month import HELD_OUT_MONTH1, TUNING_MONTH
from tier0 import tracing
from tier0.evals import __main__ as cli
from tier0.evals import caching, m3d2_selection, m3d_screening, triage_check
from tier0.evals import knowledge as kev
from tier0.evals import m3_selection as sel
from tier0.evals import month as mo
from tier0.evals.report import render_markdown
from tier0.evals.runner import run_eval
from tier0.knowledge import sealed
from tier0.models import ModelRequest, ModelResponse, StubModel
from tier0.models.pricing import cost_usd

HAIKU = "claude-haiku-5-5"
WRITE, READ = 30, 700


# --- the default and the flag --------------------------------------------------------------


@pytest.mark.parametrize(
    ("provider", "sealed_run", "expected"),
    [
        ("claude", False, True),
        ("claude", True, False),
        ("ollama", False, False),
        ("ollama", True, False),
        ("stub", False, False),
        ("anthropic", False, False),  # the API's own name isn't the setup provider's
    ],
)
def test_default_prompt_cache_table(provider: str, sealed_run: bool, expected: bool) -> None:
    assert caching.default_prompt_cache(provider=provider, sealed=sealed_run) is expected


@pytest.mark.parametrize(
    ("provider", "sealed_run", "flag", "use", "refused"),
    [
        ("claude", False, None, True, False),
        ("claude", False, True, True, False),
        ("claude", False, False, False, False),
        ("claude", True, None, False, False),
        ("claude", True, True, False, True),
        ("claude", True, False, False, False),
        ("ollama", False, None, False, False),
        ("ollama", False, True, False, True),
        ("ollama", False, False, False, False),
        ("ollama", True, None, False, False),
        ("ollama", True, True, False, True),
        ("ollama", True, False, False, False),
    ],
)
def test_resolve_table(
    provider: str, sealed_run: bool, flag: bool | None, use: bool, refused: bool
) -> None:
    got, refusal = caching.resolve(flag, provider=provider, sealed=sealed_run)
    assert got is use
    assert (refusal is not None) is refused


def test_the_sealed_refusal_says_it_waits_for_the_owner() -> None:
    _, refusal = caching.resolve(True, provider="claude", sealed=True)
    assert refusal is not None
    assert "was planned without the prompt cache" in refusal
    assert "only after the owner rules" in refusal


def test_the_flags_default_to_none_and_take_both_forms() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    caching.add_flags(parser)
    assert parser.parse_args([]).prompt_cache is None
    assert parser.parse_args(["--prompt-cache"]).prompt_cache is True
    assert parser.parse_args(["--no-prompt-cache"]).prompt_cache is False


def test_result_note_is_empty_without_a_cached_score() -> None:
    assert caching.result_note([{"input_tokens": 5}]) == []
    note = caching.result_note(
        [
            {"input_tokens": 100, "prompt_cache": True, "cache_write_tokens": 50},
            {"input_tokens": 9},
        ]
    )
    assert note[0].startswith("Prompt cache on for 1 of 2 scores")
    assert "100 at the full rate, 50 written" in note[0]


# --- tracing -------------------------------------------------------------------------------


class _Reporting:
    provider = "anthropic"
    model = HAIKU
    temperature = None
    supports_seed = False

    def __init__(self, write: int, read: int) -> None:
        self.write, self.read = write, read

    def describe(self) -> dict[str, str]:
        return {}

    def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse("x", self.model, 10, 2, "stop", None, self.write, self.read)


def _chat_attrs(write: int, read: int) -> dict[str, Any]:
    tracer = tracing.Tracer()
    with tracer.session(ticket_id="TKT-1", kind="intake") as session:
        session.chat(_Reporting(write, read), ModelRequest("triage", "s", "u"))
    return dict(next(s for s in tracer.finished_spans() if s.name.startswith("chat ")).attributes)


def test_the_cache_span_attributes_exist_only_when_not_zero() -> None:
    assert tracing.GEN_AI_USAGE_CACHE_CREATION_TOKENS == "gen_ai.usage.cache_creation.input_tokens"
    assert tracing.GEN_AI_USAGE_CACHE_READ_TOKENS == "gen_ai.usage.cache_read.input_tokens"
    plain = _chat_attrs(0, 0)
    assert tracing.GEN_AI_USAGE_CACHE_CREATION_TOKENS not in plain
    assert tracing.GEN_AI_USAGE_CACHE_READ_TOKENS not in plain
    only_read = _chat_attrs(0, READ)
    assert tracing.GEN_AI_USAGE_CACHE_CREATION_TOKENS not in only_read
    assert only_read[tracing.GEN_AI_USAGE_CACHE_READ_TOKENS] == READ
    both = _chat_attrs(WRITE, READ)
    assert both[tracing.GEN_AI_USAGE_CACHE_CREATION_TOKENS] == WRITE


def test_the_ticket_summary_adds_cache_tokens_across_calls() -> None:
    tracer = tracing.Tracer()
    with tracer.session(ticket_id="TKT-1", kind="intake") as session:
        session.chat(_Reporting(WRITE, 0), ModelRequest("triage", "s", "u"))
        session.chat(_Reporting(0, READ), ModelRequest("triage", "s", "u"))
        session.chat(_Reporting(0, READ), ModelRequest("triage", "s", "u"))
    summary = tracing.summarize(tracer.finished_spans())["TKT-1"]
    assert (summary.cache_write_tokens, summary.cache_read_tokens) == (WRITE, 2 * READ)
    assert summary.input_tokens == 30 and summary.output_tokens == 6
    plain = tracing.Tracer()
    with plain.session(ticket_id="TKT-1", kind="intake") as session:
        session.chat(StubModel(), ModelRequest("triage", "s", "u"))
    s = tracing.summarize(plain.finished_spans())["TKT-1"]
    assert (s.cache_write_tokens, s.cache_read_tokens) == (0, 0)


# --- the main eval -------------------------------------------------------------------------


class _ClaudeStub:
    """The stub's answers under a Claude name, reporting cache tokens on every call."""

    provider = "anthropic"
    model = HAIKU
    temperature = None
    supports_seed = False

    def __init__(self, write: int = 0, read: int = 0) -> None:
        self._stub = StubModel()
        self.write, self.read = write, read

    def describe(self) -> dict[str, str]:
        return {}

    def complete(self, request: ModelRequest) -> ModelResponse:
        r = self._stub.complete(request)
        return dataclasses.replace(
            r, model=self.model, cache_write_tokens=self.write, cache_read_tokens=self.read
        )


def _report(write: int, read: int, *, prompt_cache: bool):  # type: ignore[no-untyped-def]
    return anyio.run(
        lambda: run_eval(
            DEFAULT_SEED,
            k=1,
            model_factory=lambda: _ClaudeStub(write, read),
            prompt_cache=prompt_cache,
        )
    )


def test_the_main_eval_prices_and_records_the_cache_tokens() -> None:
    rep = _report(WRITE, READ, prompt_cache=True)
    assert rep.prompt_cache and rep.cache_on
    for r in rep.results:
        assert r.cache_write_tokens == WRITE * r.model_calls
        assert r.cache_read_tokens == READ * r.model_calls
        assert rep.run_cost_usd(r) == pytest.approx(
            cost_usd(
                "anthropic",
                HAIKU,
                r.input_tokens,
                r.output_tokens,
                cache_write_tokens=r.cache_write_tokens,
                cache_read_tokens=r.cache_read_tokens,
            )
        )
    plain = _report(0, 0, prompt_cache=False)
    assert rep.total_cost_usd > plain.total_cost_usd
    data = rep.to_dict()
    assert data["prompt_cache"] is True
    assert data["cache_tokens"] == {
        "cache_write_tokens": rep.cache_write_tokens,
        "cache_read_tokens": rep.cache_read_tokens,
    }
    assert data["cost"]["total_usd"] == pytest.approx(rep.total_cost_usd)
    assert all("cache_write_tokens" in d for d in data["runs_detail"])
    assert all("mean_cache_read_tokens" in t for t in data["tickets"])
    md = render_markdown(rep)
    assert "Prompt cache on:" in md and "Cache written tokens" in md and "Cache read tokens" in md


def test_the_flag_alone_marks_a_cached_run_that_read_nothing() -> None:
    rep = _report(0, 0, prompt_cache=True)
    assert rep.to_dict()["prompt_cache"] is True
    assert "Prompt cache on:" in render_markdown(rep)


def _untimed(rep):  # type: ignore[no-untyped-def]
    """The report with every latency zeroed, so two runs can be compared."""
    return dataclasses.replace(
        rep, results=tuple(dataclasses.replace(r, duration_ms=0.0) for r in rep.results)
    )


def test_a_run_without_the_cache_writes_and_renders_what_it_always_did() -> None:
    rep = _report(0, 0, prompt_cache=False)
    assert not rep.cache_on and not rep.to_dict().get("prompt_cache")
    text = json.dumps(rep.to_dict())
    assert "cache" not in text.replace("cache_system", "")
    md = render_markdown(rep)
    assert "Prompt cache" not in md and "Cache written" not in md and "cache" not in md.lower()
    # the same run through the old path (no argument at all) is identical
    old = anyio.run(lambda: run_eval(DEFAULT_SEED, k=1, model_factory=lambda: _ClaudeStub(0, 0)))
    assert render_markdown(_untimed(old)) == render_markdown(_untimed(rep))
    assert json.dumps(_untimed(old).to_dict(), sort_keys=True) == json.dumps(
        _untimed(rep).to_dict(), sort_keys=True
    )


class _Stop(Exception):
    pass


def _record_run_eval(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace run_eval with one that records its keywords and stops the command."""
    seen: dict[str, Any] = {}

    async def fake(seed: int, **kwargs: Any) -> Any:
        seen.update(kwargs)
        raise _Stop

    monkeypatch.setattr(cli, "run_eval", fake)
    return seen


def _main(*argv: str, out: Path) -> int:
    return cli.main([*argv, "--no-warm-up", "--k", "1", "--out", str(out)])


def test_the_main_eval_defaults_to_the_cache_for_claude_on_dev_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _record_run_eval(monkeypatch)
    with pytest.raises(_Stop):
        _main("--model", "claude", "--seed", str(DEFAULT_SEED), out=tmp_path)
    assert seen["prompt_cache"] is True
    with pytest.raises(_Stop):
        _main("--model", "claude", "--seed", str(DEFAULT_SEED), "--no-prompt-cache", out=tmp_path)
    assert seen["prompt_cache"] is False
    with pytest.raises(_Stop):
        _main("--model", "stub", out=tmp_path)
    assert seen["prompt_cache"] is False


def test_the_main_eval_factory_carries_the_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TIER0_CLAUDE_PROMPT_CACHE", "1")  # the live agent's option, ignored here
    assert "prompt_cache" in cli._model_factory("claude", prompt_cache=True)().describe()
    assert "prompt_cache" not in cli._model_factory("claude", prompt_cache=False)().describe()


def test_the_main_eval_on_a_setup_passes_the_cache_to_its_factory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _record_run_eval(monkeypatch)
    with pytest.raises(_Stop):
        _main("--setup", "haiku-3d2", "--seed", str(DEFAULT_SEED), out=tmp_path)
    assert seen["prompt_cache"] is True
    assert "prompt_cache" in seen["model_factory"]().describe()
    with pytest.raises(_Stop):
        _main(
            "--setup", "haiku-3d2", "--seed", str(DEFAULT_SEED), "--no-prompt-cache", out=tmp_path
        )
    assert seen["prompt_cache"] is False
    assert "prompt_cache" not in seen["model_factory"]().describe()


def test_a_sealed_pool_keeps_the_cache_off_and_refuses_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seen = _record_run_eval(monkeypatch)
    monkeypatch.setattr(cli, "FROZEN", set(cli.SETUPS))  # past the frozen-setups check
    sealed_pool = "--final-comparison=held_out_v5"
    base = ("--model", "claude", "--seed", str(HOLDOUT_V5_SEED), sealed_pool)
    with pytest.raises(_Stop):
        _main(*base, out=tmp_path)
    assert seen["prompt_cache"] is False
    assert cli.main([*base, "--prompt-cache", "--k", "1", "--out", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "refused:" in err and "planned without the prompt cache" in err


def test_the_main_eval_refuses_the_cache_for_a_local_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _record_run_eval(monkeypatch)
    assert _main("--model", "stub", "--prompt-cache", out=tmp_path) == 2
    assert "refused:" in capsys.readouterr().err


# --- the month runner ----------------------------------------------------------------------


class _MonthFake:
    temperature = None
    supports_seed = False

    def __init__(self, cache: bool) -> None:
        self.provider = "anthropic"
        self.model = HAIKU
        self.cache = cache
        self.seed: int | None = None
        self.n = 0

    def describe(self) -> dict[str, str]:
        return {}

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.n += 1
        if request.task == "triage":
            text = json.dumps({"category": "other"})
        else:
            text = json.dumps({"own_account_only": True})
        w, r = (WRITE, READ) if self.cache else (0, 0)
        return ModelResponse(text, self.model, 80, 5, "stop", None, w, r)


def _month(tmp: Path, set_name: str, *extra: str, cache_tokens: bool = True) -> int:
    argv = ["run", "--set", set_name, "--setup", "haiku-3d2", "--k", "1", *extra, "--out", str(tmp)]
    return mo.main(argv, make_model=lambda: _MonthFake(cache_tokens), samples=50)  # type: ignore[arg-type,return-value]


def _only_json(tmp: Path) -> tuple[Path, dict[str, Any]]:
    (path,) = [p for p in tmp.iterdir() if p.suffix == ".json"]
    return path, json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("set_name", [TUNING_MONTH, HELD_OUT_MONTH1])
def test_the_month_defaults_to_the_cache_on_tuning_and_dev_sets(
    tmp_path: Path, set_name: str
) -> None:
    assert _month(tmp_path, set_name) in (0, 1)
    path, result = _only_json(tmp_path)
    assert path.stem.endswith("-cache")
    assert result["config"]["prompt_cache"] is True
    assert path.with_suffix(".md").read_text(encoding="utf-8").count("- Prompt cache on:") == 1
    # the cache's default is not printed in the recorded command
    assert "prompt-cache" not in path.with_suffix(".md").read_text(encoding="utf-8").split("\n")[2]


@pytest.mark.parametrize("set_name", [TUNING_MONTH, HELD_OUT_MONTH1])
def test_the_month_can_turn_the_cache_off(tmp_path: Path, set_name: str) -> None:
    assert _month(tmp_path, set_name, "--no-prompt-cache", cache_tokens=False) in (0, 1)
    path, result = _only_json(tmp_path)
    assert "-cache" not in path.stem
    assert "prompt_cache" not in result["config"]
    md = path.with_suffix(".md").read_text(encoding="utf-8")
    assert "Prompt cache" not in md
    assert "--no-prompt-cache" in md  # it differs from the default, so the command says so


@pytest.mark.usefixtures("sealed_month1")
def test_a_sealed_month_defaults_to_off_and_refuses_the_cache(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    final = f"--final-test={HELD_OUT_MONTH1}"
    assert _month(tmp_path, HELD_OUT_MONTH1, final, cache_tokens=False) in (0, 1)
    path, result = _only_json(tmp_path)
    assert "-cache" not in path.stem and "prompt_cache" not in result["config"]
    other = tmp_path / "second"
    assert _month(other, HELD_OUT_MONTH1, final, "--prompt-cache") == 2
    assert capsys.readouterr().err.startswith("refused:")
    assert not other.exists() or list(other.iterdir()) == []


def test_the_month_command_names_the_flag_only_when_it_differs_from_the_default() -> None:
    import argparse

    def args(set_name: str, setup: str, flag: bool | None) -> argparse.Namespace:
        return argparse.Namespace(
            set=set_name,
            setup=setup,
            k=1,
            seed=1,
            picker="none",
            picker_prompt=None,
            final_test=None,
            kinds=None,
            prompt_cache=flag,
            out=Path("out"),
        )

    claude = "haiku-3d2"
    assert "prompt-cache" not in mo._command(args(TUNING_MONTH, claude, True))
    assert "prompt-cache" not in mo._command(args(TUNING_MONTH, claude, None))
    assert "--no-prompt-cache" in mo._command(args(TUNING_MONTH, claude, False))
    local = next(
        s.name for s in mo.ALL_SETUPS.values() if s.provider != "claude" and s.split_prompt is None
    )
    assert "prompt-cache" not in mo._command(args(TUNING_MONTH, local, False))


# --- the score evals -----------------------------------------------------------------------


class _KnowledgeFake:
    provider = "anthropic"
    model = HAIKU
    temperature = None
    seed = None
    supports_seed = False

    def __init__(self, tokens: tuple[int, int] = (0, 0)) -> None:
        self.tokens = tokens

    def describe(self) -> dict[str, str]:
        return {}

    def complete(self, request: ModelRequest) -> ModelResponse:
        text = json.dumps({"request_type": "other", "guide": "none"})
        return ModelResponse(text, self.model, 100, 5, "stop", None, *self.tokens)


def test_the_knowledge_score_prices_and_records_the_cache_tokens() -> None:
    result = kev.score(HAIKU, "K1", model=_KnowledgeFake((WRITE, READ)), prompt_cache=True)
    n = len(result["runs"])
    assert result["prompt_cache"] is True
    assert result["cache_write_tokens"] == WRITE * n and result["cache_read_tokens"] == READ * n
    assert result["cost_usd"] == pytest.approx(
        cost_usd(
            "anthropic",
            HAIKU,
            result["input_tokens"],
            result["output_tokens"],
            cache_write_tokens=WRITE * n,
            cache_read_tokens=READ * n,
        )
    )
    md = kev.render([result], {HAIKU: "K1"})
    assert "Prompt cache on for 1 of 1 scores" in md


def test_the_knowledge_score_without_the_cache_is_unchanged() -> None:
    result = kev.score(HAIKU, "K1", model=_KnowledgeFake())
    assert not {"prompt_cache", "cache_write_tokens", "cache_read_tokens"} & set(result)
    assert result["cost_usd"] == pytest.approx(
        cost_usd("anthropic", HAIKU, result["input_tokens"], result["output_tokens"])
    )
    assert "Prompt cache" not in kev.render([result], {HAIKU: "K1"})


def _knowledge_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def fake(*args: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        raise _Stop

    monkeypatch.setattr(kev, "score", fake)
    monkeypatch.setattr(kev, "RESULTS", tmp_path)
    return seen


def test_the_knowledge_command_defaults_on_for_tuning_off_for_a_sealed_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seen = _knowledge_cli(monkeypatch, tmp_path)
    base = ["score", "--model", HAIKU, "--version", "KH5"]
    with pytest.raises(_Stop):
        kev.main(base)
    assert seen["prompt_cache"] is True
    with pytest.raises(_Stop):
        kev.main([*base, "--no-prompt-cache"])
    assert seen["prompt_cache"] is False
    final = ["--set", kn.HELD_OUT_K1, "--final-test", kn.HELD_OUT_K1]
    assert kn.HELD_OUT_K1 in sealed.SEALED_SETS
    with pytest.raises(_Stop):
        kev.main([*base, *final])
    assert seen["prompt_cache"] is False
    assert kev.main([*base, *final, "--prompt-cache"]) == 2
    assert "planned without the prompt cache" in capsys.readouterr().err


def test_the_m3_selection_score_prices_and_records_the_cache_tokens() -> None:
    class Reporting:
        provider = "anthropic"
        supports_seed = False
        answers: ClassVar[dict[str, Any]] = {c.text: c for c in sel.instances()}

        def __init__(self, tokens: tuple[int, int]) -> None:
            self.tokens = tokens

        def complete(self, request: ModelRequest) -> ModelResponse:
            case = self.answers[request.user.split("Message:\n", 1)[-1]]
            body = json.dumps({"category": case.triage_expected})
            return ModelResponse(body, HAIKU, 100, 5, "stop", None, *self.tokens)

    cached = sel.score(HAIKU, "T1", k=1, model=Reporting((WRITE, READ)), prompt_cache=True)
    n = sum(cached["total"][g] for g in ("everyday", "self_described", "colleague"))
    assert cached["prompt_cache"] is True
    assert (cached["cache_write_tokens"], cached["cache_read_tokens"]) == (WRITE * n, READ * n)
    assert cached["cost_usd"] == pytest.approx(
        cost_usd(
            "anthropic",
            HAIKU,
            100 * n,
            5 * n,
            cache_write_tokens=WRITE * n,
            cache_read_tokens=READ * n,
        )
    )
    assert "Prompt cache on for 1 of 1 scores" in sel.render([cached], "cmd")
    plain = sel.score(HAIKU, "T1", k=1, model=Reporting((0, 0)))
    assert not {"prompt_cache", "cache_write_tokens", "cache_read_tokens"} & set(plain)
    assert plain["cost_usd"] == pytest.approx(cost_usd("anthropic", HAIKU, 100 * n, 5 * n))
    assert "Prompt cache" not in sel.render([plain], "cmd")


def test_the_make_model_builds_a_cached_claude_model_only_when_asked() -> None:
    assert "prompt_cache" in sel.make_model(HAIKU, prompt_cache=True).describe()
    assert "prompt_cache" not in sel.make_model(HAIKU).describe()


def test_the_m3_selection_command_defaults_on_for_claude(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: dict[str, Any] = {}

    def fake(*args: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        raise _Stop

    monkeypatch.setattr(sel, "score", fake)
    monkeypatch.setattr(sel, "RESULTS", tmp_path)
    version = sel.candidates(HAIKU, "triage")[0]
    base = ["score", "--model", HAIKU, "--versions", version, "--rescore"]
    with pytest.raises(_Stop):
        sel.main(base)
    assert seen["prompt_cache"] is True
    with pytest.raises(_Stop):
        sel.main([*base, "--no-prompt-cache"])
    assert seen["prompt_cache"] is False


def test_the_3d_scoring_commands_default_on_and_pass_it_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def recorder(name: str):  # type: ignore[no-untyped-def]
        def fake(*args: Any, **kwargs: Any) -> str:
            seen[name] = kwargs["prompt_cache"]
            return name

        return fake

    monkeypatch.setattr(m3d_screening, "screen", lambda *a, **k: seen.update(screen=k) or [])
    monkeypatch.setattr(m3d_screening, "score_all", recorder("all"))
    monkeypatch.setattr(m3d_screening, "score_several", recorder("several"))
    monkeypatch.setattr(m3d2_selection, "score_part2", recorder("part2"))
    assert m3d_screening.main(["score", "--model", HAIKU, "--version", "HT9", "--all"]) == 0
    assert seen["all"] is True
    assert m3d_screening.main(["screen", "--setup", "haiku-3b", "--no-prompt-cache"]) == 0
    assert seen["screen"]["prompt_cache"] is False
    assert m3d2_selection.main(["score", "--model", HAIKU, "--version", "HT9", "--part2"]) == 0
    assert seen["part2"] is True


# --- the triage check ----------------------------------------------------------------------


def test_the_triage_check_records_the_cache_and_names_the_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _ClaudeStub(WRITE, READ)
    monkeypatch.setattr(
        triage_check, "_model_factory", lambda *a, prompt_cache=False, **k: lambda: fake
    )
    assert (
        triage_check.main(
            ["--model", "claude", "--pool", "tuning", "--k", "1", "--out", str(tmp_path)]
        )
        == 0
    )
    (js,) = tmp_path.glob("*.json")
    assert js.stem.endswith("-cache")
    data = json.loads(js.read_text())
    assert data["prompt_cache"] is True and data["cache_read_tokens"] > 0
    assert "Prompt cache on:" in js.with_suffix(".md").read_text()
