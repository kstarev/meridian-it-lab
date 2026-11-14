"""Tools for a recipe author: python -m meridian_dataset.access.recipe_tools catalog|check|seal.

`catalog` prints the public vocabulary. `check` validates a recipe and prints counts only.
`seal` writes a checked recipe as recipes/<name>.json.gz and never prints its content.
"""

import argparse
import collections
import gzip
import json
import sys
from pathlib import Path

from meridian_dataset.access import catalog as cat
from meridian_dataset.access.generator import generate_access
from meridian_dataset.access.key_checks import check_key, total
from meridian_dataset.access.model import MONTH_START, REFERENCE_DATE, AccessWorld
from meridian_dataset.access.recipe import (
    Recipe,
    canonical_json,
    load_recipe_path,
    recipe_digest,
    tuning_seed,
)
from meridian_dataset.names import HUBS

SEAL_PREFIX = "held_out_"
_DEFAULT_DIR = Path(__file__).resolve().parent / "recipes"


def catalog_text() -> str:
    """Everything a recipe author needs: public vocabulary only, no generator internals."""
    lines = [
        "Access-as-Code recipe catalog (recipe format 2)",
        "",
        f"Reference date (the state of the world): {REFERENCE_DATE}",
        f"Simulated month starts {MONTH_START}; event and login days are 0-29 after that.",
        "",
        "Departments, roles and sizes for headcount 230",
    ]
    sizes = cat.dept_sizes(230)
    for dept in cat.DEPT_NAMES:
        lines.append(f"  {dept} ({sizes[dept]} people)")
        for role in cat.ROLES_BY_DEPT[dept]:
            lines.append(f"    {role.role_id}: {role.title}")
    lines += ["", "Apps (app id, SSO, name)"]
    for app in cat.APPS:
        lines.append(f"  {app.app_id}  {'sso' if app.sso else 'not-sso'}  {app.name}")
    lines += ["", f"Systems: {', '.join(cat.SYSTEMS)}, or an app id", "", "Countries by hub"]
    for hub, countries in HUBS.items():
        lines.append(f"  {hub}: {', '.join(countries)}")
    lines += [
        "",
        f"Export fields: {', '.join(cat.EXPORT_FIELDS)}",
        f"Delimiters: {', '.join(repr(d) for d in cat.DELIMITERS)}",
        f"Special date formats: {', '.join(cat.SPECIAL_DATES)}",
        f"Finding types: {', '.join(cat.FINDING_TYPES)}",
        f"Expected actions: {', '.join(cat.ACTIONS)}",
        f"Event kinds: {', '.join(cat.EVENT_KINDS)}",
        f"Display styles: {', '.join(cat.DISPLAY_STYLES)}",
        f"Username placeholders: {' '.join(cat.PLACEHOLDERS)}",
        f"Employment types: {', '.join(cat.EMPLOYMENT_TYPES)}",
        f"Person statuses: {', '.join(cat.STATUSES)}",
        f"Account states: {', '.join(cat.ACCOUNT_STATES)}",
        f"Account roles: {', '.join(cat.ACCOUNT_ROLES)}",
        "",
    ]
    return "\n".join(lines)


def counts_text(recipe: Recipe, world: AccessWorld) -> str:
    """Counts only: no names, accounts or app ids."""
    findings = collections.Counter(f.type for f in world.answer_key.findings)
    lines = [
        f"people: {len(world.hris.records)}",
        f"exports: {len(world.exports)}",
        f"layouts used: {len({e.layout for e in world.exports})} of {len(recipe.export_layouts)}",
        f"date formats used: {len({e.date_format for e in world.exports})} "
        f"of {len(recipe.date_formats)}",
        f"custom cases: {len(recipe.custom_cases)}",
        "answer key checks: 0 problems",
        f"findings: {sum(findings.values())}",
    ]
    lines += [f"  {kind}: {n}" for kind, n in sorted(findings.items())]
    return "\n".join(lines)


def check(path: Path) -> tuple[Recipe, AccessWorld]:
    """Validate a recipe file, build its world in memory and check its answer key.

    Raises ValueError on any problem. A failed answer-key check names the checks and their counts
    only, never the recipe's content, so it is safe to show to anyone.
    """
    recipe = load_recipe_path(path)
    world = generate_access(tuning_seed(recipe), recipe)
    problems = check_key(world, recipe)
    if total(problems):
        failed = ", ".join(f"{name}: {n}" for name, n in sorted(problems.items()) if n)
        raise ValueError(f"answer key checks failed ({failed})")
    return recipe, world


def seal(path: Path, name: str, recipes_dir: Path | None = None) -> tuple[Path, str]:
    """Check a recipe, then write it as <recipes_dir>/<name>.json.gz. Raises ValueError."""
    target_dir = recipes_dir if recipes_dir is not None else _DEFAULT_DIR
    if name == "tuning" or not name.startswith(SEAL_PREFIX):
        raise ValueError(
            f"a sealed recipe's name must start with {SEAL_PREFIX!r} and not be tuning"
        )
    if not name.removeprefix(SEAL_PREFIX).replace("_", "").isalnum():
        raise ValueError("a sealed recipe's name uses letters, digits and underscores only")
    target = target_dir / f"{name}.json.gz"
    if target.exists() or (target_dir / f"{name}.json").exists():
        raise ValueError(f"recipe {name!r} already exists; seal under a new name")
    recipe, _ = check(path)
    data = path.read_bytes()
    if path.name.endswith(".gz"):
        data = gzip.decompress(data)
    packed = gzip.compress(
        canonical_json(json.loads(data.decode("utf-8"))), compresslevel=9, mtime=0
    )
    target.write_bytes(packed)
    return target, recipe_digest(recipe)


def main(argv: list[str] | None = None, recipes_dir: Path | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="meridian_dataset.access.recipe_tools", description=__doc__
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("catalog", help="print the public vocabulary")
    check_parser = sub.add_parser("check", help="validate a recipe and print counts only")
    check_parser.add_argument("path", type=Path)
    seal_parser = sub.add_parser("seal", help="check a recipe, then write it sealed")
    seal_parser.add_argument("path", type=Path)
    seal_parser.add_argument("--name", required=True)
    args = parser.parse_args(argv)

    if args.command == "catalog":
        print(catalog_text())
        return 0
    try:
        if args.command == "check":
            recipe, world = check(args.path)
            print("ok")
            print(f"digest: {recipe_digest(recipe)}")
            print(counts_text(recipe, world))
        else:
            target, digest = seal(args.path, args.name, recipes_dir)
            print(f"sealed: {target}")
            print(f"digest: {digest}")
    except (ValueError, OSError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
