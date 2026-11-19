"""Mutation testing for the safety code (docs/evals/2026-11-18-safety-mutation-testing.md, and
for Access-as-Code's targets, all named "access ...",
docs/evals/2026-11-19-access-safety-mutation-testing.md).

    uv run --locked python tools/mutate.py run                   # every target, all tests
    uv run --locked python tools/mutate.py run --target ladder   # one target
    uv run --locked python tools/mutate.py run --target "access "   # Access-as-Code only
    uv run --locked python tools/mutate.py run --baseline        # only the tests from before
    uv run --locked python tools/mutate.py list                  # print the mutants, run nothing
    uv run --locked python tools/mutate.py show 17               # print mutant 17 as a diff
    uv run --locked python tools/mutate.py render BEFORE.json AFTER.json   # the tables

A mutant is a target file with one small change inside one of its named functions. Each
mutant's tests are run against it. If a test fails, the mutant is killed. If none fails, it
survived: the tests do not notice that change.

The tracked source is never edited. Each mutant is written to a new temporary folder that
holds links to the rest of its package, and that folder goes first on PYTHONPATH for one
pytest run. Test output is thrown away; only the exit code is kept. Tests run as `make test`
runs them, behind the root conftest's network block, and without ANTHROPIC_API_KEY.

The changes (one at a time):

    compare     == and !=, < and <=, > and >=, is and is not, in and not in, and each
                ordering test turned round (< to >=)
    and-or      `and` to `or`, `or` to `and`
    not         `not x` to `x`; a condition `x` to `not x`
    constant    True and False swapped; a number plus 1 and minus 1; None to ""
    arithmetic  + to -, - to +
    return      `return x` to `return None`; `return x` to `return not x` for a yes/no value
    guard       an `if ...: return/raise` with no else, removed
    yield       a `yield` statement, removed

Not changed: text constants, type annotations, the inside of an f-string (message text), and
any code outside the named functions.
"""

import argparse
import ast
import copy
import difflib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EQUIVALENT_FILE = Path(__file__).with_name("mutate_equivalent.json")

KILLED = "killed"
SURVIVED = "survived"
TIMED_OUT = "timed_out"


@dataclass(frozen=True)
class Unit:
    """One file to mutate: the functions to change and the tests that should notice."""

    target: str
    path: str
    functions: tuple[str, ...]
    tests: tuple[str, ...]  # tests that existed before the mutation work (2026-11-18)
    new_tests: tuple[str, ...] = ()  # tests added with it


T = "tier0/tests/"
A = "access/tests/"
UNITS: tuple[Unit, ...] = (
    Unit(
        "picker hand-off",
        "tier0/src/tier0/knowledge/answer.py",
        ("postable", "entries_for", "decide", "answer_question"),
        (
            T + "test_knowledge.py",
            T + "test_guide_picker_slack.py",
            T + "test_month_picker.py",
            T + "test_guide_screen.py",
        ),
        (T + "test_picker_properties.py",),
    ),
    Unit(
        "picker hand-off",
        "tier0/src/tier0/knowledge/picker.py",
        ("pick_schema", "parse_pick"),
        (
            T + "test_knowledge.py",
            T + "test_guide_picker_slack.py",
            T + "test_month_picker.py",
            T + "test_guide_screen.py",
        ),
        (T + "test_picker_properties.py",),
    ),
    Unit(
        "guide screen",
        "tier0/src/tier0/knowledge/guide_screen.py",
        ("_fold", "_normalise", "_ours", "flag_text", "pieces", "flag_guide"),
        (T + "test_guide_screen.py", T + "test_knowledge.py", T + "test_guide_set.py"),
        (T + "test_guide_screen_mutation.py", T + "test_guide_screen_properties.py"),
    ),
    Unit(
        "guide screen",
        "tier0/src/tier0/knowledge/screen.py",
        ("_normalise", "hand_off_signal"),
        (T + "test_knowledge.py", T + "test_guide_picker_slack.py"),
        (T + "test_guide_screen_mutation.py", T + "test_guide_screen_properties.py"),
    ),
    Unit(
        "same-day ladder",
        "tier0/src/tier0/verification/ladder.py",
        (
            "required_rung",
            "LadderDecision.__post_init__",
            "VerificationLadder.evaluate",
            "VerificationLadder._step_up_failure",
        ),
        (T + "test_ladder.py", T + "test_ladder_needles.py", T + "test_part2.py"),
        (T + "test_ladder_mutation.py", T + "test_ladder_properties.py"),
    ),
    Unit(
        "same-day ladder",
        "tier0/src/tier0/verification/context.py",
        (
            "ContextSettings.__post_init__",
            "ContextChecks.preload",
            "ContextChecks.check",
            "ContextChecks._same_day_repeat",
        ),
        (
            T + "test_ladder.py",
            T + "test_ladder_needles.py",
            T + "test_part2.py",
            T + "test_month_same_day.py",
        ),
        (T + "test_ladder_mutation.py", T + "test_ladder_properties.py"),
    ),
    Unit(
        "same-day ladder",
        "tier0/src/tier0/evals/month_same_day.py",
        ("count_senders", "ladder_settings"),
        (T + "test_month_same_day.py",),
        (T + "test_ladder_mutation.py", T + "test_ladder_properties.py"),
    ),
    Unit(
        "sealed-set guards",
        "tier0/src/tier0/sealed.py",
        ("require_unsealed",),
        (
            T + "test_sealed.py",
            T + "test_paired_run.py",
            T + "test_m3d_final.py",
            T + "test_m3d2_final.py",
            T + "test_setups.py",
            T + "test_triage_check.py",
            T + "test_part2.py",
        ),
        (T + "test_sealed_guards_properties.py",),
    ),
    Unit(
        "sealed-set guards",
        "tier0/src/tier0/knowledge/sealed.py",
        ("require_unsealed",),
        (T + "test_knowledge.py", T + "test_guide_set.py", T + "test_guide_screen.py"),
        (T + "test_sealed_guards_properties.py",),
    ),
    Unit(
        "sealed-set guards",
        "tier0/src/tier0/evals/month.py",
        ("check_budget_item", "check_unseal", "check_sealed_plan", "check_prompt_cache"),
        (
            T + "test_month.py",
            T + "test_month_prompt_cache.py",
            T + "test_month_picker.py",
            T + "test_month_same_day.py",
            T + "test_month_sealed_publish.py",
        ),
        (T + "test_sealed_guards_properties.py",),
    ),
    Unit(
        "sealed-set guards",
        "access/src/meridian_access/redteam/cases.py",
        ("_sealed_bytes", "unseal"),
        (A + "test_redteam_sealed.py", A + "test_redteam.py", A + "test_redteam_workflow.py"),
        (A + "test_redteam_unseal_mutation.py",),
    ),
    # Access-as-Code's safety rails (AAC-007), added 2026-11-19; `tests` are the ones on main
    # that day, `new_tests` the ones added with this work.
    Unit(
        "access identity matching",
        "access/src/meridian_access/match.py",
        (
            "fold",
            "norm_email",
            "_split_number",
            "_letters",
            "username_templates",
            "_Person.firsts",
            "_Person.lasts",
            "_Person.full_names",
            "_Person.schemes",
            "_name_key",
            "_email_number",
            "_Index.__init__",
            "_unique",
            "_names_agree",
            "_by_email",
            "match_accounts",
            "_company_email",
            "_apply_rules",
        ),
        (A + "test_match.py", A + "test_diff.py"),
        (A + "test_match_properties.py",),
    ),
    Unit(
        "access blast radius",
        "access/src/meridian_access/limits.py",
        ("_is_iso_date", "bucket_key", "buckets", "over_limit"),
        (A + "test_limits.py", A + "test_split.py", A + "test_approvals.py"),
        (A + "test_blast_radius_properties.py",),
    ),
    Unit(
        "access blast radius",
        "access/src/meridian_access/approvals.py",
        ("required_for", "count_approvals", "check"),
        (A + "test_limits.py", A + "test_split.py", A + "test_approvals.py"),
        (A + "test_blast_radius_properties.py",),
    ),
    Unit(
        "access blast radius",
        "access/src/meridian_access/plan.py",
        ("split_plan",),
        (A + "test_limits.py", A + "test_split.py", A + "test_approvals.py"),
        (A + "test_blast_radius_properties.py",),
    ),
    Unit(
        "access break-glass",
        "access/src/meridian_access/policy/load.py",
        ("Policy.break_glass_kind", "Policy.is_break_glass"),
        (A + "test_policy.py", A + "test_match.py", A + "test_diff.py", A + "test_apply.py"),
        (A + "test_break_glass_properties.py",),
    ),
    Unit(
        "access break-glass",
        "access/src/meridian_access/match.py",
        ("_break_glass",),
        (A + "test_match.py", A + "test_diff.py", A + "test_apply.py"),
        (A + "test_break_glass_properties.py", A + "test_match_properties.py"),
    ),
    Unit(
        "access break-glass",
        "access/src/meridian_access/diff.py",
        ("_break_glass_strings", "_touches_break_glass"),
        (A + "test_diff.py", A + "test_apply.py"),
        (A + "test_break_glass_properties.py",),
    ),
    Unit(
        "access break-glass",
        "access/src/meridian_access/apply.py",
        ("_break_glass_names", "_is_listed", "_touches_break_glass"),
        (A + "test_apply.py",),
        (A + "test_break_glass_properties.py", A + "test_apply_properties.py"),
    ),
    Unit(
        "access idempotent apply",
        "access/src/meridian_access/apply.py",
        (
            "normalise_mode",
            "apply_plan",
            "_verify",
            "_took_effect",
            "_one",
            "_defer_delete",
            "_send",
            "_assign",
            "_plan_call",
            "_step",
        ),
        (A + "test_apply.py",),
        (A + "test_apply_properties.py",),
    ),
    # Added 2026-11-19 in a later pass: the whole of build_changes, so its nested `hold` and
    # its break-glass last word (`keep`) are mutated too. `tests` are the ones on main that day
    # (with #111's), `new_tests` the ones added with this pass.
    Unit(
        "access build_changes",
        "access/src/meridian_access/diff.py",
        ("build_changes",),
        (
            A + "test_diff.py",
            A + "test_break_glass_properties.py",
            A + "test_split.py",
            A + "test_plan.py",
            A + "test_apply.py",
        ),
        (A + "test_build_changes_properties.py",),
    ),
)


@dataclass(frozen=True)
class Mutant:
    unit: Unit
    function: str
    line: int
    operator: str
    before: str  # the changed code, as one line
    after: str
    source: bytes  # the whole mutated file
    nth: int = 1  # tells apart two mutants of one function with the same before and after

    @property
    def key(self) -> str:
        """A name that survives line-number changes in the file."""
        nth = f" #{self.nth}" if self.nth > 1 else ""
        change = f"{self.before} -> {self.after}{nth}"
        return f"{self.unit.path}::{self.function}::{self.operator}::{change}"


# --- finding the mutants ----------------------------------------------------------------------

_COMPARE: dict[type[ast.cmpop], tuple[type[ast.cmpop], ...]] = {
    ast.Eq: (ast.NotEq,),
    ast.NotEq: (ast.Eq,),
    ast.Lt: (ast.LtE, ast.GtE),
    ast.LtE: (ast.Lt, ast.Gt),
    ast.Gt: (ast.GtE, ast.LtE),
    ast.GtE: (ast.Gt, ast.Lt),
    ast.Is: (ast.IsNot,),
    ast.IsNot: (ast.Is,),
    ast.In: (ast.NotIn,),
    ast.NotIn: (ast.In,),
}
_YES_NO_CALLS = ("any", "all", "isinstance")
# Fields that hold no behaviour: type annotations.
_SKIPPED_FIELDS = ("annotation", "returns", "type_params")


def _one_line(node: ast.AST) -> str:
    text = " ".join(ast.unparse(node).split())
    return text if len(text) <= 110 else text[:107] + "..."


def _negatable(node: ast.expr) -> bool:
    """A condition where `not` adds a new mutant: a comparison's swaps and a `not`'s removal
    already cover the others."""
    if isinstance(node, ast.Compare | ast.Constant):
        return False
    return not (isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not))


def _negated(node: ast.expr) -> ast.expr:
    return ast.UnaryOp(op=ast.Not(), operand=node)


def _is_guard(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.If)
        and not node.orelse
        and isinstance(node.body[-1], ast.Return | ast.Raise)
    )


def _changes(node: ast.AST, in_compare: bool = False) -> Iterator[tuple[ast.AST, str, ast.AST]]:
    """(the node to replace, the operator's name, its replacement) for every change in `node`."""
    if isinstance(node, ast.JoinedStr):
        return
    if isinstance(node, ast.Compare):
        for i, op in enumerate(node.ops):
            for other in _COMPARE.get(type(op), ()):
                changed = copy.deepcopy(node)
                changed.ops[i] = other()
                yield node, "compare", changed
    elif isinstance(node, ast.BoolOp):
        changed = copy.deepcopy(node)
        changed.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
        yield node, "and-or", changed
        for value in node.values:
            if _negatable(value):
                yield value, "not", _negated(value)
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        yield node, "not", node.operand
    elif isinstance(node, ast.BinOp | ast.AugAssign) and isinstance(node.op, ast.Add | ast.Sub):
        changed = copy.deepcopy(node)
        changed.op = ast.Sub() if isinstance(node.op, ast.Add) else ast.Add()
        yield node, "arithmetic", changed
    elif isinstance(node, ast.Constant):
        if isinstance(node.value, bool):
            yield node, "constant", ast.Constant(not node.value)
        elif isinstance(node.value, int | float):
            yield node, "constant", ast.Constant(node.value + 1)
            yield node, "constant", ast.Constant(node.value - 1)
        elif node.value is None and not in_compare:
            yield node, "constant", ast.Constant("")
    elif isinstance(node, ast.Return) and node.value is not None:
        value = node.value
        if not (isinstance(value, ast.Constant) and value.value is None):
            yield node, "return", ast.Return(ast.Constant(None))
        yes_no = isinstance(value, ast.BoolOp) or (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id in _YES_NO_CALLS
        )
        if yes_no:
            yield node, "return", ast.Return(_negated(value))
    elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Yield | ast.YieldFrom):
        yield node, "yield", ast.Pass()
    if isinstance(node, ast.stmt) and _is_guard(node):
        yield node, "guard", ast.Pass()
    if isinstance(node, ast.If | ast.While | ast.IfExp) and _negatable(node.test):
        yield node.test, "not", _negated(node.test)
    if isinstance(node, ast.comprehension):
        for test in node.ifs:
            if _negatable(test):
                yield test, "not", _negated(test)
    for name, value in ast.iter_fields(node):
        if name in _SKIPPED_FIELDS:
            continue
        children = value if isinstance(value, list) else [value]
        for child in children:
            if isinstance(child, ast.AST):
                yield from _changes(child, in_compare=isinstance(node, ast.Compare))


def _functions(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    """Every function in the file by its name; a method is `Class.method`."""
    found: dict[str, ast.FunctionDef] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            found[node.name] = node
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    found[f"{node.name}.{item.name}"] = item
    return found


def mutants_of(unit: Unit) -> list[Mutant]:
    source = (ROOT / unit.path).read_bytes()
    starts = [0]
    for line in source.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))
    functions = _functions(ast.parse(source))
    out: list[Mutant] = []
    seen: set[bytes] = set()
    counts: dict[tuple[str, str, str, str], int] = {}
    for name in unit.functions:
        if name not in functions:
            raise SystemExit(f"{unit.path} has no function {name}")
        for node, operator, changed in _changes(functions[name]):
            # ast offsets count UTF-8 bytes, so the file is cut as bytes.
            start = starts[node.lineno - 1] + node.col_offset
            end = starts[node.end_lineno - 1] + node.end_col_offset
            text = ast.unparse(changed)
            if isinstance(changed, ast.expr):
                text = f"({text})"
            mutated = source[:start] + text.encode() + source[end:]
            if mutated in seen:
                continue
            seen.add(mutated)
            compile(mutated, unit.path, "exec")  # a mutant must still be valid Python
            before, after = _one_line(node), _one_line(changed)
            if operator == "guard":
                after = "(removed)"
            count_key = (name, operator, before, after)
            counts[count_key] = counts.get(count_key, 0) + 1
            out.append(
                Mutant(unit, name, node.lineno, operator, before, after, mutated, counts[count_key])
            )
    return out


# --- running one mutant -----------------------------------------------------------------------


def _overlay(folder: Path, unit: Unit, source: bytes) -> None:
    """Build `folder` so that importing the unit's package from it gives `source` for the
    unit's file and the tracked files, through links, for everything else."""
    parts = Path(unit.path).parts
    at = parts.index("src") + 1
    here, there = folder, ROOT.joinpath(*parts[:at])
    inside = parts[at:]
    for i, part in enumerate(inside[:-1]):
        here, there = here / part, there / part
        here.mkdir()
        for entry in there.iterdir():
            if entry.name not in (inside[i + 1], "__pycache__"):
                (here / entry.name).symlink_to(entry)
    (here / inside[-1]).write_bytes(source)


def _pytest(unit: Unit, tests: Sequence[str], source: bytes, timeout: float, canary: bool) -> str:
    """Run the unit's tests against `source`: KILLED, SURVIVED or TIMED_OUT."""
    with tempfile.TemporaryDirectory(prefix="mutate-") as tmp:
        # tmp/<package dir>/src, with the package dir's other entries linked next to it, so
        # code that walks up from its own file (access/policy/load.py looks for access/policy)
        # finds what it finds in the checkout.
        parts = Path(unit.path).parts
        at = parts.index("src")
        folder = Path(tmp).joinpath(*parts[: at + 1])
        folder.mkdir(parents=True)
        for entry in ROOT.joinpath(*parts[:at]).iterdir():
            if entry.name != "src" and at > 0:
                (folder.parent / entry.name).symlink_to(entry)
        seen = Path(tmp) / "imported"
        if canary:
            # Proof that the tests import the copy: the copy writes this file when imported.
            source += f"\nopen({str(seen)!r}, 'w').close()\n".encode()
        _overlay(folder, unit, source)
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        env["PYTHONPATH"] = str(folder)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        command = [sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", *tests]
        process = subprocess.Popen(  # noqa: S603 - a fixed command: this interpreter and pytest
            command,
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            return TIMED_OUT
        if canary and not seen.exists():
            raise SystemExit(f"{unit.path}: the tests did not import the temporary copy")
        if code == 0:
            return SURVIVED
        # 1: a test failed. 2: pytest stopped, as when a mutant breaks an import.
        if code in (1, 2):
            return KILLED
        raise SystemExit(f"{unit.path}: pytest exited with {code}; check {' '.join(tests)}")


def _tests(unit: Unit, baseline: bool) -> tuple[str, ...]:
    tests = unit.tests if baseline else unit.tests + unit.new_tests
    missing = [t for t in tests if not (ROOT / t).exists()]
    if missing:
        raise SystemExit(f"missing test files: {', '.join(missing)}")
    return tests


def run(
    units: Sequence[Unit],
    *,
    baseline: bool,
    jobs: int,
    out: Path,
    only: frozenset[int] | None = None,
) -> dict[str, Any]:
    """Run the units' mutants (or only the numbers in `only`, as `list` prints them) and
    write every mutant's result to `out`."""
    equivalent = load_equivalent()
    records: list[dict[str, Any]] = []
    started = time.monotonic()
    first = 1
    for unit in units:
        mutants = mutants_of(unit)
        numbered = [(first + i, m) for i, m in enumerate(mutants)]
        first += len(mutants)
        if only is not None:
            numbered = [(n, m) for n, m in numbered if n in only]
        if not numbered:
            continue
        tests = _tests(unit, baseline)
        clean = time.monotonic()
        source = (ROOT / unit.path).read_bytes()
        if _pytest(unit, tests, source, 1800, canary=True) != SURVIVED:
            raise SystemExit(f"{unit.path}: its tests fail before any change; fix that first")
        seconds = time.monotonic() - clean
        # Many runs share the machine, so a mutant gets several times the clean run's time.
        timeout = max(120.0, 8 * seconds)
        print(f"{unit.path}: {len(numbered)} mutants, clean run {seconds:.0f} s", flush=True)

        def one(m: Mutant, tests=tests, timeout=timeout) -> str:
            return _pytest(m.unit, tests, m.source, timeout, canary=False)

        with ThreadPoolExecutor(max_workers=jobs) as pool:
            statuses = list(pool.map(one, [m for _, m in numbered]))
        for (number, m), status in zip(numbered, statuses, strict=True):
            records.append(
                {
                    "number": number,
                    "target": unit.target,
                    "file": unit.path,
                    "function": m.function,
                    "line": m.line,
                    "operator": m.operator,
                    "before": m.before,
                    "after": m.after,
                    "key": m.key,
                    "status": status,
                    "equivalent": equivalent.get(m.key),
                }
            )
    result = {
        "tool": "tools/mutate.py",
        "tests": "before" if baseline else "after",
        "seconds": round(time.monotonic() - started),
        "jobs": jobs,
        "units": [
            {
                "target": u.target,
                "file": u.path,
                "functions": u.functions,
                "tests": _tests(u, baseline),
            }
            for u in units
        ],
        "mutants": records,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    return result


# --- the tables -------------------------------------------------------------------------------


def load_equivalent() -> dict[str, str]:
    """Mutants that cannot change behaviour, or change nothing that matters: key -> reason."""
    if not EQUIVALENT_FILE.exists():
        return {}
    return json.loads(EQUIVALENT_FILE.read_text(encoding="utf-8"))


def _row(name: str, mutants: Sequence[dict[str, Any]], equivalent: dict[str, str]) -> str:
    killed = sum(m["status"] == KILLED for m in mutants)
    timed_out = sum(m["status"] == TIMED_OUT for m in mutants)
    survived = sum(m["status"] == SURVIVED for m in mutants)
    set_aside = sum(m["status"] == SURVIVED and m["key"] in equivalent for m in mutants)
    counted = len(mutants) - set_aside
    score = f"{100 * killed / counted:.1f}%" if counted else "-"
    return (
        f"| {name} | {len(mutants)} | {killed} | {survived} | {timed_out} | {set_aside} | {score} |"
    )


def table(result: dict[str, Any], equivalent: dict[str, str]) -> str:
    mutants = result["mutants"]
    lines = [
        "| Target | Mutants | Killed | Survived | Timed out | Equivalent or harmless | Score |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    targets = list(dict.fromkeys(str(m["target"]) for m in mutants))
    for target in targets:
        lines.append(_row(target, [m for m in mutants if m["target"] == target], equivalent))
    lines.append(_row("Total", mutants, equivalent))
    return "\n".join(lines)


def survivors(result: dict[str, Any], equivalent: dict[str, str]) -> str:
    """Every mutant no test killed, with its one-line reason when it has one."""
    lines = []
    for i, m in enumerate(result["mutants"], 1):
        if m["status"] == KILLED:
            continue
        note = "timed out" if m["status"] == TIMED_OUT else equivalent.get(m["key"], "NOT TRIAGED")
        lines.append(
            f"- {m.get('number', i)}. `{m['file']}:{m['line']}` `{m['function']}` "
            f"({m['operator']}): `{m['before']}` to `{m['after']}`. {note}"
        )
    return "\n".join(lines) or "None."


def render(before: dict[str, Any], after: dict[str, Any]) -> str:
    equivalent = load_equivalent()
    return "\n\n".join(
        [
            "Before (the tests on main):",
            table(before, equivalent),
            "After (with the new tests):",
            table(after, equivalent),
            "Survivors before:",
            survivors(before, equivalent),
            "Survivors after:",
            survivors(after, equivalent),
        ]
    )


# --- the command line -------------------------------------------------------------------------


def _chosen(target: str | None) -> list[Unit]:
    units = [u for u in UNITS if target is None or target in u.target or target in u.path]
    if not units:
        raise SystemExit(f"no target matches {target!r}: {sorted({u.target for u in UNITS})}")
    return units


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tools/mutate.py", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="run the mutants and write the results as JSON")
    r.add_argument("--target", help="part of a target's name or of a file's path")
    r.add_argument("--baseline", action="store_true", help="only the tests from before")
    r.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    r.add_argument("--only", help="mutant numbers from `list`, as 12,33,41")
    r.add_argument("--out", type=Path, default=None)
    for name in ("list", "show"):
        s = sub.add_parser(name)
        s.add_argument("--target")
        if name == "show":
            s.add_argument("number", type=int)
    d = sub.add_parser("render", help="print the before and after tables from two result files")
    d.add_argument("before", type=Path)
    d.add_argument("after", type=Path)
    args = p.parse_args(argv)

    if args.command == "render":
        print(render(json.loads(args.before.read_text()), json.loads(args.after.read_text())))
        return 0
    units = _chosen(args.target)
    if args.command in ("list", "show"):
        mutants = [m for u in units for m in mutants_of(u)]
        if args.command == "list":
            for i, m in enumerate(mutants, 1):
                where = f"{m.unit.path}:{m.line} {m.function}"
                print(f"{i:4} {where} [{m.operator}] {m.before} -> {m.after}")
            return 0
        m = mutants[args.number - 1]
        old = (ROOT / m.unit.path).read_text(encoding="utf-8").splitlines()
        new = m.source.decode().splitlines()
        print("\n".join(difflib.unified_diff(old, new, m.unit.path, m.key, lineterm="", n=2)))
        return 0
    only = frozenset(int(n) for n in args.only.split(",")) if args.only else None
    name = "partial" if only else "before" if args.baseline else "after"
    out = args.out or ROOT / "out" / "mutation" / f"{name}.json"
    result = run(units, baseline=args.baseline, jobs=args.jobs, out=out, only=only)
    equivalent = load_equivalent()
    print(table(result, equivalent))
    print(survivors(result, equivalent))
    not_triaged = [m for m in result["mutants"] if m["status"] != KILLED and not m["equivalent"]]
    for m in result["mutants"]:
        if m["status"] == KILLED and m["equivalent"]:
            print(f"listed as equivalent, but a test killed it: {m['key']}")
    print(f"{result['seconds']} s; results in {out}; survivors not triaged: {len(not_triaged)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
