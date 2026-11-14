"""The recipe says how many of each planted problem a world gets (AAC-005)."""

import hashlib
import json
from dataclasses import asdict, dataclass
from importlib import resources

from meridian_dataset.access.catalog import DATE_FORMATS, DEPT_NAMES, NON_SSO_COUNT


@dataclass(frozen=True, slots=True)
class MassTermination:
    events: int
    department: str
    people: int
    day: int


@dataclass(frozen=True, slots=True)
class Recipe:
    name: str
    headcount: int
    contractors: int
    orphaned_accounts: int
    orphaned_former_employees: int
    movers: int
    leavers_with_access: int
    rehires: int
    contractors_past_end: int
    leave_of_absence: int
    mass_termination: MassTermination
    drift: int
    break_glass: int
    name_changes: int
    duplicate_name_pairs: int
    export_layouts: int
    date_formats: tuple[str, ...]
    username_only_exports: int
    cp1252_exports: int


_COUNTS = (
    "headcount",
    "contractors",
    "orphaned_accounts",
    "orphaned_former_employees",
    "movers",
    "leavers_with_access",
    "rehires",
    "contractors_past_end",
    "leave_of_absence",
    "drift",
    "break_glass",
    "name_changes",
    "duplicate_name_pairs",
    "export_layouts",
    "username_only_exports",
    "cp1252_exports",
)


def _recipe_files() -> resources.abc.Traversable:
    return resources.files("meridian_dataset.access").joinpath("recipes")


def available_recipes() -> tuple[str, ...]:
    return tuple(
        sorted(
            p.name.removesuffix(".json")
            for p in _recipe_files().iterdir()
            if p.name.endswith(".json")
        )
    )


def load_recipe(name: str) -> Recipe:
    """Read recipes/<name>.json; an unknown name raises ValueError listing the known ones."""
    if name not in available_recipes():
        raise ValueError(f"unknown recipe {name!r}; available: {', '.join(available_recipes())}")
    raw = json.loads(_recipe_files().joinpath(f"{name}.json").read_text(encoding="utf-8"))
    try:
        raw["mass_termination"] = MassTermination(**raw["mass_termination"])
        raw["date_formats"] = tuple(raw["date_formats"])
        recipe = Recipe(**raw)
    except (TypeError, KeyError) as err:
        raise ValueError(f"recipe {name!r} is malformed: {err}") from err
    validate_recipe(recipe)
    return recipe


def recipe_digest(recipe: Recipe) -> str:
    """SHA-256 of the canonical JSON of the recipe (sorted keys, no spaces, UTF-8)."""
    text = json.dumps(asdict(recipe), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _count(label: str, value: object) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"recipe {label} must be a non-negative integer, got {value!r}")


def validate_recipe(recipe: Recipe) -> None:
    """Raise ValueError unless the counts add up and the choices exist."""
    for label in _COUNTS:
        _count(label, getattr(recipe, label))
    mass = recipe.mass_termination
    for label in ("events", "people", "day"):
        _count(f"mass_termination.{label}", getattr(mass, label))
    if recipe.headcount < 100:
        raise ValueError("recipe headcount must be at least 100")
    if recipe.contractors > recipe.headcount // 4:
        raise ValueError("recipe has too many contractors for its headcount")
    if recipe.orphaned_former_employees > recipe.orphaned_accounts:
        raise ValueError("orphaned_former_employees cannot exceed orphaned_accounts")
    if recipe.contractors_past_end > recipe.contractors:
        raise ValueError("contractors_past_end cannot exceed contractors")
    if not 1 <= recipe.export_layouts <= 4:
        raise ValueError("export_layouts must be between 1 and 4")
    if recipe.username_only_exports > NON_SSO_COUNT or recipe.cp1252_exports > NON_SSO_COUNT:
        raise ValueError(f"an export count cannot exceed the {NON_SSO_COUNT} exports")
    if recipe.username_only_exports and recipe.export_layouts < 4:
        raise ValueError("username-only exports need all 4 layouts (layout D)")
    if not recipe.date_formats or not set(recipe.date_formats) <= set(DATE_FORMATS):
        raise ValueError(f"date_formats must be a non-empty subset of {DATE_FORMATS}")
    if mass.events:
        if mass.department not in DEPT_NAMES:
            raise ValueError(f"mass-termination department {mass.department!r} does not exist")
        if mass.people < 1 or not 1 <= mass.day <= 29:
            raise ValueError("mass termination needs people and a day between 1 and 29")
