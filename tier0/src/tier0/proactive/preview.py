"""Print a year's proactive-post schedule: python -m tier0.proactive.preview --year 2027

Nothing is sent. Office countries come from the Directory of a dataset seed, the same
way the runner reads them, or `--countries all` for every country tier0 supports.
"""

import argparse
import sys
from collections.abc import Sequence
from datetime import UTC, datetime

from meridian_dataset import DEFAULT_SEED, generate
from tier0.clock import MockClock
from tier0.mocks.directory import MockDirectory
from tier0.proactive.calendar import BERLIN, COUNTRY_TZ, tz
from tier0.proactive.runner import PROACTIVE_PRINCIPAL
from tier0.proactive.schedule import Config, Post, holiday_schedule, preview_slot


def _countries(seed: int, which: str | None) -> tuple[str, ...]:
    if which == "all":
        return tuple(sorted(COUNTRY_TZ))
    if which:
        return tuple(sorted(c.strip().upper() for c in which.split(",") if c.strip()))
    world = generate(seed)
    directory = MockDirectory(world, MockClock(datetime(2026, 11, 10, tzinfo=UTC)))
    found = directory.resources_buildings_list(principal=PROACTIVE_PRINCIPAL)
    return tuple(sorted(b["address"]["regionCode"] for b in found["buildings"]))


def _preview_line(post: Post, config: Config) -> str:
    slot = preview_slot(post.send_at, config)
    if slot is None:
        return "NO PREVIEW SLOT: won't be sent"
    return f"preview to #it-team {slot.astimezone(BERLIN):%a %d %b %H:%M} Berlin"


def _night(post: Post, config: Config) -> list[str]:
    out = []
    for c in post.countries:
        local = post.send_at.astimezone(tz(c))
        if local.hour >= config.night[0] or local.hour < config.night[1]:
            out.append(f"{c} {local:%H:%M}")
    return out


def render(year: int, countries: tuple[str, ...], *, full_text: bool = False) -> str:
    config = Config()
    posts = holiday_schedule(year, countries, config)
    lines = [
        f"Proactive posts, {year}: dry run, nothing is sent.",
        f"Offices: {', '.join(countries)}.",
        "",
    ]
    for p in posts:
        berlin = p.send_at.astimezone(BERLIN)
        kind = "year-end note to everyone" if p.year_end else "holiday note"
        lines.append(
            f"{berlin:%a %Y-%m-%d} {p.send_at:%H:%M} UTC ({berlin:%H:%M} Berlin)  #{p.channel}  "
            f"{kind}  {_preview_line(p, config)}"
        )
        local = ", ".join(f"{c} {p.send_at.astimezone(tz(c)):%a %H:%M}" for c in p.countries)
        lines.append(f"    local: {local}")
        if p.holidays:
            named = "; ".join(f"{c} {d:%d %b} {name}" for c, d, name in p.holidays)
            lines.append(f"    holidays (for IT, not in the post): {named}")
        night = _night(p, config)
        if night:
            lines.append(
                f"    night for: {', '.join(night)} (no hour keeps every listed office out of "
                "22:00-07:00)"
            )
        if full_text:
            lines += ["", *("    | " + t for t in p.text.splitlines()), ""]
    with_night = sum(1 for p in posts if _night(p, config))
    lines += [
        "",
        f"{len(posts)} posts on {len({p.berlin_day for p in posts})} Berlin days "
        f"(at most 1 per day); {with_night} with an office at night because no hour avoids "
        "it.",
        "Pulse checks depend on #it-help activity, so they aren't listed: Tue-Thu 10:00 "
        "Berlin after 3 quiet working days, once per quiet streak, at most every 14 days.",
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tier0.proactive.preview", description=__doc__)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--countries", default=None, help="'all' or a comma list, e.g. DE,ES")
    parser.add_argument("--text", action="store_true", help="also print each post's text")
    args = parser.parse_args(argv)
    unknown = [c for c in _countries(args.seed, args.countries) if c not in COUNTRY_TZ]
    if unknown:
        print(f"unknown countries: {', '.join(unknown)}", file=sys.stderr)
        return 2
    print(render(args.year, _countries(args.seed, args.countries), full_text=args.text))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
