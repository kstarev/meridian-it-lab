"""The comparison blocks in docs/how-it-works.md, rewritten from the published comparisons:

    python -m tier0.evals.comparison_doc --doc docs/how-it-works.md [--evals-dir DIR]

Milestone 2's block (between the comparison markers) and milestone 3's (between the
comparison-m3 markers) are rebuilt from the published comparison .json files and the reports they
list, found by file name in DIR (default: evals next to the doc). Links are relative to the doc's
folder. Nothing is re-run or re-scored, nothing is written to DIR, and every text is built before
the doc is written.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from tier0.evals import compare, compare_many

M2 = "2026-11-09-held-out-v2-comparison.json"
M3 = "2026-11-12-held-out-v3-comparison.json"


_Read = tuple[dict[str, Any], list[dict[str, Any]], list[str], str]


def _read(evals_dir: Path, root: Path, comparison: str) -> _Read:
    path = evals_dir / comparison
    data = json.loads(path.read_text())
    paths = [evals_dir / Path(p).name for p in data["reports"]]
    raws = [json.loads(p.read_text()) for p in paths]
    base = root.resolve()
    links = [p.with_suffix(".md").resolve().relative_to(base).as_posix() for p in paths]
    link = path.with_suffix(".md").resolve().relative_to(base).as_posix()
    return data, raws, links, link


def render_m2(evals_dir: Path, root: Path) -> str:
    data, raws, links, link = _read(evals_dir, root, M2)
    a, b = raws
    return compare.readme_block(a, b, data["verdict"], links, link)


def render_m3(evals_dir: Path, root: Path) -> str:
    data, raws, links, link = _read(evals_dir, root, M3)
    views = [compare_many.view(r) for r in raws]
    return compare_many.readme_block(views, data["verdict"], links, link)


def main(argv: Sequence[str] | None = None) -> int:
    prog = "python -m tier0.evals.comparison_doc"
    parser = argparse.ArgumentParser(prog=prog, description=__doc__)
    parser.add_argument("--doc", type=Path, required=True)
    parser.add_argument("--evals-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    evals_dir = args.evals_dir or args.doc.parent / "evals"
    try:
        m2 = render_m2(evals_dir, args.doc.parent)
        m3 = render_m3(evals_dir, args.doc.parent)
        text = compare.replace_readme_block(args.doc.read_text(), m2)
        text = compare_many.replace_readme_block(text, m3)
    except (ValueError, OSError) as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    args.doc.write_text(text)
    print(f"doc: {args.doc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
