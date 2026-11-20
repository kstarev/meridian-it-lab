"""A small Claude client for structured suggestions (the design of tier0's ClaudeModel).

- `anthropic` is imported only when a real client is needed; tests pass a fake one.
- The key comes only from the environment (`ANTHROPIC_API_KEY`); `__repr__` never shows it.
- Structured output: `output_config={"format": {"type": "json_schema", "schema": ...}}`.
  Never a temperature, never a prefill. Haiku has thinking turned off.
- If a request failed after being sent, or its usage can't be read, the worst-case cost is
  written to the ledger (the money may have been spent). The SDK client has no retries and
  a 60 s timeout, so one call is one request.
- A refusal, a cut-off answer (max_tokens), malformed JSON, an object that names a key
  twice (`reply_json`), JSON that doesn't fit the schema, or any API error gives None: no
  suggestion, never a guess.
- Before every call the worst-case cost is checked against the spend ledger (SpendCapReached
  is not swallowed); the actual cost is recorded after.
"""

import json
import os
from typing import Any

from jsonschema import Draft202012Validator

from meridian_access.ai.pricing import HAIKU, worst_case_usd
from meridian_access.ai.reply_json import loads_reply
from meridian_access.ai.spend import SpendLedger

INPUT_OVERHEAD_TOKENS = 512
DEFAULT_MAX_TOKENS = 400
THINKING_OFF_MODELS = frozenset({HAIKU})
KEY_NAME = "ANTHROPIC_API_KEY"
SEALED_NO_CACHE = (
    "refusing: the sealed run was planned and frozen without the prompt cache; "
    "it runs with it only after the owner rules"
)


def request_params(
    model: str,
    system: str,
    user: str,
    schema: dict[str, Any],
    max_tokens: int,
    *,
    cache_system: bool = False,
) -> dict[str, Any]:
    """The exact keyword arguments one call passes to `messages.create`. With `cache_system`
    the system prompt is one text block marked for the 5-minute cache; without it the request
    is exactly what the frozen prompts pin."""
    params: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": (
            [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
            if cache_system
            else system
        ),
        "messages": [{"role": "user", "content": user}],
        "output_config": {"format": {"type": "json_schema", "schema": schema}},
    }
    if model in THINKING_OFF_MODELS:
        params["thinking"] = {"type": "disabled"}
    return params


def worst_case_input_tokens(system: str, user: str, schema: dict[str, Any]) -> int:
    """An upper bound on a call's input tokens: its bytes plus a fixed overhead."""
    return len(json.dumps([system, user, schema]).encode("utf-8")) + INPUT_OVERHEAD_TOKENS


def _count(usage: Any, name: str) -> int:
    """A usage count; 0 when the field is absent or None."""
    value = getattr(usage, name, None)
    return 0 if value is None else int(value)


class MissingKeyError(RuntimeError):
    """No ANTHROPIC_API_KEY in the environment; the message never contains a key."""


class ClaudeClient:
    def __init__(
        self,
        model: str,
        client: Any | None = None,
        ledger: SpendLedger | None = None,
        *,
        cache_system: bool = False,
    ) -> None:
        self.model = model
        self.cache_system = cache_system
        self._client = client
        self.ledger = ledger if ledger is not None else SpendLedger()
        self.calls = 0
        self.cache_write_tokens = 0  # totals over this client's calls (0 unless caching is on)
        self.cache_read_tokens = 0

    def __repr__(self) -> str:  # never shows the client, and so never the key
        return f"ClaudeClient(model={self.model!r})"

    def _record(
        self, model: str, tokens_in: int, tokens_out: int, cache_write: int, cache_read: int
    ) -> None:
        self.cache_write_tokens += cache_write
        self.cache_read_tokens += cache_read
        if self.cache_system:
            self.ledger.record(model, tokens_in, tokens_out, cache_write, cache_read, cached=True)
        else:
            self.ledger.record(model, tokens_in, tokens_out)

    def _sdk_client(self) -> Any:
        if self._client is None:
            key = os.environ.get(KEY_NAME, "")
            if not key:
                raise MissingKeyError(f"set {KEY_NAME} in the environment")
            import anthropic  # lazy: only a real call needs the SDK

            self._client = anthropic.Anthropic(api_key=key, max_retries=0, timeout=60)
        return self._client

    def complete_json(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> dict[str, Any] | None:
        """The model's JSON answer, or None. Raises SpendCapReached before any call that
        could cross the cap, and MissingKeyError when a real client has no key."""
        worst_in = worst_case_input_tokens(system, user, schema)
        self.ledger.check(
            worst_case_usd(self.model, worst_in, max_tokens, cache_write=self.cache_system)
        )
        client = self._sdk_client()
        params = request_params(
            self.model, system, user, schema, max_tokens, cache_system=self.cache_system
        )
        self.calls += 1
        try:
            response = client.messages.create(**params)
        except Exception:  # any API error is "no suggestion"; its text is never echoed
            # the request may have been sent and billed: ledger the worst case
            self._record(self.model, worst_in, max_tokens, 0, 0)
            return None
        try:
            usage = response.usage
            used = (
                str(response.model),
                int(usage.input_tokens),
                int(usage.output_tokens),
                _count(usage, "cache_creation_input_tokens") if self.cache_system else 0,
                _count(usage, "cache_read_input_tokens") if self.cache_system else 0,
            )
        except (AttributeError, TypeError, ValueError):
            used = (self.model, worst_in, max_tokens, 0, 0)  # usage unreadable: assume the worst
        self._record(*used)
        if response.stop_reason in ("refusal", "max_tokens"):
            return None  # on a refusal the content is not read
        try:
            text = "".join(b.text for b in response.content if b.type == "text")
            data = loads_reply(text)  # a doubled key is a ValueError, as malformed JSON is
        except (AttributeError, TypeError, ValueError):
            return None
        if not isinstance(data, dict) or not Draft202012Validator(schema).is_valid(data):
            return None
        return data
