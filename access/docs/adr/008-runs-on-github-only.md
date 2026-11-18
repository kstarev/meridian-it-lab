# AAC-008: Runs on GitHub only; the summary lives in the plan pull request; Slack is a mock adapter

**Date:** 2026-11-14 · **Status:** Accepted; built, with the open checks listed in [MILESTONES.md](../../MILESTONES.md). The user's decision (2026-11-14): no real Slack anywhere in Access-as-Code.

**Context:** Access-as-Code is a scheduled job: it should run every night without anyone's laptop, and its output must be seen where the IT team reviews it. There is no Slack workspace for the lab and there won't be one, but an IT team's tool would normally post to Slack. So the message should still be built and tested, without being sent anywhere.

**Decision:**

- **Everything runs on GitHub Actions:** the nightly plan (a scheduled workflow), apply (on merge), CI (the existing lint and test workflow) and the paid model runs (by hand, with the owner's approval, [AAC-006](006-model-choice-and-spend-cap.md)). Nothing depends on a personal device.
- **The plain-English summary goes in the plan pull request:** in its description, and in a **pull request comment each time the plan changes** (what changed since the last version, with the new counts). The pull request is the only place people read the plan ([AAC-002](002-the-plan-is-a-pull-request.md)).
- **Slack is a mock adapter only:**
  - It builds the Slack message the plan would post, as Block Kit JSON: a header, the counts, the warnings (blast radius, drift, break-glass) and a link to the pull request.
  - It writes the message to a file, `out/access/slack-message.json`, which the nightly workflow uploads as a CI artifact.
  - Tests check the Block Kit structure (valid block types, text within Slack's length limits, every number matching `plan.json`) and that nothing in it goes beyond what the pull request shows.
  - **It never sends anything:** no Slack token, no webhook and no workspace. The adapter has no network code, and the network guard blocks outbound calls in tests.
- **Secrets only in GitHub Actions secrets:** the Anthropic API key (in the approval-gated environment) and the GitHub App's private key. Nothing secret is in the repo, the `access-state` branch or a log. Secret scanning and push protection are on for the repo.
- **Workflows follow the lab's baseline** (lab ADR-011): actions pinned to commit SHAs, `permissions: {}` at the top and per-job grants, no credentials kept by checkout, one concurrency group for anything that writes to `access-state`.
- **Tests stay offline.** CI never calls Claude; the model has a stub, as in tier0.

**Consequences:**

- The lab's scope doesn't change: no live Slack for Access-as-Code, and GitHub Actions already runs the lab's CI.
- A reviewer sees everything in one place, the pull request, with a comment trail of how the plan changed.
- The Slack message is real enough to inspect and test, and connecting it later would mean adding a sender, not redesigning the message.
- Scheduled workflows on GitHub can start late, so "nightly" means roughly nightly.
- The user creates the GitHub App and the secrets, in milestone 5.
