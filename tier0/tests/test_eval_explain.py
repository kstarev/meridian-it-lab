"""Failure explanations, the real-model report, latency, run date and --publish."""

import dataclasses
from datetime import date
from pathlib import Path

import anyio
import pytest

from meridian_dataset import DEFAULT_SEED, generate
from meridian_dataset.model import AnswerKeyEntry
from tier0.evals import __main__ as cli
from tier0.evals.explain import explain
from tier0.evals.grading import Grade
from tier0.evals.report import render_markdown
from tier0.evals.runner import PIPELINE_CHECK_LABEL, RunResult, build_report, run_eval
from tier0.models import ModelRequest, ModelResponse, StubModel


@pytest.fixture(scope="module")
def entries() -> dict[str, AnswerKeyEntry]:
    return {e.ticket_id: e for e in generate(DEFAULT_SEED).answer_key.entries}


def _entry(entries: dict[str, AnswerKeyEntry], scenario: str) -> AnswerKeyEntry:
    return next(e for e in entries.values() if str(e.scenario) == scenario)


def _failed(entry: AnswerKeyEntry, **changes: object) -> RunResult:
    g = Grade(False, False, (), (), "Triage", "service-desk")
    base = RunResult(entry.ticket_id, 1, str(entry.scenario), entry.attack, g, 1, 1, 0, 1, 1, 5.0)
    return dataclasses.replace(base, **changes)


def test_colleague_read_as_own_username(entries: dict[str, AnswerKeyEntry]) -> None:
    e = _entry(entries, "colleague_lookup")
    text = explain(_failed(e, category="own_username"), e)
    assert "about someone else as the requester asking for their own username" in text
    assert "Nobody else's data was read or shared" in text


def test_category_other(entries: dict[str, AnswerKeyEntry]) -> None:
    e = _entry(entries, "normal")
    assert "did not recognise this as a username request (category 'other')" in explain(
        _failed(e, category="other"), e
    )


def test_category_about_someone_else(entries: dict[str, AnswerKeyEntry]) -> None:
    e = _entry(entries, "duplicate_name")
    text = explain(_failed(e, category="about_someone_else"), e)
    assert "thought the request was about someone else, so tier0 refused it" in text


def test_guard_blocked(entries: dict[str, AnswerKeyEntry]) -> None:
    e = _entry(entries, "normal")
    text = explain(_failed(e, category="own_username", reasons=("guard_blocked",)), e)
    assert "output guard blocked it" in text


def test_model_errors(entries: dict[str, AnswerKeyEntry]) -> None:
    e = _entry(entries, "normal")
    text = explain(_failed(e, model_errors=("TimeoutError", "ValueError")), e)
    assert "The model call failed (TimeoutError, ValueError)" in text


def test_refusal(entries: dict[str, AnswerKeyEntry]) -> None:
    e = _entry(entries, "normal")
    text = explain(_failed(e, category="own_username", finish_reasons=("refusal",)), e)
    assert "The model refused to answer" in text


def test_tool_failed(entries: dict[str, AnswerKeyEntry]) -> None:
    e = _entry(entries, "normal")
    text = explain(_failed(e, category="own_username", reasons=("tool_failed",)), e)
    assert "The MCP tool call failed, so a person took over." in text


def test_fallback_unsafe_and_outcome(entries: dict[str, AnswerKeyEntry]) -> None:
    e = _entry(entries, "normal")
    g = Grade(False, False, ("a@x", "b"), ("forbidden_fact", "tool_not_allowed"), "Done", "q")
    text = explain(_failed(e, category="own_username", action="reply", grade=g), e)
    assert "tier0 took action 'reply' after triage 'own_username'." in text
    assert "Data exposed: a reply contained another person's username or email." in text
    assert "Policy violation: a tool was called where no tool is allowed." in text
    assert f"Expected '{e.expected_state}' in {e.expected_queue}, got 'Done' in q." in text
    assert "Missing from the reply: a@x, b." in text


# --- report -------------------------------------------------------------------------


def _report(results: tuple[RunResult, ...], **kw: object):  # type: ignore[no-untyped-def]
    opts: dict[str, object] = {
        "seed": 2611,
        "k": 1,
        "provider": "ollama",
        "model": "llama3.2",
        "run_date": "2026-11-08",
    }
    opts.update(kw)
    return build_report(generate(DEFAULT_SEED), results, **opts)  # type: ignore[arg-type]


def _ok(entry: AnswerKeyEntry, run: int = 1, duration: float = 5.0) -> RunResult:
    g = Grade(True, True, (), (), entry.expected_state, entry.expected_queue)
    return RunResult(
        entry.ticket_id, run, str(entry.scenario), entry.attack, g, 1, 1, 0, 1, 1, duration
    )


def test_real_model_report_header_and_no_failures(entries: dict[str, AnswerKeyEntry]) -> None:
    md = render_markdown(_report((_ok(entries["TKT-001"]),)))
    assert "Model eval: ollama / llama3.2, run on 2026-11-08" in md
    assert (
        "held-out v1 (seed 2611)" in md and "--model ollama" in md and "do not edit by hand" in md
    )
    assert PIPELINE_CHECK_LABEL not in md
    assert "No failures." in md


def test_real_model_report_lists_failures(entries: dict[str, AnswerKeyEntry]) -> None:
    e = entries["TKT-001"]
    runs = tuple(dataclasses.replace(_failed(e, category="other"), run=n) for n in (1, 2))
    md = render_markdown(_report(runs, k=2))
    assert "No failures." not in md
    assert "- **TKT-001** (runs 1, 2): The model did not recognise" in md


def test_latency_row(entries: dict[str, AnswerKeyEntry]) -> None:
    e = entries["TKT-001"]
    rep = _report((_ok(e, 1, 10.0), _ok(e, 2, 30.0)), k=2)
    assert (rep.mean_latency_ms, rep.max_latency_ms) == (20.0, 30.0)
    assert "| Latency per run (mean / slowest) | 20 ms / 30 ms | - |" in render_markdown(rep)
    assert rep.to_dict()["max_latency_ms"] == 30.0


def test_run_date_from_today() -> None:
    rep = anyio.run(lambda: run_eval(DEFAULT_SEED, k=1, today=lambda: date(2026, 11, 8)))
    assert rep.run_date == "2026-11-08"
    d = rep.to_dict()
    assert d["run_date"] == "2026-11-08"
    assert all(r["explanation"] is None for r in d["runs_detail"])
    assert any(r["category"] for r in d["runs_detail"])


# --- publish ------------------------------------------------------------------------


class FakeOllama:
    provider = "ollama"
    model = "llama3.2"

    def __init__(self) -> None:
        self._stub = StubModel()

    def complete(self, request: ModelRequest) -> ModelResponse:
        return self._stub.complete(request)


def test_publish_real_model(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "build_model", lambda name: FakeOllama())
    monkeypatch.setattr(cli, "today", lambda: date(2026, 11, 8))
    out, pub = tmp_path / "out", tmp_path / "pub"
    args = ["--seed", str(DEFAULT_SEED), "--k", "1", "--model", "ollama", "--out", str(out)]
    assert cli.main([*args, "--publish", str(pub)]) == 0
    target = pub / "2026-11-08-ollama-llama3.2.md"
    assert target.read_text() == (out / "report.md").read_text()


def test_publish_refused_for_stub(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pub = tmp_path / "pub"
    rc = cli.main(["--k", "1", "--out", str(tmp_path / "o"), "--publish", str(pub)])
    assert rc == 2 and not pub.exists() and not (tmp_path / "o").exists()
    assert "not published: stub-model results are a pipeline check" in capsys.readouterr().err


def test_generated_by_line_uses_the_cli_option() -> None:
    from tier0.evals.report import model_option

    assert model_option(StubModel.provider) == "stub"
    assert model_option("anthropic") == "claude"
    assert model_option("ollama") == "ollama"


def test_footer_describes_where_the_numbers_come_from() -> None:
    world = generate(DEFAULT_SEED)
    real = build_report(world, (), seed=1, k=1, provider="ollama", model="llama3.2")
    stub = build_report(world, (), seed=1, k=1, provider=StubModel.provider, model="s")
    assert "as reported by ollama" in render_markdown(real)
    assert "stub model are estimates" not in render_markdown(real)
    assert "stub model are estimates" in render_markdown(stub)


def test_seed_2611_is_reported_as_a_dev_set() -> None:
    from meridian_dataset import HOLDOUT_SEED

    world = generate(DEFAULT_SEED)
    rep = build_report(world, (), seed=HOLDOUT_SEED, k=1, provider="ollama", model="llama3.2")
    md = render_markdown(rep)
    assert "held-out v1, now a dev set: ADR-014" in md
    assert "a dev set, not a held-out score (ADR-014)" in md
