"""Fault injection (ADR-022): tuning worlds only, only triage's answer flips, checks unchanged."""

import json
from pathlib import Path

import anyio
import pytest

from meridian_dataset import HOLDOUT_SEED, generate
from meridian_dataset.generator import HOLDOUT_V2_SEED
from tier0 import messages
from tier0.evals.faults import (
    COLLEAGUE,
    FLIP_SCENARIOS,
    OUTSIDER,
    FlipTriage,
    NotTuningError,
    build_fault_report,
    main,
    render_markdown,
    require_tuning,
    run_faults,
    select_flips,
    tuning_worlds,
)
from tier0.models import ABOUT_SOMEONE_ELSE, OWN_USERNAME, ModelRequest, ModelResponse, StubModel

SEEDS = range(1108, 1118)


class Recording:
    """A fake model: triage is right (about_someone_else), the check always says yes."""

    provider = "fake"
    model = "fake-recording"
    temperature = 0.5
    supports_seed = True

    def __init__(self) -> None:
        self.seed: int | None = None
        self.requests: list[ModelRequest] = []

    def describe(self) -> dict[str, str]:
        return {"model_id": "fake", "model_digest": "0" * 12, "ollama_version": "0"}

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if request.task == "triage":
            text = json.dumps({"category": ABOUT_SOMEONE_ELSE})
        elif request.task == "own_account_check":
            text = json.dumps({"own_account_only": True})
        else:
            text = "Hi, I'm the IT AI assistant."
        return ModelResponse(text, self.model, 1, 1, "stop", None)


@pytest.mark.parametrize("seed", [HOLDOUT_SEED, HOLDOUT_V2_SEED])
def test_held_out_seeds_are_refused(seed: int, tmp_path: Path) -> None:
    with pytest.raises(NotTuningError, match="tuning data only"):
        require_tuning(seed)
    with pytest.raises(NotTuningError):
        tuning_worlds([1108, seed])
    out = tmp_path / "r.md"
    assert (
        main(["--models", "stub", "--first-seed", str(seed), "--worlds", "1", "--out", str(out)])
        == 2
    )
    assert not out.exists()


def test_run_faults_refuses_a_held_out_world() -> None:
    worlds = {HOLDOUT_SEED: generate(HOLDOUT_SEED)}
    with pytest.raises(NotTuningError):
        anyio.run(lambda: run_faults(StubModel, worlds, [], k=1))


def test_flips_are_fixed_and_stratified() -> None:
    worlds = tuning_worlds(SEEDS)
    picked = select_flips(worlds, rate=0.3, fault_seed=22)
    assert picked == select_flips(worlds, rate=0.3, fault_seed=22)
    for scenario in FLIP_SCENARIOS:
        assert sum(1 for t in picked if t.scenario == scenario) == round(0.3 * len(SEEDS))
    assert {t.scenario for t in picked} == {COLLEAGUE, OUTSIDER}
    for t in picked:
        assert worlds[t.world_seed].answer_key.entry(t.ticket_id).scenario == t.scenario


def test_only_the_triage_answer_flips() -> None:
    inner = Recording()
    model = FlipTriage(inner)
    model.seed = 1001
    assert inner.seed == 1001 and model.supports_seed

    triage = ModelRequest(task="triage", system=messages.TRIAGE_SYSTEM, user="who is bob")
    assert json.loads(model.complete(triage).text) == {"category": OWN_USERNAME}
    assert model.real_triage == [ABOUT_SOMEONE_ELSE]

    check = ModelRequest(task="own_account_check", system="s", user="who is bob")
    assert model.complete(check) == inner.complete(check)
    assert inner.requests[1] is check  # forwarded untouched


def test_the_check_never_sees_the_flip() -> None:
    """The check request carries only the raw message and the sender, as in production."""
    worlds = tuning_worlds(SEEDS)
    target = next(t for t in select_flips(worlds) if t.scenario == COLLEAGUE)
    made: list[Recording] = []

    def factory() -> Recording:
        made.append(Recording())
        return made[-1]

    runs = anyio.run(lambda: run_faults(factory, worlds, [target], k=1))
    ticket = next(t for t in worlds[target.world_seed].tickets if t.id == target.ticket_id)
    checks = [r for m in made for r in m.requests if r.task == "own_account_check"]
    assert len(checks) == 1
    assert checks[0].system == messages.OWN_ACCOUNT_CHECK_SYSTEM
    assert ticket.text in checks[0].user
    for label in (OWN_USERNAME, ABOUT_SOMEONE_ELSE):
        assert label not in checks[0].user
    # The fake check said yes, so only the code-only mention check could stop it.
    (run,) = runs
    assert run.flipped and run.real_triage == ABOUT_SOMEONE_ELSE
    assert run.result.own_account_check == "yes"
    assert run.mention_caught and not run.answered


def test_stub_fault_report_counts_add_up() -> None:
    data = build_fault_report({"stub": StubModel}, world_seeds=SEEDS, k=2)
    m = data["models"]["stub"]
    assert m["pipeline_check"]
    c, o = m["summary"]["colleague"], m["summary"]["outsider"]
    assert c["flipped_runs"] == c["runs"] == 2 * data["flipped_tickets"][COLLEAGUE]
    assert (
        c["both"] + c["model_check_only"] + c["mention_check_only"] + c["neither"]
        == (c["flipped_runs"])
    )
    assert c["model_check_caught"] == c["both"] + c["model_check_only"]
    assert c["mention_check_caught"] == c["both"] + c["mention_check_only"]
    assert c["data_exposed_runs"] == 0
    # Outsiders stop at identity binding: triage, and so the flip, never happens.
    assert o["runs"] == 2 * data["flipped_tickets"][OUTSIDER]
    assert o["reached_triage"] == 0
    assert o["handed_to_identity_verification"] == o["runs"]
    assert data["message_pool"] == "tuning"
    # Every tuning colleague template names the colleague, so the mention check can see it.
    assert data["colleague_named_in_full"] == data["flipped_tickets"][COLLEAGUE]


def test_markdown_and_cli(tmp_path: Path) -> None:
    out = tmp_path / "faults.md"
    args = ["--models", "stub", "--k", "1", "--worlds", "10", "--out", str(out)]
    assert main(args) == 0
    text = out.read_text()
    data = json.loads(out.with_suffix(".json").read_text())
    assert text == render_markdown(data, text.split("`")[1])
    assert "do not edit by hand" in text
    assert "stub (pipeline check, not AI accuracy)" in text
    assert "not a performance number" in text
    assert "Outsiders are stopped by identity binding" in text
