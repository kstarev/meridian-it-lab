"""Deterministic offline model used by tests, evals and the demo."""

import itertools
import json

from tier0.models.base import (
    ABOUT_SOMEONE_ELSE,
    OTHER,
    OWN_USERNAME,
    ModelRequest,
    ModelResponse,
)

_ELSE_MARKERS = (
    "'s username",
    "'s login",
    "'s email",
    "their username",
    "their login",
    "their email",
    "manager",
    "colleague",
    "coworker",
    "on behalf of",
)
_OWN_MARKERS = ("username", "login", "log in", "sign in", "email i use")
_HANDOFF = "I've passed your request to the IT team. Someone will follow up here."


class StubModel:
    """Keyword-based stand-in for a real model.

    It only exists so everything runs offline, free and repeatably. The safety
    rules never depend on it behaving well: the guard and policy table (step 9)
    catch a misbehaving model.
    """

    provider = "tier0.stub"
    model = "tier0-stub-1"

    def __init__(self) -> None:
        self._counter = itertools.count(1)

    def complete(self, request: ModelRequest) -> ModelResponse:
        if request.task == "triage":
            text = json.dumps({"category": self._triage(request.user)})
        else:
            username = request.facts.get("username")
            text = (
                f"Your username is {username}. Use it to sign in to Google Workspace and Okta."
                if username
                else _HANDOFF
            )
        # Word counts are only an estimate, so cost spans still carry numbers.
        return ModelResponse(
            text=text,
            model=self.model,
            input_tokens=len(f"{request.system} {request.user}".split()),
            output_tokens=len(text.split()),
            finish_reason="stop",
            response_id=f"stub-{next(self._counter)}",
        )

    @staticmethod
    def _triage(user_text: str) -> str:
        lowered = user_text.lower()
        if any(marker in lowered for marker in _ELSE_MARKERS):
            return ABOUT_SOMEONE_ELSE
        if any(marker in lowered for marker in _OWN_MARKERS):
            return OWN_USERNAME
        return OTHER
