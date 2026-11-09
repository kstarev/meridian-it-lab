"""Triage check tool: stub runs, labels, sealed refusal, fake wrong answers."""

import json
from pathlib import Path

import pytest

from tier0.agent import parse_triage, triage_request
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
    assert data["accuracy"] == 1.0 and data["wrong"] == []
    assert "tuning data" in next(tmp_path.glob("*.md")).read_text()


def test_held_out_v1_is_labelled_dev_set(tmp_path: Path) -> None:
    assert (
        main(["--model", "stub", "--pool", "held_out_v1", "--k", "1", "--out", str(tmp_path)]) == 0
    )
    assert "dev set (held-out v1)" in next(tmp_path.glob("*.md")).read_text()


def test_held_out_v2_refused(tmp_path: Path) -> None:
    out = tmp_path / "o"
    assert main(["--model", "stub", "--pool", "held_out_v2", "--out", str(out)]) == 2
    assert not out.exists()


def test_always_own_fails_colleagues() -> None:
    result = run_check(AlwaysOwn(), "tuning", 2)
    assert result["scenarios"]["colleague"]["accuracy"] == 0
    assert result["scenarios"]["normal"]["accuracy"] == 1
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
