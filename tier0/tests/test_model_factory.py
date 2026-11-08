"""build_model picks the adapter from a name or the environment."""

import pytest

from tier0.models import ClaudeModel, OllamaModel, StubModel, build_model


def test_default_is_stub() -> None:
    assert isinstance(build_model(env={}), StubModel)


def test_claude_default_and_override() -> None:
    default = build_model(env={"TIER0_MODEL": "claude"})
    assert isinstance(default, ClaudeModel)
    assert default.model == "claude-opus-5-5"
    assert (
        build_model(env={"TIER0_MODEL": "claude", "TIER0_CLAUDE_MODEL": "other"}).model == "other"
    )


def test_ollama_overrides() -> None:
    model = build_model(
        "ollama", env={"TIER0_OLLAMA_MODEL": "qwen", "TIER0_OLLAMA_URL": "http://box:1234"}
    )
    assert isinstance(model, OllamaModel)
    assert model.model == "qwen"


def test_unknown_name_raises() -> None:
    with pytest.raises(ValueError, match="stub, claude, ollama"):
        build_model("gpt", env={})
