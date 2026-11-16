"""Rung 2 of the verification ladder: a fresh SSO step-up (RFC 9470).

For a Tier B action, tier0 asks the identity provider to sign the requester in
again, now, with strong factors. RFC 9470 calls this step-up authentication: the
request carries `max_age` and `acr_values`, and the token that comes back says
when the user last signed in (`auth_time`), how strong that sign-in was (`acr`)
and which methods were used (`amr`, RFC 8176).

`StepUpVerifier.verify` checks that token in code and fails closed. It accepts a
token only when all of this holds:

- the signature, issuer and audience are right, and the token is not expired;
- it is for the requester (`sub`) and for this challenge (`nonce`, used once);
- `auth_time` is present, not in the future and no older than `max_age`;
- `acr` is one of the accepted values;
- `amr` lists every required method and no banned one.

Anything missing, malformed or unexpected is a refusal with a short reason code.
In the lab the directory ID stands in for the provider's user ID in `sub`.
"""

import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

import jwt
from cryptography.hazmat.primitives.asymmetric import ec

from tier0.clock import Clock
from tier0.identity.binding import BoundIdentity

OIDC_ISSUER = "https://sso.meridian.example"
STEP_UP_CLIENT_ID = "tier0-step-up"
SIGNING_ALGORITHM = "ES256"

# Okta's acr values for "two factors" and "phishing-resistant" sign-ins.
ACR_TWO_FACTORS = "urn:okta:loa:2fa:any"
ACR_PHISHING_RESISTANT = "phr"
ACR_PHISHING_RESISTANT_HARDWARE = "phrh"

_REQUIRED_CLAIMS = ["iss", "sub", "aud", "exp", "iat", "nonce", "auth_time", "acr", "amr"]


@dataclass(frozen=True, slots=True)
class StepUpPolicy:
    """What a step-up must prove.

    - `max_age`: the sign-in must be this fresh. 5 minutes is long enough to
      finish a sign-in and short enough that an old session does not count.
    - `acr_values`: accepted strength levels: two factors, or phishing-resistant.
    - `required_amr`: methods that must be listed. "mfa" means more than one factor.
    - `banned_amr`: methods that spoil the sign-in. "kba" is knowledge-based
      answers (security questions), which the ladder never accepts as proof.
    - `challenge_ttl`: how long a challenge waits for its answer.
    """

    max_age: timedelta = timedelta(minutes=5)
    acr_values: tuple[str, ...] = (
        ACR_TWO_FACTORS,
        ACR_PHISHING_RESISTANT,
        ACR_PHISHING_RESISTANT_HARDWARE,
    )
    required_amr: tuple[str, ...] = ("mfa",)
    banned_amr: tuple[str, ...] = ("kba",)
    challenge_ttl: timedelta = timedelta(minutes=10)

    def __post_init__(self) -> None:
        if self.max_age <= timedelta(0) or self.challenge_ttl <= timedelta(0):
            raise ValueError("max_age and challenge_ttl must be positive")
        if not self.acr_values:
            raise ValueError("at least one acr value must be accepted")


@dataclass(frozen=True, slots=True)
class StepUpChallenge:
    """What tier0 sends the requester to the identity provider with (RFC 9470,
    section 4): who must sign in, how fresh and how strong, and a one-time nonce."""

    subject: str  # the requester's directory ID
    nonce: str
    acr_values: tuple[str, ...]
    max_age: int  # seconds
    issued_at: datetime


@dataclass(frozen=True, slots=True)
class StepUpResult:
    ok: bool
    reason: str  # "ok" or a short code saying which check failed


def _refuse(reason: str) -> StepUpResult:
    return StepUpResult(False, reason)


class StepUpVerifier:
    """Issues step-up challenges and checks the ID token that answers one."""

    def __init__(
        self,
        public_key: ec.EllipticCurvePublicKey,
        clock: Clock,
        *,
        issuer: str = OIDC_ISSUER,
        client_id: str = STEP_UP_CLIENT_ID,
        policy: StepUpPolicy | None = None,
    ) -> None:
        self._public_key = public_key
        self._clock = clock
        self._issuer = issuer
        self._client_id = client_id
        self.policy = policy if policy is not None else StepUpPolicy()
        self._open: dict[str, datetime] = {}

    def challenge(self, identity: BoundIdentity) -> StepUpChallenge:
        """A new challenge for the requester's own account. There is no way to ask
        for a step-up by someone else."""
        now = self._clock.now()
        for old, issued_at in list(self._open.items()):
            if now - issued_at > self.policy.challenge_ttl:
                del self._open[old]
        nonce = secrets.token_urlsafe(24)
        self._open[nonce] = now
        return StepUpChallenge(
            subject=identity.directory_id,
            nonce=nonce,
            acr_values=self.policy.acr_values,
            max_age=int(self.policy.max_age.total_seconds()),
            issued_at=now,
        )

    def verify(
        self, id_token: str, identity: BoundIdentity, challenge: StepUpChallenge
    ) -> StepUpResult:
        """Check the token for this requester and this challenge. Fails closed.

        A challenge works once: whatever the outcome, its nonce is spent."""
        issued_at = self._open.pop(challenge.nonce, None)
        if issued_at is None:
            return _refuse("unknown_challenge")
        if self._clock.now() - issued_at > self.policy.challenge_ttl:
            return _refuse("challenge_expired")
        if challenge.subject != identity.directory_id:
            return _refuse("wrong_subject")

        # Header first, before any key is used: blocks alg=none and key confusion.
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError:
            return _refuse("malformed")
        if header.get("alg") != SIGNING_ALGORITHM:
            return _refuse("bad_header")
        # Time claims are checked below against the injected clock, not the wall clock.
        try:
            claims = jwt.decode(
                id_token,
                self._public_key,
                algorithms=[SIGNING_ALGORITHM],
                audience=self._client_id,
                issuer=self._issuer,
                leeway=0,
                options={
                    "require": _REQUIRED_CLAIMS,
                    "verify_exp": False,
                    "verify_nbf": False,
                    "verify_iat": False,
                },
            )
        except jwt.InvalidSignatureError:
            return _refuse("bad_signature")
        except jwt.InvalidAudienceError:
            return _refuse("wrong_audience")
        except jwt.InvalidIssuerError:
            return _refuse("wrong_issuer")
        except jwt.MissingRequiredClaimError:
            return _refuse("missing_claim")
        except jwt.PyJWTError:
            return _refuse("malformed")

        exp, iat, auth_time = claims["exp"], claims["iat"], claims["auth_time"]
        for value in (exp, iat, auth_time):
            # bool is an int subclass; reject it explicitly.
            if not isinstance(value, int) or isinstance(value, bool):
                return _refuse("malformed")
        now = int(self._clock.now().timestamp())
        if now >= exp:
            return _refuse("expired")
        if iat > now:
            return _refuse("not_yet_valid")

        if claims["sub"] != identity.directory_id:
            return _refuse("wrong_subject")
        nonce = claims["nonce"]
        if not isinstance(nonce, str) or not hmac.compare_digest(nonce, challenge.nonce):
            return _refuse("wrong_nonce")

        if auth_time > now:
            return _refuse("auth_time_in_future")
        if now - auth_time > int(self.policy.max_age.total_seconds()):
            return _refuse("stale_sign_in")

        if claims["acr"] not in self.policy.acr_values:
            return _refuse("acr_not_accepted")
        amr = claims["amr"]
        if not isinstance(amr, list) or not amr or not all(isinstance(m, str) for m in amr):
            return _refuse("malformed")
        if any(method in amr for method in self.policy.banned_amr):
            return _refuse("amr_not_accepted")
        if not all(method in amr for method in self.policy.required_amr):
            return _refuse("amr_not_accepted")
        return StepUpResult(True, "ok")
