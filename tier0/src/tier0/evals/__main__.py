"""Command line: python -m tier0.evals [--seed N] [--k 3] [--model stub] [--out out/eval]."""

import argparse
import os
import re
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import anyio

from meridian_dataset import DEFAULT_SEED, HOLDOUT_SEED, generate
from meridian_dataset.generator import HOLDOUT_V2_SEED, HOLDOUT_V3_SEED
from tier0.agent import Prompts, own_account_check_request, triage_request
from tier0.evals.report import write_report
from tier0.evals.runner import PIPELINE_CHECK_LABEL, run_eval
from tier0.evals.setups import BIG_MODELS, FROZEN, SETUPS
from tier0.models import ModelAdapter, ModelError, build_model, memory
from tier0.models.base import DEFAULT_MODEL_SEED_BASE
from tier0.models.ollama import DEFAULT_OLLAMA_URL, make_room
from tier0.netguard import guard_for_model, is_loopback_url
from tier0.sealed import SEALED_POOLS, SealedSetError, require_unsealed


def today() -> date:
    return datetime.now(UTC).date()


def default_seed(model: str) -> int:
    """The stub only understands tuning wording, so its pipeline check runs on the tuning
    seed; real models are scored on the held-out seed (ADR-013)."""
    return DEFAULT_SEED if model == "stub" else HOLDOUT_SEED


def split_label(seed: int) -> str:
    """Which message set a seed uses, for published file names."""
    return {
        HOLDOUT_SEED: "held-out-v1",
        HOLDOUT_V2_SEED: "held-out-v2",
        HOLDOUT_V3_SEED: "held-out-v3",
    }.get(seed, "tuning")


def _model_factory(name: str, temperature: float | None = None):  # type: ignore[no-untyped-def]
    if name != "ollama" or temperature is None:
        return lambda: build_model(name)
    env = {**os.environ, "TIER0_OLLAMA_TEMPERATURE": str(temperature)}
    return lambda: build_model(name, env=env)


WARM_UP_TEXT = "Warm-up call before the eval; not a ticket."


def warm_up(model: ModelAdapter, prompts: Prompts | None) -> int:
    """One untimed call per task, so loading the model doesn't land in the first run's
    latency. Not part of the report; a Claude call still goes through the spend ledger.
    Returns the number of calls that failed."""
    prompts = prompts or Prompts()
    requests = (
        triage_request(WARM_UP_TEXT, prompts.triage),
        own_account_check_request(
            WARM_UP_TEXT, sender_name=None, sender_email="warm-up@invalid", system=prompts.check
        ),
    )
    failed = 0
    for request in requests:
        try:
            model.complete(request)
        except ModelError:
            failed += 1
    return failed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tier0.evals", description=__doc__)
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="default: the tuning seed for the stub, the held-out seed for real models",
    )
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--model", choices=("stub", "claude", "ollama"), default="stub")
    parser.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="Ollama only (default 0.5); Claude and the stub ignore it",
    )
    parser.add_argument(
        "--model-seed",
        type=int,
        default=DEFAULT_MODEL_SEED_BASE,
        metavar="BASE",
        help="Ollama only: run r uses sampling seed BASE + r (default %(default)s)",
    )
    parser.add_argument("--out", type=Path, default=Path("out/eval"))
    parser.add_argument("--publish", type=Path, default=None, metavar="DIR")
    parser.add_argument(
        "--final-comparison",
        choices=sorted(SEALED_POOLS),
        default=None,
        metavar="SET",
        help="unseal exactly this held-out set for its final comparison, e.g. "
        "--final-comparison=held_out_v3 (ADR-015, ADR-029)",
    )
    parser.add_argument(
        "--setup",
        choices=sorted(SETUPS),
        default=None,
        help="milestone 3: a named setup (models per task and its frozen prompts); "
        "overrides --model (ADR-030)",
    )
    parser.add_argument(
        "--no-warm-up",
        action="store_true",
        help="skip the untimed warm-up calls real models get before the runs",
    )
    args = parser.parse_args(argv)
    setup = SETUPS[args.setup] if args.setup else None
    if setup is not None:
        args.model = setup.provider
    if args.seed is None:
        args.seed = default_seed(args.model)
    if args.final_comparison is not None and set(SETUPS) != FROZEN:
        waiting = ", ".join(sorted(set(SETUPS) - FROZEN))
        print(
            f"refused: nothing runs on {args.final_comparison} until every setup is frozen; "
            f"not yet: {waiting} (ADR-031)",
            file=sys.stderr,
        )
        return 2
    if args.final_comparison is not None and setup is not None and args.k != setup.v3_k:
        print(
            f"refused: {setup.name} runs at k = {setup.v3_k} on {args.final_comparison} "
            f"(ADR-032), not {args.k}",
            file=sys.stderr,
        )
        return 2
    if setup is not None and setup.name not in FROZEN and args.seed != DEFAULT_SEED:
        print(
            f"refused: {setup.name}'s prompts aren't frozen yet, so it runs on tuning data "
            "only (ADR-030, ADR-031)",
            file=sys.stderr,
        )
        return 2

    try:
        require_unsealed(generate(args.seed), final_comparison=args.final_comparison)
    except SealedSetError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2

    if args.publish is not None and args.model == "stub":
        print("not published: stub-model results are a pipeline check", file=sys.stderr)
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
    if args.model != "stub":
        print(f"WARNING: --model {args.model} may call a paid or local model.", file=sys.stderr)

    if setup is not None:
        factory = setup.factory(args.temperature if args.temperature is not None else 0.5)
        prompts = setup.prompts()
    else:
        factory, prompts = _model_factory(args.model, args.temperature), None

    async def go():  # type: ignore[no-untyped-def]
        return await run_eval(
            args.seed,
            k=args.k,
            model_factory=factory,
            out_dir=args.out,
            today=today,
            final_comparison=args.final_comparison,
            model_seed_base=args.model_seed,
            prompts=prompts,
        )

    # Stub and Ollama runs stay on this machine; a Claude run may reach only its API host.
    guard = guard_for_model(args.model)
    with guard:
        if setup is not None and setup.provider == "ollama":
            # Never two big models in memory together (ADR-031).
            url = os.environ.get("TIER0_OLLAMA_URL", DEFAULT_OLLAMA_URL)
            unloaded = make_room(setup.models, big=BIG_MODELS, base_url=url)
            if unloaded:
                print(f"unloaded first: {', '.join(unloaded)}", file=sys.stderr)
            for big in sorted(setup.models & BIG_MODELS):
                ok, message = memory.headroom(big, base_url=url)
                print(f"memory: {message}", file=sys.stderr)
                if not ok:
                    print(f"paused: {message}. Free memory and re-run.", file=sys.stderr)
                    return 3
        if args.model != "stub" and not args.no_warm_up:
            failed = warm_up(factory(), prompts)
            if failed:
                print(f"WARNING: {failed} warm-up call(s) failed", file=sys.stderr)
        report = anyio.run(go)
    json_path, md_path = write_report(report, args.out)
    if args.publish is not None:
        if report.is_pipeline_check:  # a custom factory could still report the stub
            print("not published: stub-model results are a pipeline check", file=sys.stderr)
            return 2
        if report.model_seeds and (not report.provenance or "error" in report.provenance):
            print(
                "not published: the model's version and digest could not be read, "
                "so the report would not be reproducible",
                file=sys.stderr,
            )
            return 2
        name = re.sub(
            r"[^A-Za-z0-9._-]",
            "-",
            f"{report.run_date}-{split_label(report.seed)}-{report.provider}-"
            f"{setup.name if setup is not None else report.model}",
        )
        args.publish.mkdir(parents=True, exist_ok=True)
        published = args.publish / f"{name}.md"
        published_json = args.publish / f"{name}.json"
        # Published reports are never edited or replaced.
        taken = [p for p in (published, published_json) if p.exists()]
        if taken:
            print(f"not published: {taken[0]} already exists", file=sys.stderr)
            return 2
        published.write_text(md_path.read_text())
        published_json.write_text(json_path.read_text())
        print(f"published: {published}\npublished: {published_json}")
    if report.is_pipeline_check:
        print(f"{PIPELINE_CHECK_LABEL}:")
    print(
        f"pass^1 {report.pass_1:.2f} | pass^{report.k} everyday {report.everyday_pass_k:.2f} | "
        f"policy violations {report.policy_violation_runs} | "
        f"data exposed {report.data_exposed_runs} | escalation "
        f"{report.escalation_correct}/{report.escalation_total}"
    )
    print(f"report: {md_path}\njson: {json_path}")
    if report.unsafe_runs > 0:
        print(
            f"FAIL: {report.unsafe_runs} unsafe run(s): {report.policy_violation_runs} with "
            f"policy violations, {report.data_exposed_runs} with data exposed"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
