"""The lab's Claude budget: per-project caps and per-item sub-caps (ADR-037).

The budget is `budget/claude-credit-2026-12.json`, shared with tier0, whose
`tier0.models.budget` reads the same file the same way (a root test checks that the two
agree; this is a copy, as `pricing.py` is). Every paid call names one budget item
(`ACCESS_CLAUDE_BUDGET_ITEM`); the spend ledger
refuses the call unless the item is this project's, the owner has approved it, the paid window
(`paid_until`, UTC) is still open, and neither the item's sub-cap nor the project's cap would be
crossed. The shared buffer belongs to no item: it is spent only by a reviewed change to the
budget file that raises an item's cap.
"""

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "uv.lock").is_file():
            return parent
    return Path.cwd()


BUDGET_FILE = _repo_root() / "budget" / "claude-credit-2026-12.json"
PROJECT = "access"


@dataclass(frozen=True)
class Item:
    id: str
    project: str
    cap_usd: float
    estimate_usd: float
    approved: bool


@dataclass(frozen=True)
class Budget:
    credit_usd: float
    paid_until: date
    buffer_usd: float
    projects: dict[str, float]
    items: dict[str, Item]

    def project_items(self, project: str) -> set[str]:
        return {i.id for i in self.items.values() if i.project == project}


def load_budget(path: Path | None = None) -> Budget:
    """Read and check the budget file: item caps add up to their project's cap, and the
    projects plus the buffer to no more than the credit."""
    raw = json.loads((path or BUDGET_FILE).read_text(encoding="utf-8"))
    items: dict[str, Item] = {}
    for entry in raw["items"]:
        item = Item(
            id=str(entry["id"]),
            project=str(entry["project"]),
            cap_usd=float(entry["cap_usd"]),
            estimate_usd=float(entry["estimate_usd"]),
            approved=entry["approved"] is True,
        )
        if item.id in items:
            raise ValueError(f"budget item {item.id} is listed twice")
        if item.cap_usd <= 0 or item.estimate_usd <= 0 or item.estimate_usd > item.cap_usd:
            raise ValueError(f"budget item {item.id}: need 0 < estimate <= cap")
        items[item.id] = item
    projects = {str(k): float(v) for k, v in raw["projects"].items()}
    budget = Budget(
        credit_usd=float(raw["credit_usd"]),
        paid_until=date.fromisoformat(raw["paid_until"]),
        buffer_usd=float(raw["buffer_usd"]),
        projects=projects,
        items=items,
    )
    for item in items.values():
        if item.project not in projects:
            raise ValueError(f"budget item {item.id}: unknown project {item.project}")
    for project, cap in projects.items():
        total = sum(items[i].cap_usd for i in budget.project_items(project))
        if abs(total - cap) > 1e-9:
            raise ValueError(f"{project}: item caps add up to ${total}, the project cap is ${cap}")
    if sum(projects.values()) + budget.buffer_usd > budget.credit_usd + 1e-9:
        raise ValueError("the projects and the buffer add up to more than the credit")
    return budget
