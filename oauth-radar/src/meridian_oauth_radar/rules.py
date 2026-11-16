"""The milestone-0 rules engine: a stub with two rules, documented here and in OAR-002.

Each rule looks at one grant and returns a `RuleHit` or nothing. A grant with at least one
hit becomes a `Finding`, and findings are ranked by score (the sum of their hits' points),
highest first. In milestone 0 each rule marks a grant high-risk on its own. The full scoring
table (publisher, reach, AI apps, leavers, and the medium and low tiers) comes in milestone 2.

The engine only reads grants and returns findings. It can't revoke, change or call anything
(OAR-004, OAR-005).
"""

from collections.abc import Iterable, Mapping

from meridian_oauth_radar import Finding, Grant, RuleHit

# Rule `broad_scope`: the grant holds a scope that reaches all of a person's mail or all of
# their Drive files. Each entry says in plain words what the scope lets the app do. Narrow
# scopes, such as drive.file (only files the app made or was handed), aren't on the list.
BROAD_SCOPES: dict[str, str] = {
    "https://mail.google.com/": "full Gmail access: read, send and delete all mail",
    "https://www.googleapis.com/auth/gmail.modify": "read and change all mail",
    "https://www.googleapis.com/auth/gmail.readonly": "read all mail",
    "https://www.googleapis.com/auth/drive": (
        "full Drive access: see, edit, share and delete every file"
    ),
    "https://www.googleapis.com/auth/drive.readonly": "see and download every Drive file",
}
BROAD_SCOPE_POINTS = 3

# Rule `banned_app`: the grant is to an app on IT's banned list, whatever its scopes.
BANNED_APP_POINTS = 5

# Every rule, with one line on what it flags. OAR-002 lists the same ids.
RULES: dict[str, str] = {
    "banned_app": "The app is on IT's banned list, whatever scopes it holds.",
    "broad_scope": "The grant reaches all of a person's mail or all of their Drive files.",
}


def broad_scope(grant: Grant) -> RuleHit | None:
    """Fires when the grant holds at least one scope from BROAD_SCOPES (one hit per grant)."""
    held = [scope for scope in grant.scopes if scope in BROAD_SCOPES]
    if not held:
        return None
    what = "; ".join(f"{BROAD_SCOPES[scope]} ({scope})" for scope in held)
    return RuleHit("broad_scope", BROAD_SCOPE_POINTS, f"Broad scope: {what}.")


def banned_app(grant: Grant, banned: Mapping[str, str]) -> RuleHit | None:
    """Fires when the grant's app ID is on IT's banned list ({app_id: reason})."""
    reason = banned.get(grant.app_id)
    if reason is None:
        return None
    return RuleHit("banned_app", BANNED_APP_POINTS, f"Banned app: on IT's banned list ({reason}).")


def _rank(finding: Finding) -> tuple[int, str, str, str]:
    grant = finding.grant
    return (-finding.score, grant.app_name.casefold(), grant.user, grant.app_id)


def scan(grants: Iterable[Grant], banned: Mapping[str, str] | None = None) -> tuple[Finding, ...]:
    """Run every rule on every grant and return the findings, highest score first.

    Ties are broken by app name, then user, then app ID, so the same input always gives the
    same list.
    """
    banned = banned or {}
    findings = []
    for grant in grants:
        hits = tuple(hit for hit in (banned_app(grant, banned), broad_scope(grant)) if hit)
        if hits:
            findings.append(Finding(grant=grant, hits=hits, severity="high"))
    return tuple(sorted(findings, key=_rank))
