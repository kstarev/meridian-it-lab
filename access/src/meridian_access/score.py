"""Scoring against the answer key. Only this module and tests may read the key.

`score_matching(result, owners)`: precision = correct / predicted (accounts given a person),
recall = correct / accounts that have an owner, and how many ownerless accounts were
correctly left without a person. Okta app-assignment accounts are excluded (the key's owner
list has none). Intervals are Wilson 95%.
"""

import argparse
import collections
import json
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from meridian_access.match import MatchResult
from meridian_access.policy.load import Policy, load_policy
from meridian_dataset.access.model import AccountOwner

START = "<!-- access-matching:start -->"
END = "<!-- access-matching:end -->"
DIRECTORY = ("okta", "google", "slack")

if TYPE_CHECKING:
    from meridian_access.plan import Plan


def wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a proportion; (0, 1) when there is nothing to measure."""
    if total == 0:
        return (0.0, 1.0)
    p = successes / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


@dataclass(frozen=True, slots=True)
class Rate:
    successes: int
    total: int

    @property
    def value(self) -> float:
        return self.successes / self.total if self.total else 0.0

    @property
    def interval(self) -> tuple[float, float]:
        return wilson(self.successes, self.total)

    def cell(self) -> str:
        lo, hi = self.interval
        return f"{self.value:.3f} ({self.successes}/{self.total}), 95% {lo:.3f} to {hi:.3f}"


@dataclass(frozen=True, slots=True)
class MatchScore:
    precision: Rate
    recall: Rate
    unowned_left_unmatched: Rate
    by_rule: dict[str, Rate]  # per rule: correct / predicted
    by_group: dict[str, tuple[Rate, Rate]]  # directory | exports: (precision, recall)
    not_in_key: int  # matched accounts the key doesn't list (besides Okta app assignments)


def _norm(system: str, account: str) -> tuple[str, str]:
    return (system, account.strip().casefold())


def score_matching(result: MatchResult, owners: Iterable[AccountOwner]) -> MatchScore:
    truth: dict[tuple[str, str], str] = {}
    for o in owners:
        truth[_norm(o.system, o.account)] = o.owner_employee_id
    predicted: dict[tuple[str, str], tuple[str, str]] = {}  # key -> (employee, rule)
    seen: set[tuple[str, str]] = set()
    not_in_key = 0
    for (system, app_id, account), m in result.matches.items():
        if system == "okta" and app_id:
            continue
        k = _norm(system, account)
        if k not in truth:
            not_in_key += 1
            continue
        predicted[k] = (m.employee_id, m.rule)
    for system, app_id, account in [*result.unmatched, *result.ambiguous]:
        if system == "okta" and app_id:
            continue
        seen.add(_norm(system, account))

    def groups(k: tuple[str, str]) -> str:
        return "directory" if k[0] in DIRECTORY else "exports"

    n_pred = correct = n_true = n_unowned = unowned_ok = 0
    rule_total: collections.Counter[str] = collections.Counter()
    rule_ok: collections.Counter[str] = collections.Counter()
    group_counts: dict[str, collections.Counter[str]] = {
        g: collections.Counter() for g in ("directory", "exports")
    }
    for k, owner in truth.items():
        g = group_counts[groups(k)]
        got, rule = predicted.get(k, ("", ""))
        if owner:
            n_true += 1
            g["true"] += 1
            if got:
                n_pred += 1
                g["pred"] += 1
                rule_total[rule] += 1
                if got == owner:
                    correct += 1
                    g["ok"] += 1
                    rule_ok[rule] += 1
        else:
            n_unowned += 1
            if not got:
                unowned_ok += 1
            else:  # a person given to an ownerless account is a wrong prediction
                n_pred += 1
                g["pred"] += 1
                rule_total[rule] += 1
    return MatchScore(
        precision=Rate(correct, n_pred),
        recall=Rate(correct, n_true),
        unowned_left_unmatched=Rate(unowned_ok, n_unowned),
        by_rule={r: Rate(rule_ok[r], rule_total[r]) for r in sorted(rule_total)},
        by_group={
            g: (Rate(c["ok"], c["pred"]), Rate(c["ok"], c["true"])) for g, c in group_counts.items()
        },
        not_in_key=not_in_key,
    )


def render_matching(score: MatchScore, title: str = "Rules only") -> str:
    lines = [
        f"| {title} | Result |",
        "|---|---|",
        f"| Precision (right person / given a person) | {score.precision.cell()} |",
        f"| Recall (right person / accounts with an owner) | {score.recall.cell()} |",
        f"| Ownerless accounts correctly left without a person | "
        f"{score.unowned_left_unmatched.cell()} |",
    ]
    for group, (p, r) in score.by_group.items():
        lines.append(f"| Precision, {group} | {p.cell()} |")
        lines.append(f"| Recall, {group} | {r.cell()} |")
    lines += ["", "| Rule | Right / given a person |", "|---|---|"]
    for rule, rate in score.by_rule.items():
        lines.append(f"| {rule} | {rate.successes}/{rate.total} |")
    return "\n".join(lines) + "\n"


def score_world(world_dir: Path, policy: Policy | None = None) -> MatchScore:
    """Collect and match one world directory, then score it against its answer key."""
    from meridian_access.collect import actual_state
    from meridian_access.desired import load_hris
    from meridian_access.match import match_accounts

    policy = policy or load_policy()
    records, _ = load_hris(world_dir / "hris.json")
    state = actual_state(world_dir, policy)
    result = match_accounts(state.accounts, records, policy)
    key = load_key(world_dir)
    owners = [AccountOwner(**o) for o in key["owners"]]
    return score_matching(result, owners)


PLAN_START = "<!-- access-plan-score:start -->"
PLAN_END = "<!-- access-plan-score:end -->"
FINDING_TYPES = (
    "leaver_access_left",
    "orphaned_account",
    "contractor_past_end",
    "leave_of_absence",
    "mover_stale_access",
    "rehire",
    "drift",
    "break_glass",
    "hris_mass_termination",
    "hr_data_error",
)


def _plan_target(system: str, app_id: str, account: str, employee_id: str) -> tuple[str, ...]:
    if system.strip().casefold() == "hris":
        return ("hris", employee_id.strip())
    return (system.strip().casefold(), app_id.strip().casefold(), account.strip().casefold())


def _plan_exact(item: dict) -> tuple:
    """(target, type, action, owner): the referee comparison's notion of the same finding."""
    hris = item["system"].strip().casefold() == "hris"
    return (
        _plan_target(item["system"], item["app_id"], item["account"], item["employee_id"]),
        item["type"],
        (item.get("action") or item.get("expected_action", "")) if not hris else "",
        "" if hris else item["employee_id"].strip(),
    )


@dataclass(frozen=True, slots=True)
class PlanTypeScore:
    precision: Rate
    recall: Rate
    predicted: int
    expected: int


def score_plan(plan: "Plan | dict", answer_key: dict) -> dict[str, PlanTypeScore]:
    """Per finding type: precision and recall of the plan's changes against the key's
    findings. A change matches a finding on target (system, app_id, normalised account), type,
    action and employee id; `hris` findings match on (type, employee id) only. Multiset
    matching: each finding is used once. The key is read here and in tests, never by the planner."""
    changes = plan.changes if hasattr(plan, "changes") else plan["changes"]
    findings = answer_key["findings"]
    out: dict[str, PlanTypeScore] = {}
    for type_ in FINDING_TYPES:
        pred = collections.Counter(_plan_exact(c) for c in changes if c["type"] == type_)
        true = collections.Counter(_plan_exact(f) for f in findings if f["type"] == type_)
        ok = sum((pred & true).values())
        n_pred, n_true = sum(pred.values()), sum(true.values())
        out[type_] = PlanTypeScore(Rate(ok, n_pred), Rate(ok, n_true), n_pred, n_true)
    total_ok = sum(s.precision.successes for s in out.values())
    n_pred = sum(s.predicted for s in out.values())
    n_true = sum(s.expected for s in out.values())
    out["all"] = PlanTypeScore(Rate(total_ok, n_pred), Rate(total_ok, n_true), n_pred, n_true)
    return out


def render_plan_score(scores: dict[str, PlanTypeScore]) -> str:
    lines = [
        "| Finding type | Predicted | In the key | Precision | Recall |",
        "|---|---|---|---|---|",
    ]
    for type_, s in scores.items():
        precision = s.precision.cell() if s.predicted else "n/a (none predicted)"
        recall = s.recall.cell() if s.expected else "n/a (none in the key)"
        lines.append(f"| {type_} | {s.predicted} | {s.expected} | {precision} | {recall} |")
    return "\n".join(lines) + "\n"


def plan_misses(plan: "Plan | dict", answer_key: dict, type_: str, limit: int = 15) -> dict:
    """The first few false positives and false negatives of one type (for tuning the rules)."""
    changes = plan.changes if hasattr(plan, "changes") else plan["changes"]
    pred = collections.Counter(_plan_exact(c) for c in changes if c["type"] == type_)
    true = collections.Counter(_plan_exact(f) for f in answer_key["findings"] if f["type"] == type_)
    return {
        "false_positive": sorted((pred - true).elements(), key=str)[:limit],
        "false_negative": sorted((true - pred).elements(), key=str)[:limit],
    }


def replace_plan_block(doc: str, body: str) -> str:
    if PLAN_START not in doc or PLAN_END not in doc:
        raise ValueError(f"the document needs {PLAN_START} and {PLAN_END}")
    head, rest = doc.split(PLAN_START, 1)
    _, tail = rest.split(PLAN_END, 1)
    return f"{head}{PLAN_START}\n{body}{PLAN_END}{tail}"


def read_plan_block(doc: str) -> str:
    m = re.search(re.escape(PLAN_START) + r"\n(.*?)" + re.escape(PLAN_END), doc, re.S)
    if not m:
        raise ValueError("no access-plan-score block")
    return m.group(1)


def score_plan_world(world_dir: Path, policy: Policy | None = None) -> dict[str, PlanTypeScore]:
    """Build the plan for a world directory (without its key), then score it."""
    import shutil
    import tempfile

    from meridian_access.plan import build_plan

    policy = policy or load_policy()
    key = load_key(world_dir)
    with tempfile.TemporaryDirectory() as tmp:  # the planner sees a copy without the key
        clean = Path(tmp) / "world"
        shutil.copytree(world_dir, clean, ignore=shutil.ignore_patterns("answer_key.json"))
        plan = build_plan(clean, policy)
    return score_plan(plan, key)


def load_key(world_dir: Path) -> dict:
    """The answer key of a world directory (scoring only)."""
    return json.loads((world_dir / "answer_key.json").read_text(encoding="utf-8"))


def score_titles(
    suggestions: dict[str, str], records: Iterable, true_roles: Iterable[tuple[str, str]]
) -> Rate:
    """Title -> role accuracy: a suggestion is right when it is the role most people with
    that title really hold. `suggestions` maps a title to a role id ("none" is a miss)."""
    role_of = dict(true_roles)
    holders: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    for r in records:
        if r.employee_id in role_of:
            holders[r.job_title][role_of[r.employee_id]] += 1
    right = sum(
        1
        for title, role in suggestions.items()
        if holders.get(title) and holders[title].most_common(1)[0][0] == role
    )
    return Rate(right, len(suggestions))


def replace_block(doc: str, body: str) -> str:
    """Put `body` between the markers of `doc`; ValueError if a marker is missing."""
    if START not in doc or END not in doc:
        raise ValueError(f"the document needs {START} and {END}")
    head, rest = doc.split(START, 1)
    _, tail = rest.split(END, 1)
    return f"{head}{START}\n{body}{END}{tail}"


def read_block(doc: str) -> str:
    m = re.search(re.escape(START) + r"\n(.*?)" + re.escape(END), doc, re.S)
    if not m:
        raise ValueError("no access-matching block")
    return m.group(1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m meridian_access.score")
    sub = parser.add_subparsers(dest="what", required=True)
    m = sub.add_parser("matching", help="score identity matching on a tuning world directory")
    m.add_argument("--world", type=Path, required=True)
    m.add_argument("--write-doc", type=Path, help="refresh the marked block in this file")
    p = sub.add_parser("plan", help="score the plan on a tuning world directory")
    p.add_argument("--world", type=Path, required=True)
    p.add_argument("--write-doc", type=Path, help="refresh the marked block in this file")
    p.add_argument("--misses", help="print the misses of one finding type")
    args = parser.parse_args(argv)
    if args.what == "plan":
        scores = score_plan_world(args.world)
        body = render_plan_score(scores)
        print(body, end="")
        if args.misses:
            from meridian_access.plan import build_plan

            print(
                plan_misses(
                    build_plan(args.world, load_policy()), load_key(args.world), args.misses
                )
            )
        if args.write_doc:
            args.write_doc.write_text(
                replace_plan_block(args.write_doc.read_text(encoding="utf-8"), body),
                encoding="utf-8",
            )
        return 0
    body = render_matching(score_world(args.world))
    print(body, end="")
    if args.write_doc:
        args.write_doc.write_text(
            replace_block(args.write_doc.read_text(encoding="utf-8"), body), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
