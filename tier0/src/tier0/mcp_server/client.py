"""How tier0's agent talks to its MCP server, in-process."""

import contextlib
import json
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from types import TracebackType
from typing import Any

import httpx2
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import MCPError
from starlette.applications import Starlette

from tier0.identity.tokens import MCP_RESOURCE
from tier0.mcp_server.server import MCP_HOST


class McpAuthError(Exception):
    """The server answered 401 or 403. The SDK client hides the status; we capture it."""

    def __init__(self, status: int, www_authenticate: str | None) -> None:
        super().__init__(f"MCP server refused the token: HTTP {status}")
        self.status = status
        self.www_authenticate = www_authenticate


class McpToolError(Exception):
    """A tool or protocol error. `code` is the JSON-RPC code when there is one."""

    def __init__(self, code: int | None, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@asynccontextmanager
async def serve_in_process(server: MCPServer) -> AsyncIterator[Starlette]:
    """Build the ASGI app and run the session manager around it. No port is opened.

    Stateless + JSON responses is required: httpx2's ASGITransport buffers the
    whole response, so a long-lived SSE stream would never finish. The ASGI
    transport does not run lifespans, so the session manager is started here.
    """
    app = server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(allowed_hosts=[MCP_HOST], allowed_origins=[]),
    )
    try:
        async with server.session_manager.run():
            yield app
    except BaseExceptionGroup as group:
        # The session manager's task group wraps whatever the caller raised.
        leaves = _leaves(group)
        if len(leaves) == 1:
            raise leaves[0] from None
        raise


def _leaves(error: BaseException) -> list[BaseException]:
    if isinstance(error, BaseExceptionGroup):
        return [leaf for inner in error.exceptions for leaf in _leaves(inner)]
    return [error]


class McpSession:
    """One bearer token, one connection. Use one session per ticket."""

    def __init__(self, app: Starlette, token: str | None) -> None:
        self._app = app
        self._token = token
        self._auth_failure: tuple[int, str | None] | None = None
        self._stack = AsyncExitStack()
        self._client: Client | None = None

    async def _record(self, response: httpx2.Response) -> None:
        if response.status_code in (401, 403) and self._auth_failure is None:
            self._auth_failure = (response.status_code, response.headers.get("www-authenticate"))

    def _translate(self, error: BaseException) -> Exception:
        if self._auth_failure is not None:
            return McpAuthError(*self._auth_failure)
        for leaf in _leaves(error):
            if isinstance(leaf, MCPError):
                return McpToolError(leaf.error.code, leaf.error.message)
        return McpToolError(None, "MCP call failed")

    async def __aenter__(self) -> "McpSession":
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        http = httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=self._app),
            base_url=f"https://{MCP_HOST}",
            headers=headers,
            event_hooks={"response": [self._record]},
        )
        try:
            await self._stack.enter_async_context(http)
            self._client = await self._stack.enter_async_context(
                Client(streamable_http_client(MCP_RESOURCE, http_client=http))
            )
        except BaseException as error:
            await self._close_quietly()
            if isinstance(error, Exception):
                raise self._translate(error) from None
            raise
        return self

    async def _close_quietly(self) -> None:
        # Already failing: the first error is the one that gets reported.
        with contextlib.suppress(Exception):
            await self._stack.aclose()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            await self._stack.aclose()
        except Exception as error:
            if exc is None:
                raise self._translate(error) from None

    def _require_client(self) -> Client:
        if self._client is None:
            raise McpToolError(None, "session is not open")
        return self._client

    async def list_tools(self) -> list[Any]:
        try:
            result = await self._require_client().list_tools()
        except Exception as error:
            if isinstance(error, McpToolError | McpAuthError):
                raise
            raise self._translate(error) from None
        return list(result.tools)

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            result = await self._require_client().call_tool(name, arguments or {})
        except Exception as error:
            if isinstance(error, McpToolError | McpAuthError):
                raise
            raise self._translate(error) from None
        texts = [c.text for c in result.content if getattr(c, "text", None) is not None]
        if result.is_error:
            raise McpToolError(None, texts[0] if texts else "tool failed")
        if result.structured_content is not None:
            return dict(result.structured_content)
        try:
            parsed = json.loads(texts[0])
        except (IndexError, ValueError):
            raise McpToolError(None, "tool returned no usable result") from None
        if not isinstance(parsed, dict):
            raise McpToolError(None, "tool returned no usable result")
        return parsed


def open_session(app: Starlette, token: str | None) -> McpSession:
    return McpSession(app, token)
