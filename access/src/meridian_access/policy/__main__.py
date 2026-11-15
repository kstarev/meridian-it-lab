"""python -m meridian_access.policy build|seed-titles [--check]."""

import argparse
import sys
from pathlib import Path

from meridian_access.policy.build import build_from_public, check_files
from meridian_access.policy.load import default_policy_dir
from meridian_access.policy.seed import seed_title_map


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m meridian_access.policy")
    parser.add_argument("command", choices=["build", "seed-titles"])
    parser.add_argument("--check", action="store_true", help="exit 1 if the files differ")
    parser.add_argument("--dir", type=Path, default=None, help="policy directory")
    args = parser.parse_args(argv)
    directory = args.dir if args.dir is not None else default_policy_dir()
    files = build_from_public() if args.command == "build" else {"title_map.yaml": seed_title_map()}
    if args.check:
        stale = check_files(directory, files)
        if stale:
            print(f"out of step: {', '.join(stale)}", file=sys.stderr)
            return 1
        print(f"in step: {', '.join(files)}")
        return 0
    for name, text in files.items():
        (directory / name).write_text(text)
        print(f"wrote {directory / name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
