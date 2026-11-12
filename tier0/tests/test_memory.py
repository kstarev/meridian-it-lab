"""Memory headroom before a big local model runs (ADR-031)."""

import httpx2
import pytest

from tier0.evals import __main__ as cli
from tier0.evals import m3_selection as sel
from tier0.evals.setups import SETUPS
from tier0.models import memory

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


def test_tuning_pauses_before_a_version_instead_of_being_killed(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys
) -> None:
    monkeypatch.setattr(sel, "RESULTS", tmp_path)
    monkeypatch.setattr(sel, "make_room", lambda *a, **k: [])
    monkeypatch.setattr(sel.memory, "headroom", lambda model, **k: (False, "1.0 GB free"))
    called: list[str] = []
    monkeypatch.setattr(sel, "score", lambda *a, **k: called.append(a[1]) or {})
    assert sel.main(["score", "--model", "qwen3.6:27b", "--versions", "QC4"]) == 3
    assert "paused before QC4: 1.0 GB free" in capsys.readouterr().out
    assert called == [] and not list(tmp_path.iterdir())


def test_an_eval_pauses_before_a_big_model_loads(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys
) -> None:
    monkeypatch.setattr(cli, "make_room", lambda *a, **k: [])
    monkeypatch.setattr(cli, "FROZEN", frozenset(SETUPS))
    monkeypatch.setattr(cli.memory, "headroom", lambda model, **k: (False, "2.0 GB free"))
    made: list[str] = []
    setup = SETUPS["qwen3.6:27b"]
    monkeypatch.setattr(type(setup), "factory", lambda self, t=0.5: lambda: made.append("x"))
    args = ["--setup", "qwen3.6:27b", "--seed", "2611", "--k", "3", "--out", str(tmp_path)]
    assert cli.main(args) == 3
    assert "paused: 2.0 GB free" in capsys.readouterr().err
    assert made == [] and not (tmp_path / "report.json").exists()
