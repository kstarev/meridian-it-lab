"""Identity matching, rules only: which HRIS person owns each account.

A false match is worse than no match (the planner removes access based on matches).
Rules run in a fixed order and each counts only if it gives exactly one person:
  1 exact work email (case-insensitive); 2 normalised email (a `+tag`, case and accents
  only; dots count); 3 Google aliases; 4 a Slack userName equal to the local part of a work
  email (an account with an outside-domain email also needs its names to agree); 5 a
  username in an export under any known username scheme (a number in the username must
  agree with the person's email number; with several candidates the row's name columns
  must pick exactly one, else the account is ambiguous); 6 an exact display or full name.
An account with an email at the company domain that rules 1-3 don't match stops there:
no name or username rule is tried.
Break-glass accounts are marked and never matched to a person: every record of a protected
account, under any of its names (`break_glass.py`). Superseded HRIS records (the old record of
a rehire) are never candidates.
The result is deterministic: the same input gives the same output.
"""

import collections
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from meridian_access.break_glass import break_glass
from meridian_access.desired import desired_state
from meridian_access.models import Account
from meridian_access.policy.load import Policy
from meridian_dataset.access.model import DOMAIN, HrisRecord

AccountKey = tuple[str, str, str]  # (system, app_id, account)

CONFIDENCE = {
    "exact_email": 1.0,
    "normalised_email": 0.95,
    "google_alias": 0.95,
    "slack_username": 0.9,
    "username": 0.9,
    "username_names": 0.85,
    "name": 0.7,
    "break_glass": 1.0,
}


@dataclass(frozen=True, slots=True)
class Match:
    employee_id: str  # "" for a break-glass account
    rule: str
    confidence: float
    note: str = ""  # break-glass: "listed" or "pattern"


@dataclass(frozen=True, slots=True)
class MatchResult:
    matches: dict[AccountKey, Match]
    unmatched: list[AccountKey]
    ambiguous: list[AccountKey]

    def owner(self, key: AccountKey) -> str:
        m = self.matches.get(key)
        return m.employee_id if m else ""


# Letters NFKD does not decompose; without this they would silently disappear.
_TRANSLIT = str.maketrans(
    {
        "Ø": "O",
        "ø": "o",
        "Æ": "AE",
        "æ": "ae",
        "ß": "ss",
        "Ł": "L",
        "ł": "l",
        "Đ": "D",
        "đ": "d",
        "Þ": "Th",
        "þ": "th",
        "Œ": "OE",
        "œ": "oe",
    }
)


def fold(text: str) -> str:
    """Lowercase, accents removed, ASCII only (Ø, ß, Æ and friends transliterated)."""
    text = text.translate(_TRANSLIT)
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()


def norm_email(email: str) -> str:
    """Case and accents folded and a `+tag` removed. Dots are kept: `a.b@` is not `ab@`."""
    local, _, domain = fold(email.strip()).partition("@")
    return f"{local.split('+', 1)[0]}@{domain}"


def _split_number(local: str) -> tuple[str, int]:
    m = re.fullmatch(r"(.*?)(\d+)", local)
    return (m[1], int(m[2])) if m else (local, 0)


def _letters(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", fold(text))


def username_templates(first: str, last: str) -> dict[str, str]:
    """Usernames a person could have under each known scheme (name -> username, no number)."""
    f, ln = _letters(first), _letters(last)
    if not f or not ln:
        return {}
    return {
        "first.last": f"{f}.{ln}",
        "flast": f"{f[0]}{ln}",
        "firstl": f"{f}{ln[0]}",
        "first.l": f"{f}.{ln[0]}",
        "first_last": f"{f}_{ln}",
        "lastf": f"{ln}{f[0]}",
        "f.last": f"{f[0]}.{ln}",
        "last.first": f"{ln}.{f}",
        "firstlast": f"{f}{ln}",
        "first_l": f"{f}_{ln[0]}",
        "last_first": f"{ln}_{f}",
    }


def username_schemes(first: str, last: str) -> set[str]:
    """Usernames a person could have under the known schemes, without any number."""
    return set(username_templates(first, last).values())


@dataclass(frozen=True, slots=True)
class _Person:
    employee_id: str
    email: str
    given: str
    preferred: str
    family: str
    start: str

    @property
    def firsts(self) -> set[str]:
        return {_letters(n) for n in (self.given, self.preferred) if _letters(n)}

    @property
    def lasts(self) -> str:
        return _letters(self.family)

    def full_names(self) -> set[str]:
        names: set[str] = set()
        for first in (self.given, self.preferred):
            if not first:
                continue
            for text in (f"{first} {self.family}", f"{self.family} {first}"):
                names.add(_name_key(text))
        return names

    def schemes(self) -> set[str]:
        out: set[str] = set()
        for first in (self.given, self.preferred):
            out |= username_schemes(first, self.family)
        return out


def _name_key(text: str) -> str:
    return " ".join(sorted(re.sub(r"[^a-z0-9 ]", " ", fold(text).replace("'", "")).split()))


def _email_number(person: _Person) -> int:
    return _split_number(person.email.split("@")[0].casefold())[1]


class _Index:
    def __init__(self, people: Sequence[_Person]) -> None:
        self.people = list(people)
        self.by_email: dict[str, list[_Person]] = collections.defaultdict(list)
        self.by_norm: dict[str, list[_Person]] = collections.defaultdict(list)
        self.by_local: dict[str, list[_Person]] = collections.defaultdict(list)
        self.by_scheme: dict[str, list[_Person]] = collections.defaultdict(list)
        self.by_name: dict[str, list[_Person]] = collections.defaultdict(list)
        for p in self.people:
            email = p.email.casefold()
            local = email.partition("@")[0]
            self.by_email[email].append(p)
            self.by_norm[norm_email(email)].append(p)
            self.by_local[local].append(p)
            for s in p.schemes():
                self.by_scheme[s].append(p)
            for n in p.full_names():
                self.by_name[n].append(p)


def _unique(found: Iterable[_Person]) -> list[_Person]:
    seen: dict[str, _Person] = {}
    for p in found:
        seen[p.employee_id] = p
    return list(seen.values())


def _row_names(a: Account) -> tuple[str, str, str]:
    return _letters(a.given), _letters(a.family), a.display


def _names_agree(p: _Person, a: Account) -> bool | None:
    """True/False when the account carries names that agree/disagree with the person;
    None when it carries none."""
    first, last, display = _row_names(a)
    verdicts: list[bool] = []
    if first or last:
        ok = True
        if last:
            ok = ok and last == p.lasts
        if first:
            # exact, or a single initial ("A" ~ "Alice"); never a longer prefix
            ok = ok and (
                first in p.firsts or (len(first) == 1 and any(f[0] == first for f in p.firsts))
            )
        verdicts.append(ok)
    if display.strip():
        verdicts.append(_name_key(display) in p.full_names())
    if not verdicts:
        return None
    return all(verdicts)


def _by_email(index: _Index, email: str) -> tuple[list[_Person], list[_Person]]:
    """(exact candidates, normalised candidates) for one address."""
    return (
        _unique(index.by_email.get(email.casefold(), ())),
        _unique(index.by_norm.get(norm_email(email), ())),
    )


def match_accounts(
    accounts: Sequence[Account],
    hris_records: Iterable[HrisRecord],
    policy: Policy,
) -> MatchResult:
    records = tuple(hris_records)
    superseded = {
        p.employee_id
        for p in desired_state(records, (), policy).people
        if p.category == "superseded"
    }
    people = [
        _Person(
            r.employee_id,
            r.work_email,
            r.given_name,
            r.preferred_name,
            r.family_name,
            r.start_date,
        )
        for r in sorted(records, key=lambda r: r.employee_id)
        if r.employee_id not in superseded
    ]
    index = _Index(people)
    primary_of_alias: dict[str, str] = {}
    for a in accounts:
        for alias in a.aliases:
            primary_of_alias[alias.casefold()] = a.email or a.account

    protected = break_glass(policy, accounts)
    matches: dict[AccountKey, Match] = {}
    unmatched: list[AccountKey] = []
    ambiguous: list[AccountKey] = []
    for a in sorted(accounts, key=lambda x: (x.system, x.app_id, x.account, x.source_ref)):
        key = a.key
        if key in matches or key in unmatched or key in ambiguous:
            continue  # the same account twice (e.g. a repeated export row)
        kind = protected.account_kind(a)
        if kind:
            matches[key] = Match("", "break_glass", CONFIDENCE["break_glass"], kind)
            continue
        found, many = _apply_rules(index, a, primary_of_alias)
        if found:
            matches[key] = found
        elif many:
            ambiguous.append(key)
        else:
            unmatched.append(key)
    return MatchResult(matches, unmatched, ambiguous)


def _company_email(email: str) -> bool:
    return email.strip().casefold().rpartition("@")[2] == DOMAIN


def _apply_rules(
    index: _Index, a: Account, primary_of_alias: dict[str, str]
) -> tuple[Match | None, bool]:
    many = False

    def take(found: list[_Person], rule: str) -> Match | None:
        nonlocal many
        if len(found) == 1:
            return Match(found[0].employee_id, rule, CONFIDENCE[rule])
        if len(found) > 1:
            many = True
        return None

    email = a.email or (a.account if "@" in a.account else "")
    if email:
        exact, norm = _by_email(index, email)
        if (m := take(exact, "exact_email")) or (m := take(norm, "normalised_email")):
            return m, many
        # 3: aliases, of this Google user or of the Google user this address is an alias of
        for alias in a.aliases:
            exact, norm = _by_email(index, alias)
            if (m := take(exact, "google_alias")) or (m := take(norm, "google_alias")):
                return m, many
        primary = primary_of_alias.get(email.casefold())
        if primary and primary.casefold() != email.casefold():
            exact, norm = _by_email(index, primary)
            if (m := take(exact, "google_alias")) or (m := take(norm, "google_alias")):
                return m, many
        if _company_email(email):
            return None, many  # a company address that rules 1-3 don't place: stop here
    if a.system == "slack":
        found = _unique(index.by_local.get(fold(a.account.strip()), ()))
        if email:  # an outside-domain email: the names have to agree as well
            found = [p for p in found if _names_agree(p, a) is True]
        if m := take(found, "slack_username"):
            return m, many
    if not email and a.app_id and a.account.strip():
        username = fold(a.account.strip())
        base, number = _split_number(username)
        candidates = _unique([*index.by_scheme.get(base, []), *index.by_scheme.get(username, [])])
        if len(candidates) == 1:
            (p,) = candidates
            verdict = _names_agree(p, a)
            if verdict is False:
                pass  # the row's names contradict the username: try the name rule below
            elif number != _email_number(p):
                return None, many  # the number in the username must agree with the email's
            else:
                return Match(p.employee_id, "username", CONFIDENCE["username"]), many
        elif len(candidates) > 1:
            agree = [p for p in candidates if _names_agree(p, a) is True]
            if len(agree) == 1:
                return Match(
                    agree[0].employee_id, "username_names", CONFIDENCE["username_names"]
                ), many
            return None, True  # the names don't pick exactly one: a person decides
    for text in (a.display, f"{a.given} {a.family}"):
        if text.strip():
            found = _unique(index.by_name.get(_name_key(text), ()))
            if m := take(found, "name"):
                return m, many
    return None, many
