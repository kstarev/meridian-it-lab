"""Which paths are sealed, and how to leave them out of a search (see docs/sealed-search-guard.md).

Stdlib only. Importable, and runnable: `python tools/sealed_paths.py` lists the pathspecs.
"""

from __future__ import annotations

import subprocess
from collections.abc import Collection
from pathlib import Path, PurePosixPath

SEALED_GLOB = "held_out_*"  # a tracked file whose basename starts with this is left out
# Source code that also holds blind wording inline. Searches leave it out; the hook does not
# block it, because it is code people edit.
EXTRA_PATHS = ("dataset/src/meridian_dataset/names.py",)
# Dev sets the owner opened by an ADR: these may be searched when named with --open.
OPENED = {
    "held_out_month1": "ADR-049",
    "held_out_month2": "ADR-052",
    "held_out_month2b": "ADR-052",
}

REPO_ROOT = Path(__file__).resolve().parent.parent
_PREFIX = SEALED_GLOB.rstrip("*")


def _stem(basename: str) -> str:
    return basename.split(".", 1)[0]


def is_sealed(path: str, opened: Collection[str] = (), *, extra: bool = True) -> bool:
    """True if a search leaves this path out: a `held_out_*` file whose set is not in `opened`,
    or one of EXTRA_PATHS. With `extra=False`, only the `held_out_*` files count."""
    posix = PurePosixPath(path.replace("\\", "/")).as_posix()
    base = posix.rsplit("/", 1)[-1]
    if base.startswith(_PREFIX) and _stem(base) not in opened:
        return True
    return extra and any(posix == p or posix.endswith("/" + p) for p in EXTRA_PATHS)


def _tracked_sealed_files() -> list[str]:
    out = subprocess.run(  # noqa: S603
        ["git", "-C", str(REPO_ROOT), "ls-files"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [p for p in out.splitlines() if p.rsplit("/", 1)[-1].startswith(_PREFIX)]


def exclude_pathspecs(opened: Collection[str] = ()) -> list[str]:
    """Git pathspecs that leave sealed paths out of a `git grep` or `git ls-files`."""
    if opened:
        specs = [f":(exclude){p}" for p in _tracked_sealed_files() if is_sealed(p, opened)]
    else:
        specs = [f":(exclude,glob)**/{SEALED_GLOB}"]
    return specs + [f":(exclude){p}" for p in EXTRA_PATHS]


def check_opened(names: Collection[str]) -> None:
    unknown = [n for n in names if n not in OPENED]
    if unknown:
        raise ValueError(f"not an opened dev set: {', '.join(unknown)} (see OPENED)")


if __name__ == "__main__":
    print("\n".join(exclude_pathspecs()))
