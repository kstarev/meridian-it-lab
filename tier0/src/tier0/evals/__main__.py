"""Command line: python -m tier0.evals [--seed N] [--k 3] [--model stub] [--out out/eval]."""

import argparse
import contextlib
import os
import re
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import anyio

from meridian_dataset import DEFAULT_SEED, HOLDOUT_SEED, generate
from tier0.evals.report import write_report
from tier0.evals.runner import PIPELINE_CHECK_LABEL, run_eval
from tier0.models import build_model
from tier0.models.ollama import DEFAULT_OLLAMA_URL
from tier0.netguard import is_loopback_url, loopback_only
from tier0.sealed import SealedSetError, require_unsealed


def today() -> date:
    return datetime.now(UTC).date()


def default_seed(model: str) -> int:
    """The stub only understands tuning wording, so its pipeline check runs on the tuning
    seed; real models are scored on the held-out seed (ADR-013)."""
    return DEFAULT_SEED if model == "stub" else HOLDOUT_SEED


def _model_factory(name: str):  # type: ignore[no-untyped-def]
    return lambda: build_model(name)


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
    parser.add_argument("--out", type=Path, default=Path("out/eval"))
    parser.add_argument("--publish", type=Path, default=None, metavar="DIR")
    parser.add_argument(
        "--final-comparison",
        action="store_true",
        help="unseal held-out v2 for milestone 2's final comparison (ADR-015)",
    )
    args = parser.parse_args(argv)
    if args.seed is None:
        args.seed = default_seed(args.model)

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

    if args.model != "stub":
        print(f"WARNING: --model {args.model} may call a paid or local model.", file=sys.stderr)

    async def go():  # type: ignore[no-untyped-def]
        return await run_eval(
            args.seed,
            k=args.k,
            model_factory=_model_factory(args.model),
            out_dir=args.out,
            today=today,
            final_comparison=args.final_comparison,
        )

    # Stub and Ollama runs must never leave this machine; only a Claude run needs the internet.
    guard = loopback_only() if args.model in ("stub", "ollama") else contextlib.nullcontext()
    with guard:
        report = anyio.run(go)
    json_path, md_path = write_report(report, args.out)
    if args.publish is not None:
        if report.is_pipeline_check:  # a custom factory could still report the stub
            print("not published: stub-model results are a pipeline check", file=sys.stderr)
            return 2
        name = re.sub(
            r"[^A-Za-z0-9._-]", "-", f"{report.run_date}-{report.provider}-{report.model}"
        )
        args.publish.mkdir(parents=True, exist_ok=True)
        published = args.publish / f"{name}.md"
        published.write_text(md_path.read_text())
        print(f"published: {published}")
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
