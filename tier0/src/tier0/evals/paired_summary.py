"""Milestone 3b's result tables, generated from the published paired-run records (ADR-038):

    python -m tier0.evals.paired_summary docs/evals/2026-11-15-m3b-held-out-v4-v4b.json \\
        --doc docs/how-it-works.md --readme README.md

The summary .json holds the numbers of the three-arm paired run on held-out v4 and v4b. This
writes the v4 table, the v4b table and the cost table between their markers in the doc, and the
short follow-up table between its markers in the README. Nothing is re-run or re-scored, and
the other generated blocks are left as they are.

The summary itself is rebuilt from the run records published next to it (one report per set,
arm and model, exactly as the run wrote them) and from the local spend ledger:

    python -m tier0.evals.paired_summary docs/evals/2026-11-15-m3b-held-out-v4-v4b.json \\
        --collect docs/evals/2026-11-15-m3b-runs --ledger out/claude-spend.jsonl

Only numbers and ticket ids are read from a record. No message wording is in the records, and
none is written here.
"""

import argparse
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from tier0.evals import readme_blocks
from tier0.evals.compare_many import EVERYDAY_TARGET
from tier0.evals.paired_run import ARMS

ITEM = "tier0-3b"
SETS = ("held_out_v4", "held_out_v4b")
MODELS = ("haiku", "sonnet")
ARM_NAMES = tuple(arm for arm, _, _ in ARMS)
ARM_LABELS = {
    "arm1-3a": "1: 3a prompts, old signal",
    "arm2-new-prompts": "2: 3b prompts, old signal",
    "arm3-3b": "3: 3b prompts, with the fix",
}
RECORDS_DIR = "2026-11-15-m3b-runs"

V4_START, V4_END = "<!-- results-3b-v4:start -->", "<!-- results-3b-v4:end -->"
V4B_START, V4B_END = "<!-- results-3b-v4b:start -->", "<!-- results-3b-v4b:end -->"
COST_START, COST_END = "<!-- cost-3b:start -->", "<!-- cost-3b:end -->"
README_START, README_END = "<!-- results-3b:start -->", "<!-- results-3b:end -->"


def record_stem(set_name: str, arm: str, model: str) -> str:
    """A published run record's file name, without its suffix."""
    return f"{set_name.replace('_', '-')}-{arm}-{model}"


# From the run records to the summary.


def _one_run(r: Mapping[str, Any]) -> dict[str, Any]:
    runs = r["runs_detail"]
    fails: Counter[tuple[str, str, str | None, str, str]] = Counter()
    for x in runs:
        if not x["passed"]:
            kind = "self" if x["self_described"] else x["scenario"]
            fails[
                (x["ticket"], kind, x["category"], x["action"], ",".join(sorted(x["reasons"])))
            ] += 1
    self_runs = [x for x in runs if x["self_described"]]
    unnamed = [x for x in runs if x["scenario"] == "colleague_lookup" and not x["colleague_named"]]
    plain = [x for x in runs if x["scenario"] == "normal" and not x["self_described"]]
    everyday = [t for t in r["tickets"] if t["scenario"] == "normal"]
    return {
        "setup": r["setup"],
        "k": r["k"],
        "tickets": len(r["tickets"]),
        "runs": len(runs),
        "pass_1": r["pass_1"],
        "pass_1_ci": r["pass_1_ci"],
        "pass_k": r["pass_k"],
        "pass_k_ci": r["pass_k_ci"],
        "everyday_pass_k": r["everyday_pass_k"],
        "everyday_tickets": len(everyday),
        "everyday_all_k": sum(1 for t in everyday if t["runs_passed"] == t["runs"]),
        "plain_normal_runs": len(plain),
        "plain_normal_passed": sum(x["passed"] for x in plain),
        "self": r["self_described_outcomes"],
        "colleague": r["colleague_outcomes"],
        "unsafe_runs": r["unsafe_runs"],
        "policy_violation_runs": r["policy_violation_runs"],
        "data_exposed_runs": r["data_exposed_runs"],
        "escalation": [r["escalation_correct"], r["escalation_total"]],
        "unexpected_handoffs": r["unexpected_handoffs"],
        "handoff_runs": r["handoff_runs"],
        "attack": {k: dict(v) for k, v in r["attack_by_category"].items()},
        "triage_category_runs": dict(Counter(x["category"] for x in runs)),
        "self_tickets_signal_fired": len({x["ticket"] for x in self_runs if x["role_signal"]}),
        "self_runs_signal_fired": sum(1 for x in self_runs if x["role_signal"]),
        "unnamed_tickets_signal_fired": len({x["ticket"] for x in unnamed if x["role_signal"]}),
        "cost_usd": round(sum(x["cost_usd"] for x in runs), 4),
        "failed_runs": [
            {
                "ticket": t,
                "kind": kind,
                "triage": triage,
                "action": action,
                "reasons": why,
                "runs": n,
            }
            for (t, kind, triage, action, why), n in sorted(
                fails.items(), key=lambda item: tuple(str(part) for part in item[0])
            )
        ],
    }


def collect(records: Path) -> dict[str, Any]:
    """The summary's numbers for both sets, from the run records in `records`."""
    out: dict[str, Any] = {}
    for set_name in SETS:
        out[set_name] = {}
        for arm in ARM_NAMES:
            for model in MODELS:
                path = records / f"{record_stem(set_name, arm, model)}.json"
                out[set_name][f"{arm}/{model}"] = _one_run(json.loads(path.read_text()))
    return out


def final_runs_usd(sets: Mapping[str, Any]) -> dict[str, float]:
    return {s: round(sum(v["cost_usd"] for v in sets[s].values()), 4) for s in SETS}


def collect_summary(records: Path, ledger: Path, cap_usd: float) -> dict[str, Any]:
    sets = collect(records)
    lines = [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]
    spent = sum(e["cost_usd"] for e in lines if e.get("budget_item") == ITEM)
    spend = {
        "item": ITEM,
        "cap_usd": cap_usd,
        "spent_usd": round(spent, 4),
        "final_runs_usd": final_runs_usd(sets),
    }
    return {**sets, "spend": spend}


# From the summary to the tables.


def _ci(interval: Sequence[float]) -> str:
    return f"{interval[0]:.2f}-{interval[1]:.2f}"


def _rows(summary: Mapping[str, Any], set_name: str) -> list[tuple[str, str, Mapping[str, Any]]]:
    return [
        (arm, model, summary[set_name][f"{arm}/{model}"]) for arm in ARM_NAMES for model in MODELS
    ]


def _one(values: Any) -> Any:
    (value,) = set(values)
    return value


def _per_1000(run: Mapping[str, Any]) -> str:
    return f"${1000 * run['cost_usd'] / run['runs']:.2f}"


def _passed(group: Mapping[str, Any]) -> int:
    """How many tickets of a group passed every run."""
    return round(group["pass_k"] * group["tickets"])


def _setup_link(set_name: str, arm: str, model: str, run: Mapping[str, Any]) -> str:
    return f"[{run['setup']}](evals/{RECORDS_DIR}/{record_stem(set_name, arm, model)}.md)"


def _intro(report: str, set_label: str, tickets: int, k: int) -> str:
    return (
        f"Generated by `python -m tier0.evals.paired_summary` from [{report}](evals/{report}) and "
        f"the run records it links \N{EM DASH} do not edit by hand. {set_label}, {tickets} "
        f"tickets, k = {k}. Each setup name links to its run record."
    )


def v4_block(summary: Mapping[str, Any], report: str) -> str:
    rows = _rows(summary, "held_out_v4")
    k = _one(run["k"] for _, _, run in rows)
    tickets = _one(run["tickets"] for _, _, run in rows)
    lines = [
        V4_START,
        "",
        _intro(report, "Held-out v4", tickets, k),
        "",
        f"| Arm | Setup | pass^{k} (95% CI); tickets | pass^1 | Everyday pass^{k} vs target "
        f"| Self-described: pass^{k} (95% CI); outcomes | Colleague requests: runs refused "
        "| Must go to a person: runs sent there | Unsafe runs | API cost per 1,000 tickets |",
        "| " + " | ".join(["---"] * 10) + " |",
    ]
    for arm, model, run in rows:
        own, all_colleague = run["self"], run["colleague"]["all"]
        mark = "meets" if run["everyday_pass_k"] >= EVERYDAY_TARGET else "below"
        lines.append(
            f"| {ARM_LABELS[arm]} | {_setup_link('held_out_v4', arm, model, run)} "
            f"| {run['pass_k']:.2f} ({_ci(run['pass_k_ci'])}); "
            f"{round(run['pass_k'] * run['tickets'])} of {run['tickets']} "
            f"| {run['pass_1']:.2f} "
            f"| {run['everyday_pass_k']:.2f} ({mark} {EVERYDAY_TARGET:.2f}) "
            f"| {own['pass_k']:.2f} ({_ci(own['pass_k_ci'])}); answered {own['answered']}, "
            f"refused {own['refused']}, handed off {own['handed_off']} "
            f"| {all_colleague['refused']} of {all_colleague['runs']} "
            f"| {run['escalation'][0]} of {run['escalation'][1]} "
            f"| {run['unsafe_runs']} | {_per_1000(run)} |"
        )
    lines += ["", V4_END]
    return "\n".join(lines)


def _role_signal_handoffs(run: Mapping[str, Any]) -> int:
    return sum(f["runs"] for f in run["failed_runs"] if f["reasons"] == "role_reference")


def v4b_block(summary: Mapping[str, Any], report: str) -> str:
    rows = _rows(summary, "held_out_v4b")
    k = _one(run["k"] for _, _, run in rows)
    tickets = _one(run["tickets"] for _, _, run in rows)
    lines = [
        V4B_START,
        "",
        _intro(report, "Held-out v4b", tickets, k),
        "",
        f"| Arm | Setup | Self-described: pass^{k} (95% CI); tickets; outcomes "
        "| Self-described runs handed off by the role signal "
        f"| Unnamed colleague: pass^{k} (95% CI); tickets; outcomes | Unsafe runs "
        "| API cost per 1,000 tickets |",
        "| " + " | ".join(["---"] * 7) + " |",
    ]
    for arm, model, run in rows:
        own, unnamed = run["self"], run["colleague"]["unnamed"]
        lines.append(
            f"| {ARM_LABELS[arm]} | {_setup_link('held_out_v4b', arm, model, run)} "
            f"| {own['pass_k']:.2f} ({_ci(own['pass_k_ci'])}); {_passed(own)} of {own['tickets']}; "
            f"answered {own['answered']}, refused {own['refused']}, "
            f"handed off {own['handed_off']} "
            f"| {_role_signal_handoffs(run)} "
            f"| {unnamed['pass_k']:.2f} ({_ci(unnamed['pass_k_ci'])}); {_passed(unnamed)} of "
            f"{unnamed['tickets']}; refused {unnamed['refused']}, "
            f"handed off {unnamed['handed_off']}, answered {unnamed['answered']} "
            f"| {run['unsafe_runs']} | {_per_1000(run)} |"
        )
    lines += ["", V4B_END]
    return "\n".join(lines)


def cost_block(summary: Mapping[str, Any], report: str) -> str:
    spend = summary["spend"]
    lines = [
        COST_START,
        "",
        f"Generated by `python -m tier0.evals.paired_summary` from [{report}](evals/{report}) "
        "\N{EM DASH} do not edit by hand. Cost is API cost only, from token usage, over every run. "
        "Per-ticket costs are from held-out v4.",
        "",
        "| Setup | Prompts | API cost per ticket | API cost per 1,000 tickets |",
        "| --- | --- | --- | --- |",
    ]
    for arm, prompts in (("arm1-3a", "3a"), ("arm3-3b", "3b")):
        for model in MODELS:
            run = summary["held_out_v4"][f"{arm}/{model}"]
            lines.append(
                f"| {_setup_link('held_out_v4', arm, model, run)} | {prompts} "
                f"| ${run['cost_usd'] / run['runs']:.5f} | {_per_1000(run)} |"
            )
    lines += ["", "| Milestone 3b | Spend |", "| --- | --- |"]
    for set_name, label in (("held_out_v4", "v4"), ("held_out_v4b", "v4b")):
        runs = sum(run["runs"] for run in summary[set_name].values())
        lines.append(
            f"| The paired run on {label} ({runs:,} ticket-runs) "
            f"| ${spend['final_runs_usd'][set_name]:.2f} |"
        )
    lines += [
        f"| All of `{spend['item']}`: screening, tuning, the dev-set check and both paired runs "
        f"| ${spend['spent_usd']:.2f} of its ${spend['cap_usd']:.0f} cap |",
        "",
        COST_END,
    ]
    return "\n".join(lines)


README_ARMS = ("Old version", "New instructions only", "New instructions and the fix")


def readme_block(summary: Mapping[str, Any]) -> str:
    """The README's short follow-up table: tickets right on every try, per arm."""
    v4, v4b = summary["held_out_v4"], summary["held_out_v4b"]
    k = _one(run["k"] for runs in (v4, v4b) for run in runs.values())

    def whole(run: Mapping[str, Any]) -> tuple[int, int]:
        return round(run["pass_k"] * run["tickets"]), run["tickets"]

    def own(run: Mapping[str, Any]) -> tuple[int, int]:
        return _passed(run["self"]), run["self"]["tickets"]

    def unnamed(run: Mapping[str, Any]) -> tuple[int, int]:
        group = run["colleague"]["unnamed"]
        return _passed(group), group["tickets"]

    tests = (
        (v4, whole, "{n} new requests of every kind"),
        (v4b, own, "{n} short requests from people describing themselves"),
        (v4b, unnamed, "{n} short requests for a colleague's login, with no name given"),
    )
    lines = [
        README_START,
        "<!-- Generated by python -m tier0.evals.paired_summary from the published milestone 3b "
        "report; do not edit by hand. -->",
        "",
        f"Requests handled right on all {k} tries:",
        "",
        "| Test | AI setup | " + " | ".join(README_ARMS) + " | Unsafe actions |",
        "| " + " | ".join(["---"] * 6) + " |",
    ]
    for runs, count, label in tests:
        for model in MODELS:
            cells = [count(runs[f"{arm}/{model}"]) for arm in ARM_NAMES]
            total = _one(n for _, n in cells)
            unsafe = sum(runs[f"{arm}/{model}"]["unsafe_runs"] for arm in ARM_NAMES)
            lines.append(
                f"| {label.format(n=total)} | {model} | "
                + " | ".join(f"{passed} of {n}" for passed, n in cells)
                + f" | {unsafe} |"
            )
    lines += ["", README_END]
    return "\n".join(lines)


def render_blocks(summary_path: Path) -> tuple[str, str, str, str]:
    """The doc's v4, v4b and cost blocks and the README block."""
    summary = json.loads(summary_path.read_text())
    report = summary_path.with_suffix(".md").name
    return (
        v4_block(summary, report),
        v4b_block(summary, report),
        cost_block(summary, report),
        readme_block(summary),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tier0.evals.paired_summary",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("summary", type=Path)
    parser.add_argument("--doc", type=Path)
    parser.add_argument("--readme", type=Path)
    parser.add_argument("--collect", type=Path, metavar="RECORDS")
    parser.add_argument("--ledger", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.collect is not None:
            if args.ledger is None:
                raise ValueError("--collect needs --ledger")
            cap = json.loads(args.summary.read_text())["spend"]["cap_usd"]
            summary = collect_summary(args.collect, args.ledger, cap)
            args.summary.write_text(json.dumps(summary, indent=2) + "\n")
            print(f"summary: {args.summary}")
            return 0
        if args.doc is None or args.readme is None:
            raise ValueError("pass --doc and --readme, or --collect and --ledger")
        v4, v4b, cost, readme = render_blocks(args.summary)
        doc_text = args.doc.read_text()
        for block, start, end, what in (
            (v4, V4_START, V4_END, "3b v4"),
            (v4b, V4B_START, V4B_END, "3b v4b"),
            (cost, COST_START, COST_END, "3b cost"),
        ):
            doc_text = readme_blocks.replace_between(doc_text, block, start, end, what)
        readme_text = readme_blocks.replace_between(
            args.readme.read_text(), readme, README_START, README_END, "3b results"
        )
    except (ValueError, OSError, KeyError) as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    args.doc.write_text(doc_text)
    args.readme.write_text(readme_text)
    print(f"doc: {args.doc}")
    print(f"readme: {args.readme}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
