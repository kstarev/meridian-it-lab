"""tier0 MCP server: token checks, strict arguments, self-only username lookup."""

from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any, TypeVar

import anyio
import httpx2
import pytest

from meridian_dataset import World
from tier0.clock import MockClock
from tier0.identity.binding import BoundIdentity
from tier0.identity.tokens import (
    ISSUER,
    MCP_RESOURCE,
    JwtVerifier,
    TokenIssuer,
)
from tier0.mcp_server.client import McpAuthError, McpToolError, open_session, serve_in_process
from tier0.mcp_server.server import (
    MCP_HOST,
    MCP_PRINCIPAL,
    TOOL_ZONES,
    USERNAME_FIELDS,
    McpServerHandle,
    build_mcp_server,
)
from tier0.mocks.directory import MockDirectory

T = TypeVar("T")


class Env:
    def __init__(self, world: World, clock: MockClock, directory: MockDirectory) -> None:
        self.world = world
        self.clock = clock
        self.directory = directory
        self.issuer = TokenIssuer(clock)
        self.handle: McpServerHandle = build_mcp_server(
            directory=directory, verifier=JwtVerifier(self.issuer.public_key, clock)
        )

    def identity(self, ticket_id: str) -> BoundIdentity:
        directory_id = self.world.answer_key.entry(ticket_id).requester_directory_id
        return self._identity(directory_id)

    def _identity(self, directory_id: str | None) -> BoundIdentity:
        person = next(p for p in self.world.people if p.directory_id == directory_id)
        return BoundIdentity(person.directory_id, person.primary_email, "U0TEST", "T0TEST")

    def leaver(self) -> BoundIdentity:
        person = next(p for p in self.world.people if p.status == "suspended")
        return self._identity(person.directory_id)

    def run(self, body: Callable[[Any], Awaitable[T]]) -> T:
        async def main() -> T:
            with anyio.fail_after(20):
                async with serve_in_process(self.handle.server) as app:
                    return await body(app)

        return anyio.run(main)

    def call(self, token: str | None, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        async def body(app: Any) -> dict[str, Any]:
            async with open_session(app, token) as session:
                return await session.call_tool("get_my_username", arguments)

        return self.run(body)


@pytest.fixture
def env(world: World, clock: MockClock, directory: MockDirectory) -> Env:
    return Env(world, clock, directory)


def test_lists_exactly_one_tool_without_user_argument(env: Env) -> None:
    async def body(app: Any) -> list[Any]:
        async with open_session(
            app, env.issuer.mint(env.identity("TKT-001"), ticket_id="T").token
        ) as s:
            return await s.list_tools()

    tools = env.run(body)
    assert [t.name for t in tools] == ["get_my_username"]
    (tool,) = tools
    assert not tool.input_schema.get("properties")
    assert tool.annotations.read_only_hint is True
    assert tool.name in TOOL_ZONES


def test_every_registered_tool_has_a_zone(env: Env) -> None:
    async def body(_app: Any) -> list[str]:
        return [t.name for t in await env.handle.server.list_tools()]

    names = env.run(body)
    assert names
    assert set(names) <= set(TOOL_ZONES)
    assert set(TOOL_ZONES.values()) <= {"public", "internal", "restricted"}


def test_valid_token_returns_own_username(env: Env) -> None:
    identity = env.identity("TKT-001")
    result = env.call(env.issuer.mint(identity, ticket_id="TKT-001").token)
    assert result == {"username": identity.email}
    (read,) = env.directory.reads
    assert read.principal == MCP_PRINCIPAL
    assert read.resolved_id == identity.directory_id
    assert read.fields == USERNAME_FIELDS


def test_lookalike_user_gets_own_address(env: Env) -> None:
    token = env.issuer.mint(env.identity("TKT-004"), ticket_id="TKT-004").token
    assert env.call(token) == {"username": "alex.kim2@meridian.example"}


def test_extra_argument_is_rejected_before_the_tool_runs(env: Env) -> None:
    token = env.issuer.mint(env.identity("TKT-004"), ticket_id="TKT-004").token
    with pytest.raises(McpToolError) as info:
        env.call(token, {"user": "alex.kim@meridian.example"})
    assert info.value.code == -32602
    assert env.directory.reads == ()


def _assert_401(env: Env, token: str | None, reason: str | None) -> None:
    with pytest.raises(McpAuthError) as info:
        env.call(token)
    assert info.value.status == 401
    assert "resource_metadata=" in (info.value.www_authenticate or "")
    assert env.directory.reads == ()
    if reason is not None:
        # The SDK may call the verifier more than once per request.
        assert env.handle.verifier.rejections
        assert set(env.handle.verifier.rejections) == {reason}


def test_no_token(env: Env) -> None:
    _assert_401(env, None, None)


def test_garbage_token(env: Env) -> None:
    _assert_401(env, "not-a-jwt", "malformed")


def test_wrong_audience(env: Env) -> None:
    token = env.issuer.mint(
        env.identity("TKT-001"), ticket_id="TKT-001", audience="https://other.example/mcp"
    ).token
    _assert_401(env, token, "wrong_audience")


def test_expired_token(env: Env) -> None:
    token = env.issuer.mint(env.identity("TKT-001"), ticket_id="TKT-001").token
    env.clock.advance(timedelta(seconds=300))
    _assert_401(env, token, "expired")


def test_token_from_another_issuer(env: Env) -> None:
    other = TokenIssuer(env.clock)
    token = other.mint(env.identity("TKT-001"), ticket_id="TKT-001").token
    _assert_401(env, token, "bad_signature")


def test_wrong_scope_is_403(env: Env) -> None:
    token = env.issuer.mint(
        env.identity("TKT-001"), ticket_id="TKT-001", scopes=("other.scope",)
    ).token
    with pytest.raises(McpAuthError) as info:
        env.call(token)
    assert info.value.status == 403
    assert env.directory.reads == ()


def test_leaver_token_is_refused(env: Env) -> None:
    # Minted directly, bypassing the binder: the tool must still fail closed.
    token = env.issuer.mint(env.leaver(), ticket_id="TKT-X").token
    with pytest.raises(McpToolError, match="suspended"):
        env.call(token)


def test_host_not_allowed(env: Env) -> None:
    token = env.issuer.mint(env.identity("TKT-001"), ticket_id="TKT-001").token

    async def body(app: Any) -> int:
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="https://evil.example",
            headers={"Authorization": f"Bearer {token}"},
        ) as http:
            response = await http.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                headers={"Accept": "application/json, text/event-stream"},
            )
            return response.status_code

    status = env.run(body)
    assert 400 <= status < 500
    assert env.directory.reads == ()


def test_protected_resource_metadata(env: Env) -> None:
    async def body(app: Any) -> dict[str, Any]:
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url=f"https://{MCP_HOST}"
        ) as http:
            response = await http.get("/.well-known/oauth-protected-resource/mcp")
            assert response.status_code == 200
            return response.json()

    metadata = env.run(body)
    assert metadata["resource"] == MCP_RESOURCE
    assert metadata["authorization_servers"] == [ISSUER]
