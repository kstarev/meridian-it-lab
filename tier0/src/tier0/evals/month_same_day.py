"""How many senders in a month go over the ladder's same-day limit.

    python -m tier0.evals.month_same_day --set tuning_month [--results out/month/<result>.json ...]
    python -m tier0.evals.month_same_day --set held_out_month1 --results <result>.json ...
        (held_out_month1 is a dev set since 2026-11-17; `--final-test=SET` is for a sealed set)

The month runner has no verification ladder, so it grades every request as if it were under
the limit. ADR-043's limit is 3 requests for one account and one action inside a rolling 24
hours; the ladder stops the next. This counts, by code, the senders who would pass it: for a
sender's sorted request times `t`, over the limit means some `t[i + limit] - t[i] < window`.

It counts four ways: own-username requests by the answer key, resets by the answer key, those
four kinds pooled, and the tickets the reset signal fired on in each run of a result file.
Counts only. It reads each ticket's sender, sending time and kind and each run's stage, and
never reads or prints a ticket's wording.
"""

import argparse
import json
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from meridian_dataset.month import DEFAULT_SEEDS, month_for
from tier0.evals.month import RESET_KINDS, RESET_SIGNAL, check_unseal, month_digest
from tier0.verification.context import ContextSettings

USERNAME_KINDS = ("own_username", "self_described")


class Refused(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Count:
    requests: int  # requests counted
    senders: int  # senders with at least one
    over_limit: int  # senders over the limit
    most: int  # the most requests any one sender has inside one window; 0 for none


def count_senders(sent: Iterable[tuple[str, datetime]], *, limit: int, window: timedelta) -> Count:
    by_sender: dict[str, list[datetime]] = defaultdict(list)
    requests = 0
    for sender, at in sent:
        by_sender[sender].append(at)
        requests += 1
    over = 0
    most = 0
    for times in by_sender.values():
        times.sort()
        busiest = max(
            sum(1 for x in times[i:] if x - start < window) for i, start in enumerate(times)
        )
        most = max(most, busiest)
        if busiest > limit:
            over += 1
    return Count(requests, len(by_sender), over, most)


def ladder_settings() -> tuple[int, timedelta]:
    settings = ContextSettings()
    return settings.same_day_limit, settings.same_day_window


def _sent(month: Any, kinds: Sequence[str]) -> list[tuple[str, datetime]]:
    return [
        (t.persona_id, datetime.fromisoformat(t.sent_at)) for t in month.tickets if t.kind in kinds
    ]


def key_counts(month: Any, *, limit: int, window: timedelta) -> dict[str, Count]:
    groups = {
        "own_username_requests": USERNAME_KINDS,
        "resets": tuple(RESET_KINDS),
        "pooled": USERNAME_KINDS + tuple(RESET_KINDS),
    }
    return {
        key: count_senders(_sent(month, kinds), limit=limit, window=window)
        for key, kinds in groups.items()
    }


def reset_signal_counts(
    month: Any, result: Mapping[str, Any], *, limit: int, window: timedelta
) -> dict[int, Count]:
    config = result["config"]
    if config["set"] != month.template_set:
        raise ValueError(f"the result is for {config['set']}, not {month.template_set}")
    if config["month_sha256"] != month_digest(month):
        raise ValueError("the result was made on a different month (the digest differs)")
    by_id = {t.id: t for t in month.tickets}
    fired: dict[int, list[tuple[str, datetime]]] = {}
    for ticket in result["tickets"]:
        known = by_id.get(ticket["ticket_id"])
        if known is None:
            raise ValueError("the result has a ticket that is not in the month")
        for run in ticket["runs"]:
            rows = fired.setdefault(run["run"], [])
            if run["stage"] == RESET_SIGNAL:
                rows.append((known.persona_id, datetime.fromisoformat(known.sent_at)))
    return {run: count_senders(fired[run], limit=limit, window=window) for run in sorted(fired)}


def report(
    set_name: str,
    *,
    final_test: str | None = None,
    seed: int | None = None,
    results: Sequence[Path] = (),
) -> dict[str, Any]:
    if seed is None:
        seed = DEFAULT_SEEDS[set_name]
    reason = check_unseal(set_name, final_test, seed)
    if reason is not None:
        raise Refused(reason)
    month = month_for(set_name, seed=seed)
    limit, window = ladder_settings()
    signal = []
    for path in results:
        result = json.loads(path.read_text(encoding="utf-8"))
        counts = reset_signal_counts(month, result, limit=limit, window=window)
        config = result["config"]
        signal.append(
            {
                "file": path.name,
                "setup": config["setup"],
                "k": config["k"],
                "runs": {str(run): asdict(count) for run, count in counts.items()},
            }
        )
    return {
        "set": set_name,
        "seed": seed,
        "tickets": len(month.tickets),
        "senders": len({t.persona_id for t in month.tickets}),
        "limit": limit,
        "window_hours": window.total_seconds() / 3600,
        "by_answer_key": {
            key: asdict(count)
            for key, count in key_counts(month, limit=limit, window=window).items()
        },
        "reset_signal": signal,
    }


def _line(c: Mapping[str, int], hours: str) -> str:
    return (
        f"{c['requests']} requests from {c['senders']} senders; over the limit: "
        f"{c['over_limit']}; the most inside {hours} hours: {c['most']}"
    )


def render(rep: Mapping[str, Any]) -> str:
    hours = f"{rep['window_hours']:g}"
    keys = rep["by_answer_key"]
    lines = [
        f"{rep['set']}, seed {rep['seed']}: {rep['tickets']} tickets from {rep['senders']} "
        f"senders. The ladder's limit: {rep['limit']} requests for one action in {hours} hours.",
        "By the answer key:",
        f"  own-username requests (own_username, self_described): "
        f"{_line(keys['own_username_requests'], hours)}",
        f"  resets (password_reset, mfa_reset): {_line(keys['resets'], hours)}",
        f"  those four kinds pooled: {_line(keys['pooled'], hours)}",
    ]
    for entry in rep["reset_signal"]:
        lines.append(f"The reset signal, {entry['setup']} (k = {entry['k']}):")
        for run, count in entry["runs"].items():
            lines.append(f"  run {run}: {_line(count, hours)}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tier0.evals.month_same_day", description=__doc__)
    p.add_argument("--set", required=True, dest="set_name", choices=sorted(DEFAULT_SEEDS))
    p.add_argument("--final-test", default=None)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--results", nargs="*", type=Path, default=[])
    p.add_argument("--json", action="store_true", help="print JSON instead of text")
    args = p.parse_args(argv)
    try:
        rep = report(
            args.set_name, final_test=args.final_test, seed=args.seed, results=args.results
        )
    except (Refused, ValueError) as err:
        print(f"refused: {err}", file=sys.stderr)
        return 2
    print(json.dumps(rep, indent=2) if args.json else render(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
