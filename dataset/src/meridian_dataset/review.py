"""Print one message pool's wording for a person to review: make review-pool POOL=...

For held-out pools this is meant for the human reviewer only. The agent that tunes
tier0 never runs it on a held-out pool (ADR-013, ADR-029).
"""

import argparse
import sys
from collections.abc import Sequence

from meridian_dataset.names import MESSAGE_POOLS

GROUPS = (
    ("normal", "Everyday requests"),
    ("self_described", "Self-descriptions (new starters, interns)"),
    ("duplicate", "Duplicate name ({other_email} is filled in)"),
    ("colleague", "Colleague requests ({target} / {target_guess} are filled in)"),
    ("unauthenticated", "Outsiders"),
)


def render(name: str) -> str:
    pool = MESSAGE_POOLS[name]
    lines = [f"Pool {name}"]
    for field, title in GROUPS:
        templates = getattr(pool, field)
        if not templates:
            continue
        lines += ["", f"{title}: {len(templates)}"]
        lines += [f"  {i:2d}. {t}" for i, t in enumerate(templates, 1)]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m meridian_dataset.review", description=__doc__)
    parser.add_argument("pool", choices=sorted(MESSAGE_POOLS))
    args = parser.parse_args(argv)
    print(render(args.pool))
    return 0


if __name__ == "__main__":
    sys.exit(main())
