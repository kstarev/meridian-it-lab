"""Command line: python -m meridian_oauth_radar [--google-tokens FILE [--banned FILE]].

Or a full dry run on a world folder (milestone 1):
python -m meridian_oauth_radar --world DIR --out DIR [--today YYYY-MM-DD] [--ledger FILE]

With no file it says there's nothing to scan yet: milestone 0 has no generated data
(OAR-003). With a Google token export it prints the ranked findings for IT to review. It only
reads files. It never revokes anything and never calls an API (OAR-004, OAR-005).
"""

import argparse
import sys
from datetime import date
from pathlib import Path

from meridian_oauth_radar import Finding, Grant
from meridian_oauth_radar.load import ExportError, load_banned_apps, load_google_tokens
from meridian_oauth_radar.rules import scan

NOTHING_TO_SCAN = "milestone 0: nothing to scan yet"
FIXTURES = "oauth-radar/tests/fixtures"
TRY = (
    f"To scan the fake fixture: python -m meridian_oauth_radar "
    f"--google-tokens {FIXTURES}/google_tokens.json --banned {FIXTURES}/banned_apps.json"
)


def render(grants: tuple[Grant, ...], findings: tuple[Finding, ...], name: str) -> str:
    """The ranked findings as plain text."""
    noun = "grant" if len(grants) == 1 else "grants"
    if not findings:
        return f"Scanned {len(grants)} {noun} from {name}: no findings. Nothing was revoked."
    lines = [
        f"Scanned {len(grants)} {noun} from {name}: {len(findings)} high-risk "
        f"{'finding' if len(findings) == 1 else 'findings'} for IT to review. "
        "Nothing was revoked."
    ]
    for rank, finding in enumerate(findings, start=1):
        grant = finding.grant
        lines.append(
            f"{rank}. {finding.severity}, score {finding.score}: {grant.app_name} "
            f"for {grant.user} ({grant.source}, app {grant.app_id})"
        )
        lines.extend(f"   - {hit.reason}" for hit in finding.hits)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="meridian_oauth_radar", description=__doc__)
    parser.add_argument(
        "--google-tokens", type=Path, metavar="FILE", help="a Google token export (JSON)"
    )
    parser.add_argument("--banned", type=Path, metavar="FILE", help="IT's banned-app list (JSON)")
    parser.add_argument("--world", type=Path, metavar="DIR", help="a world's radar/ folder")
    parser.add_argument("--out", type=Path, metavar="DIR", help="where a --world run writes")
    parser.add_argument("--today", type=date.fromisoformat, help="the simulated date")
    parser.add_argument("--ledger", type=Path, metavar="FILE", help="a previous run's ledger")
    args = parser.parse_args(argv)

    if args.world is not None:
        if args.out is None or args.google_tokens is not None:
            print("error: --world needs --out, and not --google-tokens", file=sys.stderr)
            return 2
        from meridian_oauth_radar.run import run_files, write_run

        try:
            files = run_files(args.world, today=args.today, ledger_path=args.ledger)
        except (ExportError, ValueError, OSError) as err:
            print(f"error: {err}", file=sys.stderr)
            return 2
        write_run(files, args.out)
        print(f"Wrote a dry run to {args.out}: see report.md. Nothing was removed or sent.")
        return 0

    if args.google_tokens is None:
        if args.banned is not None:
            print("error: --banned needs --google-tokens", file=sys.stderr)
            return 2
        print(NOTHING_TO_SCAN)
        print(TRY)
        return 0
    try:
        grants = load_google_tokens(args.google_tokens)
        banned = load_banned_apps(args.banned) if args.banned is not None else {}
    except ExportError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    print(render(grants, scan(grants, banned), args.google_tokens.name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
