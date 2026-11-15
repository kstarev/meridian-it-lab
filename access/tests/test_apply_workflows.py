"""The apply and approvals workflows: manual or plan-branch only, pinned, minimal permissions."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WF = ROOT / ".github" / "workflows"
APPLY = WF / "access-apply.yml"
APPROVALS = WF / "access-approvals.yml"


def load(path):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data, data.get("on", data.get(True))


def pins(path):
    return {
        m.group(1): m.group(2)
        for m in re.finditer(r"uses:\s*(\S+)@(\S+)", path.read_text(encoding="utf-8"))
    }


def known():
    out = {}
    for name in ("ci.yml", "access-plan.yml"):
        out.update(pins(WF / name))
    return out


def code(path):
    return "\n".join(
        ln
        for ln in path.read_text(encoding="utf-8").splitlines()
        if not ln.lstrip().startswith("#")
    )


def comment(path):
    return path.read_text(encoding="utf-8").replace("\n# ", " ")


def test_apply_is_manual_only_and_says_why():
    _, triggers = load(APPLY)
    assert set(triggers) == {"workflow_dispatch"}
    assert "Applying on merge needs the owner's GitHub App and ruleset" in comment(APPLY)
    assert "no mode for real systems" in comment(APPLY)


def test_apply_permissions_mode_and_approvals_input():
    data, triggers = load(APPLY)
    assert data["permissions"] == {}
    (job,) = data["jobs"].values()
    assert job["permissions"] == {"contents": "read"}
    assert job["env"]["ACCESS_APPLY_MODE"] == "${{ vars.ACCESS_APPLY_MODE || 'dry-run' }}"
    assert triggers["workflow_dispatch"]["inputs"]["approvals"]["default"] == "0"
    runs = "\n".join(s.get("run", "") for s in job["steps"])
    assert "make access-data" in runs and "meridian_access.plan" in runs
    assert "meridian_access.apply" in runs and '--approvals "${APPROVALS}"' in runs
    assert "--world out/access/seed-6108" in runs


def test_apply_uploads_the_log_even_when_apply_refuses():
    data, _ = load(APPLY)
    (job,) = data["jobs"].values()
    upload = next(s for s in job["steps"] if "upload-artifact" in s.get("uses", ""))
    assert upload["if"] == "always()"
    assert "apply-log.json" in upload["with"]["path"]


def test_apply_has_no_secrets_and_no_real_system_setting():
    text = code(APPLY)
    assert "secrets." not in text and "ANTHROPIC" not in text and "SLACK" not in text.upper()
    assert "environment:" not in text
    assert "real" not in text.lower().replace("realpath", "")


def test_approvals_always_runs_on_pr_and_review_events_and_says_what_protects_it():
    data, triggers = load(APPROVALS)
    assert set(triggers) == {"pull_request", "pull_request_review"}
    assert set(triggers["pull_request"]["types"]) == {"opened", "synchronize", "reopened"}
    (job,) = data["jobs"].values()
    assert "if" not in job  # always runs, so it can be a required check
    text = comment(APPROVALS)
    assert "required check" in text and "ruleset" in text
    assert "merge ref" in text and "can be edited by the thing it checks" in text
    assert "required code-owner review" in text and "apply does itself" in text


def test_approvals_permissions_are_read_only_and_the_token_is_scoped_to_one_step():
    data, _ = load(APPROVALS)
    assert data["permissions"] == {}
    (job,) = data["jobs"].values()
    assert job["permissions"] == {"pull-requests": "read", "contents": "read"}
    assert "env" not in job
    with_token = [s for s in job["steps"] if "GH_TOKEN" in s.get("env", {})]
    assert len(with_token) == 1 and "meridian_access.approvals" in with_token[0]["run"]


def test_approvals_follow_adr_002_plan_branch_files_and_not_a_plan_pr_exit():
    data, _ = load(APPROVALS)
    (job,) = data["jobs"].values()
    step = next(s for s in job["steps"] if "GH_TOKEN" in s.get("env", {}))
    runs = step["run"]
    assert "/files" in runs and "--paginate" in runs and "grep -q '^access/plan/'" in runs
    assert "not a plan PR" in runs and "exit 0" in runs
    assert "/reviews" in runs and "contents/${plan_path}?ref=${HEAD_SHA}" in runs
    assert "meridian_access.approvals" in runs and "--head-sha" in runs
    assert '--author "${PR_AUTHOR}"' in runs and "--policy-dir access/policy" in runs
    assert step["env"]["PR_AUTHOR"] == "${{ github.event.pull_request.user.login }}"
    text = code(APPROVALS)
    assert "access-plan/" not in text and "head_ref" not in text
    checkout = next(s for s in job["steps"] if "checkout" in s.get("uses", ""))
    assert checkout["with"]["persist-credentials"] is False


def test_both_workflows_pin_every_action_to_a_sha_ci_already_uses():
    for path in (APPLY, APPROVALS):
        mine = pins(path)
        assert mine
        for action, ref in mine.items():
            assert re.fullmatch(r"[0-9a-f]{40}", ref), (path.name, action)
            assert known().get(action) == ref, (path.name, action)
    assert pins(APPLY)["actions/upload-artifact"] == "cf430e030ddbb5b0abf93d22962f4752f3646cd9"


def test_the_pull_request_workflow_never_checks_out_the_pull_request_code():
    text = code(APPROVALS)
    assert "pull_request.head.ref" not in text and "head.sha }}" in text  # only as an env value
    data, _ = load(APPROVALS)
    (job,) = data["jobs"].values()
    checkout = next(s for s in job["steps"] if "checkout" in s.get("uses", ""))
    assert "head" not in str(checkout["with"].get("ref", ""))


def test_codeowners_cover_the_workflows_and_the_plan_directory():
    lines = (ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8").splitlines()
    owned = {
        parts[0]: parts[1:]
        for parts in (ln.split() for ln in lines if ln.strip() and not ln.startswith("#"))
    }
    assert owned["/.github/workflows/"] == ["@kstarev"]
    assert owned["/access/plan/"] == ["@kstarev"]
    assert owned["/access/policy/"] == ["@kstarev"]


def test_approvals_check_the_part_the_pull_request_touches_and_fail_on_both():
    data, _ = load(APPROVALS)
    (job,) = data["jobs"].values()
    step = next(s for s in job["steps"] if "GH_TOKEN" in s.get("env", {}))
    runs = step["run"]
    assert "grep -qx 'access/plan/held/plan\\.json' files.txt" in runs  # dots escaped
    assert "grep -qx 'access/plan/plan\\.json' files.txt" in runs
    assert "plan_path=access/plan/plan.json" in runs
    assert "plan_path=access/plan/held/plan.json" in runs
    # both parts in one pull request: fail
    both = runs[runs.index('if [ "${touches_held}" = yes ] && [ "${touches_main}" = yes ]') :]
    assert both.index("exit 1") < both.index("gh api -H")
    assert "a plan pull request holds one part" in runs
    # access/plan/ touched but neither plan.json: fail before anything is read
    neither = runs[runs.index('if [ "${touches_held}" = no ] && [ "${touches_main}" = no ]') :]
    assert "a plan pull request without a plan file" in neither
    assert neither.index("exit 1") < neither.index("gh api -H")
    # the path the file was read from goes to the check, which matches it to the part
    assert '--plan-path "${plan_path}"' in runs
    assert "held plan" in comment(APPROVALS) and "access/plan-held" in comment(APPROVALS)


def test_apply_only_ever_applies_the_main_part_and_has_no_held_path():
    assert "held/plan.json" in comment(APPLY)  # only the explanation names it
    assert "held/plan.json" not in code(APPLY)
    assert "no override" in comment(APPLY) and "two code-owner approvals" in comment(APPLY)
    data, _ = load(APPLY)
    (job,) = data["jobs"].values()
    runs = "\n".join(s.get("run", "") for s in job["steps"])
    assert "--plan out/access/plan/plan.json" in runs and "held" not in runs
    assert "held" not in code(APPLY)  # no step, input or env var can reach the held part
