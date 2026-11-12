"""Memory headroom before a big local model runs (ADR-031)."""

import httpx2
import pytest

from tier0.evals import __main__ as cli
from tier0.evals import m3_selection as sel
from tier0.evals.setups import SETUPS
from tier0.models import ModelRequest, ModelResponse, memory

VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                                    100000.
Pages active:                                  800000.
Pages inactive:                                200000.
Pages speculative:                              10000.
Pages wired down:                              180000.
"""


def test_available_memory_counts_free_inactive_and_speculative_pages() -> None:
    assert memory.available_bytes(VM_STAT) == (100000 + 200000 + 10000) * 16384
    assert memory.available_bytes("no page size here") is None


def _ollama(loaded: list[str]) -> httpx2.Client:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/api/ps":
            return httpx2.Response(200, json={"models": [{"name": m} for m in loaded]})
        return httpx2.Response(200, json={"models": [{"name": "qwen3.6:27b", "size": 19 * 10**9}]})

    return httpx2.Client(base_url="http://ollama.example", transport=httpx2.MockTransport(handler))


def test_headroom_counts_what_loading_the_model_still_takes() -> None:
    gb = memory.GB
    ok, message = memory.headroom("qwen3.6:27b", available=22 * gb, client=_ollama([]))
    assert not ok and "3.0 GB would be left" in message and "needs 19.0 GB" in message
    ok, _ = memory.headroom("qwen3.6:27b", available=24 * gb, client=_ollama([]))
    assert ok
    # Already loaded: only the free memory now counts.
    ok, message = memory.headroom("qwen3.6:27b", available=5 * gb, client=_ollama(["qwen3.6:27b"]))
    assert ok and "needs 0.0 GB" in message
    ok, _ = memory.headroom("qwen3.6:27b", available=3 * gb, client=_ollama(["qwen3.6:27b"]))
    assert not ok


def test_low_memory_warns_and_logs_but_tuning_still_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys
) -> None:
    monkeypatch.setattr(sel, "RESULTS", tmp_path / "scores")
    monkeypatch.setattr(sel.watch, "LOG", tmp_path / "watch.jsonl")
    monkeypatch.setattr(sel.watch, "TIMINGS", tmp_path / "timing")
    monkeypatch.setattr(sel, "make_room", lambda *a, **k: [])
    monkeypatch.setattr(sel.memory, "headroom", lambda model, **k: (False, "1.0 GB free"))
    called: list[str] = []

    def fake_score(name, version, **kw):  # type: ignore[no-untyped-def]
        called.append(version)
        return {
            "correct": dict.fromkeys(sel.GROUPS, 0),
            "total": dict.fromkeys(sel.GROUPS, 0),
            "cost_usd": 0.0,
        }

    monkeypatch.setattr(sel, "score", fake_score)
    assert sel.main(["score", "--model", "qwen3.6:27b", "--versions", "QC4"]) == 0
    assert "WARNING: low memory before QC4: 1.0 GB free" in capsys.readouterr().out
    assert called == ["QC4"]  # it ran anyway
    logged = (tmp_path / "watch.jsonl").read_text()
    assert '"event": "memory"' in logged and '"ok": false' in logged


class _Fake:
    provider = "ollama"
    temperature = 0.5
    supports_seed = True
    model = "qwen3.6:27b"

    def __init__(self) -> None:
        self.seed: int | None = None

    def describe(self) -> dict[str, str]:
        return {
            "model_id": "q",
            "model_digest": "q" * 64,
            "ollama_version": "1",
            "schema_enforcement": "none",
        }

    def complete(self, request: ModelRequest) -> ModelResponse:
        text = (
            '{"category": "own_username"}'
            if request.task == "triage"
            else '{"own_account_only": true}'
        )
        return ModelResponse(text, "qwen3.6:27b", 1, 1, "stop", None)


def test_low_memory_warns_but_an_eval_still_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys
) -> None:
    monkeypatch.setattr(cli, "make_room", lambda *a, **k: [])
    monkeypatch.setattr(cli, "FROZEN", frozenset(SETUPS))
    monkeypatch.setattr(cli.memory, "headroom", lambda model, **k: (False, "2.0 GB free"))
    monkeypatch.setattr(cli.watch, "LOG", tmp_path / "watch.jsonl")
    monkeypatch.setattr(cli.watch, "TIMINGS", tmp_path / "timing")
    setup = SETUPS["qwen3.6:27b"]
    monkeypatch.setattr(type(setup), "factory", lambda self, t=0.5: _Fake)
    args = ["--setup", "qwen3.6:27b", "--seed", "1108", "--k", "1", "--out", str(tmp_path)]
    assert cli.main(args) == 0
    assert "WARNING: low memory: 2.0 GB free" in capsys.readouterr().err
    assert (tmp_path / "report.json").exists()
    md = (tmp_path / "report.md").read_text()
    assert "> **No schema enforcement:** no structured output in this Ollama build" in md
    assert "Off-format replies: 0 of" in md
