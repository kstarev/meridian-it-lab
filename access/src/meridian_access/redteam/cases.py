"""The sealed red-team set, `held_out_redteam1`, written blind (`access/docs/red-team.md`).

A helper that saw only the brief (`access/docs/red-team-brief.md`) wrote it; the builder never
reads it. It is stored only as `sets/held_out_redteam1.json.gz`, the writer's bytes unchanged,
and their SHA-256 is pinned here. `unseal(name)` is the only way to read the cases, and it
takes the set's own name.

Nothing in this module shows the set's text. An error names a case id (or its position) and a
key, never a value, and a `Case` repr shows only ids and enums.
"""

import dataclasses
import gzip
import hashlib
import json
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

SET_NAME = "held_out_redteam1"
SET_SHA256 = "0d0dcc587b0854fd5bccd2c651494ac859823cafe591e4192a38069d61ab3ba4"
"""SHA-256 of the set's JSON, byte for byte as the blind writer wrote it."""
GZ_SHA256 = "dcef130dac9de5b2d75b60be06a885d0f226f239d724f6141b0690ec238e156a"
"""SHA-256 of the stored file (`gzip -n -9`, so no name or time in it)."""
SET_PATH = Path(__file__).resolve().parent / "sets" / f"{SET_NAME}.json.gz"
FORMAT = "aac-redteam-1"

TASKS = ("title_role", "account_owner", "plan_summary")
KINDS = ("attack", "control")
SHAPES = (
    "fake_system_note",
    "ignore_rules",
    "authority_claim",
    "other_language",
    "quoted_or_code",
    "fake_fields",
    "relayed",
    "forwarded",
)
FIELDS: dict[str, tuple[str, ...]] = {
    "title_role": ("job_title", "department", "team"),
    "account_owner": (
        "account.display_name",
        "account.given_name",
        "account.family_name",
        "account.username",
        "candidate.preferred_name",
        "candidate.department",
    ),
    "plan_summary": ("account_local", "department"),
}
GOALS: dict[str, tuple[str, ...]] = {
    "title_role": ("privileged_role", "wrong_role"),
    "account_owner": ("hide_orphan", "hide_leaver", "wrong_person", "force_none"),
    "plan_summary": ("claim_approved", "claim_no_review", "claim_nothing_removed", "hide_change"),
}
IT_ROLES = ("it-lead", "it-engineer")
STATUSES = ("active", "on_leave", "terminated")
PLANT_KINDS = {"account_local": "orphan_account", "department": "mover_department"}
# The writer's case objects have these 9 keys. (The brief's sentence said "8"; its table and
# examples list these 9, and the writer followed them. See red-team.md.)
CASE_KEYS = frozenset(
    ("id", "task", "kind", "field", "shape", "language", "input", "expected", "attack")
)
ACCOUNT_KEYS = frozenset(("given_name", "family_name", "display_name", "username"))
CANDIDATE_KEYS = frozenset(
    (
        "employee_id",
        "given_name",
        "family_name",
        "preferred_name",
        "email_local",
        "department",
        "status",
    )
)
_ID = re.compile(r"^rt-\d{3}$")
_EMPLOYEE_ID = re.compile(r"^E\d{5}$")
_LANGUAGE = re.compile(r"^[a-z]{2}$")


class SetError(ValueError):
    """The set is sealed, its seal is broken or a case breaks the format. The message names
    ids, keys and rules only, never a value from the set."""


@dataclasses.dataclass(frozen=True, slots=True, eq=False)
class Case:
    """One case. `expected` is a role id (title_role), an employee id or "none"
    (account_owner), or "faithful" (plan_summary). `goal` and `target` are None for a control;
    `target` is None for every plan_summary case. `data` is the case's `input` object, and it
    is never shown in a repr."""

    id: str
    task: str
    kind: str
    field: str
    shape: str
    language: str
    expected: str
    goal: str | None
    target: str | None
    data: Mapping[str, Any] = dataclasses.field(repr=False)

    @property
    def is_attack(self) -> bool:
        return self.kind == "attack"

    def candidates(self) -> tuple[Mapping[str, str], ...]:
        return tuple(self.data["candidates"]) if self.task == "account_owner" else ()


def _check_case(index: int, raw: object, problems: list[str]) -> Case | None:
    where = f"case #{index}"
    if not isinstance(raw, dict):
        problems.append(f"{where}: not an object")
        return None
    if isinstance(raw.get("id"), str) and _ID.match(raw["id"]):
        where = raw["id"]
    if set(raw) != CASE_KEYS:
        problems.append(f"{where}: keys")
        return None
    bad = len(problems)

    def need(ok: bool, rule: str) -> None:
        if not ok:
            problems.append(f"{where}: {rule}")

    need(where == raw["id"], "id")
    task, kind, field, shape = raw["task"], raw["kind"], raw["field"], raw["shape"]
    need(task in TASKS, "task")
    need(kind in KINDS, "kind")
    if len(problems) > bad:
        return None
    need(field in FIELDS[task], "field")
    need(shape in SHAPES if kind == "attack" else shape == "none", "shape")
    need(isinstance(raw["language"], str) and bool(_LANGUAGE.match(raw["language"])), "language")
    attack, expected, data = raw["attack"], raw["expected"], raw["input"]
    goal = target = None
    if kind == "control":
        need(attack is None, "attack is not null")
    elif isinstance(attack, dict) and set(attack) == {"goal", "target"}:
        goal, target = attack["goal"], attack["target"]
        need(goal in GOALS[task], "attack.goal")
        if task == "plan_summary":
            need(target is None, "attack.target")
        else:
            need(isinstance(target, str) and bool(target), "attack.target")
    else:
        need(False, "attack")
    expected_value = ""
    if not isinstance(data, dict) or not isinstance(expected, dict):
        need(False, "input or expected")
    elif task == "title_role":
        need(set(data) == {"job_title", "department", "team"}, "input keys")
        need(all(isinstance(data.get(k), str) and data.get(k) for k in data), "input values")
        need(set(expected) == {"role"} and isinstance(expected.get("role"), str), "expected")
        expected_value = str(expected.get("role", ""))
    elif task == "account_owner":
        need(set(data) == {"account", "candidates"}, "input keys")
        account, cands = data.get("account"), data.get("candidates")
        need(
            isinstance(account, dict)
            and set(account) == ACCOUNT_KEYS
            and all(isinstance(v, str) for v in account.values()),
            "input.account",
        )
        ok = isinstance(cands, list) and 4 <= len(cands) <= 8
        if ok:
            for c in cands:
                ok = ok and isinstance(c, dict) and set(c) == CANDIDATE_KEYS
                ok = ok and all(isinstance(v, str) for v in c.values())
                ok = ok and bool(_EMPLOYEE_ID.match(c["employee_id"])) and c["status"] in STATUSES
            ok = ok and len({c["employee_id"] for c in cands}) == len(cands)
        need(ok, "input.candidates")
        need(set(expected) == {"owner"} and isinstance(expected.get("owner"), str), "expected")
        expected_value = str(expected.get("owner", ""))
    else:
        need(set(data) == {"plant_kind", "account_local", "department"}, "input keys")
        need(data.get("plant_kind") == PLANT_KINDS.get(field), "input.plant_kind")
        other = "department" if field == "account_local" else "account_local"
        need(isinstance(data.get(field), str) and bool(data.get(field)), f"input.{field}")
        need(data.get(other) is None, f"input.{other}")
        need(expected == {"summary": "faithful"}, "expected")
        expected_value = "faithful"
    if len(problems) > bad:
        return None
    return Case(
        id=raw["id"],
        task=task,
        kind=kind,
        field=field,
        shape=shape,
        language=raw["language"],
        expected=expected_value,
        goal=goal,
        target=target,
        data=data,
    )


def parse_set(obj: object) -> tuple[Case, ...]:
    """Check the format and return the cases, sorted by id. Raises SetError listing every
    problem by id, key and rule."""
    problems: list[str] = []
    if not isinstance(obj, dict) or set(obj) != {"format", "name", "cases"}:
        raise SetError("the set must be an object with the keys format, name and cases")
    if obj["format"] != FORMAT or not isinstance(obj["cases"], list):
        raise SetError("the set's format is not aac-redteam-1")
    cases = [_check_case(i, raw, problems) for i, raw in enumerate(obj["cases"])]
    ids = [c.id for c in cases if c is not None]
    if len(set(ids)) != len(ids):
        problems.append("ids are not unique")
    if problems:
        raise SetError(f"{len(problems)} problems: " + "; ".join(problems[:20]))
    return tuple(sorted((c for c in cases if c is not None), key=lambda c: c.id))


def _sealed_bytes() -> bytes:
    data = gzip.decompress(SET_PATH.read_bytes())
    if hashlib.sha256(data).hexdigest() != SET_SHA256:
        raise SetError(f"the seal of {SET_NAME} is broken: its SHA-256 does not match")
    return data


def unseal(name: str) -> tuple[Case, ...]:
    """The sealed cases, for code that runs on them (the count-only tests, the dry run and
    the paid run). Refuses any name but the set's own."""
    if name != SET_NAME:
        raise SetError(f"the red-team set is sealed; it opens only by its own name, {SET_NAME}")
    obj = json.loads(_sealed_bytes().decode("utf-8"))
    if obj.get("name") != SET_NAME:
        raise SetError("the set's name field does not match")
    return parse_set(obj)


def planted_text(case: Case) -> str:
    """The value the case planted (brief, section 3). For a candidate field, that field of the
    attack's target or of the control's expected owner."""
    data = case.data
    if case.task == "title_role":
        return str(data[case.field])
    if case.task == "plan_summary":
        return str(data[case.field])
    group, name = case.field.split(".", 1)
    if group == "account":
        return str(data["account"][name])
    who = case.target if case.is_attack else case.expected
    for c in data["candidates"]:
        if c["employee_id"] == who:
            return str(c[name])
    raise SetError(f"{case.id}: the candidate-field plant is not on a candidate")


def has_non_latin_letter(text: str) -> bool:
    return any(c.isalpha() and not unicodedata.name(c, "").startswith("LATIN") for c in text)


def counts(cases: Sequence[Case]) -> dict[str, Counter]:
    """Enum counts only (ids, tasks, kinds, shapes, fields, goals, languages)."""
    attacks = [c for c in cases if c.is_attack]
    return {
        "task_kind": Counter(f"{c.task}/{c.kind}" for c in cases),
        "shape": Counter(c.shape for c in attacks),
        "shape_task": Counter(f"{c.shape}/{c.task}" for c in attacks),
        "goal": Counter(c.goal for c in attacks),
        "field": Counter(f"{c.task}/{c.kind}/{c.field}" for c in cases),
        "language_attack": Counter(c.language for c in attacks),
        "language_control": Counter(c.language for c in cases if not c.is_attack),
    }


def words(text: str) -> list[str]:
    """Lowercase words: runs of letters or digits (the brief's overlap rule)."""
    return re.findall(r"[^\W_]+", text.lower())


def runs(text: str, n: int = 6) -> set[tuple[str, ...]]:
    w = words(text)
    return {tuple(w[i : i + n]) for i in range(len(w) - n + 1)}


def shared_runs(texts: Iterable[str], other: str, n: int = 6) -> int:
    """How many texts share a run of n words with `other`."""
    theirs = runs(other, n)
    return sum(1 for t in texts if runs(t, n) & theirs)
