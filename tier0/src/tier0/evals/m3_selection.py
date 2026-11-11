"""Milestone 3 prompt selection on tuning data only (ADR-030).

    python -m tier0.evals.m3_selection score --model llama3.2 --versions T1 C1
    python -m tier0.evals.m3_selection estimate --model claude-haiku-5-5
    python -m tier0.evals.m3_selection render --out docs/evals/<date>-m3-prompt-selection.md

Each model tries 5 versions per prompt (see `candidates`): the local models T1-T5 and
C1-C5, Claude T1, HT2-HT5 and C1, HC2-HC5. All are scored on the same tuning
messages, k = 3, seeds 1001-1003 where the model has seeds. Each (model, version) score
is saved on its own, so models can be scored at different times. The selection rule:
a version may not catch fewer colleague requests than version 1 for that model; among
those, the most correct everyday answers wins; a tie goes to more colleague catches,
then to the shorter prompt (the colleague tie-break was added on tuning data, before
anything was frozen). These are tuning scores and optimistic: "best of 5".
"""

import argparse
import contextlib
import hashlib
import json
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from meridian_dataset import DEFAULT_SEED, generate
from meridian_dataset.generator import _fill_colleague
from meridian_dataset.names import TUNING_POOL
from tier0.agent import (
    check_off_format,
    own_account_check_request,
    parse_own_account_check,
    parse_triage,
    triage_off_format,
    triage_request,
)
from tier0.evals.prompt_history import M3_CHECK_VERSIONS, M3_TRIAGE_VERSIONS
from tier0.evals.setups import (
    BIG_MODELS,
    GPT_OSS,
    HAIKU,
    NO_STRUCTURED_OUTPUT,
    QWEN,
    SETUPS,
    THINK,
    timeout_for,
)
from tier0.models import ClaudeModel, ModelAdapter, ModelError, OllamaModel
from tier0.models.base import ABOUT_SOMEONE_ELSE, DEFAULT_MODEL_SEED_BASE, OWN_USERNAME
from tier0.models.ollama import make_room
from tier0.models.pricing import PRICES_PER_MTOK, cost_usd
from tier0.netguard import guard_for_model

K = 3
# The budget the user set: at most 5 versions per prompt, version 1 included.
MAX_VERSIONS = 5
TEMPERATURE = 0.5
RESULTS = Path("out/m3-selection")
CLAUDE_MODELS = frozenset(PRICES_PER_MTOK)
# Groups the scores are reported by.
GROUPS = ("everyday", "self_described", "colleague", "colleague_unnamed")
# Models that get their own versions 2-5, written from their own version-1 tuning misses
# (ADR-030, ADR-031). llama3.2 and llama3.1:8b share T2-T5 and C2-C5, which were written
# from both of their misses.
OWN_VERSIONS = {HAIKU: "H", GPT_OSS: "G", QWEN: "Q"}


def candidates(model: str, task: str) -> tuple[str, ...]:
    """The 5 versions a model may try for one task, version 1 first. The two llamas
    share T2-T5 and C2-C5; each model in OWN_VERSIONS gets its own (HT2-HT5 for Haiku,
    GT2-GT5 for gpt-oss, QT2-QT5 for Qwen, and the same for C). Same budget for all."""
    first = "T1" if task == "triage" else "C1"
    prefix = OWN_VERSIONS.get(model, "") + first[0]
    return (first, *(f"{prefix}{i}" for i in range(2, MAX_VERSIONS + 1)))


def task_of(version: str) -> str:
    if version in M3_TRIAGE_VERSIONS:
        return "triage"
    if version in M3_CHECK_VERSIONS:
        return "check"
    raise KeyError(f"unknown prompt version {version!r}")


@dataclass(frozen=True, slots=True)
class Instance:
    group: str  # everyday, self_described or colleague
    unnamed: bool
    text: str
    sender_name: str
    sender_email: str

    @property
    def triage_expected(self) -> str:
        return ABOUT_SOMEONE_ELSE if self.group == "colleague" else OWN_USERNAME

    @property
    def check_expected(self) -> bool:
        return self.group != "colleague"


def instances() -> list[Instance]:
    """Every tuning message a prompt is scored on (tuning data only, ADR-013)."""
    world = generate(DEFAULT_SEED)
    second = world.person_by_email("alex.kim2@meridian.example")
    first = world.person_by_email("alex.kim@meridian.example")
    if second is None or first is None:
        raise LookupError("the tuning world lost its duplicate-name pair")
    sender = next(p for p in world.people if p.status == "active" and p is not second)
    report = next(p for p in world.people if p.manager_id and p.status == "active")
    manager = world.person_by_id(report.manager_id or "")
    if manager is None:
        raise LookupError("the tuning world has no manager for the colleague target")
    out = [
        Instance("everyday", False, t, sender.full_name, sender.primary_email)
        for t in TUNING_POOL.normal
    ]
    out += [
        Instance("self_described", False, t, sender.full_name, sender.primary_email)
        for t in TUNING_POOL.self_described
    ]
    out += [
        Instance(
            "everyday",
            False,
            t.format(other_email=first.primary_email),
            second.full_name,
            second.primary_email,
        )
        for t in TUNING_POOL.duplicate
    ]
    out += [
        Instance(
            "colleague",
            "{" not in t,
            _fill_colleague(t, report),
            manager.full_name,
            manager.primary_email,
        )
        for t in TUNING_POOL.colleague
    ]
    return out


def make_model(name: str) -> ModelAdapter:
    if name in CLAUDE_MODELS:
        return ClaudeModel(model=name)
    return OllamaModel(
        model=name,
        temperature=TEMPERATURE,
        think=THINK.get(name),
        timeout=timeout_for(name),
        structured_output=name not in NO_STRUCTURED_OUTPUT,
    )


def provider_of(name: str) -> str:
    return "claude" if name in CLAUDE_MODELS else "ollama"


def score(
    model_name: str, version: str, *, k: int = K, model: ModelAdapter | None = None
) -> dict[str, Any]:
    """Score one prompt version (T* for triage, C* for the check) on one model."""
    task = task_of(version)
    text = (M3_TRIAGE_VERSIONS if task == "triage" else M3_CHECK_VERSIONS)[version]
    model = model if model is not None else make_model(model_name)
    cases = instances()
    correct = dict.fromkeys(GROUPS, 0)
    total = dict.fromkeys(GROUPS, 0)
    # Misses that were off-format replies rather than wrong answers (ADR-031).
    off_format = dict.fromkeys(GROUPS, 0)
    tokens_in = tokens_out = 0
    wrong: dict[str, int] = {}
    elapsed = 0.0
    calls = 0
    for run in range(1, k + 1):
        if getattr(model, "supports_seed", False):
            model.seed = DEFAULT_MODEL_SEED_BASE + run
        for case in cases:
            started = time.perf_counter()
            bad_format = False
            try:
                if task == "triage":
                    response = model.complete(triage_request(case.text, text))
                    ok = parse_triage(response) == case.triage_expected
                    bad_format = triage_off_format(response)
                else:
                    response = model.complete(
                        own_account_check_request(
                            case.text,
                            sender_name=case.sender_name,
                            sender_email=case.sender_email,
                            system=text,
                        )
                    )
                    ok = parse_own_account_check(response) is case.check_expected
                    bad_format = check_off_format(response)
                tokens_in += response.input_tokens
                tokens_out += response.output_tokens
            except ModelError:
                ok = False
            elapsed += time.perf_counter() - started
            calls += 1
            groups = [case.group] + (["colleague_unnamed"] if case.unnamed else [])
            for g in groups:
                total[g] += 1
                correct[g] += ok
                off_format[g] += bad_format and not ok
            if not ok:
                key = f"{case.group}: {case.text[:70]}"
                wrong[key] = wrong.get(key, 0) + 1
    provider = getattr(model, "provider", "")
    return {
        "model": model_name,
        "version": version,
        "task": task,
        "k": k,
        "prompt_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "prompt_chars": len(text),
        "correct": correct,
        "total": total,
        "off_format": off_format,
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "cost_usd": cost_usd(provider, model_name, tokens_in, tokens_out),
        # Wall-clock per call, for estimating how long a held-out run will take.
        "mean_call_ms": round(1000 * elapsed / calls, 1) if calls else 0.0,
        "wrong": dict(sorted(wrong.items(), key=lambda kv: -kv[1])),
    }


def _path(model_name: str, version: str) -> Path:
    return RESULTS / f"{model_name.replace(':', '-')}__{version}.json"


def choose(results: Sequence[dict[str, Any]]) -> str:
    """No fewer colleague catches than version 1, then the most correct everyday
    answers; a tie goes to more colleague catches, and only then to the shorter prompt.
    (The safety tie-break was added on tuning data, before anything was frozen.)"""
    by_version = {r["version"]: r for r in results}
    first = by_version.get("T1") or by_version.get("C1")
    if first is None:
        raise ValueError("version 1 must be scored first")
    floor = first["correct"]["colleague"]
    eligible = [r for r in results if r["correct"]["colleague"] >= floor]
    best = max(
        eligible,
        key=lambda r: (r["correct"]["everyday"], r["correct"]["colleague"], -r["prompt_chars"]),
    )
    return str(best["version"])


def flawless(r: dict[str, Any]) -> bool:
    return all(r["correct"][g] == r["total"][g] for g in GROUPS)


def settled(scored: Sequence[dict[str, Any]], wanted: Sequence[str]) -> bool:
    """Every candidate is scored, or version 1 made no tuning mistake at all. Versions
    2-5 are written from version 1's own misses, so with none there is nothing to write
    from, and the unused budget stays unspent (ADR-031)."""
    have = {r["version"]: r for r in scored}
    return set(wanted) <= set(have) or (wanted[0] in have and flawless(have[wanted[0]]))


def load_results(directory: Path = RESULTS) -> list[dict[str, Any]]:
    if not directory.exists():
        return []
    return [json.loads(p.read_text()) for p in sorted(directory.glob("*.json"))]


def _for(results: Sequence[dict[str, Any]], model: str, task: str) -> list[dict[str, Any]]:
    return [r for r in results if r["model"] == model and r["task"] == task]


def choose_setups(results: Sequence[dict[str, Any]]) -> dict[str, dict[str, str | None]]:
    """Each setup's triage and check version, from its own models' tuning scores. None
    while that model's version 1 (or any candidate) hasn't been scored yet."""
    out: dict[str, dict[str, str | None]] = {}
    for setup in SETUPS.values():
        picks: dict[str, str | None] = {}
        for task, model in (("triage", setup.triage_model), ("check", setup.check_model)):
            wanted = candidates(model, task)
            scored = [r for r in _for(results, model, task) if r["version"] in wanted]
            complete = settled(scored, wanted)
            picks[task] = choose(scored) if complete else None
        out[setup.name] = picks
    return out


def _cell(r: dict[str, Any], group: str) -> str:
    return f"{r['correct'][group]}/{r['total'][group]}"


def _off(r: dict[str, Any]) -> str:
    """Misses that were off-format replies, of all misses (older scores didn't record it)."""
    if "off_format" not in r:
        return "not recorded"
    misses = sum(r["total"][g] - r["correct"][g] for g in GROUPS if g != "colleague_unnamed")
    off = sum(r["off_format"][g] for g in GROUPS if g != "colleague_unnamed")
    return f"{off} of {misses}"


def render(results: Sequence[dict[str, Any]], command: str) -> str:
    """The selection report: every version's tuning score, labelled optimistic."""
    lines = [
        "# Milestone 3: prompt selection on tuning data",
        "",
        f"Generated by `{command}` — do not edit by hand.",
        "",
        "**Every number on this page is a tuning score, and optimistic:** the prompts were "
        f"written after seeing tuning misses, and each setup keeps the best of up to "
        f"{MAX_VERSIONS} versions per prompt. Only held-out v3 measures performance "
        "(ADR-029, ADR-030).",
        "",
        f"All {len(instances())} tuning messages, k = {K}, temperature {TEMPERATURE:g} and "
        "seeds 1001-1003 for local models (the Claude API takes neither). The rule: a "
        "version may not catch fewer colleague requests than version 1 on that model; "
        "among those, the most correct everyday answers (normal and duplicate-name) wins; "
        "a tie goes to more colleague catches, then to the shorter prompt. The colleague "
        "tie-break was added after the local scores, on tuning data only, before anything "
        "was frozen (ADR-030).",
        "",
    ]
    models = sorted({r["model"] for r in results}, key=lambda m: (m in CLAUDE_MODELS, m))
    for model in models:
        for task in ("triage", "check"):
            wanted = candidates(model, task)
            scored = sorted(
                (r for r in _for(results, model, task) if r["version"] in wanted),
                key=lambda r: wanted.index(r["version"]),
            )
            if not scored:
                continue
            complete = settled(scored, wanted)
            chosen = choose(scored) if complete else None
            lines += [
                f"## {model}: {task}",
                "",
                "| Version | Everyday | Self-described | Colleague caught | Unnamed caught "
                "| Misses off-format | Prompt (chars) | sha256 | API cost |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
            for r in scored:
                mark = " **(chosen)**" if r["version"] == chosen else ""
                lines.append(
                    f"| {r['version']}{mark} | {_cell(r, 'everyday')} "
                    f"| {_cell(r, 'self_described')} | {_cell(r, 'colleague')} "
                    f"| {_cell(r, 'colleague_unnamed')} | {_off(r)} | {r['prompt_chars']} "
                    f"| `{r['prompt_sha256'][:12]}` | ${r['cost_usd']:.4f} |"
                )
            if complete and len(scored) == 1:
                lines.append("")
                lines.append(
                    f"Version 1 made no mistake on any tuning message, so versions 2-"
                    f"{MAX_VERSIONS} were not written: there was nothing to fix (ADR-031)."
                )
            if not complete:
                missing = [v for v in wanted if v not in {r["version"] for r in scored}]
                lines.append("")
                lines.append(f"Not chosen yet: {', '.join(missing)} not scored.")
            lines.append("")
    lines += [
        "## Chosen per setup",
        "",
        "| Setup | Triage model | Triage version | Check model | Check version |",
        "| --- | --- | --- | --- | --- |",
    ]
    for setup_name, picks in choose_setups(results).items():
        setup = SETUPS[setup_name]
        lines.append(
            f"| {setup_name} | {setup.triage_model} | {picks['triage'] or 'pending'} "
            f"| {setup.check_model} | {picks['check'] or 'pending'} |"
        )
    lines.append("")
    return "\n".join(lines)


def estimate_calls(k: int = K) -> dict[str, int]:
    n = len(instances())
    # The full budget, not just the versions written so far.
    tuning = n * k * 2 * MAX_VERSIONS
    # Agent runs: at most 2 model calls each (triage + check). Tuning eval seed 1108 has
    # 6 tickets, v1 6, v2 42, v3 54, all k = 3.
    agent = (6 + 6 + 42 + 54) * k * 2
    # One warm-up per task before each of those 4 eval runs.
    warm_up = 4 * 2
    return {
        "tuning_selection_calls": tuning,
        "agent_calls_max": agent,
        "warm_up_calls": warm_up,
        "total_calls_max": tuning + agent + warm_up,
    }


# Tickets per held-out set, for time estimates (counts only; nothing on v3 is read).
DEV_TICKETS = 6 + 42
V3_TICKETS = 54


def time_calls(model_name: str, model: ModelAdapter, n: int = 10) -> dict[str, Any]:
    """Time n tuning calls, alternating triage and check, after one untimed loading call.
    Tuning data only. Returns the times the remaining tuning, the dev sets and v3 would
    take at that pace: an estimate, since big models slow down as replies get longer."""
    cases = instances()
    model.complete(triage_request(cases[0].text, None))  # load the model; not timed
    seconds: list[float] = []
    for i in range(n):
        case = cases[(i * 7) % len(cases)]
        request = (
            triage_request(case.text, None)
            if i % 2 == 0
            else own_account_check_request(
                case.text, sender_name=case.sender_name, sender_email=case.sender_email
            )
        )
        started = time.perf_counter()
        with contextlib.suppress(ModelError):
            model.complete(request)
        seconds.append(time.perf_counter() - started)
    per_call = sum(seconds) / len(seconds)
    tuning_calls = len(cases) * K * 2 * MAX_VERSIONS
    # At most 2 model calls per ticket run, plus one warm-up per task.
    dev_calls = DEV_TICKETS * K * 2 + 2 * 2
    v3_calls = V3_TICKETS * K * 2 + 2

    def hours(calls: int) -> float:
        return round(calls * per_call / 3600, 2)

    return {
        "model": model_name,
        "timed_calls": n,
        "seconds_per_call": {
            "mean": round(per_call, 2),
            "slowest": round(max(seconds), 2),
            "fastest": round(min(seconds), 2),
        },
        "tuning_full_budget": {"calls": tuning_calls, "hours": hours(tuning_calls)},
        "tuning_version_1_only": {
            "calls": tuning_calls // MAX_VERSIONS,
            "hours": hours(tuning_calls // MAX_VERSIONS),
        },
        "dev_sets_v1_v2": {"calls": dev_calls, "hours": hours(dev_calls)},
        "v3": {"calls": v3_calls, "hours": hours(v3_calls)},
    }


def estimate_cost(model_name: str, k: int = K) -> dict[str, Any]:
    """An upper estimate from the real prompt and message sizes: ~3 characters per token
    (conservative), plus 150 tokens for the JSON-format instructions, and 30 output
    tokens per call (thinking off). No API call is made."""
    cases = instances()
    avg_msg = sum(len(c.text) for c in cases) / len(cases)
    avg_sys = sum(len(v) for v in (*M3_TRIAGE_VERSIONS.values(), *M3_CHECK_VERSIONS.values())) / (
        len(M3_TRIAGE_VERSIONS) + len(M3_CHECK_VERSIONS)
    )
    per_call_in = (avg_sys + avg_msg + 60) / 3 + 150
    per_call_out = 30
    calls = estimate_calls(k)
    usd = cost_usd(
        "anthropic",
        model_name,
        int(per_call_in * calls["total_calls_max"]),
        per_call_out * calls["total_calls_max"],
    )
    return {
        "model": model_name,
        **calls,
        "input_tokens_per_call": round(per_call_in),
        "output_tokens_per_call": per_call_out,
        "estimated_usd": usd,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tier0.evals.m3_selection", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("score")
    s.add_argument("--model", required=True)
    s.add_argument("--versions", nargs="+", required=True)
    s.add_argument("--k", type=int, default=K)
    s.add_argument("--rescore", action="store_true", help="score again even if saved")
    e = sub.add_parser("estimate")
    e.add_argument("--model", required=True)
    t = sub.add_parser("time", help="time a few calls on a local model and estimate run times")
    t.add_argument("--model", required=True)
    t.add_argument("--calls", type=int, default=10)
    r = sub.add_parser("render")
    r.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "estimate":
        print(json.dumps(estimate_cost(args.model), indent=2))
        return 0
    if args.command == "time":
        with guard_for_model(provider_of(args.model)):
            if provider_of(args.model) == "ollama":
                make_room([args.model], big=BIG_MODELS)
            print(json.dumps(time_calls(args.model, make_model(args.model), args.calls), indent=2))
        return 0
    if args.command == "render":
        results = load_results()
        command = f"python -m tier0.evals.m3_selection render --out {args.out.as_posix()}"
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(render(results, command))
        args.out.with_suffix(".json").write_text(
            json.dumps(
                {"chosen": choose_setups(results), "scores": results},
                indent=2,
                ensure_ascii=False,
            )
            + "\n"
        )
        print(f"report: {args.out}")
        return 0
    unknown = [v for v in args.versions if v not in {**M3_TRIAGE_VERSIONS, **M3_CHECK_VERSIONS}]
    if unknown:
        print(f"unknown versions: {', '.join(unknown)}", file=sys.stderr)
        return 2
    # Each model tries only its own 5 versions per prompt: the budget.
    other = [v for v in args.versions if v not in candidates(args.model, task_of(v))]
    if other:
        print(
            f"not candidates for {args.model}: {', '.join(other)} (ADR-030 budget)",
            file=sys.stderr,
        )
        return 2
    RESULTS.mkdir(parents=True, exist_ok=True)
    with guard_for_model(provider_of(args.model)):
        if provider_of(args.model) == "ollama":
            unloaded = make_room([args.model], big=BIG_MODELS)
            if unloaded:
                print(f"unloaded first: {', '.join(unloaded)}")
        for version in args.versions:
            path = _path(args.model, version)
            if path.exists() and not args.rescore:
                print(f"{args.model} {version}: already scored ({path})")
                continue
            result = score(args.model, version, k=args.k)
            # Saved per version, atomically: a crash or restart loses at most the version
            # in progress, and a re-run picks up at the first unsaved one.
            partial = path.with_suffix(".json.partial")
            partial.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
            partial.replace(path)
            c, t = result["correct"], result["total"]
            print(
                f"{args.model} {version}: everyday {c['everyday']}/{t['everyday']}, self "
                f"{c['self_described']}/{t['self_described']}, colleague {c['colleague']}/"
                f"{t['colleague']} (unnamed {c['colleague_unnamed']}/{t['colleague_unnamed']}), "
                f"${result['cost_usd']:.4f}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
