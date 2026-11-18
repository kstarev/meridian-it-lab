"""Triage-only check: python -m tier0.evals.triage_check --model stub --pool tuning.

Sends every template of a pool straight to the triage prompt (no tools, no policy) and
counts how often the model picks the right category. Held-out v2 is refused (ADR-015).
Wording is printed only for pools that are not secret.
"""

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from meridian_dataset import DEFAULT_SEED, generate
from meridian_dataset.generator import _fill_colleague
from meridian_dataset.names import MESSAGE_POOLS
from tier0 import messages
from tier0.agent import parse_triage, triage_request
from tier0.evals import caching
from tier0.evals.__main__ import _model_factory
from tier0.evals.prompt_history import VERSIONS
from tier0.evals.runner import describe_model
from tier0.models import ABOUT_SOMEONE_ELSE, OWN_USERNAME, ModelError
from tier0.models.base import DEFAULT_MODEL_SEED_BASE
from tier0.models.ollama import DEFAULT_OLLAMA_URL
from tier0.netguard import guard_for_model, is_loopback_url
from tier0.sealed import SealedSetError, require_unsealed

OTHER_EMAIL = "alex.kim@meridian.example"
SCENARIOS = ("normal", "duplicate", "colleague")
_EXPECTED = {"normal": OWN_USERNAME, "duplicate": OWN_USERNAME, "colleague": ABOUT_SOMEONE_ELSE}
_OPEN_POOLS = ("tuning", "held_out_v1")
_LABELS = {
    "tuning": "tuning score (tuning data; the prompt was chosen on it, so it is optimistic)",
    "held_out_v1": "dev-set check (held-out v1, diagnosis only, ADR-014)",
}


def build_instances(pool_name: str) -> list[tuple[str, int, str, str]]:
    """(scenario, template index, text, expected category) for every triage-bound template."""
    world = generate(DEFAULT_SEED, split=pool_name)
    pool = MESSAGE_POOLS[pool_name]
    target = next(p for p in world.people if p.status == "active" and p.manager_id is not None)
    out: list[tuple[str, int, str, str]] = []
    for scenario in SCENARIOS:
        for index, template in enumerate(getattr(pool, scenario)):
            if scenario == "colleague":
                text = _fill_colleague(template, target)
            else:
                text = template.format(other_email=OTHER_EMAIL)
            out.append((scenario, index, text, _EXPECTED[scenario]))
    return out


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def run_check(
    model: Any,
    pool_name: str,
    k: int,
    *,
    model_seed_base: int = DEFAULT_MODEL_SEED_BASE,
    prompt: str | None = None,
) -> dict[str, Any]:
    """Run r of k uses sampling seed base + r on models that support one.

    prompt is a VERSIONS key; None means the live prompt. The live prompt is restored after.
    """
    if prompt is not None and prompt not in VERSIONS:
        raise ValueError(f"unknown prompt version {prompt!r}")
    live = messages.TRIAGE_SYSTEM
    if prompt is not None:
        messages.TRIAGE_SYSTEM = VERSIONS[prompt]
    try:
        used = messages.TRIAGE_SYSTEM
        result = _run_check(model, pool_name, k, model_seed_base)
    finally:
        messages.TRIAGE_SYSTEM = live
    result["prompt"] = prompt or "live"
    result["prompt_sha256"] = _sha256(used)
    return result


def _run_check(model: Any, pool_name: str, k: int, model_seed_base: int) -> dict[str, Any]:
    instances = build_instances(pool_name)
    seeded = bool(getattr(model, "supports_seed", False))
    provenance = describe_model(model)
    per: dict[str, dict[str, int]] = {
        s: {"instances": 0, "runs": 0, "correct": 0} for s in SCENARIOS
    }
    wrong: Counter[tuple[str, int, str, str]] = Counter()
    for scenario, _, _, _ in instances:
        per[scenario]["instances"] += 1
    cache_written = cache_read = 0
    for run in range(1, k + 1):
        if seeded:
            model.seed = model_seed_base + run
        for scenario, index, text, expected in instances:
            try:
                response = model.complete(triage_request(text))
            except ModelError:
                response = None
            if response is not None:
                cache_written += response.cache_write_tokens
                cache_read += response.cache_read_tokens
            got = parse_triage(response)
            per[scenario]["runs"] += 1
            if got == expected:
                per[scenario]["correct"] += 1
            else:
                wrong[(scenario, index, expected, got)] += 1
    show = pool_name in _OPEN_POOLS
    texts = {(s, i): t for s, i, t, _ in instances}
    scenarios = {
        s: {**v, "score": v["correct"] / v["runs"] if v["runs"] else 0.0} for s, v in per.items()
    }
    runs = sum(v["runs"] for v in per.values())
    correct = sum(v["correct"] for v in per.values())
    cache = (
        {"prompt_cache": True, "cache_write_tokens": cache_written, "cache_read_tokens": cache_read}
        if cache_written or cache_read
        else {}
    )
    return {
        **cache,
        "pool": pool_name,
        "label": _LABELS.get(pool_name, "sealed"),
        "provider": model.provider,
        "model": model.model,
        "temperature": getattr(model, "temperature", None),
        "k": k,
        "model_seeds": [model_seed_base + r for r in range(1, k + 1)] if seeded else [],
        "model_seed_base": model_seed_base if seeded else None,
        "provenance": provenance,
        "scenarios": scenarios,
        "runs": runs,
        "correct": correct,
        "score": correct / runs if runs else 0.0,
        "wrong": [
            {
                "scenario": s,
                "template_index": i,
                "expected": e,
                "got": g,
                "count": n,
                **({"text": texts[(s, i)]} if show else {}),
            }
            for (s, i, e, g), n in sorted(wrong.items())
        ],
    }


def _provenance_lines(result: dict[str, Any]) -> list[str]:
    seeds = result["model_seeds"]
    prov = result["provenance"]
    if not seeds:
        return []
    if "model_digest" in prov:
        what = (
            f"Model ID {prov['model_id']} (digest {prov['model_digest']}), "
            f"Ollama version {prov['ollama_version']}"
        )
    else:
        what = f"Model digest and Ollama version not recorded ({prov.get('error', 'unavailable')})"
    listed = ", ".join(str(s) for s in seeds)
    return [f"{what}. Seeds: {listed} (base {result['model_seed_base']} + run number).", ""]


def render_markdown(result: dict[str, Any]) -> str:
    temp = result["temperature"]
    lines = [
        f"# Triage check: {result['pool']}",
        "",
        f"{result['label']}",
        "",
        f"Prompt: {result['prompt']}, SHA-256 {result['prompt_sha256']}.",
        "",
        f"Pool {result['pool']}, model {result['provider']} / {result['model']}, "
        f"temperature {'none' if temp is None else format(temp, 'g')}, k = {result['k']}.",
        "",
        *_provenance_lines(result),
        *(
            [
                "Prompt cache on: each call's system prompt is cached for 5 minutes. Input "
                f"tokens written to the cache: {result['cache_write_tokens']:,}; read from it: "
                f"{result['cache_read_tokens']:,}.",
                "",
            ]
            if result.get("prompt_cache")
            else []
        ),
        "| Scenario | Instances | Runs | Correct | Score |",
        "| --- | --- | --- | --- | --- |",
    ]
    for s, v in result["scenarios"].items():
        lines.append(
            f"| {s} | {v['instances']} | {v['runs']} | {v['correct']} | {v['score']:.2f} |"
        )
    lines += ["", f"Overall score: {result['score']:.2f}", "", "## Wrong answers", ""]
    if not result["wrong"]:
        lines.append("None.")
    for w in result["wrong"]:
        line = (
            f"- {w['scenario']} #{w['template_index']}: expected {w['expected']}, "
            f"got {w['got']} ({w['count']}x)"
        )
        if "text" in w:
            line += f"\n  > {w['text']}"
        lines.append(line)
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tier0.evals.triage_check", description=__doc__)
    parser.add_argument("--model", choices=("stub", "claude", "ollama"), default="stub")
    parser.add_argument("--pool", required=True, choices=sorted(MESSAGE_POOLS))
    parser.add_argument(
        "--prompt",
        choices=sorted(VERSIONS),
        default=None,
        help="triage prompt version to try (default: the live, frozen prompt)",
    )
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument(
        "--model-seed",
        type=int,
        default=DEFAULT_MODEL_SEED_BASE,
        metavar="BASE",
        help="Ollama only: run r uses sampling seed BASE + r (default %(default)s)",
    )
    parser.add_argument("--out", type=Path, default=Path("out/triage_check"))
    caching.add_flags(parser)
    args = parser.parse_args(argv)

    try:
        require_unsealed(generate(DEFAULT_SEED, split=args.pool))
    except SealedSetError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    if args.model == "ollama":
        url = os.environ.get("TIER0_OLLAMA_URL", DEFAULT_OLLAMA_URL)
        if not is_loopback_url(url):
            print(f"refused: the Ollama URL must be on this machine, got {url}", file=sys.stderr)
            return 2
    if args.model == "claude" and args.temperature is not None:
        print(
            "WARNING: the Claude API does not accept temperature; --temperature is ignored.",
            file=sys.stderr,
        )

    # A sealed pool was refused above, so this run is never sealed (evals/caching.py).
    prompt_cache, refusal = caching.resolve(args.prompt_cache, provider=args.model, sealed=False)
    if refusal is not None:
        print(f"refused: {refusal}", file=sys.stderr)
        return 2
    model = _model_factory(args.model, args.temperature, prompt_cache=prompt_cache)()
    # Stub and Ollama runs stay on this machine; a Claude run may reach only its API host.
    guard = guard_for_model(args.model)
    with guard:
        result = run_check(
            model, args.pool, args.k, model_seed_base=args.model_seed, prompt=args.prompt
        )

    name = re.sub(
        r"[^A-Za-z0-9._-]",
        "-",
        f"{args.pool}-{result['provider']}-{result['model']}"
        + ("" if args.prompt is None else f"-prompt-{args.prompt}")
        + ("-cache" if prompt_cache else ""),
    )
    args.out.mkdir(parents=True, exist_ok=True)
    json_path = args.out / f"{name}.json"
    md_path = args.out / f"{name}.md"
    json_path.write_text(json.dumps(result, indent=2) + "\n")
    md_path.write_text(render_markdown(result))

    print(f"{result['label']}: {result['provider']} / {result['model']}, k = {args.k}")
    for s, v in result["scenarios"].items():
        print(f"  {s}: {v['correct']}/{v['runs']} ({v['score']:.2f})")
    print(f"  overall: {result['correct']}/{result['runs']} ({result['score']:.2f})")
    print(f"report: {md_path}\njson: {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
