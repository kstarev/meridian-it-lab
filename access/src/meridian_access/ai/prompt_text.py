"""Text from the world, made safe for a line-based prompt (M7-R16 (a), 2026-11-16).

The title prompt and the summary's change lines are built line by line, so a line break inside
a job title, a department or a plan field could forge a line, such as a second `Roles:` line.
Every prompt builder passes each field through `flatten_line_breaks` before it builds the
prompt. The owner prompt does too: it is JSON, which escapes CR and LF but keeps U+2028 and
U+2029 as they are, and the same rule keeps the three prompts alike.
"""

import re

# Exactly the boundaries `str.splitlines()` splits on: LF, VT, FF, CR (and CRLF), FS, GS, RS,
# NEL, U+2028 LINE SEPARATOR and U+2029 PARAGRAPH SEPARATOR.
_LINE_BREAKS = re.compile("[\n\x0b\x0c\r\x1c\x1d\x1e\x85\u2028\u2029]+")


def flatten_line_breaks(text: str) -> str:
    """`text` with each run of line breaks replaced by one space (CRLF is one break). Nothing
    else changes: other whitespace, case and accents stay as they are."""
    return _LINE_BREAKS.sub(" ", text)
