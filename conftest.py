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


@pytest.fixture(autouse=True)
def _private_spend_ledger(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Tests never write to the real Claude spend ledger, so fake calls can't count
    against the paid-call cap. A ledger built by default charges a test-only budget item
    that is approved in memory (ADR-037); the real budget file is never changed, and it
    approves nothing until the owner does."""
    import dataclasses

    from tier0.models import budget as tier0_budget
    from tier0.models import spend as tier0_spend

    real = tier0_budget.load_budget()
    approved = dataclasses.replace(
        real,
        items={k: dataclasses.replace(v, approved=True) for k, v in real.items.items()},
    )
    mp = pytest.MonkeyPatch()
    path = tmp_path_factory.mktemp("spend") / "claude-spend.jsonl"
    mp.setenv("TIER0_CLAUDE_SPEND_LEDGER", str(path))
    mp.setenv("TIER0_CLAUDE_BUDGET_ITEM", "tier0-3b")
    mp.setattr(tier0_spend, "load_budget", lambda: approved)
    try:
        yield
    finally:
        mp.undo()
