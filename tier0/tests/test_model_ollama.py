"""Ollama adapter tests against a mock transport: no server needed."""

import json
from collections.abc import Callable
from typing import Any

import httpx2
import pytest

from tier0.models import (
    TRIAGE_SCHEMA,
    ClaudeModel,
    ModelError,
    ModelRequest,
    OllamaModel,
    StubModel,
)


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
        "options": {"temperature": 0.5, "num_predict": 50},
        "format": TRIAGE_SCHEMA,
    }


def test_no_format_without_schema() -> None:
    model, seen = capture(REPLY)
    model.complete(ModelRequest(task="triage", system="s", user="u"))
    assert "format" not in json.loads(seen[0].content)


def test_response_mapping() -> None:
    model, _ = capture(REPLY)
    response = model.complete(ModelRequest(task="triage", system="s", user="u"))
    assert response.text == "hi"
    assert response.model == "llama3.2"
    assert (response.input_tokens, response.output_tokens) == (11, 3)
    assert response.finish_reason == "stop"
    assert response.response_id is None


def test_length_and_missing_counts() -> None:
    model, _ = capture({"message": {"content": "x"}, "done_reason": "length"})
    response = model.complete(ModelRequest(task="triage", system="s", user="u"))
    assert response.finish_reason == "length"
    assert (response.input_tokens, response.output_tokens) == (0, 0)


def test_http_500_becomes_model_error() -> None:
    model = make_model(lambda _request: httpx2.Response(500, text="boom"))
    with pytest.raises(ModelError):
        model.complete(ModelRequest(task="triage", system="s", user="u"))


def test_connection_error_becomes_model_error() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    with pytest.raises(ModelError):
        make_model(handler).complete(ModelRequest(task="triage", system="s", user="u"))


def test_non_json_body_becomes_model_error() -> None:
    model = make_model(lambda _request: httpx2.Response(200, text="not json"))
    with pytest.raises(ModelError):
        model.complete(ModelRequest(task="triage", system="s", user="u"))


def test_temperature_default_and_override() -> None:
    model, seen = capture(REPLY)
    model.complete(ModelRequest(task="triage", system="s", user="u"))
    assert json.loads(seen[0].content)["options"]["temperature"] == 0.5
    assert model.temperature == 0.5
    client = httpx2.Client(
        base_url="http://x",
        transport=httpx2.MockTransport(lambda r: httpx2.Response(200, json=REPLY)),
    )
    assert OllamaModel(client=client, temperature=0.2).temperature == 0.2


# --- seed and describe --------------------------------------------------------------


def test_seed_sent_only_when_set() -> None:
    model, seen = capture(REPLY)
    model.complete(ModelRequest(task="triage", system="s", user="u"))
    assert "seed" not in json.loads(seen[0].content)["options"]
    model.seed = 1001
    model.complete(ModelRequest(task="triage", system="s", user="u"))
    assert json.loads(seen[1].content)["options"]["seed"] == 1001
    assert model.supports_seed is True


DIGEST = "a80c4f17acd55265feec403c7aef86be0c25983ab279d83f3bcd3abbcb5b8b72"


def describing(model_name: str, tags: Any, version: Any = None) -> OllamaModel:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/api/version":
            return httpx2.Response(200, json={"version": "0.6.2"} if version is None else version)
        return (
            httpx2.Response(200, json=tags)
            if request.url.path == "/api/tags"
            else httpx2.Response(404)
        )

    client = httpx2.Client(base_url="http://x", transport=httpx2.MockTransport(handler))
    return OllamaModel(client=client, model=model_name)


@pytest.mark.parametrize(
    ("asked", "listed"), [("llama3.2", "llama3.2:latest"), ("llama3.2:3b", "llama3.2:3b")]
)
def test_describe_finds_digest(asked: str, listed: str) -> None:
    tags = {
        "models": [{"name": "other:latest", "digest": "f" * 64}, {"name": listed, "digest": DIGEST}]
    }
    assert describing(asked, tags).describe() == {
        "ollama_version": "0.6.2",
        "model_digest": DIGEST,
        "model_id": "a80c4f17acd5",
    }


def test_describe_errors_become_model_error() -> None:
    with pytest.raises(ModelError):
        describing("llama3.2", {"models": [{"name": "x:latest", "digest": DIGEST}]}).describe()
    with pytest.raises(ModelError):
        describing("llama3.2", {"nope": 1}).describe()
    with pytest.raises(ModelError):
        describing("llama3.2", {"models": []}, version={"nope": 1}).describe()
    bad = httpx2.Client(
        base_url="http://x",
        transport=httpx2.MockTransport(lambda r: httpx2.Response(200, content=b"not json")),
    )
    with pytest.raises(ModelError):
        OllamaModel(client=bad).describe()
    assert StubModel().describe() == {} and ClaudeModel().describe() == {}
