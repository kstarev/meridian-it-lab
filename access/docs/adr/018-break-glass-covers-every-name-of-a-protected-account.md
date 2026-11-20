# AAC-018: Break-glass protection covers every name of a protected account, worked out in one place

**Date:** 2026-11-19 · **Status:** Accepted (the owner's ruling, 2026-11-19: "break-glass protection covers every name of a protected account, aliases included"). Pinned as gap G2 in PR #123; built in PR #129. A proposed amendment for gap G7 is at the end.

**Context:** [AAC-007](007-safety-rails.md) says no plan ever changes a break-glass account. Four places checked this, each with its own code: the policy, matching, the diff's last word and apply's final guard. They had three holes:

- A bare username was protected only when the policy listed it or a pattern matched it. So `ops` was not protected when the Google user `ops@meridian.example` was protected through its listed alias.
- Matching looked only at the names one record carried. An Okta user or Slack user under another name of the same account was matched as usual, or became an orphan to remove.
- The policy compared names with `str.lower`, but matching compares them with `str.casefold`.

Apply really deactivated the Slack user `ops`, and deprovisioned the Okta user `root-login@meridian.example` (the company address of a protected login).

**Decision:**

- **One module, `break_glass.py`, works out every name of every protected account.** Matching, the diff's last word and apply's guard all ask it.
  - Each place asks on its own, from the state it sees, so the diff and apply still don't trust matching.
  - The old helpers in `match.py`, `diff.py` and `apply.py` are removed.
- **A name has two forms, and both are the same name.** A bare username `x` is also `x@meridian.example`, and a company address is also its bare username. A local part with spaces around it or an `@` in it has no bare form.
- **Names are compared the way matching compares them:** spaces around a name are dropped and case is folded with `str.casefold`. The policy's own check (`Policy.break_glass_kind`) now uses `str.casefold` too, for its list and for the name it is given.
- **An account is every record linked by a shared name, in either form.** That covers a Google user's primary address and aliases, an Okta user's login and email, a Slack userName and email, and an export row.
  - If the policy protects any name of an account, every name of that account is protected, in both forms.
  - Every record of that account is marked break-glass.
- **The kind is still "listed" or "pattern", and it belongs to the account.** Every record of an account gets "listed" if the policy lists any name of that account, and "pattern" if it doesn't.
  - So a record whose own name only matches the pattern no longer gets the warning "matches the break-glass pattern but isn't listed" when another name of its account is listed.
  - Before, the first of a record's own names decided.

**Consequences:**

- The 12 G2 tests pass. On the tuning world nothing changes: the plan and the matches are identical before and after.
- A bare name in the policy now also protects its company address. The old test `test_a_bare_name_the_policy_lists_as_written_is_protected_as_written` said the opposite. It is now `test_a_bare_name_the_policy_lists_is_protected_in_both_forms`. The lab's policy lists only full addresses.
- Protection can spread further than before. A record that carries a protected name and someone else's address makes that person's records protected too. The diff already did this for that record's own names.
  - Each such record appears in the plan as a break-glass `never_touch` entry, so a reviewer sees it, and nothing changes it.
  - That is the safer way to fail: an unprotected break-glass account can lock IT out (AAC-007).
- The safety count `break_glass_changes` (`safety.py`) is unchanged. It is written apart from the guards on purpose. It still counts the narrower set: names the policy protects, their bare usernames, and other names of the account as written. Widening it is the owner's call.
- Only case variants are covered. A `+tag` or an accent, which rule 2 of matching ignores, still makes a different name here.
- The mutation-testing targets for break-glass now point at `break_glass.py` (`tools/mutate.py`).

## Proposed amendment (2026-11-20): protection spreads only through records IT controls

**Status:** Proposed. Needs the owner's ruling. Gap G7, pinned in PR #151.

**Context:** The decision above links every record that shares a name into one account, whoever can edit the record. A person can edit their own Slack profile email, and a Slack user can plant their userName ([red-team.md](../red-team.md)). So a leaver who set their Slack email to `breakglass-01@meridian.example` made their own Okta and Google users break-glass:

- matching marked them, and the diff's last word and apply's guard dropped their suspension;
- the plan had no warning;
- the rows for their own address said "listed in the break-glass policy", which it is not.

The leaver was never offboarded. Review finding F2, 2026-11-20.

**Proposed decision:**

- **Only records IT controls link names into one account.** These are an Okta user (its login and email) and its app assignments, and a Google user (its primary address and aliases, from the admin directory). `break_glass.it_controlled` says which.
- **Any other record links nothing.** That is a Slack user (userName and profile email) and an export row. Export rows come from each app's own admin page, typed by an app admin or by the person ([red-team.md](../red-team.md)). A parsed export row carries one name anyway: its email cell, else its username cell.
  - Such a record is still break-glass when it carries a protected name, so nothing changes it.
  - The guards also protect it by its key (system, app id, account). A change that names it is dropped even when its account name is not a protected name.
  - Its other names are not protected through it.
- **The plan warns and names each such record** when it carries a protected name and another name. For example: "ana.smith (slack) carries the break-glass name breakglass-01@meridian.example and also ana.smith, ana.smith@meridian.example: not a record IT controls, so it is left alone but protects nothing else; check who set it".
- **Each never_touch row says how the protection was reached:**
  - "listed in the break-glass policy" (or "pattern in the break-glass policy") when the record's own name is protected, as before;
  - "its email X is listed in the break-glass policy" (or "its alias X", or "matches a break-glass pattern") when another field of the record is;
  - "another name of its account, X, is listed in the break-glass policy" when the protection came through another record IT controls.

**Consequences:**

- The 10 G7 tests pass. The 12 G2 tests and every other test still pass.
- On the tuning world nothing changes. The plan (changes, held, warnings, hashes) and the matches are identical before and after. Its 6 break-glass rows are each protected by their own name, so their evidence text is unchanged.
- The leaver in G7 is offboarded: their Okta and Google users are suspended. Their Slack user carries the break-glass address, so it is left alone and named in a warning. A person decides what to do with it.
- Protection still spreads through an Okta or Google record. A Google alias or an Okta email that an admin sets to a break-glass address still protects every name of that record. That is IT's own data, and the row's evidence now names the field.
- If people can edit their own Okta profile email (Okta's self-service profile can allow it), Okta's email should not link either. The lab's mock treats it as IT's.
- A name that a break-glass account has only on a Slack user or an export row is protected on that record (by its key), not in other systems.
- The safety count `break_glass_changes` (`safety.py`) is unchanged. It still counts every name, as written, of any record that carries a protected name. So if a leaver's Slack userName is the break-glass name and its email is their own address, it counts their suspension as a change to a break-glass account. Main counted it the same way: `safety.py` has not changed since. Whether it should follow this rule is the owner's call.
- The mutation-testing target for break-glass also covers the new functions (`tools/mutate.py`).
