"""Desired state on the tuning world, checked against the tuning answer key."""

import collections
import dataclasses
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from meridian_access.desired import DesiredState, desired_state, load_hris
from meridian_access.policy import Policy
from meridian_access.policy.load import PolicyException
from meridian_dataset.access import AccessWorld
from meridian_dataset.access.export import write_access
from meridian_dataset.access.model import HrisEvent, HrisRecord

GOLDEN_DIGEST = "e98c86f9370fda49920e1d27c16920e71bc592b44e6334ebc9c6e7cd5165f101"


def by_id(state: DesiredState) -> dict:
    return {p.employee_id: p for p in state.people}


def apps(p) -> frozenset[str]:
    return p.sso_apps | p.other_apps


def test_deterministic_and_golden(world: AccessWorld, policy: Policy, state: DesiredState) -> None:
    again = desired_state(world.hris.records, world.hris.events, policy)
    assert again.digest() == state.digest()
    assert state.digest() == GOLDEN_DIGEST
    assert [p.employee_id for p in state.people] == sorted(p.employee_id for p in state.people)


def test_every_record_has_a_person(world: AccessWorld, state: DesiredState) -> None:
    assert len(state.people) == len(world.hris.records)


def test_leaver_findings(world: AccessWorld, state: DesiredState) -> None:
    people = by_id(state)
    found = [f for f in world.answer_key.findings if f.type == "leaver_access_left"]
    assert found
    for f in found:
        p = people[f.employee_id]
        assert p.category == "leaver"
        assert p.accounts == {"okta": "suspended", "google": "suspended", "slack": "none"}
        assert not apps(p)
        if f.app_id:
            assert f.app_id not in apps(p)


def test_contractor_past_end(world: AccessWorld, state: DesiredState) -> None:
    people = by_id(state)
    found = [f for f in world.answer_key.findings if f.type == "contractor_past_end"]
    assert found
    for f in found:
        p = people[f.employee_id]
        assert p.category == "contractor_past_end"
        assert set(p.accounts.values()) == {"none"}
        assert not apps(p)


def test_leave_of_absence(world: AccessWorld, state: DesiredState) -> None:
    people = by_id(state)
    found = [f for f in world.answer_key.findings if f.type == "leave_of_absence"]
    assert found
    for f in found:
        p = people[f.employee_id]
        assert p.category == "on_leave"
        assert set(p.accounts.values()) == {"suspended"}
        assert p.sso_apps and p.role is not None


def test_mover_findings(world: AccessWorld, state: DesiredState) -> None:
    people = by_id(state)
    found = [f for f in world.answer_key.findings if f.type == "mover_stale_access" and f.app_id]
    assert {f.expected_action for f in found} >= {"remove", "add"}
    for f in found:
        p = people[f.employee_id]
        if f.expected_action in ("remove", "flag"):
            assert f.app_id not in apps(p), f
        elif f.expected_action == "add":
            assert f.app_id in apps(p), f


def test_mapped_titles_get_their_true_role(world: AccessWorld, state: DesiredState) -> None:
    true_roles = dict(world.answer_key.true_roles)
    checked = [
        p for p in state.people if p.category in ("active", "on_leave") and not p.unmapped_title
    ]
    wrong = [p for p in checked if p.role != true_roles.get(p.employee_id)]
    assert len(checked) > 150
    assert wrong == []


def test_unmapped_title_gets_birthright_only(world: AccessWorld, policy: Policy) -> None:
    base = world.hris.records[0]
    fake = dataclasses.replace(
        base,
        employee_id="E9999",
        job_title="Chief Fake Officer",
        employment_type="employee",
        status="active",
        end_date="",
    )
    result = desired_state([fake], [], policy)
    (p,) = result.people
    assert p.unmapped_title and p.role is None
    assert p.sso_apps == frozenset(policy.birthright_employee)
    assert not p.other_apps
    assert set(p.reasons.values()) == {"birthright"}
    assert result.unmapped_titles == ("Chief Fake Officer",)


def test_contractor_gets_the_birthright_subset(world: AccessWorld, policy: Policy) -> None:
    base = world.hris.records[0]
    fake = dataclasses.replace(
        base, employee_id="E9998", job_title="Nobody", employment_type="contractor",
        status="active", end_date="",
    )  # fmt: skip
    (p,) = desired_state([fake], [], policy).people
    assert p.sso_apps == frozenset(policy.birthright_contractor)


def _active_person(state: DesiredState) -> str:
    return next(
        p.employee_id for p in state.people if p.category == "active" and p.role is not None
    )


def test_exceptions_expire(world: AccessWorld, policy: Policy, state: DesiredState) -> None:
    eid = _active_person(state)
    person = by_id(state)[eid]
    sso_app = next(a for a, x in policy.apps.items() if x.sso and a not in apps(person))
    other_app = next(a for a, x in policy.apps.items() if not x.sso and a not in apps(person))
    exceptions = tuple(
        PolicyException(eid, app, "test", "PR #1", date(2026, 12, 15))
        for app in (sso_app, other_app)
    )
    with_exc = dataclasses.replace(policy, exceptions=exceptions)

    def at(day: date):
        return by_id(desired_state(world.hris.records, world.hris.events, with_exc, day))[eid]

    for day in (date(2026, 12, 14), date(2026, 12, 15)):
        p = at(day)
        assert sso_app in p.sso_apps and other_app in p.other_apps
        assert p.reasons[sso_app] == p.reasons[other_app] == "exception"
    p = at(date(2026, 12, 16))
    assert sso_app not in p.sso_apps and other_app not in p.other_apps


def test_via_bulk_import(world: AccessWorld, state: DesiredState) -> None:
    expected = {
        e.employee_id
        for e in world.hris.events
        if e.kind == "terminate" and e.source == "bulk_import"
    }
    assert len(expected) == 30
    assert {p.employee_id for p in state.people if p.via_bulk_import} == expected


def test_category_counts_add_up(world: AccessWorld, state: DesiredState) -> None:
    assert sum(state.counts().values()) == len(world.hris.records)
    status = collections.Counter(p.status for p in state.people)
    assert state.counts()["on_leave"] == status["on_leave"]


def test_load_hris_from_files(
    world: AccessWorld, policy: Policy, state: DesiredState, tmp_path: Path
) -> None:
    write_access(world, tmp_path)
    records, evs = load_hris(tmp_path / "hris.json")
    assert records == world.hris.records
    # the export sorts each event's fields, so compare them as dicts
    assert [(e.day, e.employee_id, e.kind, e.source, dict(e.fields)) for e in evs] == [
        (e.day, e.employee_id, e.kind, e.source, dict(e.fields)) for e in world.hris.events
    ]
    assert desired_state(records, evs, policy).digest() == state.digest()


def rec(world: AccessWorld, **changes: str) -> HrisRecord:
    base = {
        "employee_id": "E9001",
        "work_email": "synthetic.one@meridian.example",
        "job_title": "Nobody",
        "employment_type": "employee",
        "status": "active",
        "end_date": "",
    }
    return dataclasses.replace(world.hris.records[0], **{**base, **changes})


def test_leaver_month_boundary(world: AccessWorld, policy: Policy) -> None:
    on = rec(world, status="terminated", end_date="2026-11-01")
    before = rec(world, employee_id="E9002", work_email="b@x.example", status="terminated",
                 end_date="2026-10-31")  # fmt: skip
    first, second = desired_state([on, before], [], policy).people
    assert (first.category, second.category) == ("leaver", "former")


def test_a_termination_dated_after_the_plan_date_is_a_notice_period(
    world: AccessWorld, policy: Policy
) -> None:
    on_notice = rec(world, status="terminated", end_date="2026-12-02")
    ends_today = rec(world, employee_id="E9002", work_email="b@x.example",
                     status="terminated", end_date="2026-12-01")  # fmt: skip
    first, second = desired_state([on_notice, ends_today], [], policy).people
    assert (first.category, first.notice_until) == ("active", "2026-12-02")
    assert (second.category, second.notice_until) == ("leaver", "")


def test_contractor_end_date_boundary(world: AccessWorld, policy: Policy) -> None:
    today = rec(world, employment_type="contractor", end_date="2026-12-01")
    yesterday = rec(world, employee_id="E9002", work_email="b@x.example",
                    employment_type="contractor", end_date="2026-11-30")  # fmt: skip
    first, second = desired_state([today, yesterday], [], policy).people
    assert (first.category, second.category) == ("active", "contractor_past_end")


def test_past_end_contractor_on_leave_is_suspended_and_flagged(
    world: AccessWorld, policy: Policy
) -> None:
    c = rec(world, employment_type="contractor", status="on_leave", end_date="2026-11-30")
    result = desired_state([c], [], policy)
    (p,) = result.people
    assert p.category == "on_leave"
    assert set(p.accounts.values()) == {"suspended"}
    assert p.sso_apps == frozenset(policy.birthright_contractor)
    assert result.needs_decision == (("E9001", "contractor past end date while on leave"),)


def test_leaver_with_an_exception_gets_no_apps(world: AccessWorld, policy: Policy) -> None:
    leaver = rec(world, status="terminated", end_date="2026-11-10")
    exc = PolicyException("E9001", policy.birthright_employee[0], "t", "PR #1", date(2027, 1, 1))
    result = desired_state([leaver], [], dataclasses.replace(policy, exceptions=(exc,)))
    (p,) = result.people
    assert p.category == "leaver" and not apps(p)
    assert result.unused_exceptions == (("E9001", exc.app, "not a current worker"),)


def test_unused_exceptions(world: AccessWorld, policy: Policy) -> None:
    app = policy.birthright_employee[0]
    exceptions = (
        PolicyException("E9001", app, "t", "PR #1", date(2026, 11, 1)),
        PolicyException("E7777", app, "t", "PR #1", date(2027, 1, 1)),
        PolicyException("E9001", app, "t", "PR #1", date(2027, 1, 1)),
    )
    result = desired_state([rec(world)], [], dataclasses.replace(policy, exceptions=exceptions))
    assert result.unused_exceptions == (("E9001", app, "expired"), ("E7777", app, "unknown person"))


def test_via_bulk_import_uses_the_latest_event_not_input_order(
    world: AccessWorld, policy: Policy
) -> None:
    def ev(day: int, source: str) -> HrisEvent:
        return HrisEvent(day=day, date="", employee_id="E9001", kind="terminate", fields=(),
                         source=source)  # fmt: skip

    person = rec(world, status="terminated", end_date="2026-11-10")
    unsorted = [ev(9, "bulk_import"), ev(3, "manual")]
    (p,) = desired_state([person], unsorted, policy).people
    assert p.via_bulk_import
    (p,) = desired_state([person], [ev(9, "manual"), ev(3, "bulk_import")], policy).people
    assert not p.via_bulk_import
    (p,) = desired_state([person], [ev(5, "manual"), ev(5, "bulk_import")], policy).people
    assert p.via_bulk_import  # same day: the later input wins


def test_rehires_supersede_the_old_record(state: DesiredState) -> None:
    superseded = [p for p in state.people if p.category == "superseded"]
    assert len(superseded) == 3
    people = by_id(state)
    for old in superseded:
        new = people[old.superseded_by]
        assert new.category in ("active", "on_leave")
        assert new.email.casefold() == old.email.casefold()
        assert old.status == "terminated"
        assert set(old.accounts.values()) == {"none"} and not apps(old)
    assert len(state.by_email()) == len(state.people) - 3


def test_by_email_raises_on_a_conflict(world: AccessWorld, policy: Policy) -> None:
    a = rec(world, status="terminated", end_date="2026-11-10", work_email="same@x.example")
    b = rec(world, employee_id="E9002", status="terminated", end_date="2026-11-12",
            work_email="SAME@x.example")  # fmt: skip
    with pytest.raises(ValueError, match="share the email"):
        desired_state([a, b], [], policy).by_email()


def test_person_is_immutable_and_hashable(state: DesiredState) -> None:
    p = state.people[0]
    assert isinstance(hash(p), int)
    with pytest.raises(TypeError):
        p.accounts["okta"] = "none"  # type: ignore[index]
    with pytest.raises(TypeError):
        p.reasons["x"] = "role"  # type: ignore[index]


def test_cli_prints_category_counts(
    world: AccessWorld, state: DesiredState, tmp_path: Path
) -> None:
    write_access(world, tmp_path)
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "meridian_access.desired", "--world", str(tmp_path)],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert result.returncode == 0, result.stderr
    for category, n in state.counts().items():
        assert f"{category}: {n}" in result.stdout
    assert f"digest: {state.digest()}" in result.stdout
