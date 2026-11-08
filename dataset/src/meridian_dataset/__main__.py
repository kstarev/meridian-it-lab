"""Command line: python -m meridian_dataset [--seed N] [--out DIR]."""

import argparse
import json
from pathlib import Path

from meridian_dataset.export import write_dataset
from meridian_dataset.generator import DEFAULT_SEED, generate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="meridian_dataset", description=__doc__)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out", type=Path, default=Path("out/dataset"))
    args = parser.parse_args(argv)

    world = generate(args.seed)
    target = args.out / f"seed-{args.seed}"
    paths = write_dataset(world, target)
    print(
        f"Wrote seed {args.seed} to {target}: {len(world.people)} people, "
        f"{len(world.tickets)} tickets, {len(world.answer_key.planted_problems)} planted problems"
    )
    hashes = json.loads(paths["manifest.json"].read_text(encoding="utf-8"))["files"]
    for name, digest in hashes.items():
        print(f"  {name}  sha256 {digest[:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
