"""Precision and recall on the tuning world, graded by code against the planted answer key.

This is the grader, and the only module that reads an answer key. The radar itself (the
collector, enricher, rubric, policy, nudges and report) never does: `run.run_files` takes
only the world's `radar/` folder, and a test runs it with no key on disk at all.

Two ways of counting, both reported (OAR-008, the owner's ruling on R16):

- **The headline: acted on by the radar.** A planted risky grant is found when the radar
  writes a notice draft for it, puts it in a plan (after-notice or emergency), or drafts a
  Slack nudge for it. A grant only listed for a person to review isn't counted, because that
  list holds every high and medium grant. It has two targets: **precision at least 0.90**
  (the lab brief's target for every project) and **recall 100%** (the lab brief's target for
  oauth-radar, and the catalogue's "6 of 6"). Each is reported as PASS or FAIL on its point
  estimate, and a FAIL stays a FAIL until it is met.
- **The second line: marked high by the rubric** (OAR-002's definition of "found"): a
  planted risky grant is found when the rubric rates it high; any other high grant is a false
  alarm. Reported, with no target.

Every rate comes with its count and a 95% Wilson interval, because the counts are small. The
catalogue's "dormant grants removed (simulation)" is reported too: of the grants unused for
more than the dormancy window, how many are in the after-notice plan a week later. Nothing
is removed; "removed" means "in a reviewed plan".
"""

import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from meridian_oauth_radar.rubric import STATUS
from meridian_oauth_radar.run import LEDGER, reference_date, run_files

Key = tuple[str, str]

# The headline's targets (OAR-008): the lab brief's precision target for every project, and
# its recall target for oauth-radar ("High-risk OAuth grants found: 100%").
PRECISION_TARGET = 0.90
RECALL_TARGET = 1.0
PASS, FAIL = "PASS", "FAIL"


def verdict(k: int, n: int, target: float) -> str:
    """PASS when k of n meets the target on its point estimate; FAIL otherwise (and for n = 0)."""
    return PASS if n and k / n >= target else FAIL


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    """The 95% Wilson score interval for k successes in n trials."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


@dataclass(frozen=True, slots=True)
class Count:
    """Found plants and false alarms for one way of counting."""

    name: str
    flagged: int
    found: int
    planted: int

    @property
    def false_alarms(self) -> int:
        return self.flagged - self.found

    @property
    def recall(self) -> float:
        return self.found / self.planted if self.planted else 0.0

    @property
    def precision(self) -> float:
        return self.found / self.flagged if self.flagged else 0.0

    @property
    def recall_verdict(self) -> str:
        return verdict(self.found, self.planted, RECALL_TARGET)

    @property
    def precision_verdict(self) -> str:
        return verdict(self.found, self.flagged, PRECISION_TARGET)


@dataclass(frozen=True, slots=True)
class PlantOutcome:
    app: str
    kind: str
    tier: str
    total: int
    outcome: str


@dataclass(frozen=True, slots=True)
class Evaluation:
    for_date: str
    week_later: str
    grants: int
    users: int
    leavers_in_directory: int
    high: Count
    acted: Count
    plants: tuple[PlantOutcome, ...]
    false_alarms_by_app: tuple[tuple[str, int], ...]
    acted_false_alarms_by_app: tuple[tuple[str, int], ...]
    dormant: int
    dormant_in_plan: int
    excepted: int


def _keys(rows: list[dict[str, Any]]) -> set[Key]:
    return {(row["user_key"], row["client_id"]) for row in rows}


def evaluate(world: Path, answer_key: Path, scratch: Path) -> Evaluation:
    """Run the radar on day 0 and a week later, then grade both against the answer key.

    `scratch` is a folder for the day-0 files, whose notice ledger the second run reads.
    """
    key = json.loads(answer_key.read_text(encoding="utf-8"))
    planted = _keys(key["risky_grants"])
    kinds = {(r["user_key"], r["client_id"]): r["kind"] for r in key["risky_grants"]}
    day0 = run_files(world)
    (scratch / "notices").mkdir(parents=True, exist_ok=True)
    (scratch / LEDGER).write_bytes(day0[LEDGER])
    later_date = reference_date(world) + timedelta(days=7)
    later = run_files(world, today=later_date, ledger_path=scratch / LEDGER)

    scores = json.loads(day0["scores.json"])["grants"]
    by_key = {(g["user_key"], g["client_id"]): g for g in scores}
    high = {k for k, g in by_key.items() if g["tier"] == "high"}

    noticed = _keys(json.loads(day0[LEDGER])["notices"])
    nudged = _keys(json.loads(day0["nudges/index.json"])["drafts"])
    emergency = _keys(json.loads(day0["plans/emergency-plan.json"])["items"])
    after = _keys(json.loads(later["plans/after-notice-plan.json"])["items"])
    acted = noticed | nudged | emergency | after

    def outcome(k: Key) -> str:
        parts = []
        if k in emergency:
            parts.append("emergency plan")
        if k in noticed:
            parts.append("notice, then after-notice plan" if k in after else "notice")
        if k in nudged:
            parts.append("Slack nudge draft")
        return ", ".join(parts) or "only listed for a person"

    plants = tuple(
        sorted(
            (
                PlantOutcome(
                    by_key[k]["app"], kinds[k], by_key[k]["tier"], by_key[k]["total"], outcome(k)
                )
                for k in planted
            ),
            key=lambda p: (p.kind, p.app),
        )
    )

    def per_app(keys: set[Key]) -> tuple[tuple[str, int], ...]:
        tally: dict[str, int] = {}
        for k in keys - planted:
            tally[by_key[k]["app"]] = tally.get(by_key[k]["app"], 0) + 1
        return tuple(sorted(tally.items(), key=lambda kv: (-kv[1], kv[0])))

    dormant_days = json.loads(day0["run.json"])["settings"]["dormant_days"]
    today = reference_date(world)
    dormant = {
        k
        for k, g in by_key.items()
        if g["last_used"] is not None
        and (today - datetime.fromisoformat(g["last_used"]).date()).days > dormant_days
    }
    counts = json.loads(day0["run.json"])["counts"]
    return Evaluation(
        for_date=today.isoformat(),
        week_later=later_date.isoformat(),
        grants=len(scores),
        users=counts["users"],
        leavers_in_directory=counts["leavers_in_directory"],
        high=Count("Marked high by the rubric", len(high), len(high & planted), len(planted)),
        acted=Count("Acted on by the radar", len(acted), len(acted & planted), len(planted)),
        plants=plants,
        false_alarms_by_app=per_app(high),
        acted_false_alarms_by_app=per_app(acted),
        dormant=len(dormant),
        dormant_in_plan=len(dormant & after),
        excepted=counts["excepted"],
    )


def _rate(k: int, n: int) -> str:
    low, high = wilson(k, n)
    share = k / n if n else 0.0
    return f"{k} of {n} ({share:.1%}; 95% CI {low:.1%} to {high:.1%})"


def markdown(ev: Evaluation) -> str:
    """The published report. A test checks the committed copy matches this output."""
    a, h = ev.acted, ev.high
    lines = [
        "# Shadow AI Radar: precision and recall on the tuning world",
        "",
        f"**Date:** 2026-11-19 · **World:** the OAuth tuning world (seed 9137, snapshot "
        f"{ev.for_date}), {ev.grants} grants held by {ev.users} people: the month roster, and "
        f"{ev.leavers_in_directory} {'person' if ev.leavers_in_directory == 1 else 'people'} "
        "the Access world's HRIS shows as gone whose account was never removed · "
        f"**Rubric:** {STATUS}.",
        "",
        "Generated by `meridian_oauth_radar.evaluate` and checked by a test; not typed by hand. "
        f"These are **tuning numbers**: the same builder wrote the world, the {ev.high.planted} "
        "planted risky grants and the radar, so they show how the rubric behaves, not how well "
        "it would do on a world it has never seen. There is no held-out world yet (OAR-006 "
        "waits).",
        "",
        "## Headline: acted on by the radar",
        "",
        "| Metric | Result | Target | |",
        "|---|---|---|---|",
        f"| Recall: planted risky grants the radar acted on | {_rate(a.found, a.planted)} "
        f"| {RECALL_TARGET:.0%} | **{a.recall_verdict}** |",
        f"| Precision: grants the radar acted on that are planted | {_rate(a.found, a.flagged)} "
        f"| at least {PRECISION_TARGET:.2f} | **{a.precision_verdict}** |",
        "",
        f"The radar acted on {a.flagged} grants: {a.found} planted, {a.false_alarms} false "
        'alarms. "Acted on" means a notice draft, a place in a plan (after-notice or emergency) '
        "or a Slack nudge draft; a grant only listed for a person to review isn't counted. The "
        "targets are the lab brief's: precision at least 0.90 for every project, and recall "
        '100% for oauth-radar (the catalogue\'s "6 of 6": every planted grant). They are set '
        "for a held-out world. A FAIL here stays a FAIL until it is met, and a PASS on a tuning "
        "world wouldn't be the final word.",
        "",
        "## Second line: marked high by the rubric",
        "",
        "| Flagged | Plants found (recall) | False alarms | Precision |",
        "|---|---|---|---|",
        f"| {h.flagged} | {_rate(h.found, h.planted)} | {h.false_alarms} "
        f"| {_rate(h.found, h.flagged)} |",
        "",
        "OAR-002's definition of found: a planted grant the rubric rates high. Reported, with "
        "no target.",
        "",
        "## Each planted grant",
        "",
        "| App | Kind | Rubric | What the radar does |",
        "|---|---|---|---|",
        *(f"| {p.app} | {p.kind} | {p.tier}, {p.total} points | {p.outcome} |" for p in ev.plants),
        "",
        "## Where the false alarms come from",
        "",
        "| App | Marked high, not planted | Acted on, not planted |",
        "|---|---|---|",
    ]
    acted = dict(ev.acted_false_alarms_by_app)
    names = [app for app, _ in ev.false_alarms_by_app] + sorted(
        set(acted) - {app for app, _ in ev.false_alarms_by_app}
    )
    high = dict(ev.false_alarms_by_app)
    lines += [f"| {app} | {high.get(app, 0)} | {acted.get(app, 0)} |" for app in names]
    lines += [
        "",
        "## Dormant grants removed (simulation)",
        "",
        f"Of the {ev.dormant} grants unused for more than 90 days on {ev.for_date}, "
        f"{ev.dormant_in_plan} are in the after-notice plan on {ev.week_later}, after their "
        f"7-day notice: {_rate(ev.dormant_in_plan, ev.dormant)}. Only grants with a "
        f"restricted scope are planned, and {ev.excepted} under a current IT exception "
        'is left alone. Nothing is removed: "removed" means "in a reviewed plan".',
        "",
        "## What this says about the rubric",
        "",
        "- Since the owner's ruling (OAR-008), an app IT approved is never high on its reach "
        "alone, so the approved assistant and the expenses app are medium. The radar still "
        "acts on their old, unused grants with a restricted scope, through the notice.",
        "- Its false alarms are AI apps IT never reviewed: one that sees only files it made or "
        "was handed (`drive.file`) reaches 5 points through an unverified publisher, reach, AI "
        "and not reviewed; a verified transcriber gets there through calendar access, reach, AI "
        "and not reviewed.",
        "- It has no dormancy signal, so an old, unused full-Gmail grant from a verified "
        "publisher stays medium; the policy engine's dormancy rule is what catches it.",
        "- The leaver override makes the leaver's grant to the meetings app high (on its "
        "points alone it is medium), but the policy only lists it for a person: no rule acts "
        "on a leaver's grant that holds no restricted scope, so it is a miss on the acted-on "
        "count. The banned app's grant is high and gets a Slack nudge draft.",
        "",
    ]
    return "\n".join(lines)


__all__ = [
    "FAIL",
    "PASS",
    "PRECISION_TARGET",
    "RECALL_TARGET",
    "Count",
    "Evaluation",
    "PlantOutcome",
    "evaluate",
    "markdown",
    "verdict",
    "wilson",
]
