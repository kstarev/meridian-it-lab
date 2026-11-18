"""held_out_month2's pass rules and selection rule are committed before its seal (the owner's
ruling, 2026-11-17, item 3). This test fails if the commit that first added the rules file is
not a strict ancestor of the commit that first added the sealed set, or if the rules section has
changed since. It reads git history and file names only: never the sealed set's bytes.
"""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RULES = "docs/plans/milestone-3e-month2-rules.md"
BRIEF = "docs/plans/briefs/held_out_month2-brief.md"
SEALED = "dataset/src/meridian_dataset/month/pools/held_out_month2.json"
START, END = "<!-- pass-rules:start -->", "<!-- pass-rules:end -->"


def _git(*args: str) -> str:
    return subprocess.run(  # noqa: S603
        ["git", "-C", str(ROOT), *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _first_commit(path: str) -> str:
    """The commit that first added `path`, following no renames."""
    added = _git("log", "--diff-filter=A", "--format=%H", "--", path).splitlines()
    return added[-1] if added else ""


def _rules(text: str) -> str:
    assert START in text and END in text, "the pass rules' markers are missing"
    return text.split(START, 1)[1].split(END, 1)[0]


@pytest.fixture(scope="module")
def history() -> None:
    if _git("rev-parse", "--is-shallow-repository") == "true":
        pytest.fail("a shallow clone can't show the commit order: CI checks out the full history")


def test_the_rules_and_the_brief_are_committed(history: None) -> None:
    assert _first_commit(RULES), f"{RULES} is not committed"
    assert _first_commit(BRIEF), f"{BRIEF} is not committed"


def test_the_rules_section_is_unchanged_since_its_first_commit(history: None) -> None:
    first = _first_commit(RULES)
    then = _git("show", f"{first}:{RULES}")
    now = (ROOT / RULES).read_text(encoding="utf-8")
    assert _rules(now) == _rules(then)


def test_the_brief_is_unchanged_since_its_first_commit(history: None) -> None:
    first = _first_commit(BRIEF)
    assert (ROOT / BRIEF).read_text(encoding="utf-8") == _git("show", f"{first}:{BRIEF}") + "\n"


def test_the_rules_commit_comes_before_the_seal(history: None) -> None:
    seal = _first_commit(SEALED)
    if not seal:
        assert not (ROOT / SEALED).exists(), "the sealed set exists but is not committed"
        pytest.skip("held_out_month2 is not sealed yet")
    rules = _first_commit(RULES)
    assert rules != seal, "the rules and the sealed set were added in the same commit"
    done = subprocess.run(  # noqa: S603
        ["git", "-C", str(ROOT), "merge-base", "--is-ancestor", rules, seal],  # noqa: S607
        check=False,
    )
    assert done.returncode == 0, "the rules commit is not an ancestor of the seal's commit"
    for path in (RULES, BRIEF):
        assert _git("cat-file", "-e", f"{seal}:{path}") == ""  # both are in the sealed commit
