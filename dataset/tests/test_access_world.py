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
ACCESS_GOLDEN_MANIFEST_SHA256 = "06c73e0ae8e6469ea947c1db90e1985d500d830d2488b1eecd4d070839a6727d"


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
    assert len(orphans) == recipe.orphaned_accounts == 23
    assert sum(1 for f in orphans if f.employee_id) == 17
    assert sum(1 for f in orphans if not f.employee_id) == 6
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
    assert len([f for f in findings if f.type == "break_glass"]) == 3
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
    assert available_recipes() == ("tuning",)
    assert len(recipe_digest(load_recipe("tuning"))) == 64
