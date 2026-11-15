"""Mock device assurance: is the requester's current session on a managed device?

A real deployment would ask the identity provider (Okta device assurance) about
the device behind the session. This mock answers the same question from a small
table. It is separate from the generated world, and everyone is on a managed,
compliant device until a test or a needle says otherwise, so existing worlds
behave exactly as before.
"""

from dataclasses import dataclass
from typing import Any

from tier0.clock import Clock, format_time
from tier0.verification.context import ContextSourceError

MANAGED = "MANAGED"
NOT_MANAGED = "NOT_MANAGED"


@dataclass(frozen=True, slots=True)
class DeviceRead:
    """One logged call."""

    principal: str
    user_id: str
    at: str


class MockDeviceAssurance:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._devices: dict[str, dict[str, Any]] = {}
        self._reads: list[DeviceRead] = []
        # Set to True to act like an outage: every call raises.
        self.unavailable = False

    @property
    def reads(self) -> tuple[DeviceRead, ...]:
        return tuple(self._reads)

    def set_session_device(self, user_id: str, *, managed: bool, compliant: bool = True) -> None:
        """Say what device this person's current session is on."""
        self._devices[user_id] = {
            "managementStatus": MANAGED if managed else NOT_MANAGED,
            "compliant": compliant,
        }

    def session_device(self, user_id: str, *, principal: str) -> dict[str, Any]:
        """The device behind the user's current session: management status and
        whether it meets the device policy. No serial number, no device name."""
        self._reads.append(
            DeviceRead(principal=principal, user_id=user_id, at=format_time(self._clock.now()))
        )
        if self.unavailable:
            raise ContextSourceError("device assurance unavailable")
        return dict(self._devices.get(user_id, {"managementStatus": MANAGED, "compliant": True}))
