"""Command line: python -m tier0.evals [--seed N] [--k 3] [--model stub] [--out out/eval]."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

import anyio

from meridian_dataset import HOLDOUT_SEED
from tier0.evals.report import write_report
from tier0.evals.runner import run_eval
from tier0.models import build_model


def _model_factory(name: str):  # type: ignore[no-untyped-def]
    return lambda: build_model(name)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tier0.evals", description=__doc__)
    parser.add_argument("--seed", type=int, default=HOLDOUT_SEED)
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--model", choices=("stub", "claude", "ollama"), default="stub")
    parser.add_argument("--out", type=Path, default=Path("out/eval"))
    args = parser.parse_args(argv)

    if args.model != "stub":
        print(f"WARNING: --model {args.model} may call a paid or local model.", file=sys.stderr)

    async def go():  # type: ignore[no-untyped-def]
        return await run_eval(
            args.seed, k=args.k, model_factory=_model_factory(args.model), out_dir=args.out
        )

    report = anyio.run(go)
    json_path, md_path = write_report(report, args.out)
    print(
        f"pass^1 {report.pass_1:.2f} | pass^{report.k} everyday {report.everyday_pass_k:.2f} | "
        f"unsafe runs {report.unsafe_runs} | escalation "
        f"{report.escalation_correct}/{report.escalation_total}"
    )
    print(f"report: {md_path}\njson: {json_path}")
    if report.unsafe_runs > 0:
        print(f"FAIL: {report.unsafe_runs} unsafe run(s)")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
