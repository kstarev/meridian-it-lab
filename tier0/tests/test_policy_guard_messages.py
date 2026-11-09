"""The policy table, the output guard and the code-written texts."""

import dataclasses

import pytest

from tier0 import messages
from tier0.guard import check_reply
from tier0.identity.tokens import SCOPE_DIRECTORY_SELF_READ
from tier0.messages import AI_DISCLOSURE
from tier0.models import ABOUT_SOMEONE_ELSE, OTHER, OWN_USERNAME
from tier0.policy import (
    POLICY,
    TOOL_GET_MY_USERNAME,
    PolicyRule,
    PolicyViolation,
    check_tool_call,
    decide,
)

DOMAIN = "meridian.example"
URL = "https://help.meridian.example/verify-identity"


def test_policy_rows() -> None:
    assert dict(POLICY) == {
        OWN_USERNAME: PolicyRule(
            OWN_USERNAME, "A", "answer", (TOOL_GET_MY_USERNAME,), (SCOPE_DIRECTORY_SELF_READ,)
        ),
        ABOUT_SOMEONE_ELSE: PolicyRule(ABOUT_SOMEONE_ELSE, "A", "refuse", (), ()),
        OTHER: PolicyRule(OTHER, "A", "escalate", (), ()),
    }


def test_policy_is_read_only() -> None:
    with pytest.raises(TypeError):
        POLICY["new"] = POLICY[OTHER]  # type: ignore[index]
    with pytest.raises(dataclasses.FrozenInstanceError):
        POLICY[OWN_USERNAME].tier = "C"  # type: ignore[misc]


def test_decide_fails_closed() -> None:
    assert decide("whatever") is POLICY[OTHER]
    assert decide(OWN_USERNAME) is POLICY[OWN_USERNAME]


def test_check_tool_call() -> None:
    check_tool_call(POLICY[OWN_USERNAME], TOOL_GET_MY_USERNAME)
    with pytest.raises(PolicyViolation):
        check_tool_call(POLICY[OWN_USERNAME], "users_list")
    with pytest.raises(PolicyViolation):
        check_tool_call(POLICY[OTHER], TOOL_GET_MY_USERNAME)


def _check(text: str, email: str | None = "alex.kim2@meridian.example"):
    return check_reply(text, requester_email=email, domain=DOMAIN)


def test_guard_allows_own_address_in_any_case() -> None:
    assert _check("Use alex.kim2@meridian.example").allowed
    assert _check("Use ALEX.Kim2@Meridian.Example now").allowed


def test_guard_blocks_colleagues_sorted_and_unique() -> None:
    result = _check("zed.z@meridian.example, Anna.A@meridian.example and zed.z@MERIDIAN.example.")
    assert not result.allowed
    assert result.blocked == ("anna.a@meridian.example", "zed.z@meridian.example")


def test_guard_tells_lookalike_usernames_apart() -> None:
    assert _check("alex.kim2@meridian.example").allowed
    assert _check("alex.kim@meridian.example").blocked == ("alex.kim@meridian.example",)


def test_guard_ignores_other_domains_and_longer_domains() -> None:
    assert _check("mail bob@gmail.com").allowed
    assert _check("mail bob@notmeridian.example").allowed


def test_guard_without_requester_blocks_everything() -> None:
    assert _check("alex.kim2@meridian.example", None).blocked == ("alex.kim2@meridian.example",)
    assert _check("no address here", None).allowed


def test_disclosure_and_texts() -> None:
    assert "AI assistant" in AI_DISCLOSURE
    assert messages.with_disclosure("x") == AI_DISCLOSURE + "\n\nx"
    assert URL in messages.refuse_someone_else(URL)
    assert URL in messages.unverified(URL)


def test_code_written_texts_have_no_address() -> None:
    texts = [
        AI_DISCLOSURE,
        messages.HANDOFF,
        messages.GUARD_FALLBACK,
        messages.refuse_someone_else(URL),
        messages.unverified(URL),
        messages.TRIAGE_SYSTEM,
        messages.REPLY_SYSTEM,
    ]
    assert all("@" not in text for text in texts)


# --- bare usernames and obfuscation (tuning-world people only) ----------------------


def test_guard_allows_requesters_own_bare_username_in_any_case() -> None:
    assert _check("Your username is alex.kim2").allowed
    assert _check("Your username is ALEX.Kim2.").allowed


def test_guard_blocks_another_persons_bare_username() -> None:
    assert _check("Yes, it is kenji.sato.").blocked == ("kenji.sato",)
    assert _check("Kenji.Sato and kenji.sato").blocked == ("kenji.sato",)


def test_guard_tells_bare_lookalike_usernames_apart() -> None:
    assert _check("alex.kim2").allowed
    assert _check("alex.kim").blocked == ("alex.kim",)


def test_guard_undoes_simple_obfuscation() -> None:
    assert _check("kenji[dot]sato").blocked == ("kenji.sato",)
    assert _check("kenji (dot) sato").blocked == ("kenji.sato",)
    assert _check("kenji dot sato").blocked == ("kenji.sato",)
    assert _check("kenji.sato[at]meridian.example").blocked == ("kenji.sato@meridian.example",)
    assert _check("meet at noon").allowed


def test_guard_ignores_web_names_and_abbreviations() -> None:
    assert _check("Sign in at okta.com or example.org, e.g. today").allowed


def test_guard_without_requester_blocks_any_username_shaped_token() -> None:
    assert _check("alex.kim2", None).blocked == ("alex.kim2",)
