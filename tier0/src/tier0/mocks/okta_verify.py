"""Mock Okta Verify push with a number challenge.

`send_push` and `poll` are shaped like Okta's push factor verify calls: a push
starts as WAITING and ends as SUCCESS, REJECTED or TIMEOUT, and the verify call
returns the number the user must pick (`correctAnswer`).

`answer` is the phone: tests and needles call it to say what the person holding
the phone tapped. A push lives only on the phone of the account it was sent to,
so an answer from any other account changes nothing.
"""

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from tier0.clock import Clock, format_time
from tier0.verification.push import REJECTED, SUCCESS, TIMEOUT, WAITING, PushError

PUSH_LIFETIME = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class PushRecord:
    """One push that reached a phone."""

    principal: str
    user_id: str
    transaction_id: str
    at: str


@dataclass(slots=True)
class _Transaction:
    user_id: str
    number: int
    sent_at: datetime
    result: str = WAITING


class MockOktaVerify:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._transactions: dict[str, _Transaction] = {}
        self._pushes: list[PushRecord] = []
        # Set to True to act like an outage: every call raises.
        self.unavailable = False

    @property
    def pushes(self) -> tuple[PushRecord, ...]:
        """Every push sent, so tests can check who got one."""
        return tuple(self._pushes)

    def send_push(self, user_id: str, *, principal: str) -> dict[str, Any]:
        if self.unavailable:
            raise PushError("push service unavailable")
        transaction_id = secrets.token_hex(12)
        number = secrets.randbelow(100)
        self._transactions[transaction_id] = _Transaction(user_id, number, self._clock.now())
        self._pushes.append(
            PushRecord(
                principal=principal,
                user_id=user_id,
                transaction_id=transaction_id,
                at=format_time(self._clock.now()),
            )
        )
        return {"factorResult": WAITING, "transactionId": transaction_id, "correctAnswer": number}

    def poll(self, transaction_id: str, *, principal: str) -> dict[str, Any]:
        if self.unavailable:
            raise PushError("push service unavailable")
        transaction = self._transactions.get(transaction_id)
        if transaction is None:
            raise PushError("unknown transaction")
        self._expire(transaction)
        return {"factorResult": transaction.result}

    def answer(
        self, transaction_id: str, *, user_id: str, approve: bool, number: int | None = None
    ) -> None:
        """What the person holding the phone did. Ignored unless it is the phone of
        the account the push was sent to and the push is still waiting."""
        transaction = self._transactions.get(transaction_id)
        if transaction is None or transaction.user_id != user_id:
            return
        self._expire(transaction)
        if transaction.result != WAITING:
            return
        approved = approve and number == transaction.number
        transaction.result = SUCCESS if approved else REJECTED

    def _expire(self, transaction: _Transaction) -> None:
        late = self._clock.now() - transaction.sent_at > PUSH_LIFETIME
        if transaction.result == WAITING and late:
            transaction.result = TIMEOUT
