"""Milestone 3f: the how-to gate scored on dev data, composed with recorded runs.

The gate sits after triage and before the picker, and it can only hand a message to a person.
So a recorded run that was handed off stays handed off, and only a run that reached a guide
(stage `picker_guide`) can change: the gate is called once for that run, and the run becomes a
hand-off unless the gate says yes. Everything else in the run (triage, the picker's pick) is
taken from the published records, made with the frozen picker KS6A-H. This is a composed dev
estimate, labelled so; the rehearsal then runs the whole path live (docs/plans/
milestone-3f-how-to-gate.md).

The recorded runs:

- tuning_month: the 3e rehearsal, both arms (docs/evals/2026-11-19-m3e-rehearsal-*.json);
- held_out_month1: the 3e dev comparison's KS6A-H run, haiku-3d2 only. The sonnet-3d2 arm is
  composed from the same runs, since both arms share the picker; its triage differs, so that
  row is marked;
- held_out_month2 and held_out_month2b: their sealed runs, both arms. All three months are dev
  sets (ADR-049, ADR-052): their numbers are dev, not blind.

    python -m tier0.evals.gate_dev run --version G1 --arm haiku-3d2 --out out/gate-dev

A Claude run must be charged to tier0-3e (TIER0_CLAUDE_BUDGET_ITEM). It resumes from its
calls file, so a stopped run loses nothing.
"""

import argparse
import json
import os
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from meridian_dataset.month import (
    HELD_OUT_MONTH1,
    HELD_OUT_MONTH2,
    HELD_OUT_MONTH2B,
    TUNING_MONTH,
    month_for,
)
from tier0.evals.month import BUDGET_ITEM, slowness_only_templates
from tier0.evals.setups import ALL_SETUPS
from tier0.knowledge.gate import GATE_VERSIONS, gate_request, parse_gate
from tier0.models.base import ModelAdapter, ModelError
from tier0.models.spend import ITEM_ENV

ROOT = Path(__file__).resolve().parents[4]
EVALS = ROOT / "docs" / "evals"
ARMS = ("haiku-3d2", "sonnet-3d2")
SETS = (TUNING_MONTH, HELD_OUT_MONTH1, HELD_OUT_MONTH2, HELD_OUT_MONTH2B)
PICKER_GUIDE = "picker_guide"

# (set, arm) -> (the published file, the arm whose runs it holds)
_REHEARSAL = "2026-11-19-m3e-rehearsal-tuning-month-{}.json"
_DEV = "2026-11-18-m3e-picker-candidates-dev.json"
_SEALED = "2026-11-19-m3e-sealed-{}-{}.json"
SOURCES: dict[tuple[str, str], tuple[str, str]] = {
    (TUNING_MONTH, "haiku-3d2"): (_REHEARSAL.format("haiku-3d2"), "haiku-3d2"),
    (TUNING_MONTH, "sonnet-3d2"): (_REHEARSAL.format("sonnet-3d2"), "sonnet-3d2"),
    (HELD_OUT_MONTH1, "haiku-3d2"): (_DEV, "haiku-3d2"),
    (HELD_OUT_MONTH1, "sonnet-3d2"): (_DEV, "haiku-3d2"),
    (HELD_OUT_MONTH2, "haiku-3d2"): (_SEALED.format("month2", "haiku-3d2"), "haiku-3d2"),
    (HELD_OUT_MONTH2, "sonnet-3d2"): (_SEALED.format("month2", "sonnet-3d2"), "sonnet-3d2"),
    (HELD_OUT_MONTH2B, "haiku-3d2"): (_SEALED.format("month2b", "haiku-3d2"), "haiku-3d2"),
    (HELD_OUT_MONTH2B, "sonnet-3d2"): (_SEALED.format("month2b", "sonnet-3d2"), "sonnet-3d2"),
}
KS6A_H_SHA256 = "04fdb9942e214d3e8598c0ff1f127cc97eb28eeb51fc6d350d7417f1f1050888"


@dataclass(frozen=True, slots=True)
class Run:
    """One recorded run of one ticket."""

    ticket_id: str
    run: int
    outcome: str
    guide: str | None
    stage: str


def recorded_runs(set_name: str, arm: str, evals: Path = EVALS) -> list[Run]:
    """The published runs made with KS6A-H, for this set and arm (see SOURCES)."""
    name, holder = SOURCES[(set_name, arm)]
    data = json.loads((evals / name).read_text(encoding="utf-8"))
    if "runs" in data and "tickets" not in data:  # the 3e dev comparison
        if data["arm"] != holder:
            raise ValueError(f"{name} holds {data['arm']}, not {holder}")
        (block,) = [r for r in data["runs"] if r["set"] == set_name and r["version"] == "KS6A-H"]
        if block["picker_prompt_sha256"] != KS6A_H_SHA256:
            raise ValueError(f"{name}: the KS6A-H run has another picker hash")
        return [Run(t, n, o, g, s) for t, n, o, g, s, _ in block["records"]]
    config = data["config"]
    if (config["set"], config["setup"]) != (set_name, holder):
        raise ValueError(f"{name} is {config['set']}/{config['setup']}")
    if config["picker_prompt_sha256"] != KS6A_H_SHA256:
        raise ValueError(f"{name} was not run with KS6A-H")
    return [
        Run(t["ticket_id"], r["run"], r["outcome"], r["guide"], r["stage"])
        for t in data["tickets"]
        for r in t["runs"]
    ]


def gate_model(arm: str) -> str:
    """The gate runs on the arm's own triage model."""
    return ALL_SETUPS[arm].triage_model


def compose(
    runs: Iterable[Run], verdicts: Mapping[tuple[str, int], bool | None]
) -> list[tuple[Run, str, str | None, bool | None]]:
    """(run, outcome, guide, the gate's verdict or None if it wasn't asked) for every run. A run
    that reached a guide keeps it only if the gate said yes."""
    out = []
    for r in runs:
        if r.stage != PICKER_GUIDE:
            out.append((r, r.outcome, r.guide, None))
            continue
        verdict = verdicts[(r.ticket_id, r.run)]
        if verdict is True:
            out.append((r, r.outcome, r.guide, True))
        else:
            out.append((r, "hand_off", None, verdict))
    return out


def score(
    set_name: str, composed: list[tuple[Run, str, str | None, bool | None]]
) -> dict[str, Any]:
    """Rule 1 and rule 2 over the composed runs, with counts by kind."""
    tickets = {t.id: t for t in month_for(set_name).tickets}
    slow = slowness_only_templates(set_name)
    wrong: Counter[str] = Counter()
    wrong_tickets: set[str] = set()
    how_to = right = 0
    how_to_no_slow = right_no_slow = 0
    gate_said: Counter[str] = Counter()
    gate_no_how_to: Counter[str] = Counter()
    for r, outcome, guide, verdict in composed:
        t = tickets[r.ticket_id]
        if verdict is not None or r.stage == PICKER_GUIDE:
            gate_said[
                f"{t.kind}:{'yes' if verdict else 'no' if verdict is False else 'failed'}"
            ] += 1
        if t.expected == "hand_off" and outcome != "hand_off":
            wrong[t.kind] += 1
            wrong_tickets.add(t.id)
        if t.expected == "answer_guide":
            ok = outcome == "answer_guide" and guide == t.expected_guide
            how_to += 1
            right += ok
            if t.template_id not in slow:
                how_to_no_slow += 1
                right_no_slow += ok
            if r.stage == PICKER_GUIDE and verdict is not True:
                gate_no_how_to[t.template_id] += 1
    return {
        "set": set_name,
        "wrongly_answered_runs": sum(wrong.values()),
        "wrongly_answered_by_kind": dict(sorted(wrong.items())),
        "wrongly_answered_tickets": sorted(wrong_tickets),
        "how_to_runs": how_to,
        "right_guide_runs": right,
        "right_guide": round(right / how_to, 4) if how_to else None,
        "right_guide_without_slowness_only": (
            round(right_no_slow / how_to_no_slow, 4) if how_to_no_slow else None
        ),
        "gate_calls_by_kind": dict(sorted(gate_said.items())),
        "how_to_runs_the_gate_handed_off_by_template": dict(sorted(gate_no_how_to.items())),
    }


def passes(summary: Mapping[str, Any]) -> bool:
    """The plan's selection rule, parts 1 and 2, for one set: 0 wrongly answered, and the right
    guide in at least 0.90 of how-to runs."""
    rg = summary["right_guide"]
    return summary["wrongly_answered_runs"] == 0 and rg is not None and rg >= 0.90


def ask_gate(model: ModelAdapter, system: str, text: str) -> bool | None:
    try:
        return parse_gate(model.complete(gate_request(text, system)))
    except ModelError:
        return None


def run(
    version: str,
    arm: str,
    out: Path,
    *,
    make_model: Callable[[str], ModelAdapter],
    sets: Iterable[str] = SETS,
    progress: Callable[[str], None] = lambda _: None,
) -> dict[str, Any]:
    """Score one gate version on one arm. The calls file makes the run resumable."""
    system = GATE_VERSIONS[version]
    out.mkdir(parents=True, exist_ok=True)
    calls_path = out / f"gate-{version}-{arm}.calls.jsonl"
    done: dict[tuple[str, str, int], bool | None] = {}
    if calls_path.exists():
        for line in calls_path.read_text(encoding="utf-8").splitlines():
            c = json.loads(line)
            done[(c["set"], c["ticket_id"], c["run"])] = c["verdict"]
    model = make_model(gate_model(arm))
    summaries = []
    with calls_path.open("a", encoding="utf-8") as calls:
        for set_name in sets:
            texts = {t.id: t.text for t in month_for(set_name).tickets}
            runs = recorded_runs(set_name, arm)
            verdicts: dict[tuple[str, int], bool | None] = {}
            for r in runs:
                if r.stage != PICKER_GUIDE:
                    continue
                key = (set_name, r.ticket_id, r.run)
                if key not in done:
                    done[key] = ask_gate(model, system, texts[r.ticket_id])
                    calls.write(
                        json.dumps(
                            {
                                "set": set_name,
                                "ticket_id": r.ticket_id,
                                "run": r.run,
                                "verdict": done[key],
                            }
                        )
                        + "\n"
                    )
                    calls.flush()
                verdicts[(r.ticket_id, r.run)] = done[key]
            summary = score(set_name, compose(runs, verdicts))
            summary["runs_from"] = SOURCES[(set_name, arm)][1]
            summary["passes"] = passes(summary)
            summaries.append(summary)
            progress(
                f"{arm} {version} {set_name}: wrongly answered {summary['wrongly_answered_runs']}, "
                f"right guide {summary['right_guide']}"
            )
    return {
        "version": version,
        "arm": arm,
        "gate_model": gate_model(arm),
        "picker": "KS6A-H",
        "label": "composed dev estimate: the gate on recorded KS6A-H runs (dev, not blind)",
        "sets": summaries,
        "passes_every_set": all(s["passes"] for s in summaries),
    }


def _claude(name: str) -> ModelAdapter:
    from tier0.models.claude import ClaudeModel

    return ClaudeModel(model=name, cache_system=True)


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tier0.evals.gate_dev")
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="score one gate version on one arm (paid)")
    r.add_argument("--version", required=True, choices=sorted(GATE_VERSIONS))
    r.add_argument("--arm", required=True, choices=ARMS)
    r.add_argument("--out", type=Path, default=Path("out/gate-dev"))
    args = parser.parse_args(argv)
    env = os.environ if env is None else env
    if env.get(ITEM_ENV) != BUDGET_ITEM:
        print(f"refused: a gate run is paid by {BUDGET_ITEM}; {ITEM_ENV} is not", file=sys.stderr)
        return 2
    result = run(args.version, args.arm, args.out, make_model=_claude, progress=print)
    path = args.out / f"gate-{args.version}-{args.arm}.json"
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path}; passes every set: {result['passes_every_set']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
