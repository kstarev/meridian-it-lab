"""The seal: nothing runs the agent on a sealed set before its final comparison, and only
its own name unseals it (ADR-015, ADR-029). Held-out v2 and v3 are spent and run like any
dev set (ADR-029, ADR-030). Held-out v4 (ADR-033) is sealed. The mechanism's details are
tested on v3 as a stand-in, so no test needs to run anything on v4."""

from pathlib import Path

import anyio
import pytest

from meridian_dataset import (
    DEFAULT_SEED,
    HOLDOUT_SEED,
    HOLDOUT_V2_SEED,
    HOLDOUT_V3_SEED,
    HOLDOUT_V4_SEED,
    HOLDOUT_V4B_SEED,
    generate,
)
from tier0 import demo, sealed
from tier0.evals import __main__ as cli
from tier0.evals import report
from tier0.evals.report import render_markdown
from tier0.evals.runner import build_report, run_eval
from tier0.runtime import offline_runtime
from tier0.sealed import SealedSetError, require_unsealed

STAND_IN = frozenset({"held_out_v3"})


@pytest.fixture
def seal_v3(monkeypatch: pytest.MonkeyPatch) -> None:
    """Seal v3 again for one test, as a stand-in for the next sealed set."""
    for module in (sealed, cli, report):
        monkeypatch.setattr(module, "SEALED_POOLS", STAND_IN)


def test_only_v4_is_sealed_and_v3_runs_as_a_dev_set(tmp_path: Path) -> None:
    assert frozenset({"held_out_v4", "held_out_v4b"}) == sealed.SEALED_POOLS
    for seed in (DEFAULT_SEED, HOLDOUT_SEED, HOLDOUT_V2_SEED, HOLDOUT_V3_SEED):
        require_unsealed(generate(seed))
    result = anyio.run(lambda: run_eval(HOLDOUT_V3_SEED, k=1))
    assert result.seed == HOLDOUT_V3_SEED
    # Held-out v4 (ADR-033): nothing runs on it before milestone 3b's final comparison.
    v4 = generate(HOLDOUT_V4_SEED)
    assert v4.message_pool == "held_out_v4"
    with pytest.raises(SealedSetError, match="--final-comparison=held_out_v4"):
        require_unsealed(v4)
    with pytest.raises(SealedSetError):
        require_unsealed(v4, final_comparison="held_out_v3")
    with pytest.raises(SealedSetError):
        anyio.run(lambda: run_eval(HOLDOUT_V4_SEED, k=1))
    assert demo.main(["--seed", str(HOLDOUT_V4_SEED), "--out", str(tmp_path)]) == 2
    # v4b (ADR-038) is sealed too, and neither set's name unseals the other.
    v4b = generate(HOLDOUT_V4B_SEED)
    assert v4b.message_pool == "held_out_v4b"
    with pytest.raises(SealedSetError, match="--final-comparison=held_out_v4b"):
        require_unsealed(v4b)
    with pytest.raises(SealedSetError):
        require_unsealed(v4b, final_comparison="held_out_v4")
    with pytest.raises(SealedSetError):
        require_unsealed(v4, final_comparison="held_out_v4b")


def test_v3_reports_say_dev_set() -> None:
    world = generate(DEFAULT_SEED)
    md = render_markdown(
        build_report(world, (), seed=HOLDOUT_V3_SEED, k=3, provider="ollama", model="m")
    )
    assert "held-out v3, spent in milestone 3, now a dev set" in md
    assert "--final-comparison" not in md


@pytest.mark.usefixtures("seal_v3")
def test_a_sealed_set_is_unsealed_only_by_its_own_name() -> None:
    v3 = generate(HOLDOUT_V3_SEED)
    assert v3.message_pool == "held_out_v3"
    with pytest.raises(SealedSetError, match="--final-comparison=held_out_v3"):
        require_unsealed(v3)
    with pytest.raises(SealedSetError):
        require_unsealed(v3, final_comparison="held_out_v2")
    with pytest.raises(SealedSetError):
        require_unsealed(generate(DEFAULT_SEED, split="held_out_v3"))
    require_unsealed(v3, final_comparison="held_out_v3")
    require_unsealed(generate(DEFAULT_SEED), final_comparison="held_out_v3")


@pytest.mark.usefixtures("seal_v3")
def test_cli_needs_the_sets_own_name(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = ["--seed", str(HOLDOUT_V3_SEED), "--k", "1", "--out", str(tmp_path)]
    assert cli.main(args) == 2
    assert "sealed until its final comparison" in capsys.readouterr().err
    with pytest.raises(SystemExit):  # held_out_v2 isn't a sealed set
        cli.main([*args, "--final-comparison=held_out_v2"])
    assert not (tmp_path / "report.md").exists()
    with pytest.raises(SystemExit):  # a bare flag is a usage error
        cli.main([*args, "--final-comparison"])


@pytest.mark.usefixtures("seal_v3")
def test_the_runtime_run_eval_and_demo_refuse_a_sealed_world(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    async def open_it() -> None:
        async with offline_runtime(generate(HOLDOUT_V3_SEED)):
            pass  # pragma: no cover - never reached

    with pytest.raises(SealedSetError):
        anyio.run(open_it)
    with pytest.raises(SealedSetError):
        anyio.run(lambda: run_eval(HOLDOUT_V3_SEED, k=1))
    assert demo.main(["--seed", str(HOLDOUT_V3_SEED), "--out", str(tmp_path)]) == 2
    assert "sealed" in capsys.readouterr().err


@pytest.mark.usefixtures("seal_v3")
def test_reports_name_the_flag_that_unsealed_a_set() -> None:
    world = generate(DEFAULT_SEED)
    md = render_markdown(
        build_report(world, (), seed=HOLDOUT_V3_SEED, k=3, provider="ollama", model="m")
    )
    assert "--final-comparison=held_out_v3" in md
    v2 = render_markdown(
        build_report(world, (), seed=HOLDOUT_V2_SEED, k=3, provider="ollama", model="m")
    )
    assert "held-out v2 (seed 3107)" in v2 and "--final-comparison" not in v2
