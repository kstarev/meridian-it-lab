"""With the prompt cache on, only the system prompt is cached (the owner's ruling R-3E-3 for the
sealed run of held_out_month2). The ticket's text and its sender go in the user turn, which
carries no cache marker. Checked on every request a month run sends, with tuning tickets."""

from typing import Any

import pytest

from meridian_dataset.month import TUNING_MONTH, month_for
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
