"""python -m meridian_access.redteam --unseal held_out_redteam1 [--models haiku,sonnet,opus]
[--k 3] [--dry-run | --no-dry-run] [--report PATH] [--details PATH]

The set is sealed: nothing runs without `--unseal` and the set's own name. `--dry-run` is the
default: it prices the paid run from the real request sizes, makes no call and needs no key.
`--no-dry-run` also needs ACCESS_AI_LIVE=1, ACCESS_CLAUDE_BUDGET_ITEM=aac-red-team and the owner's
approval of that item. Before the first call it refuses a run limit (ACCESS_CLAUDE_RUN_LIMIT_USD)
above the item's cap, and a run whose worst case doesn't fit; the ledger checks every call again.
Output is numbers only: an error after the start prints its type, never its message, because the
message could quote the set. The workflow `Access red team` is the only place the paid run runs.
"""

import argparse
import os
from pathlib import Path

from meridian_access.ai.client import SEALED_NO_CACHE
from meridian_access.ai.pricing import model_id
from meridian_access.ai.spend import ITEM_ENV
from meridian_access.redteam import harness
from meridian_access.redteam.cases import SET_NAME

DEFAULT_MODELS = "haiku,sonnet,opus"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m meridian_access.redteam")
    parser.add_argument("--unseal", default=None, metavar="NAME")
    parser.add_argument("--models", default=DEFAULT_MODELS)
    parser.add_argument("--k", type=int, default=3, help="repeats of the whole set")
    parser.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--prompt-cache", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--report", type=Path, default=None, help="ids, enums and numbers")
    parser.add_argument("--details", type=Path, default=None, help="outside the repository")
    args = parser.parse_args(argv)
    if args.prompt_cache:
        print(SEALED_NO_CACHE)
        return 2
    if args.unseal != SET_NAME:
        print(f"refusing: the red-team set is sealed; pass --unseal {SET_NAME}")
        return 2
    try:
        models = [model_id(m) for m in args.models.split(",") if m.strip()]
    except ValueError as exc:
        print(f"refusing: {exc}")
        return 2
    if not models or len(set(models)) != len(models):
        print("refusing: name each model once")
        return 2
    if args.dry_run:
        plan = harness.dry_run(args.unseal, models, k=args.k)
        print(
            f"dry run: no calls made. {plan['requests_per_model_per_repeat']} requests per "
            f"model per repeat ({', '.join(f'{n} {t}' for t, n in plan['by_task'].items())}), "
            f"k={plan['k']}, {plan['calls_total']} calls in all"
        )
        for m in models:
            print(
                f"  {m}: worst case ${plan['worst_case_usd'][m]:.4f}, "
                f"likely ${plan['likely_usd'][m]:.4f}"
            )
        print(
            f"  total: worst case ${plan['worst_case_total_usd']:.4f}, "
            f"likely ${plan['likely_total_usd']:.4f}"
        )
        return 0
    try:
        report = harness.live_run(
            args.unseal,
            models,
            k=args.k,
            item=os.environ.get(ITEM_ENV),
            report_path=args.report,
            details_path=args.details,
        )
    except harness.RunRefused as exc:  # numbers, names and paths only: nothing from the set
        print(f"refusing: {exc}")
        return 2
    except Exception as exc:  # never print the message: it could quote the set
        print(f"error: {type(exc).__name__} (message withheld)")
        return 1
    for m, entry in report["models"].items():
        s = entry["summary"]
        print(
            f"{m}: {s['repeats_finished']} repeats, attack successes {s['attack_successes']}, "
            f"force_none successes {s['force_none_successes']}, stopped by code "
            f"{s['stopped_by_code']}, no answer {s['no_answer']}, cost ${entry['cost_usd']:.4f}"
        )
    print(
        f"worst case ${report['worst_case_usd']:.4f}, run limit ${report['run_limit_usd']:.2f}, "
        f"budget item {report['budget_item']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
