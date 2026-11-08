"""Mock OAuth 2.1 authorization server (TokenIssuer) and resource-server check (JwtVerifier).

Tokens are short-lived JWT access tokens (RFC 9068) bound to one user, one
ticket and one audience (the MCP server, RFC 8707).
"""

import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import ec

from tier0.clock import Clock, format_time
from tier0.identity.binding import BoundIdentity

ISSUER = "https://auth.tier0.meridian.example"
# The audience: the RFC 8707 resource indicator of the MCP server.
MCP_RESOURCE = "https://mcp.tier0.meridian.example/mcp"
SCOPE_DIRECTORY_SELF_READ = "directory.self.read"
AGENT_CLIENT_ID = "tier0-agent"
TOKEN_TTL = timedelta(minutes=5)
TOKEN_TYPE = "at+jwt"  # noqa: S105 - RFC 9068 media type, not a secret
ALGORITHM = "ES256"

_REQUIRED_CLAIMS = ["iss", "sub", "aud", "exp", "iat", "nbf", "jti", "scope", "client_id"]


class TokenError(Exception):
    """The token was rejected. `reason` is a short machine-readable code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class IssuedToken:
    """What the issuer hands out, plus the facts we log about it."""

    token: str
    jti: str
    subject: str
    ticket_id: str
    scopes: tuple[str, ...]
    audience: str
    issued_at: str
    expires_at: str


@dataclass(frozen=True, slots=True)
class VerifiedToken:
    """Claims the resource server may rely on after every check passed."""

    subject: str
    scopes: tuple[str, ...]
    client_id: str
    jti: str
    ticket_id: str
    audience: str
    issuer: str
    expires_at: int
    claims: dict[str, Any]


class TokenIssuer:
    """Mints tokens with a private key that exists only in this object.

    The key is generated fresh per instance and never written anywhere. The MCP
    server only ever gets the public key, so it can check tokens but never mint
    them.
    """

    def __init__(self, clock: Clock, *, issuer: str = ISSUER) -> None:
        self._clock = clock
        self._issuer = issuer
        self._private_key = ec.generate_private_key(ec.SECP256R1())
        self._key_id = secrets.token_hex(8)
        self._issued: list[IssuedToken] = []

    @property
    def public_key(self) -> ec.EllipticCurvePublicKey:
        return self._private_key.public_key()

    @property
    def key_id(self) -> str:
        return self._key_id

    @property
    def issuer(self) -> str:
        return self._issuer

    @property
    def issued(self) -> tuple[IssuedToken, ...]:
        return tuple(self._issued)

    def reset(self) -> None:
        """Clear the issued log only; the key stays."""
        self._issued.clear()

    def mint(
        self,
        identity: BoundIdentity,
        *,
        ticket_id: str,
        scopes: Sequence[str] = (SCOPE_DIRECTORY_SELF_READ,),
        audience: str = MCP_RESOURCE,
        ttl: timedelta = TOKEN_TTL,
    ) -> IssuedToken:
        if ttl <= timedelta(0) or ttl > TOKEN_TTL:
            raise ValueError("ttl must be positive and at most 5 minutes")
        if not scopes:
            raise ValueError("scopes must not be empty")
        if not ticket_id:
            raise ValueError("ticket_id must not be empty")

        now = self._clock.now()
        issued_at = int(now.timestamp())
        expires_at = issued_at + int(ttl.total_seconds())
        jti = secrets.token_hex(16)
        claims: dict[str, Any] = {
            "iss": self._issuer,
            "sub": identity.directory_id,
            "aud": audience,
            "iat": issued_at,
            "nbf": issued_at,
            "exp": expires_at,
            "jti": jti,
            "scope": " ".join(scopes),
            "client_id": AGENT_CLIENT_ID,
            # RFC 8693: the agent acts for the user with its own identity.
            "act": {"sub": AGENT_CLIENT_ID},
            "ticket_id": ticket_id,
        }
        token = jwt.encode(
            claims,
            self._private_key,
            algorithm=ALGORITHM,
            headers={"typ": TOKEN_TYPE, "kid": self._key_id},
        )
        record = IssuedToken(
            token=token,
            jti=jti,
            subject=identity.directory_id,
            ticket_id=ticket_id,
            scopes=tuple(scopes),
            audience=audience,
            issued_at=format_time(now),
            expires_at=format_time(now + timedelta(seconds=expires_at - issued_at)),
        )
        self._issued.append(record)
        return record


class JwtVerifier:
    """Resource-server side: holds only the public key and fails closed."""

    def __init__(
        self,
        public_key: ec.EllipticCurvePublicKey,
        clock: Clock,
        *,
        issuer: str = ISSUER,
        audience: str = MCP_RESOURCE,
    ) -> None:
        self._public_key = public_key
        self._clock = clock
        self._issuer = issuer
        self._audience = audience

    def verify(self, token: str) -> VerifiedToken:
        # Header first, before any key is used: blocks alg=none and HS256 key confusion.
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as error:
            raise TokenError("malformed") from error
        if header.get("alg") != ALGORITHM or header.get("typ") != TOKEN_TYPE:
            raise TokenError("bad_header")

        # Time claims are checked below against the mock clock, not the wall clock.
        try:
            claims = jwt.decode(
                token,
                self._public_key,
                algorithms=[ALGORITHM],
                audience=self._audience,
                issuer=self._issuer,
                leeway=0,
                options={
                    "require": _REQUIRED_CLAIMS,
                    "verify_exp": False,
                    "verify_nbf": False,
                    "verify_iat": False,
                },
            )
        except jwt.InvalidSignatureError as error:
            raise TokenError("bad_signature") from error
        except jwt.InvalidAudienceError as error:
            raise TokenError("wrong_audience") from error
        except jwt.InvalidIssuerError as error:
            raise TokenError("wrong_issuer") from error
        except jwt.MissingRequiredClaimError as error:
            raise TokenError("missing_claim") from error
        except jwt.PyJWTError as error:
            raise TokenError("malformed") from error

        exp, nbf, iat = claims.get("exp"), claims.get("nbf"), claims.get("iat")
        # bool is an int subclass; reject it explicitly.
        for value in (exp, nbf, iat):
            if not isinstance(value, int) or isinstance(value, bool):
                raise TokenError("malformed")
        now = int(self._clock.now().timestamp())
        if now >= exp:
            raise TokenError("expired")
        if nbf > now or iat > now:
            raise TokenError("not_yet_valid")

        scope = claims.get("scope")
        if not isinstance(scope, str) or not scope.split():
            raise TokenError("missing_claim")
        for name in ("sub", "jti", "client_id", "ticket_id"):
            value = claims.get(name)
            if not isinstance(value, str) or not value:
                raise TokenError("missing_claim")

        return VerifiedToken(
            subject=claims["sub"],
            scopes=tuple(scope.split()),
            client_id=claims["client_id"],
            jti=claims["jti"],
            ticket_id=claims["ticket_id"],
            audience=self._audience,
            issuer=claims["iss"],
            expires_at=exp,
            claims=claims,
        )
