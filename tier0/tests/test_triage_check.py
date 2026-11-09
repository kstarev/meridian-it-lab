"""Triage check tool: stub runs, labels, sealed refusal, fake wrong answers."""

import hashlib
import json
from pathlib import Path

import pytest

from tier0 import messages
from tier0.agent import parse_triage, triage_request
from tier0.evals.prompt_history import VERSIONS
from tier0.evals.triage_check import main, run_check
from tier0.models import (
    ABOUT_SOMEONE_ELSE,
    OTHER,
    OWN_USERNAME,
    ClaudeModel,
    ModelResponse,
    OllamaModel,
    StubModel,
)


def response(text: str, finish: str = "stop") -> ModelResponse:
    return ModelResponse(text, "m", 1, 1, finish, None)


class AlwaysOwn:
    provider = "fake"
    model = "always-own"
    temperature = None

    def complete(self, request):  # type: ignore[no-untyped-def]
        return response(json.dumps({"category": OWN_USERNAME}))


def test_stub_on_tuning_is_perfect(tmp_path: Path) -> None:
    assert main(["--model", "stub", "--pool", "tuning", "--k", "2", "--out", str(tmp_path)]) == 0
    files = sorted(p.name for p in tmp_path.iterdir())
    assert len(files) == 2
    data = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert data["score"] == 1.0 and data["wrong"] == []
    md = next(tmp_path.glob("*.md")).read_text()
    assert "tuning score" in md and "accuracy" not in md.lower()
    assert data["prompt"] == "live" and data["prompt_sha256"] == messages.TRIAGE_SYSTEM_SHA256


def test_held_out_v1_is_labelled_dev_set(tmp_path: Path) -> None:
    assert (
        main(["--model", "stub", "--pool", "held_out_v1", "--k", "1", "--out", str(tmp_path)]) == 0
    )
    assert (
        "dev-set check (held-out v1, diagnosis only, ADR-014)"
        in next(tmp_path.glob("*.md")).read_text()
    )


def test_held_out_v2_refused(tmp_path: Path) -> None:
    out = tmp_path / "o"
    assert main(["--model", "stub", "--pool", "held_out_v2", "--out", str(out)]) == 2
    assert not out.exists()


def test_always_own_fails_colleagues() -> None:
    result = run_check(AlwaysOwn(), "tuning", 2)
    assert result["scenarios"]["colleague"]["score"] == 0
    assert result["scenarios"]["normal"]["score"] == 1
    wrong = result["wrong"]
    assert wrong and all(w["scenario"] == "colleague" and w["got"] == OWN_USERNAME for w in wrong)
    assert all(
        w["expected"] == ABOUT_SOMEONE_ELSE and w["count"] == 2 and "text" in w for w in wrong
    )


def test_temperature_defaults() -> None:
    assert StubModel().temperature is None
    assert ClaudeModel().temperature is None
    assert OllamaModel().temperature == 0.5


@pytest.mark.parametrize(
    "res",
    [
        None,
        response('{"category": "own_username"}', "refusal"),
        response("not json"),
        response('{"category": "own_username", "tier": 1}'),
        response('{"category": "bogus"}'),
        response('{"category": 3}'),
        response("[1]"),
    ],
)
def test_parse_triage_fails_closed(res: ModelResponse | None) -> None:
    assert parse_triage(res) == OTHER


def test_parse_triage_ok_and_request() -> None:
    assert parse_triage(response('{"category": "about_someone_else"}')) == ABOUT_SOMEONE_ELSE
    req = triage_request("hi")
    assert req.task == "triage" and req.user == "hi" and req.json_schema is not None


class SeededOwn(AlwaysOwn):
    supports_seed = True

    def __init__(self) -> None:
        self.seed: int | None = None
        self.seen: list[int | None] = []

    def complete(self, request):  # type: ignore[no-untyped-def]
        self.seen.append(self.seed)
        return super().complete(request)

    def describe(self) -> dict[str, str]:
        return {"ollama_version": "0.6.2", "model_digest": "cd" * 32, "model_id": "cd" * 6}


def test_seeds_per_run_and_provenance() -> None:
    model = SeededOwn()
    result = run_check(model, "tuning", 3, model_seed_base=500)
    assert result["model_seeds"] == [501, 502, 503] and result["model_seed_base"] == 500
    assert result["provenance"]["model_id"] == "cd" * 6
    per_run = len(model.seen) // 3
    assert model.seen == [501] * per_run + [502] * per_run + [503] * per_run
    from tier0.evals.triage_check import render_markdown

    md = render_markdown(result)
    assert "cd" * 32 in md and "Seeds: 501, 502, 503" in md


def test_stub_has_no_seeds(tmp_path: Path) -> None:
    assert main(["--pool", "tuning", "--k", "1", "--model-seed", "7", "--out", str(tmp_path)]) == 0
    data = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert data["model_seeds"] == [] and data["provenance"] == {}


class Recorder(AlwaysOwn):
    def __init__(self, fail: bool = False) -> None:
        self.systems: set[str] = set()
        self.fail = fail

    def complete(self, request):  # type: ignore[no-untyped-def]
        self.systems.add(request.system)
        if self.fail:
            raise RuntimeError("boom")
        return super().complete(request)


def test_prompt_option_uses_that_version_and_restores(monkeypatch, tmp_path: Path) -> None:
    live = messages.TRIAGE_SYSTEM
    model = Recorder()
    monkeypatch.setattr("tier0.evals.triage_check._model_factory", lambda *_: lambda: model)
    assert (
        main(
            [
                "--model",
                "stub",
                "--pool",
                "tuning",
                "--k",
                "1",
                "--prompt",
                "C",
                "--out",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert model.systems == {VERSIONS["C"]}
    assert live == messages.TRIAGE_SYSTEM
    data = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert data["prompt"] == "C"
    assert data["prompt_sha256"] == hashlib.sha256(VERSIONS["C"].encode()).hexdigest()
    assert "SHA-256 " + data["prompt_sha256"] in next(tmp_path.glob("*.md")).read_text()


def test_prompt_restored_when_the_run_raises() -> None:
    live = messages.TRIAGE_SYSTEM
    with pytest.raises(RuntimeError):
        run_check(Recorder(fail=True), "tuning", 1, prompt="A")
    assert live == messages.TRIAGE_SYSTEM
