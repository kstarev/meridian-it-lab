"""The paired run's driver (ADR-038). The harness is faked: no test runs anything on a
sealed set."""

import json
from pathlib import Path

from tier0 import roles
from tier0.evals import paired_run


def _fake(calls: list[list[str]], fail_on: int | None = None):  # type: ignore[no-untyped-def]
    def run(argv: list[str]) -> int:
        calls.append(argv)
        if fail_on is not None and len(calls) == fail_on:
            return 1
        out = Path(argv[argv.index("--out") + 1])
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.json").write_text("{}")
        return 0

    return run


def _arg(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


def test_three_arms_two_models_one_unsealing(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    assert paired_run.main(["held_out_v4b", "--out", str(tmp_path)], run=_fake(calls)) == 0
    assert [(_arg(c, "--setup"), _arg(c, "--tkt004-fix")) for c in calls] == [
        ("haiku", "off"),
        ("sonnet", "off"),
        ("haiku-3b", "off"),
        ("sonnet-3b", "off"),
        ("haiku-3b", "on"),
        ("sonnet-3b", "on"),
    ]
    assert all("--final-comparison=held_out_v4b" in c for c in calls)
    assert {(_arg(c, "--seed"), _arg(c, "--k")) for c in calls} == {("6247", "5")}
    assert roles.TKT004_FIX is True
    # A second start is refused: the set is unsealed once.
    assert paired_run.main(["held_out_v4b", "--out", str(tmp_path)], run=_fake(calls)) == 2
    assert len(calls) == 6
    # The other set has its own marker.
    assert paired_run.main(["held_out_v4", "--out", str(tmp_path)], run=_fake([])) == 0


def test_a_cut_short_run_resumes_without_rescoring(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    args = ["held_out_v4", "--out", str(tmp_path)]
    assert paired_run.main(args, run=_fake(calls, fail_on=3)) == 1
    assert paired_run.main(args, run=_fake(calls)) == 2
    again: list[list[str]] = []
    assert paired_run.main([*args, "--resume"], run=_fake(again)) == 0
    assert [_arg(c, "--setup") for c in again] == ["haiku-3b"]  # only the one that failed
    marker = json.loads((tmp_path / "held_out_v4" / "UNSEALED.json").read_text())
    assert len(marker["starts"]) == 2
    assert paired_run.main(["held_out_v4b", "--out", str(tmp_path), "--resume"]) == 2
