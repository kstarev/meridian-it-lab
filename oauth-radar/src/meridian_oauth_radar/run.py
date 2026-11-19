"""One radar run: collect, score, apply the policy as a dry run, draft nudges, write files.

    python -m meridian_oauth_radar --world out/oauth-radar/tuning/radar --out out/oauth-radar/run

The world folder holds the radar's inputs (the shared dataset's `radar/` files). The run
never reads the answer key. It writes, under `--out`:

- `run.json`: the date, the settings and the counts;
- `scores.json`: every grant's rubric score, with the points and reason per signal;
- `plans/after-notice-plan.json` and `plans/emergency-plan.json`: dry-run plans, never applied;
- `notices/`: notice drafts, one per user, and `ledger.json`, which the next run reads;
- `to-a-person.json`: the grants for a person in IT;
- `nudges/`: Slack message drafts, one per user, and an index;
- `report.md`: the weekly report for IT and Security, built from the files above.

The same inputs, ledger and date give byte-identical files. Nothing is sent or changed.
"""

import json
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from meridian_oauth_radar.collector import collect
from meridian_oauth_radar.enrich import load_knowledge
from meridian_oauth_radar.fake_admin import FakeAdminApi
from meridian_oauth_radar.nudges import nudge_files, nudges
from meridian_oauth_radar.policy import (
    Settings,
    decide,
    load_compromised,
    load_exceptions,
    load_ledger,
    policy_files,
)
from meridian_oauth_radar.report import weekly_report
from meridian_oauth_radar.rubric import STATUS, counts, score_all

LEDGER = "notices/ledger.json"


def _dump(obj: Any) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def reference_date(world: Path) -> date:
    """The world's snapshot date, from `world.json`."""
    meta = json.loads((world / "world.json").read_text(encoding="utf-8"))
    return datetime.fromisoformat(meta["reference_time"]).date()


def run_files(
    world: Path,
    *,
    today: date | None = None,
    ledger_path: Path | None = None,
    settings: Settings | None = None,
) -> dict[str, bytes]:
    """Every file one run writes, by path, without writing them."""
    settings = settings or Settings()
    today = today or reference_date(world)
    api = FakeAdminApi.from_files(world)
    collection = collect(api)
    knowledge = load_knowledge(world)
    found = score_all(collection.grants, knowledge)
    result = decide(
        found,
        today=today,
        ledger=load_ledger(ledger_path) if ledger_path else (),
        exceptions=load_exceptions(world / "exceptions.json"),
        compromised=load_compromised(world / "compromised_apps.json"),
        settings=settings,
    )
    drafts = nudges(result, knowledge)
    files: dict[str, bytes] = {}
    files["scores.json"] = _dump(
        {
            "rubric": STATUS,
            "grants": [
                {
                    "user_key": s.grant.user_key,
                    "client_id": s.grant.client_id,
                    "app": s.grant.display_text,
                    "tier": s.tier,
                    "total": s.total,
                    "last_used": s.grant.last_used.isoformat() if s.grant.last_used else None,
                    "signals": [asdict(p) for p in s.signals],
                    "overrides": [asdict(p) for p in s.overrides],
                }
                for s in found
            ],
        }
    )
    files.update(policy_files(result))
    files.update(nudge_files(drafts, today.isoformat()))
    apps = {s.grant.client_id for s in found}
    files["run.json"] = _dump(
        {
            "for_date": today.isoformat(),
            "rubric": STATUS,
            "dry_run": True,
            "settings": asdict(settings),
            "api_calls": len(api.calls),
            "changes_made": 0,
            "messages_sent": 0,
            "counts": {
                "users": len(collection.users),
                "grants": len(collection.grants),
                "apps": len(apps),
                "tiers": dict(counts(found)),
                "dormant_restricted": result.dormant_restricted,
                "notices_new": len(result.new_notices),
                "notices_waiting": len(result.waiting),
                "after_notice_plan": len(result.plan.items),
                "emergency_plan": len(result.emergency.items),
                "to_a_person": len(result.to_a_person),
                "excepted": len(result.excepted),
                "exceptions_expired": len(result.expired_exceptions),
                "no_action": result.no_action,
                "nudges": len(drafts),
            },
            "apps": {
                client: {
                    "vendor": facts.vendor,
                    "data_location": facts.data_location,
                    "vendor_maturity": facts.vendor_maturity,
                    "it_status": facts.it_status,
                }
                for client in sorted(apps)
                for facts in (knowledge.facts(client),)
            },
        }
    )
    files["report.md"] = weekly_report(files).encode()
    return files


def write_run(files: dict[str, bytes], out: Path) -> dict[str, Path]:
    paths = {}
    for name, data in sorted(files.items()):
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        paths[name] = path
    return paths


__all__ = ["LEDGER", "reference_date", "run_files", "write_run"]
