# OAR-004: Read-only: the radar opens a review and never revokes anything

**Date:** 2026-11-15 · **Status:** Superseded by [OAR-010](010-dry-run-removal-plans-never-applied.md) (owner, 2026-11-19). Before that it was Proposed and never accepted; the text below is unchanged. Its original note: Holds for every planned milestone (0 to 4).

**Context:** Revoking a grant cuts someone off from a tool they may use every day. A false positive would then cost a person their work, and IT their trust. A bad scan, or a bad export, could revoke dozens of grants at once. tier0 and Access-as-Code both keep a person in the loop for any change (lab ADR-002, [AAC-007](../../../access/docs/adr/007-safety-rails.md)). This project has no plan, no approval flow and no blast-radius limit for changes, so it shouldn't make any.

**Decision:**

- **The radar only reads and reports.** It writes a ranked findings list and, from milestone 2, a review ticket draft: a file shaped like a Linear issue of ticket type 9 ("New app or integration approval"), grouped by app. Nothing is filed, sent or changed.
- **A finding's only action is "review".** The data model refuses any other value: building a `Finding` with an action such as "revoke" raises an error ([`test_a_finding_can_only_ask_for_review`](../../tests/test_model.py)).
- **There is no code path to revoke.** The package imports no network library and names nothing that revokes or deletes. A test reads the source and fails if either appears ([`test_guards.py`](../../tests/test_guards.py)). Its input files are byte-identical after a scan ([`test_the_scan_leaves_its_input_files_unchanged`](../../tests/test_cli.py)).
- **A person in IT decides,** and does any revoking by hand in the admin console, outside the radar.
- **Revoking by the radar would need a new ADR** that supersedes this one, with the owner's approval and at least: a plan a person approves before anything changes, a dry run by default, a blast-radius limit, and an allowlist of apps and accounts it may never touch, as Access-as-Code has. It isn't planned.

**Consequences:**

- The worst a false positive can do is open a ticket a person closes.
- The radar can run on any export without risk, which is also why it can run unattended.
- Fixing a risky grant still takes a person's time. The metric is "found", not "fixed".
