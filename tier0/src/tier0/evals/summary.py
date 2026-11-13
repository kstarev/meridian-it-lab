"""The README's held-out v3 summary table, generated from the published comparison:

    python -m tier0.evals.summary COMPARISON.json --readme README.md

For the published v3 comparison, COMPARISON.json is
docs/evals/2026-11-12-held-out-v3-comparison.json.

It reads the comparison's .json, loads the setup reports it lists (published next to it, found by
file name), and writes the summary table and the cost table between their markers. Nothing is
re-run or re-scored, and the other generated README blocks are left as they are.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from tier0.evals import compare_many


def _report_paths(comparison: Path) -> list[Path]:
    data = json.loads(comparison.read_text())
    return [comparison.parent / Path(p).name for p in data["reports"]]


def load_raw(comparison: Path) -> list[dict[str, Any]]:
    return [json.loads(path.read_text()) for path in _report_paths(comparison)]


def load_reports(comparison: Path) -> list[dict[str, Any]]:
    return [compare_many.view(raw) for raw in load_raw(comparison)]


def _checked(comparison: Path, root: Path) -> tuple[list[dict[str, Any]], list[str], str]:
    reports = load_reports(comparison)
    compare_many.check_comparable(reports)
    compare_many.require_every_setup(reports)
    links = [
        path.with_suffix(".md").resolve().relative_to(root.resolve()).as_posix()
        for path in _report_paths(comparison)
    ]
    comparison_link = comparison.with_suffix(".md").resolve().relative_to(root.resolve()).as_posix()
    return reports, links, comparison_link


def render(comparison: Path, root: Path) -> str:
    reports, links, comparison_link = _checked(comparison, root)
    return compare_many.summary_block(reports, links, comparison_link)


def render_cost(comparison: Path, root: Path) -> str:
    reports, links, comparison_link = _checked(comparison, root)
    run_counts = [sum(t["runs"] for t in raw["tickets"]) for raw in load_raw(comparison)]
    return compare_many.cost_block(reports, run_counts, links, comparison_link)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tier0.evals.summary", description=__doc__)
    parser.add_argument("comparison", type=Path)
    parser.add_argument("--readme", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        summary = render(args.comparison, args.readme.parent)
        cost = render_cost(args.comparison, args.readme.parent)
    except compare_many.NotComparableError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    text = compare_many.replace_summary_block(args.readme.read_text(), summary)
    args.readme.write_text(compare_many.replace_cost_block(text, cost))
    print(f"readme: {args.readme}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
