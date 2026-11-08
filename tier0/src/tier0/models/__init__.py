"""Model adapters: a stub for offline use, plus Claude and Ollama."""

import os
from collections.abc import Mapping

from tier0.models.base import (
    ABOUT_SOMEONE_ELSE,
    CATEGORIES,
    OTHER,
    OWN_USERNAME,
    TRIAGE_SCHEMA,
    ModelAdapter,
    ModelError,
    ModelRequest,
    ModelResponse,
    Task,
)
from tier0.models.claude import DEFAULT_CLAUDE_MODEL, ClaudeModel
from tier0.models.ollama import DEFAULT_OLLAMA_MODEL, DEFAULT_OLLAMA_URL, OllamaModel
from tier0.models.stub import StubModel

__all__ = [
    "ABOUT_SOMEONE_ELSE",
    "CATEGORIES",
    "OTHER",
    "OWN_USERNAME",
    "TRIAGE_SCHEMA",
    "ClaudeModel",
    "ModelAdapter",
    "ModelError",
    "ModelRequest",
    "ModelResponse",
    "OllamaModel",
    "StubModel",
    "Task",
    "build_model",
]

_CHOICES = ("stub", "claude", "ollama")


def build_model(name: str | None = None, *, env: Mapping[str, str] | None = None) -> ModelAdapter:
    """Pick a model by name or TIER0_MODEL. Defaults to the offline stub."""
    env = os.environ if env is None else env
    name = name or env.get("TIER0_MODEL", "stub")
    if name == "stub":
        return StubModel()
    if name == "claude":
        return ClaudeModel(model=env.get("TIER0_CLAUDE_MODEL", DEFAULT_CLAUDE_MODEL))
    if name == "ollama":
        return OllamaModel(
            model=env.get("TIER0_OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL),
            base_url=env.get("TIER0_OLLAMA_URL", DEFAULT_OLLAMA_URL),
        )
    raise ValueError(f"unknown model {name!r}; choose one of: {', '.join(_CHOICES)}")
