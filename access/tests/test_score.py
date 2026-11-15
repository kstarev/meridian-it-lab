"""Scoring: Wilson intervals, matching metrics, the CLI and the generated results table."""

from pathlib import Path

import pytest

from meridian_access import score
from meridian_access.match import Match, MatchResult
from meridian_access.score import Rate, render_matching, score_matching, wilson
from meridian_dataset.access.model import AccountOwner

DOC = (
    Path(__file__).resolve().parents[2] / "docs" / "evals" / "2026-11-15-access-tuning-matching.md"
)


def test_wilson_interval():
    lo, hi = wilson(50, 100)
    assert 0.40 < lo < 0.41 and 0.59 < hi < 0.60
    assert wilson(0, 0) == (0.0, 1.0)
    lo, hi = wilson(100, 100)
    assert 0.96 < lo < 0.97 and hi == pytest.approx(1.0)
    assert Rate(0, 0).value == 0.0


def test_score_matching_counts_each_kind_of_outcome():
    owners = [
        AccountOwner("google", "a@x", "E1"),
        AccountOwner("google", "b@x", "E2"),
        AccountOwner("google", "c@x", "E3"),
        AccountOwner("app-one", "Orphan@x ", ""),
        AccountOwner("app-one", "ghost", ""),
    ]
    result = MatchResult(
        matches={
            ("google", "", "a@x"): Match("E1", "exact_email", 1.0),  # right
            ("google", "", "b@x"): Match("E9", "exact_email", 1.0),  # wrong person
            ("app-one", "app-one", "orphan@x"): Match(
                "E4", "name", 0.7
            ),  # ownerless, given a person
            ("okta", "amber-ledger", "a@x"): Match("E1", "exact_email", 1.0),  # excluded
        },
        unmatched=[("google", "", "c@x"), ("app-one", "app-one", "ghost")],
        ambiguous=[],
    )
    s = score_matching(result, owners)
    assert (s.precision.successes, s.precision.total) == (1, 3)
    assert (s.recall.successes, s.recall.total) == (1, 3)
    assert (s.unowned_left_unmatched.successes, s.unowned_left_unmatched.total) == (1, 2)
    assert s.not_in_key == 0


def test_the_cli_prints_a_table(keyed_world_dir, capsys):
    assert score.main(["matching", "--world", str(keyed_world_dir)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("| Rules only | Result |") and "Precision" in out and "Recall" in out


def test_the_results_doc_is_the_generated_table(keyed_world_dir):
    doc = DOC.read_text(encoding="utf-8")
    expected = render_matching(score.score_world(keyed_world_dir))
    assert score.read_block(doc) == expected, (
        "the matching table drifted: run `uv run --locked python -m meridian_access.score "
        "matching --world out/access/seed-6108 "
        "--write-doc docs/evals/2026-11-15-access-tuning-matching.md`"
    )


def test_replace_block_needs_its_markers():
    with pytest.raises(ValueError):
        score.replace_block("no markers", "x")
    assert (
        score.read_block(score.replace_block(f"a\n{score.START}\nold\n{score.END}\nb", "new\n"))
        == "new\n"
    )
