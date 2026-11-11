"""A spend ledger and a hard cap for paid Claude calls (milestone 3).

Every paid call appends one line to a JSON-lines ledger under `out/` (gitignored):
the time, model, tokens and cost, never a key or any text. Before each call the
ledger's total is checked against the cap (default $2, TIER0_CLAUDE_SPEND_CAP_USD).
Because the ledger is a file, the cap holds across the separate tuning, dev-set and
final-comparison commands, not just within one process. When the cap is reached,
SpendCapReached stops the run; it is not a ModelError, so the agent can't quietly
treat it as a model failure and carry on.
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from tier0.models.pricing import cost_usd
from tier0.secrets import REPO_ROOT

DEFAULT_LEDGER = REPO_ROOT / "out" / "claude-spend.jsonl"
DEFAULT_CAP_USD = 2.0


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

    def check(self) -> None:
        spent = self.total_usd()
        if spent >= self.cap_usd:
            raise SpendCapReached(
                f"Claude spend cap reached: ${spent:.4f} of ${self.cap_usd:.2f} "
                f"(ledger {self.path})"
            )

    def record(self, model: str, input_tokens: int, output_tokens: int) -> float:
        cost = cost_usd("anthropic", model, input_tokens, output_tokens)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": round(cost, 8),
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
        return cost
