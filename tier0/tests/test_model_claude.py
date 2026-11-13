"""Claude adapter tests against a mock transport: no network, no API key."""

import json
from collections.abc import Callable
from pathlib import Path
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


# --- milestone 3: Haiku, the key from .env only, the spend cap ----------------------


def test_haiku_sends_no_fallback_no_temperature_and_no_thinking() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json=message() | {"model": "claude-haiku-5-5"})

    model = make_model(handler)
    model.model = "claude-haiku-5-5"
    model.complete(ModelRequest(task="triage", system="s", user="u", json_schema=TRIAGE_SCHEMA))
    body = json.loads(seen[0].content)
    assert body["model"] == "claude-haiku-5-5"
    assert "fallbacks" not in body and "anthropic-beta" not in seen[0].headers
    assert body["thinking"] == {"type": "disabled"}
    assert body["output_config"]["effort"] == "low"
    assert "temperature" not in body and "top_p" not in body
    assert model.describe()["sampling"].startswith("API default")


def test_the_key_comes_from_dotenv_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tier0 import secrets

    dotenv = tmp_path / ".env"
    dotenv.write_text('# a comment\nexport ANTHROPIC_API_KEY="sk-from-dotenv"\n')
    assert secrets.read_api_key(dotenv) == "sk-from-dotenv"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-the-shell")
    assert secrets.read_api_key(dotenv) == "sk-from-dotenv"  # the shell is ignored
    with pytest.raises(secrets.MissingKeyError) as missing:
        secrets.read_api_key(tmp_path / "nope.env")
    assert "sk-" not in str(missing.value)


def test_the_client_is_built_with_the_dotenv_key_and_the_real_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tier0.models.claude as claude

    built: dict[str, Any] = {}

    class FakeAnthropic:
        def __init__(self, **kwargs: Any) -> None:
            import os

            built.update(kwargs, env_key=os.environ.get("ANTHROPIC_API_KEY"))
            self.messages = self

        def create(self, **kwargs: Any) -> Any:
            raise anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x"))

    fake_module = type("M", (), {"Anthropic": FakeAnthropic, "APIError": anthropic.APIError})
    monkeypatch.setattr(claude, "_import_anthropic", lambda: fake_module)
    monkeypatch.setattr(claude, "read_api_key", lambda: "sk-secret-from-dotenv")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-the-shell")
    model = ClaudeModel(model="claude-haiku-5-5")
    with pytest.raises(ModelError) as failed:
        model.complete(ModelRequest(task="triage", system="s", user="u"))
    assert built["api_key"] == "sk-secret-from-dotenv"
    assert built["base_url"] == "https://api.anthropic.com"
    assert built["env_key"] is None  # the shell's key was hidden while building
    # The key never shows up in an error, a repr or the provenance.
    for text in (str(failed.value), repr(model), json.dumps(model.describe())):
        assert "sk-secret" not in text


def test_dotenv_is_gitignored() -> None:
    import shutil
    import subprocess

    from tier0.secrets import REPO_ROOT

    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not installed")
    result = subprocess.run(  # noqa: S603 - a fixed git command, no outside input
        [git, "check-ignore", "-q", ".env"], cwd=REPO_ROOT, check=False
    )
    assert result.returncode == 0


def test_every_call_is_ledgered_and_the_cap_stops_the_next(tmp_path: Path) -> None:
    from tier0.models.spend import SpendCapReached, SpendLedger

    ledger = SpendLedger(path=tmp_path / "spend.jsonl", cap_usd=0.01)
    model, seen = capture(message() | {"model": "claude-haiku-5-5"})
    model.model = "claude-haiku-5-5"
    model._ledger = ledger
    model.complete(ModelRequest(task="triage", system="s", user="u"))  # under the cap
    line = json.loads((tmp_path / "spend.jsonl").read_text().splitlines()[0])
    assert line["input_tokens"] == 42 and line["output_tokens"] == 7
    assert line["cost_usd"] == pytest.approx(42 * 0.10e-6 + 7 * 0.50e-6)
    assert set(line) == {"at", "model", "input_tokens", "output_tokens", "cost_usd"}
    # Push the total over the cap: the next call is refused before anything is sent.
    ledger.record("claude-haiku-5-5", 20_000, 20_000)
    with pytest.raises(SpendCapReached):
        model.complete(ModelRequest(task="triage", system="s", user="u"))
    assert len(seen) == 1
    assert not issubclass(SpendCapReached, ModelError)  # the agent can't swallow it


def test_local_models_cost_nothing_and_unknown_claude_models_have_no_silent_price() -> None:
    from tier0.models.pricing import cost_usd

    assert cost_usd("ollama", "llama3.2", 10_000, 10_000) == 0.0
    assert cost_usd("anthropic", "claude-haiku-5-5", 1_000_000, 1_000_000) == pytest.approx(0.60)
    with pytest.raises(KeyError):
        cost_usd("anthropic", "claude-unknown", 1, 1)


def test_an_unknown_model_id_is_ledgered_at_the_highest_rate_and_never_raises(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Finding 1: the API may answer under an id we have no price for (a fallback, a dated
    id). The call is already paid for, so it's written down, never raised."""
    from tier0.models.pricing import highest_rate
    from tier0.models.spend import SpendLedger

    ledger = SpendLedger(path=tmp_path / "spend.jsonl", cap_usd=4.0)
    model, _ = capture(message() | {"model": "claude-fable-5-2-20261101"})
    model.model = "claude-sonnet-5-5"
    model._ledger = ledger
    with caplog.at_level("WARNING"):
        response = model.complete(ModelRequest(task="triage", system="s", user="u"))
    assert response.text  # the reply still comes back
    line = json.loads((tmp_path / "spend.jsonl").read_text().splitlines()[0])
    per_in, per_out = highest_rate()
    assert line["cost_usd"] == pytest.approx(42 * per_in / 1e6 + 7 * per_out / 1e6)
    assert line["priced_as"] == "highest known rate (unknown model id)"
    assert "no price for claude-fable-5-2-20261101" in caplog.text


def test_the_cap_stops_a_call_whose_worst_case_would_cross_it(tmp_path: Path) -> None:
    """Finding 5: refused before sending when spent + worst case > cap, not only once the
    cap is already reached."""
    from tier0.models.pricing import worst_case_usd
    from tier0.models.spend import SpendCapReached, SpendLedger

    ledger = SpendLedger(path=tmp_path / "spend.jsonl", cap_usd=1.0)
    ledger.record("claude-sonnet-5-5", 0, 99_000)  # $0.99 spent, under the cap
    model, seen = capture(message() | {"model": "claude-sonnet-5-5"})
    model.model = "claude-sonnet-5-5"
    model._ledger = ledger
    request = ModelRequest(task="triage", system="s", user="u")  # 1,024 output tokens: $0.01+
    assert worst_case_usd("claude-sonnet-5-5", 0, request.max_tokens) > 0.01
    with pytest.raises(SpendCapReached, match="could cost up to"):
        model.complete(request)
    assert seen == []  # nothing was sent
    ledger.check(0.005)  # a cheaper call would still fit
    with pytest.raises(SpendCapReached):
        ledger.check(0.011)
