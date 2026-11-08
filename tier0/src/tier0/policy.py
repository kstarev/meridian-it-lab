"""The tier and tool policy table. The model never picks a tier or a tool.

The model only labels a request (see `tier0.models.TRIAGE_SCHEMA`). This table,
written in code, decides what happens next: which action, which tier, which
tools and which token scopes. An unknown label falls back to "other", which
escalates to a person (fail closed).

Learning may add knowledge, never permissions: whatever tier0 learns from past
tickets may change what it says, but it can never change this table. The table
is code, reviewed like code.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from tier0.identity.tokens import SCOPE_DIRECTORY_SELF_READ
from tier0.models import ABOUT_SOMEONE_ELSE, OTHER, OWN_USERNAME

TOOL_GET_MY_USERNAME = "get_my_username"

type Action = Literal["answer", "refuse", "escalate"]


@dataclass(frozen=True, slots=True)
class PolicyRule:
    """What tier0 may do for one category of request."""

    category: str
    tier: str
    action: Action
    allowed_tools: tuple[str, ...]
    scopes: tuple[str, ...]


class PolicyViolation(Exception):
    """A tool call that the policy does not allow."""


POLICY: Mapping[str, PolicyRule] = MappingProxyType(
    {
        OWN_USERNAME: PolicyRule(
            category=OWN_USERNAME,
            tier="A",
            action="answer",
            allowed_tools=(TOOL_GET_MY_USERNAME,),
            scopes=(SCOPE_DIRECTORY_SELF_READ,),
        ),
        ABOUT_SOMEONE_ELSE: PolicyRule(
            category=ABOUT_SOMEONE_ELSE,
            tier="A",
            action="refuse",
            allowed_tools=(),
            scopes=(),
        ),
        OTHER: PolicyRule(
            category=OTHER,
            tier="A",
            action="escalate",
            allowed_tools=(),
            scopes=(),
        ),
    }
)


def decide(category: str, policy: Mapping[str, PolicyRule] = POLICY) -> PolicyRule:
    """The rule for a category. Unknown categories get the "other" rule (escalate)."""
    rule = policy.get(category)
    if rule is not None:
        return rule
    return policy.get(OTHER, POLICY[OTHER])


def check_tool_call(rule: PolicyRule, tool_name: str) -> None:
    """Raise PolicyViolation unless the rule allows this tool."""
    if tool_name not in rule.allowed_tools:
        raise PolicyViolation(f"tool {tool_name!r} is not allowed for {rule.category!r}")
