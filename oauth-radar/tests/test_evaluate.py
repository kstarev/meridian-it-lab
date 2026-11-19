"""Precision and recall on the tuning world, against the planted answer key (milestone 1).

The numbers are pinned: a change to the world, the rubric or the policy that moves them has
to change this test and the published report in the same pull request.
"""

import ast
from pathlib import Path

import pytest

from meridian_dataset.oauth import KEY_DIR, RADAR_DIR, generate_oauth_world, write_oauth_world
from meridian_oauth_radar.evaluate import (
    FAIL,
    PASS,
    PRECISION_TARGET,
    RECALL_TARGET,
    Count,
    Evaluation,
    evaluate,
    markdown,
    verdict,
    wilson,
)

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "docs" / "evals" / "2026-11-19-oauth-radar-tuning.md"
SRC = ROOT / "oauth-radar" / "src" / "meridian_oauth_radar"


@pytest.fixture(scope="module")
def evaluation(tmp_path_factory: pytest.TempPathFactory) -> Evaluation:
    folder = tmp_path_factory.mktemp("oauth")
    write_oauth_world(generate_oauth_world(), folder / "w")
    return evaluate(
        folder / "w" / RADAR_DIR, folder / "w" / KEY_DIR / "answer_key.json", folder / "s"
    )


def test_the_headline_is_acted_on_with_the_lab_targets_and_both_fail(
    evaluation: Evaluation,
) -> None:
    c = evaluation.acted
    assert c.name == "Acted on by the radar"
    assert (PRECISION_TARGET, RECALL_TARGET) == (0.90, 1.0)
    assert (c.recall_verdict, c.precision_verdict) == (FAIL, FAIL)
    report = markdown(evaluation)
    headline = report.index("## Headline: acted on by the radar")
    second = report.index("## Second line: marked high by the rubric")
    assert headline < second
    assert (
        "| Recall: planted risky grants the radar acted on "
        "| 7 of 8 (87.5%; 95% CI 52.9% to 97.8%) | 100% | **FAIL** |"
    ) in report
    assert (
        "| Precision: grants the radar acted on that are planted "
        "| 7 of 36 (19.4%; 95% CI 9.8% to 35.0%) | at least 0.90 | **FAIL** |"
    ) in report


def test_the_second_line_is_marked_high_with_no_target(evaluation: Evaluation) -> None:
    report = markdown(evaluation)
    part = report[report.index("## Second line: marked high by the rubric") :]
    part = part[: part.index("## Each planted grant")]
    assert (
        "| 35 | 7 of 8 (87.5%; 95% CI 52.9% to 97.8%) | 28 "
        "| 7 of 35 (20.0%; 95% CI 10.0% to 35.9%) |"
    ) in part
    assert PASS not in part and FAIL not in part
    assert "Reported, with no target." in part


@pytest.mark.parametrize(
    ("k", "n", "target", "expected"),
    [
        (9, 10, 0.90, PASS),
        (89, 100, 0.90, FAIL),
        (8, 8, 1.0, PASS),
        (7, 8, 1.0, FAIL),
        (0, 0, 0.90, FAIL),
    ],
)
def test_a_verdict_can_pass_and_can_fail(k: int, n: int, target: float, expected: str) -> None:
    assert verdict(k, n, target) == expected


def test_a_perfect_count_passes_both_targets() -> None:
    perfect = Count("Acted on by the radar", flagged=8, found=8, planted=8)
    assert (perfect.recall_verdict, perfect.precision_verdict) == (PASS, PASS)
    one_false_alarm_in_ten = Count("Acted on by the radar", flagged=10, found=9, planted=9)
    assert one_false_alarm_in_ten.precision_verdict == PASS
    assert Count("x", flagged=11, found=9, planted=9).precision_verdict == FAIL


def test_marked_high_by_the_rubric(evaluation: Evaluation) -> None:
    c = evaluation.high
    assert (c.planted, c.found, c.flagged, c.false_alarms) == (8, 7, 35, 28)


def test_acted_on_by_the_radar(evaluation: Evaluation) -> None:
    c = evaluation.acted
    assert (c.planted, c.found, c.flagged, c.false_alarms) == (8, 7, 36, 29)


def test_the_one_plant_the_rubric_misses_is_caught_by_dormancy(evaluation: Evaluation) -> None:
    missed = [p for p in evaluation.plants if p.tier != "high"]
    assert [(p.app, p.tier, p.total, p.outcome) for p in missed] == [
        ("Mergewick Mail Merge", "medium", 4, "notice, then after-notice plan")
    ]


def test_the_leaver_is_high_but_only_listed_and_the_banned_grant_is_nudged(
    evaluation: Evaluation,
) -> None:
    by_kind = {p.kind: p for p in evaluation.plants}
    leaver, banned = by_kind["leaver_grant"], by_kind["banned_app"]
    assert (leaver.app, leaver.tier, leaver.total, leaver.outcome) == (
        "Huddlewave Meet",
        "high",
        4,
        "only listed for a person",
    )
    assert (banned.app, banned.tier, banned.outcome) == (
        "NoteSprout AI",
        "high",
        "Slack nudge draft",
    )
    missed = [p.kind for p in evaluation.plants if p.outcome == "only listed for a person"]
    assert missed == ["leaver_grant"]
    assert (evaluation.users, evaluation.leavers_in_directory) == (231, 1)


def test_dormant_grants_in_the_plan_a_week_later(evaluation: Evaluation) -> None:
    assert (evaluation.dormant, evaluation.dormant_in_plan, evaluation.excepted) == (66, 5, 1)


def test_the_published_report_is_the_generated_one(evaluation: Evaluation) -> None:
    assert REPORT.read_text(encoding="utf-8") == markdown(evaluation)


def test_wilson() -> None:
    low, high = wilson(6, 6)
    assert (round(low, 3), high) == (0.61, 1.0)
    assert wilson(0, 0) == (0.0, 1.0)


def test_only_the_grader_reads_an_answer_key() -> None:
    readers = []
    for path in sorted(SRC.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        strings = {
            node.value
            for node in ast.walk(ast.parse(text))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        if any("risky_grants" in s or "answer_key" in s for s in strings):
            readers.append(path.name)
    assert readers == ["evaluate.py"]
