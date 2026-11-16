"""Mock OIDC provider for step-up sign-ins (RFC 9470), shaped like Okta's ID tokens.

It signs ID tokens with a key made fresh per instance, so nothing is shared
between runs and nothing is stored. tier0 only ever gets the public key.
Tests use `claims` to build tokens that are stale, weak or broken on purpose.
"""

import secrets
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import ec

from tier0.clock import Clock
from tier0.verification.step_up import (
    ACR_TWO_FACTORS,
    OIDC_ISSUER,
    SIGNING_ALGORITHM,
    STEP_UP_CLIENT_ID,
)

ID_TOKEN_TTL = timedelta(minutes=5)
# Passed in `claims` to leave a claim out of the token.
MISSING = object()


class MockOidcProvider:
    def __init__(self, clock: Clock, *, issuer: str = OIDC_ISSUER) -> None:
        self._clock = clock
        self._issuer = issuer
        self._private_key = ec.generate_private_key(ec.SECP256R1())
        self._key_id = secrets.token_hex(8)

    @property
    def public_key(self) -> ec.EllipticCurvePublicKey:
        return self._private_key.public_key()

    def sign_in(
        self,
        subject: str,
        *,
        nonce: str,
        acr: str = ACR_TWO_FACTORS,
        amr: Sequence[str] = ("pwd", "swk", "mfa"),
        auth_time: datetime | None = None,
        client_id: str = STEP_UP_CLIENT_ID,
        claims: Mapping[str, Any] | None = None,
    ) -> str:
        """The ID token for a sign-in by `subject`. By default the sign-in happens
        now, with two factors. `claims` replaces claims, or removes them (MISSING)."""
        now = self._clock.now()
        signed_in = auth_time if auth_time is not None else now
        payload: dict[str, Any] = {
            "iss": self._issuer,
            "sub": subject,
            "aud": client_id,
            "iat": int(now.timestamp()),
            "exp": int((now + ID_TOKEN_TTL).timestamp()),
            "auth_time": int(signed_in.timestamp()),
            "nonce": nonce,
            "acr": acr,
            "amr": list(amr),
        }
        for name, value in (claims or {}).items():
            if value is MISSING:
                payload.pop(name, None)
            else:
                payload[name] = value
        return jwt.encode(
            payload,
            self._private_key,
            algorithm=SIGNING_ALGORITHM,
            headers={"kid": self._key_id},
        )
