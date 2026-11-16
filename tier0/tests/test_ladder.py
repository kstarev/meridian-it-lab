"""The verification ladder: the policy, rung 1's context checks, the "never accepted as
proof" rule, and the wiring into the own-username flow."""

import itertools
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

import anyio
import pytest

from meridian_dataset import World
from meridian_dataset.model import Person, Ticket
from tier0 import replies
from tier0.agent import Outcome
from tier0.clock import MockClock, parse_time
from tier0.evals.ladder import identity_of
from tier0.identity.binding import BoundIdentity, Unbound
from tier0.mocks.calendar import MockCalendar
from tier0.mocks.device_assurance import MockDeviceAssurance
from tier0.mocks.oidc import MockOidcProvider
from tier0.mocks.okta_verify import MockOktaVerify
from tier0.mocks.security_events import MockSecurityEvents
from tier0.policy import POLICY
from tier0.runtime import Runtime, offline_runtime
from tier0.tickets.adapter import AWAITING_REPLY, QUEUE_SERVICE_DESK, TRIAGE
from tier0.verification.context import (
    LADDER_PRINCIPAL,
    SECURITY_CHANGE_EVENTS,
    ContextChecks,
    ContextSettings,
)
from tier0.verification.ladder import (
    AUTOMATIC_TIERS,
    NEVER_PROOF,
    REQUIRED_RUNG,
    RUNG_NAMES,
    TIER_C_HUMAN_STEPS,
    LadderDecision,
    OidcStepUp,
    PushApproval,
    Rung,
    VerificationLadder,
    required_rung,
)
from tier0.verification.push import PushChallenge, PushVerifier
from tier0.verification.step_up import StepUpVerifier

OWN_REQUEST = "what's my username? i can't find it."
ACTION = "own_username"


class Env:
    """The ladder with its mocks, for one employee of the world."""

    def __init__(self, world: World, settings: ContextSettings | None = None) -> None:
        self.world = world
        self.clock = MockClock(parse_time(world.reference_time))
        self.calendar = MockCalendar(self.clock)
        self.devices = MockDeviceAssurance(self.clock)
        self.security_events = MockSecurityEvents(self.clock)
        self.oidc = MockOidcProvider(self.clock)
        self.step_up = StepUpVerifier(self.oidc.public_key, self.clock)
        self.okta = MockOktaVerify(self.clock)
        self.push = PushVerifier(self.okta, self.clock)
        self.context = ContextChecks(
            clock=self.clock,
            calendar=self.calendar,
            devices=self.devices,
            security_events=self.security_events,
            settings=settings,
        )
        self.ladder = VerificationLadder(context=self.context, step_up=self.step_up, push=self.push)
        self.person = next(p for p in world.people if p.status == "active")
        self.other = next(p for p in world.people if p.status == "active" and p is not self.person)
        self.identity = identity_of(world, self.person)

    def fresh_step_up(self) -> OidcStepUp:
        challenge = self.step_up.challenge(self.identity)
        token = self.oidc.sign_in(self.identity.directory_id, nonce=challenge.nonce)
        return OidcStepUp(token, challenge)

    def approved_push(self) -> PushApproval:
        challenge = self.push.start(self.identity)
        assert isinstance(challenge, PushChallenge)
        self.okta.answer(
            challenge.transaction_id,
            user_id=self.identity.directory_id,
            approve=True,
            number=challenge.number,
        )
        return PushApproval(challenge)

    def out_of_office(self) -> None:
        now = self.clock.now()
        self.calendar.set_out_of_office(
            self.person.primary_email, start=now - timedelta(hours=2), end=now + timedelta(days=3)
        )


@pytest.fixture
def env(world: World) -> Env:
    return Env(world)


# --- the policy ----------------------------------------------------------------------


def test_each_tier_needs_its_rung() -> None:
    assert dict(REQUIRED_RUNG) == {"A": Rung.CONTEXT, "B": Rung.STEP_UP, "C": Rung.HUMAN}
    assert [int(r) for r in Rung] == [0, 1, 2, 3]
    assert set(RUNG_NAMES) == set(Rung)
    assert frozenset({"A", "B"}) == AUTOMATIC_TIERS
    # An unknown tier is treated like Tier C: a human only.
    assert required_rung("D") == required_rung("") == required_rung("a") == Rung.HUMAN


def test_every_tier_in_the_tool_policy_has_a_rung() -> None:
    assert {rule.tier for rule in POLICY.values()} <= set(REQUIRED_RUNG)


def test_the_policy_can_not_be_changed_at_run_time() -> None:
    with pytest.raises(TypeError):
        REQUIRED_RUNG["C"] = Rung.CONTEXT  # type: ignore[index]


def test_tier_c_human_steps_are_the_briefs() -> None:
    text = " ".join(TIER_C_HUMAN_STEPS)
    assert "ID" in text and "number on file" in text and "manager" in text


def test_tier_a_passes_with_nothing_unusual(env: Env) -> None:
    decision = env.ladder.evaluate("A", env.identity, action=ACTION)
    assert decision.verdict == (True, None, "ok")
    assert decision.findings == () and decision.claims_ignored == ()


# --- rung 0 --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reason",
    ["guest_user", "suspended", "deactivated_slack_user", "external_user", "not_in_directory"],
)
@pytest.mark.parametrize("tier", ["A", "B", "C"])
def test_rung_0_stops_anyone_the_binder_refused(env: Env, tier: str, reason: str) -> None:
    unbound = Unbound(reason=reason, slack_user_id="U0X", slack_team_id="T0X")
    decision = env.ladder.evaluate(tier, unbound, action=ACTION)
    assert decision.verdict == (False, Rung.SLACK_IDENTITY, reason)
    # Nothing else was even looked at.
    assert env.calendar.reads == () and env.devices.reads == ()
    assert env.security_events.reads == ()


def test_rung_0_stops_anything_that_is_not_a_bound_identity(env: Env) -> None:
    for junk in (None, "U0ANA", {"directory_id": env.identity.directory_id}):
        decision = env.ladder.evaluate("A", junk, action=ACTION)  # type: ignore[arg-type]
        assert decision.verdict == (False, Rung.SLACK_IDENTITY, "not_verified")


# --- rung 1: context checks ----------------------------------------------------------


def test_the_mocks_start_with_nothing_unusual(env: Env) -> None:
    result = env.context.check(env.identity, action=ACTION)
    assert result.ok and result.reason == "ok" and result.findings == ()
    # One read per source, under the ladder's own principal, about the requester only.
    assert [(r.principal, r.calendar_id) for r in env.calendar.reads] == [
        (LADDER_PRINCIPAL, env.person.primary_email)
    ]
    assert [(r.principal, r.user_id) for r in env.devices.reads] == [
        (LADDER_PRINCIPAL, env.person.directory_id)
    ]
    assert [(r.principal, r.user_id) for r in env.security_events.reads] == [
        (LADDER_PRINCIPAL, env.person.directory_id)
    ]


def test_an_out_of_office_requester_is_stopped_at_rung_1(env: Env) -> None:
    env.out_of_office()
    decision = env.ladder.evaluate("A", env.identity, action=ACTION)
    assert decision.verdict == (False, Rung.CONTEXT, "out_of_office")


def test_out_of_office_only_counts_while_it_lasts(env: Env) -> None:
    now = env.clock.now()
    email = env.person.primary_email
    env.calendar.set_out_of_office(
        email, start=now - timedelta(days=9), end=now - timedelta(days=2)
    )
    env.calendar.set_out_of_office(
        email, start=now + timedelta(hours=3), end=now + timedelta(days=1)
    )
    assert env.ladder.evaluate("A", env.identity, action=ACTION).allowed
    env.clock.advance(timedelta(hours=3))
    assert env.ladder.evaluate("A", env.identity, action=ACTION).reason == "out_of_office"
    env.clock.advance(timedelta(days=1))
    assert env.ladder.evaluate("A", env.identity, action=ACTION).allowed


def test_someone_elses_out_of_office_changes_nothing(env: Env) -> None:
    now = env.clock.now()
    env.calendar.set_out_of_office(
        env.other.primary_email, start=now - timedelta(hours=1), end=now + timedelta(hours=1)
    )
    assert env.ladder.evaluate("A", env.identity, action=ACTION).allowed


@pytest.mark.parametrize("event_type", sorted(SECURITY_CHANGE_EVENTS))
def test_a_recent_security_change_is_stopped_at_rung_1(env: Env, event_type: str) -> None:
    env.security_events.add_event(
        env.person.directory_id, event_type, at=env.clock.now() - timedelta(hours=23)
    )
    decision = env.ladder.evaluate("A", env.identity, action=ACTION)
    assert decision.verdict == (False, Rung.CONTEXT, "recent_security_change")


def test_old_changes_other_peoples_changes_and_plain_sign_ins_do_not_count(env: Env) -> None:
    now = env.clock.now()
    env.security_events.add_event(
        env.person.directory_id, "user.mfa.factor.activate", at=now - timedelta(hours=25)
    )
    env.security_events.add_event(env.other.directory_id, "user.account.reset_password")
    env.security_events.add_event(env.person.directory_id, "user.session.start")
    assert env.ladder.evaluate("A", env.identity, action=ACTION).allowed


@pytest.mark.parametrize(
    ("managed", "compliant", "reason"),
    [(False, True, "unmanaged_device"), (True, False, "noncompliant_device")],
)
def test_an_unmanaged_or_failing_device_is_stopped_at_rung_1(
    env: Env, managed: bool, compliant: bool, reason: str
) -> None:
    env.devices.set_session_device(env.person.directory_id, managed=managed, compliant=compliant)
    decision = env.ladder.evaluate("A", env.identity, action=ACTION)
    assert decision.verdict == (False, Rung.CONTEXT, reason)


def test_same_day_repeats_are_stopped_from_the_fourth(env: Env) -> None:
    assert ContextSettings().same_day_limit == 3
    assert ContextSettings().same_day_window == timedelta(hours=24)
    verdicts = []
    for _ in range(6):
        verdicts.append(env.ladder.evaluate("A", env.identity, action=ACTION).reason)
        env.clock.advance(timedelta(hours=2))
    assert verdicts == ["ok", "ok", "ok", "same_day_repeat", "same_day_repeat", "same_day_repeat"]
    # A different account is not affected, and neither is a different action.
    assert env.ladder.evaluate("A", identity_of(env.world, env.other), action=ACTION).allowed
    assert env.ladder.evaluate("A", env.identity, action="another_action").allowed


def test_the_repeat_window_rolls_past_midnight(env: Env) -> None:
    """Three requests late in the day and one just after midnight are still four in a
    day: the window is the last 24 hours, not the calendar date."""
    env.clock.advance_to(parse_time("2026-11-02T23:30:00.000Z"))
    for _ in range(3):
        assert env.ladder.evaluate("A", env.identity, action=ACTION).allowed
        env.clock.advance(timedelta(minutes=5))
    env.clock.advance_to(parse_time("2026-11-03T00:05:00.000Z"))
    assert env.ladder.evaluate("A", env.identity, action=ACTION).reason == "same_day_repeat"
    # Tries that were stopped count too, so asking again keeps the window full.
    env.clock.advance(timedelta(hours=23, minutes=15))
    assert env.ladder.evaluate("A", env.identity, action=ACTION).reason == "same_day_repeat"
    env.clock.advance(timedelta(hours=24, minutes=1))
    assert env.ladder.evaluate("A", env.identity, action=ACTION).allowed


def test_every_finding_is_listed_for_the_person_who_picks_it_up(env: Env) -> None:
    env.out_of_office()
    env.security_events.add_event(env.person.directory_id, "security.threat.detected")
    env.devices.set_session_device(env.person.directory_id, managed=False)
    decision = env.ladder.evaluate("A", env.identity, action=ACTION)
    assert decision.verdict == (False, Rung.CONTEXT, "out_of_office")
    assert decision.findings == ("out_of_office", "recent_security_change", "unmanaged_device")


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        ("calendar", "calendar_unavailable"),
        ("security_events", "security_events_unavailable"),
        ("devices", "device_unavailable"),
    ],
)
def test_a_source_that_can_not_answer_fails_closed(env: Env, source: str, reason: str) -> None:
    getattr(env, source).unavailable = True
    decision = env.ladder.evaluate("A", env.identity, action=ACTION)
    assert decision.verdict == (False, Rung.CONTEXT, reason)


def test_an_answer_the_code_can_not_read_fails_closed(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        env.calendar, "events_list", lambda *a, **k: {"items": [{"start": "sometime"}]}
    )
    monkeypatch.setattr(env.devices, "session_device", lambda *a, **k: {})
    monkeypatch.setattr(env.security_events, "logs", lambda *a, **k: [{"eventType": 7}])
    decision = env.ladder.evaluate("A", env.identity, action=ACTION)
    assert decision.findings == (
        "calendar_unavailable",
        "security_events_unavailable",
        "unmanaged_device",
    )


def test_context_settings_must_be_positive() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        ContextSettings(same_day_limit=0)
    with pytest.raises(ValueError, match="positive"):
        ContextSettings(security_lookback=timedelta(0))


# --- rung 2: Tier B ------------------------------------------------------------------


def test_tier_b_without_a_step_up_is_stopped_at_rung_2(env: Env) -> None:
    decision = env.ladder.evaluate("B", env.identity, action="tier_b_action")
    assert decision.verdict == (False, Rung.STEP_UP, "step_up_required")


def test_tier_b_passes_with_a_fresh_step_up_or_an_approved_push(env: Env) -> None:
    with_token = env.ladder.evaluate(
        "B", env.identity, action="tier_b_action", step_up=env.fresh_step_up()
    )
    with_push = env.ladder.evaluate(
        "B", env.identity, action="tier_b_action", step_up=env.approved_push()
    )
    assert with_token.verdict == with_push.verdict == (True, None, "ok")


def test_tier_b_fails_closed_on_weak_or_borrowed_evidence(world: World) -> None:
    # Many tries in a row, so rung 1's same-day check is set out of the way.
    env = Env(world, ContextSettings(same_day_limit=100))

    def verdict(evidence: Any) -> tuple[bool, Rung | None, str]:
        return env.ladder.evaluate(
            "B", env.identity, action="tier_b_action", step_up=evidence
        ).verdict

    challenge = env.step_up.challenge(env.identity)
    stale = env.oidc.sign_in(
        env.identity.directory_id,
        nonce=challenge.nonce,
        auth_time=env.clock.now() - timedelta(hours=1),
    )
    assert verdict(OidcStepUp(stale, challenge)) == (False, Rung.STEP_UP, "stale_sign_in")

    # A colleague's valid step-up is not the requester's.
    colleague = identity_of(env.world, env.other)
    theirs = env.step_up.challenge(colleague)
    token = env.oidc.sign_in(colleague.directory_id, nonce=theirs.nonce)
    assert verdict(OidcStepUp(token, theirs)) == (False, Rung.STEP_UP, "wrong_subject")

    # A push nobody answered, and a push a colleague got and approved.
    waiting = env.push.start(env.identity)
    assert isinstance(waiting, PushChallenge)
    assert verdict(PushApproval(waiting)) == (False, Rung.STEP_UP, "not_answered")
    other_push = env.push.start(colleague)
    assert isinstance(other_push, PushChallenge)
    env.okta.answer(
        other_push.transaction_id,
        user_id=colleague.directory_id,
        approve=True,
        number=other_push.number,
    )
    assert verdict(PushApproval(other_push)) == (False, Rung.STEP_UP, "wrong_subject")
    assert verdict("trust me") == (False, Rung.STEP_UP, "step_up_required")


def test_tier_b_without_a_verifier_fails_closed(env: Env) -> None:
    bare = VerificationLadder(context=env.context)
    for evidence in (env.fresh_step_up(), env.approved_push()):
        decision = bare.evaluate("B", env.identity, action="tier_b_action", step_up=evidence)
        assert decision.verdict == (False, Rung.STEP_UP, "step_up_unavailable")


def test_rung_1_comes_before_rung_2(env: Env) -> None:
    env.out_of_office()
    decision = env.ladder.evaluate(
        "B", env.identity, action="tier_b_action", step_up=env.fresh_step_up()
    )
    assert decision.verdict == (False, Rung.CONTEXT, "out_of_office")


# --- rung 3: Tier C is never allowed -------------------------------------------------


@pytest.mark.parametrize("tier", ["C", "D", "", "c", "admin"])
def test_tier_c_and_unknown_tiers_are_never_allowed(env: Env, tier: str) -> None:
    """Whatever is offered: a fresh step-up, an approved push, every claimed proof."""
    claimed = dict.fromkeys(NEVER_PROOF, "right")
    for evidence in (None, env.fresh_step_up(), env.approved_push()):
        decision = env.ladder.evaluate(
            tier, env.identity, action="mfa_reset", step_up=evidence, claimed=claimed
        )
        assert decision.verdict == (False, Rung.HUMAN, "human_only")


def test_a_decision_that_allows_tier_c_can_not_even_be_built() -> None:
    for tier in ("C", "D", ""):
        with pytest.raises(ValueError, match="only Tier A and B"):
            LadderDecision(tier, True, None, "ok")
    with pytest.raises(ValueError, match="only Tier A and B"):
        LadderDecision("A", True, Rung.CONTEXT, "ok")  # allowed, yet a rung failed
    with pytest.raises(ValueError, match="which rung"):
        LadderDecision("A", False, None, "no")


# --- never accepted as proof ---------------------------------------------------------


def test_the_never_proof_list_is_the_briefs() -> None:
    assert (
        frozenset(
            {
                "date_of_birth",
                "manager_name",
                "employee_id",
                "username",
                "security_answers",
                "phone_number",
            }
        )
        == NEVER_PROOF
    )


def _subsets(names: frozenset[str]) -> list[dict[str, str]]:
    ordered = sorted(names)
    return [
        dict.fromkeys(combo, "the right value")
        for size in range(len(ordered) + 1)
        for combo in itertools.combinations(ordered, size)
    ]


@pytest.mark.parametrize("tier", ["A", "B", "C"])
@pytest.mark.parametrize(
    "situation", ["nothing_unusual", "out_of_office", "unmanaged_device", "guest"]
)
def test_claimed_proof_changes_nothing(world: World, tier: str, situation: str) -> None:
    """Every mix of the six kinds of claimed proof (64 of them) gives the same verdict as
    offering nothing, in every situation and for every tier."""

    def verdict(claimed: dict[str, str] | None) -> tuple[bool, Rung | None, str]:
        env = Env(world)
        who: BoundIdentity | Unbound = env.identity
        if situation == "out_of_office":
            env.out_of_office()
        elif situation == "unmanaged_device":
            env.devices.set_session_device(env.person.directory_id, managed=False)
        elif situation == "guest":
            who = Unbound("guest_user", env.person.slack_user_id, world.slack_team.team_id)
        return env.ladder.evaluate(tier, who, action=ACTION, claimed=claimed).verdict

    baseline = verdict(None)
    assert baseline[0] is (tier == "A" and situation == "nothing_unusual")
    for claimed in _subsets(NEVER_PROOF):
        assert verdict(claimed) == baseline


def test_claims_are_listed_by_name_only(env: Env) -> None:
    decision = env.ladder.evaluate(
        "A",
        env.identity,
        action=ACTION,
        claimed={"date_of_birth": "1990-01-01", "phone_number": "+49 30 1234", "badge": "42"},
    )
    assert decision.allowed
    assert decision.claims_ignored == ("date_of_birth", "other", "phone_number")
    assert "1990" not in repr(decision) and "1234" not in repr(decision)


# --- wired into the own-username flow ------------------------------------------------


def _run[T](world: World, body: Callable[[Runtime], Awaitable[T]], **options: Any) -> T:
    async def main() -> T:
        with anyio.fail_after(30):
            async with offline_runtime(world, **options) as rt:
                return await body(rt)

    return anyio.run(main)


def _requester(world: World) -> Person:
    return next(p for p in world.people if p.status == "active")


async def _ask(rt: Runtime, person: Person, number: int = 1, text: str = OWN_REQUEST) -> Outcome:
    ticket = Ticket(
        id=f"LAD-{number}",
        channel=rt.slack.dm_channel_for(person.slack_user_id),
        sender_slack_user_id=person.slack_user_id,
        sender_team_id=rt.world.slack_team.team_id,
        text=text,
        received_at=rt.world.reference_time,
    )
    return await rt.agent.handle(rt.slack.message_event(ticket))


def _ladder_events(rt: Runtime) -> list[dict[str, Any]]:
    return [
        dict(event.attributes or {})
        for span in rt.tracer.finished_spans()
        for event in span.events
        if event.name == "tier0.ladder"
    ]


def _is_handoff(text: str) -> bool:
    return any(v in text for variants in replies.TEMPLATES["handoff"].values() for v in variants)


def test_an_ordinary_request_passes_the_ladder_and_is_answered(world: World) -> None:
    person = _requester(world)

    async def body(rt: Runtime) -> tuple[Runtime, Outcome]:
        return rt, await _ask(rt, person)

    rt, outcome = _run(world, body)
    assert (outcome.action, outcome.state) == ("answer", AWAITING_REPLY)
    assert _ladder_events(rt) == [{"tier": "A", "allowed": True, "rung": 1, "reason": "ok"}]
    # The ladder reads its own three sources and never the directory.
    assert len(rt.calendar.reads) == len(rt.devices.reads) == len(rt.security_events.reads) == 1
    assert all(r.principal != LADDER_PRINCIPAL for r in rt.directory.reads)


NEEDLE_SETUPS: dict[str, Callable[[Runtime, Person], None]] = {
    "out_of_office": lambda rt, p: rt.calendar.set_out_of_office(
        p.primary_email,
        start=rt.clock.now() - timedelta(hours=1),
        end=rt.clock.now() + timedelta(days=2),
    ),
    "recent_security_change": lambda rt, p: rt.security_events.add_event(
        p.directory_id, "user.mfa.factor.activate"
    ),
    "unmanaged_device": lambda rt, p: rt.devices.set_session_device(p.directory_id, managed=False),
    "noncompliant_device": lambda rt, p: rt.devices.set_session_device(
        p.directory_id, managed=True, compliant=False
    ),
    "calendar_unavailable": lambda rt, p: setattr(rt.calendar, "unavailable", True),
}

# Everything a caller might offer as proof, in one message.
CLAIMS = (
    " my date of birth is 1990-01-01, my employee id is 48213, my boss is the head "
    "of finance, my security answer is blue, call me back on +49 30 1234567."
)


@pytest.mark.parametrize("reason", sorted(NEEDLE_SETUPS))
@pytest.mark.parametrize("claims", ["", CLAIMS], ids=["plain", "with_claimed_proof"])
def test_a_rung_1_finding_hands_the_request_to_a_person_before_anything_is_done(
    world: World, reason: str, claims: str
) -> None:
    person = _requester(world)

    async def body(rt: Runtime) -> tuple[Runtime, Outcome]:
        NEEDLE_SETUPS[reason](rt, person)
        return rt, await _ask(rt, person, text=OWN_REQUEST + claims)

    rt, outcome = _run(world, body)
    assert (outcome.action, outcome.category, outcome.state) == ("escalate", ACTION, TRIAGE)
    assert _ladder_events(rt) == [{"tier": "A", "allowed": False, "rung": 1, "reason": reason}]
    # Stopped before the second checks: one model call (triage), no token, no tool call,
    # no directory lookup beyond binding the sender.
    assert rt.issuer.issued == ()
    assert {r.principal for r in rt.directory.reads} == {"tier0-gateway"}
    ticket = rt.tickets.get_ticket(outcome.ticket or "")
    assert (ticket.state, ticket.queue) == (TRIAGE, QUEUE_SERVICE_DESK)
    bodies = [c.body for c in ticket.comments]
    assert any("verification ladder stopped at rung 1" in b and reason in b for b in bodies)
    assert any(f"ladder_{reason}" in b for b in bodies)
    # The requester gets the normal hand-off and learns nothing about why.
    (reply,) = rt.slack.sent_messages
    assert _is_handoff(reply.text)
    assert reason not in reply.text and person.primary_email not in reply.text


def test_the_fourth_request_in_a_day_goes_to_a_person(world: World) -> None:
    person = _requester(world)

    async def body(rt: Runtime) -> tuple[Runtime, list[Outcome]]:
        outcomes = []
        for number in range(1, 6):
            outcomes.append(await _ask(rt, person, number))
            rt.clock.advance(timedelta(hours=3))
        return rt, outcomes

    rt, outcomes = _run(world, body)
    assert [o.action for o in outcomes] == ["answer"] * 3 + ["escalate"] * 2
    assert len(rt.issuer.issued) == 3
    assert [e["reason"] for e in _ladder_events(rt)] == ["ok"] * 3 + ["same_day_repeat"] * 2


def test_a_guest_never_reaches_the_ladder(world: World) -> None:
    """Rung 0 is the identity binder, as it has been since milestone 1."""
    person = _requester(world)

    async def body(rt: Runtime) -> tuple[Runtime, Outcome, Outcome]:
        rt.slack.mark_guest(person.slack_user_id)
        first = await _ask(rt, person, 1)
        rt.slack.mark_guest(person.slack_user_id, single_channel=True)
        return rt, first, await _ask(rt, person, 2)

    rt, first, second = _run(world, body)
    for outcome in (first, second):
        assert (outcome.action, outcome.reason) == ("verify_identity", "guest_user")
    assert _ladder_events(rt) == [] and rt.calendar.reads == () and rt.issuer.issued == ()


def test_a_policy_that_calls_the_answer_tier_b_or_c_hands_off(world: World) -> None:
    """No Tier B or C action exists. If the own-username rule were ever moved up a tier,
    the ladder would stop it instead of answering."""
    person = _requester(world)
    rule = POLICY[ACTION]
    for tier, rung, reason in (("B", 2, "step_up_required"), ("C", 3, "human_only")):
        rows = {
            **POLICY,
            ACTION: type(rule)(ACTION, tier, "answer", rule.allowed_tools, rule.scopes),
        }

        async def body(rt: Runtime) -> tuple[Runtime, Outcome]:
            return rt, await _ask(rt, person)

        rt, outcome = _run(world, body, policy=rows)
        assert (outcome.action, outcome.state) == ("escalate", TRIAGE)
        assert _ladder_events(rt) == [
            {"tier": tier, "allowed": False, "rung": rung, "reason": reason}
        ]
        assert rt.issuer.issued == ()


def test_without_a_ladder_the_agent_behaves_as_before(world: World) -> None:
    person = _requester(world)

    async def body(rt: Runtime) -> tuple[Runtime, Outcome]:
        rt.calendar.unavailable = True
        return rt, await _ask(rt, person)

    rt, outcome = _run(world, body, ladder=None)
    assert outcome.action == "answer" and _ladder_events(rt) == []
