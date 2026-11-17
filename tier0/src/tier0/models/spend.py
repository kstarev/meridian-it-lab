"""A spend ledger and a hard cap for paid Claude calls (milestone 3).

Every paid call appends one line to a JSON-lines ledger under `out/` (gitignored):
the time, model, tokens, cost and budget item, never a key or any text.

Before each call (ADR-037), the call must name a budget item (`TIER0_CLAUDE_BUDGET_ITEM`) from
`budget/claude-credit-2026-12.json`, and the ledger refuses it unless that item is tier0's, the
owner has approved it, the paid window is open (to `paid_until`, UTC) and the call's worst case
would cross neither the item's sub-cap nor tier0's cap. Entries from before the budget (no
item) count against no item. A ledger built with an explicit `cap_usd` uses that one cap
instead, with no budget: only tests do that, and a guard test keeps it out of the code.

Because the ledger is a file, the caps hold across the separate tuning, dev-set and
final-comparison commands, not just within one process. When a cap is reached,
SpendCapReached stops the run; it is not a ModelError, so the agent can't quietly
treat it as a model failure and carry on.
"""

import json
import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from tier0.models.budget import PROJECT, Budget, load_budget
from tier0.models.pricing import tokens_usd
from tier0.secrets import REPO_ROOT

DEFAULT_LEDGER = REPO_ROOT / "out" / "claude-spend.jsonl"
ITEM_ENV = "TIER0_CLAUDE_BUDGET_ITEM"
log = logging.getLogger(__name__)


class SpendCapReached(RuntimeError):
    """The paid-call budget is used up; nothing more is sent."""


class SpendLedger:
    def __init__(
        self,
        path: Path | None = None,
        cap_usd: float | None = None,
        item: str | None = None,
        budget: Budget | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        env_path = os.environ.get("TIER0_CLAUDE_SPEND_LEDGER")
        self.path = path or (Path(env_path) if env_path else DEFAULT_LEDGER)
        self.cap_usd = cap_usd  # set only by tests: one fixed cap, no budget
        self.item = None if cap_usd is not None else (item or os.environ.get(ITEM_ENV) or None)
        self._budget = budget
        self._clock = clock or (lambda: datetime.now(UTC))

    def _entries(self) -> list[dict[str, object]]:
        if not self.path.is_file():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]

    def total_usd(self) -> float:
        return sum(float(e["cost_usd"]) for e in self._entries())  # type: ignore[arg-type]

    def check(self, next_call_usd: float = 0.0) -> None:
        """Refuse the next call if its worst-case cost would take spend past a cap, so
        no cap is ever crossed, not even by one call."""
        if self.cap_usd is not None:
            spent = self.total_usd()
            if spent >= self.cap_usd or spent + next_call_usd > self.cap_usd:
                raise SpendCapReached(
                    f"Claude spend cap: ${spent:.4f} spent, the next call could cost up to "
                    f"${next_call_usd:.4f}, cap ${self.cap_usd:.2f} (ledger {self.path})"
                )
            return
        self._check_budget(next_call_usd)

    def _check_budget(self, next_call_usd: float) -> None:
        if not self.item:
            raise SpendCapReached(
                f"no budget item: set {ITEM_ENV} to an approved {PROJECT} item in "
                "budget/claude-credit-2026-12.json (ADR-037)"
            )
        budget = self._budget or load_budget()
        item = budget.items.get(self.item)
        if item is None or item.project != PROJECT:
            raise SpendCapReached(f"budget item {self.item!r} is not a {PROJECT} item")
        if not item.approved:
            raise SpendCapReached(f"budget item {item.id} is not approved by the owner yet")
        today = self._clock().astimezone(UTC).date()
        if today > budget.paid_until:
            raise SpendCapReached(
                f"the paid window closed on {budget.paid_until.isoformat()} (UTC); no paid calls"
            )
        mine = budget.project_items(PROJECT)
        spent_item = spent_project = 0.0
        for entry in self._entries():
            tag = entry.get("budget_item")
            cost = float(entry["cost_usd"])  # type: ignore[arg-type]
            spent_item += cost if tag == item.id else 0.0
            spent_project += cost if tag in mine else 0.0
        for label, spent, cap in (
            (f"budget item {item.id}", spent_item, item.cap_usd),
            (f"the {PROJECT} budget", spent_project, budget.projects[PROJECT]),
        ):
            if spent >= cap or spent + next_call_usd > cap:
                raise SpendCapReached(
                    f"Claude spend cap for {label}: ${spent:.4f} spent, the next call could "
                    f"cost up to ${next_call_usd:.4f}, cap ${cap:.2f} (ledger {self.path})"
                )

    def record(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        *,
        cache_write_tokens: int = 0,
        cache_read_tokens: int = 0,
    ) -> float:
        """Write the call down. This runs after money is spent, so it never raises on a
        model it can't price: an unknown id (a fallback, a dated id) is priced at the
        highest known rate and logged."""
        cost, known = tokens_usd(
            model,
            input_tokens,
            output_tokens,
            cache_write_tokens=cache_write_tokens,
            cache_read_tokens=cache_read_tokens,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry: dict[str, object] = {
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": round(cost, 8),
        }
        if cache_write_tokens:
            entry["cache_write_tokens"] = cache_write_tokens
        if cache_read_tokens:
            entry["cache_read_tokens"] = cache_read_tokens
        if self.item:
            entry["budget_item"] = self.item
        if not known:
            entry["priced_as"] = "highest known rate (unknown model id)"
            log.warning("no price for %s; ledgered at the highest known rate", model)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
        return cost
