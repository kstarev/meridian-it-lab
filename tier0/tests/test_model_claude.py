"""Claude adapter tests against a mock transport: no network, no API key."""

import json
from collections.abc import Callable
from typing import Any

import anthropic
import httpx2
import pytest

from tier0.models import TRIAGE_SCHEMA, ClaudeModel, ModelError, ModelRequest
from tier0.models.claude import FALLBACK_BETA


def message(
    content: list[dict[str, Any]] | None = None, stop_reason: str = "end_turn"
) -> dict[str, Any]:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": content if content is not None else [{"type": "text", "text": "hello"}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 42, "output_tokens": 7},
    }


def make_model(
    handler: Callable[[httpx2.Request], httpx2.Response],
) -> ClaudeModel:
    client = anthropic.Anthropic(
        api_key="test-key-not-real",
        base_url="https://api.anthropic.example",
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)),
        max_retries=0,
    )
    return ClaudeModel(client=client)


def capture(payload: dict[str, Any]) -> tuple[ClaudeModel, list[httpx2.Request]]:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json=payload)

    return make_model(handler), seen


def test_triage_request_shape() -> None:
    model, seen = capture(message())
    model.complete(
        ModelRequest(
            task="triage", system="sys", user="what is my username", json_schema=TRIAGE_SCHEMA
        )
    )
    request = seen[0]
    body = json.loads(request.content)
    assert request.url.path == "/v1/messages"
    assert FALLBACK_BETA in request.headers["anthropic-beta"]
    assert body["model"] == "claude-opus-5-5"
    assert body["fallbacks"] == "default"
    assert body["output_config"]["effort"] == "low"
    assert body["output_config"]["format"] == {"type": "json_schema", "schema": TRIAGE_SCHEMA}
    assert body["system"] == "sys"
    assert body["messages"] == [{"role": "user", "content": "what is my username"}]
    assert "thinking" not in body
    assert "temperature" not in body


def test_reply_request_has_no_format() -> None:
    model, seen = capture(message())
    model.complete(ModelRequest(task="triage", system="sys", user="hi"))
    assert "format" not in json.loads(seen[0].content)["output_config"]


def test_response_mapping() -> None:
    model, _ = capture(message())
    response = model.complete(ModelRequest(task="triage", system="s", user="u"))
    assert response.text == "hello"
    assert response.model == "claude-opus-5-5"
    assert (response.input_tokens, response.output_tokens) == (42, 7)
    assert response.response_id == "msg_test"
    assert response.finish_reason == "stop"


def test_max_tokens_maps_to_length() -> None:
    model, _ = capture(message(stop_reason="max_tokens"))
    assert (
        model.complete(ModelRequest(task="triage", system="s", user="u")).finish_reason == "length"
    )


def test_thinking_block_is_skipped() -> None:
    content = [
        {"type": "thinking", "thinking": "hmm", "signature": "sig"},
        {"type": "text", "text": "answer"},
    ]
    model, _ = capture(message(content))
    assert model.complete(ModelRequest(task="triage", system="s", user="u")).text == "answer"


def test_refusal_returns_empty_text() -> None:
    model, _ = capture(message(stop_reason="refusal"))
    response = model.complete(ModelRequest(task="triage", system="s", user="u"))
    assert response.finish_reason == "refusal"
    assert response.text == ""


def test_http_error_becomes_model_error() -> None:
    model = make_model(lambda _request: httpx2.Response(500, json={"type": "error"}))
    with pytest.raises(ModelError):
        model.complete(ModelRequest(task="triage", system="s", user="u"))


def test_building_without_client_needs_no_key_or_network() -> None:
    model = ClaudeModel()
    assert model.provider == "anthropic"
    assert model.model == "claude-opus-5-5"
