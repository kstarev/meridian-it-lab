"""Rung 2: the SSO step-up check (RFC 9470) fails closed on anything but a fresh,
strong sign-in by the requester, for this challenge."""

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest

from tier0.clock import MockClock
from tier0.identity.binding import BoundIdentity
from tier0.mocks.oidc import MISSING, MockOidcProvider
from tier0.verification.step_up import (
    ACR_PHISHING_RESISTANT,
    ACR_TWO_FACTORS,
    OIDC_ISSUER,
    STEP_UP_CLIENT_ID,
    StepUpPolicy,
    StepUpVerifier,
)

START = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
ANA = BoundIdentity("1001", "ana.diaz@meridian.example", "U0ANA", "T0MERIDIAN")
BEN = BoundIdentity("1002", "ben.okoro@meridian.example", "U0BEN", "T0MERIDIAN")


class Env:
    def __init__(self, policy: StepUpPolicy | None = None) -> None:
        self.clock = MockClock(START)
        self.provider = MockOidcProvider(self.clock)
        self.verifier = StepUpVerifier(self.provider.public_key, self.clock, policy=policy)

    def check(self, identity: BoundIdentity = ANA, **sign_in: Any) -> str:
        """Challenge `identity`, sign in at the provider, verify. Returns the reason."""
        challenge = self.verifier.challenge(identity)
        subject = sign_in.pop("subject", identity.directory_id)
        nonce = sign_in.pop("nonce", challenge.nonce)
        token = self.provider.sign_in(subject, nonce=nonce, **sign_in)
        return self.verifier.verify(token, identity, challenge).reason


@pytest.fixture
def env() -> Env:
    return Env()


def test_default_policy_is_five_minutes_and_two_factors() -> None:
    policy = StepUpPolicy()
    assert policy.max_age == timedelta(minutes=5)
    assert ACR_TWO_FACTORS in policy.acr_values and ACR_PHISHING_RESISTANT in policy.acr_values
    assert policy.required_amr == ("mfa",) and policy.banned_amr == ("kba",)


def test_challenge_carries_the_rfc_9470_parameters(env: Env) -> None:
    challenge = env.verifier.challenge(ANA)
    assert challenge.subject == ANA.directory_id
    assert challenge.max_age == 300
    assert challenge.acr_values == StepUpPolicy().acr_values
    assert len(challenge.nonce) >= 24
    assert env.verifier.challenge(ANA).nonce != challenge.nonce


def test_a_fresh_two_factor_sign_in_by_the_requester_passes(env: Env) -> None:
    challenge = env.verifier.challenge(ANA)
    token = env.provider.sign_in(ANA.directory_id, nonce=challenge.nonce)
    result = env.verifier.verify(token, ANA, challenge)
    assert (result.ok, result.reason) == (True, "ok")


def test_a_sign_in_at_the_edge_of_max_age_passes_and_one_second_older_fails(env: Env) -> None:
    edge = START - timedelta(minutes=5)
    assert env.check(auth_time=edge) == "ok"
    assert env.check(auth_time=edge - timedelta(seconds=1)) == "stale_sign_in"


@pytest.mark.parametrize(
    ("sign_in", "reason"),
    [
        ({"auth_time": START - timedelta(hours=2)}, "stale_sign_in"),
        ({"auth_time": START + timedelta(seconds=30)}, "auth_time_in_future"),
        ({"claims": {"auth_time": MISSING}}, "missing_claim"),
        ({"claims": {"auth_time": "1794819600"}}, "malformed"),
        ({"claims": {"auth_time": True}}, "malformed"),
        ({"acr": "urn:okta:loa:1fa:any"}, "acr_not_accepted"),
        ({"acr": ""}, "acr_not_accepted"),
        ({"claims": {"acr": MISSING}}, "missing_claim"),
        ({"amr": ("pwd",)}, "amr_not_accepted"),
        ({"amr": ("pwd", "kba", "mfa")}, "amr_not_accepted"),
        ({"amr": ()}, "malformed"),
        ({"claims": {"amr": "mfa"}}, "malformed"),
        ({"claims": {"amr": ["mfa", 7]}}, "malformed"),
        ({"claims": {"amr": MISSING}}, "missing_claim"),
        ({"subject": BEN.directory_id}, "wrong_subject"),
        ({"nonce": "someone-elses-nonce"}, "wrong_nonce"),
        ({"claims": {"nonce": MISSING}}, "missing_claim"),
        ({"client_id": "another-app"}, "wrong_audience"),
        ({"claims": {"iss": "https://sso.evil.example"}}, "wrong_issuer"),
        ({"claims": {"exp": int(START.timestamp())}}, "expired"),
        ({"claims": {"iat": int(START.timestamp()) + 60}}, "not_yet_valid"),
        ({"claims": {"exp": "tomorrow"}}, "malformed"),
    ],
    ids=[
        "old_sign_in",
        "sign_in_from_the_future",
        "no_auth_time",
        "auth_time_as_text",
        "auth_time_as_bool",
        "one_factor_acr",
        "empty_acr",
        "no_acr",
        "password_only",
        "security_questions",
        "empty_amr",
        "amr_not_a_list",
        "amr_with_a_number",
        "no_amr",
        "someone_elses_sign_in",
        "wrong_nonce",
        "no_nonce",
        "token_for_another_app",
        "wrong_issuer",
        "expired",
        "issued_in_the_future",
        "exp_as_text",
    ],
)
def test_anything_else_is_refused(env: Env, sign_in: dict[str, Any], reason: str) -> None:
    assert env.check(**sign_in) == reason


def test_a_token_signed_by_another_key_is_refused(env: Env) -> None:
    other = MockOidcProvider(env.clock)
    challenge = env.verifier.challenge(ANA)
    token = other.sign_in(ANA.directory_id, nonce=challenge.nonce)
    assert env.verifier.verify(token, ANA, challenge).reason == "bad_signature"


def test_unsigned_and_wrong_algorithm_tokens_are_refused_before_any_key_is_used(
    env: Env,
) -> None:
    now = int(START.timestamp())

    def claims(nonce: str) -> dict[str, Any]:
        return {
            "iss": OIDC_ISSUER,
            "sub": ANA.directory_id,
            "aud": STEP_UP_CLIENT_ID,
            "iat": now,
            "exp": now + 300,
            "auth_time": now,
            "nonce": nonce,
            "acr": ACR_TWO_FACTORS,
            "amr": ["pwd", "mfa"],
        }

    challenge = env.verifier.challenge(ANA)
    unsigned = jwt.encode(claims(challenge.nonce), key=None, algorithm="none")  # type: ignore[arg-type]
    assert env.verifier.verify(unsigned, ANA, challenge).reason == "bad_header"
    challenge = env.verifier.challenge(ANA)
    shared = jwt.encode(claims(challenge.nonce), "a-shared-secret-of-32-bytes-or-more", "HS256")
    assert env.verifier.verify(shared, ANA, challenge).reason == "bad_header"
    challenge = env.verifier.challenge(ANA)
    assert env.verifier.verify("not a token", ANA, challenge).reason == "malformed"


def test_a_challenge_works_once(env: Env) -> None:
    challenge = env.verifier.challenge(ANA)
    token = env.provider.sign_in(ANA.directory_id, nonce=challenge.nonce)
    assert env.verifier.verify(token, ANA, challenge).ok
    assert env.verifier.verify(token, ANA, challenge).reason == "unknown_challenge"


def test_a_failed_try_also_spends_the_challenge(env: Env) -> None:
    challenge = env.verifier.challenge(ANA)
    assert env.verifier.verify("not a token", ANA, challenge).reason == "malformed"
    good = env.provider.sign_in(ANA.directory_id, nonce=challenge.nonce)
    assert env.verifier.verify(good, ANA, challenge).reason == "unknown_challenge"


def test_a_challenge_for_one_person_can_not_be_answered_for_another(env: Env) -> None:
    challenge = env.verifier.challenge(ANA)
    token = env.provider.sign_in(BEN.directory_id, nonce=challenge.nonce)
    assert env.verifier.verify(token, BEN, challenge).reason == "wrong_subject"


def test_a_challenge_expires(env: Env) -> None:
    challenge = env.verifier.challenge(ANA)
    env.clock.advance(timedelta(minutes=11))
    token = env.provider.sign_in(ANA.directory_id, nonce=challenge.nonce)
    assert env.verifier.verify(token, ANA, challenge).reason == "challenge_expired"


def test_a_challenge_from_another_verifier_is_unknown(env: Env) -> None:
    challenge = Env().verifier.challenge(ANA)
    token = env.provider.sign_in(ANA.directory_id, nonce=challenge.nonce)
    assert env.verifier.verify(token, ANA, challenge).reason == "unknown_challenge"


def test_the_clock_is_the_injected_one(env: Env) -> None:
    """A sign-in that is fresh now is stale six minutes later on the mock clock."""
    challenge = env.verifier.challenge(ANA)
    token = env.provider.sign_in(ANA.directory_id, nonce=challenge.nonce)
    env.clock.advance(timedelta(minutes=6))
    assert env.verifier.verify(token, ANA, challenge).reason == "expired"
    challenge = env.verifier.challenge(ANA)
    old = env.provider.sign_in(
        ANA.directory_id, nonce=challenge.nonce, auth_time=START
    )  # issued now, but the sign-in itself was six minutes ago
    assert env.verifier.verify(old, ANA, challenge).reason == "stale_sign_in"


def test_a_stricter_policy_is_honoured() -> None:
    env = Env(StepUpPolicy(max_age=timedelta(seconds=30), acr_values=(ACR_PHISHING_RESISTANT,)))
    assert env.check() == "acr_not_accepted"
    assert env.check(acr=ACR_PHISHING_RESISTANT) == "ok"
    assert (
        env.check(acr=ACR_PHISHING_RESISTANT, auth_time=START - timedelta(seconds=31))
        == "stale_sign_in"
    )
    with pytest.raises(ValueError, match="positive"):
        StepUpPolicy(max_age=timedelta(0))
    with pytest.raises(ValueError, match="acr"):
        StepUpPolicy(acr_values=())
