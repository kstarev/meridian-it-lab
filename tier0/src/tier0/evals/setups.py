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
SONNET = "claude-sonnet-5-5"
# Runs per ticket on held-out v3: the Claude setups get 5 (the user's budget, ADR-032);
# every setup is still compared at pass^3, on its first 3 runs.
V3_K = {"claude": 5, "ollama": 3}
COMPARISON_K = 3  # the one definition; runner, report and compare_many import it
GPT_OSS = "gpt-oss:20b"
QWEN = "qwen3.6:27b"
# Too big to share a 32 GB Mac with each other: one at a time, never paired (ADR-031).
BIG_MODELS = frozenset({GPT_OSS, QWEN})
# Reasoning settings, recorded in every report's provenance: gpt-oss at its lowest
# level; Qwen with thinking off, like Haiku.
THINK: dict[str, bool | str] = {GPT_OSS: "low", QWEN: False}
# No constrained decoding in this Ollama build, so free text and the strict parsers
# (the user's choice, ADR-031).
NO_STRUCTURED_OUTPUT = frozenset({QWEN})
# No schema enforcement at all: Qwen gets no structured output, and gpt-oss is sent the
# schema but Ollama doesn't hold it to it (tuning: other keys, and "true" as a string).
# Every report and the comparison say so, with off-format counts (ADR-031).
# Model-specific known limits, added to every report for that model (the user's wording).
MODEL_LIMITS: dict[str, tuple[str, ...]] = {
    QWEN: (
        "qwen3.6:27b needs more than 32 GB to run without swapping; its speed here doesn't "
        "reflect its speed on suitable hardware.",
    ),
}
NO_SCHEMA_ENFORCEMENT: dict[str, str] = {
    GPT_OSS: "the schema is sent as Ollama's format, but gpt-oss isn't held to it",
    QWEN: "no structured output in this Ollama build; the schema is in the system message",
}


def timeout_for(model: str) -> float:
    """Big models get longer: loading 15-20 GB and reasoning both take time."""
    return 180.0 if model in BIG_MODELS else 60.0


@dataclass(frozen=True, slots=True)
class Setup:
    name: str
    provider: Literal["ollama", "claude"]
    triage_model: str
    check_model: str
    triage_prompt: str  # a key of M3_TRIAGE_VERSIONS
    check_prompt: str  # a key of M3_CHECK_VERSIONS

    def __post_init__(self) -> None:
        if self.triage_model != self.check_model and {self.triage_model, self.check_model} <= (
            BIG_MODELS
        ):
            raise ValueError(f"{self.name}: two big models can't share a 32 GB Mac (ADR-031)")

    @property
    def v3_k(self) -> int:
        return V3_K[self.provider]

    @property
    def models(self) -> frozenset[str]:
        return frozenset({self.triage_model, self.check_model})

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
            return OllamaModel(
                model=model,
                base_url=url,
                temperature=temperature,
                think=THINK.get(model),
                timeout=timeout_for(model),
                structured_output=model not in NO_STRUCTURED_OUTPUT,
                schema_enforced=model not in NO_SCHEMA_ENFORCEMENT,
            )

        def make() -> ModelAdapter:
            if self.triage_model == self.check_model:
                return one(self.triage_model)
            return RoutedModel(triage=one(self.triage_model), check=one(self.check_model))  # type: ignore[return-value]

        return make


# Frozen from the tuning scores by the selection rule (ADR-030, ADR-031, ADR-032): the
# llamas and Haiku on 2026-11-11, Sonnet, gpt-oss and Qwen on 2026-11-12. Tests pin
# each prompt's hash. Nothing runs on v3 until every setup is in FROZEN.
SETUPS: dict[str, Setup] = {
    s.name: s
    for s in (
        Setup("llama3.2", "ollama", "llama3.2", "llama3.2", "T1", "C1"),
        Setup("llama3.1:8b", "ollama", "llama3.1:8b", "llama3.1:8b", "T2", "C2"),
        Setup("mixed", "ollama", "llama3.2", "llama3.1:8b", "T1", "C2"),
        Setup("haiku", "claude", HAIKU, HAIKU, "T1", "C1"),
        # Added before anything ran on v3, with the $4 budget (ADR-032).
        Setup("sonnet", "claude", SONNET, SONNET, "T1", "C1"),
        # Added before anything ran on v3 (ADR-031); not run until the user says the
        # downloads are done.
        Setup("gpt-oss:20b", "ollama", GPT_OSS, GPT_OSS, "T1", "GC5"),
        Setup("qwen3.6:27b", "ollama", QWEN, QWEN, "T1", "QC5"),
    )
}
FROZEN = frozenset(SETUPS)  # all seven, 2026-11-12

# Milestone 3b (ADR-033, ADR-038): Haiku and Sonnet re-tuned on the harder tuning set, picked
# by the same rule and frozen on 2026-11-15, before anything ran on held-out v4 or v4b. They
# are kept apart from the seven 3a setups, which stay as they were so that the v3 comparison
# can be rerun. Tests pin each prompt's hash.
SETUPS_3B: dict[str, Setup] = {
    s.name: s
    for s in (
        Setup("haiku-3b", "claude", HAIKU, HAIKU, "HT4", "C1"),
        Setup("sonnet-3b", "claude", SONNET, SONNET, "ST4", "SC4"),
    )
}
ALL_SETUPS: dict[str, Setup] = SETUPS | SETUPS_3B
