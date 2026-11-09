"""Adapter for Anthropic's Claude models.

Server-side fallbacks are on by default, so if a classifier refuses, Anthropic
retries on its recommended fallback model. Effort is "low" because triage and a
two-sentence reply don't need more. No API key is needed unless this adapter is
chosen: the client is only created on the first call.
"""

from typing import Any

from tier0.models.base import ModelError, ModelRequest, ModelResponse

DEFAULT_CLAUDE_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
DEFAULT_EFFORT = "low"

_FINISH_REASONS = {"end_turn": "stop", "max_tokens": "length", "refusal": "refusal"}


def _import_anthropic() -> Any:
    # Lazy so tier0 works without the optional `claude` extra.
    try:
        import anthropic
    except ImportError as exc:
        raise ModelError("install tier0[claude] to use the Claude adapter") from exc
    return anthropic


class ClaudeModel:
    """Calls the Anthropic Messages API (beta, for server-side fallbacks)."""

    provider = "anthropic"
    # The Claude API no longer accepts a temperature, so we never send one.
    temperature: float | None = None
    # The Claude API has no seed parameter either, so runs can't be pinned.
    seed: int | None = None
    supports_seed = False

    def describe(self) -> dict[str, str]:
        return {}

    def __init__(
        self,
        *,
        model: str = DEFAULT_CLAUDE_MODEL,
        effort: str = DEFAULT_EFFORT,
        client: Any | None = None,
    ) -> None:
        self.model = model
        self._effort = effort
        self._client = client

    def complete(self, request: ModelRequest) -> ModelResponse:
        anthropic = _import_anthropic()
        if self._client is None:
            self._client = anthropic.Anthropic()
        # Thinking is always on for this model, so we never send `thinking`,
        # `temperature` or a prefill.
        output_config: dict[str, Any] = {"effort": self._effort}
        if request.json_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": request.json_schema}
        try:
            response = self._client.beta.messages.create(
                model=self.model,
                max_tokens=request.max_tokens,
                system=request.system,
                messages=[{"role": "user", "content": request.user}],
                betas=[FALLBACK_BETA],
                fallbacks="default",
                output_config=output_config,
            )
        except anthropic.APIError as exc:
            raise ModelError(f"Claude request failed: {exc}") from exc

        finish_reason = _FINISH_REASONS.get(response.stop_reason, str(response.stop_reason))
        # On a refusal the content is not meant to be used, so don't read it.
        if response.stop_reason == "refusal":
            text = ""
        else:
            text = "".join(b.text for b in response.content if b.type == "text")
        return ModelResponse(
            text=text,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            finish_reason=finish_reason,
            response_id=response.id,
        )
