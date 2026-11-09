"""users.list (narrow lookup), the mention check, and that no model can reach either."""

import json
from pathlib import Path

import pytest

from meridian_dataset import World
from tier0.directory_api import DirectoryError
from tier0.identity.binding import (
    DIRECTORY_FIELDS,
    GATEWAY_PRINCIPAL,
    BoundIdentity,
    IdentityBinder,
)
from tier0.mentions import (
    MAX_LOOKUPS,
    MENTION_PRINCIPAL,
    MentionCheck,
    find_candidates,
)
from tier0.mocks.directory import MockDirectory
from tier0.mocks.slack import APP_ID, MockSlack

DOMAIN = "meridian.example"
SRC = Path(__file__).resolve().parents[1] / "src" / "tier0"


def _person(world: World, email: str):
    return next(p for p in world.people if p.primary_email == f"{email}@{DOMAIN}")


def _bound(world: World, email: str) -> BoundIdentity:
    p = _person(world, email)
    return BoundIdentity(
        directory_id=p.directory_id,
        email=p.primary_email,
        slack_user_id=p.slack_user_id,
        slack_team_id=world.slack_team.team_id,
        full_name=p.full_name,
    )


def _text(world: World, ticket_id: str) -> str:
    return next(t.text for t in world.tickets if t.id == ticket_id)


# --- users.list -------------------------------------------------------------


def test_list_by_email_and_name_ids_only(world: World, directory: MockDirectory) -> None:
    kenji = _person(world, "kenji.weber")
    out = directory.users_list(query=f"email:{kenji.primary_email.upper()}", principal="t")
    assert out == {"kind": "admin#directory#users", "users": [{"id": kenji.directory_id}]}
    out = directory.users_list(query="name:'kenji weber'", principal="t")
    assert out["users"] == [{"id": kenji.directory_id}]
    both = directory.users_list(query="name:'Alex Kim'", principal="t")
    assert len(both["users"]) == 2
    assert all(set(u) == {"id"} for u in both["users"])
    assert (
        len(directory.users_list(query="name:'Alex Kim'", principal="t", max_results=1)["users"])
        == 1
    )


def test_list_no_match_omits_users(directory: MockDirectory) -> None:
    out = directory.users_list(query=f"email:nobody.here@{DOMAIN}", principal="t")
    assert out == {"kind": "admin#directory#users"}


@pytest.mark.parametrize(
    "query",
    [
        "",
        "email:*@meridian.example",
        "email:kenji*",
        "name:'Kenji*'",
        "name:Kenji",
        "isAdmin:true",
        "orgUnitPath:/",
        "email:a.b@meridian.example name:'Kenji Weber'",
        "kenji.weber@meridian.example",
        "email:",
        "name:''",
    ],
)
def test_list_rejects_other_queries(directory: MockDirectory, query: str) -> None:
    with pytest.raises(DirectoryError) as exc:
        directory.users_list(query=query, principal="t")
    assert exc.value.code == 400


def test_list_rejects_fields_and_max_results(directory: MockDirectory) -> None:
    q = f"email:kenji.weber@{DOMAIN}"
    for kwargs in (
        {"fields": ("id", "primaryEmail")},
        {"fields": ("primaryEmail",)},
        {"max_results": 3},
        {"max_results": 0},
    ):
        with pytest.raises(DirectoryError) as exc:
            directory.users_list(query=q, principal="t", **kwargs)  # type: ignore[arg-type]
        assert exc.value.code == 400


def test_list_logs_every_call(world: World, directory: MockDirectory) -> None:
    kenji = _person(world, "kenji.weber")
    directory.users_list(query=f"email:{kenji.primary_email}", principal="p")
    directory.users_list(query="name:'Alex Kim'", principal="p")
    directory.users_list(query=f"email:x.y@{DOMAIN}", principal="p")
    with pytest.raises(DirectoryError):
        directory.users_list(query="email:*", principal="p")
    reads = directory.reads
    assert [r.method for r in reads] == ["users.list"] * 4
    assert all(r.resolved_id is None and r.fields == ("id",) for r in reads)
    assert [r.matched for r in reads] == [1, 2, 0, 0]
    assert reads[0].user_key == f"email:{kenji.primary_email}"
    directory.users_get(kenji.primary_email, principal="p", fields=("id",))
    last = directory.reads[-1]
    assert (last.method, last.resolved_id, last.matched) == (
        "users.get",
        kenji.directory_id,
        0,
    )


# --- grader / runner ----------------------------------------------------------


def test_runner_directory_reads_excludes_users_list() -> None:
    src = (SRC / "evals" / "runner.py").read_text()
    assert 'r.method == "users.get"' in src


# --- binder -------------------------------------------------------------------


def test_binder_fills_full_name(world: World, slack: MockSlack, directory: MockDirectory) -> None:
    binder = IdentityBinder(
        slack=slack,
        directory=directory,
        team_id=world.slack_team.team_id,
        app_id=APP_ID,
        domain=world.domain,
    )
    ticket = next(t for t in world.tickets if t.id == "TKT-001")
    result = binder.bind(json.loads(slack.message_event(ticket).body))
    assert isinstance(result, BoundIdentity)
    assert result.full_name == _person(world, "anika.sato").full_name
    (read,) = directory.reads
    assert read.principal == GATEWAY_PRINCIPAL
    assert read.method == "users.get"
    assert read.fields == DIRECTORY_FIELDS
    assert read.fields is not None
    assert "name" in read.fields


# --- find_candidates ------------------------------------------------------------


def test_find_candidates() -> None:
    emails, names = find_candidates(
        "Is Kenji Weber at Kenji.Weber@meridian.example or noah.jansen? "
        "Also sofia[dot]rahman[at]meridian.example, see help.example and docs.com. "
        "Kenji Weber again.",
        DOMAIN,
    )
    assert emails == [
        "kenji.weber@meridian.example",
        "sofia.rahman@meridian.example",
        "noah.jansen@meridian.example",
    ]
    assert "Kenji Weber" in names
    assert names.count("Kenji Weber") == 1


def test_find_candidates_nothing() -> None:
    assert find_candidates("what is my username? see wiki.example", DOMAIN) == ([], [])


# --- MentionCheck ---------------------------------------------------------------


def test_normal_template_no_lookups(world: World, directory: MockDirectory) -> None:
    res = MentionCheck(directory).check(
        _text(world, "TKT-003"), _bound(world, "sofia.rahman"), domain=DOMAIN
    )
    assert (res.others, res.namesakes, res.lookups) == (0, 0, 0)
    assert not res.mentions_other_user
    assert directory.reads == ()


def test_colleague_template_mentions_other(world: World, directory: MockDirectory) -> None:
    res = MentionCheck(directory).check(
        _text(world, "TKT-005"), _bound(world, "anika.sato"), domain=DOMAIN
    )
    assert res.mentions_other_user
    assert res.others >= 1
    assert res.lookups <= MAX_LOOKUPS
    assert {r.principal for r in directory.reads} == {MENTION_PRINCIPAL}


def test_namesake_only_is_not_a_handoff_signal(world: World, directory: MockDirectory) -> None:
    res = MentionCheck(directory).check(
        _text(world, "TKT-004"), _bound(world, "alex.kim2"), domain=DOMAIN
    )
    assert (res.others, res.namesakes) == (0, 1)
    assert not res.mentions_other_user


def test_own_name_and_email_not_looked_up(world: World, directory: MockDirectory) -> None:
    res = MentionCheck(directory).check(
        "Alex Kim here, alex.kim@meridian.example", _bound(world, "alex.kim"), domain=DOMAIN
    )
    assert (res.others, res.namesakes, res.lookups) == (0, 0, 0)
    assert directory.reads == ()


def test_namesake_plus_colleague(world: World, directory: MockDirectory) -> None:
    res = MentionCheck(directory).check(
        "I tried alex.kim@meridian.example and Kenji Weber",
        _bound(world, "alex.kim2"),
        domain=DOMAIN,
    )
    assert res.others >= 1
    assert res.namesakes == 1


def test_lookups_are_capped(world: World, directory: MockDirectory) -> None:
    text = " ".join(f"user{chr(97 + i)}x.person{chr(97 + i)}x@{DOMAIN}" for i in range(12))
    text += " Kenji Weber"
    res = MentionCheck(directory).check(text, _bound(world, "alex.kim"), domain=DOMAIN)
    assert res.lookups <= MAX_LOOKUPS
    assert len(directory.reads) <= MAX_LOOKUPS


# --- not a model tool -------------------------------------------------------------


def test_users_list_unreachable_by_models() -> None:
    for sub in ("models", "mcp_server"):
        for path in (SRC / sub).rglob("*.py"):
            assert "users_list" not in path.read_text(), path


def test_mcp_tools_exactly_get_my_username() -> None:
    from tier0.mcp_server.server import TOOL_ZONES

    assert sorted(TOOL_ZONES) == ["get_my_username"]
