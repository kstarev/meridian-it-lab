"""Searches leave sealed paths out by default (docs/sealed-search-guard.md): the list of
sealed paths, the search command, ripgrep's ignore file and git's own attribute.

Never opens a real sealed file: only names from `git ls-files` are used.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import sealed_paths  # noqa: E402

CANARY = "CANARY_7f3a91"
NAMES_PY = "dataset/src/meridian_dataset/names.py"
MONTH1 = "dataset/src/meridian_dataset/month/pools/held_out_month1.json"


def _git(*args: str, cwd: Path = ROOT) -> str:
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _tracked_sealed() -> list[str]:
    files = _git("ls-files").splitlines()
    return [f for f in files if f.rsplit("/", 1)[-1].startswith("held_out_")]


def test_every_tracked_sealed_file_is_sealed_and_listing_leaves_them_out():
    sealed = _tracked_sealed()
    assert len(sealed) >= 10
    assert all(sealed_paths.is_sealed(p) for p in sealed)
    assert sealed_paths.is_sealed(NAMES_PY)
    assert sealed_paths.is_sealed("/abs/checkout/" + NAMES_PY)
    listed = set(_git("ls-files", "--", ".", *sealed_paths.exclude_pathspecs()).splitlines())
    assert not listed & set(sealed)
    assert NAMES_PY not in listed
    assert len(listed) > 50


def test_opened_dev_set_comes_back_and_unknown_name_is_refused():
    opened = {"held_out_month1"}
    sealed = _tracked_sealed()
    specs = sealed_paths.exclude_pathspecs(opened)
    listed = set(_git("ls-files", "--", ".", *specs).splitlines())
    assert listed & set(sealed) == {p for p in sealed if not sealed_paths.is_sealed(p, opened)}
    month1 = [p for p in sealed if p.endswith("held_out_month1.json")]
    assert len(month1) == 1
    assert set(month1) <= listed
    assert len(listed & set(sealed)) == 1
    assert NAMES_PY not in listed
    sealed_paths.check_opened(["held_out_month1"])
    with pytest.raises(ValueError, match="held_out_k1"):
        sealed_paths.check_opened(["held_out_k1"])


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    shutil.copytree(
        ROOT / "tools", tmp_path / "tools", ignore=shutil.ignore_patterns("__pycache__")
    )
    shutil.copy(ROOT / ".rgignore", tmp_path / ".rgignore")
    shutil.copy(ROOT / ".gitattributes", tmp_path / ".gitattributes")
    (tmp_path / "pools").mkdir()
    (tmp_path / "notes.txt").write_text(f"{CANARY}\n")
    (tmp_path / "pools" / "held_out_fake.json").write_text(f'["{CANARY}"]\n')
    for cmd in (
        ["init", "-q"],
        ["add", "-A"],
        ["-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "x"],
    ):
        _git(*cmd, cwd=tmp_path)
    return tmp_path


def _search(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, str(repo / "tools" / "search.py"), *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


def test_search_prints_normal_file_only(fake_repo: Path):
    done = _search(fake_repo, CANARY)
    assert done.returncode == 0
    assert "notes.txt" in done.stdout
    assert "held_out_fake" not in done.stdout
    assert "left out: sealed paths" in done.stderr


def test_search_refuses_sealed_path_and_unknown_open(fake_repo: Path):
    done = _search(fake_repo, CANARY, "pools/held_out_fake.json")
    assert done.returncode == 2
    assert CANARY not in done.stdout
    assert _search(fake_repo, "--open", "nope", CANARY).returncode == 2


def test_rgignore_matches_the_sealed_paths():
    lines = [
        ln
        for ln in (ROOT / ".rgignore").read_text().splitlines()
        if ln.strip() and not ln.startswith("#")
    ]
    assert lines == [sealed_paths.SEALED_GLOB, *("/" + p for p in sealed_paths.EXTRA_PATHS)]


@pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not installed")
def test_rg_skips_sealed_by_default(fake_repo: Path):
    done = subprocess.run(  # noqa: S603
        [shutil.which("rg") or "rg", "-l", CANARY],
        cwd=fake_repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.stdout.split() == ["notes.txt"]


def test_git_treats_every_sealed_file_as_binary():
    """`held_out_* -diff` in .gitattributes: git grep, diff and show print no line of them."""
    sealed = _tracked_sealed()
    assert len(sealed) >= 10
    attrs = _git("check-attr", "diff", "--", *sealed).splitlines()
    assert len(attrs) == len(sealed)
    assert all(line.endswith(": diff: unset") for line in attrs)
    other = _git("check-attr", "diff", "--", "README.md", NAMES_PY).splitlines()
    assert all(line.endswith(": diff: unspecified") for line in other)


def test_plain_git_commands_print_no_sealed_line(fake_repo: Path):
    grep = _git("grep", "-n", CANARY, cwd=fake_repo)
    assert f"notes.txt:1:{CANARY}" in grep
    assert "Binary file pools/held_out_fake.json matches" in grep
    assert f'["{CANARY}"]' not in grep
    (fake_repo / "pools" / "held_out_fake.json").write_text(f'["{CANARY}", "changed"]\n')
    diff = _git("diff", cwd=fake_repo)
    assert "Binary files" in diff
    assert CANARY not in diff
    _git(
        "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qam", "y", cwd=fake_repo
    )
    shown = _git("show", "HEAD", cwd=fake_repo)
    assert "Binary files" in shown
    assert CANARY not in shown
