"""The neutral Directory interface.

Production code depends on this, not on the mock, so the mock can be swapped
for the real Google Admin SDK client without touching the callers.
"""

from collections.abc import Sequence
from typing import Any, Protocol


class DirectoryError(Exception):
    """An API error, carrying the same JSON body Google returns."""

    def __init__(self, code: int, reason: str, message: str) -> None:
        super().__init__(f"{code} {reason}: {message}")
        self.code = code
        self.reason = reason
        self.message = message

    @property
    def body(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "errors": [{"domain": "global", "reason": self.reason, "message": self.message}],
            }
        }


class DirectoryApi(Protocol):
    """Read one user (users.get) or run the narrow existence lookup. Raises DirectoryError."""

    def users_get(
        self, user_key: str, *, principal: str, fields: Sequence[str] | None = None
    ) -> dict[str, Any]: ...

    def users_list(
        self,
        *,
        query: str,
        principal: str,
        fields: Sequence[str] = ("id",),
        max_results: int = 2,
    ) -> dict[str, Any]: ...
