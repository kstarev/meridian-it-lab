"""Milestone 3d's screening and tuning, on tuning data only.

The plan: docs/plans/milestone-3d-injection-relayed.md.

    python -m tier0.evals.m3d_screening screen --setup haiku-3b
    python -m tier0.evals.m3d_screening score --model claude-haiku-5-5 --version HT4 --all
    python -m tier0.evals.m3d_screening show

`screen` scores one setup's triage and check prompts on 3d's tuning messages (prompt injection
inside messages; requests relayed or forwarded in someone else's own words), k = 3, with the
3b scorer. `score --all` scores one prompt version on every tuning message (3b's 116 plus 3d's)
for the selection rule. Outsiders are left out: identity binding stops them in code before any
model call, so no prompt can change their outcome.

Results go to out/m3d/, one file per (model, version, set), never overwritten.
"""

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from meridian_dataset import DEFAULT_SEED, generate
from meridian_dataset.generator import _fill_colleague
from meridian_dataset.names import TUNING_3D_POOL
from tier0.evals import m3_selection
from tier0.evals.m3_selection import Instance
from tier0.evals.setups import ALL_SETUPS
from tier0.netguard import guard_for_model

RESULTS = Path("out/m3d")
LABELS = Path(__file__).resolve().parents[4] / "dataset/src/meridian_dataset/pools"
SCREENED_SETUPS = ("haiku", "haiku-3b", "sonnet-3b")


def labels(pool_name: str = "tuning_3d") -> dict[str, list[str]]:
    return json.loads((LABELS / f"{pool_name}_labels.json").read_text(encoding="utf-8"))


def instances_3d() -> list[tuple[Instance, str]]:
    """3d's tuning messages as scorer cases, each with its sub-group label."""
    world = generate(DEFAULT_SEED)
    second = world.person_by_email("alex.kim2@meridian.example")
    sender = next(p for p in world.people if p.status == "active" and p is not second)
    report = next(p for p in world.people if p.manager_id and p.status == "active")
    manager = world.person_by_id(report.manager_id or "")
    if manager is None:
        raise LookupError("the tuning world has no manager for the colleague target")
    names = labels()
    out: list[tuple[Instance, str]] = []
    for text, label in zip(TUNING_3D_POOL.normal, names["normal"], strict=True):
        out.append(
            (Instance("everyday", False, text, sender.full_name, sender.primary_email), label)
        )
    for text, label in zip(TUNING_3D_POOL.self_described, names["self_described"], strict=True):
        case = Instance("self_described", False, text, sender.full_name, sender.primary_email)
        out.append((case, label))
    for text, label in zip(TUNING_3D_POOL.colleague, names["colleague"], strict=True):
        case = Instance(
            "colleague",
            "{target" not in text,
            _fill_colleague(text, report),
            manager.full_name,
            manager.primary_email,
        )
        out.append((case, label))
    return out


def by_label(result: dict[str, Any], cases: Sequence[tuple[Instance, str]]) -> dict[str, Any]:
    """Correct runs per (group, label), from the scorer's list of wrong answers."""
    k = result["k"]
    wrong: Counter[tuple[str, str]] = Counter()
    for case, label in cases:
        key = f"{case.group}: {case.text[:70]}"
        wrong[(case.group, label)] += result["wrong"].get(key, 0)
    totals = Counter((case.group, label) for case, label in cases)
    return {
        f"{group}/{label}": {"runs": n * k, "correct": n * k - wrong[(group, label)]}
        for (group, label), n in sorted(totals.items())
    }


def _path(model: str, version: str, set_name: str) -> Path:
    return RESULTS / f"{model}__{version}__{set_name}.json"


def _save(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".json.partial")
    partial.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    partial.replace(path)


def screen(setup_name: str, *, model_factory: Any = None) -> list[Path]:
    setup = ALL_SETUPS[setup_name]
    cases = instances_3d()
    saved = []
    for model, version in (
        (setup.triage_model, setup.triage_prompt),
        (setup.check_model, setup.check_prompt),
    ):
        path = _path(model, version, "tuning_3d")
        if path.exists():
            saved.append(path)
            continue
        made = model_factory(model) if model_factory is not None else None
        result = m3_selection.score(model, version, model=made, cases=[c for c, _ in cases])
        result["set"] = "tuning_3d"
        result["by_label"] = by_label(result, cases)
        _save(path, result)
        saved.append(path)
    return saved


def score_all(model: str, version: str, *, model_factory: Any = None) -> Path:
    """One version on every tuning message: 3b's 116 plus 3d's, for the selection rule."""
    path = _path(model, version, "all")
    if path.exists():
        return path
    cases = [*m3_selection.instances(), *(c for c, _ in instances_3d())]
    made = model_factory(model) if model_factory is not None else None
    result = m3_selection.score(model, version, model=made, cases=cases)
    result["set"] = "all"
    _save(path, result)
    return path


def show() -> str:
    lines = []
    for path in sorted(RESULTS.glob("*.json")):
        r = json.loads(path.read_text())
        groups = ", ".join(
            f"{g} {r['correct'][g]}/{r['total'][g]}" for g in r["total"] if r["total"][g]
        )
        lines.append(f"{r['model']} {r['version']} {r['set']}: {groups}; ${r['cost_usd']:.4f}")
        for name, v in r.get("by_label", {}).items():
            lines.append(f"    {name}: {v['correct']}/{v['runs']}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tier0.evals.m3d_screening", description=__doc__
    )
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("screen")
    s.add_argument("--setup", required=True, choices=SCREENED_SETUPS)
    a = sub.add_parser("score")
    a.add_argument("--model", required=True)
    a.add_argument("--version", required=True)
    a.add_argument("--all", action="store_true", required=True)
    sub.add_parser("show")
    args = parser.parse_args(argv)
    if args.command == "show":
        print(show())
        return 0
    with guard_for_model("claude"):
        if args.command == "screen":
            for path in screen(args.setup):
                print(f"saved: {path}")
        else:
            print(f"saved: {score_all(args.model, args.version)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
