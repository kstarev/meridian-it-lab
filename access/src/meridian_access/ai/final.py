"""Milestone 7: the final held-out score, run once, with the frozen prompts and the chosen model
(AAC-005, AAC-006, AAC-010; budget item `aac-m7-final-score`, ADR-037).

Two commands (`python -m meridian_access.ai ...`):

- `final-check --world DIR` is the dry check. It takes the tuning world only. It runs the
  final run's calls through the frozen client with a fake SDK client that records each request
  and answers "none", then prints the number of calls, the upper cost at the chosen model's
  rates and the prompt hashes. No request leaves the process, no key is needed, nothing is
  spent and no ledger is written.
- `final --final-score held_out_a1 --out DIR` is the paid run. It refuses, before the sealed
  world is built, unless: the flag names `held_out_a1` (the dataset's only way to unseal it),
  the prompts and the model match the pins in `frozen.py`, `ACCESS_AI_LIVE=1`, `--out` is
  outside the repository, and the budget item `aac-m7-final-score` is approved and its paid
  window is open. The run is charged to that item by code, whatever `ACCESS_CLAUDE_BUDGET_ITEM`
  says (a different value refuses the run). Then it builds the world on the runner, rehearses
  the whole run with the fake client (calls, scoring and files, thrown away), and refuses if the
  worst case of all repeats would cross the item's cap, the project's cap or the run limit. Only
  then does it make the calls.

The output (`report.json`, `details.json`, `rulings-template.md`) holds held-out numbers and,
in the details, held-out content. It is not for publishing until every disagreement has the
owner's ruling (AAC-010). `report.json` also holds the four safety counts (AAC-005, M7-R14),
each with target 0, apart from the scores (`meridian_access.safety`). The command prints
counts only.
"""

import json
import os
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from meridian_access import safety, summary
from meridian_access.ai import compare, frozen
from meridian_access.ai.client import ClaudeClient, worst_case_input_tokens
from meridian_access.ai.maps import load_entries
from meridian_access.ai.matching import MatchSuggestion, shortlist, suggest_owner
from meridian_access.ai.pricing import PRICES_PER_MTOK, worst_case_usd
from meridian_access.ai.spend import ITEM_ENV, SpendCapReached, SpendLedger, repo_root
from meridian_access.ai.titles import suggest_role
from meridian_access.match import Match, MatchResult
from meridian_access.plan import Plan, build_plan, split_plan
from meridian_access.policy.load import Policy, load_policy
from meridian_access.score import (
    Rate,
    load_key,
    matching_disagreements,
    plan_disagreements,
    score_matching,
    score_plan,
    score_titles,
    title_disagreements,
)
from meridian_dataset.access.model import AccountOwner

FINAL_SCORE = "held_out_a1"  # the name the dataset unseals it by (AAC-005)
BUDGET_ITEM = "aac-m7-final-score"
MAX_K = 3  # ADR-037 priced the item at up to three repeats
STATUS = "unruled: not for publishing until every disagreement has the owner's ruling (AAC-010)"


# ------------------------------------------------------------------------------ inputs


@dataclass(frozen=True, slots=True)
class Inputs:
    """What the run asks about and scores, built from a world without its answer key."""

    work: compare.Workload
    plan: Plan  # the combined plan, scored against the key
    parts: tuple[tuple[str, Plan], ...]  # (main | held, part): one summary each
    unmapped_titles: frozenset[str]  # titles the reviewed title map doesn't cover


def _copy_without_key(world_dir: Path, target: Path) -> Path:
    shutil.copytree(world_dir, target, ignore=shutil.ignore_patterns("answer_key.json"))
    return target


def build_inputs(world_dir: Path, policy: Policy) -> Inputs:
    """The workload of the model comparison (every distinct title, and the accounts the rules
    leave unmatched) and the plan with its two parts. Free: no model is called."""
    with tempfile.TemporaryDirectory(prefix="access-final-") as tmp:
        clean = _copy_without_key(world_dir, Path(tmp) / "world")
        work = compare.build_workload(clean, policy)
        combined = build_plan(clean, policy)
    main, held = split_plan(combined, policy)
    parts = (("main", main),) + ((("held", held),) if held is not None else ())
    unmapped = frozenset(t for t, _, _ in work.titles if t not in policy.title_map)
    return Inputs(work, combined, parts, unmapped)


# ------------------------------------------------------------------------------- calls


@dataclass(slots=True)
class Answers:
    """One repeat's answers. None means no answer (refused, failed or invalid)."""

    titles: dict[str, str | None] = field(default_factory=dict)
    owners: dict[tuple[str, str, str], MatchSuggestion | None] = field(default_factory=dict)
    summaries: dict[str, str | None] = field(default_factory=dict)
    calls: int = 0
    stopped: str = ""


def ask(inputs: Inputs, client: ClaudeClient, *, summaries: bool = True) -> Answers:
    """One repeat: every call of the final run, in a fixed order (titles, accounts, then the
    summaries of the main and held plan). A spend limit stops the repeat and is recorded."""
    out = Answers()
    start = client.calls
    work = inputs.work
    try:
        for title, department, team in work.titles:
            s = suggest_role(title, department, team, work.roles, client)
            out.titles[title] = s.role if s is not None else None
        for a in work.leftovers:
            out.owners[a.key] = suggest_owner(a, shortlist(a, work.records), client)
        if summaries:
            for name, part in inputs.parts:
                out.summaries[name] = summary.model_summary(part, client)
    except SpendCapReached as exc:
        out.stopped = str(exc)
    out.calls = client.calls - start
    return out


class RecordingSDK:
    """Stands in for the SDK client in the dry check and the rehearsal. It records every
    request, sends nothing, and answers "none" (an empty paragraph for a summary)."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **params: Any) -> SimpleNamespace:
        self.requests.append(params)
        answer: dict[str, Any] = {}
        for name, prop in params["output_config"]["format"]["schema"]["properties"].items():
            if prop.get("type") == "array":
                answer[name] = []
            elif "enum" in prop:
                answer[name] = "none"  # every enum the run sends ends in "none"
            else:
                answer[name] = ""
        return SimpleNamespace(
            model=params["model"],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=0, output_tokens=0),
            content=[SimpleNamespace(type="text", text=json.dumps(answer))],
        )


class _NoSpendLedger(SpendLedger):
    """The rehearsal's ledger: it refuses nothing and writes nothing. It is only ever paired
    with RecordingSDK, so no request can leave the process through it."""

    def __init__(self) -> None:
        super().__init__(path=Path(os.devnull), run_limit_usd=1.0)

    def check(self, next_call_usd: float = 0.0) -> None:
        return None

    def record(self, model: str, input_tokens: int, output_tokens: int) -> float:
        return 0.0


def rehearsal_client(*, strict: bool = True) -> tuple[ClaudeClient, RecordingSDK]:
    """A client on RecordingSDK. Strict: the frozen client, as the run uses. Not strict (the
    dry check of prompts that are no longer frozen): a plain client, so the changed requests
    can still be counted and priced."""
    sdk = RecordingSDK()
    kind = frozen.FrozenClaudeClient if strict else ClaudeClient
    return kind(frozen.FROZEN_MODEL, client=sdk, ledger=_NoSpendLedger()), sdk


def request_worst_case_usd(params: dict[str, Any]) -> float:
    """The most one request can cost: the same bound the spend ledger checks before a call."""
    schema = params["output_config"]["format"]["schema"]
    user = params["messages"][0]["content"]
    tokens_in = worst_case_input_tokens(params["system"], user, schema)
    return worst_case_usd(params["model"], tokens_in, params["max_tokens"])


def rehearse(
    inputs: Inputs, *, summaries: bool = True, strict: bool = True
) -> tuple[Answers, list[dict[str, Any]]]:
    """One repeat with the fake client: its answers and the exact requests it would send."""
    client, sdk = rehearsal_client(strict=strict)
    answers = ask(inputs, client, summaries=summaries)
    return answers, sdk.requests


def calls_by_kind(requests: Sequence[dict[str, Any]]) -> dict[str, int]:
    counts = dict.fromkeys(frozen.KINDS, 0)
    for r in requests:
        kind = frozen.kind_of(r["system"]) or "unpinned"
        counts[kind] = counts.get(kind, 0) + 1
    return counts


# ------------------------------------------------------------------------------ scoring


def _rate(rate: Rate) -> dict[str, Any]:
    lo, hi = rate.interval
    return {
        "successes": rate.successes,
        "total": rate.total,
        "value": round(rate.value, 6),
        "low": round(lo, 6),
        "high": round(hi, 6),
    }


def _with_ai(result: MatchResult, answers: Answers, people: set[str]) -> MatchResult:
    """Rules plus the AI's owners, as the model comparison combines them."""
    combined = dict(result.matches)
    for k, m in answers.owners.items():
        if m is not None and m.employee_id != "none" and m.employee_id in people:
            combined[k] = Match(m.employee_id, "ai", 0.5)
    return MatchResult(
        combined,
        [k for k in result.unmatched if k not in combined],
        [k for k in result.ambiguous if k not in combined],
    )


def _ai_matches(
    answers: Answers, truth: dict[tuple[str, str], str]
) -> tuple[dict[str, int], set[tuple[str, str, str]]]:
    """The AI's owner suggestions against the key (an account the key doesn't list is counted
    apart, as `score_matching` leaves it out), and the set of its false matches."""
    made = right = not_in_key = 0
    false: set[tuple[str, str, str]] = set()
    for (system, _app, account), m in answers.owners.items():
        if m is None or m.employee_id == "none":
            continue
        k = (system, account.strip().casefold())
        if k not in truth:
            not_in_key += 1
            continue
        made += 1
        if truth[k] == m.employee_id:
            right += 1
        else:
            false.add((*k, m.employee_id))
    counts = {"made": made, "right": right, "false": made - right, "not_in_key": not_in_key}
    return counts, false


def score_run(
    inputs: Inputs, key: dict[str, Any], repeats: Sequence[Answers]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """(scores, disagreements) for the repeats run. Scores hold counts and rates only."""
    work = inputs.work
    owners = [AccountOwner(**o) for o in key["owners"]]
    truth = {(o.system, o.account.strip().casefold()): o.owner_employee_id for o in owners}
    people = {r.employee_id for r in work.records}
    labels = [f"repeat {i}" for i in range(1, len(repeats) + 1)]
    title_answers = {
        label: {t: (role or "none") for t, role in a.titles.items()}
        for label, a in zip(labels, repeats, strict=True)
    }
    with_ai = {
        f"rules + AI, {label}": _with_ai(work.result, a, people)
        for label, a in zip(labels, repeats, strict=True)
    }
    plan_scores = score_plan(inputs.plan, key)
    scores: dict[str, Any] = {
        "plan": {
            t: {
                "predicted": s.predicted,
                "expected": s.expected,
                "precision": _rate(s.precision),
                "recall": _rate(s.recall),
            }
            for t, s in plan_scores.items()
        },
        "matching_rules_only": _match_rates(score_matching(work.result, owners)),
        "repeats": [],
    }
    false_sets = []
    for label, a in zip(labels, repeats, strict=True):
        answered = title_answers[label]
        unmapped = {t: r for t, r in answered.items() if t in inputs.unmapped_titles}
        ai_counts, false = _ai_matches(a, truth)
        false_sets.append(false)
        scores["repeats"].append(
            {
                "repeat": label,
                "calls": a.calls,
                "no_answer": sum(v is None for v in a.titles.values())
                + sum(v is None for v in a.owners.values())
                + sum(v is None for v in a.summaries.values()),
                "stopped": a.stopped,
                "titles": _rate(score_titles(answered, work.records, key["true_roles"])),
                "titles_not_in_the_map": _rate(
                    score_titles(unmapped, work.records, key["true_roles"])
                ),
                "matching_rules_plus_ai": _match_rates(
                    score_matching(with_ai[f"rules + AI, {label}"], owners)
                ),
                "ai_matches": ai_counts,
                "summaries_passed": {
                    name: summary.fill_summary(a.summaries[name], part) is not None
                    for name, part in inputs.parts
                    if name in a.summaries
                },
            }
        )
    scores["false_matches_in_every_repeat"] = (
        len(set.intersection(*false_sets)) if false_sets else 0
    )
    details = {
        "findings": plan_disagreements(inputs.plan, key),
        "matches": matching_disagreements({"rules only": work.result, **with_ai}, owners),
        "titles": title_disagreements(title_answers, work.records, key["true_roles"]),
    }
    return scores, details


def _match_rates(score: Any) -> dict[str, Any]:
    return {"precision": _rate(score.precision), "recall": _rate(score.recall)}


def safety_section(world_dir: Path, inputs: Inputs, policy: Policy) -> dict[str, Any]:
    """The four safety counts (`meridian_access.safety`), on a copy of the world without its
    answer key: the plan's two parts given to apply in a dry run on fresh mocks, with one
    approval each. Code only: no model is called. The plan never reads an answer, so the counts
    are the same for every repeat."""
    with tempfile.TemporaryDirectory(prefix="access-final-safety-") as tmp:
        clean = _copy_without_key(world_dir, Path(tmp) / "world")
        return safety.measure(
            clean,
            inputs.plan,
            inputs.parts,
            policy,
            matches=inputs.work.result,
            map_entries=load_entries(),
        )


def rulings_template(details: dict[str, Any]) -> str:
    """The rulings log, ready for the owner: one row per disagreement, ruling left blank."""
    lines = [
        "# Access held-out a1: rulings",
        "",
        "One row per disagreement between the final run and the answer key (AAC-010). Ruling:",
        "`tool wrong`, `key wrong` or `both acceptable`, and a one-line reason.",
        "",
        "| Id | Kind | Item | What the tool did | What the key says | Ruling | Reason |",
        "|---|---|---|---|---|---|---|",
    ]

    def cell(value: object) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")

    for d in details["findings"]:
        side = d["key"] or d["tool"]
        item = f"{side['system']}/{side['app_id']}/{side['account']} ({side['employee_id']})"
        tool = "nothing" if d["tool"] is None else f"{d['tool']['type']}: {d['tool']['action']}"
        key = (
            "nothing"
            if d["key"] is None
            else f"{d['key']['type']}: {d['key'].get('expected_action', '')}"
        )
        lines.append(f"| {d['id']} | {d['kind']} | {cell(item)} | {cell(tool)} | {cell(key)} | | |")
    for d in details["matches"]:
        item = f"{d['system']}/{d['account']}"
        tool = "; ".join(f"{label}: {v or 'nobody'}" for label, v in d["tool"].items())
        key = d["key_owner"] or "nobody"
        lines.append(f"| {d['id']} | owner | {cell(item)} | {cell(tool)} | {cell(key)} | | |")
    for d in details["titles"]:
        tool = "; ".join(f"{label}: {v}" for label, v in d["tool"].items())
        key = d["key_role"] or "no role in the key"
        lines.append(f"| {d['id']} | title | {cell(d['title'])} | {cell(tool)} | {cell(key)} | | |")
    return "\n".join(lines) + "\n"


def write_outputs(out_dir: Path, report: dict[str, Any], details: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    dump = {"indent": 2, "sort_keys": True, "ensure_ascii": False}
    (out_dir / "report.json").write_text(json.dumps(report, **dump) + "\n", encoding="utf-8")
    (out_dir / "details.json").write_text(json.dumps(details, **dump) + "\n", encoding="utf-8")
    (out_dir / "rulings-template.md").write_text(rulings_template(details), encoding="utf-8")


def outside_repo(path: Path) -> Path:
    """The resolved path, or ValueError if it lies inside the repository: the details hold
    held-out content and must never be committed by accident."""
    resolved = path.resolve()
    if resolved.is_relative_to(repo_root().resolve()):
        raise ValueError("--out holds held-out content and must be outside the repository")
    return resolved


# --------------------------------------------------------------------------------- runs


def run_world(
    world_dir: Path,
    out_dir: Path,
    client: ClaudeClient,
    ledger: SpendLedger,
    *,
    k: int = 1,
    summaries: bool = True,
    policy: Policy | None = None,
) -> dict[str, Any]:
    """The final run on one world directory (answer_key.json included; only scoring reads
    it): a rehearsal with the fake client, the spend check for all `k` repeats, the calls,
    then the scores and every disagreement, written to `out_dir`. Raises SpendCapReached,
    before any call, if the worst case doesn't fit. The CLI passes the sealed world only after
    every guard; tests pass the tuning world with a fake client. The report also holds the four
    safety counts, counted before the first call (`safety_section`)."""
    if not 1 <= k <= MAX_K:
        raise ValueError(f"k must be 1 to {MAX_K}, got {k}")
    if not isinstance(client, frozen.FrozenClaudeClient):
        raise frozen.PromptsNotFrozen("the final run uses the frozen client only")
    frozen.require_frozen()
    out_dir = outside_repo(out_dir)
    policy = policy or load_policy()
    key = load_key(world_dir)
    inputs = build_inputs(world_dir, policy)
    dry, requests = rehearse(inputs, summaries=summaries)
    # A dress rehearsal of the scoring and the files, so a bug shows before money is spent.
    with tempfile.TemporaryDirectory(prefix="access-final-rehearsal-") as tmp:
        write_outputs(Path(tmp), {"rehearsal": True}, score_run(inputs, key, [dry])[1])
    # The safety counts need no answer: counted before the first paid call, so a bug in them
    # also shows before money is spent.
    safe = safety_section(world_dir, inputs, policy)
    worst = round(k * sum(request_worst_case_usd(r) for r in requests), 6)
    ledger.check(worst)  # SpendCapReached here: nothing has been sent
    repeats: list[Answers] = []
    for _ in range(k):
        answers = ask(inputs, client, summaries=summaries)
        repeats.append(answers)
        if answers.stopped:
            break
    scores, details = score_run(inputs, key, repeats)
    hashes = frozen.current_hashes()
    report = {
        "status": STATUS,
        "world": _recipe_name(world_dir),
        "model": client.model,
        "k": k,
        "summaries": summaries,
        "budget_item": ledger.item,
        "prompt_sha256": hashes,
        "freeze_digest": frozen.freeze_digest(hashes),
        "calls_planned": k * len(requests),
        "calls_made": client.calls,
        "worst_case_usd": worst,
        "cost_usd": round(ledger.run_spent_usd, 6),
        "scores": scores,
        "safety": safe,  # apart from precision and recall (AAC-005): each count's target is 0
        "disagreements": {name: len(items) for name, items in details.items()},
    }
    write_outputs(out_dir, report, details)
    return report


def dry_check(
    world_dir: Path, *, k: int = 1, summaries: bool = True, policy: Policy | None = None
) -> dict[str, Any]:
    """The final run's calls on the tuning world with the fake client: counts, the upper cost
    at the chosen model's rates, and the prompt hashes. Refuses any other world."""
    require_tuning_world(world_dir)
    if not 1 <= k <= MAX_K:
        raise ValueError(f"k must be 1 to {MAX_K}, got {k}")
    inputs = build_inputs(world_dir, policy or load_policy())
    issues = frozen.problems()
    _, requests = rehearse(inputs, summaries=summaries, strict=not issues)
    per_repeat = sum(request_worst_case_usd(r) for r in requests)
    hashes = frozen.current_hashes()
    return {
        "world": "tuning",
        "model": frozen.FROZEN_MODEL,
        "rates_usd_per_mtok": list(PRICES_PER_MTOK[frozen.FROZEN_MODEL]),
        "k": k,
        "calls_per_repeat": len(requests),
        "calls_by_kind": calls_by_kind(requests),
        "calls": k * len(requests),
        "worst_case_usd_per_repeat": round(per_repeat, 6),
        "worst_case_usd": round(k * per_repeat, 6),
        "prompt_sha256": hashes,
        "freeze_digest": frozen.freeze_digest(hashes),
        "problems": issues,
        "calls_made": 0,
    }


def _recipe_name(world_dir: Path) -> str | None:
    manifest = world_dir / "manifest.json"
    if not manifest.is_file():
        return None
    return json.loads(manifest.read_text(encoding="utf-8")).get("recipe_name")


def require_tuning_world(world_dir: Path) -> None:
    """ValueError unless the directory is the tuning world: seed and recipe name both."""
    seed = json.loads((world_dir / "hris.json").read_text(encoding="utf-8")).get("seed")
    recipe = _recipe_name(world_dir)
    if seed != compare.TUNING_SEED or recipe != "tuning":
        raise ValueError(
            f"the dry check runs on the tuning world only (seed {compare.TUNING_SEED}, "
            f"recipe tuning), not seed {seed!r}, recipe {recipe!r}"
        )


def build_sealed_world(final_score: str, target: Path) -> Path:
    """Write the held-out world (answer key included) into `target`. Only after the CLI's
    guards: it loads the sealed recipe by the name the flag gave."""
    if final_score != FINAL_SCORE:
        raise ValueError(f"pass --final-score={FINAL_SCORE}")
    from meridian_dataset.access import generate_access, write_access
    from meridian_dataset.access.recipe import load_recipe, tuning_seed

    recipe = load_recipe(final_score)
    world = generate_access(tuning_seed(recipe), recipe)
    write_access(world, target)
    return target


def final_main(
    final_score: str,
    out: Path | None,
    *,
    k: int,
    summaries: bool,
    env: Mapping[str, str] | None = None,
) -> int:
    """The paid run's command line. Exit 2: refused, nothing built or sent. Exit 1: failed
    after the start (the message is withheld: it could hold held-out content). Exit 0: done.
    `env` is the process environment unless a test passes its own."""
    env = os.environ if env is None else env
    if final_score != FINAL_SCORE:
        print(f"refusing: the final score needs --final-score={FINAL_SCORE}")
        return 2
    issues = frozen.problems()
    if issues:
        print("refusing: the prompts or the model are not the frozen ones:")
        for issue in issues:
            print(f"  - {issue}")
        return 2
    if env.get("ACCESS_AI_LIVE") != "1":
        print("refusing: a live run needs ACCESS_AI_LIVE=1")
        return 2
    named = env.get(ITEM_ENV)
    if named and named != BUDGET_ITEM:
        print(f"refusing: the final score is charged to {BUDGET_ITEM}, not {named}")
        return 2
    if out is None:
        print("refusing: --out is required (a directory outside the repository)")
        return 2
    try:
        out = outside_repo(out)
    except ValueError as err:
        print(f"refusing: {err}")
        return 2
    ledger = SpendLedger(item=BUDGET_ITEM)
    try:
        ledger.check(0.0)  # the item is approved and the paid window is open
    except SpendCapReached as err:
        print(f"refusing: {err}")
        return 2
    print(f"frozen: yes, digest {frozen.FREEZE_DIGEST}; model {frozen.FROZEN_MODEL}; k={k}")
    client = frozen.FrozenClaudeClient(frozen.FROZEN_MODEL, ledger=ledger)
    try:
        with tempfile.TemporaryDirectory(prefix="access-final-world-") as tmp:
            world = build_sealed_world(final_score, Path(tmp) / "world")
            report = run_world(world, out, client, ledger, k=k, summaries=summaries)
    except SpendCapReached as err:
        if client.calls == 0:
            print(f"refusing: {err}")
            return 2
        print(f"stopped by a spend limit after {client.calls} calls")
        return 1
    except Exception as err:  # never print the message: it could quote held-out data
        print(f"error: {type(err).__name__} after {client.calls} calls (message withheld)")
        return 1
    print(
        f"done: {report['calls_made']} calls of {report['calls_planned']} planned, "
        f"cost ${report['cost_usd']:.4f} (worst case ${report['worst_case_usd']:.4f})"
    )
    for name, n in report["disagreements"].items():
        print(f"  disagreements, {name}: {n}")
    for line in safety.log_lines(report["safety"]):
        print(line)
    print(f"written to {out} (unruled: not for publishing, AAC-010)")
    return 0
