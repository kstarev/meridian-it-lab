"""held_out_month2's and held_out_month2b's pass rules are committed before each seal (the
owner's rulings of 2026-11-17 and 2026-11-18). This test fails if the commit that first added a
set's rules file is not a strict ancestor of the commit that first added the sealed set, or if
the rules section or the brief has changed since. A rule added later (AMENDMENTS) must be
unchanged since its first commit, and that commit must be an ancestor of every later commit that
changes the sealed set. It reads git history and file names only: never a sealed set's bytes.
"""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SETS = {
    "held_out_month2": (
        "docs/plans/milestone-3e-month2-rules.md",
        "docs/plans/briefs/held_out_month2-brief.md",
    ),
    "held_out_month2b": (
        "docs/plans/milestone-3e-month2b-rules.md",
        "docs/plans/briefs/held_out_month2b-brief.md",
    ),
}
POOLS = "dataset/src/meridian_dataset/month/pools"
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


@pytest.mark.parametrize("name", sorted(SETS))
def test_the_rules_and_the_brief_are_committed(history: None, name: str) -> None:
    rules, brief = SETS[name]
    assert _first_commit(rules), f"{rules} is not committed"
    assert _first_commit(brief), f"{brief} is not committed"


@pytest.mark.parametrize("name", sorted(SETS))
def test_the_rules_section_is_unchanged_since_its_first_commit(history: None, name: str) -> None:
    rules = SETS[name][0]
    then = _git("show", f"{_first_commit(rules)}:{rules}")
    now = (ROOT / rules).read_text(encoding="utf-8")
    assert _rules(now) == _rules(then)


@pytest.mark.parametrize("name", sorted(SETS))
def test_the_brief_is_unchanged_since_its_first_commit(history: None, name: str) -> None:
    brief = SETS[name][1]
    then = _git("show", f"{_first_commit(brief)}:{brief}")
    assert (ROOT / brief).read_text(encoding="utf-8") == then + "\n"


@pytest.mark.parametrize("name", sorted(SETS))
def test_the_rules_commit_comes_before_the_seal(history: None, name: str) -> None:
    rules, brief = SETS[name]
    sealed = f"{POOLS}/{name}.json"
    seal = _first_commit(sealed)
    if not seal:
        assert not (ROOT / sealed).exists(), "the sealed set exists but is not committed"
        pytest.skip(f"{name} is not sealed yet")
    first = _first_commit(rules)
    assert first != seal, "the rules and the sealed set were added in the same commit"
    done = subprocess.run(  # noqa: S603
        ["git", "-C", str(ROOT), "merge-base", "--is-ancestor", first, seal],  # noqa: S607
        check=False,
    )
    assert done.returncode == 0, "the rules commit is not an ancestor of the seal's commit"
    for path in (rules, brief):
        assert _git("cat-file", "-e", f"{seal}:{path}") == ""  # both are in the sealed commit


# Rules added to a set's file after its first seal, each before the set was sealed again and
# before any sealed run: held_out_month2b's slowness-only rule (the owner's ruling, 2026-11-18).
AMENDMENTS = {
    "held_out_month2b": ("<!-- slowness-rule:start -->", "<!-- slowness-rule:end -->"),
}


def _amendment(text: str, start: str, end: str) -> str:
    assert start in text and end in text, "the amendment's markers are missing"
    return text.split(start, 1)[1].split(end, 1)[0]


def _first_commit_with(path: str, marker: str) -> str:
    """The commit that first put `marker` into `path`."""
    found = _git("log", "--no-merges", "-S", marker, "--format=%H", "--", path).splitlines()
    return found[-1] if found else ""


@pytest.mark.parametrize("name", sorted(AMENDMENTS))
def test_the_amendment_is_unchanged_since_its_first_commit(history: None, name: str) -> None:
    rules = SETS[name][0]
    start, end = AMENDMENTS[name]
    first = _first_commit_with(rules, start)
    assert first, f"{rules}: the amendment is not committed"
    then = _git("show", f"{first}:{rules}")
    now = (ROOT / rules).read_text(encoding="utf-8")
    assert _amendment(now, start, end) == _amendment(then, start, end)


@pytest.mark.parametrize("name", sorted(AMENDMENTS))
def test_every_later_seal_comes_after_the_amendment(history: None, name: str) -> None:
    rules = SETS[name][0]
    sealed = f"{POOLS}/{name}.json"
    seals = _git("log", "--no-merges", "--format=%H", "--", sealed).splitlines()
    later = seals[:-1]  # every commit that changed the set after it was first added
    if not later:
        pytest.skip(f"{name} has not been sealed again yet")
    first = _first_commit_with(rules, AMENDMENTS[name][0])
    for seal in later:
        assert first != seal, "the amendment and the new seal were made in the same commit"
        done = subprocess.run(  # noqa: S603
            ["git", "-C", str(ROOT), "merge-base", "--is-ancestor", first, seal],  # noqa: S607
            check=False,
        )
        assert done.returncode == 0, "the amendment's commit is not an ancestor of a later seal"
