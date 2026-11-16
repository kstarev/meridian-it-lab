"""A small Claude client for structured suggestions (the design of tier0's ClaudeModel).

- `anthropic` is imported only when a real client is needed; tests pass a fake one.
- The key comes only from the environment (`ANTHROPIC_API_KEY`); `__repr__` never shows it.
- Structured output: `output_config={"format": {"type": "json_schema", "schema": ...}}`.
  Never a temperature, never a prefill. Haiku has thinking turned off.
- If a request failed after being sent, or its usage can't be read, the worst-case cost is
  written to the ledger (the money may have been spent). The SDK client has no retries and
  a 60 s timeout, so one call is one request.
- A refusal, a cut-off answer (max_tokens), malformed JSON, JSON that doesn't fit the
  schema, or any API error gives None: no suggestion, never a guess.
- Before every call the worst-case cost is checked against the spend ledger (SpendCapReached
  is not swallowed); the actual cost is recorded after.
"""

import json
import os
from typing import Any

from jsonschema import Draft202012Validator

from meridian_access.ai.pricing import HAIKU, worst_case_usd
from meridian_access.ai.spend import SpendLedger

INPUT_OVERHEAD_TOKENS = 512
DEFAULT_MAX_TOKENS = 400
THINKING_OFF_MODELS = frozenset({HAIKU})
KEY_NAME = "ANTHROPIC_API_KEY"


def request_params(
    model: str, system: str, user: str, schema: dict[str, Any], max_tokens: int
) -> dict[str, Any]:
    """The exact keyword arguments one call passes to `messages.create`."""
    params: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "output_config": {"format": {"type": "json_schema", "schema": schema}},
    }
    if model in THINKING_OFF_MODELS:
        params["thinking"] = {"type": "disabled"}
    return params


def worst_case_input_tokens(system: str, user: str, schema: dict[str, Any]) -> int:
    """An upper bound on a call's input tokens: its bytes plus a fixed overhead."""
    return len(json.dumps([system, user, schema]).encode("utf-8")) + INPUT_OVERHEAD_TOKENS


class MissingKeyError(RuntimeError):
    """No ANTHROPIC_API_KEY in the environment; the message never contains a key."""


class ClaudeClient:
    def __init__(
        self,
        model: str,
        client: Any | None = None,
        ledger: SpendLedger | None = None,
    ) -> None:
        self.model = model
        self._client = client
        self.ledger = ledger if ledger is not None else SpendLedger()
        self.calls = 0

    def __repr__(self) -> str:  # never shows the client, and so never the key
        return f"ClaudeClient(model={self.model!r})"

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
        self.ledger.check(worst_case_usd(self.model, worst_in, max_tokens))
        client = self._sdk_client()
        params = request_params(self.model, system, user, schema, max_tokens)
        self.calls += 1
        try:
            response = client.messages.create(**params)
        except Exception:  # any API error is "no suggestion"; its text is never echoed
            # the request may have been sent and billed: ledger the worst case
            self.ledger.record(self.model, worst_in, max_tokens)
            return None
        try:
            used = (
                str(response.model),
                int(response.usage.input_tokens),
                int(response.usage.output_tokens),
            )
        except (AttributeError, TypeError, ValueError):
            used = (self.model, worst_in, max_tokens)  # usage unreadable: assume the worst
        self.ledger.record(*used)
        if response.stop_reason in ("refusal", "max_tokens"):
            return None  # on a refusal the content is not read
        try:
            text = "".join(b.text for b in response.content if b.type == "text")
            data = json.loads(text)
        except (AttributeError, TypeError, ValueError):
            return None
        if not isinstance(data, dict) or not Draft202012Validator(schema).is_valid(data):
            return None
        return data
