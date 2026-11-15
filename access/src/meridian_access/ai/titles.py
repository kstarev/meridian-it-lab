"""Job title -> role suggestions. Only the title, department and team are sent."""

from collections.abc import Sequence
from dataclasses import dataclass

from meridian_access.ai.client import ClaudeClient
from meridian_access.ai.pricing import HAIKU

SYSTEM = (
    "You map a messy job title to one role from a fixed list. Answer with the role id that "
    'fits best, or "none" if no role fits. Give a short reason. You only suggest; people '
    "review every suggestion."
)


@dataclass(frozen=True, slots=True)
class RoleSuggestion:
    role: str  # a role id, or "none"
    reason: str
    model: str


def title_schema(roles: Sequence[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "role": {"type": "string", "enum": [*sorted(roles), "none"]},
            "reason": {"type": "string"},
        },
        "required": ["role", "reason"],
        "additionalProperties": False,
    }


def title_prompt(title: str, department: str, team: str, roles: Sequence[str]) -> str:
    return (
        f"Job title: {title}\nDepartment: {department}\nTeam: {team}\n"
        f"Roles: {', '.join(sorted(roles))}"
    )


def suggest_role(
    title: str,
    department: str,
    team: str,
    roles: Sequence[str],
    client: ClaudeClient | None = None,
) -> RoleSuggestion | None:
    """One suggestion, or None when the model refused, failed or gave an invalid answer."""
    client = client or ClaudeClient(HAIKU)
    data = client.complete_json(
        SYSTEM, title_prompt(title, department, team, roles), title_schema(roles)
    )
    if data is None:
        return None
    return RoleSuggestion(role=data["role"], reason=data["reason"], model=client.model)
