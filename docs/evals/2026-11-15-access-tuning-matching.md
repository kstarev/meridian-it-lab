# Access tuning world: identity matching, rules only

**Date:** 2026-11-15 · **Decision:** [AAC-003](../../access/docs/adr/003-mock-api-shape.md), [AAC-006](../../access/docs/adr/006-model-choice-and-spend-cap.md)

Which HRIS person owns each account in the tuning world (seed 6108): 262 Okta users, 252 Google users, 249 Slack users and the rows of the 95 messy CSV exports. The collectors read the mocks and the exports without the generator's metadata, and the rules match accounts to people without the answer key. The key is used only here, to score.

- **Precision** is the share of accounts given a person that got the right one. A person given to an account nobody owns counts as wrong.
- **Recall** is the share of accounts that have an owner that were given the right one.
- Okta app assignments are left out (the key lists one owner per user, not per assignment). Break-glass accounts get no person.
- A false match is worse than no match, so a rule that can't pick exactly one person leaves the account for a person to decide. The accounts the rules miss are namesakes whose export row has no names to tell them apart, and people who changed their name: the export still has the old family name. Those are for the AI suggestions (rules plus AI, not run yet; $0 spent), which a person reviews before anything depends on them.

The table is generated: `uv run --locked python -m meridian_access.score matching --world out/access/seed-6108 --write-doc docs/evals/2026-11-15-access-tuning-matching.md` (`make access-data` makes `out/access/seed-6108`). A test fails if it drifts.

<!-- access-matching:start -->
| Rules only | Result |
|---|---|
| Precision (right person / given a person) | 1.000 (2010/2010), 95% 0.998 to 1.000 |
| Recall (right person / accounts with an owner) | 0.996 (2010/2019), 95% 0.992 to 0.998 |
| Ownerless accounts correctly left without a person | 1.000 (12/12), 95% 0.757 to 1.000 |
| Precision, directory | 1.000 (753/753), 95% 0.995 to 1.000 |
| Recall, directory | 1.000 (753/753), 95% 0.995 to 1.000 |
| Precision, exports | 1.000 (1257/1257), 95% 0.997 to 1.000 |
| Recall, exports | 0.993 (1257/1266), 95% 0.987 to 0.996 |

| Rule | Right / given a person |
|---|---|
| exact_email | 1746/1746 |
| google_alias | 6/6 |
| username | 207/207 |
| username_names | 51/51 |
<!-- access-matching:end -->
