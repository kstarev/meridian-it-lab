"""Test-wide guard: no test can reach the network outside this machine.

The lab uses fake data only and must never touch a real tenant. Every test runs
inside tier0.netguard.loopback_only(): any outbound connection, UDP send or DNS
lookup raises OutboundNetworkBlocked. Loopback and Unix sockets stay allowed, so
in-process servers and the asyncio event loop keep working. Eval runs use the same
guard, so tests and evals follow one rule.
"""

from collections.abc import Iterator

import pytest

# pytest's importlib mode names tier0/tests/*.py "tier0.tests.<file>". If the real
# tier0 package isn't imported yet, pytest creates an empty stand-in "tier0" package
# that hides it. This root conftest always loads first, so importing tier0 here
# prevents that.
import tier0  # noqa: F401
from tier0.netguard import loopback_only


@pytest.fixture(autouse=True)
def _block_outbound_network() -> Iterator[None]:
    with loopback_only():
        yield
