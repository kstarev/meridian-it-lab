"""Milestone 3 prompt selection on tuning data only (ADR-030).

    python -m tier0.evals.m3_selection score --model llama3.2 --versions T1 C1
    python -m tier0.evals.m3_selection estimate --model claude-haiku-5-5
    python -m tier0.evals.m3_selection render --out docs/evals/<date>-m3-prompt-selection.md

Every triage version (T1-T5) and check version (C1-C5) is scored on the same tuning
messages, k = 3, seeds 1001-1003 where the model has seeds. Each (model, version) score
is saved on its own, so models can be scored at different times. The selection rule,
fixed before anything ran: a version may not catch fewer colleague requests than
version 1 for that model; among those, the most correct everyday answers wins; a tie
goes to the shorter prompt. These are tuning scores and optimistic: "best of 5".
"""

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from meridian_dataset import DEFAULT_SEED, generate
from meridian_dataset.generator import _fill_colleague
from meridian_dataset.names import TUNING_POOL
from tier0.agent import (
    own_account_check_request,
    parse_own_account_check,
    parse_triage,
    triage_request,
)
from tier0.evals.prompt_history import M3_CHECK_VERSIONS, M3_TRIAGE_VERSIONS
from tier0.models import ClaudeModel, ModelAdapter, ModelError, OllamaModel
from tier0.models.base import ABOUT_SOMEONE_ELSE, DEFAULT_MODEL_SEED_BASE, OWN_USERNAME
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
    return OllamaModel(model=name, temperature=TEMPERATURE)


def provider_of(name: str) -> str:
    return "claude" if name in CLAUDE_MODELS else "ollama"


def score(
    model_name: str, version: str, *, k: int = K, model: ModelAdapter | None = None
) -> dict[str, Any]:
    """Score one prompt version (T* for triage, C* for the check) on one model."""
    task = "triage" if version.startswith("T") else "check"
    text = (M3_TRIAGE_VERSIONS if task == "triage" else M3_CHECK_VERSIONS)[version]
    model = model if model is not None else make_model(model_name)
    cases = instances()
    correct = dict.fromkeys(GROUPS, 0)
    total = dict.fromkeys(GROUPS, 0)
    tokens_in = tokens_out = 0
    wrong: dict[str, int] = {}
    for run in range(1, k + 1):
        if getattr(model, "supports_seed", False):
            model.seed = DEFAULT_MODEL_SEED_BASE + run
        for case in cases:
            try:
                if task == "triage":
                    response = model.complete(triage_request(case.text, text))
                    ok = parse_triage(response) == case.triage_expected
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
                tokens_in += response.input_tokens
                tokens_out += response.output_tokens
            except ModelError:
                ok = False
            groups = [case.group] + (["colleague_unnamed"] if case.unnamed else [])
            for g in groups:
                total[g] += 1
                correct[g] += ok
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
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "cost_usd": cost_usd(provider, model_name, tokens_in, tokens_out),
        "wrong": dict(sorted(wrong.items(), key=lambda kv: -kv[1])),
    }


def _path(model_name: str, version: str) -> Path:
    return RESULTS / f"{model_name.replace(':', '-')}__{version}.json"


def choose(results: Sequence[dict[str, Any]]) -> str:
    """The rule fixed before any run: no fewer colleague catches than version 1, then
    the most correct everyday answers, then the shorter prompt."""
    by_version = {r["version"]: r for r in results}
    first = by_version.get("T1") or by_version.get("C1")
    if first is None:
        raise ValueError("version 1 must be scored first")
    floor = first["correct"]["colleague"]
    eligible = [r for r in results if r["correct"]["colleague"] >= floor]
    best = max(eligible, key=lambda r: (r["correct"]["everyday"], -r["prompt_chars"]))
    return str(best["version"])


def estimate_calls(k: int = K) -> dict[str, int]:
    n = len(instances())
    # The full budget, not just the versions written so far.
    tuning = n * k * 2 * MAX_VERSIONS
    # Agent runs: at most 2 model calls each (triage + check). Tuning eval seed 1108 has
    # 6 tickets, v1 6, v2 42, v3 54, all k = 3.
    agent = (6 + 6 + 42 + 54) * k * 2
    return {
        "tuning_selection_calls": tuning,
        "agent_calls_max": agent,
        "total_calls_max": tuning + agent,
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
    args = parser.parse_args(argv)

    if args.command == "estimate":
        print(json.dumps(estimate_cost(args.model), indent=2))
        return 0
    unknown = [v for v in args.versions if v not in {**M3_TRIAGE_VERSIONS, **M3_CHECK_VERSIONS}]
    if unknown:
        print(f"unknown versions: {', '.join(unknown)}", file=sys.stderr)
        return 2
    RESULTS.mkdir(parents=True, exist_ok=True)
    with guard_for_model(provider_of(args.model)):
        for version in args.versions:
            path = _path(args.model, version)
            if path.exists() and not args.rescore:
                print(f"{args.model} {version}: already scored ({path})")
                continue
            result = score(args.model, version, k=args.k)
            path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
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
