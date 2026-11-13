"""Wire the offline lab: everything in-process, fake data, no network, no credentials.

Each runtime makes fresh signing keys and a fresh Slack secret, so nothing is
shared between runs. Used by tests, `make eval` and `make demo`.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from meridian_dataset import World
from tier0.agent import ServiceDeskAgent
from tier0.clock import MockClock, parse_time
from tier0.identity.binding import IdentityBinder
from tier0.identity.tokens import JwtVerifier, TokenIssuer
from tier0.mcp_server.client import serve_in_process
from tier0.mcp_server.server import McpServerHandle, build_mcp_server
from tier0.mentions import MentionCheck
from tier0.mocks.directory import MockDirectory
from tier0.mocks.linear import MockLinear
from tier0.mocks.slack import APP_ID, MockSlack
from tier0.models import ModelAdapter, StubModel
from tier0.sealed import require_unsealed
from tier0.tickets.linear import LinearTicketAdapter
from tier0.tracing import Tracer


@dataclass(frozen=True, slots=True)
class Runtime:
    world: World
    clock: MockClock
    slack: MockSlack
    directory: MockDirectory
    linear: MockLinear
    tickets: LinearTicketAdapter
    issuer: TokenIssuer
    mcp: McpServerHandle
    tracer: Tracer
    model: ModelAdapter
    agent: ServiceDeskAgent
    mcp_app: Any


@asynccontextmanager
async def offline_runtime(
    world: World,
    *,
    model: ModelAdapter | None = None,
    clock: MockClock | None = None,
    tracer: Tracer | None = None,
    final_comparison: str | None = None,
    **agent_options: Any,
) -> AsyncIterator[Runtime]:
    """Build the whole agent against the mocks. `agent_options` go to ServiceDeskAgent.

    Refuses a sealed world (held-out v2) unless this is the final comparison (ADR-015).
    """
    require_unsealed(world, final_comparison=final_comparison)
    clock = clock if clock is not None else MockClock(parse_time(world.reference_time))
    tracer = tracer if tracer is not None else Tracer()
    model = model if model is not None else StubModel()
    directory = MockDirectory(world, clock)
    slack = MockSlack(world, clock)
    linear = MockLinear(clock)
    tickets = LinearTicketAdapter(linear)
    issuer = TokenIssuer(clock)
    verifier = JwtVerifier(issuer.public_key, clock)
    handle = build_mcp_server(directory=directory, verifier=verifier)
    binder = IdentityBinder(
        slack=slack,
        directory=directory,
        team_id=world.slack_team.team_id,
        app_id=APP_ID,
        domain=world.domain,
    )
    async with serve_in_process(handle.server) as app:
        agent = ServiceDeskAgent(
            slack=slack,
            signing_secret=slack.signing_secret,
            binder=binder,
            tickets=tickets,
            issuer=issuer,
            mcp_app=app,
            model=model,
            tracer=tracer,
            clock=clock,
            domain=world.domain,
            recovery_url=world.identity_recovery_url,
            **{"mention_check": MentionCheck(directory), **agent_options},
        )
        yield Runtime(
            world=world,
            clock=clock,
            slack=slack,
            directory=directory,
            linear=linear,
            tickets=tickets,
            issuer=issuer,
            mcp=handle,
            tracer=tracer,
            model=model,
            agent=agent,
            mcp_app=app,
        )
