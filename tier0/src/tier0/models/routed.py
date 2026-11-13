"""One adapter that sends each task to its own model (milestone 3's mixed setup).

The agent still talks to a single ModelAdapter; triage goes to one model and the
"own account only?" check to another, e.g. llama3.2 for triage and llama3.1:8b for
the check. Seeds and provenance cover both models.
"""

from tier0.models.base import ModelAdapter, ModelRequest, ModelResponse


class RoutedModel:
    def __init__(self, *, triage: ModelAdapter, check: ModelAdapter) -> None:
        if triage.provider != check.provider:
            raise ValueError("both tasks must use the same provider")
        self._triage = triage
        self._check = check
        self.provider = triage.provider
        self.model = f"{triage.model}+{check.model}"

    @property
    def temperature(self) -> float | None:
        return self._triage.temperature

    @property
    def supports_seed(self) -> bool:
        return bool(getattr(self._triage, "supports_seed", False)) and bool(
            getattr(self._check, "supports_seed", False)
        )

    @property
    def seed(self) -> int | None:
        return self._triage.seed

    @seed.setter
    def seed(self, value: int | None) -> None:
        self._triage.seed = value
        self._check.seed = value

    def complete(self, request: ModelRequest) -> ModelResponse:
        target = self._check if request.task == "own_account_check" else self._triage
        return target.complete(request)

    def describe(self) -> dict[str, str]:
        """Both models' provenance in one record; an error in either is kept."""
        triage, check = self._triage.describe(), self._check.describe()
        if "error" in triage or "error" in check:
            return {"error": triage.get("error") or check.get("error") or "unavailable"}
        out = {
            key: f"{triage[key]} (triage), {check[key]} (check)"
            for key in ("model_id", "model_digest")
            if key in triage and key in check
        }
        if "ollama_version" in triage:
            out["ollama_version"] = triage["ollama_version"]
        return out
