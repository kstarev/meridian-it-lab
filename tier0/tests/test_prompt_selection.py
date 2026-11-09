"""Prompt selection table: fake models only, every version, both pools for the chosen one."""

import json
from pathlib import Path

import pytest

from tier0 import messages
from tier0.evals.prompt_history import CHOSEN, VERSIONS
from tier0.evals.prompt_selection import build_selection, main, render_markdown
from tier0.models import OWN_USERNAME, ModelResponse


class AlwaysOwn:
    provider = "fake"
    model = "fake-own"
    temperature = 0.5

    def complete(self, request):  # type: ignore[no-untyped-def]
        text = json.dumps({"category": OWN_USERNAME})
        return ModelResponse(text, "m", 1, 1, "stop", None)


def test_selection_covers_every_version_and_both_pools() -> None:
    live = messages.TRIAGE_SYSTEM
    data = build_selection({"fake": AlwaysOwn}, k=2, seed_base=10)
    assert list(data["versions"]) == list(VERSIONS)
    assert live == messages.TRIAGE_SYSTEM
    for name, info in data["versions"].items():
        entry = info["models"]["fake"]
        overall = entry["tuning"]["overall"]
        assert overall["total"] == sum(entry["tuning"][s]["total"] for s in data["scenarios"])
        assert entry["tuning"]["colleague"]["correct"] == 0
        assert ("dev" in entry) == (name == CHOSEN)
    assert data["frozen_sha256"] == messages.TRIAGE_SYSTEM_SHA256
    assert data["versions"][CHOSEN]["sha256"] == messages.TRIAGE_SYSTEM_SHA256


def test_markdown_wording() -> None:
    data = build_selection({"fake": AlwaysOwn}, k=1, seed_base=10)
    md = render_markdown(data, "python -m tier0.evals.prompt_selection --models fake")
    assert "tuning, best of 8 (optimistic)" in md and "not accuracy" in md
    assert messages.TRIAGE_SYSTEM_SHA256 in md
    assert "Triage prompt selection (milestone 2)" in md and "do not edit by hand" in md
    for name in VERSIONS:
        assert name in md
    assert md.lower().count("accuracy") == 1
    assert "Dev-set check of the chosen version" in md


def test_non_loopback_url_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TIER0_OLLAMA_URL", "http://example.com:11434")
    assert main(["--models", "m", "--out", str(tmp_path / "x.md")]) == 2
    assert not (tmp_path / "x.md").exists()
