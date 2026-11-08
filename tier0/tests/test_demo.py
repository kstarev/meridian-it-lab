from pathlib import Path

import pytest

from meridian_dataset import DEFAULT_SEED
from tier0.demo import main


def _lines(path: Path) -> int:
    return len(path.read_text().splitlines())


def test_demo_transcript(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--seed", str(DEFAULT_SEED), "--out", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    for n in range(1, 7):
        assert f"TKT-00{n}" in out
    for needle in (
        "AI assistant",
        "Done",
        "Canceled",
        "identity-verification",
        "NOT verified (external_user)",
        "3 days later",
        "Final state | Queue",
    ):
        assert needle in out
    assert "\x1b" not in out
    assert (tmp_path / "traces.jsonl").exists()
    assert _lines(tmp_path / "traces.jsonl") >= 1


def test_demo_twice_keeps_only_second_run(tmp_path: Path) -> None:
    assert main(["--out", str(tmp_path)]) == 0
    first = _lines(tmp_path / "traces.jsonl")
    assert main(["--out", str(tmp_path)]) == 0
    assert _lines(tmp_path / "traces.jsonl") == first
