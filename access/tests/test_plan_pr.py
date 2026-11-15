"""The rolling plan pull requests: open, update, close, one part each, run against a fake."""

import io
import json
import urllib.error
from email.message import Message
from itertools import count
from pathlib import Path

import pytest

from meridian_access import plan as plan_cli
from meridian_access import plan_pr
from meridian_access.plan_pr import HELD, MAIN, Built, RestGitHub, sync_part

RUN = "[run 1](https://github.com/o/r/actions/runs/1)"


class FakeGitHub:
    """Branches are dicts of path -> bytes on top of `main`; one commit per push."""

    def __init__(self, main_files=None):
        self._ids = count(1)
        self.commits = {"c0": {"files": dict(main_files or {}), "parent": None, "message": ""}}
        self.refs = {"main": "c0"}
        self.pulls = {}
        self.comments = []
        self.pushes = []
        self.deleted = []

    def open_pull(self, branch):
        for p in self.pulls.values():
            if p["head"] == branch and p["state"] == "open":
                return p
        return None

    def file_at(self, ref, path):
        sha = self.refs.get(ref)
        return None if sha is None else self.commits[sha]["files"].get(path)

    def branch_sha(self, branch):
        return self.refs.get(branch)

    def commit_on(self, base_sha, files, message):
        sha = f"c{next(self._ids)}"
        merged = {**self.commits[base_sha]["files"], **files}
        self.commits[sha] = {
            "files": merged,
            "parent": base_sha,
            "added": dict(files),
            "message": message,
        }
        return sha

    def set_branch(self, branch, sha):
        assert branch != "main"
        self.refs[branch] = sha
        self.pushes.append((branch, sha))

    def delete_branch(self, branch):
        self.refs.pop(branch, None)
        self.deleted.append(branch)

    def create_pull(self, branch, title, body):
        n = 100 + len(self.pulls)
        self.pulls[n] = {"number": n, "head": branch, "title": title, "body": body, "state": "open"}
        return self.pulls[n]

    def update_pull(self, number, title, body):
        self.pulls[number].update(title=title, body=body)

    def close_pull(self, number):
        self.pulls[number]["state"] = "closed"

    def comment(self, number, body):
        self.comments.append((number, body))


def change(i, action="remove", type_="leaver_access_left"):
    return {
        "type": type_,
        "employee_id": f"E{i:04d}",
        "system": "okta",
        "app_id": "app",
        "account": f"p{i}@meridian.example",
        "action": action,
    }


def built(changes, as_of="2026-12-01", part="main", approvals=1, total=None):
    by_type = {}
    for c in changes:
        by_type[c["type"]] = by_type.get(c["type"], 0) + 1
    data = {
        "as_of": as_of,
        "part": part,
        "approvals_required": approvals,
        "changes": changes,
        "counts": {"total": len(changes) if total is None else total, "by_type": by_type},
        "actual_state_sha256": "a" * 64,
        "policy_sha256": "b" * 64,
    }
    raw = plan_cli.canonical_json(data).encode()
    return Built(plan=raw, markdown=b"# Access plan\n", summary="# Access plan summary\n")


# --------------------------------------------------------------------------- the decisions


def test_a_new_plan_opens_one_pull_request_per_part_with_only_that_parts_files():
    gh = FakeGitHub()
    assert sync_part(gh, MAIN, built([change(1)]), RUN) == "opened #100"
    assert sync_part(gh, HELD, built([change(2)], part="held", approvals=2), RUN) == "opened #101"
    main_commit = gh.commits[gh.refs["access/plan"]]
    held_commit = gh.commits[gh.refs["access/plan-held"]]
    assert set(main_commit["added"]) == {"access/plan/plan.json", "access/plan/PLAN.md"}
    assert set(held_commit["added"]) == {"access/plan/held/plan.json", "access/plan/held/PLAN.md"}
    assert main_commit["parent"] == held_commit["parent"] == gh.refs["main"]
    assert gh.pulls[100]["title"] == "Access plan for 2026-12-01"
    assert "two approvals" in gh.pulls[101]["title"]
    assert "two code-owner approvals" in gh.pulls[101]["body"]
    assert "one code-owner approval" in gh.pulls[100]["body"]
    assert "# Access plan summary" in gh.pulls[100]["body"]
    assert RUN in gh.pulls[100]["body"]
    assert gh.comments == []  # the description is enough when a pull request opens


def test_the_same_plan_again_pushes_nothing_so_approvals_stay():
    gh = FakeGitHub()
    sync_part(gh, MAIN, built([change(1)]), RUN)
    pushes = list(gh.pushes)
    assert sync_part(gh, MAIN, built([change(1)]), RUN) == "unchanged #100"
    assert gh.pushes == pushes
    assert gh.comments == []


def test_a_changed_plan_force_pushes_replaces_the_description_and_says_what_changed():
    gh = FakeGitHub()
    sync_part(gh, MAIN, built([change(1), change(2)]), RUN)
    first = gh.refs["access/plan"]
    new = built([change(1), change(3), change(4, "suspend", "leave_of_absence")], approvals=1)
    assert sync_part(gh, MAIN, new, RUN) == "updated #100"
    assert gh.refs["access/plan"] != first
    assert gh.commits[gh.refs["access/plan"]]["parent"] == "c0"  # rebuilt on main, not stacked
    assert gh.file_at("access/plan", "access/plan/plan.json") == new.plan
    ((number, text),) = gh.comments
    assert number == 100
    assert "2 changes added, 1 removed" in text
    assert "| leave_of_absence | 0 | 1 |" in text
    assert "| total | 2 | 3 |" in text
    assert "leaver_access_left" not in text  # 2 before, 2 after: not listed
    assert "approvals were dismissed" in text


def test_the_comment_names_a_new_actual_state_and_approval_count():
    old = json.loads(built([change(1)]).plan)
    new = {**old, "approvals_required": 2, "actual_state_sha256": "c" * 64}
    text = plan_pr.change_comment(old, new, RUN)
    assert "Approvals required: 1 → 2." in text
    assert "Actual-state hash: aaaaaaaaaaaa → cccccccccccc." in text
    assert "Policy hash" not in text


def test_an_empty_plan_closes_the_pull_request_and_deletes_the_bot_branch():
    gh = FakeGitHub()
    sync_part(gh, MAIN, built([change(1)]), RUN)
    only_notes = built([change(9, "never_touch", "break_glass")])
    assert sync_part(gh, MAIN, only_notes, RUN) == "closed #100 (empty)"
    assert gh.pulls[100]["state"] == "closed"
    assert "access/plan" not in gh.refs and gh.deleted == ["access/plan"]
    assert "empty" in gh.comments[-1][1]
    assert sync_part(gh, MAIN, only_notes, RUN) == "empty, no pull request"


def test_no_held_plan_closes_the_held_pull_request_and_leaves_the_main_one():
    gh = FakeGitHub()
    sync_part(gh, MAIN, built([change(1)]), RUN)
    sync_part(gh, HELD, built([change(2)], part="held", approvals=2), RUN)
    assert sync_part(gh, HELD, None, RUN) == "closed #101 (empty)"
    assert gh.pulls[100]["state"] == "open" and gh.refs.get("access/plan")
    assert sync_part(gh, HELD, None, RUN) == "empty, no pull request"


def test_a_plan_already_on_main_opens_nothing_and_closes_a_stale_pull_request():
    b = built([change(1)])
    gh = FakeGitHub(main_files={"access/plan/plan.json": b.plan})
    assert sync_part(gh, MAIN, b, RUN) == "already on main, no pull request"
    assert gh.pulls == {} and gh.pushes == []
    gh2 = FakeGitHub()
    sync_part(gh2, MAIN, b, RUN)
    gh2.commits["c0"]["files"]["access/plan/plan.json"] = b.plan  # the owner merged it
    assert sync_part(gh2, MAIN, b, RUN) == "closed #100 (already on main)"


def test_a_closed_pull_request_with_its_branch_left_is_opened_again_from_main():
    gh = FakeGitHub()
    sync_part(gh, MAIN, built([change(1)]), RUN)
    gh.pulls[100]["state"] = "closed"  # closed by hand, branch kept
    assert sync_part(gh, MAIN, built([change(1)]), RUN) == "opened #101"


def test_a_very_long_summary_is_cut_to_fit_a_pull_request_description():
    b = built([change(1)])
    long = Built(plan=b.plan, markdown=b.markdown, summary="x" * 80_000)
    body = plan_pr.body_for(MAIN, long, RUN)
    assert len(body) < 65_536
    assert "cut to fit" in body


def test_with_the_real_tuning_plan_each_pull_request_touches_exactly_one_plan_file(
    world_dir: Path, tmp_path: Path
):
    out = tmp_path / "plan"
    plan_cli.main(["--world", str(world_dir), "--out", str(out)])
    gh = FakeGitHub()
    results = {p.name: sync_part(gh, p, plan_pr.read_part(out, p), RUN) for p in plan_pr.PARTS}
    assert results == {"main": "opened #100", "held": "opened #101"}
    for branch, plan_path in (
        ("access/plan", "access/plan/plan.json"),
        ("access/plan-held", "access/plan/held/plan.json"),
    ):
        added = gh.commits[gh.refs[branch]]["added"]
        assert [p for p in added if p.endswith("plan.json")] == [plan_path]
    held = json.loads(gh.file_at("access/plan-held", "access/plan/held/plan.json"))
    assert held["part"] == "held" and held["approvals_required"] == 2
    # Built again from the same inputs, nothing moves.
    again = tmp_path / "again"
    plan_cli.main(["--world", str(world_dir), "--out", str(again)])
    pushes = list(gh.pushes)
    for p in plan_pr.PARTS:
        assert sync_part(gh, p, plan_pr.read_part(again, p), RUN).startswith("unchanged")
    assert gh.pushes == pushes


# --------------------------------------------------------------------------- the REST client


class Resp:
    def __init__(self, status, payload):
        self.status, self._payload = status, payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(code, headers=None):
    msg = Message()
    for k, v in (headers or {}).items():
        msg[k] = v
    return urllib.error.HTTPError("u", code, "x", msg, io.BytesIO(b'{"message":"no"}'))


class Recorder:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, req):
        body = json.loads(req.data) if req.data else None
        self.calls.append((req.get_method(), req.full_url, body, dict(req.header_items())))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return Resp(200, json.dumps(answer).encode() if not isinstance(answer, bytes) else answer)


def client(answers):
    rec = Recorder(answers)
    return RestGitHub("o/r", "tok", opener=rec, sleep=lambda s: rec.calls.append(("sleep", s))), rec


def test_the_client_makes_a_commit_on_main_with_blobs_and_a_tree():
    gh, rec = client(
        [
            {"tree": {"sha": "T0"}},
            {"sha": "B1"},
            {"sha": "B2"},
            {"sha": "T1"},
            {"sha": "C1"},
        ]
    )
    files = {"access/plan/plan.json": b"{}", "access/plan/PLAN.md": b"# x\n"}
    assert gh.commit_on("M0", files, "msg") == "C1"
    methods = [(c[0], c[1].removeprefix("https://api.github.com/repos/o/r/")) for c in rec.calls]
    assert methods == [
        ("GET", "git/commits/M0"),
        ("POST", "git/blobs"),
        ("POST", "git/blobs"),
        ("POST", "git/trees"),
        ("POST", "git/commits"),
    ]
    assert rec.calls[3][2]["base_tree"] == "T0"
    assert [e["path"] for e in rec.calls[3][2]["tree"]] == list(files)
    assert rec.calls[4][2] == {"message": "msg", "tree": "T1", "parents": ["M0"]}
    assert rec.calls[0][3]["Authorization"] == "Bearer tok"


def test_the_client_creates_a_missing_branch_and_force_updates_an_existing_one():
    gh, rec = client([http_error(404), {"ref": "refs/heads/access/plan"}])
    gh.set_branch("access/plan", "C1")
    assert rec.calls[-1][:3] == (
        "POST",
        "https://api.github.com/repos/o/r/git/refs",
        {"ref": "refs/heads/access/plan", "sha": "C1"},
    )
    gh, rec = client([{"object": {"sha": "C0"}}, {}])
    gh.set_branch("access/plan", "C2")
    assert rec.calls[-1][:3] == (
        "PATCH",
        "https://api.github.com/repos/o/r/git/refs/heads/access/plan",
        {"sha": "C2", "force": True},
    )


def test_the_client_finds_the_open_pull_request_by_head_branch():
    gh, rec = client([[{"number": 7}]])
    assert gh.open_pull("access/plan-held") == {"number": 7}
    url = rec.calls[0][1]
    assert "state=open" in url and "head=o%3Aaccess%2Fplan-held" in url and "base=main" in url


def test_the_client_reads_raw_files_and_a_missing_file_is_none():
    gh, rec = client([b'{"a":1}', http_error(404)])
    assert gh.file_at("access/plan", "access/plan/plan.json") == b'{"a":1}'
    assert rec.calls[0][3]["Accept"] == "application/vnd.github.raw"
    assert gh.file_at("main", "access/plan/plan.json") is None


def test_the_client_retries_429_and_5xx_and_gives_up_on_other_errors():
    gh, rec = client([http_error(429, {"Retry-After": "3"}), http_error(502), [{"number": 1}]])
    assert gh.open_pull("access/plan") == {"number": 1}
    assert ("sleep", 3.0) in rec.calls
    gh, _ = client([http_error(422)])
    with pytest.raises(RuntimeError, match="HTTP 422"):
        gh.create_pull("access/plan", "t", "b")


def test_the_cli_refuses_to_run_without_a_token(monkeypatch, tmp_path):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        plan_pr.main(["--plan-dir", str(tmp_path), "--repo", "o/r"])


def test_the_run_link_comes_from_the_actions_environment():
    env = {
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_REPOSITORY": "o/r",
        "GITHUB_RUN_ID": "42",
    }
    assert plan_pr.run_url_from_env(env) == "[run 42](https://github.com/o/r/actions/runs/42)"
    assert plan_pr.run_url_from_env({}) == "a local run"
