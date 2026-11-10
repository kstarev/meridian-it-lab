"""The pure scheduler: given the time, the office countries, #it-help activity and what
has been sent, which previews and posts are due (ADR-020). No I/O, no model, no clock of
its own; tests pass any moment.

- Holiday notes: national holidays per office country, the working day before at 09:00
  local. Notes that would land on the same Berlin day merge into one post, sent at an
  hour that keeps everyone listed out of the night (22:00-07:00 local) if any hour does,
  and in 08:00-18:00 for as many of them as possible. One year-end note goes to everyone
  and replaces the country notes from 24 December to 1 January.
- Pulse check: after 3 quiet working days in #it-help, Tue-Thu at 10:00 Berlin, once per
  quiet streak, at most every 14 days, never on a day with a holiday note, an incident or
  reduced IT coverage.
- Every post needs a preview in #it-team at least 23 h before (sent 24 h before; the
  tolerance covers the runner's tick), and at most one post goes out per Berlin day.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from typing import Literal

from tier0.proactive import templates
from tier0.proactive.calendar import (
    BERLIN,
    COUNTRY_LANGUAGE,
    HolidayBlock,
    berlin_working_day,
    holiday_blocks,
    it_off,
    previous_working_day,
    tz,
)

type Kind = Literal["holiday", "pulse"]

CHANNEL_GENERAL = "general"
CHANNEL_IT_HELP = "it-help"
CHANNEL_IT_TEAM = "it-team"


@dataclass(frozen=True, slots=True)
class Config:
    # Local languages next to English; each must have reviewed templates.
    local_languages: frozenset[str] = frozenset({"de", "es", "fr"})
    pulse_language: str = "en"
    reduced_coverage_dates: frozenset[date] = frozenset()
    quiet_working_days: int = 3
    pulse_min_gap: timedelta = timedelta(days=14)
    pulse_hour: int = 10
    pulse_weekdays: tuple[int, ...] = (1, 2, 3)  # Tue, Wed, Thu
    daytime: tuple[int, int] = (8, 18)  # 08:00 <= local hour < 18:00
    night: tuple[int, int] = (22, 7)  # local hour >= 22 or < 7
    preview_lead: timedelta = timedelta(hours=24)
    min_preview_lead: timedelta = timedelta(hours=23)  # the hour covers the runner's tick
    # Previews go out in IT's working hours (Berlin), at the latest such slot at least
    # preview_lead before the post, and never more than max_preview_lead before it.
    it_hours: tuple[int, int] = (9, 17)  # 09:00 to 17:00
    max_preview_lead: timedelta = timedelta(days=7)
    it_days_off: frozenset[date] = frozenset()  # extra days IT is off (e.g. an offsite)
    post_grace: timedelta = timedelta(hours=6)

    def __post_init__(self) -> None:
        unreviewed = self.local_languages - templates.REVIEWED_LANGUAGES
        if unreviewed or "en" in self.local_languages:
            raise ValueError(
                f"no reviewed templates for {sorted(unreviewed or {'en'})}: a local variant "
                "can only be turned on once its text is reviewed and added to templates.py"
            )
        if self.pulse_language not in templates.REVIEWED_LANGUAGES:
            raise ValueError(f"no reviewed pulse templates for {self.pulse_language}")


@dataclass(frozen=True, slots=True)
class Post:
    post_id: str
    kind: Kind
    channel: str
    send_at: datetime  # UTC
    text: str
    countries: tuple[str, ...] = ()
    holidays: tuple[tuple[str, date, str], ...] = ()  # (country, date, name), for IT
    year_end: bool = False

    @property
    def berlin_day(self) -> date:
        return self.send_at.astimezone(BERLIN).date()


@dataclass(frozen=True, slots=True)
class State:
    """What has happened so far (the runner keeps it)."""

    previewed: Mapping[str, datetime] = field(default_factory=dict)
    sent: frozenset[str] = frozenset()
    cancelled: frozenset[str] = frozenset()
    broadcast_days: frozenset[date] = frozenset()  # Berlin dates with a post
    last_pulse_at: datetime | None = None
    incident: bool = False


@dataclass(frozen=True, slots=True)
class Due:
    previews: tuple[Post, ...] = ()
    posts: tuple[Post, ...] = ()
    # Posts whose time has come but that won't go out, with why (no_preview_slot,
    # not_previewed). The runner logs each once.
    skipped: tuple[tuple[Post, str], ...] = ()


# --- when IT can see a preview ----------------------------------------------------------


def it_working_day(day: date, config: Config) -> bool:
    """A day IT is at work: Mon-Fri, not a German or Berlin holiday, not the year-end
    break, not a day IT marked off."""
    start, end = _year_end_window(day.year if day.month == 12 else day.year - 1)
    return not it_off(day) and not (start <= day <= end) and day not in config.it_days_off


def preview_slot(send_at: datetime, config: Config) -> datetime | None:
    """When the preview goes to #it-team: the latest moment in IT's working hours
    (Mon-Fri 09:00-17:00 Berlin, not IT's days off) that is at least preview_lead before
    the post. None if there is no such moment within max_preview_lead: then the post
    isn't sent."""
    latest = (send_at - config.preview_lead).astimezone(BERLIN)
    open_h, close_h = config.it_hours
    for back in range(config.max_preview_lead.days + 1):
        day = latest.date() - timedelta(days=back)
        if not it_working_day(day, config):
            continue
        opens = datetime.combine(day, time(open_h), tzinfo=BERLIN)
        closes = datetime.combine(day, time(close_h), tzinfo=BERLIN)
        slot = min(latest, closes) if back == 0 else closes
        if slot < opens:
            continue
        slot_utc = slot.astimezone(UTC)
        if send_at - slot_utc > config.max_preview_lead:
            return None
        return slot_utc
    return None


# --- holiday notes ---------------------------------------------------------------------


def _year_end_window(season: int) -> tuple[date, date]:
    return date(season, 12, 24), date(season + 1, 1, 1)


def _in_year_end(block: HolidayBlock) -> bool:
    """A block that touches the break (24 Dec to 1 Jan) belongs to the year-end note, even
    if it runs on, like a substitute Monday after a New Year's Day on a Saturday."""
    for season in (block.first.year - 1, block.first.year):
        start, end = _year_end_window(season)
        if any(start <= d <= end for d, _ in block.holidays):
            return True
    return False


@dataclass(frozen=True, slots=True)
class _Item:
    countries: tuple[str, ...]  # who the item is about (the year-end item: everyone)
    deadline: datetime  # UTC: the earliest local midnight when someone is off
    default: datetime  # UTC: 09:00 local on the working day before
    block: HolidayBlock | None = None
    season: int | None = None  # set for the year-end item


def _local(moment: datetime, country: str) -> datetime:
    return moment.astimezone(tz(country))


def is_night(hour: int, night: tuple[int, int]) -> bool:
    """Whether a local hour is night (the window wraps past midnight)."""
    start, end = night
    return hour >= start or hour < end


def pick_send_time(
    countries: Iterable[str],
    deadline: datetime,
    default: datetime,
    day: date,
    config: Config,
) -> datetime:
    """The hour on Berlin `day` (and before `deadline`) to send a note about
    `countries`: nobody at night if any hour allows it, then as many of them as possible
    in daytime, then the hour closest to `default`."""
    codes = sorted(set(countries))
    best: tuple[tuple[int, int, float], datetime] | None = None
    start = datetime.combine(day, time(0), tzinfo=BERLIN)
    for step in range(24):
        moment = (start + timedelta(hours=step)).astimezone(UTC)
        if moment >= deadline:
            break
        hours = [_local(moment, c).hour for c in codes]
        night = sum(is_night(h, config.night) for h in hours)
        daytime = sum(config.daytime[0] <= h < config.daytime[1] for h in hours)
        score = (-night, daytime, -abs((moment - default).total_seconds()))
        if best is None or score > best[0]:
            best = (score, moment)
    if best is None:  # the whole day is past the deadline: fall back to the default
        return default
    return best[1]


def _items(year: int, countries: tuple[str, ...]) -> list[_Item]:
    items: list[_Item] = []
    for country in countries:
        for y in (year - 1, year, year + 1):
            for block in holiday_blocks(country, y):
                if _in_year_end(block):
                    continue
                before = previous_working_day(country, block.first)
                default = datetime.combine(before, time(9), tzinfo=tz(country))
                items.append(
                    _Item(
                        (country,),
                        block.starts_at().astimezone(UTC),
                        default.astimezone(UTC),
                        block,
                    )
                )
    for season in (year - 1, year):
        start, _ = _year_end_window(season)
        deadline = min(datetime.combine(start, time(0), tzinfo=tz(c)) for c in countries)
        day = start - timedelta(days=1)
        while not berlin_working_day(day):
            day -= timedelta(days=1)
        default = datetime.combine(day, time(9), tzinfo=BERLIN)
        items.append(
            _Item(countries, deadline.astimezone(UTC), default.astimezone(UTC), season=season)
        )
    return items


def _render(post_id: str, group: list[_Item], everyone: tuple[str, ...], config: Config) -> str:
    year_end = [i for i in group if i.season is not None]
    blocks = [i.block for i in group if i.block is not None]

    def section(language: str, codes: set[str] | None) -> str | None:
        parts: list[str] = []
        if year_end:
            start, end = _year_end_window(year_end[0].season or 0)
            parts.append(templates.year_end_section(language, post_id, start, end))
        rows = [
            (b.country, tuple(d for d, _ in b.holidays), b.coverage(), b.it_off_days())
            for b in sorted(blocks, key=lambda b: (b.first, b.country))
            if codes is None or b.country in codes
        ]
        if rows:
            parts.append(templates.holiday_section(language, post_id, rows))
        return "\n\n".join(parts) if parts else None

    out = [section("en", None)]
    listed = set(everyone) if year_end else {b.country for b in blocks}
    for language in ("de", "es", "fr"):
        if language not in config.local_languages:
            continue
        codes = {c for c in listed if COUNTRY_LANGUAGE.get(c) == language}
        if not codes:
            continue
        out.append(section(language, codes))
    return "\n\n---\n\n".join(p for p in out if p)


# How many Berlin days earlier a note may move to find a free day before merging.
MAX_SHIFT_DAYS = 2


def choose_day(default_day: date, deadline_day: date, taken: set[date]) -> date | None:
    """The Berlin day for a note: its own default day if free, else a free Berlin working
    day up to MAX_SHIFT_DAYS earlier (never a weekend or holiday), else None (merge into
    the default day's note)."""
    if default_day not in taken and default_day <= deadline_day:
        return default_day
    for back in range(1, MAX_SHIFT_DAYS + 1):
        day = default_day - timedelta(days=back)
        if day not in taken and day <= deadline_day and berlin_working_day(day):
            return day
    return None


@lru_cache(maxsize=32)
def holiday_schedule(year: int, countries: tuple[str, ...], config: Config) -> tuple[Post, ...]:
    """Every holiday post whose Berlin send day falls in `year`, in send order.

    The same holiday start in several countries is one note. A different holiday that
    would land on a Berlin day that's taken moves to a free day up to MAX_SHIFT_DAYS
    earlier (still before it starts), and only merges if none is free."""
    if not countries:
        return ()
    groups: dict[object, list[_Item]] = {}
    for item in _items(year, countries):
        key = ("year_end", item.season) if item.season is not None else item.block.first  # type: ignore[union-attr]
        groups.setdefault(key, []).append(item)

    # A holiday on a weekend that gives nobody a weekday off gets no note of its own; it
    # still appears in a note that has a real day off (1 May 2027: Poland's Monday).
    groups = {
        key: group
        for key, group in groups.items()
        if any(i.season is not None for i in group)
        or any(i.block is not None and i.block.coverage() != "weekend" for i in group)
    }

    def listed(group: list[_Item]) -> set[str]:
        if any(i.season is not None for i in group):
            return set(countries)
        return {c for i in group for c in i.countries}

    taken: dict[date, list[_Item]] = {}
    for group in sorted(groups.values(), key=lambda g: min(i.deadline for i in g)):
        default_day = min(i.default for i in group).astimezone(BERLIN).date()
        deadline_day = min(i.deadline for i in group).astimezone(BERLIN).date()
        day = choose_day(default_day, deadline_day, set(taken))
        if day is None:
            taken[default_day].extend(group)
        else:
            taken[day] = list(group)

    posts = []
    for day, group in sorted(taken.items()):
        if day.year != year:
            continue
        when = pick_send_time(
            listed(group),
            min(i.deadline for i in group),
            min(i.default for i in group),
            day,
            config,
        )
        post_id = f"holiday-{day.isoformat()}"
        blocks = [i.block for i in group if i.block is not None]
        year_end = any(i.season is not None for i in group)
        posts.append(
            Post(
                post_id=post_id,
                kind="holiday",
                channel=CHANNEL_GENERAL,
                send_at=when,
                text=_render(post_id, group, countries, config),
                countries=countries if year_end else tuple(sorted({b.country for b in blocks})),
                holidays=tuple(
                    (b.country, d, name)
                    for b in sorted(blocks, key=lambda b: (b.first, b.country))
                    for d, name in b.holidays
                ),
                year_end=year_end,
            )
        )
    return tuple(posts)


# --- pulse check -----------------------------------------------------------------------


def quiet_working_days(last_human: datetime | None, day: date) -> int:
    """Berlin working days strictly between the last human message and `day`."""
    if last_human is None:
        return 10_000
    d = last_human.astimezone(BERLIN).date() + timedelta(days=1)
    count = 0
    while d < day:
        count += berlin_working_day(d)
        d += timedelta(days=1)
    return count


def reduced_coverage(day: date, config: Config) -> bool:
    start, end = _year_end_window(day.year if day.month == 12 else day.year - 1)
    return it_off(day) or day in config.reduced_coverage_dates or start <= day <= end


def pulse_post(day: date, config: Config) -> Post:
    send = datetime.combine(day, time(config.pulse_hour), tzinfo=BERLIN).astimezone(UTC)
    post_id = f"pulse-{day.isoformat()}"
    return Post(
        post_id=post_id,
        kind="pulse",
        channel=CHANNEL_IT_HELP,
        send_at=send,
        text=templates.pulse_text(config.pulse_language, post_id),
    )


def pulse_blocker(
    day: date,
    last_human: datetime | None,
    state: State,
    config: Config,
    holiday_days: frozenset[date],
) -> str | None:
    """Why no pulse check can go out on `day` (None: it can)."""
    send = pulse_post(day, config).send_at
    if day.weekday() not in config.pulse_weekdays:
        return "not_tue_to_thu"
    if reduced_coverage(day, config):
        return "reduced_coverage"
    if day in holiday_days:
        return "holiday_note_day"
    if state.incident:
        return "incident"
    if quiet_working_days(last_human, day) < config.quiet_working_days:
        return "not_quiet"
    if state.last_pulse_at is not None:
        if last_human is None or state.last_pulse_at > last_human:
            return "already_sent_this_streak"
        if send - state.last_pulse_at < config.pulse_min_gap:
            return "too_soon"
    return None


# --- the plan ----------------------------------------------------------------------------


def plan(
    now: datetime,
    countries: Iterable[str],
    last_human_at: datetime | None,
    state: State,
    config: Config = Config(),  # noqa: B008 - frozen, so a shared default is safe
) -> Due:
    """Which previews and posts are due at `now`."""
    codes = tuple(sorted(set(countries)))
    today = now.astimezone(BERLIN).date()
    holiday = [
        p
        for y in (today.year - 1, today.year, today.year + 1)
        for p in holiday_schedule(y, codes, config)
    ]
    holiday_days = frozenset(p.berlin_day for p in holiday)

    def open_(post: Post) -> bool:
        return post.post_id not in state.sent and post.post_id not in state.cancelled

    def preview_due(post: Post) -> bool:
        # At its slot (a tick late is fine), and only while the post can still go out: a
        # late preview would show IT a post that will never be sent.
        slot = preview_slot(post.send_at, config)
        return (
            open_(post)
            and slot is not None
            and post.post_id not in state.previewed
            and slot
            <= now
            <= min(slot + timedelta(hours=1), post.send_at - config.min_preview_lead)
        )

    def post_due(post: Post) -> bool:
        previewed = state.previewed.get(post.post_id)
        return (
            open_(post)
            and previewed is not None
            and previewed <= post.send_at - config.min_preview_lead
            and post.send_at <= now < post.send_at + config.post_grace
            and post.berlin_day not in state.broadcast_days
        )

    def skip_reason(post: Post) -> str | None:
        if not open_(post) or post.post_id in state.previewed:
            return None
        if not post.send_at <= now < post.send_at + config.post_grace:
            return None
        return "no_preview_slot" if preview_slot(post.send_at, config) is None else "not_previewed"

    previews = [p for p in holiday if preview_due(p)]
    posts = [p for p in holiday if post_due(p)]
    skipped = [(p, r) for p in holiday if (r := skip_reason(p)) is not None]
    # A pulse preview can come days early (a Tuesday pulse after a holiday Monday is
    # previewed the Friday before), so look a week ahead. Only one pulse per streak can
    # post, so while an earlier pulse is previewed and still pending, no later one is
    # previewed: IT never reviews a check-in that can't go out.
    pending_pulse = any(
        pid.startswith("pulse-")
        and pid not in state.sent
        and pid not in state.cancelled
        and now
        < pulse_post(date.fromisoformat(pid.removeprefix("pulse-")), config).send_at
        + config.post_grace
        for pid in state.previewed
    )
    for ahead in range(8):
        day = today + timedelta(days=ahead)
        pulse = pulse_post(day, config)
        if pulse_blocker(day, last_human_at, state, config, holiday_days) is not None:
            continue
        if preview_due(pulse) and not pending_pulse:
            previews.append(pulse)
            pending_pulse = True
        if post_due(pulse):
            posts.append(pulse)
    # One post per Berlin day: the earliest wins, the rest wait (they'll be blocked).
    chosen: dict[date, Post] = {}
    for p in sorted(posts, key=lambda p: p.send_at):
        chosen.setdefault(p.berlin_day, p)
    return Due(
        tuple(sorted(previews, key=lambda p: p.send_at)),
        tuple(chosen.values()),
        tuple(skipped),
    )


def tickets_within_24h(pulse_times: Iterable[datetime], ticket_times: Iterable[datetime]) -> int:
    """The brief's metric: tickets opened within 24 h after a pulse check."""
    pulses = sorted(pulse_times)
    return sum(1 for t in ticket_times if any(p <= t < p + timedelta(hours=24) for p in pulses))
