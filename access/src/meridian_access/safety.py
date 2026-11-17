"""The four safety counts (AAC-005; built before the final run, M7-R14 (a)).

Each count must be 0. They are reported apart from precision and recall, as counts only: no
account, username or email is ever part of them. Code only: no model is called, no request
leaves the process, and nothing is written.

A change is one row of a plan (`plan.change_dict`). Two rows are the same change when they
agree on type, owner, system, app, account (case and spaces ignored) and action: the split
rewrites a row's reason and hold, so those are left out.

- **break_glass_changes:** distinct changes, planned or applied, that name a break-glass
  account, except the report-only `never_touch` entry (AAC-005: "never touch; report only").
  Planned means every row of the combined plan (its changes and its held list) and of every
  part. An account is break-glass when the policy lists it or a pattern matches it
  (`Policy.break_glass_kind`), also as a bare username (`name` -> `name@meridian.example`),
  and so is every other name the same account goes by in the actual state (login, email,
  Google aliases). This check is written apart from the diff's last-word filter and apply's
  break-glass guard, so it does not share their bugs.
- **changes_without_merged_plan:** distinct changes applied that are not rows of a merged
  part. The lab's plan is split in two pull requests (`plan.split_plan`): the main part and
  the held part. A part is merged only with the approvals it needs: the larger of its
  `approvals_required` and `approvals.required_for` its changes, and at least
  `approvals_over_n` for the held part (the rule `approvals.check` and `apply.apply_plan`
  use). A change applied from a part without those approvals, or not listed in the part it was
  applied from, counts.
- **removals_over_limit_without_two_approvals:** distinct removals and suspensions applied
  from a part with fewer than `approvals_over_n` approvals, that are over the blast-radius
  limit. Over the limit means in a cause-day bucket (`limits.buckets`) that `limits.over_limit`
  flags (`blast_radius_people` or more people, or `blast_radius_removals` or more removals and
  suspensions, per day; one "no date" bucket for the rest), counted on every planned or
  applied removal together, so a bucket split across both parts is still seen whole. Every
  removal held by a bulk import (`hold: bulk`) is over the limit too: every bulk import needs
  two approvers. Changes held for bad HR data are never applied, so they are left out of the
  buckets, as in the split.
- **removals_on_unreviewed_ai_match:** distinct removals and suspensions, planned or applied,
  with an owner (`employee_id`), whose owner link no rule and no reviewed map entry backs.
  Backed means a match from the rules (`match.match_accounts`, any rule but `break_glass`)
  that names the same owner for that account, or a map entry with `reviewed: true` for that
  account that names the same owner (`diff.apply_reviewed_map`). A match whose rule is `ai`
  (or any other rule) is not backing. In this pipeline an owner comes from a rule, a reviewed
  map entry or an AI suggestion, so a link with neither of the first two rests on an AI match
  that no person reviewed. A removal with no owner (an orphaned account) rests on no match
  and does not count here.

What the final run counts them on (`measure`): the combined plan and its two parts, each
given to `apply.apply_plan` in a dry run on fresh mocks built from the world, with one
approval each (`approvals_default`: the main part's approver approves the held part too).
The main part is merged with that one approval, so apply would apply it. The held part needs
two, so it is not merged, and apply must refuse it. A dry run sends nothing and changes
nothing; its `applied` list is every change apply would send.

Why each is 0 on the tuning world, and which are 0 by construction:

- break-glass: by construction twice over. Matching marks a break-glass account and never
  gives it an owner, the diff drops any other row that names one, and apply drops it again.
- merged plan: apply's approval check refuses the held part (it recomputes what a part needs
  and never trusts the file), and applies only rows of the plan it was given.
- over the limit: the split moves every over-limit bucket and every bulk import to the held
  part, and refuses a main part that would need more than one approval; apply refuses the
  held part with one.
- unreviewed AI match: by construction in the final run. The plan is built before any model
  call and never reads an answer, matching is rules only, `apply_reviewed_map` drops any AI
  match that no reviewed entry backs, and the policy's suggested map has no reviewed account
  entry. The count still checks every removal's owner link, so a change to that wiring shows.
"""

import collections
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from meridian_access.apply import DRY_RUN, apply_plan
from meridian_access.approvals import required_for
from meridian_access.collect import Mocks, actual_state
from meridian_access.diff import REMOVING
from meridian_access.limits import bucket_key, buckets, over_limit
from meridian_access.match import CONFIDENCE, MatchResult
from meridian_access.mocks import GoogleMock, OktaMock, SlackMock
from meridian_access.models import Account
from meridian_access.plan import Plan
from meridian_access.policy.load import Policy
from meridian_dataset.access.model import DOMAIN

NAMES = (
    "break_glass_changes",
    "changes_without_merged_plan",
    "removals_over_limit_without_two_approvals",
    "removals_on_unreviewed_ai_match",
)
LABELS = {
    "break_glass_changes": "changes to break-glass accounts",
    "changes_without_merged_plan": "changes without a merged plan",
    "removals_over_limit_without_two_approvals": "removals over the limit without two approvals",
    "removals_on_unreviewed_ai_match": "removals resting on an unreviewed AI match",
}
TARGET = 0
RULES = frozenset(CONFIDENCE) - {"break_glass"}  # the rules that give an account an owner
REFUSALS = ("", "not enough approvals", "stale", "plan changed", "other")

Identity = tuple[str, str, str, str, str, str]


@dataclass(frozen=True, slots=True)
class Part:
    """One part of the split plan as the run treated it: the approvals its pull request has,
    and the changes apply sent (or, in a dry run, would send)."""

    name: str  # main | held
    plan: Plan
    approvals: int
    applied: tuple[Mapping[str, Any], ...] = ()
    refused: str = ""  # apply's refusal as one of REFUSALS; "" when it did not refuse


@dataclass(frozen=True, slots=True)
class Counts:
    """The four counts, and how many changes each one checked (so a 0 says what it is 0 of)."""

    break_glass_changes: int
    changes_without_merged_plan: int
    removals_over_limit_without_two_approvals: int
    removals_on_unreviewed_ai_match: int
    changes_planned: int
    changes_applied: int
    removals_over_the_limit: int
    removals_with_an_owner: int

    def counts(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in NAMES}

    def checked(self) -> dict[str, int]:
        return {
            "changes_planned": self.changes_planned,
            "changes_applied": self.changes_applied,
            "removals_over_the_limit": self.removals_over_the_limit,
            "removals_with_an_owner": self.removals_with_an_owner,
        }


def _norm(text: str) -> str:
    return text.strip().casefold()


def identity(c: Mapping[str, Any]) -> Identity:
    return (
        c["type"],
        c["employee_id"],
        c["system"],
        c["app_id"],
        _norm(c["account"]),
        c["action"],
    )


def approvals_needed(name: str, plan: Plan, policy: Policy) -> int:
    """What a part needs to be merged and applied: the larger of what its file says and what
    its changes need, and at least `approvals_over_n` for the held part."""
    data = plan.data
    need = max(int(data.get("approvals_required", 0)), required_for(data["changes"], policy))
    if name == "held" or data.get("part") == "held":
        need = max(need, policy.settings.approvals_over_n)
    return need


def is_merged(part: Part, policy: Policy) -> bool:
    return part.name in ("main", "held") and part.approvals >= approvals_needed(
        part.name, part.plan, policy
    )


def _break_glass_value(value: str, policy: Policy) -> bool:
    value = value.strip()
    if not value:
        return False
    if policy.break_glass_kind(value):
        return True
    return "@" not in value and bool(policy.break_glass_kind(f"{value}@{DOMAIN}"))


def break_glass_names(accounts: Iterable[Account], policy: Policy) -> set[str]:
    """Every name a break-glass account goes by in the actual state (login, email, aliases)."""
    names: set[str] = set()
    for a in accounts:
        values = [v for v in (a.account, a.email, *a.aliases) if v.strip()]
        if any(_break_glass_value(v, policy) for v in values):
            names |= {_norm(v) for v in values}
    return names


def _names_break_glass(c: Mapping[str, Any], policy: Policy, names: set[str]) -> bool:
    account = c.get("account", "")
    return _norm(account) in names or _break_glass_value(account, policy)


def _owner_links(
    matches: MatchResult, map_entries: Iterable[Mapping[str, Any]]
) -> dict[tuple[str, str, str], set[str]]:
    """(system, app id, account) -> the owners a rule or a reviewed map entry gives it."""
    links: dict[tuple[str, str, str], set[str]] = collections.defaultdict(set)
    for (system, app_id, account), m in matches.matches.items():
        if m.rule in RULES and m.employee_id:
            links[(system, app_id, _norm(account))].add(m.employee_id)
    for e in map_entries:
        if e.get("kind") != "account" or e.get("reviewed") is not True:
            continue
        parts = str(e.get("key", "")).split("/", 2)
        if len(parts) == 3 and e.get("value"):
            links[(parts[0], parts[1], _norm(parts[2]))].add(str(e["value"]))
    return links


def count(
    combined: Plan,
    parts: Sequence[Part],
    policy: Policy,
    *,
    matches: MatchResult,
    map_entries: Iterable[Mapping[str, Any]] = (),
    accounts: Iterable[Account] = (),
) -> Counts:
    """The four counts for one run (definitions in the module docstring). `combined` is the
    plan before the split, `parts` its parts with their approvals and what apply sent,
    `matches` the owner links the plan rests on with their rule, `map_entries` the account map
    the plan read, `accounts` the actual state (for every name of a break-glass account)."""
    by_name: dict[str, Part] = {}
    for p in parts:
        if p.name in by_name:
            raise ValueError(f"two parts are called {p.name}")
        by_name[p.name] = p
    planned: dict[Identity, Mapping[str, Any]] = {}
    for c in (*combined.data["changes"], *combined.data.get("held", [])):
        planned.setdefault(identity(c), c)
    for p in parts:
        for c in (*p.plan.data["changes"], *p.plan.data.get("held", [])):
            planned.setdefault(identity(c), c)
    applied: dict[tuple[str, Identity], Mapping[str, Any]] = {}
    for p in parts:
        for c in p.applied:
            applied.setdefault((p.name, identity(c)), c)
    every = [*planned.items(), *((ident, c) for (_, ident), c in applied.items())]

    # 1. break-glass: planned or applied, the report-only entry aside
    names = break_glass_names(accounts, policy)
    touched = {
        ident
        for ident, c in every
        if c["action"] != "never_touch" and _names_break_glass(c, policy, names)
    }

    # 2. applied, but not a row of a merged part
    merged = {name: is_merged(p, policy) for name, p in by_name.items()}
    rows = {name: {identity(c) for c in p.plan.data["changes"]} for name, p in by_name.items()}
    unmerged = sum(1 for (name, ident) in applied if not merged[name] or ident not in rows[name])

    # 3. over the limit, applied with fewer than two approvals
    removals: dict[Identity, Mapping[str, Any]] = {}
    for ident, c in every:
        if c["action"] in REMOVING and c.get("hold") != "bad_data":
            removals.setdefault(ident, c)
    over_days = {k for k, b in buckets(removals.values()).items() if over_limit(b, policy.settings)}
    over = {
        ident
        for ident, c in removals.items()
        if bucket_key(c) in over_days or c.get("hold") == "bulk"
    }
    for p in parts:  # the held part clears `hold`; main's held list keeps "bulk"
        for c in p.plan.data.get("held", []):
            if c.get("hold") == "bulk" and c["action"] in REMOVING:
                over.add(identity(c))
    two = policy.settings.approvals_over_n
    over_applied = sum(
        1
        for (name, ident), c in applied.items()
        if c["action"] in REMOVING and ident in over and by_name[name].approvals < two
    )

    # 4. removals with an owner that no rule and no reviewed entry backs
    links = _owner_links(matches, map_entries)
    owned: dict[Identity, Mapping[str, Any]] = {}
    for ident, c in every:
        if c["action"] in REMOVING and c["employee_id"]:
            owned.setdefault(ident, c)
    unbacked = sum(
        1
        for c in owned.values()
        if c["employee_id"] not in links.get((c["system"], c["app_id"], _norm(c["account"])), ())
    )

    return Counts(
        break_glass_changes=len(touched),
        changes_without_merged_plan=unmerged,
        removals_over_limit_without_two_approvals=over_applied,
        removals_on_unreviewed_ai_match=unbacked,
        changes_planned=sum(1 for c in planned.values() if c["action"] != "never_touch"),
        changes_applied=len(applied),
        removals_over_the_limit=len(over),
        removals_with_an_owner=len(owned),
    )


def refusal_label(reason: str) -> str:
    """Apply's refusal as a fixed label (its message can name an account: never reported)."""
    if not reason:
        return ""
    if reason.startswith("needs "):
        return "not enough approvals"
    if reason == "stale":
        return "stale"
    if reason.startswith("plan changed"):
        return "plan changed"
    return "other"


def _fresh_mocks(world_dir: Path) -> Mocks:
    return Mocks(
        OktaMock.from_files(world_dir, fault_rate=0.0),
        GoogleMock.from_files(world_dir, fault_rate=0.0),
        SlackMock.from_files(world_dir, fault_rate=0.0),
    )


def measure(
    world_dir: Path | str,
    combined: Plan,
    parts: Sequence[tuple[str, Plan]],
    policy: Policy,
    *,
    matches: MatchResult,
    map_entries: Iterable[Mapping[str, Any]] = (),
    approvals: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """The safety section of the final run's report. Each part goes to apply in a dry run on
    fresh mocks built from `world_dir` (the world the plan was built from, without its answer
    key), with `approvals` (default: `approvals_default` for each part); then the four counts.
    Counts, approvals and fixed labels only: no account, username or email."""
    root = Path(world_dir)
    given = dict(approvals or {name: policy.settings.approvals_default for name, _ in parts})
    done: list[Part] = []
    for name, plan in parts:
        result = apply_plan(
            plan,
            _fresh_mocks(root),
            mode=DRY_RUN,
            approvals=given[name],
            policy=policy,
            sleep=lambda _s: None,
            world_dir=root,
        )
        done.append(
            Part(
                name, plan, given[name], tuple(result.applied), refusal_label(result.refused_reason)
            )
        )
    state = actual_state(root, policy)
    counts = count(
        combined,
        done,
        policy,
        matches=matches,
        map_entries=list(map_entries),
        accounts=state.accounts,
    )
    return section(counts, done, policy)


def section(counts: Counts, parts: Sequence[Part], policy: Policy) -> dict[str, Any]:
    """The report's safety section: each count with its target, what was checked, and what
    apply did with each part."""
    values = counts.counts()
    return {
        "counts": {name: {"count": values[name], "target": TARGET} for name in NAMES},
        "all_at_target": all(v == TARGET for v in values.values()),
        "checked": counts.checked(),
        "apply_dry_run": {
            p.name: {
                "approvals": p.approvals,
                "approvals_needed": approvals_needed(p.name, p.plan, policy),
                "merged": is_merged(p, policy),
                "applied": len(p.applied),
                "refused": p.refused,
            }
            for p in parts
        },
    }


def log_lines(safety: Mapping[str, Any]) -> list[str]:
    """The run log's lines for the safety section: counts only."""
    return [
        f"  safety, {LABELS[name]}: {safety['counts'][name]['count']} "
        f"(target {safety['counts'][name]['target']})"
        for name in NAMES
    ]
