"""Held-out v3 is sealed: nothing runs the agent on it before its final comparison, and
only its own name unseals it. Held-out v2 is spent and runs like any dev set (ADR-029)."""

from pathlib import Path

import anyio
import pytest

from meridian_dataset import DEFAULT_SEED, HOLDOUT_SEED, HOLDOUT_V2_SEED, HOLDOUT_V3_SEED, generate
from tier0 import demo
from tier0.evals import __main__ as cli
from tier0.evals.report import render_markdown
from tier0.evals.runner import build_report, run_eval
from tier0.runtime import offline_runtime
from tier0.sealed import SealedSetError, require_unsealed


def test_only_held_out_v3_is_sealed() -> None:
    for seed in (DEFAULT_SEED, HOLDOUT_SEED, HOLDOUT_V2_SEED):
        require_unsealed(generate(seed))  # v2 is a plain dev set now
    with pytest.raises(SealedSetError, match="sealed until its final comparison"):
        require_unsealed(generate(HOLDOUT_V3_SEED))


def test_each_set_is_unsealed_only_by_its_own_name() -> None:
    v3 = generate(HOLDOUT_V3_SEED)
    assert v3.message_pool == "held_out_v3"
    with pytest.raises(SealedSetError, match="--final-comparison=held_out_v3"):
        require_unsealed(v3)
    with pytest.raises(SealedSetError):
        require_unsealed(v3, final_comparison="held_out_v2")
    with pytest.raises(SealedSetError):
        require_unsealed(generate(DEFAULT_SEED, split="held_out_v3"))
    require_unsealed(v3, final_comparison="held_out_v3")
    # Unsealing a set never matters for an unsealed one.
    require_unsealed(generate(DEFAULT_SEED), final_comparison="held_out_v3")


def test_cli_needs_the_sets_own_name(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = ["--seed", str(HOLDOUT_V3_SEED), "--k", "1", "--out", str(tmp_path)]
    assert cli.main(args) == 2
    with pytest.raises(SystemExit):  # held_out_v2 isn't a sealed set any more
        cli.main([*args, "--final-comparison=held_out_v2"])
    assert "--final-comparison=held_out_v3" in capsys.readouterr().err
    assert not (tmp_path / "report.md").exists()
    with pytest.raises(SystemExit):  # a bare flag, or an unknown set, is a usage error
        cli.main([*args, "--final-comparison"])


def test_the_runtime_refuses_a_sealed_world() -> None:
    async def open_it() -> None:
        async with offline_runtime(generate(HOLDOUT_V3_SEED)):
            pass  # pragma: no cover - never reached

    with pytest.raises(SealedSetError):
        anyio.run(open_it)


def test_run_eval_refuses_held_out_v3() -> None:
    with pytest.raises(SealedSetError):
        anyio.run(lambda: run_eval(HOLDOUT_V3_SEED, k=1))


def test_cli_refuses_held_out_v3_without_the_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["--seed", str(HOLDOUT_V3_SEED), "--k", "1", "--out", str(tmp_path)]
    assert cli.main(args) == 2
    assert "sealed until its final comparison" in capsys.readouterr().err
    assert not (tmp_path / "report.md").exists()


def test_demo_refuses_held_out_v3(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert demo.main(["--seed", str(HOLDOUT_V3_SEED), "--out", str(tmp_path)]) == 2
    assert "sealed" in capsys.readouterr().err


def test_reports_label_the_held_out_sets() -> None:
    world = generate(DEFAULT_SEED)
    v3 = render_markdown(
        build_report(world, (), seed=HOLDOUT_V3_SEED, k=3, provider="ollama", model="m")
    )
    assert "--final-comparison=held_out_v3" in v3
    v2 = render_markdown(
        build_report(world, (), seed=HOLDOUT_V2_SEED, k=3, provider="ollama", model="m")
    )
    assert "held-out v2 (seed 3107)" in v2 and "--final-comparison" not in v2
