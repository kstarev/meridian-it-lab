"""Blast radius on the tuning data: the numbers, the generated doc, and what needs two approvals."""

import json
import shutil
from pathlib import Path

import pytest

from meridian_access import blast
from meridian_access.approvals import required_for
from meridian_access.blast import blast_radius_report, percentile
from meridian_access.collect import ActualState, actual_state
from meridian_access.desired import load_hris
from meridian_access.diff import MIN_BATCH_PEOPLE
from meridian_access.match import match_accounts
from meridian_access.plan import build_plan

DOC = Path(__file__).resolve().parents[2] / "docs" / "evals" / "2026-11-15-access-blast-radius.md"


@pytest.fixture(scope="module")
def plan(world_dir, policy):
    return build_plan(world_dir, policy)


@pytest.fixture(scope="module")
def report(world_dir, policy):
    return blast_radius_report(world_dir, policy)


def test_percentiles_are_nearest_rank():
    assert percentile([5, 1, 3, 2, 4], 50) == 3
    assert percentile(list(range(1, 11)), 90) == 9
    assert percentile([7], 90) == 7


def test_every_removal_is_counted_once_and_the_backlog_is_its_own_bucket(report, plan):
    both = [*plan.data["changes"], *plan.data["held"]]
    total = sum(1 for c in both if c["action"] in ("remove", "suspend"))
    assert sum(report["per_day"]) + report["backlog"] == total
    assert len(report["per_day"]) == 30 and report["backlog"] > 0
    assert report["per_day"][0] == 0  # day 0 holds only day-0 events, not the backlog
    assert report["max"] == max(report["per_day"]) == report["bulk_count"]
    assert report["p50"] <= report["p90"] <= report["normal_max"]


def test_percentiles_are_over_event_days_1_to_29_without_the_bulk_day(report):
    days = [n for d, n in enumerate(report["per_day"]) if d != 0 and d != report["bulk_day"]]
    assert len(days) == 28
    assert report["p50"] == percentile(days, 50) and report["p90"] == percentile(days, 90)
    body = blast.render(report)
    assert "event days 1-29 excluding the bulk-import day" in body


def test_an_hr_event_outside_the_30_days_is_an_error(world_dir, policy, tmp_path):
    dst = tmp_path / "world"
    shutil.copytree(world_dir, dst)
    hris = json.loads((dst / "hris.json").read_text(encoding="utf-8"))
    hris["events"][0]["day"] = 30
    (dst / "hris.json").write_text(json.dumps(hris), encoding="utf-8")
    with pytest.raises(ValueError, match="outside 0-29"):
        blast_radius_report(dst, policy)


def test_the_proposal_uses_the_diffs_people_threshold_and_does_not_recommend_raising_n(report):
    body = blast.render(report)
    assert f"touch {MIN_BATCH_PEOPLE} or more people" in body and "`MIN_BATCH_PEOPLE`" in body
    assert "is not recommended" in body and "or raise N" not in body
    assert "assumes one plan per day" in body
    assert f"{report['plan_removals']} removals and suspensions that are not held" in body
    assert f"needs {report['plan_approvals']} approvals regardless" in body


def test_the_bulk_import_day_is_far_above_every_normal_day(report):
    assert report["bulk_count"] > report["limit"]
    assert report["bulk_count"] > 5 * report["normal_max"]


def test_the_doc_says_honestly_that_a_normal_day_is_above_n(report):
    # milestone 5 found two ordinary terminations causing 33 removals; with a leave suspension
    # the same day is 36, over N = 25, and the doc must say so and propose a new N
    assert report["normal_over_limit"]
    assert max(report["per_day"][d] for d in report["normal_over_limit"]) > report["limit"]
    body = blast.render(report)
    assert "does not cleanly separate" in body and "Proposal" in body


def people_plan(world_dir, policy, wanted):
    """The plan for only the accounts of `wanted` people (a plan with nothing else in it)."""
    records, _ = load_hris(world_dir / "hris.json")
    full = actual_state(world_dir, policy)
    matches = match_accounts(full.accounts, records, policy).matches
    keys = {key for key, m in matches.items() if m.employee_id in wanted}
    only = ActualState(tuple(a for a in full.accounts if a.key in keys), (), None)
    return build_plan(world_dir, policy, state=only)


def test_the_planted_mass_termination_needs_two_approvals(world_dir, policy):
    _, events = load_hris(world_dir / "hris.json")
    bulk = {e.employee_id for e in events if e.kind == "terminate" and e.source == "bulk_import"}
    assert len(bulk) >= MIN_BATCH_PEOPLE
    small = people_plan(world_dir, policy, bulk)
    actions = {c["action"] for c in small.data["changes"]}
    assert "block_two_approvers" in actions and small.data["held"]
    # what is left once the block is set aside is under N, so the two approvals come from the
    # block and not from the removal count
    rest = [c for c in small.data["changes"] if c["action"] != "block_two_approvers"]
    assert (
        sum(1 for c in rest if c["action"] in ("remove", "suspend"))
        <= policy.settings.blast_radius_n
    )
    assert required_for(rest, policy) == 1
    assert small.data["approvals_required"] == 2 == required_for(small.data["changes"], policy)


def test_a_normal_leavers_plan_needs_one_approval(world_dir, policy, plan):
    _, events = load_hris(world_dir / "hris.json")
    hr_leavers = {e.employee_id for e in events if e.kind == "terminate" and e.source == "hr"}
    leaver = next(
        eid
        for eid in sorted(hr_leavers)
        if any(c["employee_id"] == eid and c["type"] == "leaver_access_left" for c in plan.changes)
        and 0
        < sum(
            1
            for c in plan.changes
            if c["employee_id"] == eid and c["action"] in ("remove", "suspend")
        )
        <= 25
    )
    small = people_plan(world_dir, policy, {leaver})
    removals = small.counts["removals_and_suspensions"]
    assert 0 < removals <= policy.settings.blast_radius_n
    assert small.data["approvals_required"] == 1


def test_the_whole_month_plan_needs_two_approvals_without_counting_the_held_changes(plan, report):
    assert plan.data["held"] and plan.data["approvals_required"] == 2
    assert report["plan_removals"] > report["limit"]


def test_the_results_doc_is_the_generated_report(report):
    doc = DOC.read_text(encoding="utf-8")
    assert blast.read_block(doc) == blast.render(report), (
        "refresh with `uv run --locked python -m meridian_access.blast "
        "--world out/access/seed-6108 --write-doc docs/evals/2026-11-15-access-blast-radius.md`"
    )


def test_the_block_needs_its_markers():
    with pytest.raises(ValueError):
        blast.replace_block("no markers", "x")
    doc = f"a\n{blast.START}\nold\n{blast.END}\nb\n"
    assert blast.read_block(blast.replace_block(doc, "new\n")) == "new\n"
