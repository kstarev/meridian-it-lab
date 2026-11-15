"""The policy files load, validate, and stay in step with the public guide and the tuning world."""

import copy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from meridian_access.policy import Policy, PolicyError, load_policy
from meridian_access.policy.build import build_from_public, check_files, parse_policy_markdown
from meridian_access.policy.load import FILES, build_policy, default_policy_dir, validate
from meridian_access.policy.seed import seed_title_map
from meridian_dataset.access.recipe_tools import access_policy_markdown


def raw_files() -> dict[str, object]:
    return {n: yaml.safe_load((default_policy_dir() / f"{n}.yaml").read_text()) for n in FILES}


def rebuild(raw: dict[str, object]) -> Policy:
    """Validate every file against its schema, then cross-check, as load_policy does."""
    for name, data in raw.items():
        validate(name, data)
    return build_policy(raw)


def test_files_load_and_validate(policy: Policy) -> None:
    assert len(policy.apps) == 140
    assert len(policy.roles) == 23
    assert len(policy.birthright_employee) == 8
    assert len(policy.birthright_contractor) == 6
    assert policy.settings.blast_radius_n == 25
    assert policy.exceptions == ()
    assert len(policy.break_glass_accounts) == 3


def test_build_check_passes() -> None:
    assert check_files(default_policy_dir(), build_from_public()) == []


def test_seed_titles_check_passes() -> None:
    assert check_files(default_policy_dir(), {"title_map.yaml": seed_title_map()}) == []


def test_check_notices_a_changed_file(tmp_path: Path) -> None:
    for name, text in build_from_public().items():
        (tmp_path / name).write_text(text)
    (tmp_path / "apps.yaml").write_text("changed")
    assert check_files(tmp_path, build_from_public()) == ["apps.yaml"]


def test_roles_yaml_equals_the_public_markdown_table(policy: Policy) -> None:
    birthright, table = parse_policy_markdown(access_policy_markdown())
    assert list(policy.birthright_employee) == birthright["employee"]
    assert list(policy.birthright_contractor) == birthright["contractor"]
    assert {r.id for r in policy.roles.values()} == set(table)
    for role_id, apps in table.items():
        assert list(policy.roles[role_id].sso_apps) == apps["sso_apps"]
        assert list(policy.roles[role_id].other_apps) == apps["other_apps"]


def test_a_missing_directory_file_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(PolicyError, match=r"apps\.yaml is missing"):
        load_policy(tmp_path)


def test_a_copy_of_the_files_loads(tmp_path: Path) -> None:
    for name in FILES:
        shutil.copy(default_policy_dir() / f"{name}.yaml", tmp_path)
    assert load_policy(tmp_path).settings.blast_radius_n == 25


def test_unknown_app_in_a_role_fails() -> None:
    raw = copy.deepcopy(raw_files())
    raw["roles"]["roles"][0]["sso_apps"].append("no-such-app")  # type: ignore[index]
    with pytest.raises(PolicyError, match="unknown app 'no-such-app'"):
        rebuild(raw)


def test_sso_app_in_other_apps_fails(policy: Policy) -> None:
    raw = copy.deepcopy(raw_files())
    raw["roles"]["roles"][0]["other_apps"].append(policy.birthright_employee[0])  # type: ignore[index]
    with pytest.raises(PolicyError, match="non-SSO"):
        rebuild(raw)


def test_unknown_title_map_role_fails() -> None:
    raw = copy.deepcopy(raw_files())
    raw["title_map"]["titles"][0]["role"] = "no-such-role"  # type: ignore[index]
    with pytest.raises(PolicyError, match="unknown role"):
        rebuild(raw)


def good_exception(policy: Policy) -> dict[str, str]:
    return {
        "employee_id": "E0001",
        "app": policy.birthright_employee[0],
        "reason": "cover",
        "approval": "PR #123",
        "expires": "2026-12-31",
    }


def test_a_good_exception_loads(policy: Policy) -> None:
    raw = copy.deepcopy(raw_files())
    raw["exceptions"]["exceptions"].append(good_exception(policy))  # type: ignore[index]
    assert len(rebuild(raw).exceptions) == 1


def test_exception_without_approval_fails(policy: Policy) -> None:
    raw = copy.deepcopy(raw_files())
    item = good_exception(policy)
    del item["approval"]
    raw["exceptions"]["exceptions"].append(item)  # type: ignore[index]
    with pytest.raises(PolicyError, match="approval"):
        rebuild(raw)


def test_exception_with_blank_approval_fails(policy: Policy) -> None:
    raw = copy.deepcopy(raw_files())
    raw["exceptions"]["exceptions"].append({**good_exception(policy), "approval": "  "})  # type: ignore[index]
    with pytest.raises(PolicyError, match="approval"):
        rebuild(raw)


def test_exception_for_an_unknown_app_fails(policy: Policy) -> None:
    raw = copy.deepcopy(raw_files())
    raw["exceptions"]["exceptions"].append({**good_exception(policy), "app": "nope"})  # type: ignore[index]
    with pytest.raises(PolicyError, match="unknown app"):
        rebuild(raw)


def test_blast_radius_zero_fails() -> None:
    raw = copy.deepcopy(raw_files())
    raw["settings"]["blast_radius_n"] = 0  # type: ignore[index]
    with pytest.raises(PolicyError, match="blast_radius_n"):
        rebuild(raw)


def test_unknown_key_fails() -> None:
    raw = copy.deepcopy(raw_files())
    raw["settings"]["surprise"] = 1  # type: ignore[index]
    with pytest.raises(PolicyError, match="surprise"):
        rebuild(raw)


def test_break_glass_by_list_and_by_pattern(policy: Policy) -> None:
    assert policy.is_break_glass("breakglass-01@meridian.example")
    assert policy.is_break_glass("BreakGlass-02@meridian.example")
    assert policy.is_break_glass("breakglass-77@meridian.example")  # pattern only
    assert not policy.is_break_glass("breakglass-ab@meridian.example")
    assert not policy.is_break_glass("someone@meridian.example")


def test_break_glass_list_only_match() -> None:
    raw = copy.deepcopy(raw_files())
    raw["break_glass"] = {"accounts": ["odd-name@meridian.example"], "patterns": []}
    p = rebuild(raw)
    assert p.is_break_glass("odd-name@meridian.example")
    assert not p.is_break_glass("breakglass-01@meridian.example")


def test_break_glass_kind_and_fullmatch(policy: Policy) -> None:
    assert policy.break_glass_kind("breakglass-01@meridian.example") == "listed"
    assert policy.break_glass_kind("breakglass-77@meridian.example") == "pattern"
    assert policy.break_glass_kind("someone@meridian.example") is None
    assert policy.break_glass_kind("xbreakglass-77@meridian.example.evil") is None
    assert not policy.is_break_glass("breakglass-77@meridian.example.evil")


def test_break_glass_needs_an_account() -> None:
    raw = copy.deepcopy(raw_files())
    raw["break_glass"]["accounts"] = []  # type: ignore[index]
    with pytest.raises(PolicyError, match="accounts"):
        rebuild(raw)


def test_duplicate_yaml_key_fails(tmp_path: Path) -> None:
    for name in FILES:
        shutil.copy(default_policy_dir() / f"{name}.yaml", tmp_path)
    (tmp_path / "settings.yaml").write_text("blast_radius_n: 25\nblast_radius_n: 99\n")
    with pytest.raises(PolicyError, match=r"settings\.yaml.*duplicate key 'blast_radius_n'"):
        load_policy(tmp_path)


def test_unquoted_dates_load(tmp_path: Path, policy: Policy) -> None:
    for name in FILES:
        shutil.copy(default_policy_dir() / f"{name}.yaml", tmp_path)
    app = policy.birthright_employee[0]
    (tmp_path / "exceptions.yaml").write_text(
        "exceptions:\n"
        f"  - {{employee_id: E0001, app: {app}, reason: cover, approval: 'PR #1', "
        "expires: 2026-12-31}\n"
    )
    assert load_policy(tmp_path).exceptions[0].expires.isoformat() == "2026-12-31"


def test_month_start_after_reference_date_fails() -> None:
    raw = copy.deepcopy(raw_files())
    raw["settings"]["month_start"] = "2026-12-02"  # type: ignore[index]
    with pytest.raises(PolicyError, match="month_start"):
        rebuild(raw)


@pytest.mark.parametrize(
    ("over", "default"), [(1, 2), (2, 0), (0, 0)], ids=["over<default", "default<1", "both 0"]
)
def test_approvals_cross_check_fails(over: int, default: int) -> None:
    raw = copy.deepcopy(raw_files())
    raw["settings"]["approvals_over_n"] = over  # type: ignore[index]
    raw["settings"]["approvals_default"] = default  # type: ignore[index]
    with pytest.raises(PolicyError, match="approvals"):
        rebuild(raw)


def test_exception_employee_id_pattern(policy: Policy) -> None:
    raw = copy.deepcopy(raw_files())
    raw["exceptions"]["exceptions"].append(  # type: ignore[index]
        {**good_exception(policy), "employee_id": "bob"}
    )
    with pytest.raises(PolicyError, match="employee_id"):
        rebuild(raw)


def test_policy_mappings_are_read_only(policy: Policy) -> None:
    with pytest.raises(TypeError):
        policy.apps["x"] = None  # type: ignore[index]
    with pytest.raises(TypeError):
        policy.roles["x"] = None  # type: ignore[index]
    with pytest.raises(TypeError):
        policy.title_map["x"] = None  # type: ignore[index]


def test_build_reports_a_role_list_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    from meridian_access.policy import build

    table = {"a": {"sso_apps": [], "other_apps": []}}
    monkeypatch.setattr(build, "parse_policy_markdown", lambda _t: ({}, table))
    monkeypatch.setattr(build, "parse_catalog", lambda _t: ([], {"b": ("T", "D")}))
    with pytest.raises(ValueError, match="disagree on the role list"):
        build.build_from_public()


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", *args], capture_output=True, text=True, check=False
    )


@pytest.mark.parametrize("command", ["build", "seed-titles"])
def test_cli_check_passes(command: str) -> None:
    result = run_cli("meridian_access.policy", command, "--check")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("command", "file"), [("build", "apps.yaml"), ("seed-titles", "title_map.yaml")]
)
def test_cli_check_fails_on_a_modified_copy(command: str, file: str, tmp_path: Path) -> None:
    for name in FILES:
        shutil.copy(default_policy_dir() / f"{name}.yaml", tmp_path)
    (tmp_path / file).write_text("# changed\n" + (tmp_path / file).read_text())
    result = run_cli("meridian_access.policy", command, "--check", "--dir", str(tmp_path))
    assert result.returncode == 1
    assert file in result.stderr
