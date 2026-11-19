"""The risk rubric of OAR-002, exactly as OAR-002 states it.

**Status: Proposed (OAR-002), not yet accepted by the owner.** The owner's catalogue asks
for a different rubric ("scope sensitivity times users times dormancy times vendor maturity"); see
oauth-radar/docs/catalogue-differences.md. Until the owner rules (R14), this module builds
OAR-002 as written, so its results can be compared, and changes none of its numbers.

One grant (one person, one app, every scope that person gave it) gets points for five
signals. Every score lists each signal with its points and the reason, so a person can
recompute it by hand:

| Signal | Points |
|---|---|
| `scope_breadth`: the broadest scope the grant holds | broad 3, medium 2, narrow 0 |
| `unverified_publisher`: Google doesn't list the publisher as verified | +2 |
| `reach`: people at Meridian with a grant to the same app | 1-4: 0, 5-24: +1, 25+: +2 |
| `ai_app`: tagged AI in IT's catalogue, or (not in it) its name matches the AI word list | +1 |
| `not_reviewed`: the app isn't in IT's catalogue at all | +1 |

Two overrides make a grant high whatever its points: `banned_app` (on IT's banned list) and
`leaver` (the person has left and the grant holds a scope above narrow). Tiers: 5 points or
more is high, 3-4 medium, 2 or fewer low.

Scope tiers come from the scope explainer's classes: Google's restricted scopes and Admin
SDK scopes are broad (all mail, all Drive files, admin), sensitive scopes are medium
(calendar, contacts, Sheets, Docs, sending mail, the directory), non-sensitive ones are
narrow (sign-in, files the app made or was handed). **A scope the table doesn't know counts
as broad,** as OAR-002 says: it fails toward a review, never toward silence. The broadest
scope counts, not the sum.

The rubric only scores. It reads facts and grants and returns numbers; it never decides an
action, and no model is involved. AI may later word an explanation; it never sets a score.
"""

import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from meridian_oauth_radar import scopes
from meridian_oauth_radar.collector import TokenGrant
from meridian_oauth_radar.enrich import AppFacts, Knowledge

STATUS = "Proposed (OAR-002), not yet accepted by the owner"

HIGH, MEDIUM, LOW = "high", "medium", "low"
TIERS = (HIGH, MEDIUM, LOW)
HIGH_FROM = 5
MEDIUM_FROM = 3

BROAD, MEDIUM_SCOPE, NARROW = "broad", "medium", "narrow"
BREADTH_POINTS = {BROAD: 3, MEDIUM_SCOPE: 2, NARROW: 0}
# Scope class (from the explainer) -> OAR-002's breadth. Unknown counts as broad.
BREADTH_OF_CLASS = {
    scopes.RESTRICTED: BROAD,
    scopes.ADMIN: BROAD,
    scopes.UNKNOWN: BROAD,
    scopes.SENSITIVE: MEDIUM_SCOPE,
    scopes.NON_SENSITIVE: NARROW,
}
UNVERIFIED_POINTS = 2
REACH_SOME_FROM, REACH_SOME_POINTS = 5, 1
REACH_ORG_WIDE_FROM, REACH_ORG_WIDE_POINTS = 25, 2
AI_POINTS = 1
NOT_REVIEWED_POINTS = 1

# OAR-002's "short AI word list", for apps not in IT's catalogue. Whole words, any case,
# in the app's name or its publisher's name.
AI_WORDS = frozenset(
    {"ai", "gpt", "llm", "copilot", "assistant", "agent", "transcribe", "transcription"}
)

SIGNALS = ("scope_breadth", "unverified_publisher", "reach", "ai_app", "not_reviewed")
OVERRIDES = ("banned_app", "leaver")


@dataclass(frozen=True, slots=True)
class Points:
    """One signal or override, with its points and the reason in plain English."""

    signal: str
    points: int
    reason: str


@dataclass(frozen=True, slots=True)
class Score:
    """A grant's score: every signal's points, any override, the total and the tier."""

    grant: TokenGrant
    signals: tuple[Points, ...]
    overrides: tuple[Points, ...]
    tier: str
    status: str = STATUS

    def __post_init__(self) -> None:
        if [p.signal for p in self.signals] != list(SIGNALS):
            raise ValueError(f"a score lists every signal, in order: {SIGNALS}")
        if self.tier not in TIERS:
            raise ValueError(f"unknown tier {self.tier!r}")

    @property
    def total(self) -> int:
        return sum(p.points for p in self.signals)


def breadth(scope_urls: Iterable[str]) -> tuple[str, str | None]:
    """The broadest tier among the scopes, and the scope that set it (None for no scopes)."""
    order = {BROAD: 0, MEDIUM_SCOPE: 1, NARROW: 2}
    best: tuple[int, str, str] | None = None
    for url in scope_urls:
        tier = BREADTH_OF_CLASS[scopes.google_class(url)]
        candidate = (order[tier], url, tier)
        if best is None or candidate < best:
            best = candidate
    if best is None:
        return NARROW, None
    return best[2], best[1]


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.casefold()))


def is_ai_app(name: str, facts: AppFacts) -> tuple[bool, str]:
    """OAR-002's AI signal: IT's tag if IT reviewed the app, else the word list."""
    if facts.in_it_catalogue:
        return bool(facts.it_ai_tag), "IT's catalogue tags it AI" if facts.it_ai_tag else ""
    hits = sorted(_words(name) & AI_WORDS)
    if not hits and facts.vendor:
        hits = sorted(_words(facts.vendor) & AI_WORDS)
    return bool(hits), f"its name matches the AI word list ({', '.join(hits)})" if hits else ""


def _tier(total: int, overridden: bool) -> str:
    if overridden or total >= HIGH_FROM:
        return HIGH
    return MEDIUM if total >= MEDIUM_FROM else LOW


def score_grant(
    grant: TokenGrant, facts: AppFacts, users_of_app: int, *, leaver: bool = False
) -> Score:
    """Score one grant. `users_of_app` is how many people hold a grant to the same app."""
    tier_of_scope, widest = breadth(grant.scopes)
    if widest is None:
        breadth_reason = "No scopes at all: narrow (0)."
    elif not scopes.known(widest):
        breadth_reason = (
            f"Broadest scope {widest} isn't in the scope table, so it counts as broad (3)."
        )
    else:
        breadth_reason = (
            f"Broadest scope {widest} is {tier_of_scope} ({BREADTH_POINTS[tier_of_scope]}): "
            f"{scopes.explain(widest)}"
        )
    if facts.verified_publisher is True:
        verified = Points("unverified_publisher", 0, "Google lists the publisher as verified.")
    elif facts.verified_publisher is False:
        verified = Points(
            "unverified_publisher",
            UNVERIFIED_POINTS,
            "Google doesn't list the publisher as verified.",
        )
    else:
        verified = Points(
            "unverified_publisher",
            UNVERIFIED_POINTS,
            "Nobody knows the publisher, so it counts as not verified.",
        )
    if users_of_app >= REACH_ORG_WIDE_FROM:
        reach_points = REACH_ORG_WIDE_POINTS
    elif users_of_app >= REACH_SOME_FROM:
        reach_points = REACH_SOME_POINTS
    else:
        reach_points = 0
    people = "person" if users_of_app == 1 else "people"
    ai, ai_why = is_ai_app(grant.display_text, facts)
    signals = (
        Points("scope_breadth", BREADTH_POINTS[tier_of_scope], breadth_reason),
        verified,
        Points("reach", reach_points, f"{users_of_app} {people} at Meridian hold a grant to it."),
        Points("ai_app", AI_POINTS, f"An AI app: {ai_why}.")
        if ai
        else Points("ai_app", 0, "Not known as an AI app."),
        Points("not_reviewed", 0, "In IT's app catalogue.")
        if facts.in_it_catalogue
        else Points(
            "not_reviewed", NOT_REVIEWED_POINTS, "Not in IT's app catalogue: never reviewed."
        ),
    )
    overrides = []
    if facts.banned_reason is not None:
        overrides.append(
            Points("banned_app", 0, f"On IT's banned list ({facts.banned_reason}), so high.")
        )
    if leaver and tier_of_scope != NARROW:
        overrides.append(
            Points(
                "leaver", 0, "The person has left and the grant reaches more than sign-in, so high."
            )
        )
    total = sum(p.points for p in signals)
    return Score(grant, signals, tuple(overrides), _tier(total, bool(overrides)))


def score_all(
    grants: Iterable[TokenGrant], knowledge: Knowledge, leavers: Iterable[str] = ()
) -> tuple[Score, ...]:
    """Score every grant, ranked: tier, then total, then app name, user and app ID."""
    grants = tuple(grants)
    holders = Counter(client_id for _user, client_id in {g.key for g in grants})
    left = set(leavers)
    found = [
        score_grant(
            g, knowledge.facts(g.client_id), holders[g.client_id], leaver=g.user_key in left
        )
        for g in grants
    ]
    return tuple(sorted(found, key=rank))


def rank(score: Score) -> tuple[int, int, str, str, str]:
    grant = score.grant
    return (
        TIERS.index(score.tier),
        -score.total,
        grant.display_text.casefold(),
        grant.user_key,
        grant.client_id,
    )


def counts(found: Iterable[Score]) -> Mapping[str, int]:
    tally = Counter(score.tier for score in found)
    return {tier: tally.get(tier, 0) for tier in TIERS}


__all__ = [
    "AI_WORDS",
    "HIGH",
    "LOW",
    "MEDIUM",
    "OVERRIDES",
    "SIGNALS",
    "STATUS",
    "TIERS",
    "Points",
    "Score",
    "breadth",
    "counts",
    "is_ai_app",
    "rank",
    "score_all",
    "score_grant",
]
