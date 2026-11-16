"""The contract between the agent and any language model.

The agent only ever sees `ModelAdapter`, so the stub, Claude and Ollama are
interchangeable and the safety rules never depend on which one is plugged in.
"""

from dataclasses import dataclass
from typing import Any, Literal, Protocol

OWN_USERNAME = "own_username"
ABOUT_SOMEONE_ELSE = "about_someone_else"
OTHER = "other"
CATEGORIES = (OWN_USERNAME, ABOUT_SOMEONE_ELSE, OTHER)

# Passed to models that support structured output so triage is always one known label.
TRIAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"category": {"type": "string", "enum": list(CATEGORIES)}},
    "required": ["category"],
    "additionalProperties": False,
}

# The separate yes/no question asked before any token is issued (tier0.agent).
OWN_ACCOUNT_CHECK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"own_account_only": {"type": "boolean"}},
    "required": ["own_account_only"],
    "additionalProperties": False,
}

# The model only classifies; replies come from code templates (ADR-028). "guide_pick" is
# milestone 4: the model picks which approved guide answers a how-to question (ADR-035).
type Task = Literal["triage", "own_account_check", "guide_pick"]


@dataclass(frozen=True, slots=True)
class ModelRequest:
    """One model call: a system prompt, the untrusted user text and an optional schema."""

    task: Task
    system: str
    user: str
    json_schema: dict[str, Any] | None = None
    max_tokens: int = 1024


@dataclass(frozen=True, slots=True)
class ModelResponse:
    """What came back. `finish_reason` is "stop", "length", "refusal" or the raw provider value."""

    text: str
    model: str
    input_tokens: int
    output_tokens: int
    finish_reason: str
    response_id: str | None


class ModelError(Exception):
    """Any provider or transport failure, so callers catch one type."""


# Run r of an eval uses sampling seed base + r, so runs differ but the eval can be re-run.
DEFAULT_MODEL_SEED_BASE = 1000


class ModelAdapter(Protocol):
    """Anything that can answer a `ModelRequest`."""

    # Value for the OpenTelemetry `gen_ai.provider.name` attribute.
    provider: str
    # The model we asked for (the response may report a different one).
    model: str
    # Sampling temperature we send, or None when the adapter sends none.
    temperature: float | None
    # Sampling seed we send, or None. Only adapters with `supports_seed` use it.
    seed: int | None
    supports_seed: bool

    def complete(self, request: ModelRequest) -> ModelResponse: ...

    def describe(self) -> dict[str, str]:
        """What is needed to reproduce results (versions, digests); {} when nothing applies."""
        ...
