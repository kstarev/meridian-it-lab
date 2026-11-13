"""A spend ledger and a hard cap for paid Claude calls (milestone 3).

Every paid call appends one line to a JSON-lines ledger under `out/` (gitignored):
the time, model, tokens and cost, never a key or any text. Before each call the
ledger's total is checked against the cap (default $4, TIER0_CLAUDE_SPEND_CAP_USD; the
user raised it from $2 for milestone 3's Sonnet runs, ADR-032).
Because the ledger is a file, the cap holds across the separate tuning, dev-set and
final-comparison commands, not just within one process. When the cap is reached,
SpendCapReached stops the run; it is not a ModelError, so the agent can't quietly
treat it as a model failure and carry on.
"""

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from tier0.models.pricing import rate_for
from tier0.secrets import REPO_ROOT

DEFAULT_LEDGER = REPO_ROOT / "out" / "claude-spend.jsonl"
DEFAULT_CAP_USD = 4.0
log = logging.getLogger(__name__)


class SpendCapReached(RuntimeError):
    """The paid-call budget is used up; nothing more is sent."""


class SpendLedger:
    def __init__(self, path: Path | None = None, cap_usd: float | None = None) -> None:
        env_path = os.environ.get("TIER0_CLAUDE_SPEND_LEDGER")
        env_cap = os.environ.get("TIER0_CLAUDE_SPEND_CAP_USD")
        self.path = path or (Path(env_path) if env_path else DEFAULT_LEDGER)
        self.cap_usd = cap_usd if cap_usd is not None else float(env_cap or DEFAULT_CAP_USD)

    def total_usd(self) -> float:
        if not self.path.is_file():
            return 0.0
        total = 0.0
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                total += float(json.loads(line)["cost_usd"])
        return total

    def check(self, next_call_usd: float = 0.0) -> None:
        """Refuse the next call if its worst-case cost would take spend past the cap, so
        the cap is never crossed, not even by one call."""
        spent = self.total_usd()
        if spent >= self.cap_usd or spent + next_call_usd > self.cap_usd:
            raise SpendCapReached(
                f"Claude spend cap: ${spent:.4f} spent, the next call could cost up to "
                f"${next_call_usd:.4f}, cap ${self.cap_usd:.2f} (ledger {self.path})"
            )

    def record(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Write the call down. This runs after money is spent, so it never raises on a
        model it can't price: an unknown id (a fallback, a dated id) is priced at the
        highest known rate and logged."""
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
        if not known:
            entry["priced_as"] = "highest known rate (unknown model id)"
            log.warning("no price for %s; ledgered at the highest known rate", model)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
        return cost
