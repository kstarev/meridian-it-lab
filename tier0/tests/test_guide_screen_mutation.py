"""The credentials check's warning window, pinned to the character (guide_screen.flag_text).

A request to send a secret is not flagged when a warning word ("never", "not" ...) stands in
the 16 characters of normalised text before it. No test pinned that window's edges, so the
mutation run of 2026-11-18 (docs/evals/2026-11-18-safety-mutation-testing.md) could make it
15 or 17 characters, or start it one character late, and nothing failed. Made-up sentences.
"""

import pytest

from tier0.knowledge.guide_screen import CREDENTIALS, _normalise, flag_text


@pytest.mark.parametrize(
    ("filler", "expected"),
    [
        # "never" starts exactly 16 characters before "send": inside the window.
        ("x" * 9, None),
        # One character more: "never" starts 17 before, outside the window.
        ("x" * 10, CREDENTIALS),
    ],
    ids=["warning 16 before", "warning 17 before"],
)
def test_a_warning_counts_only_within_the_16_characters_before_the_request(
    filler: str, expected: str | None
) -> None:
    text = f"never {filler} send your password"
    assert _normalise(text).index("send") == len("never ") + len(filler) + 1
    assert flag_text(text) == expected


def test_a_warning_at_the_very_start_of_the_text_counts() -> None:
    # The window starts at the text's first character when the request is near the start.
    assert _normalise("Never send your password.").index("send") == 6
    assert flag_text("Never send your password.") is None
    assert flag_text("Send your password.") == CREDENTIALS
