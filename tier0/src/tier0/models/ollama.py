"""Adapter for a local Ollama server (no cloud, no key)."""

from collections.abc import Collection, Iterable
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
    supports_seed = True

    def __init__(
        self,
        *,
        model: str = DEFAULT_OLLAMA_MODEL,
        base_url: str = DEFAULT_OLLAMA_URL,
        client: httpx2.Client | None = None,
        timeout: float = 60.0,
        temperature: float = DEFAULT_TEMPERATURE,
        seed: int | None = None,
        think: bool | str | None = None,
    ) -> None:
        self.model = model
        self.temperature: float | None = temperature
        self.seed: int | None = seed
        # Reasoning models only: False turns thinking off, or a level such as "low"
        # (gpt-oss). None sends nothing, so the model's default applies.
        self.think = think
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
        if self.seed is not None:
            body["options"]["seed"] = self.seed
        if self.think is not None:
            body["think"] = self.think
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

    def describe(self) -> dict[str, str]:
        """Ollama version and the model's digest, so a report says exactly what ran."""
        try:
            version_response = self._client.get("/api/version")
            version_response.raise_for_status()
            version = str(version_response.json()["version"])
            tags_response = self._client.get("/api/tags")
            tags_response.raise_for_status()
            models = tags_response.json()["models"]
            wanted = self.model if ":" in self.model else f"{self.model}:latest"
            digest = next(str(m["digest"]) for m in models if m["name"] == wanted)
        except httpx2.HTTPError as exc:
            raise ModelError(f"Ollama describe failed: {exc}") from exc
        except StopIteration:
            raise ModelError(f"Ollama does not list the model {self.model!r}") from None
        except (ValueError, KeyError, TypeError) as exc:
            raise ModelError(f"Ollama returned an unexpected response: {exc}") from exc
        described = {"ollama_version": version, "model_digest": digest, "model_id": digest[:12]}
        if self.think is not None:
            described["think"] = str(self.think).lower()
        return described


def _tagged(name: str) -> str:
    return name if ":" in name else f"{name}:latest"


def make_room(
    wanted: Iterable[str],
    *,
    big: Collection[str],
    base_url: str = DEFAULT_OLLAMA_URL,
    client: httpx2.Client | None = None,
) -> list[str]:
    """Unload what would share memory with a big model, so two big models are never
    loaded together (a 32 GB Mac, ADR-031). When a wanted model is big, every other
    loaded model goes; otherwise only loaded big ones do. Returns what was unloaded."""
    wanted_tags = {_tagged(m) for m in wanted}
    big_tags = {_tagged(m) for m in big}
    client = client or httpx2.Client(base_url=base_url, timeout=60.0)
    try:
        response = client.get("/api/ps")
        response.raise_for_status()
        loaded = [str(m["name"]) for m in response.json().get("models", [])]
        wanted_is_big = bool(wanted_tags & big_tags)
        unload = [m for m in loaded if m not in wanted_tags and (wanted_is_big or m in big_tags)]
        for m in unload:
            client.post("/api/generate", json={"model": m, "keep_alive": 0}).raise_for_status()
    except httpx2.HTTPError as exc:
        raise ModelError(f"Ollama could not unload models: {exc}") from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise ModelError(f"Ollama returned an unexpected response: {exc}") from exc
    return unload
