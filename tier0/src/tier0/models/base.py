"""The contract between the agent and any language model.

The agent only ever sees `ModelAdapter`, so the stub, Claude and Ollama are
interchangeable and the safety rules never depend on which one is plugged in.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
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

type Task = Literal["triage", "reply"]


@dataclass(frozen=True, slots=True)
class ModelRequest:
    """One model call. `facts` are tool results (e.g. {"username": ...}).

    Real models get the facts inside the prompt text; the stub reads them directly.
    """

    task: Task
    system: str
    user: str
    json_schema: dict[str, Any] | None = None
    facts: Mapping[str, str] = field(default_factory=dict)
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


class ModelAdapter(Protocol):
    """Anything that can answer a `ModelRequest`."""

    # Value for the OpenTelemetry `gen_ai.provider.name` attribute.
    provider: str
    # The model we asked for (the response may report a different one).
    model: str

    def complete(self, request: ModelRequest) -> ModelResponse: ...
