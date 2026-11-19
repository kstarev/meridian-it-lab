"""Precision and recall on the tuning world, graded by code against the planted answer key.

This is the grader, and the only module that reads an answer key. The radar itself (the
collector, enricher, rubric, policy, nudges and report) never does: `run.run_files` takes
only the world's `radar/` folder, and a test runs it with no key on disk at all.

Two ways of counting, both reported:

- **Marked high by the rubric** (OAR-002's definition of "found"): a planted risky grant
  is found when the rubric rates it high; any other high grant is a false alarm.
- **Acted on by the radar:** a notice draft, a place in a plan (after-notice or emergency),
  or a Slack nudge draft. A grant that is only listed for a person to review isn't counted,
  because that list holds every high and medium grant.

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
    return Evaluation(
        for_date=today.isoformat(),
        week_later=later_date.isoformat(),
        grants=len(scores),
        high=Count("Marked high by the rubric", len(high), len(high & planted), len(planted)),
        acted=Count("Acted on by the radar", len(acted), len(acted & planted), len(planted)),
        plants=plants,
        false_alarms_by_app=per_app(high),
        acted_false_alarms_by_app=per_app(acted),
        dormant=len(dormant),
        dormant_in_plan=len(dormant & after),
        excepted=json.loads(day0["run.json"])["counts"]["excepted"],
    )


def _rate(k: int, n: int) -> str:
    low, high = wilson(k, n)
    share = k / n if n else 0.0
    return f"{k} of {n} ({share:.1%}; 95% CI {low:.1%} to {high:.1%})"


def markdown(ev: Evaluation) -> str:
    """The published report. A test checks the committed copy matches this output."""
    lines = [
        "# Shadow AI Radar: precision and recall on the tuning world",
        "",
        f"**Date:** 2026-11-19 · **World:** the OAuth tuning world (seed 9137, snapshot "
        f"{ev.for_date}), {ev.grants} grants for the 230 shared Meridian people · "
        f"**Rubric:** {STATUS}.",
        "",
        "Generated by `meridian_oauth_radar.evaluate` and checked by a test; not typed by hand. "
        "These are **tuning numbers**: the same builder wrote the world, the 6 planted risky "
        "grants and the radar, so they show how the rubric behaves, not how well it would do on "
        "a world it has never seen. There is no held-out world yet (OAR-006 waits).",
        "",
        "## Found and false alarms",
        "",
        "| Counting | Flagged | Plants found (recall) | False alarms | Precision |",
        "|---|---|---|---|---|",
    ]
    for c in (ev.high, ev.acted):
        lines.append(
            f"| {c.name} | {c.flagged} | {_rate(c.found, c.planted)} | {c.false_alarms} "
            f"| {_rate(c.found, c.flagged)} |"
        )
    lines += [
        "",
        '"Marked high" is OAR-002\'s definition of found. "Acted on" counts a notice '
        "draft, a place in a plan (after-notice or emergency) or a Slack nudge draft; a grant "
        "only listed for a person to review isn't counted.",
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
        "",
    ]
    return "\n".join(lines)


__all__ = ["Count", "Evaluation", "PlantOutcome", "evaluate", "markdown", "wilson"]
