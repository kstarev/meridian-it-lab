"""Command line: python -m meridian_dataset.access [--seed N] [--recipe tuning] [--out DIR].

The seed is the recipe's own when it has one.

Only the tuning recipe builds freely. Any other recipe needs --final-score=<its name>, so a
sealed world is never built by accident (AAC-005).
"""

import argparse
import collections
import hashlib
import sys
from pathlib import Path

from meridian_dataset.access.export import write_access
from meridian_dataset.access.generator import generate_access
from meridian_dataset.access.recipe import load_recipe, tuning_seed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="meridian_dataset.access", description=__doc__)
    parser.add_argument(
        "--seed", type=int, default=None, help="default: the recipe's seed, else the tuning seed"
    )
    parser.add_argument("--recipe", default="tuning")
    parser.add_argument("--final-score", default=None, metavar="NAME")
    parser.add_argument("--out", type=Path, default=Path("out/access"))
    args = parser.parse_args(argv)

    if args.recipe != "tuning" and args.final_score != args.recipe:
        print(
            f"error: recipe {args.recipe!r} is sealed; build it only with "
            f"--final-score={args.recipe}",
            file=sys.stderr,
        )
        return 2
    try:
        recipe = load_recipe(args.recipe)
    except ValueError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    seed = args.seed if args.seed is not None else tuning_seed(recipe)
    world = generate_access(seed, recipe)
    target = args.out / f"seed-{seed}"
    paths = write_access(world, target)
    key = world.answer_key
    print(
        f"Wrote seed {seed} ({recipe.name}) to {target}: {len(world.hris.records)} HRIS "
        f"records, {len(world.okta.users)} Okta users, {len(world.google_users)} Google users, "
        f"{len(world.slack_users)} Slack users, {len(world.exports)} exports, "
        f"{len(key.findings)} findings"
    )
    for kind, count in sorted(collections.Counter(f.type for f in key.findings).items()):
        print(f"  {kind}: {count}")
    digest = hashlib.sha256(paths["manifest.json"].read_bytes()).hexdigest()
    print(f"  manifest sha256 {digest[:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
