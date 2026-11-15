"""Open, update and close the two rolling plan pull requests (AAC-002, AAC-008).

    python -m meridian_access.plan_pr --plan-dir out/access/plan --repo OWNER/NAME

The plan has two parts, each in its own rolling pull request into `main`:

- the main plan: bot branch `access/plan`, files `access/plan/plan.json` and `PLAN.md`;
- the held plan: bot branch `access/plan-held`, files `access/plan/held/plan.json` and
  `PLAN.md` (two code-owner approvals, AAC-007).

For each part, on every run:

- **empty** (no plan file, or no change other than a break-glass `never_touch` note): an open
  pull request is closed with a comment, and its bot branch is deleted;
- **already on `main`** (the new `plan.json` is byte-for-byte the one on `main`): there is
  nothing to approve, so an open pull request is closed the same way;
- **unchanged** (an open pull request already holds this exact `plan.json`): nothing is pushed,
  so the approvals on it stay valid;
- **changed**: one commit on top of `main` with the part's two files is force-pushed to the bot
  branch (the ruleset then dismisses earlier approvals), the description is replaced with the
  new summary, and a comment says what changed;
- **new**: the same commit, and a pull request is opened.

A pull request holds one part only: each commit adds that part's files and nothing else, so
the approvals check sees one `plan.json` per pull request.

The token comes from `GH_TOKEN` (in the workflow: the GitHub App's installation token, so CI
and the approvals check run on the pull requests, which they wouldn't with `GITHUB_TOKEN`).
Commits are made with the Git Data API, so no git credentials are ever written to disk. The
logic in `sync_part` talks to a `GitHub` interface; the tests run it against an in-memory fake
and never touch the network.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from meridian_access.plan import canonical_json

BASE = "main"
API = "https://api.github.com"
BODY_LIMIT = 60_000  # GitHub's limit is 65,536 characters; keep a margin for the footer
INERT_ACTIONS = frozenset({"never_touch"})


@dataclass(frozen=True)
class Part:
    name: str  # "main" or "held"
    branch: str
    subdir: str  # under the --plan-dir
    repo_dir: str  # where the files go in the repo
    approvals: str

    @property
    def plan_path(self) -> str:
        return f"{self.repo_dir}/plan.json"

    @property
    def md_path(self) -> str:
        return f"{self.repo_dir}/PLAN.md"


MAIN = Part("main", "access/plan", "", "access/plan", "one code-owner approval")
HELD = Part("held", "access/plan-held", "held", "access/plan/held", "two code-owner approvals")
PARTS = (MAIN, HELD)


@dataclass(frozen=True)
class Built:
    """One part as the plan job built it."""

    plan: bytes
    markdown: bytes
    summary: str

    @property
    def data(self) -> dict[str, Any]:
        return json.loads(self.plan)


class GitHub(Protocol):
    def open_pull(self, branch: str) -> dict[str, Any] | None: ...
    def file_at(self, ref: str, path: str) -> bytes | None: ...
    def branch_sha(self, branch: str) -> str | None: ...
    def commit_on(self, base_sha: str, files: Mapping[str, bytes], message: str) -> str: ...
    def set_branch(self, branch: str, sha: str) -> None: ...
    def delete_branch(self, branch: str) -> None: ...
    def create_pull(self, branch: str, title: str, body: str) -> dict[str, Any]: ...
    def update_pull(self, number: int, title: str, body: str) -> None: ...
    def close_pull(self, number: int) -> None: ...
    def comment(self, number: int, body: str) -> None: ...


# ------------------------------------------------------------------------------ the logic


def read_part(plan_dir: Path, part: Part) -> Built | None:
    here = plan_dir / part.subdir if part.subdir else plan_dir
    plan = here / "plan.json"
    if not plan.is_file():
        return None
    return Built(
        plan=plan.read_bytes(),
        markdown=(here / "PLAN.md").read_bytes(),
        summary=(here / "summary.md").read_text(encoding="utf-8"),
    )


def is_empty(data: Mapping[str, Any]) -> bool:
    """No change to make or look at: break-glass `never_touch` rows only say nothing happens."""
    return not any(c.get("action") not in INERT_ACTIONS for c in data.get("changes", []))


def title_for(part: Part, data: Mapping[str, Any]) -> str:
    if part is HELD:
        return f"Access plan, held part, for {data['as_of']} (two approvals)"
    return f"Access plan for {data['as_of']}"


def body_for(part: Part, built: Built, run_url: str) -> str:
    data = built.data
    summary = built.summary.strip()
    if len(summary) > BODY_LIMIT:
        summary = summary[:BODY_LIMIT].rstrip() + "\n\n_(cut to fit; the full plan is in PLAN.md)_"
    lines = [
        summary,
        "",
        "---",
        "",
        f"- This pull request holds the **{part.name}** part of the plan only: "
        f"`{part.plan_path}` and `{part.md_path}`.",
        f"- It needs {part.approvals} on the latest commit "
        f"(the plan file says {data['approvals_required']}; the approvals check recomputes it).",
        f"- Built for the tuning world's simulated month, as of {data['as_of']}, by {run_url}.",
        "- The bot force-pushes a new commit when the plan changes, which dismisses earlier "
        "approvals, and adds a comment saying what changed.",
    ]
    return "\n".join(lines) + "\n"


def _identities(data: Mapping[str, Any]) -> set[str]:
    return {canonical_json(c) for c in data.get("changes", [])}


def change_comment(old: Mapping[str, Any] | None, new: Mapping[str, Any], run_url: str) -> str:
    """What changed between the plan on the pull request and the new one, from the files."""
    if old is None:
        return f"The plan was rebuilt by {run_url}; the previous plan file could not be read."
    added = _identities(new) - _identities(old)
    removed = _identities(old) - _identities(new)
    before: Counter[str] = Counter(old.get("counts", {}).get("by_type", {}))
    after: Counter[str] = Counter(new.get("counts", {}).get("by_type", {}))
    out = [
        f"The plan changed ({run_url}): {len(added)} changes added, {len(removed)} removed.",
        "",
        "| Type | Before | After |",
        "|---|---|---|",
    ]
    for name in sorted(set(before) | set(after)):
        if before[name] != after[name]:
            out.append(f"| {name} | {before[name]} | {after[name]} |")
    out.append(f"| total | {old.get('counts', {}).get('total', 0)} | {new['counts']['total']} |")
    for key, label in (
        ("approvals_required", "Approvals required"),
        ("actual_state_sha256", "Actual-state hash"),
        ("policy_sha256", "Policy hash"),
        ("as_of", "As of"),
    ):
        if old.get(key) != new.get(key):
            was, now = old.get(key), new.get(key)
            if key.endswith("sha256"):
                was, now = str(was)[:12], str(now)[:12]
            out.append(f"\n{label}: {was} → {now}.")
    out.append("\nEarlier approvals were dismissed by the new commit; please review again.")
    return "\n".join(out) + "\n"


def _close(gh: GitHub, part: Part, pull: dict[str, Any], why: str) -> None:
    gh.comment(pull["number"], why)
    gh.close_pull(pull["number"])
    gh.delete_branch(part.branch)


def sync_part(gh: GitHub, part: Part, built: Built | None, run_url: str) -> str:
    """Bring one part's rolling pull request in line with the plan. Returns what was done."""
    pull = gh.open_pull(part.branch)
    if built is None or is_empty(built.data):
        if pull is None:
            return "empty, no pull request"
        _close(gh, part, pull, f"The {part.name} plan is empty now ({run_url}). Closing.")
        return f"closed #{pull['number']} (empty)"

    if gh.file_at(BASE, part.plan_path) == built.plan:
        if pull is None:
            return "already on main, no pull request"
        _close(gh, part, pull, f"This exact plan is already on `{BASE}` ({run_url}). Closing.")
        return f"closed #{pull['number']} (already on main)"

    current = gh.file_at(part.branch, part.plan_path) if pull is not None else None
    if pull is not None and current == built.plan:
        return f"unchanged #{pull['number']}"

    base_sha = gh.branch_sha(BASE)
    if base_sha is None:
        raise RuntimeError(f"branch {BASE} not found")
    data = built.data
    files = {part.plan_path: built.plan, part.md_path: built.markdown}
    sha = gh.commit_on(base_sha, files, f"access: {part.name} plan for {data['as_of']}")
    gh.set_branch(part.branch, sha)
    title, body = title_for(part, data), body_for(part, built, run_url)
    if pull is None:
        made = gh.create_pull(part.branch, title, body)
        return f"opened #{made['number']}"
    gh.update_pull(pull["number"], title, body)
    old = None
    if current is not None:
        try:
            old = json.loads(current)
        except ValueError:
            old = None
    gh.comment(pull["number"], change_comment(old, data, run_url))
    return f"updated #{pull['number']}"


# ------------------------------------------------------------------------------ REST client


Opener = Callable[[urllib.request.Request], Any]


class RestGitHub:
    """The few GitHub REST calls the bot needs. Retries `429` and `5xx` a few times."""

    def __init__(
        self,
        repo: str,
        token: str,
        api: str = API,
        opener: Opener | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.repo = repo
        self.owner = repo.split("/", 1)[0]
        self._token = token
        self._api = api.rstrip("/")
        self._open = opener or (lambda req: urllib.request.urlopen(req, timeout=60))  # noqa: S310
        self._sleep = sleep

    def _call(
        self, method: str, path: str, body: Any = None, raw: bool = False
    ) -> tuple[int, bytes]:
        url = f"{self._api}/repos/{self.repo}/{path}"
        data = None if body is None else json.dumps(body).encode()
        accept = "application/vnd.github.raw" if raw else "application/vnd.github+json"
        for attempt in range(4):
            req = urllib.request.Request(url, data=data, method=method)  # noqa: S310 - fixed host
            req.add_header("Authorization", f"Bearer {self._token}")
            req.add_header("Accept", accept)
            req.add_header("X-GitHub-Api-Version", "2022-11-28")
            req.add_header("User-Agent", "meridian-access-plan-bot")
            if data is not None:
                req.add_header("Content-Type", "application/json")
            try:
                with self._open(req) as resp:
                    return resp.status, resp.read()
            except urllib.error.HTTPError as err:
                if err.code == 404:
                    return 404, b""
                if (err.code == 429 or err.code >= 500) and attempt < 3:
                    wait = err.headers.get("Retry-After") if err.headers else None
                    self._sleep(float(wait) if wait and wait.isdigit() else 2.0 * (attempt + 1))
                    continue
                detail = err.read().decode("utf-8", "replace")[:500]
                raise RuntimeError(f"{method} {path}: HTTP {err.code}: {detail}") from err
        raise AssertionError("unreachable")

    def _json(self, method: str, path: str, body: Any = None) -> Any:
        status, payload = self._call(method, path, body)
        if status == 404:
            return None
        return json.loads(payload) if payload else None

    def open_pull(self, branch: str) -> dict[str, Any] | None:
        query = urllib.parse.urlencode(
            {"state": "open", "head": f"{self.owner}:{branch}", "base": BASE}
        )
        pulls = self._json("GET", f"pulls?{query}") or []
        return pulls[0] if pulls else None

    def file_at(self, ref: str, path: str) -> bytes | None:
        query = urllib.parse.urlencode({"ref": ref})
        status, payload = self._call("GET", f"contents/{path}?{query}", raw=True)
        return None if status == 404 else payload

    def branch_sha(self, branch: str) -> str | None:
        ref = self._json("GET", f"git/ref/heads/{branch}")
        return None if ref is None else ref["object"]["sha"]

    def commit_on(self, base_sha: str, files: Mapping[str, bytes], message: str) -> str:
        base_tree = self._json("GET", f"git/commits/{base_sha}")["tree"]["sha"]
        entries = []
        for path, content in files.items():
            blob = self._json(
                "POST", "git/blobs", {"content": content.decode("utf-8"), "encoding": "utf-8"}
            )
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        tree = self._json("POST", "git/trees", {"base_tree": base_tree, "tree": entries})
        commit = self._json(
            "POST", "git/commits", {"message": message, "tree": tree["sha"], "parents": [base_sha]}
        )
        return commit["sha"]

    def set_branch(self, branch: str, sha: str) -> None:
        if self.branch_sha(branch) is None:
            self._json("POST", "git/refs", {"ref": f"refs/heads/{branch}", "sha": sha})
        else:
            self._json("PATCH", f"git/refs/heads/{branch}", {"sha": sha, "force": True})

    def delete_branch(self, branch: str) -> None:
        self._call("DELETE", f"git/refs/heads/{branch}")

    def create_pull(self, branch: str, title: str, body: str) -> dict[str, Any]:
        return self._json(
            "POST",
            "pulls",
            {
                "title": title,
                "head": branch,
                "base": BASE,
                "body": body,
                "maintainer_can_modify": False,
            },
        )

    def update_pull(self, number: int, title: str, body: str) -> None:
        self._json("PATCH", f"pulls/{number}", {"title": title, "body": body})

    def close_pull(self, number: int) -> None:
        self._json("PATCH", f"pulls/{number}", {"state": "closed"})

    def comment(self, number: int, body: str) -> None:
        self._json("POST", f"issues/{number}/comments", {"body": body})


# ------------------------------------------------------------------------------- CLI


def run_url_from_env(env: Mapping[str, str]) -> str:
    server = env.get("GITHUB_SERVER_URL", "https://github.com")
    repo, run = env.get("GITHUB_REPOSITORY"), env.get("GITHUB_RUN_ID")
    if repo and run:
        return f"[run {run}]({server}/{repo}/actions/runs/{run})"
    return "a local run"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m meridian_access.plan_pr")
    parser.add_argument("--plan-dir", type=Path, required=True, help="the plan job's output")
    parser.add_argument("--repo", required=True, help="OWNER/NAME")
    args = parser.parse_args(argv)
    token = os.environ.get("GH_TOKEN", "")
    if not token:
        parser.error("GH_TOKEN is not set (the workflow passes the GitHub App's token)")
    gh = RestGitHub(args.repo, token)
    run_url = run_url_from_env(os.environ)
    for part in PARTS:
        print(f"{part.name} plan: {sync_part(gh, part, read_part(args.plan_dir, part), run_url)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
