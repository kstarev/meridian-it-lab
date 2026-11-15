"""The MCP server: token check, safety controls, argument check, then one self-only tool.

The kill switch and the rate limit (ADR-008) are enforced here, before any tool
runs, so they hold whatever the agent or the model does.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS, ToolAnnotations

from tier0.controls import (
    ACTIONS_STOPPED,
    ACTIONS_STOPPED_MESSAGE,
    KIND_TOOL_CALL,
    RATE_LIMITED,
    RATE_LIMITED_MESSAGE,
    Controls,
)
from tier0.directory_api import DirectoryApi, DirectoryError
from tier0.identity.tokens import (
    ISSUER,
    MCP_RESOURCE,
    SCOPE_DIRECTORY_SELF_READ,
    JwtVerifier,
    TokenError,
)

MCP_HOST = "mcp.tier0.meridian.example"
MCP_PRINCIPAL = "tier0-mcp"
USERNAME_FIELDS = ("primaryEmail", "suspended", "archived")

# Data-classification zone of each tool's output: public / internal / restricted.
TOOL_ZONES: dict[str, str] = {"get_my_username": "internal"}


class SdkTokenVerifier:
    """Adapts JwtVerifier to the SDK's TokenVerifier protocol. Never raises.

    Every rejection reason is appended to `rejections` so tests (and later
    audit logging) can see why a token failed; the client only sees a 401.
    """

    def __init__(self, verifier: JwtVerifier) -> None:
        self._verifier = verifier
        self.rejections: list[str] = []

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            v = self._verifier.verify(token)
        except TokenError as error:
            self.rejections.append(error.reason)
            return None
        return AccessToken(
            token=token,
            client_id=v.client_id,
            scopes=list(v.scopes),
            # Expiry was already checked by JwtVerifier against the injected clock. The SDK
            # would compare it with the wall clock and reject tokens from the mock timeline.
            expires_at=None,
            resource=v.audience,
            subject=v.subject,
            claims=v.claims,
        )


def reject_undeclared_arguments(
    server: MCPServer,
) -> Callable[[Any, Callable[[Any], Awaitable[Any]]], Awaitable[Any]]:
    """Middleware: refuse tool calls that carry arguments the tool does not declare.

    The security mapping says the server validates every tool argument. The SDK
    would silently drop extras, which hides an attempt to ask about someone
    else, so we fail the call with INVALID_PARAMS before the tool runs.
    """

    async def middleware(ctx: Any, call_next: Callable[[Any], Awaitable[Any]]) -> Any:
        if ctx.method == "tools/call":
            params = ctx.params or {}
            tools = {tool.name: tool for tool in await server.list_tools()}
            tool = tools.get(params.get("name"))
            if tool is not None:
                arguments = params.get("arguments") or {}
                declared = set((tool.input_schema or {}).get("properties", {}))
                extra = set(arguments) - declared
                if extra:
                    raise MCPError(INVALID_PARAMS, f"unexpected arguments: {sorted(extra)}")
        return await call_next(ctx)

    return middleware


def enforce_controls(
    server: MCPServer, controls: Controls
) -> Callable[[Any, Callable[[Any], Awaitable[Any]]], Awaitable[Any]]:
    """Middleware: the kill switch and the rate limit, checked before any tool runs.

    - Kill switch on: every tool call is refused.
    - Rate limit: each tool call is counted against the token's subject and against
      everyone together. The call over a limit is refused and not counted.

    Both refusals are short fixed phrases with no user data, and both are written to
    the audit log. A call with no usable subject is refused too (fail closed). Listing
    the tools is not an action and is left alone.
    """

    async def middleware(ctx: Any, call_next: Callable[[Any], Awaitable[Any]]) -> Any:
        if ctx.method != "tools/call":
            return await call_next(ctx)
        if controls.kill_switch.is_on:
            # Only a registered name is logged: the name comes from the caller.
            name = (ctx.params or {}).get("name")
            known = {tool.name for tool in await server.list_tools()}
            controls.audit.record(
                "kill_switch.refused_call",
                actor=MCP_PRINCIPAL,
                tool=name if isinstance(name, str) and name in known else "unknown",
            )
            raise MCPError(ACTIONS_STOPPED, ACTIONS_STOPPED_MESSAGE)
        token = get_access_token()
        subject = token.subject if token is not None else None
        if not subject:
            raise MCPError(RATE_LIMITED, RATE_LIMITED_MESSAGE)
        decision = controls.rate_limiter.acquire(subject, kind=KIND_TOOL_CALL, source=MCP_PRINCIPAL)
        if not decision.allowed:
            raise MCPError(RATE_LIMITED, RATE_LIMITED_MESSAGE)
        return await call_next(ctx)

    return middleware


class ToolRefused(ToolError):
    """Raised inside a tool; the SDK turns it into an is_error result.

    It must be a ToolError: the SDK hides the text of any other exception.
    Messages are short fixed phrases with no user data.
    """


@dataclass(frozen=True, slots=True)
class McpServerHandle:
    """The server plus its SdkTokenVerifier, so tests can read `rejections`."""

    server: MCPServer
    verifier: SdkTokenVerifier


def build_mcp_server(
    *, directory: DirectoryApi, verifier: JwtVerifier, controls: Controls | None = None
) -> McpServerHandle:
    """Build the server. `controls` turns on the kill switch and the rate limit
    (ADR-008). The runtime always passes them; a bare server without them exists only
    so the token and argument checks can be tested alone."""
    sdk_verifier = SdkTokenVerifier(verifier)
    server = MCPServer(
        name="tier0-mcp",
        # The SDK logs every session start and stop at INFO; keep eval and demo output readable.
        log_level="WARNING",
        instructions="Self-service IT tools that only ever act for the authenticated caller.",
        token_verifier=sdk_verifier,
        auth=AuthSettings(
            issuer_url=ISSUER,
            resource_server_url=MCP_RESOURCE,
            required_scopes=[SCOPE_DIRECTORY_SELF_READ],
            validate_token_resource=True,
        ),
    )
    if controls is not None:
        # Outermost of our middleware: a stopped or over-limit call goes no further.
        server.middleware.append(enforce_controls(server, controls))
    server.middleware.append(reject_undeclared_arguments(server))

    @server.tool(
        description=(
            "Return the caller's own username (primary email). Takes no arguments; "
            "it can only ever answer for the authenticated caller."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
        ),
    )
    def get_my_username() -> dict[str, str]:
        token = get_access_token()
        if token is None or not token.subject:
            raise ToolRefused("not authenticated")
        if SCOPE_DIRECTORY_SELF_READ not in token.scopes:
            raise ToolRefused("missing scope")
        try:
            resource = directory.users_get(
                token.subject, principal=MCP_PRINCIPAL, fields=USERNAME_FIELDS
            )
        except DirectoryError as error:
            raise ToolRefused("directory lookup failed") from error
        username = resource.get("primaryEmail")
        if resource.get("suspended") or resource.get("archived"):
            raise ToolRefused("account suspended")
        if not isinstance(username, str) or not username:
            raise ToolRefused("directory lookup failed")
        return {"username": username}

    return McpServerHandle(server=server, verifier=sdk_verifier)
