"""Every name of a protected break-glass account, worked out once (AAC-007, AAC-018).

Matching, the diff's last word and apply's final guard all ask this module, so they agree.

- **A name is protected by the policy** when, in either of its forms, the policy lists it or a
  pattern fully matches it (`Policy.break_glass_kind`). The two forms of a name are the same:
  a bare username `x` is also `x@meridian.example`, and a company address is also its bare
  username. As written comes first, so listed beats pattern only within one form.
- **Names are compared as matching compares them:** spaces around a name are dropped and its
  case is folded with `str.casefold`, so `OPS`, `ops` and `op` with a long s are one name.
- **An account is every name linked by a record IT controls** (the proposed amendment to
  AAC-018, gap G7). IT sets the names on an Okta user (its login and email, and the login of
  each app assignment) and on a Google user (its primary address and aliases, from the admin
  directory). Two names on one such record are one account.
- **Any other record links nothing.** A Slack user's userName and profile email, and an export
  row, can be set by the person or an app admin. Such a record is break-glass when it carries a
  protected name, so nothing changes it, but its other names are not protected through it. Each
  one that carries a protected name and another name is listed (`unlinked`), and the plan warns.
- **An account is protected** when any of its names is protected by the policy. Then every name
  it goes by is protected, in both forms, and every record that carries one of them is
  break-glass. The kind of a protected account, and of each of its records, is "listed" if the
  policy lists any of its names, else "pattern".
- **A break-glass record is protected by its own key too** (system, app id, account): a change
  that names it is dropped even if its account name is not a protected name.

`evidence` says how a record is protected: its own name, another of its own fields, or another
name of its account.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from meridian_access.models import Account
from meridian_access.policy.load import Policy
from meridian_dataset.access.model import DOMAIN

_HOW = {"listed": "is listed in the break-glass policy", "pattern": "matches a break-glass pattern"}

RecordKey = tuple[str, str, str]  # (system, app id, a form of the account), as compared


def name_forms(name: str) -> tuple[str, ...]:
    """The forms of one name, as compared: spaces dropped and case folded, then its other form
    (a bare username gets the company domain, a company address gives its bare username). A
    local part with spaces around it or an `@` in it is no username, so it has no other form."""
    folded = name.strip().casefold()
    if not folded:
        return ()
    if "@" not in folded:
        return (folded, f"{folded}@{DOMAIN}")
    local, _, domain = folded.rpartition("@")
    if domain == DOMAIN and local and "@" not in local and local == local.strip():
        return (folded, local)
    return (folded,)


def policy_kind(policy: Policy, name: str) -> str | None:
    """The policy's kind ("listed" or "pattern") for the first form of `name` it protects, as
    written first, else None."""
    for form in name_forms(name):
        if kind := policy.break_glass_kind(form):
            return kind
    return None


def it_controlled(a: Account) -> bool:
    """True for a record whose names IT sets: an Okta user or app assignment, a Google user."""
    system = a.system.strip().casefold()
    return system == "okta" or (system == "google" and not a.app_id.strip())


def _names_of(a: Account) -> tuple[str, ...]:
    return (a.account, a.email, *a.aliases)


def _keys(system: str, app_id: str, account: str) -> list[RecordKey]:
    s, app = system.strip().casefold(), app_id.strip().casefold()
    return [(s, app, form) for form in name_forms(account)]


@dataclass(frozen=True, slots=True)
class Unlinked:
    """A record IT does not control that carries a protected name and other names. It is
    break-glass, but its other names are not protected through it."""

    system: str  # an export row's system is its app id
    account: str
    protected: tuple[str, ...]  # the protected names it carries, as compared
    others: tuple[str, ...]  # its other names, every form, as compared

    def warning(self) -> str:
        name = "name" if len(self.protected) == 1 else "names"
        return (
            f"{self.account.strip()} ({self.system}) carries the break-glass {name} "
            f"{', '.join(self.protected)} and also {', '.join(self.others)}: not a record IT "
            "controls, so it is left alone but protects nothing else; check who set it"
        )


@dataclass(frozen=True, slots=True)
class BreakGlass:
    """The protected names in one actual state: every form of every name of every protected
    account, each with the account's kind; the break-glass records by key; and the records IT
    does not control that would have linked a protected name to another."""

    policy: Policy
    names: Mapping[str, str]
    records: frozenset[RecordKey] = frozenset()  # every break-glass record, by key
    # every protected name -> a name of its account that the policy itself protects
    via: Mapping[str, str] = MappingProxyType({})
    unlinked: tuple[Unlinked, ...] = ()

    def protects(self, name: str, *, system: str = "", app_id: str = "") -> bool:
        """True if `name` is a name of a protected account, or the policy protects it, or (with
        `system`) it is the account of a break-glass record in that system and app."""
        if any(form in self.names for form in name_forms(name)):
            return True
        if system and any(k in self.records for k in _keys(system, app_id, name)):
            return True
        return policy_kind(self.policy, name) is not None

    def account_kind(self, a: Account) -> str | None:
        """The break-glass kind of one record: the kind of the protected account it belongs to.
        A record that was not in the state this was built from gets the policy's word on its
        own names (login, email, aliases, in that order)."""
        for value in _names_of(a):
            for form in name_forms(value):
                if form in self.names:
                    return self.names[form]
        for value in _names_of(a):
            if kind := policy_kind(self.policy, value):
                return kind
        return None

    def evidence(self, a: Account) -> str | None:
        """How a record is protected, for the plan: its own name is in the policy, another of
        its own fields is, or another name of its account is. None if it is not protected."""
        kind = self.account_kind(a)
        if kind is None:
            return None
        if policy_kind(self.policy, a.account) == kind:
            return f"{kind} in the break-glass policy"
        for field, values in (("email", (a.email,)), ("alias", a.aliases)):
            for value in values:
                if policy_kind(self.policy, value) == kind:
                    return f"its {field} {value.strip()} {_HOW[kind]}"
        # else the kind came from `names`, and every protected name is in `via`
        via = next(self.via[f] for v in _names_of(a) for f in name_forms(v) if f in self.via)
        return f"another name of its account, {via}, {_HOW[kind]}"


def break_glass(policy: Policy, accounts: Iterable[Account]) -> BreakGlass:
    """Resolve every record to its account and collect the names of the protected ones."""
    records = list(accounts)
    parent: dict[str, str] = {}

    def root(form: str) -> str:
        while parent[form] != form:
            parent[form] = parent[parent[form]]
            form = parent[form]
        return form

    for a in records:
        values = _names_of(a)
        if it_controlled(a):
            linked = [[f for value in values for f in name_forms(value)]]
        else:  # each name's two forms are one name; the names are not linked to each other
            linked = [list(name_forms(value)) for value in values]
        for forms in linked:
            for f in forms:
                parent.setdefault(f, f)
            for f in forms[1:]:
                first, other = root(forms[0]), root(f)
                if first != other:
                    parent[max(first, other)] = min(first, other)
    members: dict[str, list[str]] = {}
    for form in parent:
        members.setdefault(root(form), []).append(form)
    names: dict[str, str] = {}
    via: dict[str, str] = {}
    for group in members.values():
        kinds = {policy.break_glass_kind(f) for f in group} - {None}
        if kinds:
            kind = "listed" if "listed" in kinds else "pattern"
            names.update(dict.fromkeys(group, kind))
            via.update(
                dict.fromkeys(group, min(f for f in group if policy.break_glass_kind(f) == kind))
            )
    keys: set[RecordKey] = set()
    unlinked: dict[tuple[str, str, str], Unlinked] = {}
    for a in records:
        carried = [v for v in _names_of(a) if any(f in names for f in name_forms(v))]
        if not carried:
            continue
        keys.update(_keys(a.system, a.app_id, a.account))
        if it_controlled(a):
            continue
        others = sorted({f for v in _names_of(a) for f in name_forms(v)} - names.keys())
        if others:
            protected = tuple(dict.fromkeys(v.strip().casefold() for v in carried))
            u = Unlinked(a.system, a.account, protected, tuple(others))
            unlinked.setdefault((a.system, a.app_id, a.account.strip().casefold()), u)
    return BreakGlass(
        policy,
        MappingProxyType(names),
        frozenset(keys),
        MappingProxyType(via),
        tuple(unlinked[k] for k in sorted(unlinked)),
    )
