"""python -m meridian_access.ai compare --models haiku,sonnet,opus --world DIR [--dry-run]

--dry-run is the default: it prints the worst-case cost and the number of calls and makes
none. `--no-dry-run` needs ACCESS_AI_LIVE=1 and ANTHROPIC_API_KEY, and runs only from the
approved GitHub workflow.
"""

import argparse
import json
import os
from pathlib import Path

from meridian_access.ai import compare
from meridian_access.ai.pricing import model_id
from meridian_access.ai.spend import repo_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m meridian_access.ai")
    sub = parser.add_subparsers(dest="what", required=True)
    c = sub.add_parser("compare", help="compare models on the tuning world")
    c.add_argument("--models", default="haiku,sonnet,opus")
    c.add_argument("--world", type=Path, required=True)
    c.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args(argv)
    models = [model_id(m) for m in args.models.split(",") if m.strip()]
    if args.dry_run:
        plan = compare.dry_run(models, args.world)
        print(
            f"dry run: no calls made. {plan['calls_per_model']} calls per model "
            f"({plan['title_calls']} titles, {plan['owner_calls']} owners)"
        )
        for m, usd in plan["worst_case_usd"].items():
            print(f"  {m}: worst case ${usd:.4f}")
        print(f"  total worst case ${plan['worst_case_total_usd']:.4f}")
        return 0
    if os.environ.get("ACCESS_AI_LIVE") != "1":
        print("refusing: a live run needs ACCESS_AI_LIVE=1")
        return 2
    report = compare.live_run(
        models, args.world, suggestions_path=repo_root() / "out" / "access-ai-suggestions.yaml"
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
