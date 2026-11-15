"""Contract tests: every key path in a recorded example exists, with the same JSON type,
in what the mock returns (as tier0's ticket adapter has, lab ADR-004)."""

import json
import re
from pathlib import Path

import pytest

from meridian_access.mocks import GoogleMock, OktaMock, SlackMock

RECORDED = Path(__file__).parent / "fixtures" / "recorded"


def _type(value) -> str:
    return {
        bool: "boolean",
        int: "number",
        float: "number",
        str: "string",
        type(None): "null",
        list: "array",
        dict: "object",
    }[type(value)]


def paths(value, prefix=()) -> set[tuple[tuple[str, ...], str]]:
    """Every (key path, JSON type); list items share the path segment "[]"."""
    out = {(prefix, _type(value))}
    if isinstance(value, dict):
        for k, v in value.items():
            out |= paths(v, (*prefix, k))
    elif isinstance(value, list):
        for item in value:
            out |= paths(item, (*prefix, "[]"))
    return out


def _document(name: str) -> dict:
    data = json.loads((RECORDED / name).read_text())
    assert data["_note"].startswith("fake example in the shape of ")
    # the examples are written by hand from the public docs, not recorded from the mock
    assert "hand-written from the public API docs" in data["_note"]
    return data


def _recorded(name: str):
    return _document(name)["example"]


@pytest.fixture
def mocks(world):
    return {
        "okta": OktaMock.from_world(world, fault_rate=0),
        "google": GoogleMock.from_world(world, fault_rate=0),
        "slack": SlackMock.from_world(world, fault_rate=0),
    }


def _check(name: str, response) -> None:
    assert response.status == 200
    missing = paths(_recorded(name)) - paths(response.json)
    assert not missing, f"{name}: the mock lacks {sorted(missing)}"
    # Okta pages through the Link header: the documented rels have to be there, as
    # `<url>; rel="..."` entries like the documented ones
    doc = _document(name)
    if "link_rels" in doc:
        link = response.headers.get("Link", "")
        for rel in doc["link_rels"]:
            assert re.search(rf'<https?://[^>]+>; rel="{rel}"', link), f"{name}: no {rel} link"
        for example in doc["link_example"]:
            assert re.fullmatch(r'<https?://[^>]+\?[^>]*>; rel="\w+"', example)


def test_okta_users(mocks, world):
    m = mocks["okta"]
    _check("okta_users_list.json", m.request("GET", "/api/v1/users", token=m.issue_token("r")))


def test_okta_apps(mocks):
    m = mocks["okta"]
    _check("okta_apps_list.json", m.request("GET", "/api/v1/apps", token=m.issue_token("r")))


def test_okta_app_users(mocks, world):
    m = mocks["okta"]
    app = world.okta.assignments[0].app_id
    _check(
        "okta_app_users_list.json",
        m.request("GET", f"/api/v1/apps/{app}/users", token=m.issue_token("r")),
    )


def test_okta_logs(mocks):
    m = mocks["okta"]
    _check("okta_logs_list.json", m.request("GET", "/api/v1/logs", token=m.issue_token("r")))


def test_google_users(mocks):
    m = mocks["google"]
    _check(
        "google_users_list.json",
        m.request("GET", "/admin/directory/v1/users", token=m.issue_token("r")),
    )


def test_slack_users(mocks):
    m = mocks["slack"]
    _check("slack_users_list.json", m.request("GET", "/scim/v2/Users", token=m.issue_token("r")))


def test_the_check_catches_a_missing_key(mocks):
    m = mocks["slack"]
    r = m.request("GET", "/scim/v2/Users", token=m.issue_token("r"))
    broken = {k: v for k, v in r.json.items() if k != "itemsPerPage"}
    assert paths(_recorded("slack_users_list.json")) - paths(broken)


def test_every_recorded_example_says_it_is_hand_written_from_the_docs():
    files = sorted(RECORDED.glob("*.json"))
    assert len(files) == 6
    for f in files:
        _document(f.name)


def test_google_page_token_is_an_opaque_string_in_the_docs_example():
    token = _recorded("google_users_list.json")["nextPageToken"]
    assert isinstance(token, str) and not token.isdigit()  # not an offset the client can compute
