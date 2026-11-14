"""The recipe says what a world looks like and how many of each planted problem it gets (AAC-005).

Format 2: the recipe is data. It describes names, usernames, date formats, export layouts,
encodings, noise and new HR edge cases. The generator applies whatever it says. What to do
about a finding (finding types and expected actions) is a fixed public vocabulary in catalog.py.
"""

import codecs
import gzip
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from importlib import resources
from pathlib import Path
from typing import Any

from meridian_dataset.access import catalog as cat
from meridian_dataset.access.model import ACCESS_TUNING_SEED

RECIPE_FORMAT = 2
_REF_SAMPLE = date(2026, 11, 3)
_NAME_EXTRA = " '\u2019-."
_PATTERN_LITERAL = re.compile(r"[A-Za-z0-9_.+-]*")
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True, slots=True)
class MassTermination:
    events: int = 0
    department: str = ""
    people: int = 0
    day: int = 0


@dataclass(frozen=True, slots=True)
class Names:
    extra_given: tuple[str, ...] = ()
    extra_family: tuple[str, ...] = ()
    extra_share: float = 0.0
    display_styles: tuple[str, ...] = ("preferred_last",)


@dataclass(frozen=True, slots=True)
class DateFormat:
    name: str
    strftime: str = ""
    special: str = ""


@dataclass(frozen=True, slots=True)
class Column:
    header: str
    field: str


@dataclass(frozen=True, slots=True)
class Layout:
    name: str
    delimiter: str
    columns: tuple[Column, ...]
    preamble: tuple[str, ...] = ()
    footer: tuple[str, ...] = ()
    quote_all: bool = False
    role_labels: tuple[str, str] = ("member", "admin")  # (member, admin)
    status_labels: tuple[str, str] = ("active", "inactive")  # (active, inactive)

    @property
    def fields(self) -> tuple[str, ...]:
        return tuple(c.field for c in self.columns)

    @property
    def username_only(self) -> bool:
        return "username" in self.fields and "email" not in self.fields


@dataclass(frozen=True, slots=True)
class Noise:
    blank_rows_max: int = 0
    mixed_case_email_share: float = 0.0
    trailing_space_share: float = 0.0
    duplicate_row_share: float = 0.0


@dataclass(frozen=True, slots=True)
class ExportsSpec:
    username_only: int = 0
    encodings: tuple[tuple[str, int], ...] = ()  # sorted by codec name
    noise: Noise = field(default_factory=Noise)


@dataclass(frozen=True, slots=True)
class CasePerson:
    ref: str
    given_name: str
    family_name: str
    role_id: str
    employment_type: str
    country: str
    status: str
    start_date: str
    preferred_name: str = ""
    end_date: str = ""
    leave_start: str = ""
    leave_end: str = ""
    job_title: str = ""
    work_email: str = ""
    current_worker: bool = False
    true_role_id: str = ""
    standard_access: bool = False


@dataclass(frozen=True, slots=True)
class CaseEvent:
    day: int
    ref: str
    kind: str
    fields: dict[str, str] = field(default_factory=dict)
    source: str = "hr"


@dataclass(frozen=True, slots=True)
class CaseAccount:
    ref: str | None
    system: str
    account: str = ""
    state: str = "active"
    role: str = "member"
    last_login_day: int = 29
    granted_by_hand_day: int | None = None


@dataclass(frozen=True, slots=True)
class CaseFinding:
    type: str
    system: str
    expected_action: str
    day: int
    ref: str | None = None
    account: str = ""
    app_id: str = ""
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Case:
    id: str
    description: str
    people: tuple[CasePerson, ...] = ()
    events: tuple[CaseEvent, ...] = ()
    accounts: tuple[CaseAccount, ...] = ()
    findings: tuple[CaseFinding, ...] = ()


@dataclass(frozen=True, slots=True)
class Recipe:
    name: str
    headcount: int
    date_formats: tuple[DateFormat, ...]
    export_layouts: tuple[Layout, ...]
    seed: int | None = None
    format: int = RECIPE_FORMAT
    contractors: int = 0
    orphaned_accounts: int = 0
    orphaned_former_employees: int = 0
    movers: int = 0
    leavers_with_access: int = 0
    rehires: int = 0
    contractors_past_end: int = 0
    leave_of_absence: int = 0
    mass_termination: MassTermination = field(default_factory=MassTermination)
    drift: int = 0
    break_glass: int = 0
    name_changes: int = 0
    duplicate_name_pairs: int = 0
    names: Names = field(default_factory=Names)
    username_patterns: tuple[str, ...] = ("{first}.{last}{n}",)
    exports: ExportsSpec = field(default_factory=ExportsSpec)
    custom_cases: tuple[Case, ...] = ()
    digest: str = ""  # SHA-256 of the canonical JSON of the parsed object


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
)


# ---- loading ------------------------------------------------------------------------------


def _recipe_dir(directory: Path | None) -> Any:
    if directory is not None:
        return directory
    return resources.files("meridian_dataset.access").joinpath("recipes")


HELD_OUT_A1_SHA256 = "2d6cfe6625218185e0e953aff3eb4126894b79000f031d751ac04c06189deaa0"
"""The seal of held_out_a1, written blind (AAC-009). If a test fails on it, the recipe changed."""


def available_recipes(directory: Path | None = None) -> tuple[str, ...]:
    """Names of recipes/<name>.json and recipes/<name>.json.gz, sorted."""
    names = set()
    for p in _recipe_dir(directory).iterdir():
        for suffix in (".json.gz", ".json"):
            if p.name.endswith(suffix):
                names.add(p.name.removesuffix(suffix))
                break
    return tuple(sorted(names))


def canonical_json(obj: object) -> bytes:
    """Sorted keys, (",", ":") separators, UTF-8, no ASCII escaping."""
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return text.encode("utf-8")


def load_recipe(name: str, directory: Path | None = None) -> Recipe:
    """Read recipes/<name>.json or recipes/<name>.json.gz; an unknown name lists the known ones."""
    known = available_recipes(directory)
    if name not in known:
        raise ValueError(f"unknown recipe {name!r}; available: {', '.join(known)}")
    base = _recipe_dir(directory)
    plain, packed = base.joinpath(f"{name}.json"), base.joinpath(f"{name}.json.gz")
    data = plain.read_bytes() if plain.is_file() else gzip.decompress(packed.read_bytes())
    return parse_recipe(_loads(data, name))


def load_recipe_path(path: Path) -> Recipe:
    """Read a recipe from any path ending in .json or .json.gz."""
    data = path.read_bytes()
    if path.name.endswith(".gz"):
        data = gzip.decompress(data)
    return parse_recipe(_loads(data, str(path)))


def _loads(data: bytes, label: str) -> object:
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise ValueError(f"recipe {label!r} is not valid JSON: {err}") from err


def recipe_digest(recipe: Recipe) -> str:
    """SHA-256 of the canonical JSON of the parsed recipe object."""
    return recipe.digest


# ---- parsing: shapes and types only -------------------------------------------------------


def _obj(
    value: object, label: str, required: tuple[str, ...] = (), optional: tuple[str, ...] = ()
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    unknown = sorted(set(value) - set(required) - set(optional))
    if unknown:
        raise ValueError(f"{label} has unknown key(s): {', '.join(unknown)}")
    missing = [k for k in required if k not in value]
    if missing:
        raise ValueError(f"{label} is missing required key(s): {', '.join(missing)}")
    return value


def _free_obj(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _list(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    return value


def _str(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string, got {value!r}")
    return value


def _int(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{label} must be an integer, got {value!r}")
    return value


def _share(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= 1:
        raise ValueError(f"{label} must be a number from 0 to 1, got {value!r}")
    return float(value)


def _bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be true or false, got {value!r}")
    return value


def _strs(value: object, label: str) -> tuple[str, ...]:
    return tuple(_str(v, f"{label}[{i}]") for i, v in enumerate(_list(value, label)))


def _pair(value: object, label: str, keys: tuple[str, str]) -> tuple[str, str]:
    o = _obj(value, label, required=keys)
    return _str(o[keys[0]], f"{label}.{keys[0]}"), _str(o[keys[1]], f"{label}.{keys[1]}")


def _parse_names(value: object) -> Names:
    o = _obj(
        value,
        "names",
        optional=("extra_given", "extra_family", "extra_share", "display_styles"),
    )
    return Names(
        extra_given=_strs(o.get("extra_given", []), "names.extra_given"),
        extra_family=_strs(o.get("extra_family", []), "names.extra_family"),
        extra_share=_share(o.get("extra_share", 0), "names.extra_share"),
        display_styles=_strs(o.get("display_styles", ["preferred_last"]), "names.display_styles"),
    )


def _parse_date_format(value: object, i: int) -> DateFormat:
    label = f"date_formats[{i}]"
    o = _obj(value, label, required=("name",), optional=("strftime", "special"))
    return DateFormat(
        _str(o["name"], f"{label}.name"),
        _str(o.get("strftime", ""), f"{label}.strftime"),
        _str(o.get("special", ""), f"{label}.special"),
    )


def _parse_layout(value: object, i: int) -> Layout:
    label = f"export_layouts[{i}]"
    o = _obj(
        value,
        label,
        required=("name", "delimiter", "columns"),
        optional=("preamble", "footer", "quote_all", "role_labels", "status_labels"),
    )
    columns = []
    for j, c in enumerate(_list(o["columns"], f"{label}.columns")):
        co = _obj(c, f"{label}.columns[{j}]", required=("header", "field"))
        columns.append(
            Column(
                _str(co["header"], f"{label}.columns[{j}].header"),
                _str(co["field"], f"{label}.columns[{j}].field"),
            )
        )
    return Layout(
        name=_str(o["name"], f"{label}.name"),
        delimiter=_str(o["delimiter"], f"{label}.delimiter"),
        columns=tuple(columns),
        preamble=_strs(o.get("preamble", []), f"{label}.preamble"),
        footer=_strs(o.get("footer", []), f"{label}.footer"),
        quote_all=_bool(o.get("quote_all", False), f"{label}.quote_all"),
        role_labels=_pair(
            o.get("role_labels", {"member": "member", "admin": "admin"}),
            f"{label}.role_labels",
            ("member", "admin"),
        ),
        status_labels=_pair(
            o.get("status_labels", {"active": "active", "inactive": "inactive"}),
            f"{label}.status_labels",
            ("active", "inactive"),
        ),
    )


def _parse_exports(value: object) -> ExportsSpec:
    o = _obj(value, "exports", optional=("username_only", "encodings", "noise"))
    enc = _free_obj(o.get("encodings", {}), "exports.encodings")
    noise = _obj(
        o.get("noise", {}),
        "exports.noise",
        optional=(
            "blank_rows_max",
            "mixed_case_email_share",
            "trailing_space_share",
            "duplicate_row_share",
        ),
    )
    return ExportsSpec(
        username_only=_int(o.get("username_only", 0), "exports.username_only"),
        encodings=tuple(sorted((c, _int(n, f"exports.encodings[{c}]")) for c, n in enc.items())),
        noise=Noise(
            blank_rows_max=_int(noise.get("blank_rows_max", 0), "noise.blank_rows_max"),
            mixed_case_email_share=_share(
                noise.get("mixed_case_email_share", 0), "noise.mixed_case_email_share"
            ),
            trailing_space_share=_share(
                noise.get("trailing_space_share", 0), "noise.trailing_space_share"
            ),
            duplicate_row_share=_share(
                noise.get("duplicate_row_share", 0), "noise.duplicate_row_share"
            ),
        ),
    )


_PERSON_REQUIRED = (
    "ref",
    "given_name",
    "family_name",
    "role_id",
    "employment_type",
    "country",
    "status",
    "start_date",
)
_PERSON_OPTIONAL = (
    "preferred_name",
    "end_date",
    "leave_start",
    "leave_end",
    "job_title",
    "work_email",
    "current_worker",
    "true_role_id",
    "standard_access",
)


def _opt_ref(value: object, label: str) -> str | None:
    return None if value is None else _str(value, label)


def _parse_case(value: object, i: int) -> Case:
    label = f"custom_cases[{i}]"
    o = _obj(
        value,
        label,
        required=("id", "description"),
        optional=("people", "events", "accounts", "findings"),
    )
    people = []
    for j, raw in enumerate(_list(o.get("people", []), f"{label}.people")):
        pl = f"{label}.people[{j}]"
        po = _obj(raw, pl, required=_PERSON_REQUIRED, optional=_PERSON_OPTIONAL)
        kw: dict[str, Any] = {}
        for key, v in po.items():
            kw[key] = (
                _bool(v, f"{pl}.{key}")
                if key in ("current_worker", "standard_access")
                else (_str(v, f"{pl}.{key}"))
            )
        people.append(CasePerson(**kw))
    events = []
    for j, raw in enumerate(_list(o.get("events", []), f"{label}.events")):
        el = f"{label}.events[{j}]"
        eo = _obj(raw, el, required=("day", "ref", "kind"), optional=("fields", "source"))
        fields_obj = _free_obj(eo.get("fields", {}), f"{el}.fields")
        events.append(
            CaseEvent(
                day=_int(eo["day"], f"{el}.day"),
                ref=_str(eo["ref"], f"{el}.ref"),
                kind=_str(eo["kind"], f"{el}.kind"),
                fields={k: _str(v, f"{el}.fields.{k}") for k, v in fields_obj.items()},
                source=_str(eo.get("source", "hr"), f"{el}.source"),
            )
        )
    accounts = []
    for j, raw in enumerate(_list(o.get("accounts", []), f"{label}.accounts")):
        al = f"{label}.accounts[{j}]"
        ao = _obj(
            raw,
            al,
            required=("ref", "system"),
            optional=("account", "state", "role", "last_login_day", "granted_by_hand_day"),
        )
        hand = ao.get("granted_by_hand_day")
        accounts.append(
            CaseAccount(
                ref=_opt_ref(ao["ref"], f"{al}.ref"),
                system=_str(ao["system"], f"{al}.system"),
                account=_str(ao.get("account", ""), f"{al}.account"),
                state=_str(ao.get("state", "active"), f"{al}.state"),
                role=_str(ao.get("role", "member"), f"{al}.role"),
                last_login_day=_int(ao.get("last_login_day", 29), f"{al}.last_login_day"),
                granted_by_hand_day=None
                if hand is None
                else _int(hand, f"{al}.granted_by_hand_day"),
            )
        )
    findings = []
    for j, raw in enumerate(_list(o.get("findings", []), f"{label}.findings")):
        fl = f"{label}.findings[{j}]"
        fo = _obj(
            raw,
            fl,
            required=("type", "system", "expected_action", "day"),
            optional=("ref", "account", "app_id", "detail"),
        )
        findings.append(
            CaseFinding(
                type=_str(fo["type"], f"{fl}.type"),
                system=_str(fo["system"], f"{fl}.system"),
                expected_action=_str(fo["expected_action"], f"{fl}.expected_action"),
                day=_int(fo["day"], f"{fl}.day"),
                ref=_opt_ref(fo.get("ref"), f"{fl}.ref"),
                account=_str(fo.get("account", ""), f"{fl}.account"),
                app_id=_str(fo.get("app_id", ""), f"{fl}.app_id"),
                detail=_str(fo.get("detail", ""), f"{fl}.detail"),
            )
        )
    return Case(
        id=_str(o["id"], f"{label}.id"),
        description=_str(o["description"], f"{label}.description"),
        people=tuple(people),
        events=tuple(events),
        accounts=tuple(accounts),
        findings=tuple(findings),
    )


def parse_recipe(obj: object) -> Recipe:
    """Turn a parsed JSON object into a validated Recipe. Unknown keys raise ValueError."""
    o = _obj(
        obj,
        "recipe",
        required=("format", "name", "headcount", "date_formats", "export_layouts"),
        optional=(
            *_COUNTS[1:],
            "seed",
            "mass_termination",
            "names",
            "username_patterns",
            "exports",
            "custom_cases",
        ),
    )
    if o["format"] != RECIPE_FORMAT or isinstance(o["format"], bool):
        raise ValueError(f"recipe format must be {RECIPE_FORMAT}, got {o['format']!r}")
    mass = _obj(
        o.get("mass_termination", {}),
        "mass_termination",
        optional=("events", "department", "people", "day"),
    )
    seed = o.get("seed")
    recipe = Recipe(
        name=_str(o["name"], "name"),
        seed=None if seed is None else _int(seed, "seed"),
        date_formats=tuple(
            _parse_date_format(v, i) for i, v in enumerate(_list(o["date_formats"], "date_formats"))
        ),
        export_layouts=tuple(
            _parse_layout(v, i) for i, v in enumerate(_list(o["export_layouts"], "export_layouts"))
        ),
        mass_termination=MassTermination(
            events=_int(mass.get("events", 0), "mass_termination.events"),
            department=_str(mass.get("department", ""), "mass_termination.department"),
            people=_int(mass.get("people", 0), "mass_termination.people"),
            day=_int(mass.get("day", 0), "mass_termination.day"),
        ),
        names=_parse_names(o.get("names", {})),
        username_patterns=_strs(
            o.get("username_patterns", ["{first}.{last}{n}"]), "username_patterns"
        ),
        exports=_parse_exports(o.get("exports", {})),
        custom_cases=tuple(
            _parse_case(v, i)
            for i, v in enumerate(_list(o.get("custom_cases", []), "custom_cases"))
        ),
        digest=hashlib.sha256(canonical_json(obj)).hexdigest(),
        **{k: _int(o[k], k) for k in _COUNTS if k in o},
    )
    validate_recipe(recipe)
    return recipe


# ---- validation: the meaning ---------------------------------------------------------------


def format_date(d: date, fmt: DateFormat) -> str:
    """Render a date the way a date format says (works on every platform for %-d and %-m)."""
    if fmt.special == "excel_serial":
        return str((d - date(1899, 12, 30)).days)
    if fmt.special == "unix_epoch":
        return str(int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp()))
    out: list[str] = []
    pattern, i = fmt.strftime, 0
    while i < len(pattern):
        if pattern[i] == "%" and i + 1 < len(pattern):
            if pattern[i + 1] == "-" and pattern[i + 2 : i + 3] in ("d", "m"):
                out.append(str(d.day if pattern[i + 2] == "d" else d.month))
                i += 3
            else:
                out.append(pattern[i : i + 2])
                i += 2
            continue
        out.append(pattern[i])
        i += 1
    return d.strftime("".join(out))


def render_username(pattern: str, given: str, family: str, num: str) -> str:
    """Fill a username pattern. {n} is the duplicate number or an empty string."""
    first, last = cat.ascii_fold(given).lower(), cat.ascii_fold(family).lower()
    return pattern.format(first=first, last=last, f=first[:1], l=last[:1], n=num).lower()


def _count(label: str, value: object) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"recipe {label} must be a non-negative integer, got {value!r}")


def _check_name(label: str, value: str) -> None:
    ok = 1 <= len(value) <= 40 and all(
        c in _NAME_EXTRA or unicodedata.category(c)[0] in "LM" for c in value
    )
    if not ok:
        raise ValueError(
            f"{label} {value!r} must be 1-40 letters, spaces, apostrophes, hyphens or periods"
        )


def validate_recipe(recipe: Recipe) -> None:
    """Raise ValueError unless the counts add up, the choices exist and every case is sound."""
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
    if mass.events:
        if mass.department not in cat.DEPT_NAMES:
            raise ValueError(f"mass-termination department {mass.department!r} does not exist")
        if mass.people < 1 or not 1 <= mass.day <= 29:
            raise ValueError("mass termination needs people and a day between 1 and 29")
    _validate_names(recipe)
    _validate_patterns(recipe)
    _validate_dates(recipe)
    _validate_layouts(recipe)
    _validate_exports(recipe)
    _validate_cases(recipe)


def _validate_names(recipe: Recipe) -> None:
    names = recipe.names
    for given in names.extra_given:
        _check_name("names.extra_given", given)
    for family in names.extra_family:
        _check_name("names.extra_family", family)
    if not 0 <= names.extra_share <= 1:
        raise ValueError("names.extra_share must be from 0 to 1")
    if names.extra_share > 0 and not (names.extra_given or names.extra_family):
        raise ValueError("names.extra_share needs extra_given or extra_family names")
    if not names.display_styles or not set(names.display_styles) <= set(cat.DISPLAY_STYLES):
        raise ValueError(f"names.display_styles must be a non-empty subset of {cat.DISPLAY_STYLES}")


def _validate_patterns(recipe: Recipe) -> None:
    if not recipe.username_patterns:
        raise ValueError("username_patterns must have at least one pattern")
    for pattern in recipe.username_patterns:
        for token in re.findall(r"\{[^{}]*\}", pattern):
            if token not in cat.PLACEHOLDERS:
                raise ValueError(
                    f"username pattern {pattern!r} has unknown placeholder {token}; "
                    f"use {', '.join(cat.PLACEHOLDERS)}"
                )
        literal = re.sub(r"\{[^{}]*\}", "", pattern)
        if not _PATTERN_LITERAL.fullmatch(literal):
            raise ValueError(
                f"username pattern {pattern!r} has a stray brace or a character other than "
                "letters, digits, _ . - +"
            )
        if not render_username(pattern, "Ana", "Lee", ""):
            raise ValueError(f"username pattern {pattern!r} renders to nothing")


def _validate_dates(recipe: Recipe) -> None:
    if not recipe.date_formats:
        raise ValueError("date_formats must have at least one format")
    seen: set[str] = set()
    for fmt in recipe.date_formats:
        if not fmt.name or fmt.name in seen:
            raise ValueError(f"date format name {fmt.name!r} is empty or repeated")
        seen.add(fmt.name)
        if bool(fmt.strftime) == bool(fmt.special):
            raise ValueError(f"date format {fmt.name!r} needs exactly one of strftime or special")
        if fmt.special and fmt.special not in cat.SPECIAL_DATES:
            raise ValueError(
                f"date format {fmt.name!r}: special must be one of {cat.SPECIAL_DATES}"
            )
        try:
            rendered = format_date(_REF_SAMPLE, fmt)
        except ValueError as err:
            raise ValueError(f"date format {fmt.name!r} cannot render a date: {err}") from err
        if not rendered:
            raise ValueError(f"date format {fmt.name!r} renders an empty string")
    if len(recipe.date_formats) > cat.NON_SSO_COUNT:
        raise ValueError(
            f"{len(recipe.date_formats)} date formats cannot all be used by "
            f"{cat.NON_SSO_COUNT} exports"
        )


def _validate_layouts(recipe: Recipe) -> None:
    if not recipe.export_layouts:
        raise ValueError("export_layouts must have at least one layout")
    seen: set[str] = set()
    for layout in recipe.export_layouts:
        if not layout.name or layout.name in seen:
            raise ValueError(f"layout name {layout.name!r} is empty or repeated")
        seen.add(layout.name)
        if layout.delimiter not in cat.DELIMITERS:
            raise ValueError(f"layout {layout.name!r}: delimiter must be one of {cat.DELIMITERS!r}")
        if not layout.columns:
            raise ValueError(f"layout {layout.name!r} needs columns")
        for column in layout.columns:
            if column.field not in cat.EXPORT_FIELDS:
                raise ValueError(
                    f"layout {layout.name!r}: unknown field {column.field!r}; "
                    f"use one of {', '.join(cat.EXPORT_FIELDS)}"
                )
            if not column.header:
                raise ValueError(f"layout {layout.name!r} has an empty header")
        if "email" not in layout.fields and "username" not in layout.fields:
            raise ValueError(f"layout {layout.name!r} needs an email or a username column")
        for line in (*layout.preamble, *layout.footer):
            if "\n" in line or "\r" in line:
                raise ValueError(f"layout {layout.name!r}: preamble and footer lines are one line")


def _validate_exports(recipe: Recipe) -> None:
    spec, n = recipe.exports, cat.NON_SSO_COUNT
    _count("exports.username_only", spec.username_only)
    if spec.username_only > n:
        raise ValueError(f"exports.username_only cannot exceed the {n} exports")
    name_layouts = [x for x in recipe.export_layouts if x.username_only]
    plain_layouts = [x for x in recipe.export_layouts if not x.username_only]
    if name_layouts and spec.username_only < len(name_layouts):
        raise ValueError(
            f"{len(name_layouts)} username-only layout(s) cannot all be used by "
            f"{spec.username_only} username-only export(s)"
        )
    if spec.username_only and not name_layouts:
        raise ValueError("exports.username_only needs a layout with a username and no email")
    if len(plain_layouts) > n - spec.username_only:
        raise ValueError(
            f"{len(plain_layouts)} layout(s) cannot all be used by {n - spec.username_only} exports"
        )
    if not plain_layouts and spec.username_only < n:
        raise ValueError("exports without a username-only layout need a layout with an email")
    total = 0
    for codec, count in spec.encodings:
        _count(f"exports.encodings[{codec}]", count)
        try:
            info = codecs.lookup(codec)
        except LookupError as err:
            raise ValueError(f"unknown encoding {codec!r}") from err
        if not info._is_text_encoding:
            raise ValueError(f"encoding {codec!r} is not a text encoding")
        total += count
    if total > n:
        raise ValueError(f"exports.encodings add up to {total}, more than the {n} exports")
    noise = spec.noise
    _count("exports.noise.blank_rows_max", noise.blank_rows_max)
    for label in ("mixed_case_email_share", "trailing_space_share", "duplicate_row_share"):
        if not 0 <= getattr(noise, label) <= 1:
            raise ValueError(f"exports.noise.{label} must be from 0 to 1")
    if noise.mixed_case_email_share + noise.trailing_space_share > 1:
        raise ValueError("mixed_case_email_share plus trailing_space_share cannot exceed 1")


def _iso_date(case: str, label: str, value: str) -> None:
    try:
        if not _ISO.fullmatch(value):
            raise ValueError
        date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"case {case!r}: {label} {value!r} is not a YYYY-MM-DD date") from None


def finding_key(system: str) -> tuple[str, str]:
    """(finding system, app_id) for a case account's system."""
    app = cat.APP_BY_ID.get(system)
    if app is None:
        return system, ""
    return ("okta", system) if app.sso else (system, system)


def _standard_keys(role_id: str, contractor: bool) -> set[tuple[str, str]]:
    role = cat.ROLE_BY_ID[role_id]
    birthright = cat.BIRTHRIGHT_IDS[:-2] if contractor else cat.BIRTHRIGHT_IDS
    keys = {("okta", ""), ("google", ""), ("slack", "")}
    keys |= {("okta", a) for a in (*birthright, *role.sso_apps)}
    keys |= {(a, a) for a in role.other_apps}
    return keys


def _validate_cases(recipe: Recipe) -> None:
    ids: set[str] = set()
    for case in recipe.custom_cases:
        if not case.id or case.id in ids:
            raise ValueError(f"case id {case.id!r} is empty or repeated")
        ids.add(case.id)
        _validate_case(case)
    _validate_case_headcount(recipe)


def _validate_case_headcount(recipe: Recipe) -> None:
    by_dept: dict[str, int] = {}
    for case in recipe.custom_cases:
        for p in case.people:
            if p.current_worker:
                dept = cat.ROLE_BY_ID[p.role_id].department
                if dept == "IT":
                    raise ValueError(f"case {case.id!r}: current workers cannot be in IT")
                by_dept[dept] = by_dept.get(dept, 0) + 1
    sizes = cat.dept_sizes(recipe.headcount)
    for dept, n in by_dept.items():
        if sizes[dept] - n < 2 * len(cat.ROLES_BY_DEPT[dept]):
            raise ValueError(f"cases put too many current workers in {dept}")


def _validate_case(case: Case) -> None:
    cid = case.id
    people: dict[str, CasePerson] = {}
    for p in case.people:
        if not p.ref or p.ref in people:
            raise ValueError(f"case {cid!r}: person ref {p.ref!r} is empty or repeated")
        people[p.ref] = p
        _check_name(f"case {cid!r} given_name", p.given_name)
        _check_name(f"case {cid!r} family_name", p.family_name)
        if p.preferred_name:
            _check_name(f"case {cid!r} preferred_name", p.preferred_name)
        for role in (p.role_id, p.true_role_id):
            if role and role not in cat.ROLE_BY_ID:
                raise ValueError(f"case {cid!r}: role {role!r} is not in the catalog")
        if p.country not in cat.COUNTRIES:
            raise ValueError(f"case {cid!r}: country {p.country!r} is not a hub country")
        if p.employment_type not in cat.EMPLOYMENT_TYPES:
            raise ValueError(f"case {cid!r}: employment_type {p.employment_type!r} is not allowed")
        if p.status not in cat.STATUSES:
            raise ValueError(f"case {cid!r}: status {p.status!r} is not allowed")
        for label in ("start_date", "end_date", "leave_start", "leave_end"):
            value = getattr(p, label)
            if value or label == "start_date":
                _iso_date(cid, f"{p.ref} {label}", value)
        if p.status == "on_leave" and not p.leave_start:
            raise ValueError(f"case {cid!r}: person {p.ref!r} is on_leave and needs leave_start")
        if p.work_email and "@" not in p.work_email:
            raise ValueError(f"case {cid!r}: work_email {p.work_email!r} is not an address")
    for e in case.events:
        if e.ref not in people:
            raise ValueError(f"case {cid!r}: event ref {e.ref!r} is not a person in the case")
        if e.kind not in cat.EVENT_KINDS:
            raise ValueError(f"case {cid!r}: event kind {e.kind!r} is not allowed")
        if not 0 <= e.day <= 29:
            raise ValueError(f"case {cid!r}: event day {e.day} must be 0-29")
    okta_users = {p.ref for p in people.values() if p.standard_access}
    okta_users |= {a.ref for a in case.accounts if a.system == "okta" and a.ref}
    okta_unowned = {a.account for a in case.accounts if a.system == "okta" and a.ref is None}
    for a in case.accounts:
        _validate_case_account(cid, a, people, okta_users, okta_unowned)
    for f in case.findings:
        _validate_case_finding(case, f, people)


def _validate_case_account(
    cid: str,
    a: CaseAccount,
    people: dict[str, CasePerson],
    okta_users: set[str | None],
    okta_unowned: set[str],
) -> None:
    app = cat.APP_BY_ID.get(a.system)
    if a.system not in ("okta", "google", "slack") and app is None:
        raise ValueError(
            f"case {cid!r}: account system {a.system!r} is not okta, google, slack or an app id"
        )
    if a.ref is not None and a.ref not in people:
        raise ValueError(f"case {cid!r}: account ref {a.ref!r} is not a person in the case")
    if a.ref is None and not a.account:
        raise ValueError(f"case {cid!r}: an account with ref null needs an account name")
    if a.state not in cat.ACCOUNT_STATES:
        raise ValueError(f"case {cid!r}: account state {a.state!r} is not allowed")
    if a.role not in cat.ACCOUNT_ROLES:
        raise ValueError(f"case {cid!r}: account role {a.role!r} is not allowed")
    if not -1 <= a.last_login_day <= 29:
        raise ValueError(f"case {cid!r}: last_login_day must be -1 to 29")
    sso = app is not None and app.sso
    hand = a.granted_by_hand_day
    if hand is not None and (not sso or not 0 <= hand <= 29):
        raise ValueError(
            f"case {cid!r}: granted_by_hand_day (0-29) only applies to SSO app accounts"
        )
    if sso:
        if a.state != "active":
            raise ValueError(f"case {cid!r}: an SSO app access has no state of its own")
        if a.ref is not None and a.ref not in okta_users:
            raise ValueError(
                f"case {cid!r}: {a.ref!r} has no Okta user; add standard_access or an okta account"
            )
        if a.ref is None and a.account not in okta_unowned:
            raise ValueError(
                f"case {cid!r}: SSO app access for ref null needs an okta account named "
                f"{a.account!r} in the same case"
            )


def _validate_case_finding(case: Case, f: CaseFinding, people: dict[str, CasePerson]) -> None:
    cid = case.id
    if f.type not in cat.FINDING_TYPES:
        raise ValueError(f"case {cid!r}: finding type {f.type!r} is not allowed")
    if f.expected_action not in cat.ACTIONS:
        raise ValueError(f"case {cid!r}: expected_action {f.expected_action!r} is not allowed")
    if not 0 <= f.day <= 29:
        raise ValueError(f"case {cid!r}: finding day {f.day} must be 0-29")
    if f.ref is not None and f.ref not in people:
        raise ValueError(f"case {cid!r}: finding ref {f.ref!r} is not a person in the case")
    app = cat.APP_BY_ID.get(f.system)
    if f.system in ("google", "slack", "hris"):
        keys_ok = not f.app_id
    elif f.system == "okta":
        keys_ok = not f.app_id or (f.app_id in cat.APP_BY_ID and cat.APP_BY_ID[f.app_id].sso)
    elif app is not None and not app.sso:
        keys_ok = f.app_id in ("", f.system)
    else:
        raise ValueError(f"case {cid!r}: finding system {f.system!r} is not allowed")
    if not keys_ok:
        raise ValueError(
            f"case {cid!r}: finding app_id {f.app_id!r} does not fit system {f.system!r}"
        )
    if f.system == "hris" or f.expected_action == "add":
        return
    if not f.account and f.ref is None:
        raise ValueError(f"case {cid!r}: a finding needs an account or a ref")
    key = (f.system, f.app_id or (f.system if app is not None else ""))
    for a in case.accounts:
        if finding_key(a.system) != key:
            continue
        if (f.account and a.account == f.account) or (not f.account and a.ref == f.ref):
            return
    person = people.get(f.ref) if f.ref else None
    if (
        person is not None
        and not f.account
        and person.standard_access
        and key in _standard_keys(person.role_id, person.employment_type == "contractor")
    ):
        return
    raise ValueError(
        f"case {cid!r}: finding refers to an account that does not exist "
        f"(system {f.system!r}, app_id {f.app_id!r}, account {f.account!r}, ref {f.ref!r})"
    )


def tuning_seed(recipe: Recipe) -> int:
    """The seed a recipe asks for, or the tuning seed when it names none."""
    return recipe.seed if recipe.seed is not None else ACCESS_TUNING_SEED
