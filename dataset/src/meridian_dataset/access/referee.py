"""The blind referee comparison (AAC-009): a sealed, independently written script reads a world's
raw files and lists the problems it finds; we compare that list with the generator's answer key.

The referee is a standalone Python script, run as `python3 -I referee.py WORLD_DIR OUT_JSON`. It
reads `hris.json`, `okta.json`, `google.json`, `slack_scim.json` and `exports/*.csv`, never the
answer key, and writes `{"findings": [{type, employee_id, account, system, app_id,
expected_action}, ...]}`. Its source is stored sealed (gzip) and the builder never reads it.

`table_markdown` holds counts only. `details_markdown` holds content, so `held_out_comparison`
writes it only outside the repository, for the human who rules on each disagreement.

    python -m meridian_dataset.access.referee compare --final-score held_out_a1 [--details PATH]
"""

import argparse
import ast
import gzip
import hashlib
import json
import subprocess
import sys
import tempfile
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from meridian_dataset.access.catalog import ACTIONS, FINDING_TYPES
from meridian_dataset.access.export import write_access
from meridian_dataset.access.generator import generate_access
from meridian_dataset.access.key_checks import _norm
from meridian_dataset.access.model import AccessFinding, AccessWorld
from meridian_dataset.access.recipe import load_recipe, tuning_seed

HELD_OUT_A1_REFEREE = Path(__file__).resolve().parent / "recipes" / "held_out_a1_referee.py.gz"
# SHA-256 of the decompressed source bytes.
HELD_OUT_A1_REFEREE_SHA256 = "3e89c6592e7c6bab8b32c7b0feac85ea8a4eea5b3e742c849fe3f00fe80b09e4"

ALLOWED_IMPORTS = frozenset(
    {
        "json", "csv", "re", "datetime", "pathlib", "sys", "argparse", "collections", "io",
        "codecs", "unicodedata", "itertools", "dataclasses", "typing", "math", "string",
        "functools", "operator", "gzip", "__future__", "enum", "decimal", "calendar", "hashlib",
        "textwrap", "copy", "heapq", "bisect", "statistics",
    }
)  # fmt: skip
FORBIDDEN_NAMES = frozenset(
    {"__import__", "importlib", "exec", "eval", "compile", "subprocess", "os", "socket", "ctypes"}
)

_FIELDS = ("type", "employee_id", "account", "system", "app_id", "expected_action")
_COLUMNS = ("generator", "referee", "agree", "differ", "generator_only", "referee_only")
_KIND_ORDER = {"differ": 0, "generator_only": 1, "referee_only": 2}
_TABLE_START, _TABLE_END = "<!-- referee-table:start -->", "<!-- referee-table:end -->"

Target = tuple[str, ...]


def source_safety(source: str) -> dict[str, int]:
    """Count disallowed imports and forbidden names in a script. Counts only, never the names."""
    disallowed = forbidden = 0
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            disallowed += sum(a.name.split(".")[0] not in ALLOWED_IMPORTS for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            top = (node.module or "").split(".")[0]
            disallowed += node.level > 0 or top not in ALLOWED_IMPORTS
        elif isinstance(node, ast.Name):
            forbidden += node.id in FORBIDDEN_NAMES
        elif isinstance(node, ast.Attribute):
            forbidden += node.attr in FORBIDDEN_NAMES
    return {"disallowed_imports": disallowed, "forbidden_names": forbidden}


def load_sealed_referee(path: Path = HELD_OUT_A1_REFEREE) -> str:
    """Gunzip the sealed referee and check its digest. Never print the result."""
    data = gzip.decompress(path.read_bytes())
    if hashlib.sha256(data).hexdigest() != HELD_OUT_A1_REFEREE_SHA256:
        raise ValueError("referee digest mismatch")
    return data.decode("utf-8")


def _validated(raw: Any) -> list[dict[str, str]]:
    """The referee's findings, or ValueError naming counts only."""
    items = raw.get("findings") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        raise ValueError("referee output has no findings list")
    bad = 0
    for item in items:
        ok = isinstance(item, dict) and all(isinstance(item.get(k), str) for k in _FIELDS)
        bad += not (ok and item["type"] in FINDING_TYPES and item["expected_action"] in ACTIONS)
    if bad:
        raise ValueError(f"referee output has {bad} invalid findings of {len(items)}")
    return items


def run_referee(source: str, world_dir: Path, timeout: float = 900) -> list[dict[str, str]]:
    """Run a referee script on a raw world and return its validated findings.

    Its output is never shown: errors name counts and exit codes only.
    """
    problems = source_safety(source)
    if any(problems.values()):
        detail = ", ".join(f"{k}: {n}" for k, n in problems.items())
        raise ValueError(f"referee source is not allowed ({detail})")
    if (world_dir / "answer_key.json").exists():
        raise ValueError("the world directory holds an answer key")
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        script, out = work / "referee.py", work / "findings.json"
        script.write_text(source, encoding="utf-8")
        done = subprocess.run(  # noqa: S603 - fixed argv, source checked by source_safety
            [sys.executable, "-I", str(script), str(world_dir), str(out)],
            cwd=work,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if done.returncode != 0:
            raise RuntimeError(f"referee exited with {done.returncode}")
        if not out.exists():
            raise ValueError("referee wrote no output")
        try:
            raw = json.loads(out.read_text(encoding="utf-8"))
        except ValueError:
            raise ValueError("referee output is not valid JSON") from None
    return _validated(raw)


def raw_world(world: AccessWorld, out_dir: Path) -> Path:
    """Write a world's raw files without its answer key or manifest."""
    write_access(world, out_dir)
    for name in ("answer_key.json", "manifest.json"):
        (out_dir / name).unlink(missing_ok=True)
    return out_dir


def _target(f: Mapping[str, str]) -> Target:
    if _norm(f["system"]) == "hris":
        return ("hris", f["employee_id"].strip())
    return (_norm(f["system"]), _norm(f["app_id"]), _norm(f["account"]))


def _exact(f: Mapping[str, str]) -> tuple[Any, ...]:
    owner = "" if _norm(f["system"]) == "hris" else f["employee_id"].strip()
    return (_target(f), f["type"], f["expected_action"], owner)


@dataclass(frozen=True, slots=True)
class Comparison:
    counts: dict[str, dict[str, int]]
    disagreements: list[dict[str, Any]]

    @property
    def total_disagreements(self) -> int:
        return len(self.disagreements)


def compare(generator: Sequence[AccessFinding], referee: Sequence[Mapping[str, str]]) -> Comparison:
    """Match the two lists as multisets. Counts per finding type, plus every disagreement."""
    counts = {t: dict.fromkeys(_COLUMNS, 0) for t in FINDING_TYPES}
    gen = sorted(((asdict(f), f) for f in generator), key=lambda p: (_exact(p[0]), p[0]["day"]))
    ref = sorted(((dict(r), r) for r in referee), key=lambda p: _exact(p[0]))
    for f in generator:
        counts[f.type]["generator"] += 1
    for r in referee:
        counts[r["type"]]["referee"] += 1

    waiting: dict[tuple[Any, ...], list[int]] = defaultdict(list)
    for i, (r, _) in enumerate(ref):
        waiting[_exact(r)].append(i)
    used_ref: set[int] = set()
    left_gen: list[dict[str, Any]] = []
    for g, _ in gen:
        slot = waiting.get(_exact(g))
        if slot:
            used_ref.add(slot.pop(0))
            counts[g["type"]]["agree"] += 1
        else:
            left_gen.append(g)

    by_target: dict[Target, list[int]] = defaultdict(list)
    for i, (r, _) in enumerate(ref):
        if i not in used_ref:
            by_target[_target(r)].append(i)
    found: list[tuple[str, dict[str, Any] | None, dict[str, Any] | None]] = []
    for g in left_gen:
        slot = by_target.get(_target(g))
        if slot:
            r = ref[slot.pop(0)][0]
            counts[g["type"]]["differ"] += 1
            found.append(("differ", g, r))
        else:
            counts[g["type"]]["generator_only"] += 1
            found.append(("generator_only", g, None))
    for slot in by_target.values():
        for i in slot:
            r = ref[i][0]
            counts[r["type"]]["referee_only"] += 1
            found.append(("referee_only", None, r))

    def order(item: tuple[str, dict[str, Any] | None, dict[str, Any] | None]) -> tuple[Any, ...]:
        kind, g, r = item
        side = g or r or {}
        return (
            _KIND_ORDER[kind],
            _target(side),
            side["type"],
            side["expected_action"],
            (g or {}).get("day", 0),
            json.dumps(g, sort_keys=True),
            json.dumps(r, sort_keys=True),
        )

    disagreements = [
        {"id": f"D{n:03d}", "kind": kind, "generator": g, "referee": r}
        for n, (kind, g, r) in enumerate(sorted(found, key=order), start=1)
    ]
    return Comparison(counts, disagreements)


def table_markdown(counts: Mapping[str, Mapping[str, int]]) -> str:
    """A markdown table of the counts per finding type, with a Total row. Counts only."""
    lines = [
        "| Type | Generator key | Referee | Agree | Differ | Only in key | Only in referee |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for t, row in counts.items():
        lines.append(f"| {t} | " + " | ".join(str(row[c]) for c in _COLUMNS) + " |")
    totals = [sum(row[c] for row in counts.values()) for c in _COLUMNS]
    lines.append("| **Total** | " + " | ".join(f"**{n}**" for n in totals) + " |")
    return "\n".join(lines)


def _fields_text(finding: Mapping[str, Any] | None) -> str:
    if finding is None:
        return "  (none)"
    return "\n".join(f"  - {k}: {v!r}" for k, v in finding.items())


def details_markdown(comparison: Comparison) -> str:
    """Every disagreement with both findings in full. Holds content: keep it out of the repo."""
    lines = [f"# Referee disagreements ({comparison.total_disagreements})", ""]
    for d in comparison.disagreements:
        gen = d["generator"]
        lines += [f"## {d['id']} {d['kind']}", "", "Generator key:", _fields_text(gen)]
        if gen is not None:
            lines += [f"  - detail: {gen['detail']}", f"  - day: {gen['day']}"]
        lines += ["", "Referee:", _fields_text(d["referee"]), ""]
    return "\n".join(lines)


def read_table_block(path: Path) -> str:
    """The text between the referee-table markers in a log file, stripped."""
    text = path.read_text(encoding="utf-8")
    start = text.index(_TABLE_START) + len(_TABLE_START)
    return text[start : text.index(_TABLE_END, start)].strip()


def _repo_root() -> Path:
    for folder in Path(__file__).resolve().parents:
        pyproject = folder / "pyproject.toml"
        if pyproject.exists() and "[tool.uv.workspace]" in pyproject.read_text(encoding="utf-8"):
            return folder
    raise RuntimeError("repository root not found")


def check_details_path(path: Path) -> Path:
    """The resolved path, or ValueError if it lies inside the repository."""
    resolved = path.resolve()
    if resolved.is_relative_to(_repo_root()):
        raise ValueError("the details file holds held-out content and must be outside the repo")
    return resolved


def held_out_comparison(details_path: Path | None) -> Comparison:
    """Run the sealed referee on the held-out world and compare it with the answer key."""
    target = check_details_path(details_path) if details_path is not None else None
    source = load_sealed_referee()
    recipe = load_recipe("held_out_a1")
    world = generate_access(tuning_seed(recipe), recipe)
    with tempfile.TemporaryDirectory() as tmp:
        found = run_referee(source, raw_world(world, Path(tmp) / "world"))
    result = compare(world.answer_key.findings, found)
    if target is not None:
        target.write_text(details_markdown(result), encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="meridian_dataset.access.referee", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    cmp_parser = sub.add_parser("compare", help="compare the sealed referee with the answer key")
    cmp_parser.add_argument("--final-score", default="")
    cmp_parser.add_argument("--details", type=Path)
    args = parser.parse_args(argv)
    if args.final_score != "held_out_a1":
        print("error: pass --final-score=held_out_a1 to run the sealed comparison", file=sys.stderr)
        return 2
    try:
        result = held_out_comparison(args.details)
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as err:
        print(f"error: {type(err).__name__}: {err}", file=sys.stderr)
        return 1
    print(table_markdown(result.counts))
    print(f"{result.total_disagreements} disagreements")
    if args.details is not None:
        print(f"details written to {args.details}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
