"""python -m meridian_access.ai compare --models haiku,sonnet,opus --world DIR [--k N] [--dry-run]
python -m meridian_access.ai final-check --world DIR [--k N] [--no-summaries]
python -m meridian_access.ai final --final-score held_out_a1 --out DIR [--k N] [--no-summaries]

compare: --dry-run is the default: it prints the worst-case cost and the number of calls and
makes none. `--no-dry-run` needs ACCESS_AI_LIVE=1 and ANTHROPIC_API_KEY, and runs only from the
approved GitHub workflow.

final-check: the dry check of the final score, on the tuning world only, with a fake client:
the number of calls, the upper cost and the prompt hashes. No call, no key, no money.

final: the final held-out score (milestone 7), paid and run once, only from the approved GitHub
workflow. See `meridian_access.ai.final` for everything it refuses.
"""

import argparse
import json
import os
from pathlib import Path

from meridian_access.ai import compare, final
from meridian_access.ai.pricing import model_id
from meridian_access.ai.spend import repo_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m meridian_access.ai")
    sub = parser.add_subparsers(dest="what", required=True)
    c = sub.add_parser("compare", help="compare models on the tuning world")
    c.add_argument("--models", default="haiku,sonnet,opus")
    c.add_argument("--world", type=Path, required=True)
    c.add_argument("--k", type=int, default=1, help="repeats of the whole comparison")
    c.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=True)
    d = sub.add_parser("final-check", help="dry check of the final score on the tuning world")
    d.add_argument("--world", type=Path, required=True)
    d.add_argument("--k", type=int, default=1, choices=range(1, final.MAX_K + 1))
    d.add_argument("--summaries", action=argparse.BooleanOptionalAction, default=True)
    f = sub.add_parser("final", help="the final held-out score: paid, once (milestone 7)")
    f.add_argument("--final-score", default="", metavar="NAME")
    f.add_argument("--out", type=Path, default=None, help="a directory outside the repository")
    f.add_argument("--k", type=int, default=1, choices=range(1, final.MAX_K + 1))
    f.add_argument("--summaries", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args(argv)
    if args.what == "final":
        return final.final_main(args.final_score, args.out, k=args.k, summaries=args.summaries)
    if args.what == "final-check":
        return _final_check(args.world, args.k, args.summaries)
    models = [model_id(m) for m in args.models.split(",") if m.strip()]
    if args.dry_run:
        plan = compare.dry_run(models, args.world)
        print(
            f"dry run: no calls made. {plan['calls_per_model']} calls per model per repeat "
            f"({plan['title_calls']} titles, {plan['owner_calls']} owners), k={args.k}"
        )
        for m, usd in plan["worst_case_usd"].items():
            print(f"  {m}: worst case ${usd * args.k:.4f}")
        print(f"  total worst case ${plan['worst_case_total_usd'] * args.k:.4f}")
        return 0
    if os.environ.get("ACCESS_AI_LIVE") != "1":
        print("refusing: a live run needs ACCESS_AI_LIVE=1")
        return 2
    report = compare.live_run(
        models,
        args.world,
        k=args.k,
        suggestions_path=repo_root() / "out" / "access-ai-suggestions.yaml",
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def _final_check(world: Path, k: int, summaries: bool) -> int:
    try:
        check = final.dry_check(world, k=k, summaries=summaries)
    except ValueError as err:
        print(f"refusing: {err}")
        return 2
    kinds = ", ".join(f"{n} {kind}" for kind, n in check["calls_by_kind"].items())
    per_in, per_out = check["rates_usd_per_mtok"]
    print("dry check of the final score: no calls made, no key, no money. Tuning world only.")
    print(f"  model {check['model']} at ${per_in:.2f} in / ${per_out:.2f} out per million tokens")
    print(f"  calls per repeat: {check['calls_per_repeat']} ({kinds}); k={k}: {check['calls']}")
    print(
        f"  upper cost: ${check['worst_case_usd_per_repeat']:.4f} per repeat, "
        f"${check['worst_case_usd']:.4f} for k={k}"
    )
    print("  prompt hashes (SHA-256):")
    for name, digest in check["prompt_sha256"].items():
        print(f"    {name:<16} {digest}")
    print(f"  freeze digest: {check['freeze_digest']}")
    if check["problems"]:
        print("  NOT FROZEN:")
        for issue in check["problems"]:
            print(f"    - {issue}")
        return 1
    print("  frozen: yes, every hash matches its pin")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
