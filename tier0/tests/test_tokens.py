"""Token minting and verification."""

import base64
import hashlib
import hmac
import json
from datetime import timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from meridian_dataset import World
from tier0.clock import MockClock
from tier0.identity.binding import BoundIdentity
from tier0.identity.tokens import (
    AGENT_CLIENT_ID,
    ISSUER,
    MCP_RESOURCE,
    SCOPE_DIRECTORY_SELF_READ,
    JwtVerifier,
    TokenError,
    TokenIssuer,
)


@pytest.fixture
def identity(world: World) -> BoundIdentity:
    person = world.person_by_slack_id(world.tickets[0].sender_slack_user_id)
    assert person is not None
    return BoundIdentity(
        directory_id=person.directory_id,
        email=person.primary_email,
        slack_user_id=person.slack_user_id,
        slack_team_id=world.slack_team.team_id,
    )


@pytest.fixture
def issuer(clock: MockClock) -> TokenIssuer:
    return TokenIssuer(clock)


@pytest.fixture
def verifier(issuer: TokenIssuer, clock: MockClock) -> JwtVerifier:
    return JwtVerifier(issuer.public_key, clock)


def _reason(verifier: JwtVerifier, token: str) -> str:
    with pytest.raises(TokenError) as caught:
        verifier.verify(token)
    return caught.value.reason


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _claims(clock: MockClock, **overrides: Any) -> dict[str, Any]:
    now = int(clock.now().timestamp())
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "sub": "D1",
        "aud": MCP_RESOURCE,
        "iat": now,
        "nbf": now,
        "exp": now + 300,
        "jti": "j1",
        "scope": SCOPE_DIRECTORY_SELF_READ,
        "client_id": AGENT_CLIENT_ID,
        "ticket_id": "TKT-001",
    }
    claims.update(overrides)
    return {k: v for k, v in claims.items() if v is not None}


def test_mint_and_verify(
    identity: BoundIdentity, issuer: TokenIssuer, verifier: JwtVerifier
) -> None:
    issued = issuer.mint(identity, ticket_id="TKT-001")
    result = verifier.verify(issued.token)
    assert result.subject == identity.directory_id
    assert result.scopes == ("directory.self.read",)
    assert result.client_id == "tier0-agent"
    assert result.ticket_id == "TKT-001"
    assert result.audience == MCP_RESOURCE
    assert result.issuer == ISSUER
    assert result.claims["act"] == {"sub": "tier0-agent"}
    assert result.claims["exp"] - result.claims["iat"] == 300
    header = jwt.get_unverified_header(issued.token)
    assert header["typ"] == "at+jwt"
    assert header["kid"] == issuer.key_id
    assert issuer.issued == (issued,)
    issuer.reset()
    assert issuer.issued == ()


def test_issuers_have_different_keys(clock: MockClock) -> None:
    def pem(key: ec.EllipticCurvePublicKey) -> bytes:
        return key.public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )

    assert pem(TokenIssuer(clock).public_key) != pem(TokenIssuer(clock).public_key)


def test_wrong_audience(
    identity: BoundIdentity, issuer: TokenIssuer, verifier: JwtVerifier
) -> None:
    issued = issuer.mint(identity, ticket_id="TKT-001", audience="https://other.example/mcp")
    assert _reason(verifier, issued.token) == "wrong_audience"


def test_wrong_issuer(identity: BoundIdentity, issuer: TokenIssuer, clock: MockClock) -> None:
    strict = JwtVerifier(issuer.public_key, clock, issuer="https://evil.example")
    assert _reason(strict, issuer.mint(identity, ticket_id="TKT-001").token) == "wrong_issuer"


def test_expiry_uses_mock_clock(
    identity: BoundIdentity, issuer: TokenIssuer, verifier: JwtVerifier, clock: MockClock
) -> None:
    token = issuer.mint(identity, ticket_id="TKT-001").token
    clock.advance(timedelta(seconds=299))
    verifier.verify(token)
    clock.advance(timedelta(seconds=1))
    assert _reason(verifier, token) == "expired"


def test_other_issuers_token(
    identity: BoundIdentity, verifier: JwtVerifier, clock: MockClock
) -> None:
    token = TokenIssuer(clock).mint(identity, ticket_id="TKT-001").token
    assert _reason(verifier, token) == "bad_signature"


def test_tampered_payload(
    identity: BoundIdentity, issuer: TokenIssuer, verifier: JwtVerifier
) -> None:
    head, payload, sig = issuer.mint(identity, ticket_id="TKT-001").token.split(".")
    data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    data["sub"] = "someone-else"
    forged = ".".join([head, _b64(json.dumps(data).encode()), sig])
    assert _reason(verifier, forged) in {"bad_signature", "malformed"}


def test_alg_none(clock: MockClock, verifier: JwtVerifier) -> None:
    head = _b64(json.dumps({"alg": "none", "typ": "at+jwt"}).encode())
    body = _b64(json.dumps(_claims(clock)).encode())
    assert _reason(verifier, f"{head}.{body}.") == "bad_header"


def test_hs256_key_confusion(issuer: TokenIssuer, clock: MockClock, verifier: JwtVerifier) -> None:
    pem = issuer.public_key.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    head = _b64(json.dumps({"alg": "HS256", "typ": "at+jwt"}).encode())
    body = _b64(json.dumps(_claims(clock)).encode())
    mac = hmac.new(pem, f"{head}.{body}".encode(), hashlib.sha256).digest()
    assert _reason(verifier, f"{head}.{body}.{_b64(mac)}") == "bad_header"


def _custom(
    clock: MockClock, claims: dict[str, Any], typ: str = "at+jwt"
) -> tuple[str, JwtVerifier]:
    """Sign claims with a throwaway key and return a verifier that trusts that key."""
    key = ec.generate_private_key(ec.SECP256R1())
    token = jwt.encode(claims, key, algorithm="ES256", headers={"typ": typ})
    return token, JwtVerifier(key.public_key(), clock)


def test_wrong_typ(clock: MockClock) -> None:
    token, verifier = _custom(clock, _claims(clock), typ="JWT")
    assert _reason(verifier, token) == "bad_header"


def test_no_typ_header(clock: MockClock) -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    token = jwt.encode(_claims(clock), key, algorithm="ES256", headers={"typ": None})
    assert "typ" not in jwt.get_unverified_header(token)
    assert _reason(JwtVerifier(key.public_key(), clock), token) == "bad_header"


def test_missing_scope(clock: MockClock) -> None:
    claims = _claims(clock)
    del claims["scope"]
    token, verifier = _custom(clock, claims)
    assert _reason(verifier, token) == "missing_claim"


def test_missing_ticket_id(clock: MockClock) -> None:
    claims = _claims(clock)
    del claims["ticket_id"]
    token, verifier = _custom(clock, claims)
    assert _reason(verifier, token) == "missing_claim"


def test_not_yet_valid(clock: MockClock) -> None:
    now = int(clock.now().timestamp())
    token, verifier = _custom(clock, _claims(clock, nbf=now + 60))
    assert _reason(verifier, token) == "not_yet_valid"


def test_non_integer_exp(clock: MockClock) -> None:
    token, verifier = _custom(clock, _claims(clock, exp="soon"))
    assert _reason(verifier, token) in {"malformed", "missing_claim"}


def test_mint_rejects_bad_input(identity: BoundIdentity, issuer: TokenIssuer) -> None:
    with pytest.raises(ValueError, match="ttl"):
        issuer.mint(identity, ticket_id="TKT-001", ttl=timedelta(minutes=6))
    with pytest.raises(ValueError, match="ttl"):
        issuer.mint(identity, ticket_id="TKT-001", ttl=timedelta(0))
    with pytest.raises(ValueError, match="scopes"):
        issuer.mint(identity, ticket_id="TKT-001", scopes=())
    with pytest.raises(ValueError, match="ticket_id"):
        issuer.mint(identity, ticket_id="")
    assert issuer.issued == ()


def test_garbage(verifier: JwtVerifier) -> None:
    assert _reason(verifier, "not-a-token") == "malformed"
