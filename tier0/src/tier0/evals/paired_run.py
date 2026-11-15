"""Milestone 3b's paired run on a sealed set (ADR-038): three arms, one unsealing.

    python -m tier0.evals.paired_run held_out_v4 --out out/3b/final

On one sealed set, Haiku 5.5 and Sonnet 5.5 at k=5 are scored in three arms by the same
harness (`tier0.evals`), in one process:

    arm 1, 3a as it was:      3a's prompts, the role signal before the TKT-004 fix
    arm 2, new prompts only:  the 3b prompts, the role signal before the fix
    arm 3, 3b:                the 3b prompts, the role signal with the fix

"Fix only" is arm 2 against arm 3; "prompts plus fix" is arm 1 against arm 3. A set is
unsealed once: a marker file is written when the run starts, and a second start is refused.
`--resume` finishes a run that was cut short (the arms already written are kept, never
re-scored) and is recorded in the marker.

Both sets had their run on 2026-11-15 and are dev sets now (ADR-039). The driver refuses a
set that is no longer sealed, so the paired run can't be repeated on a spent set.
"""

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from meridian_dataset.generator import HOLDOUT_V4_SEED, HOLDOUT_V4B_SEED
from tier0 import roles, sealed
from tier0.evals import __main__ as cli

SETS = {"held_out_v4": HOLDOUT_V4_SEED, "held_out_v4b": HOLDOUT_V4B_SEED}
K = 5
# (arm, setup per model, the role signal's fix)
ARMS: tuple[tuple[str, dict[str, str], str], ...] = (
    ("arm1-3a", {"haiku": "haiku", "sonnet": "sonnet"}, "off"),
    ("arm2-new-prompts", {"haiku": "haiku-3b", "sonnet": "sonnet-3b"}, "off"),
    ("arm3-3b", {"haiku": "haiku-3b", "sonnet": "sonnet-3b"}, "on"),
)


def main(
    argv: Sequence[str] | None = None, *, run: Callable[[list[str]], int] | None = None
) -> int:
    parser = argparse.ArgumentParser(prog="tier0.evals.paired_run")
    parser.add_argument("set", choices=sorted(SETS))
    parser.add_argument("--out", type=Path, default=Path("out/3b/final"))
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    run = run or cli.main
    if args.set not in sealed.SEALED_POOLS:
        print(
            f"refused: {args.set} is not sealed. It was spent in its paired run and is a "
            "dev set now (ADR-039)",
            file=sys.stderr,
        )
        return 2
    base = args.out / args.set
    marker = base / "UNSEALED.json"
    if marker.exists() and not args.resume:
        print(f"refused: {args.set} was already unsealed for its paired run", file=sys.stderr)
        return 2
    if args.resume and not marker.exists():
        print(f"refused: nothing to resume for {args.set}", file=sys.stderr)
        return 2
    base.mkdir(parents=True, exist_ok=True)
    log = json.loads(marker.read_text()) if marker.exists() else {"set": args.set, "starts": []}
    log["starts"].append(datetime.now(UTC).isoformat(timespec="seconds"))
    marker.write_text(json.dumps(log, indent=2) + "\n")
    failed = 0
    try:
        for arm, setups, fix in ARMS:
            for model, setup in setups.items():
                out = base / arm / model
                if (out / "report.json").exists():
                    continue
                code = run(
                    [
                        "--setup", setup,
                        "--seed", str(SETS[args.set]),
                        "--k", str(K),
                        f"--final-comparison={args.set}",
                        "--tkt004-fix", fix,
                        "--out", str(out),
                    ]
                )  # fmt: skip
                failed += code != 0
    finally:
        roles.TKT004_FIX = True
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
