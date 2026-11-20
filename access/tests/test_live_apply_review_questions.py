"""Pins from the review of the live apply path (2026-11-20, after PR #163).

Each test is a strict xfail with the source unchanged. It says what the code should do and
fails today.

- `test_g8_*` is gap G8: an Okta app assignment whose user is missing from the users list is
  planned as an orphan removal under its raw id. So a break-glass user's assignment can be
  removed, as apply's guard cannot see a name.
- `test_qN_*` are the review's open questions, by number. Q4 and Q12 have no test (by design
  today). Q11b has none either: to say "the bot branch holds only its two files", plan_pr needs
  a GitHub call it does not have, and the bot needs to know which pull requests it opened (Q11a).
  Both are design choices.

When the owner rules: on a yes, fix the code and drop the marker; on a no, delete the test.
Each marker has `raises=AssertionError`, so a test that breaks in its setup is reported as a
failure, not counted as the gap.

Copies of the tuning world and hand-made mocks only; no network, no model call.
"""

import copy
import json
import re
import shutil

import pytest

from meridian_access import apply as apply_mod
from meridian_access import plan_pr
from meridian_access.apply import apply_plan, issue_write_tokens
from meridian_access.approvals import parse_codeowners
from meridian_access.collect import Mocks, actual_state
from meridian_access.diff import REMOVING
from meridian_access.leaver_drill import coverage, pick_leaver
from meridian_access.mocks import GoogleMock, MockResponse, OktaMock, SlackMock
from meridian_access.models import CollectorError
from meridian_access.plan import Plan, build_plan, render_markdown, split_plan
from meridian_access.policy.load import load_policy
from meridian_access.summary import fill_summary

POLICY = load_policy()
D = "meridian.example"

G8_REASON = (
    "gap G8: an Okta app assignment whose user is missing from the users list is planned as an "
    "orphan removal under its raw id, so a break-glass user's assignment can be removed"
)


def open_question(number: str, what: str) -> pytest.MarkDecorator:
    return pytest.mark.xfail(
        strict=True, raises=AssertionError, reason=f"open question {number}: {what}"
    )


# ------------------------------------------------------------------ worlds and mocks


@pytest.fixture(scope="module")
def main_plan(world_dir):
    return split_plan(build_plan(world_dir, POLICY), POLICY)[0]


def copy_world(world_dir, tmp_path):
    dst = tmp_path / "world"
    shutil.copytree(world_dir, dst)
    return dst


def small_mocks(assignments=(("00u1", "0oaG"),)):
    okta = {
        "users": [
            {
                "id": uid,
                "status": "ACTIVE",
                "created": "",
                "lastLogin": None,
                "profile": {"login": login, "email": login},
            }
            for uid, login in (("00u1", f"ana@{D}"), ("00u2", f"ben@{D}"))
        ],
        "apps": [
            {"id": "0oaG", "label": "github", "name": "github", "status": "ACTIVE"},
            {"id": "0oaJ", "label": "jira", "name": "jira", "status": "ACTIVE"},
        ],
        "assignments": [
            {
                "appId": app,
                "userId": uid,
                "created": "",
                "scope": "USER",
                "profile": {"role": "member"},
            }
            for uid, app in assignments
        ],
        "system_log": [],
    }
    google = {"users": []}
    slack = {"schemas": [], "totalResults": 0, "Resources": []}
    return Mocks(
        OktaMock(okta, fault_rate=0.0),
        GoogleMock(google, fault_rate=0.0),
        SlackMock(slack, fault_rate=0.0),
    )


def row(system, action, account, app_id="", eid="E1"):
    return {
        "type": "mover_stale_access",
        "employee_id": eid,
        "account": account,
        "system": system,
        "app_id": app_id,
        "app_name": "",
        "action": action,
        "reason": "test",
        "evidence": [],
        "cause_date": "",
        "hold": "",
    }


def hand(mocks, rows):
    return Plan(
        {
            "version": 2,
            "part": "main",
            "as_of": "2026-12-01",
            "actual_state_sha256": actual_state(mocks, POLICY, sleep=lambda s: None).digest(),
            "changes": rows,
            "held": [],
            "warnings": [],
            "manual_tasks": [],
            "approvals_required": 1,
        }
    )


def apply_hand(mocks, plan):
    return apply_plan(
        plan,
        mocks,
        mode="mock-live",
        approvals=1,
        policy=POLICY,
        write_token=issue_write_tokens(mocks),
        sleep=lambda s: None,
        rebuild=lambda state: plan,
    )


def okta_user(mocks, uid):
    return next(u for u in mocks.okta.data["users"] if u["id"] == uid)


# ------------------------------------------------------------------ apply


@open_question(
    "Q1",
    "the actual-state hash does not cover the Okta app list, so an app deleted and re-created "
    "under its name after approval leaves the plan current",
)
def test_q1_an_okta_app_replaced_after_the_plan_makes_the_plan_stale():
    mocks = small_mocks(assignments=())
    plan = hand(mocks, [row("okta", "add", f"ana@{D}", "jira")])
    apps = mocks.okta.data["apps"]
    apps[:] = [a for a in apps if a["id"] != "0oaJ"] + [
        {"id": "0oaNEWJIRA", "label": "jira (new)", "name": "jira", "status": "ACTIVE"}
    ]
    result = apply_hand(mocks, plan)
    assert result.refused_reason == "stale"  # today: ana is assigned to the new app


@open_question(
    "Q2",
    "apply does not read a target again just before its write, so a target renamed to a "
    "break-glass name after apply's state read is changed",
)
def test_q2_a_target_renamed_to_break_glass_after_the_state_read_is_not_changed(monkeypatch):
    mocks = small_mocks()
    plan = hand(mocks, [row("okta", "suspend", f"ana@{D}")])
    new_name = f"breakglass-09@{D}"
    assert POLICY.is_break_glass(new_name)

    reads = []
    real_actual_state = apply_mod.actual_state

    def actual_state_then_count(*args, **kwargs):
        state = real_actual_state(*args, **kwargs)
        reads.append(1)
        return state

    monkeypatch.setattr(apply_mod, "actual_state", actual_state_then_count)
    original = mocks.okta.request
    renamed = []

    def rename_after_the_read(method, path, **kw):
        # the first Okta request after apply's state read, whatever it is, finds ana renamed;
        # a read by id before the write would see the new name
        if reads and not renamed:
            renamed.append(f"{method} {path}")
            okta_user(mocks, "00u1")["profile"] = {"login": new_name, "email": new_name}
        return original(method, path, **kw)

    mocks.okta.request = rename_after_the_read
    apply_hand(mocks, plan)
    assert renamed, "the target was renamed after apply's state read"
    assert okta_user(mocks, "00u1")["status"] == "ACTIVE"  # today: the break-glass login suspended


@open_question(
    "Q3",
    "when a person's removal fails, apply still gives them new access in the same run",
)
def test_q3_a_person_whose_removal_failed_gets_no_new_access_in_the_same_run():
    mocks = small_mocks()
    plan = hand(
        mocks,
        [row("okta", "remove", f"ana@{D}", "github"), row("okta", "add", f"ana@{D}", "jira")],
    )
    original = mocks.okta.request

    def no_delete(method, path, **kw):
        if method == "DELETE":
            return MockResponse(503, {}, {})
        return original(method, path, **kw)

    mocks.okta.request = no_delete
    result = apply_hand(mocks, plan)
    assert [c["action"] for c in result.failed if c["app_id"] == "github"] == ["remove"]
    assert not any(a["appId"] == "0oaJ" for a in mocks.okta.data["assignments"])  # today: added


@open_question(
    "Q5",
    "apply compares only the changes and two hashes with the rebuild, so edits to the rest of "
    "the plan file (held, warnings, counts) pass",
)
def test_q5_a_plan_file_edited_outside_its_changes_is_refused(main_plan, world_dir):
    assert main_plan.data["held"], "the tuning plan holds changes for bad HR data"
    edited = copy.deepcopy(main_plan.data)
    edited["held"], edited["warnings"] = [], []
    result = apply_plan(
        Plan(edited),
        Mocks(
            OktaMock.from_files(world_dir, fault_rate=0.0),
            GoogleMock.from_files(world_dir, fault_rate=0.0),
            SlackMock.from_files(world_dir, fault_rate=0.0),
        ),
        mode="dry-run",
        approvals=1,
        policy=POLICY,
        sleep=lambda s: None,
        world_dir=world_dir,
    )
    assert result.refused_reason == "plan changed"  # today: applied, and the log says 0 held


# ------------------------------------------------------------------ collect / diff


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=G8_REASON)
def test_g8_an_okta_assignment_whose_user_is_not_listed_is_never_removed(world_dir, tmp_path):
    dst = copy_world(world_dir, tmp_path)
    okta = json.loads((dst / "okta.json").read_text())
    bg = next(u for u in okta["users"] if u["profile"]["login"] == f"breakglass-01@{D}")
    assert any(a["userId"] == bg["id"] for a in okta["assignments"]), "it has app assignments"
    okta["users"].remove(bg)  # e.g. a users page that came back short
    (dst / "okta.json").write_text(json.dumps(okta))
    try:
        main, held = split_plan(build_plan(dst, POLICY), POLICY)
    except CollectorError:
        return  # the collector refused the state: one of the two answers the review offers
    rows = [*main.changes, *(held.changes if held else [])]
    taken = [c for c in rows if c["account"] == bg["id"] and c["action"] in REMOVING]
    assert taken == []  # today: the break-glass user's app assignment is removed


@open_question(
    "Q7",
    "the diff makes one change per name with case folded, so a leaver's second account that "
    "only case tells apart waits for the next plan",
)
def test_q7_a_leaver_with_two_accounts_that_only_case_tells_apart_gets_two_changes(
    main_plan, world_dir, tmp_path
):
    leaver = next(
        c for c in main_plan.changes if c["system"] == "slack" and c["type"] == "leaver_access_left"
    )
    dst = copy_world(world_dir, tmp_path)
    slack = json.loads((dst / "slack_scim.json").read_text())
    own = next(u for u in slack["Resources"] if u["userName"] == leaver["account"])
    twin = own["userName"].upper()
    assert twin != own["userName"]
    slack["Resources"].append({**own, "id": "UTWIN2", "userName": twin})
    slack["totalResults"] = len(slack["Resources"])
    (dst / "slack_scim.json").write_text(json.dumps(slack))
    main, held = split_plan(build_plan(dst, POLICY), POLICY)
    rows = [*main.changes, *(held.changes if held else [])]
    names = sorted(
        c["account"]
        for c in rows
        if c["system"] == "slack" and c["account"].casefold() == own["userName"].casefold()
    )
    assert names == sorted([own["userName"], twin])  # today: one change


# ------------------------------------------------------------------ leaver drill


@open_question(
    "Q8",
    "the leaver drill counts an account as covered by a change whose name matches it only with "
    "case folded",
)
def test_q8_the_leaver_drill_counts_each_account_by_its_exact_name(world_dir, tmp_path):
    dst = copy_world(world_dir, tmp_path)
    eid = pick_leaver(dst, POLICY)
    hris = json.loads((dst / "hris.json").read_text())
    email = next(r["work_email"] for r in hris["records"] if r["employee_id"] == eid)
    slack = json.loads((dst / "slack_scim.json").read_text())
    own = next(
        u for u in slack["Resources"] if any(e["value"] == email for e in u.get("emails", []))
    )
    twin = own["userName"].upper()
    assert twin != own["userName"]
    slack["Resources"].append({**own, "id": "UTWIN3", "userName": twin})
    slack["totalResults"] = len(slack["Resources"])
    (dst / "slack_scim.json").write_text(json.dumps(slack))
    # one change, for the leaver's own Slack account by its exact name: the drill's count is
    # checked on its own, whatever the diff does (Q7)
    changes = [row("slack", "remove", own["userName"], eid=eid)]
    covered, missing = coverage(dst, eid, changes, POLICY)
    assert ["slack", "", own["userName"]] in covered
    assert ["slack", "", twin] in covered + missing, "the twin is matched to the leaver"
    assert ["slack", "", twin] in missing  # today: covered by the other account's change


# ------------------------------------------------------------------ PLAN.md, summary, plan PR


def _outside_code_spans(md: str) -> str:
    return re.sub(r"`[^`\n]*`", "", md)


@open_question(
    "Q9",
    "PLAN.md writes fields from the world as they are, so an HTML comment in one hides the rest",
)
def test_q9_plan_md_shows_a_field_with_an_html_comment_as_text(main_plan):
    data = copy.deepcopy(main_plan.data)
    first = next(c for c in data["changes"] if c["type"] == "leaver_access_left")
    first["account"] = "x<!--"  # e.g. an export cell; GitHub hides everything after it
    md = render_markdown(data)
    # escaped (a backslash or &lt;) or in a code span, it opens no comment
    assert not re.search(r"(?<!\\)<!--", _outside_code_spans(md))  # today: written bare


@open_question(
    "Q9",
    "PLAN.md keeps line breaks in fields from the world, so one can add a fake section",
)
def test_q9_plan_md_keeps_a_line_break_in_a_field_from_making_a_heading(main_plan):
    data = copy.deepcopy(main_plan.data)
    data["warnings"] = [*data["warnings"], "unmapped title 'a\n\n## Warnings\n\nNone.'"]
    md = render_markdown(data)
    headings = [
        line for line in md.splitlines() if re.fullmatch(r" {0,3}#{1,6}[ \t]+Warnings[ \t#]*", line)
    ]
    assert len(headings) == 1  # today: a second, fake Warnings section


@open_question(
    "Q10",
    "the summary check looks at words only, so a model paragraph can carry markup or an "
    "@-mention into the PR body",
)
@pytest.mark.parametrize(
    "paragraph",
    [
        "The plan has {total} changes. <!--",
        "Please @admin review the plan.",
        "# The plan\n\n- [ ] approved",  # "x" is not an allowed word, so "[x]" is refused
    ],
    ids=["html comment", "mention", "heading and task list"],
)
def test_q10_a_model_summary_with_markup_or_a_mention_falls_back_to_the_template(
    main_plan, paragraph
):
    assert fill_summary(paragraph, main_plan) is None  # today: accepted


class Fake:
    """A pull request from the bot branch that a person opened, with their own file on it."""

    def __init__(self, plan_bytes):
        self.files = {
            "main": {},
            "access/plan": {"access/plan/plan.json": plan_bytes, "Makefile": b"x"},
        }
        self.pull = {
            "number": 7,
            "head": "access/plan",
            "state": "open",
            "user": {"login": "someone"},
        }
        self.actions = []

    def open_pull(self, branch):
        return self.pull if branch == "access/plan" else None

    def file_at(self, ref, path):
        return self.files.get(ref, {}).get(path)

    def branch_sha(self, branch):
        return branch if branch in self.files else None

    def commit_on(self, base_sha, files, message):
        self.actions.append("commit")
        return "c1"

    def set_branch(self, branch, sha):
        self.actions.append(f"push {branch}")

    def delete_branch(self, branch):
        self.actions.append(f"delete {branch}")

    def create_pull(self, branch, title, body):
        raise AssertionError("not expected")

    def update_pull(self, number, title, body):
        self.actions.append(f"update #{number}")

    def close_pull(self, number):
        self.actions.append(f"close #{number}")

    def comment(self, number, body):
        self.actions.append(f"comment #{number}")


def _built(changes):
    data = {
        "as_of": "2026-12-01",
        "part": "main",
        "approvals_required": 1,
        "changes": changes,
        "counts": {"total": len(changes), "by_type": {}},
    }
    raw = json.dumps(data, sort_keys=True).encode()
    return plan_pr.Built(plan=raw, markdown=b"# Access plan\n", summary="# Summary\n")


@open_question(
    "Q11a",
    "plan_pr takes over any open pull request from its branch, whoever opened it",
)
@pytest.mark.parametrize("new_plan", ["changed", "empty"])
def test_q11a_the_bot_leaves_alone_a_pull_request_from_its_branch_that_it_did_not_open(new_plan):
    built = _built([{"action": "remove", "type": "x"}]) if new_plan == "changed" else None
    gh = Fake(b"old")
    plan_pr.sync_part(gh, plan_pr.MAIN, built, "run")
    # today, changed: force-pushed over it and its description replaced;
    # empty: commented on, closed, and its branch deleted
    assert gh.actions == []


# ------------------------------------------------------------------ approvals


@open_question(
    "Q13",
    "parse_codeowners adds up every matching CODEOWNERS line, where GitHub takes the last one",
)
def test_q13_the_approvals_check_uses_the_last_codeowners_line_as_github_does():
    text = "/access/plan/ @old-owner\n/access/plan/ @new-owner\n"
    assert parse_codeowners(text) == {"new-owner"}  # today: both count
