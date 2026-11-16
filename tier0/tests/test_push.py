"""Rung 2, the alternative: an Okta Verify push with a number challenge. It only ever
goes to the requester's own account, it is rate-limited, and it fails closed."""

from datetime import UTC, datetime, timedelta

import pytest

from tier0.audit import AuditLog
from tier0.clock import MockClock
from tier0.identity.binding import BoundIdentity
from tier0.mocks.okta_verify import MockOktaVerify
from tier0.verification.push import (
    PUSH_PRINCIPAL,
    PushChallenge,
    PushRefused,
    PushSettings,
    PushVerifier,
)

START = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
ANA = BoundIdentity("1001", "ana.diaz@meridian.example", "U0ANA", "T0MERIDIAN")
BEN = BoundIdentity("1002", "ben.okoro@meridian.example", "U0BEN", "T0MERIDIAN")


class Env:
    def __init__(self, settings: PushSettings | None = None) -> None:
        self.clock = MockClock(START)
        self.okta = MockOktaVerify(self.clock)
        self.audit = AuditLog(self.clock)
        self.push = PushVerifier(self.okta, self.clock, audit=self.audit, settings=settings)

    def start(self, identity: BoundIdentity = ANA) -> PushChallenge:
        challenge = self.push.start(identity)
        assert isinstance(challenge, PushChallenge)
        return challenge


@pytest.fixture
def env() -> Env:
    return Env()


def test_default_settings() -> None:
    settings = PushSettings()
    assert (settings.max_pushes, settings.window, settings.timeout) == (
        3,
        timedelta(minutes=15),
        timedelta(minutes=2),
    )
    with pytest.raises(ValueError, match="at least 1"):
        PushSettings(max_pushes=0)


def test_push_goes_to_the_requesters_own_account_and_the_right_number_passes(env: Env) -> None:
    challenge = env.start()
    (push,) = env.okta.pushes
    assert (push.user_id, push.principal) == (ANA.directory_id, PUSH_PRINCIPAL)
    assert challenge.subject == ANA.directory_id and 0 <= challenge.number < 100
    assert env.push.finish(ANA, challenge).reason == "not_answered"  # still waiting
    env.okta.answer(
        challenge.transaction_id, user_id=ANA.directory_id, approve=True, number=challenge.number
    )
    result = env.push.finish(ANA, challenge)
    assert (result.ok, result.reason) == (True, "ok")
    assert env.audit.entries == ()


@pytest.mark.parametrize("target", [BEN.email, BEN.directory_id, "someone@elsewhere.example", ""])
def test_a_push_aimed_at_anyone_else_is_refused_and_nothing_is_sent(env: Env, target: str) -> None:
    refused = env.push.start(ANA, requested_target=target)
    assert refused == PushRefused("target_not_requester")
    assert env.okta.pushes == ()
    (entry,) = env.audit.events("push.refused")
    # The log names the requester by ID and never the person the push was aimed at.
    assert dict(entry.details) == {
        "reason_code": "target_not_requester",
        "requester": ANA.directory_id,
    }


@pytest.mark.parametrize("target", [ANA.email, ANA.email.upper(), ANA.directory_id])
def test_naming_yourself_as_the_target_is_fine(env: Env, target: str) -> None:
    assert isinstance(env.push.start(ANA, requested_target=target), PushChallenge)
    assert [p.user_id for p in env.okta.pushes] == [ANA.directory_id]


def test_a_colleague_can_not_approve_someone_elses_push(env: Env) -> None:
    challenge = env.start()
    # Ben learns the number and tries to approve from his own phone. The push is not there.
    env.okta.answer(
        challenge.transaction_id, user_id=BEN.directory_id, approve=True, number=challenge.number
    )
    assert env.push.finish(ANA, challenge).reason == "not_answered"
    # And Ana's push can't be used as Ben's proof.
    assert env.push.finish(BEN, challenge).reason == "wrong_subject"
    assert env.push.finish(ANA, challenge).reason == "unknown_challenge"  # spent


def test_wrong_number_and_not_me_are_refusals(env: Env) -> None:
    first = env.start()
    wrong = (first.number + 1) % 100
    env.okta.answer(first.transaction_id, user_id=ANA.directory_id, approve=True, number=wrong)
    assert env.push.finish(ANA, first).reason == "rejected"
    second = env.start()
    env.okta.answer(second.transaction_id, user_id=ANA.directory_id, approve=False)
    assert env.push.finish(ANA, second).reason == "rejected"
    third = env.start()
    env.okta.answer(third.transaction_id, user_id=ANA.directory_id, approve=True)  # no number
    assert env.push.finish(ANA, third).reason == "rejected"


def test_a_push_times_out_on_the_injected_clock(env: Env) -> None:
    challenge = env.start()
    env.clock.advance(timedelta(minutes=2, seconds=1))
    env.okta.answer(
        challenge.transaction_id, user_id=ANA.directory_id, approve=True, number=challenge.number
    )
    assert env.push.finish(ANA, challenge).reason == "timed_out"
    assert env.push.finish(ANA, challenge).reason == "unknown_challenge"


def test_an_approved_push_counts_once(env: Env) -> None:
    challenge = env.start()
    env.okta.answer(
        challenge.transaction_id, user_id=ANA.directory_id, approve=True, number=challenge.number
    )
    assert env.push.finish(ANA, challenge).ok
    assert env.push.finish(ANA, challenge).reason == "unknown_challenge"


def test_a_made_up_challenge_is_unknown(env: Env) -> None:
    real = env.start()
    forged = PushChallenge(ANA.directory_id, "0" * 24, 7, START)
    assert env.push.finish(ANA, forged).reason == "unknown_challenge"
    # Changing a real challenge's number doesn't help either.
    altered = PushChallenge(real.subject, real.transaction_id, (real.number + 1) % 100, START)
    assert env.push.finish(ANA, altered).reason == "unknown_challenge"


def test_pushes_to_one_account_are_rate_limited(env: Env) -> None:
    """Push bombing: flooding a phone until its owner taps yes. The 4th push in 15
    minutes is not sent."""
    for _ in range(3):
        env.start()
        env.clock.advance(timedelta(minutes=1))
    assert env.push.start(ANA) == PushRefused("push_rate_limited")
    assert env.push.start(ANA) == PushRefused("push_rate_limited")
    assert len(env.okta.pushes) == 3
    assert isinstance(env.push.start(BEN), PushChallenge)  # per account
    assert [e.details["reason_code"] for e in env.audit.events("push.refused")] == [
        "push_rate_limited"
    ] * 2
    env.clock.advance(timedelta(minutes=12))  # the first push is now 15 minutes old
    assert isinstance(env.push.start(ANA), PushChallenge)
    assert env.push.start(ANA) == PushRefused("push_rate_limited")


def test_an_outage_fails_closed(env: Env) -> None:
    env.okta.unavailable = True
    assert env.push.start(ANA) == PushRefused("push_unavailable")
    env.okta.unavailable = False
    challenge = env.start()
    env.okta.answer(
        challenge.transaction_id, user_id=ANA.directory_id, approve=True, number=challenge.number
    )
    env.okta.unavailable = True
    assert env.push.finish(ANA, challenge).reason == "push_unavailable"
    env.okta.unavailable = False
    assert env.push.finish(ANA, challenge).reason == "unknown_challenge"  # spent, not retried


def test_the_mock_phone_times_a_push_out_by_itself(env: Env) -> None:
    sent = env.okta.send_push(ANA.directory_id, principal="test")
    env.clock.advance(timedelta(minutes=5, seconds=1))
    env.okta.answer(
        sent["transactionId"], user_id=ANA.directory_id, approve=True, number=sent["correctAnswer"]
    )
    assert env.okta.poll(sent["transactionId"], principal="test") == {"factorResult": "TIMEOUT"}
