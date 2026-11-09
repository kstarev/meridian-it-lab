"""Held-out v2 is sealed: nothing runs the agent on it before the final comparison."""

from pathlib import Path

import anyio
import pytest

from meridian_dataset import DEFAULT_SEED, HOLDOUT_SEED, HOLDOUT_V2_SEED, generate
from tier0 import demo
from tier0.evals import __main__ as cli
from tier0.evals.report import render_markdown
from tier0.evals.runner import build_report, run_eval
from tier0.runtime import offline_runtime
from tier0.sealed import SealedSetError, require_unsealed


def test_only_held_out_v2_is_sealed() -> None:
    require_unsealed(generate(DEFAULT_SEED))
    require_unsealed(generate(HOLDOUT_SEED))
    with pytest.raises(SealedSetError, match="sealed until the final comparison"):
        require_unsealed(generate(HOLDOUT_V2_SEED))
    with pytest.raises(SealedSetError):
        require_unsealed(generate(DEFAULT_SEED, split="held_out_v2"))
    require_unsealed(generate(HOLDOUT_V2_SEED), final_comparison=True)


def test_the_runtime_refuses_a_sealed_world() -> None:
    async def open_it() -> None:
        async with offline_runtime(generate(HOLDOUT_V2_SEED)):
            pass  # pragma: no cover - never reached

    with pytest.raises(SealedSetError):
        anyio.run(open_it)


def test_run_eval_refuses_held_out_v2() -> None:
    with pytest.raises(SealedSetError):
        anyio.run(lambda: run_eval(HOLDOUT_V2_SEED, k=1))


def test_cli_refuses_held_out_v2_without_the_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["--seed", str(HOLDOUT_V2_SEED), "--k", "1", "--out", str(tmp_path)]
    assert cli.main(args) == 2
    assert "sealed until the final comparison" in capsys.readouterr().err
    assert not (tmp_path / "report.md").exists()


def test_demo_refuses_held_out_v2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert demo.main(["--seed", str(HOLDOUT_V2_SEED), "--out", str(tmp_path)]) == 2
    assert "sealed" in capsys.readouterr().err


def test_report_labels_held_out_v2() -> None:
    world = generate(DEFAULT_SEED)
    rep = build_report(world, (), seed=HOLDOUT_V2_SEED, k=3, provider="ollama", model="m")
    md = render_markdown(rep)
    assert "held-out v2 (seed 3107)" in md
    assert "--final-comparison" in md
