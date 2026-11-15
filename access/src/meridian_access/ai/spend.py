"""A spend ledger, a hard cap and a per-run limit for paid Claude calls (AAC-006).

Each paid call appends one JSON line to a ledger (time, model, tokens, cost; never a key
or any text). Before every call its worst-case cost is checked against the cap
(`ACCESS_CLAUDE_SPEND_CAP_USD`, default $3, across all runs because the ledger is a file)
and against the per-run limit (`ACCESS_CLAUDE_RUN_LIMIT_USD`, default $0.50). If either
would be crossed, SpendCapReached is raised and nothing is sent. This is not a client
error, so a caller can't quietly treat it as "no suggestion" and go on.
"""

import json
import logging
import math
import os
from datetime import UTC, datetime
from pathlib import Path

from meridian_access.ai.pricing import rate_for

DEFAULT_CAP_USD = 3.0
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
    ) -> None:
        env_cap = os.environ.get("ACCESS_CLAUDE_SPEND_CAP_USD")
        env_run = os.environ.get("ACCESS_CLAUDE_RUN_LIMIT_USD")
        self.path = path or default_ledger_path()
        self.cap_usd = _amount(
            cap_usd if cap_usd is not None else float(env_cap or DEFAULT_CAP_USD), "spend cap"
        )
        self.run_limit_usd = _amount(
            run_limit_usd if run_limit_usd is not None else float(env_run or DEFAULT_RUN_LIMIT_USD),
            "run limit",
        )
        self.run_spent_usd = 0.0

    def total_usd(self) -> float:
        if not self.path.is_file():
            return 0.0
        total = 0.0
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                total += float(json.loads(line)["cost_usd"])
        return total

    def check(self, next_call_usd: float = 0.0) -> None:
        """Refuse the next call if its worst case would cross the cap or the run limit."""
        spent = self.total_usd()
        if spent >= self.cap_usd or spent + next_call_usd > self.cap_usd:
            raise SpendCapReached(
                f"Claude spend cap: ${spent:.4f} spent, the next call could cost up to "
                f"${next_call_usd:.4f}, cap ${self.cap_usd:.2f}"
            )
        if self.run_spent_usd + next_call_usd > self.run_limit_usd:
            raise SpendCapReached(
                f"Claude per-run limit: ${self.run_spent_usd:.4f} spent this run, the next "
                f"call could cost up to ${next_call_usd:.4f}, limit ${self.run_limit_usd:.2f}"
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
        if not known:
            entry["priced_as"] = "highest known rate (unknown model id)"
            log.warning("no price for %s; ledgered at the highest known rate", model)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
        self.run_spent_usd += cost
        return cost
