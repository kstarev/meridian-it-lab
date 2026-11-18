"""With the prompt cache on, only the system prompt is cached (the owner's ruling R-3E-3 for the
sealed run of held_out_month2). The ticket's text and its sender go in the user turn, which
carries no cache marker. Checked on every request a month run sends, with tuning tickets, and
on every ticket of each sealed set by counts only (nothing is sent: a fake client stops each
request and keeps it in memory)."""

import contextlib
from typing import Any

import pytest

from meridian_dataset.month import SEALED_SETS, TUNING_MONTH, month_for
from tier0.agent import own_account_check_request, triage_request
from tier0.knowledge.answer import entries_for
from tier0.knowledge.picker import fit_request, pick_request
from tier0.knowledge.prompts import FIT_CHECKS, PICKER_VERSIONS
from tier0.models.claude import ClaudeModel


class _Sent(Exception):
    """Raised once a request is sent: only what was sent matters here."""


class _Client:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.messages = self
        self.beta = self  # some models go through client.beta.messages

    def create(self, **params: Any) -> Any:
        params.pop("betas", None)
        params.pop("fallbacks", None)
        self.sent.append(params)
        raise _Sent


def _requests() -> list[tuple[str, Any]]:
    from meridian_dataset.knowledge import load_runbooks

    entries = entries_for(load_runbooks())
    out = []
    for t in month_for(TUNING_MONTH).tickets[:60]:
        out.append((t.text, triage_request(t.text)))
        out.append(
            (
                t.text,
                own_account_check_request(
                    t.text, sender_name=t.sender_name, sender_email=t.sender_email
                ),
            )
        )
        for version in ("KS5", "KS6A-H", "KS6B-H"):
            out.append((t.text, pick_request(t.text, entries, PICKER_VERSIONS[version])))
        out.append((t.text, fit_request(t.text, entries[0], FIT_CHECKS["KS6C-H"])))
    return out


class _Ledger:
    def check(self, *args: Any, **kwargs: Any) -> None:
        pass

    def record(self, *args: Any, **kwargs: Any) -> None:
        pass


def _sealed_requests(name: str) -> list[tuple[str, Any]]:
    """The requests the frozen plan sends for every ticket of a sealed set: triage, the
    own-account check and KS6A-H's pick. Built in memory, never printed."""
    from meridian_dataset.knowledge import load_runbooks

    entries = entries_for(load_runbooks())
    out = []
    for t in month_for(name).tickets:
        out.append((t.text, triage_request(t.text)))
        out.append(
            (
                t.text,
                own_account_check_request(
                    t.text, sender_name=t.sender_name, sender_email=t.sender_email
                ),
            )
        )
        out.append((t.text, pick_request(t.text, entries, PICKER_VERSIONS["KS6A-H"])))
    return out


@pytest.mark.parametrize("name", sorted(SEALED_SETS))
def test_no_sealed_ticket_is_in_the_cached_part_counts_only(name: str) -> None:
    """R-3E-3 on the sealed sets themselves. Every check is a count, so a failure prints
    numbers, never a ticket's words."""
    client = _Client()
    model = ClaudeModel(
        model="claude-sonnet-5-5",
        client=client,
        ledger=_Ledger(),
        cache_system=True,  # type: ignore[arg-type]
    )
    requests = _sealed_requests(name)
    for _, request in requests:
        with contextlib.suppress(_Sent):
            model.complete(request)
    sent = len(client.sent)
    marked_wrong = in_cached_part = not_in_user_turn = marker_in_user_turn = 0
    for (text, request), params in zip(requests, client.sent, strict=True):
        blocks = params["system"]
        marked_wrong += len(blocks) != 1 or blocks[0].get("cache_control") != {"type": "ephemeral"}
        marked_wrong += blocks[0].get("text") != request.system
        in_cached_part += text.strip() in blocks[0].get("text", "")
        messages = params["messages"]
        marker_in_user_turn += "cache_control" in str(messages)
        not_in_user_turn += len(messages) != 1 or text.strip() not in messages[0]["content"]
    counts = (sent, marked_wrong, in_cached_part, marker_in_user_turn, not_in_user_turn)
    assert counts == (len(requests), 0, 0, 0, 0)
    assert sent >= 900  # 300 tickets, 3 requests each


def test_only_the_system_prompt_is_marked_for_the_cache() -> None:
    client = _Client()
    model = ClaudeModel(
        model="claude-sonnet-5-5",
        client=client,
        ledger=_Ledger(),
        cache_system=True,  # type: ignore[arg-type]
    )
    requests = _requests()
    assert len(requests) >= 300
    for _, request in requests:
        with pytest.raises(_Sent):
            model.complete(request)
    assert len(client.sent) == len(requests)
    for (text, request), sent in zip(requests, client.sent, strict=True):
        (block,) = sent["system"]
        assert block["cache_control"] == {"type": "ephemeral"}
        assert block["text"] == request.system
        assert text.strip() not in block["text"]  # the ticket is never in the cached part
        (message,) = sent["messages"]
        assert "cache_control" not in str(message)
        assert text.strip() in message["content"]
