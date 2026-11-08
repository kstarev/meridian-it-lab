"""The command line writes the four files and prints a summary."""

import hashlib
import json
from pathlib import Path

import pytest

from meridian_dataset.__main__ import main


def test_cli_writes_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--seed", "7", "--out", str(tmp_path)]) == 0
    out_dir = tmp_path / "seed-7"
    names = {"world.json", "tickets.json", "answer_key.json", "manifest.json"}
    assert {p.name for p in out_dir.iterdir()} == names
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert set(manifest["files"]) == names - {"manifest.json"}
    for name, digest in manifest["files"].items():
        assert hashlib.sha256((out_dir / name).read_bytes()).hexdigest() == digest
    assert "10 people, 6 tickets, 2 planted problems" in capsys.readouterr().out
