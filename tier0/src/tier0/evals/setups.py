"""The four setups milestone 3 compares on held-out v3 (ADR-030).

A setup is a model per task plus the prompt version each task uses. Every setup is
tuned the same way, on tuning data only, and frozen by hash before v3 runs.
"""

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from tier0.agent import Prompts
from tier0.evals.prompt_history import M3_CHECK_VERSIONS, M3_TRIAGE_VERSIONS
from tier0.models import ClaudeModel, ModelAdapter, OllamaModel
from tier0.models.ollama import DEFAULT_OLLAMA_URL
from tier0.models.routed import RoutedModel

HAIKU = "claude-haiku-5-5"


@dataclass(frozen=True, slots=True)
class Setup:
    name: str
    provider: Literal["ollama", "claude"]
    triage_model: str
    check_model: str
    triage_prompt: str  # a key of M3_TRIAGE_VERSIONS
    check_prompt: str  # a key of M3_CHECK_VERSIONS

    @property
    def label(self) -> str:
        if self.triage_model == self.check_model:
            return self.triage_model
        return f"{self.triage_model}+{self.check_model}"

    def prompts(self) -> Prompts:
        return Prompts(
            triage=M3_TRIAGE_VERSIONS[self.triage_prompt],
            check=M3_CHECK_VERSIONS[self.check_prompt],
        )

    def factory(self, temperature: float = 0.5) -> Callable[[], ModelAdapter]:
        url = os.environ.get("TIER0_OLLAMA_URL", DEFAULT_OLLAMA_URL)

        def one(model: str) -> ModelAdapter:
            if self.provider == "claude":
                return ClaudeModel(model=model)
            return OllamaModel(model=model, base_url=url, temperature=temperature)

        def make() -> ModelAdapter:
            if self.triage_model == self.check_model:
                return one(self.triage_model)
            return RoutedModel(triage=one(self.triage_model), check=one(self.check_model))  # type: ignore[return-value]

        return make


# Chosen prompt versions are filled in when the selection report freezes them (ADR-030).
SETUPS: dict[str, Setup] = {
    s.name: s
    for s in (
        Setup("llama3.2", "ollama", "llama3.2", "llama3.2", "T1", "C1"),
        Setup("llama3.1:8b", "ollama", "llama3.1:8b", "llama3.1:8b", "T1", "C1"),
        Setup("mixed", "ollama", "llama3.2", "llama3.1:8b", "T1", "C1"),
        Setup("haiku", "claude", HAIKU, HAIKU, "T1", "C1"),
    )
}
