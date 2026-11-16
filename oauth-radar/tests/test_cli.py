"""The command line: nothing to scan yet, or the ranked findings for a fixture. Read-only."""

import hashlib
from pathlib import Path

import pytest

from meridian_oauth_radar.__main__ import FIXTURES, NOTHING_TO_SCAN, main

ROOT = Path(__file__).resolve().parents[2]


def test_no_file_says_nothing_to_scan_yet(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == NOTHING_TO_SCAN == "milestone 0: nothing to scan yet"
    assert "--google-tokens" in out[1]


def test_the_hint_points_at_real_fixtures() -> None:
    assert (ROOT / FIXTURES / "google_tokens.json").is_file()
    assert (ROOT / FIXTURES / "banned_apps.json").is_file()


def test_scanning_the_fixture_prints_the_ranked_findings(
    tokens_path: Path, banned_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--google-tokens", str(tokens_path), "--banned", str(banned_path)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == (
        "Scanned 6 grants from google_tokens.json: 4 high-risk findings for IT to review. "
        "Nothing was revoked."
    )
    ranked = [line for line in out if line[:1].isdigit()]
    assert [line.split(" (google")[0] for line in ranked] == [
        "1. high, score 8: NoteSprout AI for sam.okafor@meridian.example",
        "2. high, score 5: NoteSprout AI for priya.nair@meridian.example",
        "3. high, score 3: Taskmole Agent for jordan.weber@meridian.example",
        "4. high, score 3: Wordloom Assistant for alex.kim@meridian.example",
    ]
    assert "   - Banned app: on IT's banned list (an AI note-taker" in "\n".join(out)
    assert "full Drive access" in out[3]


def test_a_scan_with_no_findings_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "tokens.json"
    path.write_text('{"kind": "admin#directory#tokenList"}', encoding="utf-8")
    assert main(["--google-tokens", str(path)]) == 0
    assert capsys.readouterr().out.strip() == (
        "Scanned 0 grants from tokens.json: no findings. Nothing was revoked."
    )


def test_the_scan_leaves_its_input_files_unchanged(
    tokens_path: Path, banned_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def digests() -> list[str]:
        return [hashlib.sha256(p.read_bytes()).hexdigest() for p in (tokens_path, banned_path)]

    before = digests()
    assert main(["--google-tokens", str(tokens_path), "--banned", str(banned_path)]) == 0
    assert digests() == before


def test_a_bad_file_exits_2_with_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "users.json"
    path.write_text('{"kind": "admin#directory#users"}', encoding="utf-8")
    assert main(["--google-tokens", str(path)]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: ")
    assert "not a Google token export" in err


def test_banned_without_tokens_is_a_usage_error(
    banned_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--banned", str(banned_path)]) == 2
    assert "--banned needs --google-tokens" in capsys.readouterr().err
