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


def test_ollama_temperature_from_env() -> None:
    from tier0.models import build_model

    assert build_model("ollama", env={"TIER0_OLLAMA_TEMPERATURE": "0.9"}).temperature == 0.9
    assert build_model("ollama", env={}).temperature == 0.5


def test_claude_prompt_cache_is_on_only_for_exactly_1() -> None:
    def describe(env: dict[str, str]) -> dict[str, str]:
        return build_model(env={"TIER0_MODEL": "claude", **env}).describe()

    assert "prompt_cache" in describe({"TIER0_CLAUDE_PROMPT_CACHE": "1"})
    for value in ("0", "", "true"):
        assert "prompt_cache" not in describe({"TIER0_CLAUDE_PROMPT_CACHE": value})
    assert "prompt_cache" not in describe({})
