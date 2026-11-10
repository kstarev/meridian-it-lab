"""Countries, time zones, languages, public holidays and IT coverage (ADR-020).

Pure: only the pinned `holidays` package and the standard library. IT works from
Berlin, so IT coverage follows German national holidays plus Berlin's state holidays.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import holidays

BERLIN = ZoneInfo("Europe/Berlin")
IT_COUNTRY, IT_SUBDIV = "DE", "BE"

# One representative zone per office country; countries with several zones use the one
# where most of a typical office would sit.
COUNTRY_TZ: dict[str, str] = {
    "US": "America/New_York",
    "CA": "America/Toronto",
    "BR": "America/Sao_Paulo",
    "MX": "America/Mexico_City",
    "DE": "Europe/Berlin",
    "GB": "Europe/London",
    "PL": "Europe/Warsaw",
    "NL": "Europe/Amsterdam",
    "ES": "Europe/Madrid",
    "IE": "Europe/Dublin",
    "SG": "Asia/Singapore",
    "AU": "Australia/Sydney",
    "JP": "Asia/Tokyo",
    "IN": "Asia/Kolkata",
}
# The local language a holiday note adds next to English.
COUNTRY_LANGUAGE: dict[str, str] = {
    "DE": "de",
    "ES": "es",
    "MX": "es",
    "CA": "fr",
    "BR": "pt",
    "PL": "pl",
    "NL": "nl",
    "JP": "ja",
}


def tz(country: str) -> ZoneInfo:
    return ZoneInfo(COUNTRY_TZ[country])


@lru_cache(maxsize=256)
def _national(country: str, year: int) -> dict[date, str]:
    """National public holidays only (no state or regional ones), English names."""
    try:
        found = holidays.country_holidays(country, years=year, language="en_US")
    except NotImplementedError:  # pragma: no cover - only if a language isn't shipped
        found = holidays.country_holidays(country, years=year)
    return dict(found.items())


def national_holidays(country: str, years: Iterable[int]) -> dict[date, str]:
    out: dict[date, str] = {}
    for year in years:
        out.update(_national(country, year))
    return out


@lru_cache(maxsize=64)
def _berlin(year: int) -> dict[date, str]:
    return dict(holidays.country_holidays(IT_COUNTRY, subdiv=IT_SUBDIV, years=year).items())


def it_off(day: date) -> bool:
    """IT's home (Berlin) is off: a weekend, or a German or Berlin public holiday."""
    return day.weekday() >= 5 or day in _berlin(day.year)


def berlin_working_day(day: date) -> bool:
    return not it_off(day)


def working_day(country: str, day: date) -> bool:
    return day.weekday() < 5 and day not in _national(country, day.year)


def previous_working_day(country: str, day: date) -> date:
    d = day - timedelta(days=1)
    while not working_day(country, d):
        d -= timedelta(days=1)
    return d


@dataclass(frozen=True, slots=True)
class HolidayBlock:
    """A run of days off in one country: holidays plus any weekend days joining them."""

    country: str
    first: date
    last: date
    holidays: tuple[tuple[date, str], ...]  # the public holidays inside the block

    @property
    def days(self) -> tuple[date, ...]:
        n = (self.last - self.first).days + 1
        return tuple(self.first + timedelta(days=i) for i in range(n))

    def starts_at(self) -> datetime:
        """Local midnight at the start of the block."""
        return datetime.combine(self.first, datetime.min.time(), tzinfo=tz(self.country))

    def coverage(self) -> str:
        """What IT coverage looks like during the block: "it_open", "it_off" or
        "weekend". German state holidays (Berlin) change only this line."""
        weekdays = [d for d in self.days if d.weekday() < 5]
        if not weekdays:
            return "weekend"
        return "it_off" if any(it_off(d) for d in weekdays) else "it_open"

    def it_off_days(self) -> tuple[date, ...]:
        return tuple(d for d in self.days if d.weekday() < 5 and it_off(d))


def holiday_blocks(country: str, year: int) -> list[HolidayBlock]:
    """Blocks whose first holiday falls in `year` (neighbouring years are looked at so a
    block crossing New Year is joined up)."""
    hols = national_holidays(country, (year - 1, year, year + 1))

    def off(d: date) -> bool:
        return d in hols or d.weekday() >= 5

    blocks: list[HolidayBlock] = []
    seen: set[date] = set()
    for day in sorted(hols):
        if day.year != year or day in seen:
            continue
        first = day
        while off(first - timedelta(days=1)):
            first -= timedelta(days=1)
        last = day
        while off(last + timedelta(days=1)):
            last += timedelta(days=1)
        inside = tuple((d, hols[d]) for d in sorted(hols) if first <= d <= last)
        seen.update(d for d, _ in inside)
        # A plain weekend doesn't belong to the block unless it touches a holiday, which
        # the loops above guarantee; trim leading weekend days so notes go out before.
        while first.weekday() >= 5 and first not in hols and first < day:
            first += timedelta(days=1)
        blocks.append(HolidayBlock(country, first, last, inside))
    return blocks
