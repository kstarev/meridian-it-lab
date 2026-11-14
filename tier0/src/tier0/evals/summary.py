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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tier0.evals import compare_many


@dataclass(frozen=True)
class _Loaded:
    views: list[dict[str, Any]]
    run_counts: list[int]
    links: list[str]
    comparison_link: str


def _report_paths(comparison: Path) -> list[Path]:
    data = json.loads(comparison.read_text())
    return [comparison.parent / Path(p).name for p in data["reports"]]


def _read(comparison: Path) -> list[dict[str, Any]]:
    return [json.loads(path.read_text()) for path in _report_paths(comparison)]


def load_raw(comparison: Path) -> list[dict[str, Any]]:
    return _read(comparison)


def load_reports(comparison: Path) -> list[dict[str, Any]]:
    return [compare_many.view(raw) for raw in _read(comparison)]


def _load(comparison: Path, root: Path) -> _Loaded:
    paths = _report_paths(comparison)
    raws = [json.loads(path.read_text()) for path in paths]
    views = [compare_many.view(raw) for raw in raws]
    compare_many.check_comparable(views)
    compare_many.require_every_setup(views)
    links = [
        path.with_suffix(".md").resolve().relative_to(root.resolve()).as_posix() for path in paths
    ]
    comparison_link = comparison.with_suffix(".md").resolve().relative_to(root.resolve()).as_posix()
    run_counts = [sum(t["runs"] for t in raw["tickets"]) for raw in raws]
    return _Loaded(views, run_counts, links, comparison_link)


def render_blocks(comparison: Path, root: Path) -> tuple[str, str]:
    loaded = _load(comparison, root)
    return (
        compare_many.summary_block(loaded.views, loaded.links, loaded.comparison_link),
        compare_many.cost_block(
            loaded.views, loaded.run_counts, loaded.links, loaded.comparison_link
        ),
    )


def render(comparison: Path, root: Path) -> str:
    return render_blocks(comparison, root)[0]


def render_cost(comparison: Path, root: Path) -> str:
    return render_blocks(comparison, root)[1]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tier0.evals.summary", description=__doc__)
    parser.add_argument("comparison", type=Path)
    parser.add_argument("--readme", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        summary, cost = render_blocks(args.comparison, args.readme.parent)
        text = compare_many.replace_summary_block(args.readme.read_text(), summary)
        text = compare_many.replace_cost_block(text, cost)
    except (ValueError, OSError) as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    args.readme.write_text(text)
    print(f"readme: {args.readme}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
