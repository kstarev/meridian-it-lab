"""The default search for helpers: `git grep` that leaves sealed paths out.

python tools/search.py [--open NAME ...] [-i -E -l -c -w] PATTERN [PATH ...]
"""

from __future__ import annotations

import argparse
import subprocess
import sys

from sealed_paths import REPO_ROOT, check_opened, exclude_pathspecs, is_sealed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="search.py", description=__doc__)
    parser.add_argument("--open", dest="opened", action="append", default=[], metavar="NAME")
    for flag in ("-i", "-E", "-l", "-c", "-w"):
        parser.add_argument(flag, action="store_true")
    parser.add_argument("pattern")
    parser.add_argument("paths", nargs="*")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2
    try:
        check_opened(args.opened)
    except ValueError as exc:
        print(f"search.py: {exc}", file=sys.stderr)
        return 2
    for path in args.paths:
        if is_sealed(path, args.opened):
            print(f"search.py: refusing a sealed path: {path}", file=sys.stderr)
            return 2
    flags = [f for f in ("-i", "-E", "-l", "-c", "-w") if getattr(args, f.lstrip("-"))]
    cmd = ["git", "grep", "-n", "-I", *flags, "-e", args.pattern, "--"]
    cmd += args.paths or ["."]
    cmd += exclude_pathspecs(args.opened)
    print("left out: sealed paths (held_out_*, names.py)", file=sys.stderr)
    return subprocess.run(cmd, cwd=REPO_ROOT, check=False).returncode  # noqa: S603


if __name__ == "__main__":
    sys.exit(main())
