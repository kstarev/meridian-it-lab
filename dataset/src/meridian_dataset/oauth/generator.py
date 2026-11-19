"""The OAuth tuning world: Google OAuth grants for the shared Meridian people.

The people are the 230 of `meridian_dataset.month.personas` (`build_personas()`), read
only. Leavers come from the Access tuning world's HRIS records (`generate_access()`, seed
6108, the public tuning recipe), built in memory and read only (OAR-009): the world copies
four HRIS fields of everyone who had left by the snapshot into `radar/hris_leavers.json`, and
one of them, whose Google account was never removed, still holds a grant. The two rosters
share no one. This module draws from its own `random.Random(OAUTH_TUNING_SEED)`, so it can't
shift anyone else's draws, and it adds nothing to any existing world.

It is a **tuning world** only: its recipe is public and the radar may be tuned on it. There
is no held-out or sealed OAuth world (OAR-006 waits).

The world is a snapshot at `REFERENCE_TIME`. Every grant has an "authorize" activity when it
was made and, if it was used later, an "activity" event at its last use.

**8 risky grants are planted**, each by a different person: the catalogue's 6 (`PLANTS`),
each to an app nobody else holds; a grant to the app IT banned (`BANNED_PLANT`); and a
leaver's grant to the company's video-meeting app (`LEAVER_PLANT`), which on its points
alone is only medium, so only the leaver override makes it high. They are drawn after
everything else, so the earlier grants don't move. They are marked only in the answer key
(`key/answer_key.json`), which is written apart from the radar's inputs (`radar/`). Nothing
in `radar/` says which grants are planted.
"""

import hashlib
import json
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from meridian_dataset.access import ACCESS_TUNING_SEED, generate_access
from meridian_dataset.month.personas import Persona, build_personas
from meridian_dataset.oauth.apps import ALL_APPS, BACKGROUND, BANNED, BANNED_REASON, PLANTED, App

OAUTH_GENERATOR_VERSION = "0.3.0"
OAUTH_TUNING_SEED = 9137
REFERENCE_TIME = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
# A grant not used for more than this many days is drawn as dormant. The radar's own
# threshold is its policy setting, not this.
DORMANT_FROM_DAYS = 91
ACTIVE_UP_TO_DAYS = 75
CUSTOMER_ID = "C0fakemeridian"

RADAR_DIR = "radar"
KEY_DIR = "key"


@dataclass(frozen=True, slots=True)
class Plant:
    slug: str
    kind: str
    days_since_use: int
    why: str


# The 6 risky grants. Kinds: an old grant with a restricted scope that nobody uses
# ("dormant_restricted"), a risky grant in daily use ("risky_in_use"), an app Security lists
# as compromised ("compromised_app"), and an app that can set mail forwarding
# ("mail_settings").
PLANTS: tuple[Plant, ...] = (
    Plant(
        "murmurlark", "dormant_restricted", 160,
        "An unverified AI note-taker from a young vendor with full Drive access, unused for "
        "160 days.",
    ),
    Plant(
        "mergewick", "dormant_restricted", 130,
        "A mail-merge tool from a young vendor with full Gmail access, unused for 130 days.",
    ),
    Plant(
        "draftmoth", "risky_in_use", 2,
        "An unverified AI writer that can read and change all mail, used two days ago.",
    ),
    Plant(
        "scribblebee", "risky_in_use", 6,
        "An unverified AI recorder that can read every Drive file and calendar, in use.",
    ),
    Plant(
        "synapsel", "compromised_app", 10,
        "Security lists the app as compromised, and it can read every Drive file.",
    ),
    Plant(
        "sortwhistle", "mail_settings", 30,
        "An unverified app from an unknown vendor that can change mail settings, including "
        "forwarding all mail elsewhere.",
    ),
)  # fmt: skip

# Two more plants, on the owner's ruling of 2026-11-19 (OAR-009): a grant to the app IT
# banned, still in use; and a leaver's grant. The leaver holds the most common app at
# Meridian, which reads and changes their calendar: medium on its points alone.
BANNED_PLANT = Plant(
    "notesprout",
    "banned_app",
    12,
    "A grant to the AI note-taker IT banned, with full Drive access, still in use.",
)
LEAVER_APP = "huddlewave"
LEAVER_KIND = "leaver_grant"
LEAVER_WHY = (
    "HR says the person has left, but their Google account was never removed and still "
    "lets the meetings app read and change their calendar."
)
# The leaver last used the app within this many days before their end date.
LEAVER_LAST_USE_BEFORE_END = 14
HRIS_LEAVERS_FILE = "hris_leavers.json"

# Look-alikes that are not risky, so a false alarm can be counted.
DECOYS: tuple[tuple[str, str], ...] = (
    ("vellumine", "The approved company AI assistant: reads Drive, verified, reviewed by IT."),
    ("quillnest", "An AI writer that sees only files it made or was handed (drive.file)."),
    ("echoloop", "A verified AI transcriber with calendar read and drive.file only."),
    ("cheerboard", "An unverified app with sign-in scopes only."),
    ("tallyfern", "Approved expenses app; two old grants carry IT exceptions, one expired."),
)

APPROVED_ON = "2026-03-02"
IT_LEAD = "IT lead"
COMPROMISED_ON = "2026-11-14"
# Two dormant Tallyfern grants in Finance: one under an exception that still runs, one under
# an exception that has expired. Each runs 90 days from its approval, the longest an
# exception may last (OAR-010), so IT renews it every quarter.
EXCEPTION_DAYS = (120, 150)
EXCEPTION_APPROVED = ("2026-11-02", "2026-08-03")
EXCEPTION_EXPIRES = ("2027-01-31", "2026-11-01")
EXCEPTION_REASON = "used only at the quarterly close"
# One more entry IT wrote for a whole year, which the radar must refuse and report (OAR-010).
LONG_EXCEPTION = {
    "slug": "pollquill",
    "reason": "the yearly engagement survey",
    "approved_on": "2026-06-01",
    "expires": "2027-06-01",
}


@dataclass(frozen=True, slots=True)
class Grant:
    user: str
    app: App
    granted: datetime
    last_used: datetime


@dataclass(frozen=True, slots=True)
class Leaver:
    """The four HRIS fields the radar needs, copied from an Access world record."""

    employee_id: str
    work_email: str
    status: str
    end_date: str


@dataclass(frozen=True, slots=True)
class OAuthWorld:
    """The tuning world: what the radar may read (`radar_inputs`) and the answer key apart."""

    seed: int
    grants: tuple[Grant, ...]
    users: tuple[str, ...]
    exception_grants: tuple[Grant, ...]
    plants: tuple[tuple[Plant, Grant], ...]
    leavers: tuple[Leaver, ...]

    def radar_inputs(self) -> dict[str, Any]:
        """Every file the radar may read, as JSON-ready objects, by file name."""
        return {
            "world.json": {
                "generator_version": OAUTH_GENERATOR_VERSION,
                "seed": self.seed,
                "reference_time": _iso(REFERENCE_TIME),
            },
            "directory_users.json": [
                {"kind": "admin#directory#user", "id": f"{100000000001 + i}", "primaryEmail": u}
                for i, u in enumerate(self.users)
            ],
            "tokens.json": _tokens(self.grants),
            "token_activity.json": _activities(self.grants),
            "app_info.json": {
                "apps": [_app_info(app) for app in ALL_APPS if app.in_vendor_directory]
            },
            "it_app_catalogue.json": {
                "apps": [
                    {
                        "client_id": app.client_id,
                        "name": app.name,
                        "category": app.category,
                        "ai": app.ai,
                        "status": app.it_status,
                        "reviewed_on": APPROVED_ON,
                    }
                    for app in ALL_APPS
                    if app.it_status is not None
                ]
            },
            HRIS_LEAVERS_FILE: {
                "as_of": REFERENCE_TIME.date().isoformat(),
                "records": [
                    {
                        "employee_id": leaver.employee_id,
                        "work_email": leaver.work_email,
                        "status": leaver.status,
                        "end_date": leaver.end_date,
                    }
                    for leaver in self.leavers
                ],
            },
            "banned_apps.json": {
                "apps": [{"app_id": BANNED.client_id, "name": BANNED.name, "reason": BANNED_REASON}]
            },
            "compromised_apps.json": {
                "apps": [
                    {
                        "client_id": _app("synapsel").client_id,
                        "name": _app("synapsel").name,
                        "advisory": "The vendor reports that OAuth tokens it held were stolen "
                        "(a made-up advisory).",
                        "published": COMPROMISED_ON,
                    }
                ]
            },
            "exceptions.json": {
                "exceptions": [
                    *(
                        {
                            "client_id": grant.app.client_id,
                            "user_key": grant.user,
                            "reason": EXCEPTION_REASON,
                            "approved_by": IT_LEAD,
                            "approved_on": approved_on,
                            "expires": expires,
                        }
                        for grant, approved_on, expires in zip(
                            self.exception_grants,
                            EXCEPTION_APPROVED,
                            EXCEPTION_EXPIRES,
                            strict=True,
                        )
                    ),
                    {
                        "client_id": _app(LONG_EXCEPTION["slug"]).client_id,
                        "user_key": None,
                        "reason": LONG_EXCEPTION["reason"],
                        "approved_by": IT_LEAD,
                        "approved_on": LONG_EXCEPTION["approved_on"],
                        "expires": LONG_EXCEPTION["expires"],
                    },
                ]
            },
        }

    def answer_key(self) -> dict[str, Any]:
        """The planted risky grants and the decoys. The radar never reads this."""
        return {
            "world": "oauth-tuning",
            "seed": self.seed,
            "risky_grants": [
                {
                    "user_key": grant.user,
                    "client_id": grant.app.client_id,
                    "app": grant.app.name,
                    "kind": plant.kind,
                    "why": plant.why,
                }
                for plant, grant in self.plants
            ],
            "decoys": [
                {"client_id": _app(slug).client_id, "app": _app(slug).name, "why": why}
                for slug, why in DECOYS
            ],
        }

    def files(self) -> dict[str, bytes]:
        """Every file of the world as canonical bytes, by path, with a manifest of hashes."""
        out = {f"{RADAR_DIR}/{name}": _dump(obj) for name, obj in self.radar_inputs().items()}
        out[f"{KEY_DIR}/answer_key.json"] = _dump(self.answer_key())
        out["manifest.json"] = _dump(
            {
                "generator_version": OAUTH_GENERATOR_VERSION,
                "seed": self.seed,
                "files": {
                    path: hashlib.sha256(data).hexdigest() for path, data in sorted(out.items())
                },
            }
        )
        return out


def _app(slug: str) -> App:
    return next(app for app in ALL_APPS if app.slug == slug)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _short(text: str, length: int) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:length]


def _dump(obj: Any) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def _app_info(app: App) -> dict[str, Any]:
    return {
        "client_id": app.client_id,
        "name": app.name,
        "vendor": app.vendor,
        "category": app.category,
        "ai": app.ai,
        "verified_publisher": app.verified_publisher,
        "data_location": app.data_location,
        "vendor_maturity": app.vendor_maturity,
    }


def _tokens(grants: tuple[Grant, ...]) -> dict[str, list[dict[str, Any]]]:
    tokens: dict[str, list[dict[str, Any]]] = {}
    for grant in sorted(grants, key=lambda g: (g.user, g.app.client_id)):
        tokens.setdefault(grant.user, []).append(
            {
                "kind": "admin#directory#token",
                "etag": f'"fake-{_short(grant.user + grant.app.slug, 16)}"',
                "clientId": grant.app.client_id,
                "displayText": grant.app.name,
                "anonymous": False,
                "nativeApp": grant.app.slug == "huddlewave",
                "userKey": grant.user,
                "scopes": sorted(grant.app.scopes),
            }
        )
    return tokens


def _event(name: str, moment: datetime, grant: Grant) -> dict[str, Any]:
    return {
        "kind": "admin#reports#activity",
        "id": {
            "time": _iso(moment),
            "uniqueQualifier": hashlib.sha256(
                f"{grant.user}|{grant.app.slug}|{name}".encode()
            ).hexdigest()[:20],
            "applicationName": "token",
            "customerId": CUSTOMER_ID,
        },
        "actor": {"email": grant.user},
        "events": [
            {
                "type": "auth",
                "name": name,
                "parameters": [
                    {"name": "client_id", "value": grant.app.client_id},
                    {"name": "app_name", "value": grant.app.name},
                ],
            }
        ],
    }


def _activities(grants: tuple[Grant, ...]) -> list[dict[str, Any]]:
    items = []
    for grant in grants:
        items.append(_event("authorize", grant.granted, grant))
        if grant.last_used > grant.granted:
            items.append(_event("activity", grant.last_used, grant))
    # Newest first, as the Reports API returns them.
    return sorted(
        items,
        key=lambda i: (i["id"]["time"], i["actor"]["email"], i["id"]["uniqueQualifier"]),
        reverse=True,
    )


def _grant(rng: random.Random, user: str, app: App, days_since_use: int) -> Grant:
    last_used = REFERENCE_TIME - timedelta(days=days_since_use, minutes=rng.randint(0, 600))
    granted = last_used - timedelta(days=rng.randint(0, 200), minutes=rng.randint(0, 600))
    return Grant(user=user, app=app, granted=granted, last_used=last_used)


def _days(rng: random.Random, app: App) -> int:
    if rng.random() < app.dormant_share:
        return rng.randint(DORMANT_FROM_DAYS, 330)
    return rng.randint(0, ACTIVE_UP_TO_DAYS)


def hris_leavers(as_of: datetime = REFERENCE_TIME) -> tuple[Leaver, ...]:
    """Everyone the Access tuning world's HRIS shows as gone before `as_of`, read only.

    A record counts when its status is "terminated" and its end date is before `as_of`, and
    nobody with the same work email is still employed (a rehire's old record doesn't).
    """
    records = generate_access(ACCESS_TUNING_SEED).hris.records
    employed = {r.work_email for r in records if r.status != "terminated"}
    day = as_of.date().isoformat()
    return tuple(
        sorted(
            (
                Leaver(r.employee_id, r.work_email, r.status, r.end_date)
                for r in records
                if r.status == "terminated"
                and r.end_date
                and r.end_date < day
                and r.work_email not in employed
            ),
            key=lambda leaver: leaver.employee_id,
        )
    )


def generate_oauth_world(seed: int = OAUTH_TUNING_SEED) -> OAuthWorld:
    """The tuning world for `seed`: same seed, same world."""
    rng = random.Random(seed)
    people: tuple[Persona, ...] = build_personas()
    grants: dict[tuple[str, str], Grant] = {}
    for app in BACKGROUND:
        for person in people:
            if rng.random() < app.rate(person.department):
                grants[(person.email, app.slug)] = _grant(rng, person.email, app, _days(rng, app))

    # Two Finance people get an old, unused Tallyfern grant, each under an IT exception.
    finance = sorted(p.email for p in people if p.department == "Finance")
    tallyfern = _app("tallyfern")
    exception_grants = tuple(
        _grant(rng, user, tallyfern, days)
        for user, days in zip(rng.sample(finance, len(EXCEPTION_DAYS)), EXCEPTION_DAYS, strict=True)
    )
    for grant in exception_grants:
        grants[(grant.user, tallyfern.slug)] = grant

    # The 6 plants: six different people outside IT, none of them under an exception.
    taken = {grant.user for grant in exception_grants}
    candidates = sorted(p.email for p in people if p.department != "IT" and p.email not in taken)
    planted_users = rng.sample(candidates, len(PLANTS))
    plants = []
    for plant, user in zip(PLANTS, planted_users, strict=True):
        app = _app(plant.slug)
        grant = _grant(rng, user, app, plant.days_since_use)
        grants[(user, app.slug)] = grant
        plants.append((plant, grant))

    if {plant.slug for plant in PLANTS} != {app.slug for app in PLANTED}:
        raise AssertionError("every planted app has exactly one plant")

    # The banned-app plant: one more person outside IT, not planted yet, no exception.
    banned_user = rng.choice(sorted(set(candidates) - set(planted_users)))
    banned_grant = _grant(rng, banned_user, BANNED, BANNED_PLANT.days_since_use)
    grants[(banned_user, BANNED.slug)] = banned_grant
    plants.append((BANNED_PLANT, banned_grant))

    # The leaver plant: someone HRIS says has left, whose Google account is still there.
    leavers = hris_leavers()
    roster = {p.email for p in people}
    if not leavers or roster & {leaver.work_email for leaver in leavers}:
        raise AssertionError("the leavers must be HRIS records outside the month roster")
    leaver = rng.choice(leavers)
    since_end = (REFERENCE_TIME.date() - datetime.fromisoformat(leaver.end_date).date()).days
    days = since_end + rng.randint(0, LEAVER_LAST_USE_BEFORE_END)
    leaver_grant = _grant(rng, leaver.work_email, _app(LEAVER_APP), days)
    grants[(leaver.work_email, LEAVER_APP)] = leaver_grant
    plants.append((Plant(LEAVER_APP, LEAVER_KIND, days, LEAVER_WHY), leaver_grant))

    return OAuthWorld(
        seed=seed,
        grants=tuple(grants[key] for key in sorted(grants)),
        users=(*(p.email for p in people), leaver.work_email),
        exception_grants=exception_grants,
        plants=tuple(plants),
        leavers=leavers,
    )


def write_oauth_world(world: OAuthWorld, out_dir: Path) -> dict[str, Path]:
    """Write the world's files under `out_dir` and return their paths."""
    paths = {}
    for name, data in world.files().items():
        path = out_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        paths[name] = path
    return paths
