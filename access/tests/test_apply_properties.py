"""Property-based tests for apply (hypothesis; AAC-007): idempotent, honest in a dry run, and
never touching a break-glass account.

The rules checked here, on a tiny made-up world of three mocks:

- applying a plan, then the same changes again against the new state, changes nothing the
  second time and makes no write;
- the final state does not depend on the order of the changes, on a change listed twice, or on
  injected 429 and 503 answers;
- a dry run changes nothing, and lists exactly what a mock-live run applies;
- every change a mock-live run lists as applied is in its wanted state afterwards;
- no change names a break-glass account (listed, by pattern, by its email or a Google alias,
  in any case or with spaces) and reaches a mock: it is dropped, and no write touches it;
- apply refuses, before any write, a plan with fewer approvals than the larger of what the
  file claims and what its changes need, and a plan whose state changed;
- a change changes only the account it names: when two accounts go by its name (case and
  spaces ignored), the one with exactly its name, and when no single one has, neither, and the
  change fails; an account that changed although no change names it is reported.

Each run builds fresh mocks. No world, no network. The runs are fixed (derandomize) and keep no
example database.
"""

import copy
import dataclasses
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from meridian_access.apply import apply_plan, issue_write_tokens
from meridian_access.approvals import required_for
from meridian_access.collect import Mocks, actual_state
from meridian_access.mocks import GoogleMock, OktaMock, SlackMock
from meridian_access.plan import Plan
from meridian_access.policy.load import load_policy

SETTINGS = settings(derandomize=True, max_examples=60, database=None, deadline=None)
POLICY = load_policy()
D = "meridian.example"

# Okta: login -> (id, email). bg-admin's email is listed; breakglass-7 matches the pattern.
OKTA_USERS = {
    f"ana@{D}": ("00u1", f"ana@{D}"),
    f"ben@{D}": ("00u2", f"ben@{D}"),
    f"cara@{D}": ("00u3", f"cara@{D}"),
    f"breakglass-01@{D}": ("00u4", f"breakglass-01@{D}"),
    "bg-admin": ("00u5", f"breakglass-02@{D}"),
    f"breakglass-7@{D}": ("00u6", f"breakglass-7@{D}"),
}
APPS = {"github": "0oaG", "jira": "0oaJ"}
# Google: primary -> (id, aliases). ops@ has a listed alias.
GOOGLE_USERS = {
    f"ana@{D}": ("g1", ()),
    f"ben@{D}": ("g2", ()),
    f"cara@{D}": ("g3", ()),
    f"ops@{D}": ("g4", (f"breakglass-03@{D}",)),
    f"breakglass-01@{D}": ("g5", ()),
}
# Slack: userName -> (id, email). bgslack's email is listed.
SLACK_USERS = {
    "ana": ("U1", f"ana@{D}"),
    "ben": ("U2", f"ben@{D}"),
    "cara": ("U3", f"cara@{D}"),
    "breakglass-7": ("U4", f"breakglass-7@{D}"),
    "bgslack": ("U5", f"breakglass-01@{D}"),
}
BREAK_GLASS_IDS = {"00u4", "00u5", "00u6", "g4", "g5", "U4", "U5"}
SAFE = {
    "okta": [f"ana@{D}", f"ben@{D}", f"cara@{D}"],
    "google": [f"ana@{D}", f"ben@{D}", f"cara@{D}"],
    "slack": ["ana", "ben", "cara"],
}
PROTECTED = {
    "okta": [f"breakglass-01@{D}", "bg-admin", f"breakglass-7@{D}", f"breakglass-02@{D}"],
    "google": [f"ops@{D}", f"breakglass-01@{D}", f"breakglass-03@{D}"],
    "slack": ["breakglass-7", "bgslack", f"breakglass-7@{D}"],
}

OKTA_STATUS = ("ACTIVE", "SUSPENDED", "DEPROVISIONED")


@st.composite
def worlds(draw):
    """Starting states: each user's status, and which Okta app assignments exist."""
    okta = {login: draw(st.sampled_from(OKTA_STATUS)) for login in OKTA_USERS}
    assigned = {(login, app): draw(st.booleans()) for login in OKTA_USERS for app in APPS}
    google = {email: draw(st.booleans()) for email in GOOGLE_USERS}
    slack = {name: draw(st.booleans()) for name in SLACK_USERS}
    return okta, assigned, google, slack


def make_mocks(world, fault_rate=0.0) -> Mocks:
    okta, assigned, google, slack = world
    okta_data = {
        "users": [
            {
                "id": uid,
                "status": okta[login],
                "created": "2025-01-01T00:00:00.000Z",
                "lastLogin": None,
                "profile": {"login": login, "email": email, "firstName": "", "lastName": ""},
            }
            for login, (uid, email) in OKTA_USERS.items()
        ],
        "apps": [
            {"id": aid, "label": name, "name": name, "status": "ACTIVE"}
            for name, aid in APPS.items()
        ],
        "assignments": [
            {
                "appId": APPS[app],
                "userId": OKTA_USERS[login][0],
                "created": "",
                "scope": "USER",
                "profile": {"role": "member"},
            }
            for (login, app), yes in assigned.items()
            if yes
        ],
        "system_log": [],
    }
    google_data = {
        "users": [
            {
                "id": gid,
                "primaryEmail": email,
                "name": {"givenName": "", "familyName": "", "fullName": ""},
                "suspended": google[email],
                "orgUnitPath": "/",
                "aliases": list(aliases),
                "creationTime": "",
                "lastLoginTime": "",
            }
            for email, (gid, aliases) in GOOGLE_USERS.items()
        ]
    }
    slack_data = {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
        "totalResults": len(SLACK_USERS),
        "Resources": [
            {
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
                "id": sid,
                "userName": name,
                "displayName": name,
                "emails": [{"value": email, "primary": True}],
                "active": slack[name],
            }
            for name, (sid, email) in SLACK_USERS.items()
        ],
    }
    return Mocks(
        OktaMock(okta_data, fault_rate=fault_rate),
        GoogleMock(google_data, fault_rate=fault_rate),
        SlackMock(slack_data, fault_rate=fault_rate),
    )


def snapshot(mocks: Mocks) -> dict:
    return copy.deepcopy(
        {"okta": mocks.okta.data, "google": mocks.google.data, "slack": mocks.slack.data}
    )


def spy_writes(mocks: Mocks) -> list[tuple[str, str, Any]]:
    seen: list[tuple[str, str, Any]] = []
    for mock in (mocks.okta, mocks.google, mocks.slack):
        original = mock.request

        def spy(method, path, *, _o=original, **kw):
            if method != "GET":
                seen.append((method, path, kw.get("body")))
            return _o(method, path, **kw)

        mock.request = spy
    return seen


def change(system, action, account, app_id="", eid="E1", day=""):
    return {
        "type": "leaver_access_left",
        "employee_id": eid,
        "account": account,
        "system": system,
        "app_id": app_id,
        "app_name": "",
        "action": action,
        "reason": "test",
        "evidence": [],
        "cause_date": day,
        "hold": "",
    }


def _spell(name: str):
    return st.sampled_from((name, name.upper(), f"  {name} ", name.title()))


@st.composite
def changes(draw, names=SAFE, max_size=10):
    """Plan rows. One target never gets both an add and a removal (the diff never asks for
    both; with both, every apply would take the access away and give it back)."""
    out = []
    wants: dict[tuple[str, str, str], bool] = {}
    for _ in range(draw(st.integers(0, max_size))):
        system = draw(st.sampled_from(("okta", "google", "slack")))
        name = draw(st.sampled_from(names[system]).flatmap(_spell))
        action = draw(st.sampled_from(("suspend", "remove", "add")))
        app_id = draw(st.sampled_from(("", *APPS))) if system == "okta" else ""
        target = (system, app_id, name.strip().casefold())
        if wants.setdefault(target, action == "add") != (action == "add"):
            continue
        out.append(change(system, action, name, app_id))
    return out


def hand_plan(mocks, rows, approvals_required=1, part="main") -> Plan:
    return Plan(
        {
            "version": 2,
            "part": part,
            "as_of": "2026-12-01",
            "actual_state_sha256": actual_state(mocks, POLICY, sleep=lambda s: None).digest(),
            "changes": rows,
            "held": [],
            "warnings": [],
            "manual_tasks": [],
            "approvals_required": approvals_required,
        }
    )


def apply(mocks, rows, *, mode="mock-live", approvals=2, policy=POLICY, plan=None):
    plan = plan or hand_plan(mocks, rows)
    return apply_plan(
        plan,
        mocks,
        mode=mode,
        approvals=approvals,
        policy=policy,
        write_token=issue_write_tokens(mocks),
        sleep=lambda s: None,
        rebuild=lambda state: plan,
    )


def what(result_list):
    return [
        (c["system"], c["app_id"], c["account"], c["action"], c.get("call", ""))
        for c in result_list
    ]


# --- idempotent ----------------------------------------------------------------------------------


@SETTINGS
@given(worlds(), changes())
def test_the_same_changes_again_change_nothing_and_write_nothing(world, rows):
    mocks = make_mocks(world)
    first = apply(mocks, rows)
    assert not first.refused_reason and not first.failed
    after = snapshot(mocks)
    writes = spy_writes(mocks)
    second = apply(mocks, rows)
    assert not second.refused_reason
    assert second.applied == [] and second.failed == []
    assert writes == []
    assert snapshot(mocks) == after


@SETTINGS
@given(worlds(), changes(), st.randoms(use_true_random=False))
def test_order_and_repeats_do_not_change_the_final_state(world, rows, rnd):
    plain = make_mocks(world)
    apply(plain, rows)
    shuffled = [*rows, *rows]
    rnd.shuffle(shuffled)
    mixed = make_mocks(world)
    result = apply(mixed, shuffled)
    assert not result.failed
    assert snapshot(mixed) == snapshot(plain)


@settings(derandomize=True, max_examples=25, database=None, deadline=None)
@given(worlds(), changes())
def test_injected_429_and_503_answers_do_not_change_the_final_state(world, rows):
    clean = make_mocks(world)
    apply(clean, rows)
    noisy = make_mocks(world, fault_rate=0.25)
    result = apply(noisy, rows)
    assert not result.failed and not result.refused_reason
    assert snapshot(noisy) == snapshot(clean)


# --- honest -----------------------------------------------------------------------------------


@SETTINGS
@given(worlds(), changes())
def test_a_dry_run_changes_nothing_and_lists_what_a_live_run_applies(world, rows):
    dry_mocks = make_mocks(world)
    before = snapshot(dry_mocks)
    writes = spy_writes(dry_mocks)
    dry = apply(dry_mocks, rows, mode="dry-run")
    assert writes == [] and snapshot(dry_mocks) == before
    live = apply(make_mocks(world), rows)
    assert what(dry.applied) == what(live.applied)
    assert what(dry.skipped_already_done) == what(live.skipped_already_done)
    assert what(dry.not_applied) == what(live.not_applied)
    assert len(dry.deferred_deletes) == len(live.deferred_deletes)


def _wanted(mocks: Mocks, c: dict[str, Any]) -> bool:
    name = c["account"].strip().casefold()
    if c["system"] == "okta":
        user = next(u for u in mocks.okta.data["users"] if u["profile"]["login"].casefold() == name)
        if c["app_id"]:
            assigned = any(
                a["userId"] == user["id"] and a["appId"] == APPS[c["app_id"]]
                for a in mocks.okta.data["assignments"]
            )
            return assigned == (c["action"] == "add")
        if c["action"] == "suspend":
            return user["status"] in ("SUSPENDED", "DEPROVISIONED")
        return user["status"] == "DEPROVISIONED"
    if c["system"] == "google":
        user = next(u for u in mocks.google.data["users"] if u["primaryEmail"].casefold() == name)
        return user["suspended"] is True
    user = next(u for u in mocks.slack.data["Resources"] if u["userName"].casefold() == name)
    return user["active"] is False


@SETTINGS
@given(worlds(), changes())
def test_every_applied_change_is_in_its_wanted_state_afterwards(world, rows):
    mocks = make_mocks(world)
    result = apply(mocks, rows)
    for c in result.applied + result.skipped_already_done:
        assert _wanted(mocks, c), c
    # and every API change in the plan is accounted for exactly once
    api = [c for c in rows if not (c["action"] == "add" and not c["app_id"])]
    done = result.applied + result.skipped_already_done + result.not_applied
    assert len(done) == len(api) + sum(1 for c in rows if c["action"] == "add" and not c["app_id"])


# --- break-glass --------------------------------------------------------------------------------


@SETTINGS
@given(worlds(), changes(), changes(names=PROTECTED, max_size=8), st.randoms(use_true_random=False))
def test_no_change_reaches_a_break_glass_account(world, safe_rows, bad_rows, rnd):
    rows = [*safe_rows, *bad_rows]
    rnd.shuffle(rows)
    mocks = make_mocks(world)
    before = snapshot(mocks)
    writes = spy_writes(mocks)
    result = apply(mocks, rows)
    assert not result.refused_reason
    assert len(result.dropped_break_glass) == len(bad_rows)
    for _method, path, body in writes:
        touched = set(path.split("/")) | ({body.get("id")} if isinstance(body, dict) else set())
        assert not touched & BREAK_GLASS_IDS, path
    after = snapshot(mocks)
    for system, key in (("okta", "users"), ("google", "users"), ("slack", "Resources")):
        protected = [u for u in before[system][key] if u["id"] in BREAK_GLASS_IDS]
        assert protected == [u for u in after[system][key] if u["id"] in BREAK_GLASS_IDS]
    bg_assignments = [a for a in before["okta"]["assignments"] if a["userId"] in BREAK_GLASS_IDS]
    assert bg_assignments == [
        a for a in after["okta"]["assignments"] if a["userId"] in BREAK_GLASS_IDS
    ]


@SETTINGS
@given(worlds(), changes(names=PROTECTED, max_size=6))
def test_without_the_break_glass_policy_the_same_changes_would_be_made(world, rows):
    """The control: the guard, not the mocks or the plan, is what keeps those accounts."""
    loose = dataclasses.replace(POLICY, break_glass_accounts=frozenset(), break_glass_patterns=())
    mocks = make_mocks(world)
    result = apply(mocks, rows, policy=loose)
    assert result.dropped_break_glass == []
    strict = apply(make_mocks(world), rows)
    assert len(strict.dropped_break_glass) == len(rows)
    assert strict.applied == []


# --- refusals ------------------------------------------------------------------------------------


@SETTINGS
@given(
    worlds(),
    st.integers(1, 7),
    st.sampled_from(("", "2026-11-03")),
    st.integers(0, 3),
    st.integers(0, 3),
    st.sampled_from(("main", "held")),
)
def test_too_few_approvals_are_refused_before_any_write(world, n, day, claimed, have, part):
    mocks = make_mocks(world)
    names = [*SAFE["okta"], *SAFE["slack"], *SAFE["google"]]
    systems = ["okta"] * 3 + ["slack"] * 3 + ["google"] * 3
    rows = [change(systems[i % 9], "suspend", names[i % 9], eid=f"E{i}", day=day) for i in range(n)]
    plan = hand_plan(mocks, rows, approvals_required=claimed, part=part)
    need = max(claimed, required_for(rows, POLICY))
    if part == "held":
        need = max(need, POLICY.settings.approvals_over_n)
    before = snapshot(mocks)
    writes = spy_writes(mocks)
    result = apply(mocks, rows, approvals=have, plan=plan)
    if have < need:
        assert result.refused_reason == f"needs {need} approvals"
        assert writes == [] and snapshot(mocks) == before
    else:
        assert not result.refused_reason


@SETTINGS
@given(worlds(), changes(), st.sampled_from(("okta", "google", "slack")))
def test_a_plan_built_before_a_state_change_is_refused_as_stale(world, rows, system):
    mocks = make_mocks(world)
    plan = hand_plan(mocks, rows)
    # someone changes one account by hand after the plan was built
    if system == "okta":
        mocks.okta.data["users"][0]["status"] = (
            "SUSPENDED" if mocks.okta.data["users"][0]["status"] != "SUSPENDED" else "ACTIVE"
        )
    elif system == "google":
        mocks.google.data["users"][0]["suspended"] ^= True
    else:
        mocks.slack.data["Resources"][0]["active"] ^= True
    before = snapshot(mocks)
    writes = spy_writes(mocks)
    for mode in ("mock-live", "dry-run"):
        result = apply(mocks, rows, plan=plan, mode=mode)
        assert result.refused_reason == "stale"
    assert writes == [] and snapshot(mocks) == before


# --- focused tests for what mutation testing found unchecked -------------------------------------

ALL_ACTIVE = (
    dict.fromkeys(OKTA_USERS, "ACTIVE"),
    {(login, app): True for login in OKTA_USERS for app in APPS},
    dict.fromkeys(GOOGLE_USERS, False),
    dict.fromkeys(SLACK_USERS, True),
)


def answer_with(mock, method, status=None, *, effect=True, vanish=None):
    """Make `mock` answer every `method` request with `status` (after making the change, if
    `effect`), or remove the user `vanish` from Google after the change."""
    original = mock.request

    def fake(m, path, **kw):
        if m != method:
            return original(m, path, **kw)
        response = original(m, path, **kw) if effect else None
        if vanish:
            mock.data["users"] = [u for u in mock.data["users"] if u["primaryEmail"] != vanish]
        if status is None:
            return response
        return type(response or original("GET", "/nope", token=kw["token"]))(status, {}, {})

    mock.request = fake


def test_mock_live_without_write_tokens_is_refused_and_writes_nothing():
    mocks = make_mocks(ALL_ACTIVE)
    rows = [change("slack", "suspend", "ana")]
    plan = hand_plan(mocks, rows)
    writes = spy_writes(mocks)
    result = apply_plan(
        plan,
        mocks,
        mode="mock-live",
        approvals=1,
        policy=POLICY,
        rebuild=lambda s: plan,
        sleep=lambda s: None,
    )
    assert result.refused_reason == "no write token" and writes == []


def test_without_a_rebuild_or_a_world_apply_refuses():
    mocks = make_mocks(ALL_ACTIVE)
    plan = hand_plan(mocks, [change("slack", "suspend", "ana")])
    result = apply_plan(
        plan,
        mocks,
        mode="mock-live",
        approvals=1,
        policy=POLICY,
        write_token=issue_write_tokens(mocks),
        sleep=lambda s: None,
    )
    assert result.refused_reason == "no world to rebuild the plan from"


@given(st.integers(0, 3), st.integers(0, 2))
@settings(derandomize=True, max_examples=12, database=None, deadline=None)
def test_changes_held_for_bad_data_are_counted_and_never_applied(bad, bulk):
    mocks = make_mocks(ALL_ACTIVE)
    held = [{**change("slack", "remove", "ben"), "hold": "bad_data"} for _ in range(bad)]
    held += [{**change("slack", "remove", "cara"), "hold": "bulk"} for _ in range(bulk)]
    plan = Plan({**hand_plan(mocks, []).data, "held": held})
    writes = spy_writes(mocks)
    result = apply(mocks, [], plan=plan)
    assert result.held_not_applied == bad and writes == []
    line = f"{bad} changes held for bad HR data are never applied"
    assert (line in result.log) == (bad > 0)


def test_an_exports_folder_given_is_used_even_with_a_world(world_dir, tmp_path):
    mocks = make_mocks(ALL_ACTIVE)
    exports = world_dir / "exports"
    state = actual_state(mocks, POLICY, exports, sleep=lambda s: None)
    plan = Plan({**hand_plan(mocks, []).data, "actual_state_sha256": state.digest()})
    result = apply_plan(
        plan,
        mocks,
        mode="dry-run",
        approvals=1,
        policy=POLICY,
        sleep=lambda s: None,
        world_dir=tmp_path,
        exports_dir=exports,
        rebuild=lambda s: plan,
    )
    assert result.refused_reason == ""


def _fail_later_app_reads(mocks):
    """The first read of the Okta app list (the collector's) works; later ones fail."""
    original, seen = mocks.okta.request, []

    def fake(method, path, **kw):
        if method == "GET" and path == "/api/v1/apps":
            seen.append(path)
            if len(seen) > 1:
                return type(original("GET", path, **kw))(500, {}, {})
        return original(method, path, **kw)

    mocks.okta.request = fake


def test_the_okta_app_list_is_read_only_for_okta_app_adds():
    mocks = make_mocks(ALL_ACTIVE)
    plan = hand_plan(mocks, [change("okta", "remove", f"ana@{D}", "github")])
    _fail_later_app_reads(mocks)
    assert apply(mocks, [], plan=plan).refused_reason == ""
    mocks = make_mocks(ALL_ACTIVE)
    rows = [change("google", "add", f"ana@{D}"), change("okta", "add", f"ana@{D}")]
    plan = hand_plan(mocks, rows)
    _fail_later_app_reads(mocks)
    assert apply(mocks, [], plan=plan).refused_reason == ""
    mocks = make_mocks(ALL_ACTIVE)
    plan = hand_plan(mocks, [change("okta", "add", f"ana@{D}", "github")])
    _fail_later_app_reads(mocks)
    result = apply(mocks, [], plan=plan)
    assert result is not None
    assert result.refused_reason.startswith("cannot read the Okta apps")


def test_the_last_log_line_says_would_for_a_dry_run_and_applied_for_a_live_one():
    rows = [change("slack", "suspend", "ana")]
    dry = apply(make_mocks(ALL_ACTIVE), rows, mode="dry-run")
    live = apply(make_mocks(ALL_ACTIVE), rows)
    assert dry.log[-1].startswith("would apply 1,")
    assert live.log[-1].startswith("applied 1,")


def test_when_the_state_cannot_be_read_after_the_writes_every_change_is_failed():
    mocks = make_mocks(ALL_ACTIVE)
    plan = hand_plan(mocks, [change("slack", "suspend", "ana"), change("slack", "remove", "ben")])
    original, wrote = mocks.slack.request, []

    def fake(method, path, **kw):
        if method != "GET":
            wrote.append(path)
        elif wrote:
            return type(original(method, path, **kw))(500, {}, {})
        return original(method, path, **kw)

    mocks.slack.request = fake
    result = apply(mocks, [], plan=plan)
    assert result.applied == [] and len(result.failed) == 2
    assert all(c["error"].startswith("could not read the state to verify") for c in result.failed)


def test_an_account_gone_after_a_200_is_failed_not_applied():
    mocks = make_mocks(ALL_ACTIVE)
    plan = hand_plan(mocks, [change("google", "suspend", f"ana@{D}")])
    answer_with(mocks.google, "PATCH", vanish=f"ana@{D}")
    result = apply(mocks, [], plan=plan)
    assert result.applied == []
    assert [c["error"] for c in result.failed] == ["not in the wanted state after the call"]


def test_an_unknown_action_on_a_directory_account_is_not_applied():
    for system, name in (("okta", f"ana@{D}"), ("google", f"ana@{D}"), ("slack", "ana")):
        mocks = make_mocks(ALL_ACTIVE)
        rows = [change(system, "delete", name), change(system, "unsuspend", name)]
        writes = spy_writes(mocks)
        result = apply(mocks, rows)
        assert writes == [] and result.applied == []
        assert [c["action"] for c in result.not_applied] == ["delete", "unsuspend"]


def test_a_write_that_keeps_failing_is_failed_and_the_next_one_is_still_tried():
    mocks = make_mocks(ALL_ACTIVE)
    rows = [change("google", "remove", f"ana@{D}"), change("google", "remove", f"ANA@{D}")]
    plan = hand_plan(mocks, rows)
    answer_with(mocks.google, "PATCH", 503, effect=False)
    writes = spy_writes(mocks)
    result = apply(mocks, [], plan=plan)
    assert len(result.failed) == 2 and result.applied == []
    assert result.deferred_deletes == []  # not suspended, so no later delete either
    assert len(writes) >= 2


def test_a_404_for_a_gone_google_user_is_already_done_with_no_later_delete():
    mocks = make_mocks(ALL_ACTIVE)
    plan = hand_plan(mocks, [change("google", "remove", f"ana@{D}")])
    answer_with(mocks.google, "PATCH", 404, effect=False)
    result = apply(mocks, [], plan=plan)
    assert [c["why"] for c in result.skipped_already_done] == ["already gone (404)"]
    assert result.deferred_deletes == [] and result.failed == []


def test_only_a_2xx_answer_is_a_success():
    for status, ok in ((199, False), (200, True), (299, True), (300, False), (301, False)):
        mocks = make_mocks(ALL_ACTIVE)
        plan = hand_plan(mocks, [change("google", "remove", f"ana@{D}")])
        answer_with(mocks.google, "PATCH", status)
        result = apply(mocks, [], plan=plan)
        if ok:
            assert len(result.applied) == 1 and len(result.deferred_deletes) == 1, status
        else:
            assert [c["error"] for c in result.failed] == [f"status {status}"], status
            assert result.deferred_deletes == [], status


def test_an_add_is_made_only_for_an_okta_app_a_live_okta_user_and_a_known_app():
    world = (
        {**ALL_ACTIVE[0], f"ben@{D}": "DEPROVISIONED"},
        {k: False for k in ALL_ACTIVE[1]},
        ALL_ACTIVE[2],
        ALL_ACTIVE[3],
    )
    mocks = make_mocks(world)
    rows = [
        change("google", "add", f"ana@{D}", "github"),
        change("slack", "add", "ana"),
        change("okta", "add", f"ana@{D}"),
        change("okta", "add", f"ben@{D}", "github"),
        change("okta", "add", f"nobody@{D}", "github"),
        change("okta", "add", f"cara@{D}", "no-such-app"),
    ]
    writes = spy_writes(mocks)
    result = apply(mocks, rows)
    assert writes == [] and result.applied == [] and result.failed == []
    assert len(result.not_applied) == len(rows)
    why = [line for line in result.log if line.startswith("not applied")]
    assert sum("only Okta SSO apps" in line for line in why) == 3
    assert sum("no active Okta user" in line for line in why) == 2
    assert sum("no such app in Okta" in line for line in why) == 1


def test_an_add_listed_twice_is_made_once():
    world = (ALL_ACTIVE[0], {k: False for k in ALL_ACTIVE[1]}, ALL_ACTIVE[2], ALL_ACTIVE[3])
    mocks = make_mocks(world)
    rows = [
        change("okta", "add", f"ana@{D}", "github"),
        change("okta", "add", f"ANA@{D}", "github"),
    ]
    writes = spy_writes(mocks)
    result = apply(mocks, rows)
    assert [m for m, *_ in writes] == ["POST"]
    assert len(result.applied) == 1
    assert [c["why"] for c in result.skipped_already_done] == ["already assigned"]


def _step(c):
    if c["action"] == "suspend" and c["system"] in ("okta", "google"):
        return 0
    if c["action"] != "add" and (c["system"] == "slack" or (c["system"] == "okta" and c["app_id"])):
        return 1
    if c["action"] == "add":
        return 2
    return 3


@SETTINGS
@given(worlds(), changes())
def test_changes_run_suspend_first_then_app_removals_then_adds_then_the_rest(world, rows):
    result = apply(make_mocks(world), rows, mode="dry-run")
    steps = [_step(c) for c in result.applied]
    assert steps == sorted(steps)


def test_an_add_comes_after_an_app_removal_listed_after_it():
    world = (
        ALL_ACTIVE[0],
        {**{k: False for k in ALL_ACTIVE[1]}, (f"ben@{D}", "jira"): True},
        ALL_ACTIVE[2],
        ALL_ACTIVE[3],
    )
    rows = [
        change("okta", "add", f"ana@{D}", "github"),
        change("slack", "remove", "cara"),
        change("okta", "remove", f"ben@{D}", "jira"),
        change("google", "suspend", f"ben@{D}"),
    ]
    result = apply(make_mocks(world), rows, mode="dry-run")
    assert [(c["system"], c["action"]) for c in result.applied] == [
        ("google", "suspend"),
        ("slack", "remove"),
        ("okta", "remove"),
        ("okta", "add"),
    ]


def test_later_changes_in_one_plan_see_what_earlier_ones_did():
    mocks = make_mocks(ALL_ACTIVE)
    rows = [
        change("okta", "suspend", f"ana@{D}"),
        change("okta", "remove", f"ana@{D}"),  # still deactivated after the suspension
        change("okta", "remove", f"ben@{D}", "github"),
        change("okta", "remove", f"BEN@{D}", "github"),  # the assignment is gone: no 2nd call
        change("google", "suspend", f"cara@{D}"),
        change("google", "remove", f"cara@{D}"),  # already suspended: no 2nd call
        change("slack", "suspend", "cara"),
        change("slack", "remove", "cara"),
    ]
    writes = spy_writes(mocks)
    result = apply(mocks, rows)
    assert [(m, p) for m, p, _ in writes] == [
        ("POST", "/api/v1/users/00u1/lifecycle/suspend"),
        ("PATCH", "/admin/directory/v1/users/g3"),
        ("DELETE", "/api/v1/apps/0oaG/users/00u2"),
        ("PATCH", "/scim/v2/Users/U3"),
        ("POST", "/api/v1/users/00u1/lifecycle/deactivate"),
    ]
    assert len(result.applied) == 5 and len(result.skipped_already_done) == 3
    ana = next(u for u in mocks.okta.data["users"] if u["id"] == "00u1")
    assert ana["status"] == "DEPROVISIONED"


# --- one name, one account -----------------------------------------------------------------------


def _slack_user(sid, name, email):
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "id": sid,
        "userName": name,
        "displayName": name,
        "emails": [{"value": email, "primary": True}],
        "active": True,
    }


def _with_slack_first(*users):
    mocks = make_mocks(ALL_ACTIVE)
    mocks.slack.data["Resources"][:0] = list(users)
    mocks.slack.data["totalResults"] = len(mocks.slack.data["Resources"])
    return mocks


def _active(mocks, sid):
    return next(u["active"] for u in mocks.slack.data["Resources"] if u["id"] == sid)


def test_a_name_that_only_case_tells_apart_changes_the_account_it_names_exactly():
    mocks = _with_slack_first(_slack_user("U9", "ANA", f"ana.two@{D}"))
    writes = spy_writes(mocks)
    result = apply(mocks, [change("slack", "suspend", "ana")])
    assert [p for _, p, _ in writes] == ["/scim/v2/Users/U1"]
    assert _active(mocks, "U9") is True and _active(mocks, "U1") is False
    assert not result.failed


@SETTINGS
@given(st.sampled_from(("Ana", " aNa ", "ANA ")))
def test_a_name_two_accounts_go_by_and_neither_exactly_changes_neither(name):
    mocks = _with_slack_first(_slack_user("U9", "ANA", f"ana.two@{D}"))
    if name.strip() == "ANA":  # exactly the twin's name: it is the one named
        mocks.slack.data["Resources"][0]["userName"] = "AnA"
    before = snapshot(mocks)
    writes = spy_writes(mocks)
    result = apply(mocks, [change("slack", "suspend", name)])
    assert writes == [] and snapshot(mocks) == before
    assert [c["account"] for c in result.failed] == [name]
    assert "2 accounts go by this name" in result.failed[0]["error"]
    assert any(line.startswith("failed:") and "none was changed" in line for line in result.log)


def test_one_exact_name_on_two_ids_changes_neither():
    mocks = _with_slack_first(_slack_user("U9", "ana", f"ana.two@{D}"))
    before = snapshot(mocks)
    writes = spy_writes(mocks)
    result = apply(mocks, [change("slack", "remove", "ana")], mode="dry-run")
    assert result.applied == [] and [c["account"] for c in result.failed] == ["ana"]
    live = apply(mocks, [change("slack", "remove", "ana")])
    assert writes == [] and snapshot(mocks) == before
    assert [c["account"] for c in live.failed] == ["ana"]


def test_an_okta_add_for_a_name_two_users_go_by_assigns_nobody():
    mocks = make_mocks(ALL_ACTIVE)
    mocks.okta.data["users"].insert(
        0,
        {
            "id": "00u9",
            "status": "ACTIVE",
            "created": "2025-01-01T00:00:00.000Z",
            "lastLogin": None,
            "profile": {"login": f"ANA@{D}", "email": f"ana.two@{D}"},
        },
    )
    mocks.okta.data["assignments"] = []
    writes = spy_writes(mocks)
    result = apply(mocks, [change("okta", "add", f"Ana@{D}", "github")])
    assert writes == [] and mocks.okta.data["assignments"] == []
    assert [c["action"] for c in result.failed] == ["add"]


def _misrouted_google_mocks():
    """breakglass-01's Google user, listed first, has an alias that is cara's user id. The
    Directory mock finds a user by id, primary email or alias, in list order, so a PATCH to
    cara's id changes the break-glass user instead."""
    mocks = make_mocks(ALL_ACTIVE)
    users = mocks.google.data["users"]
    bg = next(u for u in users if u["primaryEmail"] == f"breakglass-01@{D}")
    users.remove(bg)
    users.insert(0, {**bg, "aliases": ["g3"]})
    return mocks


def test_a_write_that_changes_an_account_the_plan_does_not_name_is_reported():
    mocks = _misrouted_google_mocks()
    result = apply(mocks, [change("google", "suspend", f"cara@{D}")])
    google = {u["primaryEmail"]: u["suspended"] for u in mocks.google.data["users"]}
    assert google[f"breakglass-01@{D}"] is True  # the mock really changed it
    assert [c["account"] for c in result.failed] == [f"cara@{D}"]  # cara is not suspended
    unexpected = result.to_dict().get("unexpected", [])
    assert [(u["system"], u["account"], u["was"], u["now"]) for u in unexpected] == [
        ("google", f"breakglass-01@{D}", "active", "suspended")
    ]
    assert (
        f"failed: breakglass-01@{D} (google) changed during the apply (active -> suspended), "
        "but no change in the plan names it"
    ) in result.log


def test_an_okta_write_that_also_changes_another_okta_user_is_reported():
    mocks = make_mocks(ALL_ACTIVE)
    original = mocks.okta.request

    def also_ben(method, path, **kw):
        response = original(method, path, **kw)
        if method == "POST" and path.endswith("/lifecycle/suspend"):
            next(u for u in mocks.okta.data["users"] if u["id"] == "00u2")["status"] = "SUSPENDED"
        return response

    mocks.okta.request = also_ben
    result = apply(mocks, [change("okta", "suspend", f"ana@{D}")])
    assert [c["account"] for c in result.applied] == [f"ana@{D}"]
    rows = [(u["account"], u["app_id"], u["was"], u["now"]) for u in result.unexpected]
    # ben's user and both of ben's app assignments; ana's (named by the plan) are not listed
    assert rows == [
        (f"ben@{D}", "", "active", "suspended"),
        (f"ben@{D}", "github", "active", "suspended"),
        (f"ben@{D}", "jira", "active", "suspended"),
    ]
    assert any(f"ben@{D} (okta/github) changed during the apply" in line for line in result.log)


def test_changes_made_by_a_write_that_failed_are_still_looked_for():
    mocks = make_mocks(ALL_ACTIVE)
    original = mocks.slack.request

    def lost(method, path, **kw):
        if method == "PATCH":  # the write lands on another user, and every answer is lost
            next(u for u in mocks.slack.data["Resources"] if u["id"] == "U3")["active"] = False
            return type(original("GET", "/nope", token=kw["token"]))(503, {}, {})
        return original(method, path, **kw)

    mocks.slack.request = lost
    result = apply(mocks, [change("slack", "suspend", "ana")])
    assert result.applied == [] and [c["account"] for c in result.failed] == ["ana"]
    unexpected = result.to_dict().get("unexpected", [])
    assert [(u["account"], u["was"], u["now"]) for u in unexpected] == [
        ("cara", "active", "deprovisioned")
    ]


def test_a_malformed_answer_when_reading_the_state_after_the_writes_keeps_the_record():
    mocks = make_mocks(ALL_ACTIVE)
    plan = hand_plan(mocks, [change("google", "suspend", f"ana@{D}")])
    original, wrote = mocks.google.request, []

    def fake(method, path, **kw):
        response = original(method, path, **kw)
        if method != "GET":
            wrote.append(path)
        elif wrote and path == "/admin/directory/v1/users":  # a user with no primaryEmail
            users = [
                {k: v for k, v in u.items() if k != "primaryEmail"} for u in response.json["users"]
            ]
            return type(response)(200, {}, {**response.json, "users": users})
        return response

    mocks.google.request = fake
    result = apply(mocks, [], plan=plan)
    assert wrote == ["/admin/directory/v1/users/g1"]
    assert result.applied == [] and [c["account"] for c in result.failed] == [f"ana@{D}"]
    assert (
        result.failed[0]["error"] == "could not read the state to verify: KeyError 'primaryEmail'"
    )
    assert result.log[-1].startswith("applied 0,")
