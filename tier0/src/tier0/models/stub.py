"""Deterministic offline model used by tests, evals and the demo."""

import itertools
import json

from tier0.models.base import (
    ABOUT_SOMEONE_ELSE,
    OTHER,
    OWN_USERNAME,
    ModelError,
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
    # Milestone 3 tuning wording (German and Spanish colleague requests).
    "benutzernamen von",
    "benutzername von",
    "kollegin",
    "compañera",
    "compañero",
)
# Tuning wording only (ADR-013): typos and German, Spanish and French terms seen in the
# tuning pool. Never extend these from held-out results.
_OWN_MARKERS = (
    "username",
    "login",
    "log in",
    "sign in",
    "email i use",
    "usernmae",
    "benutzername",
    "usuario",
    "identifiant",
)


class StubModel:
    """Keyword-based stand-in for a real model.

    It only exists so everything runs offline, free and repeatably. The safety
    rules never depend on it behaving well: the guard and policy table (step 9)
    catch a misbehaving model.
    """

    provider = "tier0.stub"
    temperature: float | None = None
    model = "tier0-stub-1"
    # Deterministic already, so no seed is needed.
    seed: int | None = None
    supports_seed = False

    def describe(self) -> dict[str, str]:
        return {}

    def __init__(self) -> None:
        self._counter = itertools.count(1)

    def complete(self, request: ModelRequest) -> ModelResponse:
        if request.task == "triage":
            text = json.dumps({"category": self._triage(request.user)})
        elif request.task == "own_account_check":
            text = json.dumps({"own_account_only": self._own_account_only(request.user)})
        elif request.task == "guide_draft":
            text = json.dumps(self._draft(request.user), ensure_ascii=False)
        elif request.task == "howto_gate":
            text = json.dumps({"plain_how_to": self._plain_how_to(request.user)})
        else:
            raise ModelError(f"the stub has no task {request.task!r}")
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
    def _own_account_only(user_text: str) -> bool:
        # Deterministic: false when any someone-else marker appears, else true. These are the
        # same markers triage uses, so for the stub the two answers are NOT independent
        # (a real model's check is). The stub only proves the wiring.
        lowered = user_text.lower()
        return not any(marker in lowered for marker in _ELSE_MARKERS)

    @staticmethod
    def _plain_how_to(user_text: str) -> bool:
        # Deterministic: true exactly when the stub's own triage would call the message "other",
        # the label a how-to question gets and the only one the picker is asked about; false for
        # a request about the sender's username or someone else's. Like the own-account check, it
        # uses triage's markers, so for the stub the two answers are NOT independent (a real
        # model's gate is). The stub only proves the wiring: offline, with the how-to gate on, a
        # message its triage calls "other" still reaches the picker.
        return StubModel._triage(user_text) == OTHER

    @staticmethod
    def _draft(user_text: str) -> dict[str, object]:
        """A placeholder guide in the runbook format (tier0.knowledge.drafting).

        It never copies a word of the employees' questions: an update returns the existing
        guide with one closing step, and a new guide is three generic steps on the topic. It
        only proves the wiring; a real draft needs a real model and a later budget (ADR-037)."""
        start, end = user_text.find("<guide>"), user_text.find("</guide>")
        if 0 <= start < end:
            guide = json.loads(user_text[start + len("<guide>") : end])
            closing = (
                "If these steps don't answer your question, post in #it-help and say which "
                "step you are on."
            )
            steps = [*[s for s in guide["steps"] if s != closing][:8], closing]
            return {
                "title": guide["title"],
                "summary": guide["summary"],
                "steps": steps,
                "tags": guide["tags"],
            }
        lines = {
            key.strip(): value.strip()
            for key, _, value in (line.partition(":") for line in user_text.splitlines())
            if key.strip() in ("Topic", "Tag")
        }
        topic, tag = lines.get("Topic", "this topic"), lines.get("Tag", "general")
        return {
            "title": f"Get started with {topic}",
            "summary": f"First answers to common {topic} questions that no guide covered yet.",
            "steps": [
                f"Open the help page of the app you use for {topic} and search for your question.",
                "Search #it-help in Slack: someone may have asked the same thing.",
                f"Still stuck? Post in #it-help, say that it is about {topic}, and what you tried.",
            ],
            "tags": [tag],
        }

    @staticmethod
    def _triage(user_text: str) -> str:
        lowered = user_text.lower()
        if any(marker in lowered for marker in _ELSE_MARKERS):
            return ABOUT_SOMEONE_ELSE
        if any(marker in lowered for marker in _OWN_MARKERS):
            return OWN_USERNAME
        return OTHER
