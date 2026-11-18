"""Counts only, from two finished Access runs: the final score's details and the red-team report.

    python -m meridian_access.ai.run_counts [--final-details PATH]
        [--redteam-report PATH --unseal held_out_redteam1] [--json]

It prints numbers, ids (F001, M001, T001, rt-001) and enum values from code, and nothing else:
never an account, a name, a job title, a role id from the key or any paragraph. Every string
in the output is checked against an allow-list before anything is printed (`self_check`), and
a string outside it makes the command refuse with exit 2 and print nothing else.

Exit 0 on success; 2 on bad arguments or unreadable input (the message names the problem,
never content).
"""

import argparse
import json
import random
import re
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from meridian_access.ai.pricing import PRICES_PER_MTOK
from meridian_access.redteam import cases as cases_mod
from meridian_access.redteam import harness
from meridian_access.redteam.cases import FIELDS, GOALS, SET_NAME, SET_SHA256, SHAPES, TASKS, Case
from meridian_access.score import FINDING_TYPES
from meridian_dataset.access.catalog import ACTIONS, SYSTEMS

OTHER = "other"
NONE = "none"
MATCH_KINDS = ("agree", "left unmatched", "wrong person", "false match")  # the fixed order
PLAN_KINDS = ("missed", "extra", "differ")
TITLE_ANSWERS = ("right", "none", "other role")  # the fixed order
KEY_HAS_ROLE = "key has a role"
KEY_NO_ROLE = "no role in the key"
NO_AI = "no AI repeats"
RULES_ONLY = "rules only"
BAD_ID = "bad-id"
LANGUAGES = ("en", OTHER)
FIELD_NAMES = tuple(sorted({f for fs in FIELDS.values() for f in fs}))
GOAL_NAMES = tuple(sorted({g for gs in GOALS.values() for g in gs}))

_ENUM = re.compile(r"^[a-z][a-z0-9_ -]{0,39}$")
_ID = re.compile(r"^[FMT]\d{3,}$")
_RT_ID = re.compile(r"^rt-\d{3}$")
_MODEL = re.compile(r"^claude-[a-z0-9-]+$")
KNOWN_MODELS = frozenset(PRICES_PER_MTOK)  # only these may be printed
_LANG = re.compile(r"^[a-z]{2}$")
REFUSED = "refused: output held a string outside the allow-list"

# Hand-built: none. Systems and actions come from meridian_dataset.access.catalog.
_FINDING_ENUMS = frozenset(
    (*FINDING_TYPES, *MATCH_KINDS, *PLAN_KINDS, *SYSTEMS, *ACTIONS, NONE, OTHER)
)


def _enum(value: object, allowed: Iterable[str] | None = None) -> str:
    """The value if it is a short lowercase string in the allow-list built from code, else
    "other"."""
    pool = _FINDING_ENUMS if allowed is None else frozenset(allowed)
    if isinstance(value, str) and _ENUM.match(value) and value in pool:
        return value
    return OTHER


def _id(value: object, prefix: re.Pattern[str] = _ID) -> str:
    return value if isinstance(value, str) and prefix.match(value) else BAD_ID


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _kinds_pattern(kinds: Sequence[str], order: Sequence[str]) -> str:
    """ "agree 2, wrong person 1 of 3": counts in the fixed order, "other" last."""
    counted = Counter(kinds)
    parts = [f"{k} {counted[k]}" for k in (*order, OTHER) if counted[k]]
    return f"{', '.join(parts)} of {len(kinds)}"


def _finding_pattern(item: Mapping[str, Any]) -> tuple[str, ...]:
    tool, key = item.get("tool"), item.get("key")
    tool = tool if isinstance(tool, Mapping) else {}
    key = key if isinstance(key, Mapping) else {}

    def pick(d: Mapping[str, Any], name: str, allowed: Iterable[str]) -> str:
        return _enum(d[name], allowed) if d.get(name) is not None else NONE

    types, actions = (*FINDING_TYPES, NONE, OTHER), (*ACTIONS, NONE, OTHER)
    return (
        _enum(item.get("kind"), (*PLAN_KINDS, OTHER)),
        pick(tool, "type", types),
        pick(tool, "action", actions),
        pick(key, "type", types),
        pick(key, "expected_action", actions),
    )


def _match_pattern(item: Mapping[str, Any]) -> tuple[str, ...]:
    kinds = _mapping(item.get("kind"))
    rules = _enum(kinds.get(RULES_ONLY, NONE), (*MATCH_KINDS, NONE, OTHER))
    ai = [_enum(v, (*MATCH_KINDS, OTHER)) for label, v in kinds.items() if label != RULES_ONLY]
    pattern = _kinds_pattern(ai, MATCH_KINDS) if ai else NO_AI
    return (_enum(item.get("system"), (*SYSTEMS, OTHER)), rules, pattern)


def _title_pattern(item: Mapping[str, Any]) -> tuple[str, ...]:
    role = item.get("key_role")
    tool = _mapping(item.get("tool"))
    answers = []
    for v in tool.values():
        if v == NONE:
            answers.append("none")
        elif isinstance(role, str) and role and v == role:
            answers.append("right")
        else:
            answers.append("other role")
    has_role = KEY_HAS_ROLE if isinstance(role, str) and role else KEY_NO_ROLE
    return (has_role, _kinds_pattern(answers, TITLE_ANSWERS))


# The owner's ruling R-A3 (2026-11-18): in a group of SAMPLE_FROM ids or more, the owner reads a
# seeded random sample of SAMPLE_SIZE ids and rules on the group; in a smaller group, and for job
# titles, every id is read. The seed was committed before anyone read the disagreements, and is
# never changed after: a test pins it.
SAMPLE_SEED = 20261118
SAMPLE_SIZE = 3
SAMPLE_FROM = 10


def to_read(list_name: str, pattern: Sequence[str], ids: Sequence[str]) -> list[str]:
    """The ids the owner reads for one group: all of them, or the seeded sample."""
    if list_name == "titles" or len(ids) < SAMPLE_FROM:
        return sorted(ids)
    rng = random.Random(f"{SAMPLE_SEED}|{list_name}|{'|'.join(pattern)}")  # noqa: S311 (a reproducible sample, not a secret)
    return sorted(rng.sample(sorted(ids), SAMPLE_SIZE))


def final_groups(details: dict) -> dict:
    """Group the final score's disagreements by pattern. Counts, ids and enums only."""
    lists = (
        ("findings", _finding_pattern),
        ("matches", _match_pattern),
        ("titles", _title_pattern),
    )
    groups: dict[tuple[str, tuple[str, ...]], list[str]] = {}
    totals = {"findings": 0, "matches": 0, "titles": 0}
    for name, pattern_of in lists:
        for item in details.get(name, []):
            totals[name] += 1
            groups.setdefault((name, pattern_of(item)), []).append(_id(item.get("id")))
    rows = [
        {
            "list": name,
            "pattern": list(p),
            "count": len(ids),
            "ids": sorted(ids),
            "to_read": to_read(name, p, ids),
        }
        for (name, p), ids in groups.items()
    ]
    rows.sort(key=lambda r: (-r["count"], r["list"], tuple(r["pattern"])))
    return {"totals": {**totals, "all": sum(totals.values())}, "groups": rows}


# ------------------------------------------------------------------ the red-team report


def _language(code: str) -> str:
    return code if _LANG.match(code) else OTHER


def _cell(cases: Sequence[Case], runs: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    ids = {c.id for c in cases}
    hits = Counter(
        cid
        for r in runs
        for cid, outcome in r["outcomes"].items()
        if cid in ids and outcome == harness.ATTACK_SUCCESS
    )
    return {
        "cases": len(cases),
        "attempts": len(cases) * len(runs),
        "successes": sum(hits.values()),
        "cases_with_a_success": len(hits),
    }


def _by(
    cases: Sequence[Case], runs: Sequence[Mapping[str, Any]], key: Any
) -> dict[str, dict[str, int]]:
    buckets: dict[str, list[Case]] = {}
    for c in cases:
        buckets.setdefault(key(c), []).append(c)
    return {k: _cell(v, runs) for k, v in sorted(buckets.items())}


def _one_model(cases: Sequence[Case], runs_all: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    runs = [r for r in runs_all if r.get("finished")]
    by_id = {c.id: c for c in cases}
    attacks = [c for c in cases if c.is_attack]
    unsafe = [c for c in attacks if c.goal != "force_none"]
    hits: Counter[str] = Counter()
    outcomes: dict[str, dict[str, Counter[str]]] = {"attack": {}, "control": {}}
    allowed = {
        "attack": (*harness.ATTACK_OUTCOMES, OTHER),
        "control": (*harness.CONTROL_OUTCOMES, OTHER),
    }
    for r in runs:
        for cid, outcome in r["outcomes"].items():
            c = by_id.get(cid)
            if c is None:
                continue
            kind = "attack" if c.is_attack else "control"
            outcomes[kind].setdefault(c.task, Counter())[_enum(outcome, allowed[kind])] += 1
            if c.is_attack and outcome == harness.ATTACK_SUCCESS:
                hits[cid] += 1
    listed = [
        {
            "id": _id(c.id, _RT_ID),
            "task": _enum(c.task, TASKS),
            "goal": _enum(c.goal, GOAL_NAMES),
            "shape": _enum(c.shape, SHAPES),
            "field": _enum(c.field, FIELD_NAMES),
            "language": _language(c.language),
            "successes": hits[c.id],
            "repeats": len(runs),
        }
        for c in attacks
        if hits[c.id]
    ]
    return {
        "repeats_finished": len(runs),
        "total": _cell(attacks, runs),
        "outside_force_none": _cell(unsafe, runs),
        "by_task": _by(attacks, runs, lambda c: _enum(c.task, TASKS)),
        "by_task_goal": _by(
            attacks, runs, lambda c: f"{_enum(c.task, TASKS)}/{_enum(c.goal, GOAL_NAMES)}"
        ),
        "by_task_shape": _by(
            attacks, runs, lambda c: f"{_enum(c.task, TASKS)}/{_enum(c.shape, SHAPES)}"
        ),
        "by_task_field": _by(
            attacks, runs, lambda c: f"{_enum(c.task, TASKS)}/{_enum(c.field, FIELD_NAMES)}"
        ),
        "by_language": _by(attacks, runs, lambda c: "en" if c.language == "en" else OTHER),
        "cases_with_a_success": listed,
        "outcomes_by_task": {
            kind: {t: dict(sorted(n.items())) for t, n in sorted(per.items())}
            for kind, per in outcomes.items()
        },
    }


def redteam_groups(report: dict, cases: Sequence[Case]) -> dict:
    """Per model, over finished runs and attack cases: attempts and attack successes in cells."""
    models: dict[str, Any] = {}
    for model, data in sorted(report["models"].items()):
        name = model if model in KNOWN_MODELS else OTHER
        models[name] = _one_model(cases, data["runs"])
    return {"models": models}


# ------------------------------------------------------------------ the output check

_COUNTS = re.compile(r"^[a-z][a-z ]* \d+(, [a-z][a-z ]* \d+)* of \d+$")
FIXED = frozenset(
    (
        *_FINDING_ENUMS,
        *TASKS,
        *SHAPES,
        *GOAL_NAMES,
        *FIELD_NAMES,
        *harness.ATTACK_OUTCOMES,
        *harness.CONTROL_OUTCOMES,
        *LANGUAGES,
        KEY_HAS_ROLE,
        KEY_NO_ROLE,
        NO_AI,
        BAD_ID,
        "attack",
        "control",
        "findings",
        "matches",
        "titles",
        "all",
        "totals",
        "groups",
        "list",
        "pattern",
        "count",
        "ids",
        "to_read",
        "final",
        "redteam",
        "models",
        "repeats_finished",
        "total",
        "outside_force_none",
        "by_task",
        "by_task_goal",
        "by_task_shape",
        "by_task_field",
        "by_language",
        "cases_with_a_success",
        "outcomes_by_task",
        "cases",
        "attempts",
        "successes",
        "id",
        "task",
        "goal",
        "shape",
        "field",
        "language",
        "repeats",
    )
)
_PHRASE_WORDS = frozenset((*MATCH_KINDS, *TITLE_ANSWERS, OTHER))


def _string_ok(s: str) -> bool:
    if s in FIXED or _ID.match(s) or _RT_ID.match(s) or s in KNOWN_MODELS or _LANG.match(s):
        return True
    if "/" in s:
        return all(part in FIXED for part in s.split("/"))
    if _COUNTS.match(s):
        head, _, _ = s.rpartition(" of ")
        return all(part.rsplit(" ", 1)[0] in _PHRASE_WORDS for part in head.split(", "))
    return False


def self_check(value: object) -> bool:
    """True if every string (keys and values) in the output is allowed."""
    if isinstance(value, str):
        return _string_ok(value)
    if isinstance(value, Mapping):
        return all(self_check(k) and self_check(v) for k, v in value.items())
    if isinstance(value, list | tuple):
        return all(self_check(v) for v in value)
    return isinstance(value, int | float | bool) or value is None


# ------------------------------------------------------------------ rendering


def _cell_line(cell: Mapping[str, int]) -> str:
    return (
        f"cases {cell['cases']}, attempts {cell['attempts']}, successes {cell['successes']}, "
        f"cases with a success {cell['cases_with_a_success']}"
    )


def render(out: Mapping[str, Any]) -> str:
    lines: list[str] = []
    final = out.get("final")
    if final:
        t = final["totals"]
        lines.append(
            f"final score: {t['all']} disagreements "
            f"(findings {t['findings']}, matches {t['matches']}, titles {t['titles']})"
        )
        for g in final["groups"]:
            lines.append(f"{g['count']:>4}  {g['list']}  {' | '.join(g['pattern'])}")
            lines.append(f"      ids: {' '.join(g['ids'])}")
            lines.append(f"      to read: {' '.join(g['to_read'])}")
    for model, m in (out.get("redteam") or {}).get("models", {}).items():
        lines.append(f"red team, {model}: finished repeats {m['repeats_finished']}")
        lines.append(f"  all attacks: {_cell_line(m['total'])}")
        lines.append(f"  outside force_none: {_cell_line(m['outside_force_none'])}")
        for part in ("by_task", "by_task_goal", "by_task_shape", "by_task_field", "by_language"):
            for k, cell in m[part].items():
                lines.append(f"  {part} {k}: {_cell_line(cell)}")
        for c in m["cases_with_a_success"]:
            lines.append(
                f"  success {c['id']} {c['task']}/{c['goal']}/{c['shape']}/{c['field']} "
                f"{c['language']}: {c['successes']} of {c['repeats']}"
            )
        for kind, per in m["outcomes_by_task"].items():
            for task, counted in per.items():
                body = ", ".join(f"{o} {n}" for o, n in counted.items())
                lines.append(f"  outcomes {kind} {task}: {body}")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ the command


class _Refused(Exception):
    pass


def _load(path: Path, what: str) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise _Refused(f"cannot read {what} as JSON") from None
    if not isinstance(data, dict):
        raise _Refused(f"{what} is not a JSON object")
    return data


def _build(args: argparse.Namespace) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if args.final_details:
        out["final"] = final_groups(_load(args.final_details, "--final-details"))
    if args.redteam_report:
        report = _load(args.redteam_report, "--redteam-report")
        if report.get("set_sha256") != SET_SHA256:
            raise _Refused("the report's set_sha256 is not the pinned one")
        try:
            cases = cases_mod.unseal(args.unseal)
        except (cases_mod.SetError, OSError, ValueError):
            raise _Refused("cannot open the red-team set") from None
        out["redteam"] = redteam_groups(report, cases)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m meridian_access.ai.run_counts")
    parser.add_argument("--final-details", type=Path)
    parser.add_argument("--redteam-report", type=Path)
    parser.add_argument("--unseal", help=f"the red-team set's own name, {SET_NAME}")
    parser.add_argument("--json", action="store_true")
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 2
    problem = None
    if not (args.final_details or args.redteam_report):
        problem = "give --final-details, --redteam-report or both"
    elif args.redteam_report and args.unseal != SET_NAME:
        problem = f"--redteam-report needs --unseal {SET_NAME}"
    if problem:
        print(problem, file=sys.stderr)
        return 2
    try:
        out = _build(args)
    except _Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    except (KeyError, TypeError, AttributeError, ValueError):
        print("refused: an input does not have the expected shape", file=sys.stderr)
        return 2
    if not self_check(out):
        print(REFUSED, file=sys.stderr)
        return 2
    print(json.dumps(out, indent=2) if args.json else render(out), end="\n" if args.json else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
