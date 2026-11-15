"""The access world: same seed same bytes, the planted counts, and who owns every account."""

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

import pytest

from meridian_dataset.access import (
    ACCESS_GENERATOR_VERSION,
    ACCESS_TUNING_SEED,
    AccessWorld,
    generate_access,
    load_recipe,
    write_access,
)
from meridian_dataset.access import catalog as cat
from meridian_dataset.access.recipe import available_recipes, recipe_digest

# Bump ACCESS_GENERATOR_VERSION and this hash together when the generator changes on purpose.
ACCESS_GOLDEN_MANIFEST_SHA256 = "ab9e98ffd2a13a2054476b7bd17c60c88374180b7a9f2bfed707d00d995de243"


def test_same_seed_gives_equal_worlds(access_world: AccessWorld) -> None:
    assert generate_access(ACCESS_TUNING_SEED) == access_world


def test_same_seed_gives_identical_bytes(access_files: dict[str, Path], tmp_path: Path) -> None:
    again = write_access(generate_access(ACCESS_TUNING_SEED), tmp_path)
    assert again.keys() == access_files.keys()
    for name in again:
        assert again[name].read_bytes() == access_files[name].read_bytes()


def test_different_seed_gives_different_hris(access_files: dict[str, Path], tmp_path: Path) -> None:
    other = write_access(generate_access(1), tmp_path)
    assert other["hris.json"].read_bytes() != access_files["hris.json"].read_bytes()


def test_golden_manifest_hash(access_files: dict[str, Path]) -> None:
    digest = hashlib.sha256(access_files["manifest.json"].read_bytes()).hexdigest()
    assert digest == ACCESS_GOLDEN_MANIFEST_SHA256


def test_manifest_lists_every_file(access_files: dict[str, Path]) -> None:
    manifest = json.loads(access_files["manifest.json"].read_text(encoding="utf-8"))
    assert manifest["generator_version"] == ACCESS_GENERATOR_VERSION
    assert manifest["recipe_name"] == "tuning"
    assert set(manifest["files"]) == set(access_files) - {"manifest.json"}
    assert len(manifest["files"]) == 5 + 95
    for name, digest in manifest["files"].items():
        assert hashlib.sha256(access_files[name].read_bytes()).hexdigest() == digest


def _employees(world: AccessWorld, kind: str) -> set[str]:
    return {f.employee_id for f in world.answer_key.findings if f.type == kind}


def test_planted_counts_match_the_tuning_recipe(access_world: AccessWorld) -> None:
    key, recipe = access_world.answer_key, load_recipe("tuning")
    findings = key.findings
    orphans = [f for f in findings if f.type == "orphaned_account"]
    accounts = [f for f in orphans if not (f.system == "okta" and f.app_id)]
    assert len(accounts) == recipe.orphaned_accounts == 23
    assert sum(1 for f in accounts if f.employee_id) == 17
    assert sum(1 for f in accounts if not f.employee_id) == 6
    assert len(orphans) - len(accounts) == 11  # app access of orphaned Okta users
    assert len(_employees(access_world, "mover_stale_access")) == 11
    assert len(_employees(access_world, "leaver_access_left")) == 8
    assert len(_employees(access_world, "rehire")) == 3
    assert len(_employees(access_world, "contractor_past_end")) == 5
    assert len(_employees(access_world, "leave_of_absence")) == 4
    mass = [f for f in findings if f.type == "hris_mass_termination"]
    assert len(mass) == 1 and mass[0].expected_action == "block_two_approvers"
    drift = [f for f in findings if f.type == "drift"]
    assert len(drift) == 6
    assert sum(1 for f in drift if f.expected_action == "flag") >= 2
    assert len([f for f in findings if f.type == "break_glass"]) == 6  # Okta and Google each
    assert len(key.break_glass_accounts) == 3


def test_movers_have_stale_and_missing_access(access_world: AccessWorld) -> None:
    by_person: defaultdict[str, set[str]] = defaultdict(set)
    for f in access_world.answer_key.findings:
        if f.type == "mover_stale_access":
            by_person[f.employee_id].add(f.expected_action)
    assert len(by_person) == 11
    assert all({"add"} <= actions and len(actions) >= 2 for actions in by_person.values())


def test_hris_counts(access_world: AccessWorld) -> None:
    key, hris = access_world.answer_key, access_world.hris
    assert len(key.true_roles) == 230
    current = [r for r in hris.records if r.status in ("active", "on_leave")]
    bulk = [e for e in hris.events if e.source == "bulk_import"]
    assert len(bulk) == 30
    assert {e.day for e in bulk} == {28} and {e.kind for e in bulk} == {"terminate"}
    assert len(current) + len(bulk) == 230
    assert len([e for e in hris.events if e.kind == "name_change"]) == 4
    roles = dict(key.true_roles)
    assert Counter(r for r in roles.values() if r.startswith("it-")) == {
        "it-lead": 1,
        "it-engineer": 2,
    }
    assert sum(1 for r in current if r.department == "IT") == 3
    by_id = {r.employee_id: r for r in hris.records}
    contractors = [by_id[e] for e in roles if by_id[e].employment_type == "contractor"]
    assert len(contractors) == 18
    assert {r.department for r in by_id.values()} == set(cat.DEPT_NAMES)
    assert {by_id[e].department for e in {e.employee_id for e in bulk}} == {"Customer Success"}
    assert sum(1 for r in current if r.status == "on_leave") == 4
    ended = [r for r in contractors if r.end_date and r.end_date < access_world.reference_date]
    assert len(ended) == 5 and all(r.status == "active" for r in ended)


def test_duplicate_name_pairs(access_world: AccessWorld) -> None:
    groups: defaultdict[tuple[str, str], list[str]] = defaultdict(list)
    for r in access_world.hris.records:
        if r.status in ("active", "on_leave"):
            groups[(r.given_name, r.family_name)].append(r.work_email)
    pairs = [emails for emails in groups.values() if len(emails) > 1]
    assert len(pairs) == 2
    for emails in pairs:
        assert len(set(emails)) == 2
        assert sum(e.split("@")[0].endswith("2") for e in emails) == 1  # priya.shah2


def test_apps(access_world: AccessWorld) -> None:
    assert len(cat.APPS) == 140
    assert sum(a.sso for a in cat.APPS) == 45 and sum(not a.sso for a in cat.APPS) == 95
    assert len({a.name for a in cat.APPS}) == 140
    assert len(access_world.exports) == 95
    assert len(access_world.okta.apps) == 45
    assert len(cat.BIRTHRIGHT_IDS) == 8
    assert {a for r in cat.ROLES for a in r.other_apps} == set(cat.NON_SSO_IDS)
    assert all(3 <= len(r.sso_apps) <= 8 and 2 <= len(r.other_apps) <= 6 for r in cat.ROLES)
    assert all(2 <= len(roles) <= 4 for roles in cat.ROLES_BY_DEPT.values())


def test_every_account_has_exactly_one_owner(
    access_world: AccessWorld, access_files: dict[str, Path]
) -> None:
    owners = Counter((o.system, o.account) for o in access_world.answer_key.owners)
    assert max(owners.values()) == 1
    accounts = {("okta", u.login) for u in access_world.okta.users}
    accounts |= {("google", g.primary_email) for g in access_world.google_users}
    accounts |= {("slack", s.user_name) for s in access_world.slack_users}
    for export in access_world.exports:
        accounts |= {(export.app_id, row[0]) for row in export.rows if any(row)}
    assert set(owners) == accounts


def test_break_glass_and_mass_termination(access_world: AccessWorld) -> None:
    key = access_world.answer_key
    owner = {(o.system, o.account): o.owner_employee_id for o in key.owners}
    orphan_accounts = {f.account for f in key.findings if f.type == "orphaned_account"}
    for account in key.break_glass_accounts:
        assert owner[("okta", account)] == "" and owner[("google", account)] == ""
        assert account not in orphan_accounts
        assert account.endswith("@meridian.example")
    assert {f.expected_action for f in key.findings if f.type == "break_glass"} == {"never_touch"}
    mass = {e.employee_id for e in access_world.hris.events if e.source == "bulk_import"}
    emails = {r.work_email for r in access_world.hris.records if r.employee_id in mass}
    removing = {"remove", "suspend", "flag"}
    assert not [f for f in key.findings if f.employee_id in mass and f.expected_action in removing]
    okta = [u for u in access_world.okta.users if u.login in emails]
    google = [g for g in access_world.google_users if g.primary_email in emails]
    assert len(okta) == len(google) == 30
    assert all(u.last_login >= "2026-11-30" and u.status == "ACTIVE" for u in okta)
    assert all(g.last_login_time >= "2026-11-30" and not g.suspended for g in google)


def test_names_and_emails(access_world: AccessWorld) -> None:
    assert len({a.name for a in cat.APPS}) == 140
    domain = "@meridian.example"
    for r in access_world.hris.records:
        assert r.work_email.endswith(domain) and r.personal_email.endswith("@mail.example")
    assert all(u.login.endswith(domain) for u in access_world.okta.users)
    assert all(g.primary_email.endswith(domain) for g in access_world.google_users)
    assert all(a.endswith(domain) for g in access_world.google_users for a in g.aliases)
    assert all(s.email.endswith(domain) for s in access_world.slack_users)


def test_name_changes_leave_an_alias_in_google(access_world: AccessWorld) -> None:
    changed = {e.employee_id for e in access_world.hris.events if e.kind == "name_change"}
    emails = {r.work_email for r in access_world.hris.records if r.employee_id in changed}
    aliased = [g for g in access_world.google_users if g.primary_email in emails]
    assert len(aliased) == 4 and all(len(g.aliases) == 1 for g in aliased)


def test_rehires_get_a_new_id_and_the_same_email(access_world: AccessWorld) -> None:
    events = [e for e in access_world.hris.events if e.kind == "rehire"]
    assert len(events) == 3
    by_id = {r.employee_id: r for r in access_world.hris.records}
    for e in events:
        new = by_id[e.employee_id]
        olds = [
            r for r in by_id.values()
            if r.work_email == new.work_email and r.employee_id != new.employee_id
        ]  # fmt: skip
        assert len(olds) == 1 and olds[0].status == "terminated"
        assert (olds[0].given_name, olds[0].family_name) == (new.given_name, new.family_name)


def test_recipe_validation_rejects_inconsistent_sums() -> None:
    bad = replace(load_recipe("tuning"), orphaned_former_employees=30)
    with pytest.raises(ValueError, match="orphaned_former_employees"):
        generate_access(ACCESS_TUNING_SEED, bad)
    with pytest.raises(ValueError, match="non-negative"):
        generate_access(ACCESS_TUNING_SEED, replace(load_recipe("tuning"), movers=-1))
    mass = replace(load_recipe("tuning").mass_termination, department="Nowhere")
    with pytest.raises(ValueError, match="does not exist"):
        generate_access(ACCESS_TUNING_SEED, replace(load_recipe("tuning"), mass_termination=mass))
    big = replace(load_recipe("tuning").mass_termination, people=60)
    with pytest.raises(ValueError, match="too few people"):
        generate_access(ACCESS_TUNING_SEED, replace(load_recipe("tuning"), mass_termination=big))


def test_unknown_recipe_lists_the_available_ones() -> None:
    with pytest.raises(ValueError, match="tuning"):
        load_recipe("nope")
    assert "tuning" in available_recipes()
    assert len(recipe_digest(load_recipe("tuning"))) == 64


# ---- answer-key conventions (generator 0.3.0) ------------------------------------------


def _by(world: AccessWorld, kind: str) -> list:
    return [f for f in world.answer_key.findings if f.type == kind]


def test_former_employees_leftover_accounts_use_leaver_actions(access_world: AccessWorld) -> None:
    owned = [f for f in _by(access_world, "orphaned_account") if f.employee_id]
    assert owned
    for f in owned:
        if f.system == "okta" and not f.app_id:
            assert f.expected_action == "suspend"
        elif f.system == "okta":
            assert f.expected_action == "remove"
        else:
            assert f.expected_action == "flag"  # an export row
    unowned = [f for f in _by(access_world, "orphaned_account") if not f.employee_id]
    assert {f.expected_action for f in unowned if f.system in ("okta", "google", "slack")} == {
        "remove"
    }
    assert {f.expected_action for f in unowned if f.system not in ("okta", "google", "slack")} == {
        "flag"
    }


def test_every_orphaned_okta_user_has_app_findings(access_world: AccessWorld) -> None:
    findings = _by(access_world, "orphaned_account")
    users = {f.account for f in findings if f.system == "okta" and not f.app_id}
    assert len(users) == 11
    slug = {a.id: a.app_id for a in access_world.okta.apps}
    login = {u.id: u.login for u in access_world.okta.users}
    assigned = {
        (login[a.user_id], slug[a.app_id])
        for a in access_world.okta.assignments
        if login[a.user_id] in users
    }
    keyed = {(f.account, f.app_id) for f in findings if f.system == "okta" and f.app_id}
    assert assigned and assigned == keyed
    assert {f.expected_action for f in findings if f.system == "okta" and f.app_id} == {"remove"}


def test_privilege_drift_changes_an_existing_assignment(access_world: AccessWorld) -> None:
    world = access_world
    slug = {a.id: a.app_id for a in world.okta.apps}
    login = {u.id: u.login for u in world.okta.users}
    apps_of = {}
    for a in world.okta.assignments:
        apps_of.setdefault(login[a.user_id], []).append((slug[a.app_id], a))
    by_eid = {r.employee_id: r for r in world.hris.records}
    events = [e for e in world.okta.log if e.event_type.endswith("change_privilege")]
    assert len(events) >= 2
    drift = {(f.account, f.app_id): f for f in _by(world, "drift")}
    for e in events:
        key = (e.target_user_login, slug[e.target_app_id])
        assert drift[key].expected_action == "flag"
        mine = [a for s, a in apps_of[key[0]] if s == key[1]]
        assert len(mine) == 1 and mine[0].role == "admin" and mine[0].scope == "GROUP"
        person = by_eid[drift[key].employee_id]
        role = cat.ROLE_BY_ID[
            next(r for e_, r in world.answer_key.true_roles if e_ == person.employee_id)
        ]
        assert key[1] in (*cat.BIRTHRIGHT_IDS, *role.sso_apps)
    # Hand-added access outside the role stays a removal, one assignment per app.
    removes = [f for f in drift.values() if f.expected_action == "remove"]
    assert removes
    for f in removes:
        assert sum(1 for s, _ in apps_of[f.account] if s == f.app_id) == 1


def test_break_glass_has_okta_and_google_findings(access_world: AccessWorld) -> None:
    key = access_world.answer_key
    pairs = {(f.system, f.account) for f in key.findings if f.type == "break_glass"}
    assert pairs == {(s, a) for a in key.break_glass_accounts for s in ("okta", "google")}


def test_movers_add_in_a_username_only_export_uses_the_pattern(access_world: AccessWorld) -> None:
    from meridian_dataset.access.recipe import render_username

    recipe = load_recipe("tuning")
    layouts = {x.name: x for x in recipe.export_layouts}
    exports = {e.app_id: e for e in access_world.exports}
    people = {r.employee_id: r for r in access_world.hris.records}
    seen = 0
    for f in _by(access_world, "mover_stale_access"):
        if f.expected_action != "add" or f.system == "okta":
            continue
        fields = layouts[exports[f.app_id].layout].fields
        if "email" in fields:
            assert "@" in f.account
            continue
        seen += 1
        person = people[f.employee_id]
        allowed = {
            render_username(pat, person.given_name, person.family_name, num) + suffix
            for pat in recipe.username_patterns
            for num in ("", *map(str, range(2, 10)))
            for suffix in ("", *map(str, range(2, 10)))
        }
        assert f.account in allowed
    assert seen > 0
