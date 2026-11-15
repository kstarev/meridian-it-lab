"""The approvals check for a plan pull request (AAC-007).

A plan needs `approvals_required` approvals from code owners. Only a code owner's latest
review counts, only if it is an approval, and only if it was made on the current head commit
of the pull request, so a push after an approval makes the approval stale. Apply checks again
with the same function, because a required check is only as good as the ruleset behind it.

    python -m meridian_access.approvals --plan plan.json --reviews reviews.json \\
        --plan-path access/plan/plan.json \\
        --codeowners kstarev --head-sha SHA --author PR_AUTHOR --policy-dir access/policy

The number of approvals a plan needs is recomputed from its changes and the policy the check
loads itself (in the workflow: from the base branch). The number written in the plan file is
never trusted below that: the requirement is the larger of the two. The author of the pull
request cannot approve their own plan. The rule itself is in `required_for`.

A plan file is one part of the split plan: `part` is "main" (file `access/plan/plan.json`) or
"held" (file `access/plan/held/plan.json`). The part must be one of the two and the path the
plan was read from (`--plan-path`) must be the one for its part, so a held plan cannot be
passed off as a main plan by relabelling it or by moving the file. The held part always needs
`approvals_over_n` approvals, whatever the file says.

`reviews.json` has the shape of GitHub's `GET /repos/{o}/{r}/pulls/{n}/reviews`. The exit code
is 1 when there are not enough approvals.
"""

import argparse
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from meridian_access.limits import buckets, over_limit
from meridian_access.plan import PLAN_VERSION, Plan
from meridian_access.policy.load import Policy, load_policy

PART_PATHS = {"main": "access/plan/plan.json", "held": "access/plan/held/plan.json"}

# These review states change what a reviewer currently says; a plain comment does not.
STATES = {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}


def version_message(found: Any) -> str:
    return f"plan version {found}, expected {PLAN_VERSION}: build the plan again"


def _login(name: str) -> str:
    return name.strip().lstrip("@").casefold()


def required_for(changes: Iterable[dict[str, Any]], policy: Policy) -> int:
    """Approvals a set of changes needs: `approvals_over_n` when any cause-day bucket is over the
    limit (see `limits`: `blast_radius_people` or more distinct people, or
    `blast_radius_removals` or more removals and suspensions, per cause day; removals with no
    cause day form one "no-date" bucket) or there is a `block_two_approvers` change, else
    `approvals_default`. There is no plan-total limit: a plan with many small days needs one.

    Raises ValueError when a removal or suspension has no `cause_date` key, or one that is not
    "" or a real YYYY-MM-DD string: such a plan is malformed (made by an older version or
    edited), and guessing 1 would be silent."""
    rows = list(changes)
    settings = policy.settings
    if any(c["action"] == "block_two_approvers" for c in rows):
        return settings.approvals_over_n
    if any(over_limit(b, settings) for b in buckets(rows).values()):
        return settings.approvals_over_n
    return settings.approvals_default


def count_approvals(
    reviews: list[dict[str, Any]], codeowners: set[str], head_sha: str, author: str = ""
) -> int:
    """Distinct code-owner logins whose latest review is an approval made on `head_sha`.
    The pull request's `author` never counts (nobody approves their own plan)."""
    owners = {_login(o) for o in codeowners} - ({_login(author)} if author.strip() else set())
    latest: dict[str, dict[str, Any]] = {}
    ordered = sorted(
        enumerate(reviews), key=lambda ir: (str(ir[1].get("submitted_at") or ""), ir[0])
    )
    for _, review in ordered:
        user = (review.get("user") or {}).get("login", "")
        if not user or str(review.get("state", "")).upper() not in STATES:
            continue
        latest[_login(user)] = review
    return sum(
        1
        for user, review in latest.items()
        if user in owners
        and str(review.get("state", "")).upper() == "APPROVED"
        and review.get("commit_id") == head_sha
    )


def check(
    plan: Plan | dict[str, Any],
    reviews: list[dict[str, Any]],
    codeowners: set[str],
    head_sha: str,
    *,
    policy: Policy,
    plan_path: str,
    author: str = "",
) -> tuple[bool, str]:
    """`plan_path` is where the plan file sits in the repository (`PART_PATHS`)."""
    data = plan.data if isinstance(plan, Plan) else plan
    if data.get("version") != PLAN_VERSION:
        return False, version_message(data.get("version"))
    part = data.get("part")
    if part not in PART_PATHS:
        return False, f"unknown plan part {part!r}: expected main or held"
    if str(plan_path).removeprefix("./") != PART_PATHS[part]:
        return False, (
            f"the plan says it is the {part} part but was read from {plan_path}, "
            f"expected {PART_PATHS[part]}"
        )
    try:
        need = max(int(data["approvals_required"]), required_for(data["changes"], policy))
        if part == "held":
            need = max(need, policy.settings.approvals_over_n)
    except ValueError as exc:
        return False, f"cannot work out the approvals the plan needs: {exc}"
    have = count_approvals(reviews, codeowners, head_sha, author)
    if have >= need:
        return True, f"{have} of {need} required code-owner approvals on {head_sha[:12]}"
    return False, f"needs {need} approvals, has {have} (code owners, on the latest commit)"


def parse_codeowners(text: str, path_prefix: str = "/access/plan/") -> set[str]:
    """Logins named on the CODEOWNERS line for `path_prefix` (teams and emails are skipped)."""
    owners: set[str] = set()
    for line in text.splitlines():
        parts = line.split("#", 1)[0].split()
        if parts and parts[0] == path_prefix:
            owners |= {p.lstrip("@") for p in parts[1:] if p.startswith("@") and "/" not in p}
    return owners


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m meridian_access.approvals")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument(
        "--plan-path", required=True, help="where the plan file is in the repository"
    )
    parser.add_argument("--reviews", type=Path, required=True)
    parser.add_argument("--codeowners", default="", help="comma-separated logins")
    parser.add_argument("--codeowners-file", type=Path, default=None)
    parser.add_argument("--codeowners-path", default="/access/plan/", help="CODEOWNERS line to use")
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--author", default="", help="the pull request author (never counts)")
    parser.add_argument("--policy-dir", type=Path, default=None, help="default: access/policy")
    args = parser.parse_args(argv)
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    reviews = json.loads(args.reviews.read_text(encoding="utf-8"))
    owners: Iterable[str] = [o for o in args.codeowners.split(",") if o.strip()]
    owners = set(owners)
    if args.codeowners_file:
        owners |= parse_codeowners(
            args.codeowners_file.read_text(encoding="utf-8"), args.codeowners_path
        )
    ok, message = check(
        plan,
        reviews,
        owners,
        args.head_sha,
        policy=load_policy(args.policy_dir),
        plan_path=args.plan_path,
        author=args.author,
    )
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
