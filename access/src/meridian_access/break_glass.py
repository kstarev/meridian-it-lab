"""Every name of a protected break-glass account, worked out once (AAC-007, AAC-018).

Matching, the diff's last word and apply's final guard all ask this module, so they agree.

- **A name is protected by the policy** when, in either of its forms, the policy lists it or a
  pattern fully matches it (`Policy.break_glass_kind`). The two forms of a name are the same:
  a bare username `x` is also `x@meridian.example`, and a company address is also its bare
  username. As written comes first, so listed beats pattern only within one form.
- **Names are compared as matching compares them:** spaces around a name are dropped and its
  case is folded with `str.casefold`, so `OPS`, `ops` and `op` with a long s are one name.
- **An account is every record linked by a shared name.** A record carries its login, its email
  and its Google aliases. Two records that share any name, in either form, belong to one account.
  This takes in a Google user's primary address and its aliases, an Okta user's login and email,
  a Slack userName, and an export row.
- **An account is protected** when any of its names is protected by the policy. Then every name
  it goes by is protected, in both forms, and every record of it is break-glass. The kind of a
  protected account, and of each of its records, is "listed" if the policy lists any of its
  names, else "pattern".

A record that carries a protected name together with another person's address therefore makes
that person's records protected too: the plan reports each as a break-glass `never_touch` entry
and changes none of them, so a reviewer sees it. Leaving one of them unprotected could lock IT
out (AAC-007).
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from meridian_access.models import Account
from meridian_access.policy.load import Policy
from meridian_dataset.access.model import DOMAIN


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


def _names_of(a: Account) -> tuple[str, ...]:
    return (a.account, a.email, *a.aliases)


@dataclass(frozen=True, slots=True)
class BreakGlass:
    """The protected names in one actual state: every form of every name of every protected
    account, each with the account's kind."""

    policy: Policy
    names: Mapping[str, str]

    def protects(self, name: str) -> bool:
        """True if `name` is a name of a protected account, or the policy protects it."""
        if any(form in self.names for form in name_forms(name)):
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


def break_glass(policy: Policy, accounts: Iterable[Account]) -> BreakGlass:
    """Resolve every record to its account and collect the names of the protected ones."""
    parent: dict[str, str] = {}

    def root(form: str) -> str:
        while parent[form] != form:
            parent[form] = parent[parent[form]]
            form = parent[form]
        return form

    for a in accounts:
        forms = [f for value in _names_of(a) for f in name_forms(value)]
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
    for group in members.values():
        kinds = {policy.break_glass_kind(f) for f in group} - {None}
        if kinds:
            kind = "listed" if "listed" in kinds else "pattern"
            names.update(dict.fromkeys(group, kind))
    return BreakGlass(policy, MappingProxyType(names))
