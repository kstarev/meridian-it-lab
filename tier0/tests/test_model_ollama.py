"""Ollama adapter tests against a mock transport: no server needed."""

import json
from collections.abc import Callable
from typing import Any

import httpx2
import pytest

from tier0.models import TRIAGE_SCHEMA, ModelError, ModelRequest, OllamaModel


def make_model(handler: Callable[[httpx2.Request], httpx2.Response]) -> OllamaModel:
    client = httpx2.Client(
        base_url="http://ollama.example", transport=httpx2.MockTransport(handler)
    )
    return OllamaModel(client=client)


def capture(payload: dict[str, Any]) -> tuple[OllamaModel, list[httpx2.Request]]:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json=payload)

    return make_model(handler), seen


REPLY = {
    "model": "llama3.2",
    "message": {"role": "assistant", "content": "hi"},
    "done_reason": "stop",
    "prompt_eval_count": 11,
    "eval_count": 3,
}


def test_request_shape_with_schema() -> None:
    model, seen = capture(REPLY)
    model.complete(
        ModelRequest(
            task="triage", system="sys", user="usr", json_schema=TRIAGE_SCHEMA, max_tokens=50
        )
    )
    assert seen[0].url.path == "/api/chat"
    assert json.loads(seen[0].content) == {
        "model": "llama3.2",
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "usr"},
        ],
        "stream": False,
        "options": {"temperature": 0, "num_predict": 50},
        "format": TRIAGE_SCHEMA,
    }


def test_no_format_without_schema() -> None:
    model, seen = capture(REPLY)
    model.complete(ModelRequest(task="reply", system="s", user="u"))
    assert "format" not in json.loads(seen[0].content)


def test_response_mapping() -> None:
    model, _ = capture(REPLY)
    response = model.complete(ModelRequest(task="reply", system="s", user="u"))
    assert response.text == "hi"
    assert response.model == "llama3.2"
    assert (response.input_tokens, response.output_tokens) == (11, 3)
    assert response.finish_reason == "stop"
    assert response.response_id is None


def test_length_and_missing_counts() -> None:
    model, _ = capture({"message": {"content": "x"}, "done_reason": "length"})
    response = model.complete(ModelRequest(task="reply", system="s", user="u"))
    assert response.finish_reason == "length"
    assert (response.input_tokens, response.output_tokens) == (0, 0)


def test_http_500_becomes_model_error() -> None:
    model = make_model(lambda _request: httpx2.Response(500, text="boom"))
    with pytest.raises(ModelError):
        model.complete(ModelRequest(task="reply", system="s", user="u"))


def test_connection_error_becomes_model_error() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    with pytest.raises(ModelError):
        make_model(handler).complete(ModelRequest(task="reply", system="s", user="u"))


def test_non_json_body_becomes_model_error() -> None:
    model = make_model(lambda _request: httpx2.Response(200, text="not json"))
    with pytest.raises(ModelError):
        model.complete(ModelRequest(task="reply", system="s", user="u"))
