# AAC-014: Line breaks in world fields are flattened in code before any prompt is built, and the M7 prompts are re-frozen

**Date:** 2026-11-16 · **Status:** Accepted (the owner's ruling M7-R16, option (a), 2026-11-16). Built in its own pull request.

**Context:** The red-team mapping found a gap ([red team](../red-team.md)). The title prompt and the summary's change lines are built line by line: `Job title: …`, `Department: …`, `Team: …`, `Roles: …`. A line break inside a job title, a department or a plan field can therefore forge a line, such as a second `Roles:` line. The owner prompt is JSON, which escapes CR and LF but keeps the Unicode line and paragraph separators. Of the red-team set's cases, 16 carry a line break, and 9 of them sit where a prompt is line-based.

**Decision:**

- **Every prompt builder replaces each run of line breaks in a field with one space,** before it builds the prompt. Line breaks are the ones Python's `str.splitlines()` splits on: LF, CR, CRLF, VT, FF, the file, group and record separators, NEL, U+2028 and U+2029.
  - It is applied in the title prompt (title, department, team, role names), the summary's change lines, and every string field of the owner prompt.
  - One helper does it, `flatten_line_breaks`.
  - Nothing else in the text changes.
- **A field without a line break gives the same bytes as before.** So the k = 3 tuning comparison still describes these prompts exactly.
- **The M7 prompts are re-frozen with probes that carry every kind of line break.** That way the freeze itself shows whether the fix is in. The new freeze digest goes in [the M7 prep](../m7-prep.md), for the owner to confirm (M7-R2). A commit without the fix prints the old digest.
- **It is merged before the final score and before the red-team run,** so both measure the fixed code. The red-team set's 9 line-based cases then test whether the fix holds.

**Consequences:**

- A field can no longer add a line to a prompt. It can still carry words meant to steer the model. Those are what the red-team run measures.
- A real line break in a field is lost to the model. It means nothing in a job title or a department.
