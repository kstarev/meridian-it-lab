"""Compare Haiku, Sonnet and Opus 5.5 on the tuning world (AAC-006): a dry run that prices
the comparison and makes no call, and a live run behind a guard.

The calls are: one job-title suggestion per distinct title, and one owner suggestion per
account the rules left unmatched. The dry run builds exactly those prompts and adds up each
call's worst-case cost. A live run needs ACCESS_AI_LIVE=1 and an API key, and writes
`out/access-ai-compare.json`. It runs only from the approved GitHub workflow.
"""

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from meridian_access.ai import maps
from meridian_access.ai.client import DEFAULT_MAX_TOKENS, INPUT_OVERHEAD_TOKENS, ClaudeClient
from meridian_access.ai.matching import SYSTEM as MATCH_SYSTEM
from meridian_access.ai.matching import (
    MatchSuggestion,
    match_prompt,
    match_schema,
    shortlist,
    suggest_owner,
)
from meridian_access.ai.pricing import worst_case_usd
from meridian_access.ai.spend import SpendCapReached, SpendLedger, repo_root
from meridian_access.ai.titles import SYSTEM as TITLE_SYSTEM
from meridian_access.ai.titles import suggest_role, title_prompt, title_schema
from meridian_access.collect import actual_state
from meridian_access.desired import desired_state, load_hris
from meridian_access.match import Match, MatchResult, match_accounts
from meridian_access.models import Account
from meridian_access.policy.load import Policy, load_policy
from meridian_access.score import load_key, score_matching, score_titles
from meridian_dataset.access.model import AccountOwner, HrisRecord

TUNING_SEED = 6108
REPORT_PATH = repo_root() / "out" / "access-ai-compare.json"


@dataclass(frozen=True, slots=True)
class Call:
    kind: str  # title | owner
    system: str
    user: str
    schema: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Workload:
    titles: tuple[tuple[str, str, str], ...]  # (title, department, team)
    leftovers: tuple[Account, ...]
    records: tuple[HrisRecord, ...]
    roles: tuple[str, ...]
    result: MatchResult


def build_workload(world_dir: Path, policy: Policy) -> Workload:
    """What would be sent: distinct titles, and the accounts the rules leave unmatched."""
    records, _ = load_hris(world_dir / "hris.json")
    state = actual_state(world_dir, policy)
    result = match_accounts(state.accounts, records, policy)
    left = {*result.unmatched, *result.ambiguous}
    leftovers = tuple(
        a for a in state.accounts if a.key in left and not (a.system == "okta" and a.app_id)
    )
    first: dict[str, HrisRecord] = {}
    superseded = {
        p.employee_id
        for p in desired_state(records, (), policy).people
        if p.category == "superseded"
    }
    current = tuple(r for r in records if r.employee_id not in superseded)  # shortlists
    for r in sorted(current, key=lambda r: r.employee_id):
        first.setdefault(r.job_title, r)
    titles = tuple((t, r.department, r.team) for t, r in sorted(first.items()))
    return Workload(titles, leftovers, current, tuple(sorted(policy.roles)), result)


def build_calls(work: Workload) -> list[Call]:
    calls = [
        Call(
            "title",
            TITLE_SYSTEM,
            title_prompt(t, d, tm, work.roles),
            title_schema(work.roles),
        )
        for t, d, tm in work.titles
    ]
    for a in work.leftovers:
        cands = shortlist(a, work.records)
        calls.append(
            Call(
                "owner",
                MATCH_SYSTEM,
                match_prompt(a, cands),
                match_schema([c.employee_id for c in cands]),
            )
        )
    return calls


def worst_case_for(model: str, call: Call) -> float:
    size = len(json.dumps([call.system, call.user, call.schema]).encode("utf-8"))
    return worst_case_usd(model, size + INPUT_OVERHEAD_TOKENS, DEFAULT_MAX_TOKENS)


def dry_run(models: list[str], world_dir: Path, policy: Policy | None = None) -> dict[str, Any]:
    """Prices the comparison; makes no call and needs no key."""
    policy = policy or load_policy()
    calls = build_calls(build_workload(world_dir, policy))
    per_model = {m: round(sum(worst_case_for(m, c) for c in calls), 6) for m in models}
    return {
        "calls_per_model": len(calls),
        "title_calls": sum(c.kind == "title" for c in calls),
        "owner_calls": sum(c.kind == "owner" for c in calls),
        "worst_case_usd": per_model,
        "worst_case_total_usd": round(sum(per_model.values()), 6),
        "calls_made": 0,
    }


def live_run(
    models: list[str],
    world_dir: Path,
    policy: Policy | None = None,
    *,
    client_factory: Callable[[str, SpendLedger], ClaudeClient] | None = None,
    ledger: SpendLedger | None = None,
    suggestions_path: Path | None = None,
) -> dict[str, Any]:
    """Runs every model over the workload and scores it against the tuning key. Stops a
    model (and records why) when the spend cap or run limit would be crossed."""
    if os.environ.get("ACCESS_AI_LIVE") != "1":
        raise RuntimeError("a live run needs ACCESS_AI_LIVE=1")
    seed = json.loads((world_dir / "hris.json").read_text(encoding="utf-8")).get("seed")
    if seed != TUNING_SEED:
        raise RuntimeError(f"a live run is for the tuning world (seed {TUNING_SEED}), not {seed!r}")
    policy = policy or load_policy()
    ledger = ledger or SpendLedger()
    factory = client_factory or (lambda m, led: ClaudeClient(m, ledger=led))
    work = build_workload(world_dir, policy)
    key = load_key(world_dir)
    owners = [AccountOwner(**o) for o in key["owners"]]
    dept = {r.employee_id: r for r in work.records}
    report: dict[str, Any] = {"world": world_dir.name, "models": {}}
    entries: list[dict[str, Any]] = []
    for model in models:
        client = factory(model, ledger)
        before = ledger.total_usd()
        titles: dict[str, str] = {}
        failed = 0
        stopped = ""
        owner_of: dict[tuple[str, str, str], MatchSuggestion] = {}
        try:
            for title, department, team in work.titles:
                s = suggest_role(title, department, team, work.roles, client)
                if s is None:
                    failed += 1
                else:
                    titles[title] = s.role
                    if e := maps.title_entry(title, s):
                        entries.append(e)
            for a in work.leftovers:
                m = suggest_owner(a, shortlist(a, work.records), client)
                if m is None:
                    failed += 1
                else:
                    owner_of[a.key] = m
                    if e := maps.account_entry(a.system, a.app_id, a.account, m):
                        entries.append(e)
        except SpendCapReached as exc:
            stopped = str(exc)
        combined = dict(work.result.matches)
        for k, m in owner_of.items():
            if m.employee_id != "none" and m.employee_id in dept:
                combined[k] = Match(m.employee_id, "ai", 0.5)
        rules_only = score_matching(work.result, owners)
        with_ai = score_matching(
            MatchResult(
                combined,
                [k for k in work.result.unmatched if k not in combined],
                [k for k in work.result.ambiguous if k not in combined],
            ),
            owners,
        )
        title_rate = score_titles(titles, work.records, key["true_roles"])
        report["models"][model] = {
            "calls": client.calls,
            "failed_or_refused": failed,
            "cost_usd": round(ledger.total_usd() - before, 6),
            "stopped": stopped,
            "title_accuracy": [title_rate.successes, title_rate.total],
            "matching_rules_only": [rules_only.precision.value, rules_only.recall.value],
            "matching_rules_plus_ai": [with_ai.precision.value, with_ai.recall.value],
        }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if suggestions_path is not None:
        maps.write_entries(entries, suggestions_path)
    return report
