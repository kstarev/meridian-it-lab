"""A spend ledger, a hard cap and a per-run limit for paid Claude calls (AAC-006).

Each paid call appends one JSON line to a ledger (time, model, tokens, cost, budget item;
never a key or any text). Before every call (ADR-037) the call must name a budget item
(`ACCESS_CLAUDE_BUDGET_ITEM`) from `budget/claude-credit-2026-12.json`, and the ledger refuses
it unless the item is Access-as-Code's, the owner has approved it, the paid window is open (to
`paid_until`, UTC) and the call's worst case would cross neither the item's sub-cap nor the
project's cap (across all runs, because the ledger is a file), nor the per-run limit
(`ACCESS_CLAUDE_RUN_LIMIT_USD`, default $0.50). Entries from before the budget (no item) count
against no item. A ledger built with an explicit `cap_usd` uses that one cap instead, with no
budget: only tests do that, and a guard test keeps it out of the code. When a limit would be
crossed, SpendCapReached is raised and nothing is sent. This is not a client error, so a
caller can't quietly treat it as "no suggestion" and go on.
"""

import json
import logging
import math
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from meridian_access.ai.budget import PROJECT, Budget, load_budget
from meridian_access.ai.pricing import rate_for

ITEM_ENV = "ACCESS_CLAUDE_BUDGET_ITEM"
DEFAULT_RUN_LIMIT_USD = 0.50
log = logging.getLogger(__name__)


def repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "uv.lock").is_file():
            return parent
    return Path.cwd()


def default_ledger_path() -> Path:
    env = os.environ.get("ACCESS_CLAUDE_SPEND_LEDGER")
    return Path(env) if env else repo_root() / "out" / "access-claude-spend.jsonl"


def _amount(value: float, name: str) -> float:
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite number above 0, got {value!r}")
    return value


class SpendCapReached(RuntimeError):
    """The paid-call budget (overall or for this run) is used up; nothing more is sent."""


class SpendLedger:
    def __init__(
        self,
        path: Path | None = None,
        cap_usd: float | None = None,
        run_limit_usd: float | None = None,
        item: str | None = None,
        budget: Budget | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        env_run = os.environ.get("ACCESS_CLAUDE_RUN_LIMIT_USD")
        self.path = path or default_ledger_path()
        # Set only by tests: one fixed cap, no budget.
        self.cap_usd = None if cap_usd is None else _amount(cap_usd, "spend cap")
        self.item = None if cap_usd is not None else (item or os.environ.get(ITEM_ENV) or None)
        self._budget = budget
        self._clock = clock or (lambda: datetime.now(UTC))
        self.run_limit_usd = _amount(
            run_limit_usd if run_limit_usd is not None else float(env_run or DEFAULT_RUN_LIMIT_USD),
            "run limit",
        )
        self.run_spent_usd = 0.0

    def _entries(self) -> list[dict[str, object]]:
        if not self.path.is_file():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]

    def total_usd(self) -> float:
        return sum(float(e["cost_usd"]) for e in self._entries())  # type: ignore[arg-type]

    def check(self, next_call_usd: float = 0.0) -> None:
        """Refuse the next call if its worst case would cross a cap or the run limit."""
        if self.cap_usd is not None:
            spent = self.total_usd()
            if spent >= self.cap_usd or spent + next_call_usd > self.cap_usd:
                raise SpendCapReached(
                    f"Claude spend cap: ${spent:.4f} spent, the next call could cost up to "
                    f"${next_call_usd:.4f}, cap ${self.cap_usd:.2f}"
                )
        else:
            self._check_budget(next_call_usd)
        if self.run_spent_usd + next_call_usd > self.run_limit_usd:
            raise SpendCapReached(
                f"Claude per-run limit: ${self.run_spent_usd:.4f} spent this run, the next "
                f"call could cost up to ${next_call_usd:.4f}, limit ${self.run_limit_usd:.2f}"
            )

    def _check_budget(self, next_call_usd: float) -> None:
        if not self.item:
            raise SpendCapReached(
                f"no budget item: set {ITEM_ENV} to an approved {PROJECT} item in "
                "budget/claude-credit-2026-12.json (ADR-037)"
            )
        budget = self._budget or load_budget()
        item = budget.items.get(self.item)
        if item is None or item.project != PROJECT:
            raise SpendCapReached(f"budget item {self.item!r} is not an {PROJECT} item")
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
                    f"cost up to ${next_call_usd:.4f}, cap ${cap:.2f}"
                )

    def record(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Write a call down after the money is spent; never raises on an unknown model
        (it is priced at the highest known rate)."""
        (per_in, per_out), known = rate_for(model)
        cost = input_tokens * per_in / 1_000_000 + output_tokens * per_out / 1_000_000
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry: dict[str, object] = {
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": round(cost, 8),
        }
        if self.item:
            entry["budget_item"] = self.item
        if not known:
            entry["priced_as"] = "highest known rate (unknown model id)"
            log.warning("no price for %s; ledgered at the highest known rate", model)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
        self.run_spent_usd += cost
        return cost
