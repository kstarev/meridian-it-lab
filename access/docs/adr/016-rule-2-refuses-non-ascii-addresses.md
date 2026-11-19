# AAC-016: Rule 2 of identity matching refuses an address with any non-ASCII character other than an accent

**Date:** 2026-11-19 · **Status:** Accepted (the owner's ruling 5, 2026-11-19: "yes: rule 2 leaves such an address unmatched"). Closes gap G1. Built in PR #120.

**Context:** Rule 2 of identity matching (`match.py`) is "normalised email: a `+tag`, case and accents only; dots count". It ran `fold()`: NFKD, then every non-ASCII character dropped. So a zero-width space, a soft hyphen, or a Cyrillic or CJK letter vanished, and `ana.smith\u200b@meridian.example` was matched to the person at `ana.smith@meridian.example` (confidence 0.95). The safety property tests found it ([mutation testing record](../../../docs/evals/2026-11-19-access-safety-mutation-testing.md), gap G1). An account under a look-alike address was pinned on a real, active person, so it was never reported as an orphan. If that person left, the plan would act on an account that was not theirs.

**Decision:**

- **Rule 2 refuses an address that still has a non-ASCII character after case, a `+tag` and accents are removed.**
  - Accents are the combining marks of the canonical decomposition (NFD), plus the letters `_TRANSLIT` spells out (Ø, Æ, ß, Ł, Đ, Þ, Œ and their lower case). Those letters are kept as they are.
  - NFD, not NFKD: NFKD also turns full-width and mathematical letters (`ａ` U+FF41, `𝐚` U+1D41A) and ligatures into ASCII. They are look-alikes, not accents, so they are refused too.
  - The same check applies to HRIS addresses and to rule 3's normalised alias lookup.
- **A refused address stops there, like a company address that rules 1-3 don't place.** No Slack-username, username or name rule is tried, so the account is reported as an orphan.
  - Rule 1 (the exact address) and rule 3 (Google aliases) still run first. Rule 3 matches on Google's own record of the account's other addresses, not on folding.
  - This also covers a look-alike of the company domain, such as `meridian\u200b.example`. It is not the company domain, so without the stop the name rules would try it.
- **Why:** a false match is worse than no match (`match.py`). An orphan goes to a person. A false match quietly keeps access that should go, or removes the wrong person's.
- `fold()` itself is unchanged. The name and username rules still use it.

**Consequences:**

- The tuning world's matching does not change. No address in it has a non-ASCII character, and rule 2 placed none of its accounts. The match result, the plan and the held plan hash the same before and after.
- Published results (the final score, the red team) were made with the old rule. They are not re-scored.
- An outside address with a real non-ASCII character (an internationalised domain, a Cyrillic personal address) is now left unmatched too, even when its names agree. A person places it.
- `test_rule_2_does_not_ignore_characters_other_than_accents` (4 cases) is a normal test now, not a strict xfail.
- Rules 4 and 5 still fold usernames: a Slack userName or an export username with a zero-width space in it is still matched. That is outside rule 2 and outside this ruling.
