"""Adapter for a local Ollama server (no cloud, no key)."""

from typing import Any

import httpx2

from tier0.models.base import ModelError, ModelRequest, ModelResponse

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "llama3.2"
# The same value for every compared model, so k runs differ and pass^k means something
# (milestone 2 decision).
DEFAULT_TEMPERATURE = 0.5


class OllamaModel:
    """Calls Ollama's /api/chat at a fixed, configurable temperature."""

    provider = "ollama"

    def __init__(
        self,
        *,
        model: str = DEFAULT_OLLAMA_MODEL,
        base_url: str = DEFAULT_OLLAMA_URL,
        client: httpx2.Client | None = None,
        timeout: float = 60.0,
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> None:
        self.model = model
        self.temperature: float | None = temperature
        self._client = client or httpx2.Client(base_url=base_url, timeout=timeout)

    def complete(self, request: ModelRequest) -> ModelResponse:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "stream": False,
            "options": {"temperature": self.temperature, "num_predict": request.max_tokens},
        }
        if request.json_schema is not None:
            # Ollama structured outputs accept a JSON schema here.
            body["format"] = request.json_schema
        try:
            http_response = self._client.post("/api/chat", json=body)
            http_response.raise_for_status()
            data = http_response.json()
            return ModelResponse(
                text=data["message"]["content"],
                model=data.get("model", self.model),
                input_tokens=int(data.get("prompt_eval_count", 0)),
                output_tokens=int(data.get("eval_count", 0)),
                finish_reason=str(data.get("done_reason", "stop")),
                response_id=None,
            )
        except httpx2.HTTPError as exc:
            raise ModelError(f"Ollama request failed: {exc}") from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise ModelError(f"Ollama returned an unexpected response: {exc}") from exc
