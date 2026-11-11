"""Adapter for Anthropic's Claude models.

Effort is "low" because triage and the yes/no own-account check don't need more.
Server-side fallbacks are on for the models that offer them (Opus, Sonnet, Fable), so
if a classifier refuses, Anthropic retries on its recommended fallback model. Claude
Haiku 5.5 has no server-side fallback, so none is sent; its thinking is turned off,
which it allows at low effort, to keep a one-word label cheap and fast.

The Claude API accepts no temperature and no seed, so runs can't be pinned the way
Ollama runs are; reports say so. The key comes from `.env` only (tier0.secrets): the
client is built with it explicitly, with the shell's Anthropic variables hidden, and
only on the first call. Every paid call is checked against the spend cap first and
recorded in the spend ledger after (tier0.models.spend).
"""

import contextlib
import os
from collections.abc import Iterator
from typing import Any

from tier0.models.base import ModelError, ModelRequest, ModelResponse
from tier0.models.spend import SpendLedger
from tier0.netguard import ANTHROPIC_API_HOST
from tier0.secrets import read_api_key

DEFAULT_CLAUDE_MODEL = "claude-opus-5-5"
# Models with server-side fallbacks; any other model is called without them.
FALLBACK_MODELS = frozenset({"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-2"})
# Models whose thinking can be turned off at low effort.
THINKING_OFF_MODELS = frozenset({"claude-haiku-5-5"})
API_BASE_URL = f"https://{ANTHROPIC_API_HOST}"
# Environment variables the SDK would otherwise read for credentials or a base URL.
_SDK_ENV = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_PROFILE",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_FEDERATION_RULE_ID",
    "ANTHROPIC_IDENTITY_TOKEN",
    "ANTHROPIC_IDENTITY_TOKEN_FILE",
)
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
        return {
            "model_id": self.model,
            "sampling": "API default: the Claude API accepts no temperature and no seed",
        }

    def __init__(
        self,
        *,
        model: str = DEFAULT_CLAUDE_MODEL,
        effort: str = DEFAULT_EFFORT,
        client: Any | None = None,
        ledger: SpendLedger | None = None,
    ) -> None:
        self.model = model
        self._effort = effort
        self._client = client
        self._ledger = ledger if ledger is not None else SpendLedger()

    def __repr__(self) -> str:  # never shows the client, and so never the key
        return f"ClaudeModel(model={self.model!r}, effort={self._effort!r})"

    def complete(self, request: ModelRequest) -> ModelResponse:
        anthropic = _import_anthropic()
        self._ledger.check()  # before any paid call: raises SpendCapReached
        if self._client is None:
            with _without_sdk_env():
                self._client = anthropic.Anthropic(api_key=read_api_key(), base_url=API_BASE_URL)
        # Never `temperature` or a prefill; thinking only where it can be turned off.
        output_config: dict[str, Any] = {"effort": self._effort}
        if request.json_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": request.json_schema}
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": [{"role": "user", "content": request.user}],
            "output_config": output_config,
        }
        if self.model in THINKING_OFF_MODELS:
            params["thinking"] = {"type": "disabled"}
        try:
            if self.model in FALLBACK_MODELS:
                response = self._client.beta.messages.create(
                    **params, betas=[FALLBACK_BETA], fallbacks="default"
                )
            else:
                response = self._client.messages.create(**params)
        except anthropic.APIError as exc:
            # The status and type only: an SDK error message never needs to be echoed.
            raise ModelError(
                f"Claude request failed: {type(exc).__name__} "
                f"{getattr(exc, 'status_code', '')}".rstrip()
            ) from exc
        self._ledger.record(
            str(response.model), response.usage.input_tokens, response.usage.output_tokens
        )

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


@contextlib.contextmanager
def _without_sdk_env() -> Iterator[None]:
    """Hide the SDK's credential and base-URL variables while the client is built, so
    only the key from .env and the fixed API host are used."""
    saved = {name: os.environ.pop(name) for name in _SDK_ENV if name in os.environ}
    try:
        yield
    finally:
        os.environ.update(saved)
