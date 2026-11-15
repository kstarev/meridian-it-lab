# Access held-out a1: blind referee comparison

**Date:** 2026-11-15 · **Decision:** [AAC-011](../../access/docs/adr/011-blind-referee-against-shared-mistakes.md)

A blind helper wrote a referee script without seeing the generator, Access-as-Code, the tests or any earlier helper's notes. It read only:

- the raw held-out files (the HR export, the Okta, Google and Slack data, and the 95 app exports);
- the public guide, including the access policy table, and the catalog.

The script builds its own answer key from those by rules. It's stored sealed (`dataset/src/meridian_dataset/access/recipes/held_out_a1_referee.py.gz`, SHA-256 pinned), and the builder has never read it. A safety check before it runs found 0 imports outside the standard-library allowlist and 0 forbidden names.

The table compares the referee's key with the generator's key, counts per finding type only:

- **Differ:** the same account, with a different type, action or owner. It's counted under the generator key's type.
- **Only in key** and **only in referee:** findings that one side has and the other doesn't.

`python -m meridian_dataset.access.referee compare --final-score held_out_a1` prints the table, and a test keeps this copy in step with it.

<!-- referee-table:start -->
| Type | Generator key | Referee | Agree | Differ | Only in key | Only in referee |
|---|---:|---:|---:|---:|---:|---:|
| orphaned_account | 32 | 32 | 32 | 0 | 0 | 0 |
| mover_stale_access | 124 | 124 | 124 | 0 | 0 | 0 |
| leaver_access_left | 186 | 186 | 186 | 0 | 0 | 0 |
| rehire | 7 | 7 | 7 | 0 | 0 | 0 |
| contractor_past_end | 120 | 120 | 120 | 0 | 0 | 0 |
| leave_of_absence | 18 | 18 | 18 | 0 | 0 | 0 |
| hris_mass_termination | 1 | 1 | 1 | 0 | 0 | 0 |
| drift | 8 | 8 | 8 | 0 | 0 | 0 |
| break_glass | 8 | 8 | 8 | 0 | 0 | 0 |
| hr_data_error | 2 | 2 | 2 | 0 | 0 | 0 |
| **Total** | **506** | **506** | **506** | **0** | **0** | **0** |
<!-- referee-table:end -->

## Is the agreement real?

A perfect match could mean the referee copied the key. Two count-only checks say it didn't:

- **No typed-in answers.** The script contains 0 employee-id-like strings, 0 email addresses and 0 Okta-id-like strings.
- **It does worse on a world it wasn't fitted to.** On the practice world, whose layouts and username schemes it never saw, the same script agrees with the generator on 419 findings. It disagrees on 270, mostly accounts it can't match to a person and so calls orphaned. A copy of the held-out key would fail completely there. A rule-based reader fitted to the held-out formats would behave the way it does.

The referee's 19 judgment calls are in its notes, kept outside the repository for the owner.

## Rulings

None needed: there are 0 disagreements. If the generator or the recipe changes, the comparison is re-run. Each new disagreement then gets an id (`D001`, …) and the owner's ruling here before any held-out number is published:

- the key is wrong: the recipe's author fixes and re-seals;
- the referee is wrong;
- both are acceptable.

| Id | Kind | Ruling | Reason |
|---|---|---|---|
