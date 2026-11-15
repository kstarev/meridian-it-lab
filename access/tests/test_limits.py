"""The per-cause-day blast-radius limit: buckets, the no-date bucket, at-least thresholds."""

import pytest

from meridian_access.limits import NO_DATE, Bucket, bucket_key, buckets, over_limit


def row(account, eid="E1", day="", action="remove", type_="leaver_access_left"):
    return {
        "type": type_,
        "employee_id": eid,
        "account": account,
        "system": "okta",
        "app_id": "",
        "app_name": "",
        "action": action,
        "reason": "test",
        "evidence": [],
        "cause_date": day,
        "hold": "",
    }


@pytest.fixture(scope="module")
def settings(policy):
    return policy.settings


def day_rows(day, people, removals_each=1, action="remove"):
    return [
        row(f"{day}-{p}-{i}", eid=f"E{day}{p}", day=day, action=action)
        for p in range(people)
        for i in range(removals_each)
    ]


def test_the_limits_are_the_documented_numbers(settings):
    assert (settings.blast_radius_people, settings.blast_radius_removals) == (5, 50)


def test_people_are_checked_per_bucket_at_4_and_5(settings):
    four = buckets(day_rows("2026-11-10", 4, 12))  # 48 removals, 4 people
    assert not over_limit(four["2026-11-10"], settings)
    five = buckets(day_rows("2026-11-10", 5))
    assert over_limit(five["2026-11-10"], settings)


def test_removals_are_checked_per_bucket_at_49_and_50(settings):
    below = buckets(day_rows("2026-11-10", 2, 24) + day_rows("2026-11-10", 1, 1))
    assert below["2026-11-10"].removals == 49 and not over_limit(below["2026-11-10"], settings)
    at = buckets(day_rows("2026-11-10", 2, 25))
    assert at["2026-11-10"].removals == 50 and over_limit(at["2026-11-10"], settings)


def test_a_plan_with_many_small_days_has_no_bucket_over_the_limit(settings):
    rows = [r for d in range(10, 20) for r in day_rows(f"2026-11-{d}", 4, 11)]  # 440 removals
    assert not any(over_limit(b, settings) for b in buckets(rows).values())


def test_undated_removals_form_one_no_date_bucket(settings):
    rows = [
        row(f"a{i}", eid=f"E{i}", day="", action="remove" if i % 2 else "suspend") for i in range(5)
    ]
    found = buckets(rows)
    assert list(found) == [NO_DATE]
    assert len(found[NO_DATE].people) == 5 and over_limit(found[NO_DATE], settings)
    assert not over_limit(buckets(rows[:4])[NO_DATE], settings)


def test_removals_without_an_employee_id_count_removals_only(settings):
    rows = [row(f"o{i}", eid="", day="") for i in range(50)]
    found = buckets(rows)[NO_DATE]
    assert found.people == frozenset() and found.removals == 50 and over_limit(found, settings)
    assert not over_limit(buckets(rows[:49])[NO_DATE], settings)


def test_flags_adds_and_other_actions_never_count(settings):
    rows = [
        row(f"f{i}", eid=f"E{i}", day="2026-11-10", action=a)
        for i in range(60)
        for a in ("flag", "add", "never_touch", "block_two_approvers")
    ]
    assert buckets(rows) == {}


def test_bad_cause_dates_raise():
    for bad in (None, 5, "garbage", "2026-13-01", "2026-1-1"):
        with pytest.raises(ValueError, match="cause_date"):
            bucket_key({**row("x", eid="E1", day="2026-11-10"), "cause_date": bad})
    gone = row("x", eid="E1", day="2026-11-10")
    del gone["cause_date"]
    with pytest.raises(ValueError, match="no cause_date"):
        buckets([gone])


def test_the_bucket_key_is_the_date_or_no_date():
    assert bucket_key(row("x", day="2026-11-10")) == "2026-11-10"
    assert bucket_key(row("x", day="")) == NO_DATE
    assert Bucket(frozenset({"E1"}), 2).removals == 2
