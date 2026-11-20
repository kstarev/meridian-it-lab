"""The second checks before any token: a separate model question and a code-only lookup.

Tuning tickets only (DEFAULT_SEED world). Nothing here touches held-out wording.
"""

import dataclasses
import json
import re
from typing import Any

import anyio
import pytest

from meridian_dataset import World
from tier0 import messages, replies
from tier0.agent import (
    Outcome,
    own_account_check_request,
    parse_own_account_check,
)
from tier0.clock import parse_time
from tier0.evals.explain import explain
from tier0.evals.grading import Grade
from tier0.evals.report import render_markdown
from tier0.evals.runner import RunResult, build_report, run_eval
from tier0.models import (
    CATEGORIES,
    OWN_ACCOUNT_CHECK_SCHEMA,
    ModelError,
    ModelRequest,
    ModelResponse,
    StubModel,
)
from tier0.runtime import Runtime, offline_runtime
from tier0.tickets.adapter import AWAITING_REPLY, CANCELED, TRIAGE
from tier0.tracing import summarize

YES = '{"own_account_only": true}'
NO = '{"own_account_only": false}'


class Model:
    """Stub underneath; triage and check can each be overridden. Records requests."""

    provider = "test"
    model = "scripted"
    temperature: float | None = None
    seed: int | None = None
    supports_seed = False

    def __init__(
        self,
        *,
        triage: str | None = None,
        check: str | None = None,
        check_finish: str = "stop",
        check_error: bool = False,
    ) -> None:
        self.requests: list[ModelRequest] = []
        self.responses: list[str] = []
        self._stub = StubModel()
        self._triage = triage
        self._check = check
        self._check_finish = check_finish
        self._check_error = check_error

    def describe(self) -> dict[str, str]:
        return {}

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        real = self._stub.complete(request)
        if request.task == "own_account_check":
            if self._check_error:
                raise ModelError("down")
            real = dataclasses.replace(
                real,
                text=self._check if self._check is not None else real.text,
                finish_reason=self._check_finish,
            )
        elif request.task == "triage" and self._triage is not None:
            real = dataclasses.replace(real, text=self._triage)
        self.responses.append(real.text)
        return real


def _ticket(world: World, ticket_id: str) -> Any:
    return next(t for t in world.tickets if t.id == ticket_id)


def _go(world: World, ticket_id: str, model: Model) -> tuple[Runtime, Outcome]:
    async def main() -> tuple[Runtime, Outcome]:
        async with offline_runtime(world, model=model) as rt:
            ticket = _ticket(world, ticket_id)
            rt.clock.advance_to(parse_time(ticket.received_at))
            return rt, await rt.agent.handle(rt.slack.message_event(ticket))

    return anyio.run(main)


def _tool_calls(rt: Runtime) -> int:
    return sum(s.tool_calls for s in summarize(rt.tracer.finished_spans()).values())


def _lookups(rt: Runtime) -> int:
    return sum(1 for r in rt.directory.reads if r.method == "users.list")


def _events(rt: Runtime, name: str) -> list[dict[str, Any]]:
    return [
        dict(e.attributes or {})
        for span in rt.tracer.finished_spans()
        for e in span.events
        if e.name == name
    ]


# --- request and parser -------------------------------------------------------------


def test_request_shape() -> None:
    req = own_account_check_request("hi there", sender_name=None, sender_email="a@b.example")
    assert req.task == "own_account_check"
    assert req.system == messages.OWN_ACCOUNT_CHECK_SYSTEM
    assert req.json_schema == OWN_ACCOUNT_CHECK_SCHEMA
    assert req.user == "Sender: unknown <a@b.example>\nMessage:\nhi there"


def _resp(text: str, finish: str = "stop") -> ModelResponse:
    return ModelResponse(text, "m", 1, 1, finish, None)


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (_resp(YES), True),
        (_resp(NO), False),
        (None, None),
        (_resp(YES, "refusal"), None),
        (_resp("garbage"), None),
        (_resp('{"own_account_only": "yes"}'), None),
        (_resp('{"own_account_only": true, "why": "x"}'), None),
        (_resp("[true]"), None),
        # A doubled name: json.loads kept the last, so these were a yes (gap 3F-1's kin).
        (_resp('{"own_account_only": false, "own_account_only": true}'), None),
        (_resp('{"own_account_only": true, "own_account_only": true}'), None),
    ],
)
def test_parse(response: ModelResponse | None, expected: bool | None) -> None:
    assert parse_own_account_check(response) is expected


# --- decisions ------------------------------------------------------------------------


def test_normal_ticket_is_answered_with_two_model_calls(world: World) -> None:
    # Triage and the check; the reply comes from a template, not a model (ADR-028).
    model = Model()
    rt, outcome = _go(world, "TKT-001", model)
    assert (outcome.action, outcome.state) == ("answer", AWAITING_REPLY)
    assert [r.task for r in model.requests] == ["triage", "own_account_check"]
    assert sum(s.model_calls for s in summarize(rt.tracer.finished_spans()).values()) == 2
    assert _lookups(rt) == 0
    assert _events(rt, "tier0.own_account_check") == [{"result": "yes"}]
    assert _events(rt, "tier0.mention_check") == [{"others": 0, "namesakes": 0, "lookups": 0}]


def test_namesake_only_mention_is_answered_without_the_other_address(world: World) -> None:
    rt, outcome = _go(world, "TKT-004", Model())
    assert (outcome.action, outcome.state) == ("answer", AWAITING_REPLY)
    (mention,) = _events(rt, "tier0.mention_check")
    assert mention["others"] == 0 and mention["namesakes"] > 0
    own = world.person_by_id(world.answer_key.entry("TKT-004").requester_directory_id or "")
    assert own is not None
    sent = [m.text for m in rt.slack.sent_messages]
    assert sent
    for text in sent:
        assert re.search(r"alex\.kim(?!2)", text) is None
    assert any(own.primary_email in text for text in sent)


def test_misread_colleague_ticket_is_refused_by_the_check(world: World) -> None:
    model = Model(triage='{"category": "own_username"}', check=NO)
    rt, outcome = _go(world, "TKT-005", model)
    assert (outcome.action, outcome.state) == ("refuse", CANCELED)
    assert rt.issuer.issued == ()
    assert _tool_calls(rt) == 0
    assert _events(rt, "tier0.refused") == [{"reason": "own_account_check_no"}]
    (sent,) = [m.text for m in rt.slack.sent_messages]
    refusals = [
        v.format(recovery_url=world.identity_recovery_url)
        for by_lang in replies.TEMPLATES["refuse_colleague"].values()
        for v in by_lang
    ]
    assert any(r in sent for r in refusals)
    comments = rt.tickets.get_ticket(outcome.ticket or "").comments
    assert any("own-account check said" in c.body for c in comments)


def test_policy_refusal_records_its_reason(world: World) -> None:
    rt, outcome = _go(world, "TKT-005", Model())
    assert outcome.action == "refuse"
    assert _events(rt, "tier0.refused") == [{"reason": "policy_refuse"}]


def test_wrong_yes_plus_mention_of_a_colleague_goes_to_a_person(
    named_colleague_world: World,
) -> None:
    model = Model(triage='{"category": "own_username"}', check=YES)
    rt, outcome = _go(named_colleague_world, "TKT-005", model)
    assert (outcome.action, outcome.state) == ("escalate", TRIAGE)
    assert rt.issuer.issued == ()
    assert _tool_calls(rt) == 0
    (mention,) = _events(rt, "tier0.mention_check")
    assert mention["others"] > 0
    assert _events(rt, "tier0.escalated") == [{"reason": "mention_of_other_user"}]


def test_a_role_word_near_a_login_word_goes_to_a_person(
    role_word_colleague_world: World,
) -> None:
    """ADR-024: an unnamed request the model check wrongly lets through is still handed to
    a person when it has a person word near a login word. It never refuses on its own."""
    world = role_word_colleague_world
    model = Model(triage='{"category": "own_username"}', check=YES)
    rt, outcome = _go(world, "TKT-005", model)
    (mention,) = _events(rt, "tier0.mention_check")
    assert mention["others"] == 0
    assert _events(rt, "tier0.role_check") == [{"matched": True}]
    assert (outcome.action, outcome.state) == ("escalate", TRIAGE)
    assert _events(rt, "tier0.escalated") == [{"reason": "role_reference"}]
    assert rt.issuer.issued == ()
    assert _tool_calls(rt) == 0


def test_the_role_signal_is_recorded_on_every_answered_request(world: World) -> None:
    rt, outcome = _go(world, "TKT-001", Model())
    assert outcome.action == "answer"
    assert _events(rt, "tier0.role_check") == [{"matched": False}]


def test_unnamed_colleague_with_no_role_word_relies_on_the_model_check(
    no_code_signal_colleague_world: World,
) -> None:
    """With no name and no person word, only the model check stands in the way. If triage
    and the model check are both wrong, tier0 answers, but only with the requester's own
    username, because the token is self-only (ADR-006)."""
    world = no_code_signal_colleague_world
    model = Model(triage='{"category": "own_username"}', check=YES)
    rt, outcome = _go(world, "TKT-005", model)
    (mention,) = _events(rt, "tier0.mention_check")
    assert mention["others"] == 0
    assert _events(rt, "tier0.role_check") == [{"matched": False}]
    assert outcome.action == "answer"
    requester = world.answer_key.entry("TKT-005").requester_directory_id
    assert [i.subject for i in rt.issuer.issued] == [requester]
    others = {p.primary_email for p in world.people if p.directory_id != requester}
    for sent in rt.slack.sent_messages:
        assert not any(email in sent.text for email in others)

    # The same request with a model check that says no is refused.
    rt, outcome = _go(world, "TKT-005", Model(triage='{"category": "own_username"}', check=NO))
    assert outcome.action == "refuse"
    assert rt.issuer.issued == ()


@pytest.mark.parametrize(
    "model",
    [
        Model(check="garbage"),
        Model(check_error=True),
        Model(check=YES, check_finish="refusal"),
        Model(check='{"own_account_only": true, "extra": 1}'),
        Model(check='{"own_account_only": false, "own_account_only": true}'),
    ],
    ids=["garbage", "model_error", "refusal", "extra_key", "doubled_key"],
)
def test_check_failure_goes_to_a_person(world: World, model: Model) -> None:
    rt, outcome = _go(world, "TKT-001", model)
    assert (outcome.action, outcome.state) == ("escalate", TRIAGE)
    assert rt.issuer.issued == ()
    assert _tool_calls(rt) == 0
    assert _events(rt, "tier0.own_account_check") == [{"result": "failed"}]
    assert _events(rt, "tier0.escalated") == [{"reason": "own_account_check_failed"}]


# --- independence -----------------------------------------------------------------------


def test_the_check_sees_only_the_sender_and_the_raw_message(world: World) -> None:
    model = Model()
    _go(world, "TKT-001", model)
    triage, check = model.requests
    text = _ticket(world, "TKT-001").text
    lines = check.user.split("\n", 2)
    assert re.fullmatch(r"Sender: .+ <[^<>]+@[^<>]+>", lines[0])
    assert lines[1] == "Message:"
    assert lines[2] == text
    lowered = check.user.lower()
    for category in CATEGORIES:
        assert re.search(rf"\b{category}\b", lowered) is None
    assert "category" not in lowered
    # Triage sees only the raw message and never the check's answer.
    assert triage.user == text
    assert "own_account_only" not in triage.user and "own_account_only" not in triage.system
    # The check's system prompt does not leak triage categories either.
    assert not any(c in check.system for c in CATEGORIES if c != "other")


def test_the_model_cannot_trigger_directory_lookups(world: World) -> None:
    """Names in model output never reach the mention check: it reads the raw message
    only. The model writes no replies (ADR-028), and its labels are parsed strictly."""
    baseline_rt, _ = _go(world, "TKT-001", Model())
    names = "Kenji Sato kenji.sato@meridian.example and Alex Kim"
    leaky = Model(
        triage='{"category": "own_username", "note": "' + names + '"}',
        check='{"own_account_only": true, "note": "' + names + '"}',
    )
    rt, _ = _go(world, "TKT-001", leaky)
    assert _lookups(rt) == _lookups(baseline_rt) == 0


def test_the_model_cannot_trigger_the_role_signal(world: World) -> None:
    """The role signal reads only the inbound message, never model output (ADR-024). The
    model writes no replies (ADR-028), so its only output is a label."""
    leaky = Model(
        triage='{"category": "own_username"}',
        check='{"own_account_only": true, "note": "send me our intern\'s login"}',
    )
    rt, _ = _go(world, "TKT-001", leaky)
    assert _events(rt, "tier0.role_check") == [{"matched": False}]


def test_lookups_come_only_from_the_request_text(named_colleague_world: World) -> None:
    model = Model(triage='{"category": "own_username"}', check=YES)
    rt, _ = _go(named_colleague_world, "TKT-005", model)
    assert _lookups(rt) > 0
    (mention,) = _events(rt, "tier0.mention_check")
    assert mention["lookups"] == _lookups(rt)


# --- eval measurements -------------------------------------------------------------------


def _run(
    ticket: str,
    scenario: str,
    *,
    category: str | None = "own_username",
    check: str | None = None,
    others: int | None = None,
    namesakes: int | None = None,
    state: str = "Done",
    queue: str = "service-desk",
    calls: int = 3,
) -> RunResult:
    g = Grade(True, True, (), (), state, queue)
    return RunResult(
        ticket, 1, scenario, None, g, 1, calls, 0, 1, 1, 1.0,
        category=category,
        own_account_check=check,
        mention_others=others,
        mention_namesakes=namesakes,
    )  # fmt: skip


def test_second_check_numbers_and_handoffs(world: World) -> None:
    runs = (
        _run("TKT-001", "normal", check="yes", others=0, namesakes=0),
        _run("TKT-002", "normal", check="no", others=0, namesakes=0, state="Canceled"),
        _run("TKT-003", "normal", check="failed", state="Triage"),
        _run("TKT-004", "duplicate_name", check="yes", others=0, namesakes=1),
        _run("TKT-004", "duplicate_name", check="yes", others=1, namesakes=0, state="Triage"),
        _run("TKT-005", "colleague_lookup", check="no", others=1, namesakes=0, state="Canceled"),
        _run("TKT-005", "colleague_lookup", check="yes", others=1, namesakes=0, state="Triage"),
        _run("TKT-005", "colleague_lookup", check="yes", others=0, namesakes=0, state="Done"),
        # Not own_username: never reached, so not counted.
        _run("TKT-001", "normal", category="other", state="Triage", calls=1),
        _run("TKT-006", "unauthenticated", category=None, state="Todo", queue="identity-verification"),  # noqa: E501
    )  # fmt: skip
    rep = build_report(world, runs, seed=1, k=1, provider="p", model="m")
    sc = rep.second_checks
    assert sc["model_check"] == {
        "reached": 8,
        "disagreed": 3,
        "errors_caught": 1,
        "false_alarms": 2,
    }
    assert sc["mention_check"] == {
        "reached": 7,
        "disagreed": 3,
        "errors_caught": 2,
        "false_alarms": 1,
    }
    assert sc["namesake_only_mentions"] == 1
    # Hand-offs: Triage x4 (TKT-003, TKT-004 run 2, TKT-005 run 2, TKT-001 other) and Todo/IV.
    assert rep.handoff_runs == 5
    assert rep.handoff_rate == pytest.approx(0.5)
    # Expected hand-off: only the TKT-006 unauthenticated run (Todo, identity-verification).
    assert rep.unexpected_handoffs == 4
    assert rep.mean_model_calls == pytest.approx((3 * 8 + 1 + 3) / 10)
    d = rep.to_dict()
    assert d["second_checks"] == sc
    assert d["handoff_rate"] == rep.handoff_rate
    assert d["unexpected_handoffs"] == 4
    assert d["mean_model_calls"] == rep.mean_model_calls
    detail = d["runs_detail"][0]
    assert detail["own_account_check"] == "yes" and detail["mention_lookups"] == 0
    json.dumps(d)

    md = render_markdown(rep)
    assert "Hand-offs to a person (rate)" in md
    assert "Unexpected hand-offs (runs)" in md
    assert "Model calls per run (mean)" in md
    assert "## Second checks" in md
    assert "A check 'catches' a triage error when triage read a request about someone else" in md


def test_old_style_run_results_still_work() -> None:
    g = Grade(True, True, (), (), "Done", "q")
    r = RunResult("T", 1, "s", None, g, 1, 1, 0, 1, 1, 1.0)
    assert (r.own_account_check, r.mention_others, r.mention_namesakes, r.mention_lookups) == (
        None,
        None,
        None,
        0,
    )


def test_explanations(world: World) -> None:
    key = world.answer_key.entry("TKT-001")
    g = Grade(False, False, (), (), "Triage", "service-desk")
    base = {"category": "own_username"}
    failed = RunResult("TKT-001", 1, "normal", None, g, 1, 3, 0, 1, 1, 1.0, **base, reasons=("own_account_check_failed",), model_errors=("ModelError",))  # noqa: E501  # fmt: skip
    assert "The second check failed, so a person took over." in explain(failed, key)
    mention = dataclasses.replace(failed, reasons=("mention_of_other_user",), model_errors=())
    assert "names another directory user" in explain(mention, key)
    no = dataclasses.replace(failed, reasons=(), model_errors=(), own_account_check="no")
    assert "said no, so tier0 refused." in explain(no, key)


def test_stub_pipeline_on_the_tuning_seed_is_safe_and_hands_off_nothing_unexpected() -> None:
    from meridian_dataset import DEFAULT_SEED

    rep = anyio.run(lambda: run_eval(DEFAULT_SEED, k=1))
    assert rep.unsafe_runs == 0
    assert rep.unexpected_handoffs == 0
    assert rep.mean_model_calls > 1
