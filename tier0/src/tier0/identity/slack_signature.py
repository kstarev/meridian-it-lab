"""Verify that a request really came from Slack."""

import hashlib
import hmac
from collections.abc import Mapping
from datetime import datetime, timedelta

MAX_SKEW = timedelta(minutes=5)


class SignatureError(Exception):
    """The request failed verification. `reason` is a short machine-readable code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def verify_slack_request(
    headers: Mapping[str, str], body: bytes, *, signing_secret: bytes, now: datetime
) -> None:
    """Raise SignatureError unless the request is signed by Slack and recent.

    Rejects forged requests (wrong secret or edited body) and requests outside
    the 5-minute window. Replay of a fresh request inside the window is handled
    later by event-id dedupe in the gateway.
    """
    lowered = {name.lower(): value for name, value in headers.items()}
    timestamp = lowered.get("x-slack-request-timestamp")
    signature = lowered.get("x-slack-signature")
    if timestamp is None or signature is None:
        raise SignatureError("missing_headers")

    # Plain ASCII digits only: int() would also accept spaces, signs and underscores.
    if not (timestamp.isascii() and timestamp.isdigit()):
        raise SignatureError("bad_timestamp")
    if abs(now.timestamp() - int(timestamp)) > MAX_SKEW.total_seconds():
        raise SignatureError("stale_timestamp")

    base = b"v0:" + timestamp.encode("ascii") + b":" + body
    expected = "v0=" + hmac.new(signing_secret, base, hashlib.sha256).hexdigest()
    if not signature.startswith("v0=") or not hmac.compare_digest(
        signature.encode("utf-8"), expected.encode("utf-8")
    ):
        raise SignatureError("bad_signature")
