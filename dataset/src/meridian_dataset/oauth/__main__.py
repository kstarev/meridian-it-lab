"""Command line: python -m meridian_dataset.oauth [--out DIR]. Writes the OAuth tuning world."""

import argparse
from pathlib import Path

from meridian_dataset.oauth.generator import (
    OAUTH_TUNING_SEED,
    generate_oauth_world,
    write_oauth_world,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="meridian_dataset.oauth", description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("out/oauth-radar"))
    args = parser.parse_args(argv)
    world = generate_oauth_world(OAUTH_TUNING_SEED)
    target = args.out / "tuning"
    write_oauth_world(world, target)
    print(
        f"Wrote the OAuth tuning world (seed {world.seed}) to {target}: {len(world.users)} people, "
        f"{len(world.grants)} grants, {len(world.plants)} planted risky grants"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
