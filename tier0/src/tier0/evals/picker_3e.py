"""Milestone 3e's dev comparison of the picker-fix candidates.

Dev and tuning numbers only: it selects nothing for a sealed run. It makes no model call. It
reads the runner's result files (one per candidate and set), re-grades the saved outcomes under
the two small-talk options, and writes a table and a recommendation by a rule that is in this
file, so the rule is committed before any number is read.

    python -m tier0.evals.picker_3e collect --run KS6A-H/tuning_month=PATH ... --out data.json
    python -m tier0.evals.picker_3e report --data data.json --out report.md

The collected data holds ids, enums and numbers only: no ticket text, sender or wording.
"""

import argparse
import dataclasses
import json
import re
import sys
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

from meridian_dataset.month import ANSWER_GUIDE, Month, MonthTicket, month_for
from tier0.evals.month import Route, grade, score
from tier0.evals.stats import BOOTSTRAP_SAMPLES
from tier0.knowledge.prompts import CANDIDATES_3E, POINTER_GUIDE

SETS = ("tuning_month", "held_out_month1")
ARM = "haiku-3d2"
K = 3
# H: a message with no request is handed off, the answer key as it is. P: small-talk tickets
# are answered with the pointer guide.
OPTIONS = ("H", "P")
BASELINE = "KS5"

_SAFE = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")


# --- the answer key under each option --------------------------------------------------------


def option_month(month: Month, option: str) -> Month:
    """The month under an option. "H": unchanged. "P": small-talk tickets expect the pointer
    guide."""
    if option == "H":
        return month
    if option != "P":
        raise ValueError(f"unknown option: {option!r}")
    tickets = tuple(
        dataclasses.replace(t, expected=ANSWER_GUIDE, expected_guide=POINTER_GUIDE, reason=None)
        if t.kind == "chitchat"
        else t
        for t in month.tickets
    )
    return dataclasses.replace(month, tickets=tickets)


def regrade(month: Month, records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Full records, with ok and wrongly_answered recomputed against `month`'s answer key."""
    by_id = {t.id: t for t in month.tickets}
    out = []
    for r in records:
        stage = r.get("stage") or ""
        route = Route(r["outcome"], r["guide"], stage, r.get("triage"))
        ok, wrongly = grade(by_id[r["ticket_id"]], route)
        out.append(
            {
                "ticket_id": r["ticket_id"],
                "run": r["run"],
                "outcome": r["outcome"],
                "guide": r["guide"],
                "stage": stage,
                "triage": r.get("triage"),
                "ok": ok,
                "wrongly_answered": wrongly,
            }
        )
    return out


@lru_cache(maxsize=8)
def _month(set_name: str, seed: int) -> Month:
    return month_for(set_name, seed=seed)


def scores_for(
    set_name: str,
    seed: int,
    records: Sequence[Mapping[str, Any]],
    option: str,
    k: int,
    *,
    samples: int = BOOTSTRAP_SAMPLES,
) -> dict[str, Any]:
    month = option_month(_month(set_name, seed), option)
    return score(month, regrade(month, records), k=k, samples=samples)


# --- collecting the runner's result files ----------------------------------------------------


def _version_parts(version: str) -> tuple[str, str | None]:
    if version == BASELINE:
        return BASELINE, None
    candidate, _, variant = version.rpartition("-")
    if candidate not in CANDIDATES_3E or variant not in OPTIONS:
        raise ValueError(f"{version!r} is neither {BASELINE} nor a 3e candidate")
    return candidate, variant


def _records(result: Mapping[str, Any]) -> list[list[Any]]:
    rows = []
    for t in result["tickets"]:
        for r in t["runs"]:
            rows.append(
                [t["ticket_id"], r["run"], r["outcome"], r["guide"], r.get("stage") or "",
                 r.get("triage")]
            )  # fmt: skip
    rows.sort(key=lambda row: (row[0], row[1]))
    return rows


def _check_words(records: Sequence[Sequence[Any]]) -> None:
    for row in records:
        for value in row:
            if isinstance(value, str) and not _SAFE.match(value):
                raise ValueError("a record holds a string that is not a short id or enum")


def collect(runs: Mapping[str, Path]) -> dict[str, Any]:
    out_runs = []
    for key, path in runs.items():
        version, _, set_name = key.partition("/")
        candidate, variant = _version_parts(version)

        def refuse(reason: str, key: str = key) -> ValueError:
            return ValueError(f"{key}: {reason}")

        if set_name not in SETS:
            raise refuse(f"set {set_name!r} is not one of {SETS}")
        result = json.loads(Path(path).read_text(encoding="utf-8"))
        c = result["config"]
        if c.get("set") != set_name:
            raise refuse(f"the result is for set {c.get('set')!r}")
        if c.get("setup") != ARM:
            raise refuse(f"the setup is {c.get('setup')!r}, not {ARM}")
        if c.get("picker_prompt") != version:
            raise refuse(f"the picker prompt is {c.get('picker_prompt')!r}")
        if c.get("failed_calls") not in ({}, None):
            raise refuse("some model calls failed")
        if variant is not None:
            if c.get("k") != K:
                raise refuse(f"k is {c.get('k')!r}, not {K}")
            if c.get("prompt_cache") is not True:
                raise refuse("the run did not use the prompt cache")
        records = _records(result)
        _check_words(records)
        out_runs.append(
            {
                "version": version,
                "candidate": candidate,
                "variant": variant,
                "set": set_name,
                "k": c["k"],
                "seed": c["seed"],
                "month_sha256": c["month_sha256"],
                "picker_prompt_sha256": c["picker_prompt_sha256"],
                "picker_model": c["picker_model"],
                "prompt_cache": c.get("prompt_cache") is True,
                "calls": sum(c["calls"].values()),
                "cost_usd": c["cost_usd"],
                "records": records,
            }
        )
    out_runs.sort(key=lambda r: (r["set"], r["version"]))
    return {"sets": list(SETS), "arm": ARM, "k": K, "runs": out_runs}


# --- the tables ------------------------------------------------------------------------------


def _full_records(run: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {"ticket_id": t, "run": n, "outcome": o, "guide": g, "stage": s, "triage": tr}
        for t, n, o, g, s, tr in run["records"]
    ]


def _frozen(records: Sequence[Sequence[Any]]) -> tuple[tuple[Any, ...], ...]:
    return tuple(tuple(r) for r in records)


@lru_cache(maxsize=64)
def _scores_cached(
    set_name: str,
    seed: int,
    records: tuple[tuple[Any, ...], ...],
    option: str,
    k: int,
    samples: int,
) -> dict[str, Any]:
    rows = [
        {"ticket_id": t, "run": n, "outcome": o, "guide": g, "stage": s, "triage": tr}
        for t, n, o, g, s, tr in records
    ]
    return scores_for(set_name, seed, rows, option, k, samples=samples)


def _run_scores(run: Mapping[str, Any], option: str, samples: int) -> dict[str, Any]:
    return _scores_cached(
        run["set"], run["seed"], _frozen(run["records"]), option, run["k"], samples
    )


def _iv(d: Mapping[str, Any]) -> dict[str, Any]:
    return {"value": d["value"], "interval": list(d["interval"])}


def _row(run: Mapping[str, Any], s: Mapping[str, Any]) -> dict[str, Any]:
    w = s["wrongly_answered"]
    g = s["right_guide"]
    chat = s["per_kind"].get("chitchat")
    return {
        "version": run["version"],
        "wrongly_answered": {
            "runs": w["runs"],
            "tickets": w["tickets"],
            "of_runs": w["of_runs"],
            "rate": _iv(w["rate"]),
        },
        "pass1": _iv(s["pass1"]),
        "pass_k": _iv(s["pass_k"]),
        "right_guide": {
            **_iv(g),
            "wrong_guide_runs": g["wrong_guide_runs"],
            "handed_off_runs": g["handed_off_runs"],
        },
        "correct_hand_off": _iv(s["correct_hand_off"]),
        "small_talk_right": _iv(chat["pass1"]) if chat else None,
        "cost_usd": run["cost_usd"],
        "k": run["k"],
    }


def _in_option(run: Mapping[str, Any], option: str) -> bool:
    return run["variant"] is None or run["variant"] == option


def table(data: Mapping[str, Any], *, samples: int = BOOTSTRAP_SAMPLES) -> dict[str, Any]:
    """{option: {set: [row, ...]}}, the baseline first, then candidates in version order."""
    out: dict[str, Any] = {}
    for option in OPTIONS:
        out[option] = {}
        for set_name in data["sets"]:
            runs = [r for r in data["runs"] if r["set"] == set_name and _in_option(r, option)]
            runs.sort(key=lambda r: (r["variant"] is not None, r["version"]))
            out[option][set_name] = [_row(r, _run_scores(r, option, samples)) for r in runs]
    return out


def overlaps(a: Sequence[float], b: Sequence[float]) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]


def _prefixed(month: Month, prefix: str) -> tuple[MonthTicket, ...]:
    return tuple(
        dataclasses.replace(t, id=f"{prefix}.{t.id}", template_id=f"{prefix}.{t.template_id}")
        for t in month.tickets
    )


def pooled(
    data: Mapping[str, Any], version: str, option: str, *, samples: int = BOOTSTRAP_SAMPLES
) -> dict[str, Any]:
    """pass1, right_guide and wrongly_answered over both sets together, by the runner's own
    `score` on a month of both sets' tickets, so the interval still resamples whole templates."""
    runs = sorted(
        (r for r in data["runs"] if r["version"] == version), key=lambda r: SETS.index(r["set"])
    )
    if [r["set"] for r in runs] != list(SETS):
        raise ValueError(f"{version} was not run on both sets")
    key = tuple((r["set"], r["seed"], r["k"], _frozen(r["records"])) for r in runs)
    return _pooled_cached(key, option, samples)


@lru_cache(maxsize=32)
def _pooled_cached(key: tuple[tuple[Any, ...], ...], option: str, samples: int) -> dict[str, Any]:
    tickets: list[MonthTicket] = []
    records: list[dict[str, Any]] = []
    ks = {k for _, _, k, _ in key}
    if len(ks) != 1:
        raise ValueError("the sets were run with different k")
    (k,) = ks
    base: Month | None = None
    for set_name, seed, _, recs in key:
        month = option_month(_month(set_name, seed), option)
        base = base or month
        tickets.extend(_prefixed(month, set_name))
        rows = [
            {"ticket_id": t, "run": n, "outcome": o, "guide": g, "stage": s, "triage": tr}
            for t, n, o, g, s, tr in recs
        ]
        for r in regrade(month, rows):
            records.append({**r, "ticket_id": f"{set_name}.{r['ticket_id']}"})
    assert base is not None  # noqa: S101
    both = dataclasses.replace(base, tickets=tuple(tickets))
    s = score(both, records, k=k, samples=samples)
    w = s["wrongly_answered"]
    g = s["right_guide"]
    return {
        "k": k,
        "pass1": _iv(s["pass1"]),
        "right_guide": {
            **_iv(g),
            "wrong_guide_runs": g["wrong_guide_runs"],
            "handed_off_runs": g["handed_off_runs"],
        },
        "wrongly_answered": {
            "runs": w["runs"],
            "tickets": w["tickets"],
            "of_runs": w["of_runs"],
            "rate": _iv(w["rate"]),
        },
    }


def recommend(
    data: Mapping[str, Any], option: str, *, samples: int = BOOTSTRAP_SAMPLES
) -> dict[str, Any]:
    """The committed rule. Eligible: 0 wrongly answered runs on both sets. Best: the highest
    pooled right-guide rate. If the best one's interval overlaps another eligible candidate's,
    the simplest of those (KS6A, KS6B, KS6C) is recommended."""
    eligible: list[str] = []
    not_run: list[str] = []
    for candidate in CANDIDATES_3E:
        version = f"{candidate}-{option}"
        runs = [r for r in data["runs"] if r["version"] == version]
        if sorted(r["set"] for r in runs) != sorted(SETS):
            not_run.append(candidate)
            continue
        if all(_run_scores(r, option, samples)["wrongly_answered"]["runs"] == 0 for r in runs):
            eligible.append(candidate)
    if not eligible:
        return {
            "option": option,
            "eligible": [],
            "not_run": not_run,
            "recommended": None,
            "why": "none eligible",
        }
    rates = {
        c: pooled(data, f"{c}-{option}", option, samples=samples)["right_guide"] for c in eligible
    }
    best = max(eligible, key=lambda c: (rates[c]["value"], -CANDIDATES_3E.index(c)))
    if len(eligible) == 1:
        return {
            "option": option,
            "eligible": eligible,
            "not_run": not_run,
            "recommended": best,
            "why": "only one eligible",
        }
    close = [c for c in eligible if overlaps(rates[best]["interval"], rates[c]["interval"])]
    if close == [best]:
        recommended, why = best, "highest right-guide rate, apart from the others"
    else:
        recommended = min(close, key=CANDIDATES_3E.index)
        why = "intervals overlap: the simplest"
    return {
        "option": option,
        "eligible": eligible,
        "not_run": not_run,
        "recommended": recommended,
        "why": why,
    }


# --- the report ------------------------------------------------------------------------------


def _f(d: Mapping[str, Any]) -> str:
    lo, hi = d["interval"]
    return f"{d['value']:.3f} ({lo:.3f} to {hi:.3f})"


def _pair_lines(
    rows: Sequence[Mapping[str, Any]], measures: Sequence[tuple[str, Any]]
) -> list[str]:
    lines = []
    for name, get in measures:
        both, apart = [], []
        for i, a in enumerate(rows):
            for b in rows[i + 1 :]:
                pair = f"{a['version']} and {b['version']}"
                (both if overlaps(get(a)["interval"], get(b)["interval"]) else apart).append(pair)
        if not apart:
            lines.append(f"- {name}: all overlap.")
        else:
            overlapping = f"overlap: {', '.join(both)}; " if both else ""
            lines.append(f"- {name}: {overlapping}apart: {', '.join(apart)}.")
    return lines


_MEASURES: tuple[tuple[str, Any], ...] = (
    ("out-of-scope wrongly answered rate", lambda r: r["wrongly_answered"]["rate"]),
    ("right outcome, mean over runs", lambda r: r["pass1"]),
    ("right outcome in all k runs", lambda r: r["pass_k"]),
    ("how-to right guide", lambda r: r["right_guide"]),
)


def render(data: Mapping[str, Any], *, samples: int = BOOTSTRAP_SAMPLES) -> str:
    tables = table(data, samples=samples)
    lines = [
        "# Milestone 3e: picker-fix candidates, dev comparison",
        "",
        "These are dev and tuning numbers, not blind. They select nothing for a sealed run. "
        "Generated by tier0.evals.picker_3e from the runner's result files: do not edit by hand.",
        "",
        f"Arm {data['arm']}. Option H: a message with no request is handed off, the answer key "
        f"as it is. Option P: small-talk tickets are answered with the guide {POINTER_GUIDE}. "
        f"Intervals are 95% intervals that resample whole templates.",
    ]
    for option in OPTIONS:
        lines += ["", f"## Option {option}"]
        for set_name in data["sets"]:
            rows = tables[option][set_name]
            lines += ["", f"### {set_name}, option {option}", ""]
            if not rows:
                lines.append("No run for this set and option.")
                continue
            ks = {r["k"] for r in rows}
            caption = f"k is {rows[-1]['k']} for the candidates."
            base = [r for r in rows if r["version"] == BASELINE]
            if base and len(ks) > 1:
                caption = (
                    f"k is {base[0]['k']} for {BASELINE} and "
                    f"{rows[-1]['k']} for the candidates, so their per-run numbers differ in "
                    "how many runs they average."
                )
            lines += [
                caption,
                "",
                "| version | wrongly answered (runs, tickets) | wrongly answered rate | "
                "right outcome, mean over runs | right outcome, all k runs | how-to right guide | "
                "wrong-guide runs | small-talk right | k | cost USD |",
                "|---|---|---|---|---|---|---|---|---|---|",
            ]
            for r in rows:
                w = r["wrongly_answered"]
                chat = _f(r["small_talk_right"]) if r["small_talk_right"] else "none"
                lines.append(
                    f"| {r['version']} | {w['runs']} of {w['of_runs']} runs, {w['tickets']} "
                    f"tickets | {_f(w['rate'])} | {_f(r['pass1'])} | {_f(r['pass_k'])} | "
                    f"{_f(r['right_guide'])} | {r['right_guide']['wrong_guide_runs']} | {chat} | "
                    f"{r['k']} | {r['cost_usd']:.4f} |"
                )
            lines += ["", *_pair_lines(rows, _MEASURES)]
        lines += ["", f"### Both sets together, option {option}", ""]
        rec = recommend(data, option, samples=samples)
        versions = [v for v in _versions_on_both(data) if _in_option_version(v, option)]
        if versions:
            lines += [
                "| version | wrongly answered (runs, tickets) | wrongly answered rate | "
                "right outcome, mean over runs | how-to right guide |",
                "|---|---|---|---|---|",
            ]
            pooled_rows = []
            for v in versions:
                p = pooled(data, v, option, samples=samples)
                w = p["wrongly_answered"]
                pooled_rows.append(
                    {"version": v, "pass1": p["pass1"], "right_guide": p["right_guide"],
                     "wrongly_answered": w}
                )  # fmt: skip
                lines.append(
                    f"| {v} | {w['runs']} of {w['of_runs']} runs, {w['tickets']} tickets | "
                    f"{_f(w['rate'])} | {_f(p['pass1'])} | {_f(p['right_guide'])} |"
                )
            lines += ["", *_pair_lines(pooled_rows, _MEASURES[:2] + _MEASURES[3:])]
        else:
            lines.append("No version was run on both sets for this option.")
        lines += ["", f"### Recommendation by the rule, option {option}", ""]
        lines.append(
            f"- Eligible (0 wrongly answered runs on both sets): {_names(rec['eligible'])}."
        )
        lines.append(f"- Not run on both sets: {_names(rec['not_run'])}.")
        lines.append(f"- Recommended: {rec['recommended'] or 'none'}. Why: {rec['why']}.")
    lines += ["", "## Cost", ""]
    total = 0.0
    for r in data["runs"]:
        total += r["cost_usd"]
        lines.append(
            f"- {r['version']} on {r['set']}: {r['calls']} calls, {r['cost_usd']:.4f} USD."
        )
    lines.append(f"- Total: {total:.4f} USD.")
    return "\n".join(lines) + "\n"


def _names(names: Sequence[str]) -> str:
    return ", ".join(names) if names else "none"


def _versions_on_both(data: Mapping[str, Any]) -> list[str]:
    found: dict[str, set[str]] = {}
    for r in data["runs"]:
        found.setdefault(r["version"], set()).add(r["set"])
    both = [v for v, s in found.items() if s == set(SETS)]
    return sorted(both, key=lambda v: (v != BASELINE, v))


def _in_option_version(version: str, option: str) -> bool:
    return version == BASELINE or version.endswith(f"-{option}")


# --- the command line ------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("collect", help="read result files into one data file")
    c.add_argument("--run", action="append", required=True, metavar="KEY=PATH")
    c.add_argument("--out", type=Path, required=True)
    r = sub.add_parser("report", help="render the data file as Markdown")
    r.add_argument("--data", type=Path, required=True)
    r.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "collect":
        runs: dict[str, Path] = {}
        for item in args.run:
            key, sep, path = item.partition("=")
            if not sep:
                parser.error(f"--run needs KEY=PATH, got {item!r}")
            runs[key] = Path(path)
        try:
            data = collect(runs)
        except ValueError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        args.out.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
        print(f"data: {args.out}")
        return 0
    data = json.loads(args.data.read_text(encoding="utf-8"))
    args.out.write_text(render(data), encoding="utf-8")
    print(f"report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
