"""The blast-radius limit, counted per cause-day (AAC-007).

A removal or suspension is caused by one day's HR event (its `cause_date`). Changes are grouped
into buckets by that day. Removals and suspensions with no cause day (orphans, drift, people who
left before the month) form ONE extra bucket, "no-date", checked against the same two numbers.

A bucket is over the limit when it has `blast_radius_people` or more distinct employee ids among
its removals and suspensions, or `blast_radius_removals` or more removals and suspensions
(removals without an employee id count toward removals only). Flags, adds and other actions
never count. There is no plan-total limit: the limit is per bucket only.

A plan whose buckets are over the limit is split (`plan.split_plan`): those buckets go to a held
plan that needs `approvals_over_n` approvals, everything else stays in the main plan.

This module imports nothing from plan or approvals.
"""

import datetime
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from meridian_access.diff import REMOVING
from meridian_access.policy.load import Settings

NO_DATE = "no-date"


@dataclass(frozen=True, slots=True)
class Bucket:
    people: frozenset[str]
    removals: int


def _is_iso_date(text: str) -> bool:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return False
    try:
        datetime.date.fromisoformat(text)
    except ValueError:
        return False
    return True


def bucket_key(change: Mapping[str, Any]) -> str:
    """The bucket of a change dict: its `cause_date`, or NO_DATE when that is "". Raises
    ValueError when the key is missing or the value is not "" or a real YYYY-MM-DD string: such
    a plan is malformed (made by an older version or edited), and guessing would be silent."""
    if "cause_date" not in change:
        raise ValueError(
            "a change in the plan has no cause_date (plan made by an older version); "
            "build the plan again"
        )
    day = change["cause_date"]
    if not isinstance(day, str) or (day and not _is_iso_date(day)):
        raise ValueError(f"a change in the plan has a bad cause_date {day!r}; build the plan again")
    return day or NO_DATE


def buckets(changes: Iterable[Mapping[str, Any]]) -> dict[str, Bucket]:
    """Per bucket key: the distinct employee ids and the number of removals and suspensions.
    Only changes whose action is a removal or suspension are counted."""
    people: dict[str, set[str]] = {}
    counts: dict[str, int] = {}
    for c in changes:
        if c["action"] not in REMOVING:
            continue
        key = bucket_key(c)
        counts[key] = counts.get(key, 0) + 1
        ids = people.setdefault(key, set())
        if c.get("employee_id"):
            ids.add(c["employee_id"])
    return {k: Bucket(frozenset(people[k]), counts[k]) for k in sorted(counts)}


def over_limit(bucket: Bucket, settings: Settings) -> bool:
    """True when the bucket has `blast_radius_people` or more people, or
    `blast_radius_removals` or more removals and suspensions."""
    return (
        len(bucket.people) >= settings.blast_radius_people
        or bucket.removals >= settings.blast_radius_removals
    )
