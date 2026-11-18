"""Slowness-only tickets are scored and reported apart (the owner's ruling of 2026-11-18 for
held_out_month2b; ADR-041 is the design: guide-covered everyday slowness is a how-to question).

They stay in every number as before; the report adds what they got, and gives the two pass-rule
numbers again without them. A set its writer never tagged has none, so its report is unchanged.
Built on the tuning month, with made-up tags."""

import json
from pathlib import Path

from meridian_dataset.knowledge import load_runbooks
from meridian_dataset.month import (
    ANSWER_GUIDE,
    HAND_OFF,
    HELD_OUT_MONTH1,
    TUNING_MONTH,
    load_templates,
    month_for,
)
from tier0.evals import month as mo

FEW = 200
ROOT = Path(__file__).resolve().parents[2]


def _records(month, k: int, outcome) -> list[dict]:  # type: ignore[no-untyped-def]
    out = []
    for run in range(1, k + 1):
        for t in month.tickets:
            got, guide = outcome(t, run)
            ok = got == t.expected and (got != ANSWER_GUIDE or guide == t.expected_guide)
            out.append(
                {
                    "ticket_id": t.id,
                    "run": run,
                    "outcome": got,
                    "guide": guide,
                    "stage": "test",
                    "triage": None,
                    "ok": ok,
                    "wrongly_answered": t.expected == HAND_OFF and got != HAND_OFF,
                }
            )
    return out


def _tagged() -> tuple[str, str]:
    """One how-to template keyed to the slowness guide and one hardware template, tagged."""
    templates = load_templates(TUNING_MONTH)
    how_to = next(t.id for t in templates if t.expected_guide == mo.SLOWNESS_GUIDE)
    hardware = next(t.id for t in templates if t.kind == "hardware")
    return how_to, hardware


def _scores():  # type: ignore[no-untyped-def]
    month = month_for(TUNING_MONTH)
    how_to, hardware = _tagged()
    other = "KB-001" if mo.SLOWNESS_GUIDE != "KB-001" else "KB-002"

    def outcome(t, run):  # type: ignore[no-untyped-def]
        if t.template_id in (how_to, hardware):
            # run 1: the slowness guide; run 2: a hand-off; run 3: another guide.
            return [
                (ANSWER_GUIDE, mo.SLOWNESS_GUIDE),
                (HAND_OFF, None),
                (ANSWER_GUIDE, other),
            ][run - 1]
        return t.expected, t.expected_guide

    records = _records(month, 3, outcome)
    tags = frozenset({how_to, hardware})
    return month, tags, mo.score(month, records, k=3, samples=FEW, slowness_only=tags)


def test_the_slowness_guide_is_the_slow_laptop_checks() -> None:
    guide = next(g for g in load_runbooks() if g.id == mo.SLOWNESS_GUIDE)
    assert guide.slug == "slow-laptop-checks"


def test_slowness_only_tickets_are_counted_apart_and_left_out_of_the_pass_numbers() -> None:
    month, tags, s = _scores()
    slow = [t for t in month.tickets if t.template_id in tags]
    n = len(slow)
    assert n > 0
    assert s["slowness_only"] == {
        "templates": 2,
        "tickets": n,
        "runs": 3 * n,
        "by_kind": {kind: sum(t.kind == kind for t in slow) for kind in {t.kind for t in slow}},
        "slowness_guide_runs": n,
        "hand_off_runs": n,
        "other_guide_runs": n,
        "username_runs": 0,
    }
    # In every number as before: the tagged hardware tickets were answered in 2 of 3 runs...
    hardware = sum(t.kind == "hardware" for t in slow)
    assert s["wrongly_answered"]["runs"] == 2 * hardware
    # ...and left out of the pass-rule numbers, which are then perfect.
    rest = s["without_slowness_only"]
    hand_offs = sum(t.expected == HAND_OFF and t.template_id not in tags for t in month.tickets)
    assert rest["wrongly_answered"] == {"runs": 0, "tickets": 0, "of_runs": 3 * hand_offs}
    assert rest["right_guide"]["value"] == 1.0
    assert s["right_guide"]["value"] < 1.0
    assert rest["right_guide"]["tickets"] == s["right_guide"]["tickets"] - (n - hardware)


def test_the_report_shows_the_slowness_section_only_when_there_are_tagged_tickets() -> None:
    _, _, s = _scores()
    old = json.loads((ROOT / "docs/evals/2026-11-17-m3c-sealed-month-haiku-3d2.json").read_text())
    tagged = mo.render({"config": old["config"], "scores": s}, "cmd")
    assert "## Slowness-only tickets, scored apart" in tagged
    assert f"Answered with the slowness guide ({mo.SLOWNESS_GUIDE})" in tagged
    month = month_for(TUNING_MONTH)
    plain = mo.score(
        month, _records(month, 3, lambda t, _: (t.expected, t.expected_guide)), k=3, samples=FEW
    )
    assert plain["slowness_only"]["tickets"] == 0
    assert "Slowness-only" not in mo.render({"config": old["config"], "scores": plain}, "cmd")


def test_untagged_sets_have_no_slowness_only_templates() -> None:
    # The rehearsal's month and the spent month carry no tags, so their reports are unchanged.
    assert mo.slowness_only_templates(TUNING_MONTH) == frozenset()
    assert mo.slowness_only_templates(HELD_OUT_MONTH1) == frozenset()
