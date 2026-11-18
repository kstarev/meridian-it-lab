"""Milestone 3d part 2's prompt selection, on tuning data only.

    python -m tier0.evals.m3d2_selection score --model claude-haiku-5-5 --version HT8 --part2
    python -m tier0.evals.m3d2_selection score --model claude-haiku-5-5 --version HT9 --all
    python -m tier0.evals.m3d2_selection pick --model claude-haiku-5-5 --task triage \\
        --current HT8 --new HT9

The rule is the plan's (docs/plans/milestone-3d-part-2.md, "The selection rule for any new
prompt version"), committed before any new version was scored:

- triage: R25's floor (every several-request tuning message, R25's 12 and part 2's 12, not
  sorted own_username in any run), then the reset floor (every own-account reset tuning message
  not sorted own_username), then 3a's rule, unchanged, on every tuning message;
- check: 3a's rule, unchanged;
- version 1 (T1, C1) sets the colleague floor; the candidates are the arm's current prompt and
  the one new version. If none clears every floor, the current prompt stays.

"Every tuning message" is 3b's 116, 3d's 42, R25's 12 and part 2's 67 (its outsiders are left
out: identity binding stops them before any model call). Earlier versions' saved scores are
reused by prompt SHA-256 and k (part 1's are in out/m3d/). Scores are tuning scores and
optimistic; only held-out v6 measures anything.
"""

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from meridian_dataset import DEFAULT_SEED, generate
from meridian_dataset.generator import _fill_colleague
from meridian_dataset.names import TUNING_3D2_POOL
from tier0.evals import caching, m3_selection, m3d_screening
from tier0.evals.m3_selection import RESET, SEVERAL, Instance
from tier0.netguard import guard_for_model

RESULTS = m3d_screening.RESULTS  # out/m3d: part 1's scores and part 2's, side by side
PART2 = "part2"
# 3a's rule counts these groups; the floors count the other two.
RULE_GROUPS = ("everyday", "colleague")


def instances_3d2() -> list[Instance]:
    """Part 2's tuning messages as scorer cases. Repeat requests are everyday requests to triage
    and to the check: the same-day count is code, later. Outsiders are left out."""
    world = generate(DEFAULT_SEED)
    first = world.person_by_email("alex.kim@meridian.example")
    second = world.person_by_email("alex.kim2@meridian.example")
    if first is None or second is None:
        raise LookupError("the tuning world lost its duplicate-name pair")
    sender = next(p for p in world.people if p.status == "active" and p is not second)
    report = next(p for p in world.people if p.manager_id and p.status == "active")
    manager = world.person_by_id(report.manager_id or "")
    if manager is None:
        raise LookupError("the tuning world has no manager for the colleague target")
    pool = TUNING_3D2_POOL
    own = (sender.full_name, sender.primary_email)
    out = [Instance("everyday", False, t, *own) for t in pool.normal]
    out += [
        Instance(
            "everyday",
            False,
            t.format(other_email=first.primary_email),
            second.full_name,
            second.primary_email,
        )
        for t in pool.duplicate
    ]
    out += [Instance("everyday", False, m, *own) for chain in pool.repeat for m in chain]
    out += [
        Instance(
            "colleague",
            "{target" not in t,
            _fill_colleague(t, report),
            manager.full_name,
            manager.primary_email,
        )
        for t in pool.colleague
    ]
    out += [Instance(SEVERAL, False, t, *own) for t in pool.several]
    out += [Instance(RESET, False, t, *own) for t in pool.reset]
    return out


def _path(model: str, version: str, set_name: str) -> Path:
    return RESULTS / f"{model}__{version}__{set_name}.json"


def score_part2(
    model: str, version: str, *, model_factory: Any = None, prompt_cache: bool = False
) -> Path:
    """One version on part 2's 67 tuning messages, k = 3."""
    path = _path(model, version, PART2)
    if path.exists():
        return path
    made = model_factory(model) if model_factory is not None else None
    result = m3_selection.score(
        model, version, model=made, cases=instances_3d2(), prompt_cache=prompt_cache
    )
    result["set"] = PART2
    m3d_screening._save(path, result)
    return path


def _text(version: str) -> str:
    return (
        m3_selection.M3_TRIAGE_VERSIONS
        if m3_selection.task_of(version) == "triage"
        else m3_selection.M3_CHECK_VERSIONS
    )[version]


def load(model: str, version: str) -> dict[str, Any] | None:
    """A version's score on every tuning message, summed over its saved sets; None while a set
    is missing. A saved score counts only on the prompt text in force now, at k = 3."""
    sha = hashlib.sha256(_text(version).encode()).hexdigest()
    sets = ["all", PART2] + ([SEVERAL] if m3_selection.task_of(version) == "triage" else [])
    correct: dict[str, int] = {}
    total: dict[str, int] = {}
    for set_name in sets:
        path = _path(model, version, set_name)
        if not path.exists():
            return None
        r = json.loads(path.read_text())
        if r["prompt_sha256"] != sha or r["k"] != m3_selection.K:
            raise ValueError(f"{path} is stale (prompt text or k changed)")
        for group, n in r["total"].items():
            total[group] = total.get(group, 0) + n
            correct[group] = correct.get(group, 0) + r["correct"][group]
    return {
        "version": version,
        "correct": correct,
        "total": total,
        "prompt_chars": len(_text(version)),
    }


def clears_floors(score: dict[str, Any]) -> bool:
    """R25's floor and the reset floor: every such message handed off in every run."""
    return all(
        score["correct"].get(g, 0) == score["total"].get(g, 0) for g in (SEVERAL, RESET)
    ) and all(score["total"].get(g, 0) > 0 for g in (SEVERAL, RESET))


def pick(model: str, task: str, current: str, new: str | None) -> dict[str, Any]:
    """The plan's rule for one arm: the current prompt against the one new version."""
    first = "T1" if task == "triage" else "C1"
    names = [first, current, *([new] if new else [])]
    scores = {v: load(model, v) for v in dict.fromkeys(names)}
    missing = [v for v, s in scores.items() if s is None]
    if missing:
        raise ValueError(f"not scored on every tuning message yet: {', '.join(missing)}")
    floor = scores[first]["correct"]["colleague"]  # type: ignore[index]
    candidates = [scores[v] for v in dict.fromkeys([current, *([new] if new else [])])]
    eligible = [
        s
        for s in candidates
        if s["correct"]["colleague"] >= floor  # type: ignore[index]
        and (task != "triage" or clears_floors(s))  # type: ignore[arg-type]
    ]
    if not eligible:
        return {"pick": current, "why": "no candidate clears every floor; the current prompt stays"}
    best = max(
        eligible,
        key=lambda s: (s["correct"]["everyday"], s["correct"]["colleague"], -s["prompt_chars"]),  # type: ignore[index]
    )
    return {"pick": best["version"], "why": "the plan's rule", "scores": scores, "floor": floor}  # type: ignore[index]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tier0.evals.m3d2_selection", description=__doc__
    )
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("score")
    s.add_argument("--model", required=True)
    s.add_argument("--version", required=True)
    which = s.add_mutually_exclusive_group(required=True)
    which.add_argument("--part2", action="store_true", help="part 2's 67 tuning messages")
    which.add_argument("--all", action="store_true", help="every tuning message")
    caching.add_flags(s)
    p = sub.add_parser("pick")
    p.add_argument("--model", required=True)
    p.add_argument("--task", required=True, choices=("triage", "check"))
    p.add_argument("--current", required=True)
    p.add_argument("--new", default=None)
    args = parser.parse_args(argv)
    if args.command == "pick":
        result = pick(args.model, args.task, args.current, args.new)
        for version, score in (result.get("scores") or {}).items():
            cells = ", ".join(
                f"{g} {score['correct'][g]}/{score['total'][g]}" for g in score["total"]
            )
            print(f"{version}: {cells}; {score['prompt_chars']} characters")
        print(f"pick: {result['pick']} ({result['why']})")
        return 0
    # Tuning data only, so never sealed: on for these Claude runs unless turned off.
    cache, refusal = caching.resolve(args.prompt_cache, provider="claude", sealed=False)
    if refusal is not None:
        print(f"refused: {refusal}", file=sys.stderr)
        return 2
    with guard_for_model("claude"):
        print(f"saved: {score_part2(args.model, args.version, prompt_cache=cache)}")
        if args.all:
            print(f"saved: {m3d_screening.score_all(args.model, args.version, prompt_cache=cache)}")
            if m3_selection.task_of(args.version) == "triage":
                shown = m3d_screening.score_several(args.model, args.version, prompt_cache=cache)
                print(f"saved: {shown}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
