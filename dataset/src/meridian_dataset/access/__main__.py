"""Command line: python -m meridian_dataset.access [--seed N] [--recipe tuning] [--out DIR].

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
from meridian_dataset.access.model import ACCESS_TUNING_SEED
from meridian_dataset.access.recipe import load_recipe


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="meridian_dataset.access", description=__doc__)
    parser.add_argument("--seed", type=int, default=ACCESS_TUNING_SEED)
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

    world = generate_access(args.seed, recipe)
    target = args.out / f"seed-{args.seed}"
    paths = write_access(world, target)
    key = world.answer_key
    print(
        f"Wrote seed {args.seed} ({recipe.name}) to {target}: {len(world.hris.records)} HRIS "
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
